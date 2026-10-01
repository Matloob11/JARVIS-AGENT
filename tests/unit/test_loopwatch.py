"""Plan task C-31: event-loop stall watchdog (services/utils/jarvis_loopwatch.py) and its wiring.

Stalls are simulated in the test's own event loop: a dummy @jarvis_tool that calls time.sleep()
blocks the loop the way a sync call inside a real tool does, and a ctypes sleep that holds the GIL
stops every Python thread without using CPU, the way a laptop sleep or Modern Standby pauses the
agent. A job end is replayed as LiveKit's THREAD executor (the Windows default) does it: the job
thread's loop stops with the entrypoint still pending and the thread exits. No sleep is longer
than 0.6 s. Dummy tools are registered under 'c31_' names and removed again; tool_sync posts and
the audit log are replaced, so nothing reaches the network, the live agent or logs/.
"""
# pylint: disable=protected-access
import asyncio
import ctypes
import gc
import inspect
import logging
import os
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents import JobContext
from livekit.agents.llm import function_tool
from livekit.agents.llm.utils import prepare_function_arguments

from services.ai_core.jarvis_plugin_manager import jarvis_tool, plugin_manager
from services.utils import jarvis_audit, jarvis_bridge, jarvis_loopwatch
from services.utils.jarvis_logger import RedactingFormatter
from services.utils.jarvis_loopwatch import RESUME_TAG, STALL_TAG, LoopStall, LoopWatchdog
from services.utils.jarvis_resilience import resilience_manager
from services.utils.jarvis_security import redact_secrets
from src.core import agent_runner

ROOT = Path(__file__).resolve().parents[2]
BLOCK_S = 0.6  # every simulated stall or pause
SETTLE_S = 0.3  # time for the late heartbeat to report afterwards


@pytest.fixture(autouse=True)
def _offline_tools(monkeypatch):
    """c31_ tools are unregistered afterwards; their tool_sync posts and audits go nowhere."""
    with plugin_manager._tools_lock:
        registered = plugin_manager.tools.copy()
    monkeypatch.setattr(jarvis_bridge, "notify_event", AsyncMock())
    monkeypatch.setattr(jarvis_audit, "log_tool", MagicMock())
    yield
    with plugin_manager._tools_lock:
        plugin_manager.tools[:] = registered
    for name in [name for name in resilience_manager._breakers if name.startswith("c31_")]:
        del resilience_manager._breakers[name]


class _Records(logging.Handler):
    """A private logger handed to one watchdog, and every record it received."""

    def __init__(self, name: str) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.logger = logging.getLogger(f"tests.c31.{name}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.logger.addHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)  # also called from the watcher thread

    def tagged(self, tag: str) -> list[logging.LogRecord]:
        return [record for record in self.records if record.getMessage().startswith(tag)]


@pytest.fixture(name="records")
def _records(request):
    collector = _Records(request.node.name)
    yield collector
    collector.logger.removeHandler(collector)


async def _call_tool(name: str) -> Any:
    """Calls a registered tool the way LiveKit does: function_tool around its wrapper."""
    wrapper = next(tool for tool in plugin_manager.tools if tool.__name__ == name)
    lk_tool = function_tool(wrapper)
    args, kwargs = prepare_function_arguments(fnc=lk_tool, json_arguments="{}")
    return await lk_tool(*args, **kwargs)


def _pause_whole_process(seconds: float) -> None:
    """Sleeps holding the GIL: no Python thread runs and no CPU is used, as in a laptop sleep."""
    if sys.platform == "win32":
        ctypes.PyDLL("kernel32").Sleep(int(seconds * 1000))
    else:
        ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


def _watcher_threads(name: str) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate()
            if thread.name == f"jarvis-loopwatch-{name}"]


def _assert_watcher_ends(name: str) -> None:
    for thread in _watcher_threads(name):
        thread.join(timeout=2.0)
    assert not _watcher_threads(name)


async def _run_around(dog: LoopWatchdog, block: Callable[[], Any]) -> Any:
    """A few clean heartbeats, then block() on the loop, then time for the report; stops dog."""
    dog.start()
    await asyncio.sleep(0.25)
    result = block()
    if inspect.isawaitable(result):
        result = await result
    await asyncio.sleep(SETTLE_S)
    await dog.stop()
    _assert_watcher_ends(dog.name)
    return result


def _abandon(loop: asyncio.AbstractEventLoop) -> None:
    """Closes a loop whose tasks stay pending for good, as at a real job end, without warnings."""
    for task in asyncio.all_tasks(loop):
        task._log_destroy_pending = False
    loop.close()


def _watcher_ended(records: _Records) -> list[str]:
    return [record.getMessage() for record in records.records
            if record.getMessage().startswith("Loop watcher thread ended")]


def _tracker(tasks: set[asyncio.Task[Any]]) -> Callable[[Any], asyncio.Task[Any]]:
    """The entrypoint's track(): each task stays in tasks until it is done."""
    def track(coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task
    return track


class _JobShutdown:
    """
    The shutdown half of a LiveKit JobContext: LiveKit's own add_shutdown_callback, and end_job()
    running the callbacks as LiveKit does when the room disconnects. LiveKit never cancels the
    entrypoint then, so its _cleanup_session_resources never runs.
    """

    add_shutdown_callback = JobContext.add_shutdown_callback

    def __init__(self) -> None:
        self._shutdown_callbacks: list[Callable[[str], Any]] = []

    async def end_job(self, reason: str = "room disconnected") -> None:
        await asyncio.gather(*(callback(reason) for callback in self._shutdown_callbacks))


async def test_sync_sleep_inside_a_tool_logs_exactly_one_stall(records):
    @jarvis_tool(execution_timeout=5.0)
    async def c31_block_loop() -> str:
        """Blocks the event loop like a sync call inside a real tool."""
        time.sleep(BLOCK_S)
        return "c31 done"

    dog = LoopWatchdog(context=plugin_manager.running_tools, log=records.logger, name="c31-block")
    assert await _run_around(dog, lambda: _call_tool("c31_block_loop")) == "c31 done"

    stalls = records.tagged(STALL_TAG)
    assert len(stalls) == 1 and stalls[0].levelno == logging.WARNING
    message = stalls[0].getMessage()
    assert message.startswith("EVENT_LOOP_STALL ms=") and " tools=c31_block_loop " in message
    # The watcher thread saw the loop thread inside the tool while it was blocked.
    innermost = message.split(" stack=", 1)[1].split(" <- ")[0]
    assert innermost.startswith("unit/test_loopwatch.py:") and innermost.endswith(" c31_block_loop")
    assert dog.stall_count == 1 and 400 <= dog.last_stall.ms <= 1500
    assert dog.critical_count == 0 and dog.resume_count == 0


async def test_awaiting_tool_logs_no_stall(records):
    @jarvis_tool(execution_timeout=5.0)
    async def c31_await_loop() -> str:
        """Takes as long, but awaits: the loop keeps running."""
        await asyncio.sleep(BLOCK_S)
        return "c31 done"

    dog = LoopWatchdog(context=plugin_manager.running_tools, log=records.logger, name="c31-await")
    assert await _run_around(dog, lambda: _call_tool("c31_await_loop")) == "c31 done"

    assert records.tagged(STALL_TAG) == [] and dog.stall_count == 0
    assert dog.ticks >= 6  # the heartbeat went on while the tool awaited


async def test_a_deep_library_call_still_names_the_own_frame_that_made_it(records, monkeypatch):
    # More library frames than are kept, as with urlopen() (10 frames from request.py to socket.py).
    monkeypatch.setattr(jarvis_loopwatch, "STACK_FRAMES", 1)

    async def c31_wait_in_library() -> None:
        threading.Event().wait(BLOCK_S)  # blocks the loop two stdlib frames down

    dog = LoopWatchdog(log=records.logger, name="c31-deep")
    await _run_around(dog, c31_wait_in_library)

    (stall,) = records.tagged(STALL_TAG)
    innermost, skipped, own = stall.getMessage().split(" stack=", 1)[1].split(" <- ")
    # A file at the top of a library keeps its bare name; any other file shows its folder.
    assert innermost.startswith("threading.py:") and innermost.endswith(" wait")
    assert skipped == "..."
    assert own.startswith("unit/test_loopwatch.py:") and own.endswith(" c31_wait_in_library")


async def test_watcher_reports_a_loop_still_blocked_at_critical_ms(records):
    reported: list[tuple[LoopStall, int, float]] = []

    def on_critical(stall: LoopStall) -> None:
        reported.append((stall, threading.get_ident(), time.monotonic()))

    def block_the_loop() -> float:
        time.sleep(BLOCK_S)
        return time.monotonic()

    dog = LoopWatchdog(threshold_ms=100, critical_ms=250, on_critical=on_critical,
                       log=records.logger, name="c31-critical")
    unblocked_at = await _run_around(dog, block_the_loop)

    assert len(reported) == 1
    stall, thread_id, reported_at = reported[0]
    # Reported from the watcher thread while the loop thread was still inside block_the_loop.
    assert thread_id != threading.get_ident() and reported_at < unblocked_at
    assert stall.ongoing and stall.ms >= 250 and "block_the_loop" in stall.stack
    lines = [(r.levelno, " ongoing " in r.getMessage()) for r in records.tagged(STALL_TAG)]
    assert lines == [(logging.ERROR, True), (logging.ERROR, False)]
    assert dog.critical_count == 1 and dog.stall_count == 1


async def test_whole_process_pause_is_a_resume_not_a_stall(records):
    resumed: list[int] = []
    dog = LoopWatchdog(on_resume=resumed.append, log=records.logger, name="c31-pause")
    await _run_around(dog, lambda: _pause_whole_process(BLOCK_S))

    assert records.tagged(STALL_TAG) == [] and dog.stall_count == 0
    assert dog.resume_count == 1 and len(resumed) == 1 and resumed[0] >= 400
    (line,) = records.tagged(RESUME_TAG)
    assert line.levelno == logging.INFO and f"paused_ms={resumed[0]} " in line.getMessage()


async def test_busy_call_holding_the_gil_is_a_stall_not_a_pause(records, monkeypatch):
    # Process CPU time running as fast as the clock: whatever stopped both threads was busy.
    monkeypatch.setattr(jarvis_loopwatch, "_cpu_clock", time.monotonic)
    dog = LoopWatchdog(log=records.logger, name="c31-busy")
    await _run_around(dog, lambda: _pause_whole_process(BLOCK_S))

    assert records.tagged(RESUME_TAG) == [] and dog.resume_count == 0
    assert len(records.tagged(STALL_TAG)) == 1 and dog.stall_count == 1
    assert dog.last_stall.ms >= 400


async def test_wall_clock_jump_is_a_resume_not_a_stall(records, monkeypatch):
    offset = [0.0]
    monkeypatch.setattr(jarvis_loopwatch, "_wall_clock", lambda: time.time() + offset[0])
    resumed: list[int] = []
    dog = LoopWatchdog(on_resume=resumed.append, log=records.logger, name="c31-wall")

    def sleep_an_hour() -> None:  # where the monotonic clock skips sleep, only the wall clock jumps
        offset[0] = 3600.0

    await _run_around(dog, sleep_an_hour)
    assert records.tagged(STALL_TAG) == [] and dog.stall_count == 0
    assert dog.resume_count == 1 and len(resumed) == 1 and 3_599_000 <= resumed[0] <= 3_601_000


def test_a_stopped_loop_is_no_stall_and_closing_it_ends_the_watcher(records):
    """run_until_complete() returned with the heartbeat pending: work meanwhile delays no callback."""
    critical: list[LoopStall] = []
    dog = LoopWatchdog(threshold_ms=100, critical_ms=250, on_critical=critical.append,
                       log=records.logger, name="c31-stopped")
    loop = asyncio.new_event_loop()

    async def main() -> None:
        dog.start()
        await asyncio.sleep(0.25)

    loop.run_until_complete(main())
    time.sleep(BLOCK_S)  # synchronous work while the loop is stopped
    ticks = dog.ticks
    loop.run_until_complete(asyncio.sleep(SETTLE_S))  # running again; the stop was no stall
    assert dog.ticks > ticks and len(_watcher_threads("c31-stopped")) == 1
    _abandon(loop)  # closed with the heartbeat still pending
    _assert_watcher_ends("c31-stopped")
    assert not critical and records.tagged(STALL_TAG) == [] and dog.stall_count == 0
    assert _watcher_ended(records) == ["Loop watcher thread ended: its event loop was closed with "
                                       "the watchdog still running"]


def test_a_job_thread_that_ends_with_the_watchdog_running_ends_the_watcher(records):
    """
    A job end under LiveKit's THREAD executor, the Windows default (ipc/proc_client.py run() and
    job_proc_lazy_main.py _run_job_task()): the loop stops with the entrypoint, and so the
    heartbeat, still pending, and the job thread exits. With nothing stopping the watchdog, its
    watcher thread must end by itself, report no stall and keep nothing in memory.
    """
    critical: list[LoopStall] = []
    dog = LoopWatchdog(threshold_ms=100, critical_ms=250, on_critical=critical.append,
                       log=records.logger, name="c31-job")
    loop = asyncio.new_event_loop()
    entrypoints: list[asyncio.Task[None]] = []

    async def entrypoint() -> None:
        dog.start()
        await asyncio.Event().wait()  # agent_runner waits for a UI stop command

    async def run_job_task() -> None:
        entrypoints.append(asyncio.create_task(entrypoint()))
        await asyncio.sleep(0.25)  # the room disconnects; the entrypoint is not cancelled

    def job_thread() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_job_task())
        loop.run_until_complete(loop.shutdown_default_executor())

    job = threading.Thread(target=job_thread, name="c31-job-thread")
    job.start()
    job.join(timeout=5.0)
    assert not job.is_alive() and dog.running and dog.ticks >= 1
    _assert_watcher_ends("c31-job")
    assert not critical and records.tagged(STALL_TAG) == [] and dog.critical_count == 0
    assert _watcher_ended(records) == ["Loop watcher thread ended: its event loop thread ended "
                                       "with the watchdog still running"]
    # Only this test still refers to the ended job's loop; without it the loop can be freed.
    _abandon(loop)
    ended_loop = weakref.ref(loop)
    del loop, dog
    entrypoints.clear()
    gc.collect()
    assert ended_loop() is None


def test_import_starts_nothing_and_start_needs_a_running_loop():
    probe = (
        "import threading\n"
        "before = {thread.ident for thread in threading.enumerate()}\n"
        "import services.utils.jarvis_loopwatch as loopwatch\n"
        "print('NEW_THREADS', [thread.name for thread in threading.enumerate()"
        " if thread.ident not in before])\n"
        "try:\n"
        "    loopwatch.LoopWatchdog().start()\n"
        "except RuntimeError:\n"
        "    print('NO_RUNNING_LOOP')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                            capture_output=True, encoding="utf-8", errors="replace", timeout=120,
                            check=False)
    lines = [line for line in result.stdout.splitlines()
             if line.startswith(("NEW_THREADS", "NO_RUNNING_LOOP"))]
    assert lines == ["NEW_THREADS []", "NO_RUNNING_LOOP"], redact_secrets(result.stderr[-1500:])


@pytest.mark.parametrize("settings", [
    {"interval_s": 0}, {"threshold_ms": -1}, {"threshold_ms": float("nan")},
    {"threshold_ms": 300, "critical_ms": 300},
])
def test_invalid_settings_are_refused(settings):
    with pytest.raises(ValueError):
        LoopWatchdog(**settings)


async def test_start_and_stop_are_idempotent_and_end_the_watcher_thread(records):
    dog = LoopWatchdog(log=records.logger, name="c31-life")
    await dog.stop()  # never started: nothing to do
    task = dog.start()
    assert dog.start() is task
    await asyncio.sleep(0.25)
    assert dog.running and dog.ticks >= 1 and dog.stats()["watcher_thread"]
    assert len(_watcher_threads("c31-life")) == 1
    await dog.stop()
    await dog.stop()
    assert task.cancelled() and not dog.running
    _assert_watcher_ends("c31-life")


async def test_cancelling_the_callers_task_ends_run_and_the_watcher_thread(records):
    dog = LoopWatchdog(log=records.logger, name="c31-cancel")
    own = asyncio.create_task(dog.run())  # how agent_runner runs it: in a session task
    await asyncio.sleep(0.15)
    assert dog.running and len(_watcher_threads("c31-cancel")) == 1
    await asyncio.wait_for(dog.run(), timeout=1.0)  # a second run ends at once
    own.cancel()
    await asyncio.gather(own, return_exceptions=True)
    assert own.cancelled() and not dog.running
    _assert_watcher_ends("c31-cancel")
    events = [record.getMessage().split(" (")[0] for record in records.records]
    assert events == ["EVENT_LOOP_WATCHDOG started", "Loop watchdog 'c31-cancel' already runs;"
                      " this second run ends", "EVENT_LOOP_WATCHDOG stopped"]


async def test_failing_context_and_action_never_stop_the_watchdog(records):
    def broken_context() -> set[str]:
        raise RuntimeError("context down")

    def broken_action(_stall: LoopStall) -> None:
        raise RuntimeError("action down")

    dog = LoopWatchdog(context=broken_context, on_stall=broken_action, log=records.logger,
                       name="c31-faults")
    dog.start()
    await asyncio.sleep(0.25)
    time.sleep(BLOCK_S)
    await asyncio.sleep(SETTLE_S)
    ticks = dog.ticks
    await asyncio.sleep(0.25)
    assert dog.running and dog.ticks > ticks  # still beating after both failures
    await dog.stop()
    _assert_watcher_ends("c31-faults")
    (stall,) = records.tagged(STALL_TAG)
    assert " tools=? " in stall.getMessage()
    assert any(record.levelno == logging.ERROR and "broken_action" in record.getMessage()
               for record in records.records)


async def test_stall_reports_are_masked(records, monkeypatch):
    secret = "c31-fake-pinchtab-token-0123456789"
    monkeypatch.setenv("PINCHTAB_TOKEN", secret)  # K-29 masks the current value of this name
    dog = LoopWatchdog(context=lambda: {f"tool_{secret}"}, log=records.logger, name="c31-mask")
    await _run_around(dog, lambda: time.sleep(BLOCK_S))

    (stall,) = records.tagged(STALL_TAG)
    assert secret not in stall.getMessage() and " tools=tool_*** " in stall.getMessage()
    assert dog.last_stall.tools == ("tool_***",)
    # The module's own logger masks every line as well (K-29: a RedactingFormatter per handler).
    handlers = jarvis_loopwatch.logger.handlers
    assert handlers and all(isinstance(handler.formatter, RedactingFormatter)
                            for handler in handlers)


async def test_agent_runner_session_watchdog_runs_until_session_cleanup(monkeypatch):
    monkeypatch.setattr(agent_runner, "autonomous_protector", MagicMock(cancel_all=AsyncMock()))
    session_tasks: set[asyncio.Task[Any]] = set()
    job = _JobShutdown()

    dog = agent_runner._start_loop_watchdog(job, _tracker(session_tasks),
                                            plugin_manager.running_tools)
    await asyncio.sleep(0.15)
    assert dog.running and len(session_tasks) == 1 and len(_watcher_threads("agent")) == 1
    context = dog._context
    assert context.__self__ is plugin_manager and context.__name__ == "running_tools"
    await agent_runner._cleanup_session_resources(MagicMock(stop=AsyncMock()), list(session_tasks))
    assert not dog.running and not session_tasks
    _assert_watcher_ends("agent")
    await job.end_job()  # the job's shutdown afterwards finds it stopped already


async def test_agent_runner_session_watchdog_stops_when_the_job_ends():
    session_tasks: set[asyncio.Task[Any]] = set()
    job = _JobShutdown()

    dog = agent_runner._start_loop_watchdog(job, _tracker(session_tasks),
                                            plugin_manager.running_tools)
    await asyncio.sleep(0.15)
    assert dog.running and len(_watcher_threads("agent")) == 1
    await job.end_job()  # the room disconnected: no _cleanup_session_resources
    assert not dog.running and not session_tasks and dog.ticks >= 1
    _assert_watcher_ends("agent")
