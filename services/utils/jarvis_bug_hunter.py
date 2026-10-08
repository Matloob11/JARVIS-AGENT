"""
Jarvis AI Bug Hunter
This module monitors logs for errors and uses AI to analyze tracebacks and suggest fixes.

C-64: it reads the main log file (LOG_FILE, which holds INFO and above since C-64) by record level, never by a word
in the text: ERROR records are posted, CRITICAL ones only with 'ERROR' in their first line or a traceback (C-112:
'SYSTEM OVERLOAD' stays out of chat), WARNING and INFO never. Its read position survives the adaptation loop's
cancel and restart (the first start in a process begins at the end of the file) and follows a rotation through
<LOG_FILE>.1.
"""

import asyncio
import json
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard

# Configure logging
logger = setup_logger("bug_hunter")

# The file jarvis_logger writes to (LOG_FILE, relative to the project root).
LOG_FILE: Path = Path(config.log_file)

# Chat posts are throttled; every error stays in LOG_FILE, where tool_investigate_recent_bugs reads it.
REPEAT_WINDOW_S = 600.0  # the same error is posted at most once in 10 min
HOURLY_POST_CAP = 6  # posts in any 60 min, all errors together
POLL_S = 10.0
READ_MAX_BYTES = 1024 * 1024        # read per poll; the rest on the next one
INVESTIGATE_TAIL_BYTES = 512 * 1024  # what tool_investigate_recent_bugs reads (with the tail of .1 if needed)
INVESTIGATE_ENTRIES = 10
INVESTIGATE_ENTRY_CHARS = 600
INVESTIGATE_TOTAL_CHARS = 4000

# A record starts with jarvis_logger's asctime, or '{' with LOG_STRUCTURED; other lines (traceback frames)
# continue the record above them.
_RECORD_START = re.compile(r"^(?:\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}|\{)")
# The head of a text record: '<asctime> - <name> - <LEVEL> - '.
_RECORD_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)? - (?P<name>.*?) - "
                          r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) - ")
_TRACEBACK = "Traceback (most recent call last):"
_DIGITS = re.compile(r"\d+")


def _record_facts(entry: str) -> tuple[str | None, str, bool]:
    """(level, logger name, has a traceback) of one record; level None when its head cannot be read."""
    first, _, rest = entry.partition("\n")
    head = _RECORD_HEAD.match(first)
    if head is not None:
        return head.group("level"), head.group("name"), _TRACEBACK in rest
    if first.startswith("{"):
        try:
            data = json.loads(first)
        except ValueError:
            return None, "", False
        if isinstance(data, dict):
            level = data.get("level")
            return (level if isinstance(level, str) else None, str(data.get("name") or ""),
                    bool(data.get("exception")) or _TRACEBACK in rest)
    return None, "", False


def _is_postable(entry: str) -> bool:
    """ERROR always; CRITICAL only with 'ERROR' in its first line or a traceback; nothing else. The hunter's own
    records never."""
    level, name, traceback = _record_facts(entry)
    if name == logger.name:
        return False
    if level == "ERROR":
        return True
    return level == "CRITICAL" and ("ERROR" in entry.partition("\n")[0] or traceback)


def _records(lines: list[str]) -> list[str]:
    """Whole records among log lines, each with its continuation lines. Lines ahead of the first record (the rest
    of one read earlier) are skipped."""
    entries: list[str] = []
    for line in lines:
        if _RECORD_START.match(line):
            entries.append(line)
        elif entries:
            entries[-1] += line
    return entries


def error_entries(lines: list[str]) -> list[str]:
    """The records among newly read log lines that the hunter reports (_is_postable: by level, never by a word in
    the text), each with its continuation lines. Lines ahead of the first record (the rest of one read earlier) are
    skipped."""
    return [entry for entry in _records(lines) if _is_postable(entry)]


class ErrorThrottle:
    """Decides which error entries are posted. An entry whose first line, digit runs ignored (timestamp,
    counters, ports, ids), matches one posted in the last REPEAT_WINDOW_S is dropped, and nothing is
    posted once HOURLY_POST_CAP posts were made in the last hour."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._posted: dict[str, float] = {}
        self._posts: deque[float] = deque()

    def admit(self, entries: list[str]) -> list[str]:
        """The entries to post now, recorded as posted."""
        now = self._clock()
        self._posted = {key: ts for key, ts in self._posted.items() if now - ts < REPEAT_WINDOW_S}
        while self._posts and now - self._posts[0] >= 3600.0:
            self._posts.popleft()
        if len(self._posts) >= HOURLY_POST_CAP:
            return []
        fresh: list[str] = []
        for entry in entries:
            key = _DIGITS.sub("#", entry.split("\n", 1)[0].strip())
            if key not in self._posted:
                self._posted[key] = now
                fresh.append(entry)
        if fresh:
            self._posts.append(now)
        return fresh


# Shared by every monitor_logs run, so a restarted loop does not post again what was just posted.
_throttle = ErrorThrottle()


@dataclass
class LogCursor:
    """Where the hunter stopped reading: the file (path and inode) and the byte position after the last whole
    line read."""
    path: str
    ino: int
    position: int


# Shared by every monitor_logs run in this process (the adaptation loop cancels and restarts the hunter), so lines
# written while it was suspended are still read. None until the first start, which begins at the end of the file.
_cursor: LogCursor | None = None
_cursor_lock = threading.Lock()


def _whole_lines(data: bytes, full: bool) -> tuple[bytes, int]:
    """(the whole lines in data, bytes consumed). A record still being written is read next time; a chunk of
    full size without any newline (one giant line) is skipped instead of blocking the reader."""
    cut = data.rfind(b"\n") + 1
    if cut == 0 and full:
        return b"", len(data)
    return data[:cut], cut


def _read_from(path: str, position: int, limit: int) -> tuple[bytes, int]:
    with open(path, "rb") as f:
        f.seek(position)
        data = f.read(limit)
    return _whole_lines(data, len(data) >= limit)


def _decode(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def _read_new_lines(path: str) -> list[str]:
    """The whole lines written to path since the last call (at most READ_MAX_BYTES), moving the shared cursor. The
    first call in a process starts at the end of the file. After a rotation (another inode, or a smaller file) the
    rest of <path>.1 is read when it is the file the cursor was on, then the new file from its start."""
    global _cursor  # pylint: disable=global-statement
    key = os.path.normcase(os.path.abspath(path))
    with _cursor_lock:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return []
        cursor = _cursor
        if cursor is None or cursor.path != key:
            _cursor = LogCursor(key, st.st_ino, st.st_size)
            return []
        lines: list[str] = []
        budget = READ_MAX_BYTES
        if cursor.ino != st.st_ino or st.st_size < cursor.position:
            # Known, accepted loss (C-64 review COR-5, nit): when the cursor's inode is neither the live file
            # nor <path>.1 - a rollover that failed midway left it as '<path>.rotating-<pid>-<n>', or another
            # process shifted the backups between these two stats - the unread tail is skipped and the cursor
            # resets to the new file. Every such record stays in the backup files, where
            # tool_investigate_recent_bugs reads it.
            rotated = f"{path}.1"
            try:
                rotated_st = os.stat(rotated)
                remaining = rotated_st.st_size - cursor.position
                if rotated_st.st_ino == cursor.ino and remaining > 0:
                    data, used = _read_from(rotated, cursor.position, min(budget, remaining))
                    lines += _decode(data)
                    if remaining > budget:  # more of .1 than one poll reads: the rest next time
                        cursor.position += used
                        return lines
                    budget -= remaining  # .1 is done (a line it never finished is dropped)
            except OSError:
                pass
            cursor.ino, cursor.position = st.st_ino, 0
        if budget > 0 and st.st_size > cursor.position:
            data, used = _read_from(path, cursor.position, budget)
            lines += _decode(data)
            cursor.position += used
        return lines


class BugHunter:
    """Analyzes logs and suggests fixes."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_new_errors(self) -> list[str]:
        """The new whole lines of LOG_FILE since the last read (the shared cursor; see _read_new_lines)."""
        async with self._lock:
            return await asyncio.to_thread(_read_new_lines, str(LOG_FILE))

    async def analyze_error(self, error_text: str) -> str:
        """
        Uses the internal LLM context (via prompting) to analyze an error.
        In this implementation, it prepares a report for the agent's brain.
        """
        # This is a placeholder for actual LLM integration.
        # The agent.py will call this or we will push a notification to the agent.
        return f"Sir, ek naya error mila hai:\n\n{error_text[:500]}..."


def _one_line(entry: str) -> str:
    return " ".join(line.strip() for line in entry.splitlines() if line.strip())


async def monitor_logs(callback: Callable[[str], Any]) -> None:
    """Background task to watch logs: each poll's new error entries reach callback as one block, as far
    as the shared ErrorThrottle allows."""
    hunter = BugHunter()
    logger.info("AI Bug Hunter monitoring started on %s", LOG_FILE)

    while True:
        try:
            new_lines: list[str] = await hunter.get_new_errors()
            fresh: list[str] = _throttle.admit(error_entries(new_lines))
            if fresh:
                # Sanitize before callback to prevent injection propagation
                sanitized_error = vortex_guard.sanitize_input(" ".join(_one_line(entry) for entry in fresh))
                await callback(sanitized_error)
            await asyncio.sleep(POLL_S)
        except asyncio.CancelledError:
            # C-33 (08 Oct): the cancel must go on. Swallowing it returned normally, start_bug_hunter_loop's own loop
            # started a new monitor, the task never ended, and the job's teardown waited for it forever (a zombie job
            # without a session held the room for 1.5 h after a laptop sleep).
            logger.info("Bug Hunter monitoring stopping...")
            raise
        except (OSError, ValueError) as e:
            logger.error("Bug Hunter loop error: %s", e)
            await asyncio.sleep(POLL_S)


def _tail(path: str, limit: int) -> tuple[bytes, bool]:
    """(the last limit bytes of path from a line start, whether the file exists)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - limit)
            f.seek(start)
            data = f.read(limit)
    except FileNotFoundError:
        return b"", False
    if start > 0:
        data = data[data.find(b"\n") + 1:] if b"\n" in data else b""
    return data, True


def _recent_log_text() -> tuple[str, bool]:
    """(the last INVESTIGATE_TAIL_BYTES of LOG_FILE, preceded by the tail of LOG_FILE.1 when the current file is
    shorter, whether any of them exists)."""
    path = str(LOG_FILE)
    current, exists = _tail(path, INVESTIGATE_TAIL_BYTES)
    previous, previous_exists = b"", False
    if len(current) < INVESTIGATE_TAIL_BYTES:
        previous, previous_exists = _tail(f"{path}.1", INVESTIGATE_TAIL_BYTES - len(current))
    return (previous + current).decode("utf-8", errors="replace"), exists or previous_exists


def recent_error_report() -> tuple[list[str], bool, bool]:
    """(the last INVESTIGATE_ENTRIES error entries, each one line of at most INVESTIGATE_ENTRY_CHARS and sanitized,
    together at most INVESTIGATE_TOTAL_CHARS; whether a log file exists; whether it holds anything)."""
    text, exists = _recent_log_text()
    entries = [vortex_guard.sanitize_input(_one_line(entry))[:INVESTIGATE_ENTRY_CHARS]
               for entry in error_entries(text.splitlines(keepends=True))[-INVESTIGATE_ENTRIES:]]
    while entries and sum(len(entry) + 1 for entry in entries) > INVESTIGATE_TOTAL_CHARS:
        entries.pop(0)
    return entries, exists, bool(text.strip())


@jarvis_tool
async def tool_investigate_recent_bugs() -> str:
    """Manual tool to check and analyze the last few errors."""
    entries, exists, has_text = await asyncio.to_thread(recent_error_report)
    if not exists:
        return "Sir, koi error logs nahi mile. Sab theek lag raha hai."
    if not has_text:
        return "Sir, recent logs khali hain. System stable hai."
    if not entries:
        return "Sir, koi error logs nahi mile. Sab theek lag raha hai."

    report = "\n".join(entries)
    return (
        "Sir, maine recent logs analyze kiye hain. "
        "Yahan kuch patterns mile hain: \n\n"
        f"```\n{report}\n```\n\n"
        "Main inhein theek karne ke liye ready hoon."
    )
