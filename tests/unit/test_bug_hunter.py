"""Plan task C-64: the bug hunter on the real log (services/utils/jarvis_bug_hunter.py).

LOG_FILE holds INFO and above since C-64, so the hunter decides by the record's level, never by a word in its text:
ERROR is posted, CRITICAL only with 'ERROR' in its first line or a traceback (C-112: 'SYSTEM OVERLOAD' stays out of
chat), WARNING and INFO never, and never its own lines. Its read position survives the adaptation loop's cancel and
restart, starts at the end of the file in a fresh process and follows a rotation through LOG_FILE.1.
tool_investigate_recent_bugs reports the last error entries only.

Every log file lives in tmp_path; only the hunter's own asyncio.sleep is faked.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import json
import os
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.utils import jarvis_bug_hunter as hunter
from services.utils.jarvis_config import config


def _record(clock: str, name: str, level: str, message: str) -> str:
    return f"2026-10-04 {clock},101 - {name} - {level} - {message}\n"


def _json(level: str, message: str, exception: str | None = None, name: str = "JARVIS") -> str:
    record: dict[str, Any] = {"timestamp": "2026-10-04T10:00:00", "level": level, "name": name, "message": message}
    if exception:
        record["exception"] = exception
    return json.dumps(record) + "\n"


TRACEBACK = ["Traceback (most recent call last):\n", '  File "x.py", line 1, in <module>\n', "KeyError: 'k'\n"]


@pytest.fixture
def log(monkeypatch, tmp_path):
    """LOG_FILE in tmp_path, a fresh cursor (as in a new process) and a fresh throttle."""
    path = tmp_path / "jarvis.log"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(hunter, "LOG_FILE", path)
    monkeypatch.setattr(hunter, "_cursor", None)
    monkeypatch.setattr(hunter, "_throttle", hunter.ErrorThrottle())
    return path


def _append(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(lines))


def _fake_asyncio(monkeypatch, steps: list[Any]) -> None:
    """The hunter's own asyncio with a sleep that runs the next step (a callable) and ends the run (CancelledError)
    when none is left; to_thread, Lock and CancelledError stay the real ones, as test_role_gating fakes them."""
    async def fake_sleep(_delay: float) -> None:
        if not steps:
            raise asyncio.CancelledError
        steps.pop(0)()

    monkeypatch.setattr(hunter, "asyncio", types.SimpleNamespace(
        sleep=fake_sleep, to_thread=asyncio.to_thread, Lock=asyncio.Lock, CancelledError=asyncio.CancelledError))


async def _run(monkeypatch, steps: list[Any]) -> list[str]:
    posts: list[str] = []
    _fake_asyncio(monkeypatch, steps)
    with pytest.raises(asyncio.CancelledError):  # the run ends with a cancel, which the hunter passes on (C-33)
        await hunter.monitor_logs(AsyncMock(side_effect=posts.append))
    return posts


async def test_a_cancelled_hunter_ends_instead_of_starting_again(log, monkeypatch):
    """C-33 (08 Oct): monitor_logs used to swallow its cancel; start_bug_hunter_loop then started a new monitor, the
    task never ended, and the job's teardown waited for it forever. The cancel now ends the whole loop."""
    from services.ai_core import agent_loops

    monkeypatch.setattr(hunter, "POLL_S", 0.01)
    task = asyncio.ensure_future(agent_loops.start_bug_hunter_loop(None))
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.wait({task}, timeout=2)
    assert task.done() and task.cancelled()


# ---------------------------------------------------------------- what is reported


def test_an_info_or_warning_line_that_mentions_error_is_never_reported():
    lines = [_record("10:00:01", "AGENT-RUNNER", "INFO", "No ERROR so far, Traceback printing is off"),
             _record("10:00:02", "JARVIS-LOOPS", "WARNING", "ERROR budget at 80%"),
             _record("10:00:03", "JARVIS-CLIPBOARD", "ERROR", "Clipboard loop runtime error"),
             _record("10:00:04", "JARVIS-X", "DEBUG", "ERROR in debug")]
    assert [entry.split(" - ")[1] for entry in hunter.error_entries(lines)] == ["JARVIS-CLIPBOARD"]


def test_critical_needs_error_in_its_first_line_or_a_traceback():
    overload = _record("10:00:01", "JARVIS-LOOPS", "CRITICAL", "SYSTEM OVERLOAD: Throttling high-load services...")
    named = _record("10:00:02", "JARVIS-CORE", "CRITICAL", "UNHANDLED ERROR in turn")
    with_traceback = [_record("10:00:03", "JARVIS-CORE", "CRITICAL", "Turn Error (unexpected): boom"), *TRACEBACK]
    entries = hunter.error_entries([overload, named, *with_traceback])
    assert [entry.splitlines()[0] for entry in entries] == [named.rstrip("\n"), with_traceback[0].rstrip("\n")]
    assert entries[1].endswith("KeyError: 'k'\n")


def test_json_records_are_read_by_their_level_and_exception():
    lines = [_json("INFO", "ERROR mentioned at info"), _json("ERROR", "json error"),
             _json("CRITICAL", "overload"), _json("CRITICAL", "crash", exception="Traceback ...\nValueError"),
             _json("WARNING", "w")]
    assert [json.loads(entry)["message"] for entry in hunter.error_entries(lines)] == ["json error", "crash"]


def test_the_hunters_own_lines_and_unreadable_heads_are_skipped():
    lines = [_record("10:00:01", "bug_hunter", "ERROR", "Bug Hunter loop error: disk"),
             "2026-10-04 10:00:02 something ERROR without the standard head\n",
             _record("10:00:03", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK,
             _json("ERROR", "own json", name="bug_hunter")]
    entries = hunter.error_entries(lines)
    assert len(entries) == 1 and "Lazy enrollment failed." in entries[0] and "KeyError" in entries[0]


# ---------------------------------------------------------------- reading the file


async def test_only_errors_are_posted_from_a_log_full_of_info(log, monkeypatch):
    steps = [lambda: _append(log, _record("10:00:01", "AGENT-RUNNER", "INFO", "Connected; 0 ERROR so far"),
                             _record("10:00:02", "JARVIS-LOOPS", "CRITICAL", "SYSTEM OVERLOAD: Throttling"),
                             _record("10:00:03", "JARVIS-MEMORY", "WARNING", "slow ERROR path"),
                             _record("10:00:04", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK)]
    posts = await _run(monkeypatch, steps)
    assert len(posts) == 1
    assert "Lazy enrollment failed." in posts[0] and "KeyError" in posts[0]
    assert "Connected" not in posts[0] and "OVERLOAD" not in posts[0] and "slow" not in posts[0]
    assert "\n" not in posts[0]


async def test_a_fresh_process_starts_at_the_end_of_the_file(log, monkeypatch):
    _append(log, _record("09:00:00", "OLD", "ERROR", "from before this process"))
    steps = [lambda: _append(log, _record("10:00:00", "NEW", "ERROR", "after the start"))]
    posts = await _run(monkeypatch, steps)
    assert len(posts) == 1 and "after the start" in posts[0] and "before" not in posts[0]


async def test_a_restart_resumes_where_the_last_run_stopped(log, monkeypatch):
    posts = await _run(monkeypatch, [lambda: _append(log, _record("10:00:00", "A", "ERROR", "first error"))])
    assert len(posts) == 1
    # The adaptation loop cancelled the hunter (the run above ended on a CancelledError); errors keep coming.
    _append(log, _record("10:01:00", "B", "ERROR", "logged while the hunter was suspended"))
    posts = await _run(monkeypatch, [])
    assert len(posts) == 1 and "while the hunter was suspended" in posts[0] and "first error" not in posts[0]
    assert hunter._cursor is not None and hunter._cursor.position == log.stat().st_size


async def test_a_record_still_being_written_is_read_whole_on_the_next_poll(log, monkeypatch):
    steps = [lambda: _append(log, _record("10:00:00", "VOICE-ID", "ERROR", "Voice ID verification fa").rstrip("\n")),
             lambda: _append(log, "iled: KeyError\n")]
    posts = await _run(monkeypatch, steps)
    assert len(posts) == 1 and "Voice ID verification failed: KeyError" in posts[0]


async def test_after_a_rotation_the_rest_of_dot_1_is_read_then_the_new_file(log, monkeypatch):
    def rotate() -> None:
        _append(log, _record("10:00:01", "A", "ERROR", "written just before the rotation"))
        os.replace(log, f"{log}.1")
        log.write_text(_record("10:00:02", "B", "ERROR", "first line of the new file"), encoding="utf-8")

    posts = await _run(monkeypatch, [lambda: _append(log, _record("10:00:00", "S", "INFO", "started")), rotate])
    assert len(posts) == 1
    assert "written just before the rotation" in posts[0] and "first line of the new file" in posts[0]
    assert "started" not in posts[0]


async def test_a_file_that_was_cleared_is_read_from_its_start(log, monkeypatch):
    _append(log, _record("09:00:00", "OLD", "INFO", "x" * 200))

    def clear() -> None:
        log.write_text(_record("10:00:00", "C", "ERROR", "after the clear"), encoding="utf-8")

    posts = await _run(monkeypatch, [clear])
    assert len(posts) == 1 and "after the clear" in posts[0]


def _lf(lines: list[str]) -> list[str]:
    """Lines as written in text mode read back (CRLF on Windows, as the real log file)."""
    return [line.replace("\r\n", "\n") for line in lines]


def test_at_most_the_read_limit_is_read_per_poll_and_a_giant_line_never_blocks(log, monkeypatch):
    assert hunter.READ_MAX_BYTES == 1024 * 1024
    monkeypatch.setattr(hunter, "READ_MAX_BYTES", 200)
    assert hunter._read_new_lines(str(log)) == []  # the first read places the cursor at the end
    lines = [_record("10:00:%02d" % n, "X", "ERROR", f"error {n}") for n in range(10)]  # about 50 bytes each
    _append(log, *lines)
    first = hunter._read_new_lines(str(log))
    assert 0 < sum(len(line.encode()) for line in first) <= 200 and _lf(first) == lines[:len(first)]
    rest: list[str] = []
    for _ in range(10):
        rest += hunter._read_new_lines(str(log))
    assert _lf(first + rest) == lines
    _append(log, "y" * 450 + "\n", _record("10:01:00", "X", "ERROR", "after the giant line"))
    tail: list[str] = []
    for _ in range(6):
        tail += hunter._read_new_lines(str(log))
    assert _lf(tail)[-1] == _record("10:01:00", "X", "ERROR", "after the giant line")
    assert not any(line.startswith("y" * 200) for line in tail)  # the giant line's full chunks were skipped


def test_a_missing_file_reads_nothing(log):
    log.unlink()
    assert hunter._read_new_lines(str(log)) == []
    assert hunter._read_new_lines(str(log)) == []


# ---------------------------------------------------------------- tool_investigate_recent_bugs


async def test_investigate_says_no_errors_without_a_file_an_empty_file_or_errors(log):
    log.unlink()
    assert await hunter.tool_investigate_recent_bugs() == "Sir, koi error logs nahi mile. Sab theek lag raha hai."
    log.write_text("", encoding="utf-8")
    assert await hunter.tool_investigate_recent_bugs() == "Sir, recent logs khali hain. System stable hai."
    log.write_text(_record("10:00:00", "AGENT-RUNNER", "INFO", "No ERROR at all") +
                   _record("10:00:01", "JARVIS-LOOPS", "CRITICAL", "SYSTEM OVERLOAD") +
                   _record("10:00:02", "bug_hunter", "ERROR", "Bug Hunter loop error: disk"), encoding="utf-8")
    assert await hunter.tool_investigate_recent_bugs() == "Sir, koi error logs nahi mile. Sab theek lag raha hai."


async def test_investigate_returns_the_last_ten_error_entries_short_and_sanitized(log):
    lines: list[str] = []
    for n in range(15):
        lines.append(_record("10:00:%02d" % n, "AGENT-RUNNER", "INFO", f"busy {n}"))
        lines.append(_record("10:00:%02d" % n, f"MOD-{n}", "ERROR", f"failed {n}; rm -rf $HOME | x " + "z" * 900))
        lines += TRACEBACK
    log.write_text("".join(lines), encoding="utf-8")
    report = await hunter.tool_investigate_recent_bugs()
    body = report.split("```\n", 1)[1].rsplit("\n```", 1)[0]
    entries = body.split("\n")
    assert report.startswith("Sir, maine recent logs analyze kiye hain.")
    assert 1 <= len(entries) <= 10 and all(len(entry) <= 600 for entry in entries)
    assert len(body) <= 4000
    assert "MOD-14" in entries[-1] and "MOD-4 " not in body  # the newest ones
    assert not any(ch in body for ch in ";|$<>`&") and "busy" not in body


async def test_investigate_reads_the_tail_of_dot_1_when_the_new_file_is_short(log):
    Path(f"{log}.1").write_text(_record("09:00:00", "VOICE-ID", "ERROR", "before the rotation") + "".join(TRACEBACK),
                                encoding="utf-8")
    log.write_text(_record("10:00:00", "AGENT-RUNNER", "INFO", "fresh file"), encoding="utf-8")
    report = await hunter.tool_investigate_recent_bugs()
    assert "before the rotation" in report and "KeyError" in report


def test_the_tool_keeps_its_name_and_doc():
    """The committed desktop manifest (K-71) records the doc; a change would need a new manifest."""
    assert hunter.tool_investigate_recent_bugs.__name__ == "tool_investigate_recent_bugs"
    assert hunter.tool_investigate_recent_bugs.__doc__ == "Manual tool to check and analyze the last few errors."
    assert hunter.LOG_FILE.name and config.log_file
