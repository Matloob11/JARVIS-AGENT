"""C-33 step A (08 Oct 2026): a job never outlives its room or its session.

On the Hp laptop seven jobs outlived a laptop sleep or a network loss: the rtc engine gave up reconnecting without a
'disconnected' event and the Gemini session closed itself on an unrecoverable error, but nothing ended the job. They
kept every loop and their bridge listener (active_jobs=8; one dashboard SIM search ran twice). These tests pin the
pieces of the fix: the room liveness check, the job's own task protector, a bounded teardown that closes the session
with aclose(), and one bridge HTTP client per event loop (C-39 step 2, K-57).
"""
from __future__ import annotations

import asyncio
import threading
import types
import weakref
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit import rtc

from services.utils import jarvis_autonomous, jarvis_bridge
from src.core import agent_runner as ar
from src.core import jarvis_runtime

CONNECTED = rtc.ConnectionState.CONN_CONNECTED
RECONNECTING = rtc.ConnectionState.CONN_RECONNECTING


class _StateRoom:
    """A room whose connection_state walks through a list (the last value repeats), with an optional async probe."""

    def __init__(self, states, probe_error: Exception | None = None) -> None:
        self._states = list(states)
        self.reads = 0
        self.probes = 0
        self._probe_error = probe_error

    @property
    def connection_state(self):
        self.reads += 1
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]

    async def get_rtc_stats(self):
        self.probes += 1
        if self._probe_error is not None:
            raise self._probe_error
        return object()


async def _run_liveness(room, *, dead_after: float, interval: float = 1.0, max_checks: int = 200) -> list[str]:
    ended: list[str] = []
    checks = 0
    real_sleep = asyncio.sleep

    async def tick(_delay):
        nonlocal checks
        checks += 1
        if checks > max_checks:
            raise asyncio.CancelledError
        await real_sleep(0)

    original = ar.asyncio.sleep
    ar.asyncio.sleep = tick
    try:
        await ar._room_liveness_loop(room, ended.append, interval=interval, dead_after=dead_after)
    except asyncio.CancelledError:
        pass
    finally:
        ar.asyncio.sleep = original
    return ended


# ---------------------------------------------------------------- the room liveness check


@pytest.mark.asyncio
async def test_a_room_that_stays_unconnected_ends_the_job_after_the_awake_budget():
    room = _StateRoom([CONNECTED, RECONNECTING])
    ended = await _run_liveness(room, dead_after=3.0)  # three consecutive non-connected checks
    assert ended == [ar.END_ROOM_DISCONNECTED]
    assert room.reads == 4  # one connected check, then three misses


@pytest.mark.asyncio
async def test_a_reconnect_inside_the_budget_resets_the_count():
    room = _StateRoom([RECONNECTING, RECONNECTING, CONNECTED, RECONNECTING, RECONNECTING, CONNECTED])
    ended = await _run_liveness(room, dead_after=3.0, max_checks=40)
    assert ended == []  # never three misses in a row: the rtc engine reconnected in time


@pytest.mark.asyncio
async def test_a_closed_engine_behind_a_stale_connected_state_is_caught_by_the_probe(monkeypatch):
    """06-08 Oct: no state change ever reached Python after 'failed to reconnect'. The engine probe still fails."""
    monkeypatch.setattr(ar, "ROOM_PROBE_EVERY", 2)
    room = _StateRoom([CONNECTED], probe_error=ConnectionError("engine is closed"))
    ended = await _run_liveness(room, dead_after=4.0)  # probe every 2 checks -> 2 failed probes needed
    assert ended == [ar.END_ROOM_DISCONNECTED]
    assert room.probes == 2


@pytest.mark.asyncio
async def test_a_healthy_room_is_never_ended():
    room = _StateRoom([CONNECTED])
    ended = await _run_liveness(room, dead_after=3.0, max_checks=60)
    assert ended == [] and room.probes >= 1


@pytest.mark.asyncio
async def test_a_stand_in_room_without_state_or_probe_is_not_watched():
    for room in (types.SimpleNamespace(name="x"), MagicMock(name="room")):
        assert ar._room_watchable(room) is False
        assert await _run_liveness(room, dead_after=1.0) == []


@pytest.mark.asyncio
async def test_a_real_rtc_room_is_watchable():
    assert ar._room_watchable(rtc.Room()) is True


def test_the_error_close_reason_check_matches_livekit_and_plain_values():
    from livekit.agents.voice.events import CloseReason

    assert jarvis_runtime.is_error_close_reason(CloseReason.ERROR) is True
    assert jarvis_runtime.is_error_close_reason("error") is True
    for other in (CloseReason.USER_INITIATED, CloseReason.JOB_SHUTDOWN, CloseReason.PARTICIPANT_DISCONNECTED, None):
        assert jarvis_runtime.is_error_close_reason(other) is False


# ---------------------------------------------------------------- one task protector per job


@pytest.mark.asyncio
async def test_two_jobs_protectors_never_cancel_each_others_loops():
    first, second = jarvis_autonomous.job_protector(), jarvis_autonomous.job_protector()

    async def forever():
        await asyncio.Event().wait()

    await first.run_protected("memory_loop", forever)
    await second.run_protected("memory_loop", forever)
    await asyncio.sleep(0)
    await first.cancel_all()  # job 1 ends
    assert second.is_task_healthy("memory_loop")  # job 2 keeps its loop
    await second.cancel_all()
    jarvis_autonomous.release_job_protector(first)
    jarvis_autonomous.release_job_protector(second)


@pytest.mark.asyncio
async def test_live_job_protectors_count_in_the_load_and_health_status():
    protector = jarvis_autonomous.job_protector()

    async def forever():
        await asyncio.Event().wait()

    await protector.run_protected("c33_probe_loop", forever)
    await asyncio.sleep(0)
    assert "c33_probe_loop" in jarvis_autonomous.all_task_status()
    await protector.cancel_all()
    jarvis_autonomous.release_job_protector(protector)
    assert "c33_probe_loop" not in jarvis_autonomous.all_task_status()
    jarvis_autonomous.release_job_protector(None)  # tolerated


@pytest.mark.asyncio
async def test_background_tasks_run_on_the_given_protector_not_the_global_one(monkeypatch):
    started: list[str] = []
    protector = types.SimpleNamespace(_tasks={},
                                      run_protected=AsyncMock(side_effect=lambda name, *a: started.append(name)))
    global_run = AsyncMock()
    monkeypatch.setattr(ar.autonomous_protector, "run_protected", global_run)
    monkeypatch.setattr(ar, "ClipboardMonitor", MagicMock(side_effect=AssertionError("no clipboard in remote")))
    await ar._start_background_tasks(MagicMock(), MagicMock(), mode="remote", protector=protector)
    global_run.assert_not_awaited()
    assert "memory_loop" in started and "ui_command_listener" in started
    adaptation = [c for c in protector.run_protected.await_args_list if c.args[0] == "adaptation_loop"]
    assert adaptation[0].args[2] is protector  # the adaptation loop throttles this job's loops only


# ---------------------------------------------------------------- bounded teardown


@pytest.mark.asyncio
async def test_cleanup_cancels_the_jobs_protector_closes_the_assistant_and_the_session(monkeypatch):
    global_cancel = AsyncMock()
    monkeypatch.setattr(ar.autonomous_protector, "cancel_all", global_cancel)
    protector = types.SimpleNamespace(cancel_all=AsyncMock())
    assistant = types.SimpleNamespace(shutdown=AsyncMock())
    session = types.SimpleNamespace(aclose=AsyncMock())

    async def tracked():
        await asyncio.Event().wait()

    task = asyncio.create_task(tracked())
    await ar._cleanup_session_resources(session, [task], protector=protector, assistant=assistant)
    protector.cancel_all.assert_awaited_once()
    global_cancel.assert_not_awaited()  # never another job's loops
    assistant.shutdown.assert_awaited_once()
    session.aclose.assert_awaited_once()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_a_wedged_session_close_cannot_hold_the_teardown(monkeypatch):
    monkeypatch.setattr(ar, "CLEANUP_STEP_TIMEOUT_S", 0.05)

    async def wedged():
        await asyncio.Event().wait()

    session = types.SimpleNamespace(aclose=wedged)
    assistant = types.SimpleNamespace(shutdown=MagicMock(side_effect=RuntimeError("boom")))
    protector = types.SimpleNamespace(cancel_all=AsyncMock())
    await asyncio.wait_for(ar._cleanup_session_resources(session, [], protector=protector, assistant=assistant), 2)


@pytest.mark.asyncio
async def test_a_loop_that_swallows_its_cancel_cannot_hold_the_teardown(monkeypatch):
    """C-33 (08 Oct): the bug hunter swallowed its cancel, cancel_all waited for it forever and the job never ended
    (a session-less agent held the room for 1.5 h). cancel_all now waits CANCEL_WAIT_S and names the loop."""
    warnings: list[str] = []
    monkeypatch.setattr(jarvis_autonomous.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    protector = jarvis_autonomous.TaskProtector()
    stop = asyncio.Event()

    async def stubborn() -> None:
        while not stop.is_set():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass  # swallowed, like monitor_logs before the fix

    await protector.run_protected("stubborn_loop", stubborn)
    task = protector.get_task("stubborn_loop")
    await asyncio.sleep(0)
    await asyncio.wait_for(protector.cancel_all(timeout=0.05), 2)
    assert any("stubborn_loop ignored its cancel" in line for line in warnings)
    assert protector.task_names == []
    stop.set()  # let it end, so the test leaves no task behind
    task.cancel()
    await asyncio.wait({task}, timeout=2)
    assert task.done()


@pytest.mark.asyncio
async def test_a_tracked_task_that_swallows_its_cancel_cannot_hold_the_cleanup(monkeypatch):
    monkeypatch.setattr(ar, "CLEANUP_STEP_TIMEOUT_S", 0.05)
    stop = asyncio.Event()

    async def stubborn() -> None:
        while not stop.is_set():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

    task = asyncio.ensure_future(stubborn())
    await asyncio.sleep(0)
    protector = types.SimpleNamespace(cancel_all=AsyncMock())
    await asyncio.wait_for(ar._cleanup_session_resources(None, [task], protector=protector), 2)
    stop.set()
    task.cancel()
    await asyncio.wait({task}, timeout=2)
    assert task.done()


@pytest.mark.asyncio
async def test_the_runtime_teardown_bounds_a_protector_that_never_finishes(monkeypatch):
    monkeypatch.setattr(jarvis_runtime, "PROTECTOR_CANCEL_TIMEOUT_S", 0.05)
    shutdowns: list[str] = []

    async def never() -> None:
        await asyncio.Event().wait()

    ctx = types.SimpleNamespace(room=None, job=types.SimpleNamespace(id="AJ_x"),
                                shutdown=lambda reason="": shutdowns.append(reason))
    runtime = jarvis_runtime.JarvisRuntime(ctx, session_factory=lambda: None,
                                           protector=types.SimpleNamespace(cancel_all=never))
    await asyncio.wait_for(runtime.aclose("session_error"), 2)
    assert shutdowns == ["session_error"]


# ---------------------------------------------------------------- one bridge HTTP client per event loop (K-57)


def test_each_event_loop_keeps_its_own_client_and_the_ssl_context_is_built_once(monkeypatch):
    monkeypatch.setattr(jarvis_bridge, "_http_by_loop", weakref.WeakKeyDictionary())
    monkeypatch.setattr(jarvis_bridge, "_ssl_context", None)
    builds: list[int] = []
    real_create = jarvis_bridge._create_ssl_context
    monkeypatch.setattr(jarvis_bridge, "_create_ssl_context", lambda: builds.append(1) or real_create())
    clients: dict[str, list[Any]] = {}

    def job(name: str) -> None:
        async def run():
            first = await jarvis_bridge.get_http_client()
            again = await jarvis_bridge.get_http_client()
            clients[name] = [first, again, first.is_closed]
            await jarvis_bridge.aclose_loop_client()
            clients[name].append(first.is_closed)
            rebuilt = await jarvis_bridge.get_http_client()
            clients[name].append(rebuilt is not first)
            await jarvis_bridge.aclose_loop_client()

        asyncio.run(run())

    threads = [threading.Thread(target=job, args=(name,)) for name in ("job-1", "job-2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    one, two = clients["job-1"], clients["job-2"]
    assert one[0] is one[1] and two[0] is two[1]  # one client per loop, reused
    assert one[0] is not two[0]  # never shared across loops (no rebuild on every switch)
    assert one[2] is False and one[3] is True and one[4] is True  # closed at job end, rebuilt only after that
    assert len(builds) == 1  # the expensive SSL context once per process


def test_an_ended_loops_entry_is_dropped_so_the_loop_can_be_collected(monkeypatch):
    """C-33 review: livekit-agents stops a job's loop without closing it, sometimes before the job closed its client.
    The next lookup from any live loop drops the dead loop's entry, so the registry never pins it."""
    import gc

    monkeypatch.setattr(jarvis_bridge, "_http_by_loop", weakref.WeakKeyDictionary())
    refs: list[Any] = []

    def job_thread() -> None:
        loop = asyncio.new_event_loop()  # like livekit's proc client: run_until_complete, never close
        loop.run_until_complete(jarvis_bridge.get_http_client())
        refs.append(weakref.ref(loop))

    thread = threading.Thread(target=job_thread)
    thread.start()
    thread.join(10)
    assert len(jarvis_bridge._http_by_loop) == 1  # the ended job's entry is still there

    async def next_job():
        jarvis_bridge._loop_http()
        assert list(jarvis_bridge._http_by_loop) == [asyncio.get_running_loop()]
        await jarvis_bridge.aclose_loop_client()
        assert len(jarvis_bridge._http_by_loop) == 0  # the job's end drops its whole entry

    asyncio.run(next_job())
    gc.collect()
    assert refs[0]() is None  # nothing in the registry keeps the dead loop alive


@pytest.mark.asyncio
async def test_lock_and_semaphore_belong_to_the_running_loop(monkeypatch):
    monkeypatch.setattr(jarvis_bridge, "_http_by_loop", weakref.WeakKeyDictionary())
    assert jarvis_bridge._get_lock() is jarvis_bridge._get_lock()
    semaphore = jarvis_bridge._get_semaphore()
    assert semaphore is jarvis_bridge._get_semaphore() and semaphore._value == 20
