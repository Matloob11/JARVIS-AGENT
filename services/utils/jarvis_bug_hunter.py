"""
Jarvis AI Bug Hunter
This module monitors logs for errors and uses AI to analyze tracebacks and suggest fixes.
"""

import asyncio
import os
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard

# Configure logging
logger = setup_logger("bug_hunter")

# The file jarvis_logger writes ERROR lines to (LOG_FILE, relative to the project root).
LOG_FILE: Path = Path(config.log_file)

# Chat posts are throttled; every error stays in LOG_FILE, where tool_investigate_recent_bugs reads it.
REPEAT_WINDOW_S = 600.0  # the same error is posted at most once in 10 min
HOURLY_POST_CAP = 6  # posts in any 60 min, all errors together
# A record starts with jarvis_logger's asctime, or '{' with LOG_STRUCTURED; other lines (traceback frames)
# continue the record above them.
_RECORD_START = re.compile(r"^(?:\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}|\{)")
_DIGITS = re.compile(r"\d+")


def error_entries(lines: list[str]) -> list[str]:
    """The records among newly read log lines that hold 'ERROR' or a traceback, each with its continuation
    lines. Lines ahead of the first record (the rest of one read earlier) are skipped."""
    entries: list[str] = []
    for line in lines:
        if _RECORD_START.match(line):
            entries.append(line)
        elif entries:
            entries[-1] += line
    return [entry for entry in entries if "ERROR" in entry or "TRACEBACK" in entry.upper()]


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


class BugHunter:
    """Analyzes logs and suggests fixes."""

    def __init__(self) -> None:
        self.last_position: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        if LOG_FILE.exists():
            self.last_position = LOG_FILE.stat().st_size

    async def get_new_errors(self) -> list[str]:
        """Reads new lines from the error log with lock protection."""
        async with self._lock:
            if not os.path.exists(LOG_FILE):
                return []

            def _read_sync():
                current_size = os.path.getsize(LOG_FILE)
                if current_size < self.last_position:
                    # Log was rotated or cleared
                    self.last_position = 0

                with open(LOG_FILE, "rb") as f:
                    f.seek(self.last_position)
                    data = f.read()
                # Whole lines only: a record still being written is read on the next poll.
                data = data[:data.rfind(b"\n") + 1]
                self.last_position += len(data)
                return data.decode("utf-8", errors="replace").splitlines(keepends=True)

            return await asyncio.to_thread(_read_sync)

    async def analyze_error(self, error_text: str) -> str:
        """
        Uses the internal LLM context (via prompting) to analyze an error.
        In this implementation, it prepares a report for the agent's brain.
        """
        # This is a placeholder for actual LLM integration.
        # The agent.py will call this or we will push a notification to the agent.
        return f"Sir, ek naya error mila hai:\n\n{error_text[:500]}..."


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
                sanitized_error = vortex_guard.sanitize_input("".join(fresh))
                await callback(sanitized_error)
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            logger.info("Bug Hunter monitoring stopping...")
            break
        except (OSError, ValueError) as e:
            logger.error("Bug Hunter loop error: %s", e)
            await asyncio.sleep(10)


@jarvis_tool
async def tool_investigate_recent_bugs() -> str:
    """Manual tool to check and analyze the last few errors."""
    if not LOG_FILE.exists():
        return "Sir, koi error logs nahi mile. Sab theek lag raha hai."

    def _read_recent() -> str:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            # Read last 4KB
            f.seek(0, 2) # os.SEEK_END
            size = f.tell()
            f.seek(max(0, size - 4000))
            return f.read()

    recent_logs: str = await asyncio.to_thread(_read_recent)

    if not recent_logs.strip():
        return "Sir, recent logs khali hain. System stable hai."

    # Zero-trust: Sanitize logs before passing to agent prompt
    sanitized_logs: str = vortex_guard.sanitize_input(recent_logs)

    return (
        "Sir, maine recent logs analyze kiye hain. "
        "Yahan kuch patterns mile hain: \n\n"
        f"```\n{sanitized_logs}\n```\n\n"
        "Main inhein theek karne ke liye ready hoon."
    )
