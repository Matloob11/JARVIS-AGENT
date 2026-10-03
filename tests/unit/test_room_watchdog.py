"""src/core/room_watchdog.py: the PC worker re-runs ensure_room.py when the room loses its agent.

Everything is injected or mocked: no network, no process and no wait on the 60 s clock (one test runs the real
thread at a 10 ms interval). Settings are throwaway values.
"""
# pylint: disable=protected-access,redefined-outer-name,unnecessary-lambda,no-member  # protobuf members are generated
from __future__ import annotations

import logging
import subprocess
import sys
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


def test_run_ensure_room_captures_output_and_returns_the_result_line(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    output = (f"[ROOM] LiveKit API: https://{FAKE_HOST}:7443\n[ROOM] Ensuring room: wave-room\n"
              "[ROOM] Result: OK, an agent of the current worker is in the room\n").encode("utf-8")

    def fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

    monkeypatch.setattr(rw, "_run", fake_run)
    assert rw.run_ensure_room() == "[ROOM] Result: OK, an agent of the current worker is in the room"
    assert seen["cmd"] == [sys.executable, str(rw.ENSURE_ROOM)]
    assert seen["kwargs"]["cwd"] == str(rw.ROOT)
    assert seen["kwargs"]["timeout"] == rw.ENSURE_TIMEOUT_S
    assert seen["kwargs"]["capture_output"] is True


def test_run_ensure_room_without_result_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rw, "_run", lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=b"[ERROR] x\n"))
    assert rw.run_ensure_room() == "ensure_room.py exited 1 without a result line"


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
