"""src/core/room_watchdog.py: the PC worker re-runs ensure_room.py when the room loses its agent.

Everything is injected or mocked: no network and no wait on the 60 s clock (one test runs the real thread at a
10 ms interval). Settings are throwaway values. ensure_room.py itself is never started: the tests that need a real
child run a throwaway sleeper script in its place (it ends by itself after 30 s at the latest), and two run a
throwaway Python process that imports this module to show what the worker's stderr gets.
"""
# pylint: disable=protected-access,redefined-outer-name,unnecessary-lambda,no-member  # protobuf members are generated
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.core import room_watchdog as rw

FAKE_KEY = "APIroomWatchdogFakeKey"
FAKE_SECRET = "roomWatchdogFakeSecretValue0123456789"
FAKE_HOST = "lk-watchdog.invalid"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Harness:
    """A RoomWatchdog with a scripted room state, a fake clock and a counted ensure_room run."""

    def __init__(self, states: list[Any], *, connected: bool = True, ensure: Any = None) -> None:
        self.states = list(states)
        self.connected = connected
        self.checks = 0
        self.ensure_calls = 0
        self.clock = Clock()
        self._ensure = ensure
        self.watchdog = rw.RoomWatchdog("wave-room", clock=self.clock, connected=lambda: self.connected,
                                        check=self.check, ensure=self.ensure)

    def check(self, room: str) -> str:
        assert room == "wave-room"
        self.checks += 1
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, BaseException):
            raise state
        return state

    def ensure(self) -> str:
        self.ensure_calls += 1
        if self._ensure is not None:
            return self._ensure()
        return "[ROOM] Result: OK, room created with its agent dispatch (the worker gets the job)"

    def ticks(self, count: int, step: float = rw.CHECK_S) -> None:
        for _ in range(count):
            self.watchdog.tick()
            self.clock.now += step


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", f"wss://{FAKE_HOST}:7443")
    monkeypatch.setenv("LIVEKIT_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", FAKE_SECRET)
    monkeypatch.delenv("JARVIS_BRAIN", raising=False)
    monkeypatch.delenv(rw.SWITCH_ENV, raising=False)
    monkeypatch.setattr(rw, "ENV_FILES", ())  # never the real .env
    monkeypatch.setattr(rw, "_watchdog", None)
    # setup_logger's logger does not propagate (its own stderr handler); caplog listens on the root logger.
    monkeypatch.setattr(rw.LOG, "propagate", True)


# --------------------------------------------------------------------------- the checks


def test_missing_room_twice_runs_ensure_room_once() -> None:
    h = Harness([rw.MISSING_ROOM])
    h.ticks(2)
    assert h.ensure_calls == 1
    assert h.watchdog.misses == 0


def test_no_agent_twice_runs_ensure_room() -> None:
    h = Harness([rw.NO_AGENT])
    h.ticks(2)
    assert h.ensure_calls == 1


def test_agent_present_never_runs_ensure_room() -> None:
    h = Harness([rw.PRESENT])
    h.ticks(20)
    assert h.checks == 20
    assert h.ensure_calls == 0


def test_one_miss_then_present_does_not_run() -> None:
    h = Harness([rw.NO_AGENT, rw.PRESENT, rw.MISSING_ROOM, rw.PRESENT, rw.PRESENT])
    h.ticks(5)
    assert h.ensure_calls == 0
    assert h.watchdog.misses == 0


def test_rate_limit_holds_for_five_minutes() -> None:
    h = Harness([rw.MISSING_ROOM])
    h.ticks(2)  # t=0, 60: run at 60
    assert h.ensure_calls == 1
    h.ticks(4)  # t=120..300: misses again, but less than 300 s since the run
    assert h.ensure_calls == 1
    h.ticks(1)  # t=360: 300 s after the run
    assert h.ensure_calls == 2


PAUSED_RESULT = ("[ROOM] Result: PAUSED by the owner: the bridge dispatches no agent until Wake "
                 "(ui_command start)")


def test_a_paused_result_makes_the_next_run_wait_thirty_minutes(caplog: pytest.LogCaptureFixture) -> None:
    """C-23 (K-53 v9): while the owner paused JARVIS the bridge dispatches nothing, so the watchdog's next
    ensure_room.py run waits PAUSED_ENSURE_EVERY_S (1800 s) instead of 300 s, with one INFO line."""
    h = Harness([rw.NO_AGENT], ensure=lambda: PAUSED_RESULT)
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        h.ticks(2)  # t=0, 60: run at 60 -> PAUSED
    assert h.ensure_calls == 1
    waits = [r for r in caplog.records if "next ensure_room.py run waits 1800 s" in r.getMessage()]
    assert len(waits) == 1 and waits[0].levelno == logging.INFO
    h.ticks(int(rw.ENSURE_EVERY_S / rw.CHECK_S) + 1)  # 300 s later: still waiting
    assert h.ensure_calls == 1
    h.ticks(int((rw.PAUSED_ENSURE_EVERY_S - rw.ENSURE_EVERY_S) / rw.CHECK_S))  # 1800 s after the run
    assert h.ensure_calls == 2


def test_an_ok_result_after_a_pause_brings_back_the_five_minute_cadence() -> None:
    results = [PAUSED_RESULT, "[ROOM] Result: OK, an agent of the current worker is in the room (bridge)"]
    h = Harness([rw.NO_AGENT], ensure=lambda: results.pop(0) if len(results) > 1 else results[0])
    h.ticks(2)
    assert h.ensure_calls == 1
    h.ticks(int(rw.PAUSED_ENSURE_EVERY_S / rw.CHECK_S))  # the second run, after the paused wait: OK
    assert h.ensure_calls == 2
    h.ticks(int(rw.ENSURE_EVERY_S / rw.CHECK_S))  # 300 s again
    assert h.ensure_calls == 3


def test_link_not_connected_makes_no_api_call() -> None:
    h = Harness([rw.MISSING_ROOM], connected=False)
    h.ticks(10)
    assert h.checks == 0
    assert h.ensure_calls == 0


def test_link_drop_resets_the_miss_count() -> None:
    h = Harness([rw.MISSING_ROOM])
    h.watchdog.tick()
    h.connected = False
    h.watchdog.tick()
    h.connected = True
    h.watchdog.tick()
    assert h.ensure_calls == 0
    h.watchdog.tick()
    assert h.ensure_calls == 1


def test_link_state_error_counts_as_not_connected() -> None:
    h = Harness([rw.MISSING_ROOM])

    def broken() -> bool:
        raise OSError("sharing violation")

    h.watchdog._connected = broken
    h.ticks(3)
    assert h.checks == 0


def test_api_errors_are_swallowed_with_one_rate_limited_warning(caplog: pytest.LogCaptureFixture) -> None:
    error = ConnectionError(f"Cannot connect to host {FAKE_HOST}:7443 key={FAKE_KEY} secret={FAKE_SECRET}")
    h = Harness([error])
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        h.ticks(5)  # 5 failures within 600 s
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert h.ensure_calls == 0
    assert h.watchdog.misses == 0  # an error is no miss
    h.clock.now += rw.WARN_EVERY_S
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        h.watchdog.tick()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_ensure_room_errors_are_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    def boom() -> str:
        raise subprocess.TimeoutExpired(cmd=["python", "ensure_room.py"], timeout=rw.ENSURE_TIMEOUT_S)

    h = Harness([rw.MISSING_ROOM], ensure=boom)
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        h.ticks(2)
    assert h.ensure_calls == 1
    assert "did not finish" in caplog.text

    def crash() -> str:
        raise OSError(f"cannot start: {FAKE_SECRET}")

    h2 = Harness([rw.MISSING_ROOM], ensure=crash)
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        h2.ticks(2)
    assert h2.ensure_calls == 1
    assert FAKE_SECRET not in caplog.text


def test_loop_survives_a_tick_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    watchdog = rw.RoomWatchdog("wave-room", clock=Clock(), connected=lambda: True, check=lambda _r: rw.PRESENT,
                               ensure=lambda: "")
    calls = {"n": 0}

    def tick() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(f"unexpected {FAKE_SECRET}")
        watchdog._stopped.set()

    monkeypatch.setattr(watchdog, "tick", tick)
    monkeypatch.setattr(watchdog._stopped, "wait", lambda _timeout: watchdog._stopped.is_set())
    watchdog._loop()  # returns: the first exception was caught, the second tick stopped it
    assert calls["n"] == 2


def test_logs_contain_no_key_secret_or_address(caplog: pytest.LogCaptureFixture) -> None:
    error = RuntimeError(f"401 for wss://{FAKE_HOST}:7443/twirp key {FAKE_KEY} secret {FAKE_SECRET}")
    h = Harness([rw.MISSING_ROOM, error, rw.MISSING_ROOM, rw.MISSING_ROOM],
                ensure=lambda: f"[ROOM] Result: OK {FAKE_SECRET}")
    with caplog.at_level(logging.DEBUG, logger=rw.LOG.name):
        h.ticks(4)
    assert h.ensure_calls == 1
    assert caplog.text
    for value in (FAKE_KEY, FAKE_SECRET, FAKE_HOST):
        assert value not in caplog.text


# --------------------------------------------------------------------------- ensure_room.py as a subprocess


class FakeChild:
    """A finished ensure_room.py child as Popen shows it."""

    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self.pid, self.returncode, self._stdout, self._code = 4242, None, stdout, returncode
        self.timeouts: list[float | None] = []
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        self.timeouts.append(timeout)
        self.returncode = self._code
        return self._stdout, b""

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _fake_popen(monkeypatch: pytest.MonkeyPatch, child: FakeChild) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeChild:
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return child

    monkeypatch.setattr(rw, "_popen", fake_popen)
    monkeypatch.setattr(rw, "bind_to_this_process", lambda pid: seen.setdefault("bound", pid) == pid)
    return seen


def test_run_ensure_room_captures_output_and_returns_the_result_line(monkeypatch: pytest.MonkeyPatch) -> None:
    output = (f"[ROOM] LiveKit API: https://{FAKE_HOST}:7443\n[ROOM] Ensuring room: wave-room\n"
              "[ROOM] Result: OK, an agent of the current worker is in the room\n").encode("utf-8")
    child = FakeChild(output)
    seen = _fake_popen(monkeypatch, child)
    assert rw.run_ensure_room() == "[ROOM] Result: OK, an agent of the current worker is in the room"
    assert seen["cmd"] == [sys.executable, str(rw.ENSURE_ROOM), "--reason", "watchdog"]  # C-23 (K-53 v9)
    assert seen["kwargs"]["cwd"] == str(rw.ROOT)
    assert seen["kwargs"]["stdout"] is subprocess.PIPE and seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert child.timeouts == [rw.ENSURE_TIMEOUT_S]
    if sys.platform == "win32":  # started suspended, put into the worker's job, then resumed
        assert seen["kwargs"]["creationflags"] & rw.CREATE_SUSPENDED
        assert seen["kwargs"]["creationflags"] & rw.CREATE_NO_WINDOW
        assert seen["bound"] == child.pid
    assert rw._child is None  # no longer tracked once it has finished


def test_run_ensure_room_without_result_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_popen(monkeypatch, FakeChild(b"[ERROR] x\n", returncode=1))
    assert rw.run_ensure_room() == "ensure_room.py exited 1 without a result line"


def test_a_child_that_cannot_be_resumed_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    child = FakeChild(b"")
    _fake_popen(monkeypatch, child)

    def cannot_resume(_pid: int) -> bool:
        raise OSError("NtResumeProcess failed")

    monkeypatch.setattr(rw, "bind_to_this_process", cannot_resume)
    monkeypatch.setattr(rw.sys, "platform", "win32")
    with pytest.raises(OSError):
        rw.run_ensure_room()
    assert child.killed and rw._child is None


SLEEPER = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "Path(os.environ['ROOM_WATCHDOG_TEST_PID']).write_text(str(os.getpid()))\n"
    "print('[ROOM] Ensuring room: wave-room', flush=True)\n"
    "time.sleep(30)\n"  # ends by itself if a test fails before it is ended
    "print('[ROOM] Result: OK, too late')\n"
)


def _gone(pid: int, timeout: float = 15.0) -> bool:
    import psutil  # pylint: disable=import-outside-toplevel

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def _wait_for_pid(path: Path, timeout: float = 60.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return int(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("the stand-in ensure_room.py never started")


def test_stop_ends_an_ensure_room_run_still_in_progress(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A real child (a sleeper standing in for ensure_room.py): RoomWatchdog.stop(), as on a worker stop, ends
    it and the run reports no result line."""
    script = tmp_path / "ensure_room_standin.py"
    script.write_text(SLEEPER, encoding="utf-8")
    pid_file = tmp_path / "child.pid"
    monkeypatch.setattr(rw, "ENSURE_ROOM", script)
    monkeypatch.setenv("ROOM_WATCHDOG_TEST_PID", str(pid_file))
    results: list[Any] = []

    def run() -> None:
        try:
            results.append(rw.run_ensure_room())
        except Exception as exc:  # pylint: disable=broad-exception-caught  # reported by the assertion below
            results.append(exc)

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    child_pid = _wait_for_pid(pid_file)
    watchdog = rw.RoomWatchdog("wave-room", connected=lambda: False, check=lambda _r: rw.PRESENT,
                               ensure=lambda: "", interval=3600)
    watchdog.stop()
    runner.join(30)
    assert not runner.is_alive()
    assert _gone(child_pid)
    assert len(results) == 1 and isinstance(results[0], str) and "without a result line" in results[0]
    assert rw._child is None and rw.terminate_child() is False


BINDER = (
    "import os, sys, threading, time\n"
    "from pathlib import Path\n"
    "from src.core import room_watchdog as rw\n"
    "rw.ENSURE_ROOM = Path(sys.argv[1])\n"
    "threading.Thread(target=rw.run_ensure_room, daemon=True).start()\n"
    "pid_file = Path(os.environ['ROOM_WATCHDOG_TEST_PID'])\n"
    "deadline = time.monotonic() + 60\n"
    "while not pid_file.exists() and time.monotonic() < deadline:\n"
    "    time.sleep(0.05)\n"
    "time.sleep(0.5)\n"
    "os._exit(0)\n"  # like taskkill /F: no atexit hook, no stop()
)


@pytest.mark.skipif(sys.platform != "win32", reason="the kill-on-close job object is Windows only")
def test_an_ensure_room_child_dies_with_a_worker_that_is_killed(tmp_path: Path) -> None:
    """A throwaway process plays the worker: it starts the stand-in ensure_room.py through run_ensure_room and
    then ends at once without any cleanup (as under taskkill /F). The child was put into the worker's
    kill-on-close job, so Windows ends it with the worker instead of leaving it running for up to 150 s."""
    script = tmp_path / "ensure_room_standin.py"
    script.write_text(SLEEPER, encoding="utf-8")
    pid_file = tmp_path / "child.pid"
    env = {**os.environ, "ROOM_WATCHDOG_TEST_PID": str(pid_file), "JARVIS_RUN_DIR": str(tmp_path / "run"),
           "LOG_LEVEL": "INFO"}
    env.pop("PYTEST_CURRENT_TEST", None)
    worker = subprocess.run([sys.executable, "-c", BINDER, str(script)], cwd=rw.ROOT, env=env,
                            capture_output=True, timeout=120, check=False)
    assert worker.returncode == 0, worker.stderr.decode("utf-8", "replace")[-2000:]
    child_pid = _wait_for_pid(pid_file, timeout=5)
    assert _gone(child_pid), "the ensure_room.py stand-in outlived the worker"


WORKER_LOG = (
    "import logging, sys\n"
    "logging.basicConfig(level=logging.WARNING, force=True)\n"  # a root logger that hides INFO, as in the worker
    "from src.core import room_watchdog as rw\n"
    "result = '[ROOM] Result: OK, an agent of the current worker is in the room'\n"
    "w = rw.RoomWatchdog('wave-room', connected=lambda: False, check=lambda r: rw.PRESENT,\n"
    "                    ensure=lambda: result, interval=3600)\n"
    "w.start()\n"
    "w._run_ensure()\n"
    "w.stop()\n"
)


def test_the_worker_stderr_gets_the_start_line_and_one_line_per_ensure_room_run(tmp_path: Path) -> None:
    """In a real process whose root logger shows only warnings (the worker's case: none of the watchdog's lines
    reached logs/agent_stderr.log), the watchdog's own logger still writes its INFO lines to stderr: the start
    line and one line per ensure_room.py run with the '[ROOM] Result:' text."""
    env = {**os.environ, "JARVIS_RUN_DIR": str(tmp_path / "run"), "LOG_LEVEL": "INFO",
           "LIVEKIT_URL": f"wss://{FAKE_HOST}:7443", "LIVEKIT_API_KEY": FAKE_KEY, "LIVEKIT_API_SECRET": FAKE_SECRET}
    env.pop("PYTEST_CURRENT_TEST", None)
    done = subprocess.run([sys.executable, "-c", WORKER_LOG], cwd=rw.ROOT, env=env, capture_output=True,
                          timeout=120, check=False)
    err = done.stderr.decode("utf-8", "replace")
    assert done.returncode == 0, err[-2000:]
    lines = [line for line in err.splitlines() if "ROOM-WATCHDOG" in line]
    starts = [line for line in lines if "ROOM WATCHDOG on: every 3600 s, room wave-room" in line]
    runs = [line for line in lines if "[ROOM] Result: OK, an agent of the current worker is in the room" in line]
    assert len(starts) == 1 and " - INFO - " in starts[0]
    assert len(runs) == 1 and " - INFO - " in runs[0] and "ran ensure_room.py" in runs[0]
    for value in (FAKE_KEY, FAKE_SECRET, FAKE_HOST):
        assert value not in err


def test_the_module_logs_through_the_projects_logger() -> None:
    from services.utils.jarvis_logger import RedactingFormatter  # pylint: disable=import-outside-toplevel

    assert rw.LOG.name == "ROOM-WATCHDOG"
    streams = [h for h in rw.LOG.handlers if type(h) is logging.StreamHandler]  # pylint: disable=unidiomatic-typecheck
    assert streams and all(isinstance(h.formatter, RedactingFormatter) for h in streams)


def test_one_info_line_when_it_starts(caplog: pytest.LogCaptureFixture) -> None:
    watchdog = rw.RoomWatchdog("wave-room", connected=lambda: False, check=lambda _r: rw.PRESENT,
                               ensure=lambda: "", interval=rw.CHECK_S)
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        watchdog.start()
        watchdog.stop()
    infos = [r.getMessage() for r in caplog.records if r.name == rw.LOG.name and r.levelno == logging.INFO]
    assert len(infos) == 1 and infos[0].startswith("ROOM WATCHDOG on: every 60 s, room wave-room")


def test_one_info_line_per_ensure_room_run_with_its_result(caplog: pytest.LogCaptureFixture) -> None:
    h = Harness([rw.NO_AGENT])
    with caplog.at_level(logging.INFO, logger=rw.LOG.name):
        h.ticks(2)
    assert h.ensure_calls == 1
    runs = [r for r in caplog.records if "ensure_room.py" in r.getMessage() and "[ROOM] Result:" in r.getMessage()]
    assert len(runs) == 1 and runs[0].levelno == logging.INFO
    assert runs[0].getMessage() == ("ROOM WATCHDOG ran ensure_room.py (no agent in room wave-room for 2 checks): "
                                    "[ROOM] Result: OK, room created with its agent dispatch (the worker gets the job)")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --------------------------------------------------------------------------- link_connected on real files


def _write_worker_files(run_dir: Path, *, state: str, lock_pid: int = 4242,
                        lock_started_at: str = "2026-10-03T09:15:00+05:00") -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "agent.pid.json").write_text(json.dumps({"pid": 4242, "started_at": "2026-10-03T09:15:00+05:00",
                                                        "by": "claude", "reason": "test"}), encoding="utf-8")
    (run_dir / "agent_link.json").write_text(json.dumps({
        "state": state, "since": 1759465000.0, "worker_id": "AW_watchdogTest", "detail": "", "cause": "",
        "connected_once": state == "connected", "recovery": True, "recoveries": 0, "pid": 4242,
        "lock_pid": lock_pid, "lock_started_at": lock_started_at}), encoding="utf-8")


def test_link_connected_reads_the_real_run_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The real link_connected(): run/agent.pid.json and run/agent_link.json written in JARVIS_RUN_DIR, read
    through agent_lock and livekit_link as the worker writes them (no mock of either)."""
    run_dir = tmp_path / "run"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run_dir))
    assert rw.link_connected() is False  # no files at all

    _write_worker_files(run_dir, state="connected")
    assert rw.link_connected() is True

    for state in ("connecting", "retrying", "failed"):
        _write_worker_files(run_dir, state=state)
        assert rw.link_connected() is False, state

    _write_worker_files(run_dir, state="connected", lock_pid=1)  # an earlier worker's file
    assert rw.link_connected() is False
    _write_worker_files(run_dir, state="connected", lock_started_at="2026-10-03T08:00:00+05:00")
    assert rw.link_connected() is False

    _write_worker_files(run_dir, state="connected")
    (run_dir / "agent_link.json").write_text("{not json", encoding="utf-8")
    assert rw.link_connected() is False
    (run_dir / "agent_link.json").unlink()
    assert rw.link_connected() is False  # a worker from before C-136 writes no link file


# --------------------------------------------------------------------------- the LiveKit check (fake API)


class FakeRoomService:
    def __init__(self, rooms: list[str], kinds: list[tuple[int, int]]) -> None:
        self.rooms, self.kinds = rooms, kinds

    async def list_rooms(self, request: Any) -> SimpleNamespace:
        assert list(request.names) == ["wave-room"]
        return SimpleNamespace(rooms=[SimpleNamespace(name=n) for n in self.rooms])

    async def list_participants(self, request: Any) -> SimpleNamespace:
        assert request.room == "wave-room"
        return SimpleNamespace(participants=[SimpleNamespace(kind=k, state=s) for k, s in self.kinds])


@pytest.mark.parametrize(("rooms", "kinds", "expected"), [
    ([], [], rw.MISSING_ROOM),
    (["wave-room"], [], rw.NO_AGENT),
    (["wave-room"], [(0, 1)], rw.NO_AGENT),  # a person only
    (["wave-room"], [(rw.AGENT_KIND, rw.GONE_STATE)], rw.NO_AGENT),  # a dropped agent still listed
    (["wave-room"], [(0, 1), (rw.AGENT_KIND, 1)], rw.PRESENT),
])
def test_room_state_with_a_fake_api(monkeypatch: pytest.MonkeyPatch, rooms: list[str],
                                    kinds: list[tuple[int, int]], expected: str) -> None:
    from livekit import api  # pylint: disable=import-outside-toplevel

    created: list[tuple[Any, ...]] = []

    class FakeAPI:
        def __init__(self, *args: Any) -> None:
            created.append(args)
            self.room = FakeRoomService(rooms, kinds)
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(api, "LiveKitAPI", FakeAPI)
    assert rw.room_state("wave-room") == expected
    assert created == [(f"https://{FAKE_HOST}:7443", FAKE_KEY, FAKE_SECRET)]


def test_agent_kind_matches_livekit() -> None:
    from livekit import api  # pylint: disable=import-outside-toplevel

    assert api.ParticipantInfo.Kind.AGENT == rw.AGENT_KIND == 4
    assert api.ParticipantInfo.State.DISCONNECTED == rw.GONE_STATE


def test_room_state_without_settings_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVEKIT_API_SECRET")
    with pytest.raises(RuntimeError) as info:
        rw.room_state("wave-room")
    assert FAKE_KEY not in str(info.value)


# --------------------------------------------------------------------------- where the thread starts


@pytest.fixture
def worker_process(monkeypatch: pytest.MonkeyPatch) -> list[rw.RoomWatchdog]:
    """This test process posing as the worker that holds run/agent.lock; RoomWatchdog.start is recorded
    instead of starting a thread."""
    started: list[rw.RoomWatchdog] = []
    monkeypatch.setattr(rw.agent_lock, "lock_disabled", lambda: False)
    monkeypatch.setattr(rw.agent_lock, "is_locked", lambda: True)
    monkeypatch.setattr(rw.agent_lock, "read_pid_info", lambda: {"pid": rw.os.getpid()})
    monkeypatch.setattr(rw.multiprocessing, "parent_process", lambda: None)
    monkeypatch.setattr(rw.RoomWatchdog, "start", lambda self: started.append(self))
    return started


def test_starts_in_the_pc_worker(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ROOM_NAME", "wave-room")
    watchdog = rw.start_room_watchdog()
    assert watchdog is not None and worker_process == [watchdog]
    assert watchdog.room == "wave-room"
    assert rw.start_room_watchdog() is watchdog  # once per process
    assert len(worker_process) == 1


def test_explicit_pc_brain_starts(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_BRAIN", "PC")
    assert rw.start_room_watchdog() is not None


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_switch_off_starts_nothing(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch,
                                   value: str) -> None:
    monkeypatch.setenv(rw.SWITCH_ENV, value)
    assert rw.start_room_watchdog() is None
    assert not worker_process


def test_vps_mode_starts_nothing(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    assert rw.start_room_watchdog() is None
    assert not worker_process


def test_vps_mode_from_the_env_file(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch,
                                    tmp_path: Any) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("JARVIS_BRAIN=vps\n", encoding="utf-8")
    monkeypatch.setattr(rw, "ENV_FILES", (env_file,))
    assert rw.start_room_watchdog() is None
    assert not worker_process


def test_job_process_starts_nothing(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rw.multiprocessing, "parent_process", object)
    assert rw.start_room_watchdog() is None
    assert not worker_process


def test_process_without_the_lock_starts_nothing(worker_process: list[rw.RoomWatchdog],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rw.agent_lock, "read_pid_info", lambda: {"pid": rw.os.getpid() + 1})
    assert rw.start_room_watchdog() is None
    monkeypatch.setattr(rw.agent_lock, "read_pid_info", lambda: {"pid": rw.os.getpid()})
    monkeypatch.setattr(rw.agent_lock, "is_locked", lambda: False)
    assert rw.start_room_watchdog() is None
    assert not worker_process


def test_pytest_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Any] = []
    monkeypatch.setattr(rw.RoomWatchdog, "start", lambda self: started.append(self))
    assert rw.start_room_watchdog() is None  # agent_lock.lock_disabled() is True under pytest
    assert not started


def test_start_never_raises(worker_process: list[rw.RoomWatchdog], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> str:
        raise ValueError("bad")

    monkeypatch.setattr(rw, "why_not_started", broken)
    assert rw.start_room_watchdog() is None


def test_import_starts_no_thread() -> None:
    import importlib  # pylint: disable=import-outside-toplevel
    import threading  # pylint: disable=import-outside-toplevel

    importlib.reload(rw)
    assert rw._watchdog is None
    assert not [t for t in threading.enumerate() if t.name == "jarvis-room-watchdog"]


def test_real_thread_runs_ticks_and_stops() -> None:
    """The real thread with a tiny interval: it calls the check and stops on stop()."""
    import threading  # pylint: disable=import-outside-toplevel

    ticked = threading.Event()

    def check(_room: str) -> str:
        ticked.set()
        return rw.PRESENT

    watchdog = rw.RoomWatchdog("wave-room", connected=lambda: True, check=check, ensure=lambda: "", interval=0.01)
    watchdog.start()
    try:
        assert ticked.wait(5)
        assert watchdog.thread is not None and watchdog.thread.daemon
    finally:
        watchdog.stop()
    assert watchdog.thread is not None and not watchdog.thread.is_alive()


def test_agent_entry_starts_the_watchdog_only_in_main() -> None:
    source = (rw.ROOT / "src" / "core" / "agent.py").read_text(encoding="utf-8")
    main_block = source.rsplit('if __name__ == "__main__":', 1)[1]
    assert "start_room_watchdog()" in main_block
    assert source.count("start_room_watchdog()") == 1
