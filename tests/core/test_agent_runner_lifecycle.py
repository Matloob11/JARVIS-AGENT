"""
tests/core/test_agent_runner_lifecycle.py — C-33 JarvisRuntime lifecycle (design §1.2, §4).

Mocked JobContext/AgentSession, no livekit: connect() exactly once; session.start retried <= 3 with
aclose between attempts and a FRESH session per attempt; room handlers registered once; close
reason ERROR -> <= 3 in-place rebuilds then reason 'session_error'; the rebuild re-attaches the
wiring to the NEW session; bounded teardown in the exact order protector -> listener -> assistant
-> session -> room.off -> ctx.shutdown; a pause_agent set before the runtime waits is not lost;
aclose is idempotent and tolerates a partial start.

STEP 1 note: the class lives in src/core/jarvis_runtime.py until the wiring commit (design Q6 keeps
the agent_runner location open); this test imports the module directly so it keeps passing
wherever the wiring step re-exports it from.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from typing import Any

import pytest

from src.core.jarvis_runtime import (
    BACKOFF_S,
    END_BY_COMMAND,
    END_CONNECT_FAILED,
    END_ROOM_DISCONNECTED,
    END_SESSION_ERROR,
    END_SESSION_FAILED,
    RESTART_CAP,
    SESSION_START_RETRIES,
    JarvisRuntime,
)


class CloseReason(enum.Enum):
    ERROR = "error"
    USER_INITIATED = "user_initiated"


class Ev:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class FakeSession:
    def __init__(self, trail: list[str], *, fail_start: bool = False, hang_aclose: bool = False) -> None:
        self._trail = trail
        self._fail_start = fail_start
        self._hang_aclose = hang_aclose
        self.started = 0
        self.aclosed = 0
        self.handlers: dict[str, list[Any]] = {}
        self.off_calls: list[str] = []

    async def start(self) -> None:
        self.started += 1
        if self._fail_start:
            raise RuntimeError("gemini said no")

    async def aclose(self) -> None:
        if self._hang_aclose:
            await asyncio.sleep(60)
        self.aclosed += 1
        self._trail.append("session")

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Any) -> None:
        self.off_calls.append(event)
        if handler in self.handlers.get(event, []):
            self.handlers[event].remove(handler)

    def emit(self, event: str, ev: Any) -> None:
        for handler in list(self.handlers.get(event, [])):
            handler(ev)


class FakeRoom:
    def __init__(self, trail: list[str]) -> None:
        self._trail = trail
        self.name = "jarvis-ai-room"
        self.handlers: list[tuple[str, Any]] = []
        self.off_calls: list[tuple[str, Any]] = []

    def on(self, event: str, handler: Any) -> None:
        self.handlers.append((event, handler))

    def off(self, event: str, handler: Any) -> None:
        self.off_calls.append((event, handler))
        self._trail.append("room_off")

    def emit(self, event: str, *args: Any) -> None:
        for name, handler in list(self.handlers):
            if name == event:
                handler(*args)


class FakeCtx:
    def __init__(self, trail: list[str]) -> None:
        self._trail = trail
        self.room = FakeRoom(trail)
        self.job = Ev(id="JOB_test")
        self.connects = 0
        self.shutdowns: list[str] = []

    async def connect(self) -> None:
        self.connects += 1

    async def shutdown(self, reason: str = "") -> None:
        self.shutdowns.append(reason)
        self._trail.append("shutdown")


class FakeProtector:
    def __init__(self, trail: list[str]) -> None:
        self._trail = trail
        self.cancelled = 0

    async def cancel_all(self) -> None:
        self.cancelled += 1
        self._trail.append("protector")


class FakeListener:
    def __init__(self, trail: list[str]) -> None:
        self._trail = trail
        self.aclosed = 0

    async def aclose(self) -> None:
        self.aclosed += 1
        self._trail.append("listener")


class FakeAssistant:
    def __init__(self, trail: list[str]) -> None:
        self._trail = trail
        self._stop_event = asyncio.Event()
        self.end_reason: str | None = None
        self.shutdowns = 0

    async def shutdown(self) -> None:
        self.shutdowns += 1
        self._trail.append("assistant")


class Harness:
    """A runtime wired to fakes. session_plan: 'ok' or 'fail' per factory call; past the plan's end
    every further session is 'ok'."""

    def __init__(self, *session_plan: str, factory_error_after: int | None = None) -> None:
        self.trail: list[str] = []
        self.ctx = FakeCtx(self.trail)
        self.protector = FakeProtector(self.trail)
        self.listener = FakeListener(self.trail)
        self.assistant = FakeAssistant(self.trail)
        self.sessions: list[FakeSession] = []
        self.sleeps: list[float] = []
        self.attach_calls: list[tuple[FakeSession, bool]] = []
        self._plan = list(session_plan)
        self._factory_error_after = factory_error_after
        self.attrs: list[str] = []

        def factory() -> FakeSession:
            if self._factory_error_after is not None and len(self.sessions) >= self._factory_error_after:
                raise RuntimeError("factory down")
            mode = self._plan[len(self.sessions)] if len(self.sessions) < len(self._plan) else "ok"
            session = FakeSession(self.trail, fail_start=(mode == "fail"))
            self.sessions.append(session)
            return session

        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        def on_started(session: FakeSession, rebuilt: bool) -> None:
            self.attach_calls.append((session, rebuilt))

        self.rt = JarvisRuntime(
            self.ctx,
            session_factory=factory,
            on_session_started=on_started,
            protector=self.protector,
            listener=self.listener,
            assistant=self.assistant,
            set_session_attr=self.attrs.append,
            sleep=fake_sleep,
        )


async def _drain(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


# ---- start --------------------------------------------------------------------------------------


async def test_start_connects_once_and_second_start_raises():
    h = Harness("ok")
    assert await h.rt.start() is True
    assert h.ctx.connects == 1
    with pytest.raises(RuntimeError):
        await h.rt.start()
    assert h.ctx.connects == 1


async def test_start_logs_runtime_started(caplog):
    h = Harness("ok")
    with caplog.at_level(logging.INFO, logger="JARVIS-RUNTIME"):
        await h.rt.start()
    assert any("RUNTIME_STARTED" in r.getMessage() and "JOB_test" in r.getMessage()
               for r in caplog.records)
    assert h.attrs == ["starting", "running"]


async def test_session_start_retried_with_aclose_between_and_fresh_sessions():
    h = Harness("fail", "fail", "ok")
    assert await h.rt.start() is True
    assert len(h.sessions) == 3  # a FRESH session per attempt
    assert h.sessions[0].aclosed == 1 and h.sessions[1].aclosed == 1  # aclose after each failure
    assert h.sessions[2].aclosed == 0
    assert h.rt.session is h.sessions[2]
    assert h.sleeps == [BACKOFF_S[0], BACKOFF_S[1]]  # 2/4 s between the three attempts
    assert h.attach_calls == [(h.sessions[2], False)]


async def test_all_start_attempts_failing_ends_the_job_session_failed():
    h = Harness("fail", "fail", "fail")
    assert await h.rt.start() is False
    assert len(h.sessions) == SESSION_START_RETRIES
    assert all(s.aclosed == 1 for s in h.sessions)
    assert h.rt.closed.is_set()
    assert await h.rt.wait_closed() == END_SESSION_FAILED
    await h.rt.aclose(h.rt.reason)
    assert h.ctx.shutdowns == [END_SESSION_FAILED]


async def test_connect_failure_closes_with_connect_failed(caplog):
    """F5: a raising ctx.connect must not escape start() with reason None — the entrypoint's
    final aclose(rt.reason) would then log the misleading 'ended_by_command'."""
    h = Harness("ok")

    async def bad_connect() -> None:
        raise RuntimeError("livekit down")

    h.ctx.connect = bad_connect
    with caplog.at_level(logging.ERROR, logger="JARVIS-RUNTIME"):
        assert await h.rt.start() is False
    assert await h.rt.wait_closed() == END_CONNECT_FAILED
    assert h.sessions == []  # no session was ever built
    assert any("ctx.connect failed" in r.getMessage() for r in caplog.records)
    await h.rt.aclose(h.rt.reason)
    assert h.ctx.shutdowns == [END_CONNECT_FAILED]  # truthful reason reaches job_end


async def test_hanging_connect_hits_the_cap_and_closes_connect_failed():
    h = Harness("ok")

    async def hanging_connect() -> None:
        await asyncio.Event().wait()

    h.ctx.connect = hanging_connect
    h.rt._connect_timeout_s = 0.05  # the 60 s cap, shrunk for the test
    assert await h.rt.start() is False
    assert h.rt.reason == END_CONNECT_FAILED


# ---- a close requested while the job still starts (C-33 step B review) ---------------------------


async def test_a_close_requested_during_the_connect_starts_no_session():
    """A framework shutdown or a lost room while the job still connects ends it without a session."""
    h = Harness("ok")

    async def connect_then_close() -> None:
        h.rt.request_close(END_ROOM_DISCONNECTED)

    h.ctx.connect = connect_then_close
    assert await h.rt.start() is False
    assert h.sessions == [] and h.rt.reason == END_ROOM_DISCONNECTED


async def test_a_close_requested_between_start_attempts_stops_the_retries():
    h = Harness("fail", "ok")

    async def sleep_then_close(seconds: float) -> None:
        h.sleeps.append(seconds)
        h.rt.request_close(END_ROOM_DISCONNECTED)

    h.rt._sleep = sleep_then_close
    assert await h.rt.start() is False
    assert len(h.sessions) == 1  # no second attempt
    assert h.rt.reason == END_ROOM_DISCONNECTED  # the requested reason, not 'session_failed'


async def test_a_close_requested_while_the_session_starts_skips_the_job_setup():
    h = Harness("ok")

    async def start_then_close(session: FakeSession) -> None:
        await session.start()
        h.rt.request_close(END_ROOM_DISCONNECTED)

    h.rt._start_session_hook = start_then_close
    assert await h.rt.start() is False  # the entrypoint then skips its setup
    assert h.ctx.room.handlers == []  # no room handlers for a job that is already ending
    await h.rt.aclose(h.rt.reason)
    assert h.sessions[0].aclosed == 1 and h.ctx.shutdowns == [END_ROOM_DISCONNECTED]


# ---- the close_session hook (C-33 step B review) -------------------------------------------------


async def test_failed_attempts_and_replaced_sessions_go_through_the_close_session_hook():
    """A session that is not the live one is released by the wiring's close_session hook (which also frees what
    livekit-agents leaves behind for it); the live session is closed by aclose() itself."""
    h = Harness("fail", "ok")
    released: list[FakeSession] = []

    async def close_session(session: FakeSession) -> None:
        released.append(session)
        await session.aclose()

    h.rt._close_session_hook = close_session
    assert await h.rt.start() is True
    assert released == [h.sessions[0]]  # the failed attempt
    h.sessions[1].emit("close", Ev(reason=CloseReason.ERROR))
    for _ in range(40):
        await asyncio.sleep(0)
        if len(h.sessions) == 3 and h.rt.session is h.sessions[2]:
            break
    assert released == [h.sessions[0], h.sessions[1]]  # the replaced one, before its successor existed
    await h.rt.aclose("paused_by_owner")
    assert released == [h.sessions[0], h.sessions[1]] and h.sessions[2].aclosed == 1


async def test_a_hanging_close_session_hook_is_bounded():
    h = Harness("fail", "ok")

    async def hang(_session: FakeSession) -> None:
        await asyncio.Event().wait()

    h.rt._close_session_hook = hang
    h.rt._release_timeout_s = 0.05  # 20 s in production
    assert await asyncio.wait_for(h.rt.start(), 5) is True
    assert h.rt.session is h.sessions[1]


async def test_room_handlers_registered_once_and_disconnect_closes():
    h = Harness("ok")
    await h.rt.start()
    assert [e for e, _ in h.ctx.room.handlers] == ["disconnected"]
    h.ctx.room.emit("disconnected")
    assert await h.rt.wait_closed() == END_ROOM_DISCONNECTED


# ---- session error restarts ---------------------------------------------------------------------


async def test_error_close_rebuilds_in_place_and_reattaches_wiring(caplog):
    h = Harness("ok")
    await h.rt.start()
    first = h.sessions[0]
    with caplog.at_level(logging.WARNING, logger="JARVIS-RUNTIME"):
        first.emit("close", Ev(reason=CloseReason.ERROR))
        await _drain()
    assert len(h.sessions) == 2
    assert first.aclosed == 1  # the old session is aclosed, never .stop()ped
    assert h.rt.session is h.sessions[1]
    assert h.attach_calls[-1] == (h.sessions[1], True)  # wiring re-attached to the NEW session
    assert first.off_calls == ["close", "error"]  # old handlers unhooked
    assert [e for e, _ in h.ctx.room.handlers] == ["disconnected"]  # room handlers untouched
    assert any("RUNTIME_SESSION_RESTART n=1" in r.getMessage() for r in caplog.records)
    assert not h.rt.closed.is_set()
    assert h.attrs == ["starting", "running", "restarting", "running"]


async def test_non_error_close_does_not_restart():
    h = Harness("ok")
    await h.rt.start()
    h.sessions[0].emit("close", Ev(reason=CloseReason.USER_INITIATED))
    await _drain()
    assert len(h.sessions) == 1
    assert not h.rt.closed.is_set()


async def test_an_unrecoverable_error_event_waits_for_the_close_that_starts_the_rebuild():
    """C-33 step B review: livekit emits the error BEFORE it closes the session, and the close comes only after the
    old activity drained (in-flight tools included). Rebuilding on the error itself started the job's one assistant
    while the old activity still drained, whose late cleanup then nulled the new session's activity."""
    h = Harness("ok")
    await h.rt.start()
    h.sessions[0].emit("error", Ev(error=Ev(recoverable=False)))
    await _drain()
    assert len(h.sessions) == 1  # no rebuild yet: the old session is still closing
    h.sessions[0].emit("close", Ev(reason=CloseReason.ERROR))
    await _drain()
    assert len(h.sessions) == 2 and h.rt.session is h.sessions[1]  # exactly one rebuild

    # a recoverable error is livekit-agents' own business: no rebuild
    h.sessions[1].emit("error", Ev(error=Ev(recoverable=True)))
    await _drain()
    assert len(h.sessions) == 2
    assert not h.rt.closed.is_set()


async def test_a_session_that_never_closes_after_an_unrecoverable_error_ends_the_job():
    """A tool that never returns keeps the old session's drain, so its close never comes: the job ends instead of
    sitting in the room without a session."""
    h = Harness("ok")
    h.rt._error_close_wait_s = 0.05  # 120 s in production
    await h.rt.start()
    h.sessions[0].emit("error", Ev(error=Ev(recoverable=False)))
    assert await asyncio.wait_for(h.rt.wait_closed(), 2) == END_SESSION_ERROR
    assert len(h.sessions) == 1  # never rebuilt on top of the draining session


async def test_restart_cap_three_per_window_then_session_error(caplog):
    h = Harness("ok")
    await h.rt.start()
    with caplog.at_level(logging.WARNING, logger="JARVIS-RUNTIME"):
        for n in range(RESTART_CAP + 1):  # 4 error closes: 3 rebuilds, then the cap
            h.rt.session.emit("close", Ev(reason=CloseReason.ERROR))
            await _drain()
    assert len(h.sessions) == 1 + RESTART_CAP
    assert await h.rt.wait_closed() == END_SESSION_ERROR
    restarts = [r.getMessage() for r in caplog.records if "RUNTIME_SESSION_RESTART" in r.getMessage()]
    assert restarts == [f"RUNTIME_SESSION_RESTART n={i}" for i in range(1, RESTART_CAP + 1)]
    assert [s for s in h.sleeps if s in BACKOFF_S] == list(BACKOFF_S)  # 2/4/8 s backoff

    await h.rt.aclose(h.rt.reason)
    assert h.ctx.shutdowns == [END_SESSION_ERROR]  # the job ends; the worker process never dies here


async def test_failed_rebuild_start_acloses_the_half_started_session():
    """F3: on a rebuild whose factory succeeds but whose session.start fails, the half-started
    session (it can hold a Gemini websocket) is aclosed before the next pass — same rule as
    _start_session_with_retries."""
    h = Harness("ok", "fail")  # session 2 = the rebuild: factory ok, start raises
    await h.rt.start()
    h.sessions[0].emit("close", Ev(reason=CloseReason.ERROR))
    for _ in range(40):
        await asyncio.sleep(0)
        if len(h.sessions) >= 3 and h.rt.session is h.sessions[2]:
            break
    assert len(h.sessions) == 3
    assert h.sessions[1].started == 1
    assert h.sessions[1].aclosed == 1  # the half-started rebuild did not leak
    assert h.rt.session is h.sessions[2]  # the next pass recovered with a fresh session
    assert h.attach_calls[-1] == (h.sessions[2], True)
    assert not h.rt.closed.is_set()


async def test_failed_rebuild_counts_against_the_cap():
    h = Harness("ok", factory_error_after=1)  # every rebuild's factory call raises
    await h.rt.start()
    h.sessions[0].emit("close", Ev(reason=CloseReason.ERROR))
    for _ in range(40):
        await asyncio.sleep(0)
        if h.rt.closed.is_set():
            break
    assert await h.rt.wait_closed() == END_SESSION_ERROR


# ---- stop-event bridge ---------------------------------------------------------------------------


async def test_pause_agent_before_the_runtime_waits_is_not_lost():
    h = Harness("ok")
    h.assistant.end_reason = "paused_by_owner"
    h.assistant._stop_event.set()  # pause_agent arrived before start finished (design §4.7)
    await h.rt.start()
    await _drain()
    assert await h.rt.wait_closed() == "paused_by_owner"


async def test_stop_event_without_reason_uses_end_by_command():
    h = Harness("ok")
    await h.rt.start()
    h.assistant._stop_event.set()
    await _drain()
    assert await h.rt.wait_closed() == END_BY_COMMAND


# ---- teardown -----------------------------------------------------------------------------------


async def test_aclose_exact_order_and_shutdown_reason(caplog):
    h = Harness("ok")
    await h.rt.start()
    with caplog.at_level(logging.INFO, logger="JARVIS-RUNTIME"):
        await h.rt.aclose("paused_by_owner")
    assert h.trail == ["protector", "listener", "assistant", "session", "room_off", "shutdown"]
    assert h.ctx.shutdowns == ["paused_by_owner"]
    assert h.protector.cancelled == 1 and h.listener.aclosed == 1 and h.assistant.shutdowns == 1
    assert any("RUNTIME_CLOSED reason=paused_by_owner" in r.getMessage() for r in caplog.records)
    assert h.rt.closed.is_set()


async def test_aclose_is_idempotent():
    h = Harness("ok")
    await h.rt.start()
    await h.rt.aclose("x")
    await h.rt.aclose("y")
    assert h.ctx.shutdowns == ["x"]
    assert h.protector.cancelled == 1


async def test_aclose_tolerates_partial_start():
    h = Harness("ok")
    rt = JarvisRuntime(h.ctx, session_factory=lambda: FakeSession(h.trail))
    await rt.aclose("session_failed")  # no session, no listener, no protector, no assistant
    assert h.ctx.shutdowns == ["session_failed"]


async def test_aclose_bounded_on_wedged_session():
    h = Harness("ok")
    await h.rt.start()
    wedged = FakeSession(h.trail, hang_aclose=True)
    h.rt.session = wedged
    h.rt._session_close_timeout_s = 0.05  # the 60 s hang must not hold teardown (design §4.10)
    await asyncio.wait_for(h.rt.aclose("session_error"), 2.0)
    assert h.ctx.shutdowns == ["session_error"]  # ctx.shutdown ran regardless


async def test_aclose_uses_end_job_hook_instead_of_ctx_shutdown():
    ends: list[str] = []
    h = Harness("ok")
    rt = JarvisRuntime(
        h.ctx,
        session_factory=lambda: FakeSession(h.trail),
        end_job=lambda reason: ends.append(reason),  # the C-23 end block, console-job skip included
    )
    await rt.start()
    await rt.aclose("room_disconnected")
    assert ends == ["room_disconnected"]
    assert h.ctx.shutdowns == []  # the hook owns ctx.shutdown


async def test_close_events_after_aclose_are_ignored():
    h = Harness("ok")
    await h.rt.start()
    session = h.rt.session
    await h.rt.aclose("x")
    session.emit("close", Ev(reason=CloseReason.ERROR))
    await _drain()
    assert len(h.sessions) == 1  # no rebuild after teardown
