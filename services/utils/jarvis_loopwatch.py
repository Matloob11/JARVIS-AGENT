"""
# services/utils/jarvis_loopwatch.py
Event-loop stall watchdog (plan task C-31).

LoopWatchdog.run() sleeps interval_s in a loop and measures how late it wakes up. When the loop
could not run callbacks for longer than threshold_ms (a blocking call inside a tool; Gemini audio
freezes meanwhile) it logs WARNING 'EVENT_LOOP_STALL ms=<n> tools=<running tools> stack=<frames>'.
A tool that awaits is never a stall, however long it takes. A daemon watcher thread sees a stall
while it lasts: it records the running tools and the loop thread's innermost frames for that line,
and reports a loop still blocked after critical_ms (ERROR, plus on_critical, for example a
supervisor restart). A pause of the whole process (laptop sleep or Modern Standby, a debugger) is
logged as EVENT_LOOP_RESUME and never counted as a stall, and neither is a loop that was stopped
(run_until_complete() returned) with the heartbeat pending. The watcher thread ends with the
heartbeat, or once its loop is closed or the loop's thread has ended (a LiveKit job thread ends
that way). Importing this module starts nothing.

C-53: sample_loop_lag(until) measures the loop's drift every 100 ms while until() is False (the vector memory's
start-up and its first write) and logs 'LOOP_LAG max_ms=<n> during=<label> samples=<n>' once, at WARNING from
LOOP_LAG_WARN_MS on. Like the watchdog it never counts a pause of the whole process as lag (its watcher thread beside
the sampler); such a pause is noted at the end of the line as 'paused_ms=<n>'.
"""

import asyncio
import collections
import logging
import math
import os
import sys
import sysconfig
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

logger = setup_logger("LOOP-WATCH")

STALL_TAG = "EVENT_LOOP_STALL"
RESUME_TAG = "EVENT_LOOP_RESUME"
DEFAULT_INTERVAL_S = 0.1
DEFAULT_THRESHOLD_MS = 250.0
# Well before LiveKit kills a job process whose loop has not answered its pings for 60 s.
DEFAULT_CRITICAL_MS = 10_000.0
STACK_FRAMES = 10

# A span in which the watcher thread could not run either and the process used less CPU than this
# share of it was a pause of the whole process (sleep, standby, a debugger). A stall leaves the
# watcher running (time.sleep, I/O) or burns CPU (a C call that holds the GIL).
_PAUSED_CPU_SHARE = 0.25
# Where the monotonic clock skips system sleep (Linux, macOS) the wall clock runs ahead of it after
# a resume. Where it counts sleep (Windows) the loop sees one long gap instead, which the watcher
# thread explains as a pause.
_WALL_JUMP_S = 2.0
_PAUSE_SPANS = 32
_NAME_MAX = 80
# Frames from these folders are library code (stdlib, site-packages); the others are the program's.
_LIBRARY_DIRS = tuple({os.path.join(os.path.normcase(os.path.abspath(path)), "")
                       for key, path in sysconfig.get_paths().items()
                       if key in ("stdlib", "platstdlib", "purelib", "platlib")})

# Read through the module so tests can simulate a resume or a busy CPU.
_wall_clock: Callable[[], float] = time.time
_cpu_clock: Callable[[], float] = time.process_time


def _is_library(path: str) -> bool:
    return path.startswith("<") or os.path.normcase(path).startswith(_LIBRARY_DIRS)


def _frame_label(frame: FrameType) -> str:
    """'folder/file.py:12 func', or 'file.py:12 func' at the top of a library ('socket.py')."""
    code = frame.f_code
    folder, name = os.path.split(code.co_filename)
    if folder and os.path.join(os.path.normcase(folder), "") not in _LIBRARY_DIRS:
        name = f"{os.path.basename(folder)}/{name}"
    return f"{name}:{frame.f_lineno} {code.co_name}"


@dataclass(frozen=True)
class LoopStall:
    """One stall: how long the loop ran no callbacks, the tools running, where the loop was."""

    ms: int
    tools: tuple[str, ...] = ()
    stack: str = ""
    ongoing: bool = False  # reported from the watcher thread while the loop is still blocked
    ts: float = field(default_factory=time.time)

    def describe(self) -> str:
        """The report after its tag: 'ms=<n> tools=<a,b or -> stack=<innermost <- outer>'."""
        text = f"ms={self.ms} tools={','.join(self.tools) or '-'}"
        return f"{text} stack={self.stack}" if self.stack else text


class _Watcher:
    """
    The daemon thread beside the loop; it keeps running while the loop is blocked. It records the
    tools and the loop thread's stack of a stall while it lasts, reports a loop still blocked at
    critical_s, and keeps the spans in which it could not run either while the process used
    almost no CPU (pauses of the whole process) for the heartbeat. It ends when halted, or by
    itself once its loop is closed or the loop's thread has ended with the heartbeat pending.
    """

    def __init__(self, *, loop: asyncio.AbstractEventLoop, loop_thread: threading.Thread,
                 interval_s: float, threshold_s: float, critical_s: float | None,
                 tools: Callable[[], tuple[str, ...]], on_critical: Callable[[LoopStall], None],
                 log: logging.Logger, name: str) -> None:
        self.loop = loop
        self.loop_thread = loop_thread
        self.interval_s = interval_s
        self.threshold_s = threshold_s
        self.critical_s = critical_s
        self.tools = tools
        self.on_critical = on_critical
        self.log = log
        self.halt = threading.Event()
        self.lock = threading.Lock()
        # (heartbeat number, its monotonic start); the heartbeat begins right after this thread.
        self.tick: tuple[int, float] = (0, time.monotonic())
        self.seen: float | None = None  # when this thread last ran; None while it checks
        self.pauses: collections.deque[tuple[float, float]] = collections.deque(
            maxlen=_PAUSE_SPANS)
        self.capture: tuple[int, tuple[str, ...], str] | None = None
        self.critical_tick = -1
        # The heartbeat during which the loop was last seen stopped; it measures no stall.
        self.idle_tick = -1
        self.thread = threading.Thread(target=self.run, name=f"jarvis-loopwatch-{name}",
                                       daemon=True)

    def run(self) -> None:
        try:
            self._watch()
        except Exception:  # pylint: disable=broad-exception-caught
            self.log.exception("Loop watcher thread stopped; stalls are still measured")
        finally:
            with self.lock:
                self.seen = None

    def _watch(self) -> None:
        last, cpu = time.monotonic(), _cpu_clock()
        self.seen = last
        failed = False
        while not self.halt.wait(self.interval_s):
            if self.loop.is_closed() or not self.loop_thread.is_alive():
                # Nothing left to watch, and the loop must not be kept in memory: LiveKit ends a
                # job thread this way, its entrypoint (and so the heartbeat) left pending.
                self.log.info("Loop watcher thread ended: its event loop %s with the watchdog "
                              "still running", "was closed" if self.loop.is_closed()
                              else "thread ended")
                return
            now, cpu_now = time.monotonic(), _cpu_clock()
            span = now - last
            # Woken this late, the thread could not run: the process was paused, or the loop
            # thread held the GIL and has moved on since, so its stack would mislead.
            late = span - self.interval_s >= self.threshold_s
            with self.lock:
                if late and cpu_now - cpu < _PAUSED_CPU_SHARE * span:
                    self.pauses.append((last + self.interval_s, now))
                self.seen = None
            if not self.loop.is_running():
                # Stopped with the heartbeat pending (run_until_complete() returned): no callback
                # is kept waiting, so that is no stall, not now and not when the loop runs again.
                self.idle_tick = self.tick[0]
            elif not late:
                try:
                    self._observe(now)
                except Exception:  # pylint: disable=broad-exception-caught
                    if not failed:  # a bug here must neither end the thread nor flood the log
                        failed = True
                        self.log.exception("Loop watcher check failed")
            last, cpu = time.monotonic(), _cpu_clock()
            self.seen = last

    def _observe(self, now: float) -> None:
        """Records a stall in progress and reports it once when it reaches critical_s."""
        number, started = self.tick
        if number == self.idle_tick:
            return
        stuck = now - started - self.interval_s
        if stuck >= self.threshold_s / 2:  # early enough for a stall just over the threshold
            stuck -= self.paused_within(started, now)
        if stuck < self.threshold_s / 2:
            return
        capture = self.capture
        if capture is None or capture[0] != number:
            self.capture = (number, self.tools(), self.loop_stack())
        if (self.critical_s is not None and stuck >= self.critical_s
                and self.critical_tick != number):
            self.critical_tick = number
            self.on_critical(LoopStall(ms=round(stuck * 1000), tools=self.tools(),
                                       stack=self.loop_stack(), ongoing=True))

    def paused_within(self, start: float, end: float, cpu_s: float | None = None) -> float:
        """
        Seconds of [start, end] in which the whole process was paused. Given cpu_s (the process
        CPU seconds over the span) the heartbeat also counts the time since this thread was due to
        run again, unless the process was busy meanwhile (a GIL-holding call is a stall).
        """
        with self.lock:
            spans = list(self.pauses)
            seen = self.seen
        paused = sum(max(0.0, min(stop, end) - max(begin, start)) for begin, stop in spans)
        if cpu_s is not None and seen is not None:
            due = seen + self.interval_s
            if end - due >= self.threshold_s and cpu_s < _PAUSED_CPU_SHARE * (end - start):
                paused += end - max(due, start)
        return min(paused, max(0.0, end - start))

    def loop_stack(self) -> str:
        """
        The loop thread's innermost frames, innermost first: 'utils/file.py:12 func <- ...'. When
        they are all library code (a deep blocking call such as urlopen), the innermost frame of
        the program's own code follows, after '...' for the frames left out: the call to fix.
        """
        current = sys._current_frames()  # pylint: disable=protected-access
        frame = current.get(self.loop_thread.ident)
        frames: list[str] = []
        own = False
        while frame is not None and len(frames) < STACK_FRAMES:
            frames.append(_frame_label(frame))
            own = own or not _is_library(frame.f_code.co_filename)
            frame = frame.f_back
        if not own:
            skipped = 0
            while frame is not None and _is_library(frame.f_code.co_filename):
                frame, skipped = frame.f_back, skipped + 1
            if frame is not None:
                if skipped:
                    frames.append("...")
                frames.append(_frame_label(frame))
        return redact_secrets(" <- ".join(frames))


class LoopWatchdog:
    """
    Event-loop stall watchdog (see the module docstring), generic so that other JARVIS processes
    can reuse it. interval_s: time between heartbeats; threshold_ms: a heartbeat later than this
    is a stall; critical_ms: a stall still in progress after this is reported at once (None:
    never); context(): names of what is running, here the running tools; watch_thread=False:
    heartbeat only (no stacks, no critical report, no pause detection); log: the logger.
    Every report is logged (K-29 masked) and then passed to its action: on_stall(LoopStall) in
    the loop thread after a stall, on_critical(LoopStall) in the watcher thread while the loop is
    still blocked (once per stall), on_resume(paused_ms) in the loop thread after a pause of the
    whole process. Actions and context() must be quick, thread-safe plain functions; their errors
    are logged and ignored. start(), or awaiting run() in a task of the caller's, must happen on
    the running loop; stop(), or cancelling that task, ends the heartbeat and the watcher thread.
    A loop closed, or whose thread ended, with the heartbeat still pending ends the watcher thread.
    """

    def __init__(self, *, interval_s: float = DEFAULT_INTERVAL_S,
                 threshold_ms: float = DEFAULT_THRESHOLD_MS,
                 critical_ms: float | None = DEFAULT_CRITICAL_MS,
                 context: Callable[[], Iterable[str]] | None = None,
                 on_stall: Callable[[LoopStall], object] | None = None,
                 on_critical: Callable[[LoopStall], object] | None = None,
                 on_resume: Callable[[int], object] | None = None,
                 watch_thread: bool = True,
                 log: logging.Logger | None = None,
                 name: str = "loop") -> None:
        if not 0 < interval_s < math.inf or not 0 < threshold_ms < math.inf:
            raise ValueError("interval_s and threshold_ms must be positive and finite")
        if critical_ms is not None and critical_ms <= threshold_ms:
            raise ValueError("critical_ms must be greater than threshold_ms")
        self.interval_s = float(interval_s)
        self.threshold_ms = float(threshold_ms)
        self.critical_ms = None if critical_ms is None else float(critical_ms)
        self.name = name
        self._context = context
        self._on_stall = on_stall
        self._on_critical = on_critical
        self._on_resume = on_resume
        self._watch_thread = watch_thread
        self._log = log or logger
        self._task: asyncio.Task[None] | None = None
        self._watcher: _Watcher | None = None
        self.ticks = 0
        self.stall_count = 0
        self.critical_count = 0
        self.resume_count = 0
        self.max_stall_ms = 0
        self.last_stall: LoopStall | None = None

    @property
    def running(self) -> bool:
        """True while the heartbeat runs."""
        task = self._task
        return task is not None and not task.done()

    def stats(self) -> dict[str, Any]:
        """Counters for health reports and tests."""
        watcher = self._watcher
        return {"running": self.running,
                "watcher_thread": watcher is not None and watcher.thread.is_alive(),
                "ticks": self.ticks, "stalls": self.stall_count,
                "critical": self.critical_count, "resumes": self.resume_count,
                "max_stall_ms": self.max_stall_ms}

    def start(self) -> asyncio.Task[None]:
        """Starts the watchdog on the running loop; while it runs, the same task is returned."""
        task = self._task
        if task is None or task.done():
            task = asyncio.get_running_loop().create_task(self.run(),
                                                          name=f"loop_watchdog:{self.name}")
            self._task = task
        return task

    async def stop(self) -> None:
        """Ends the heartbeat and the watcher thread; also when never started or already stopped."""
        task, self._task = self._task, None
        watcher = self._watcher
        if watcher is not None:
            watcher.halt.set()
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.wait({task})

    async def run(self) -> None:
        """The heartbeat; runs until cancelled. start() wraps it in a task."""
        task = asyncio.current_task()
        current = self._task
        if current is not None and current is not task and not current.done():
            self._log.warning("Loop watchdog '%s' already runs; this second run ends", self.name)
            return
        self._task = task
        watcher = self._start_watcher()
        critical = "off" if self.critical_ms is None else f"{self.critical_ms:.0f} ms"
        self._log.info("EVENT_LOOP_WATCHDOG started (heartbeat %.0f ms, stall > %.0f ms, "
                       "critical %s, watcher thread %s)", self.interval_s * 1000,
                       self.threshold_ms, critical, "on" if watcher is not None else "off")
        try:
            await self._beat(watcher)
        finally:
            if watcher is not None:
                watcher.halt.set()
            if self._watcher is watcher:
                self._watcher = None
            if self._task is task:
                self._task = None
            self._log.info("EVENT_LOOP_WATCHDOG stopped (ticks=%d stalls=%d critical=%d "
                           "resumes=%d max_stall_ms=%d)", self.ticks, self.stall_count,
                           self.critical_count, self.resume_count, self.max_stall_ms)

    def _start_watcher(self) -> _Watcher | None:
        if not self._watch_thread:
            return None
        critical_s = None if self.critical_ms is None else self.critical_ms / 1000
        watcher = _Watcher(loop=asyncio.get_running_loop(), loop_thread=threading.current_thread(),
                           interval_s=self.interval_s, threshold_s=self.threshold_ms / 1000,
                           critical_s=critical_s, tools=self._tools, on_critical=self._critical,
                           log=self._log, name=self.name)
        try:
            watcher.thread.start()
        except RuntimeError as e:
            self._log.warning("Loop watcher thread not started (%s); stalls are measured "
                              "without stacks", e)
            return None
        self._watcher = watcher
        return watcher

    async def _beat(self, watcher: _Watcher | None) -> None:
        number = 0
        failed = False
        while True:
            start, wall, cpu = time.monotonic(), _wall_clock(), _cpu_clock()
            if watcher is not None:
                watcher.tick = (number, start)
            await asyncio.sleep(self.interval_s)
            end = time.monotonic()
            self.ticks += 1
            try:
                self._measure(watcher, number, start=start, end=end, wall_s=_wall_clock() - wall,
                              cpu_s=_cpu_clock() - cpu)
            except Exception:  # pylint: disable=broad-exception-caught
                if not failed:  # a bug here must neither end the heartbeat nor flood the log
                    failed = True
                    self._log.exception("Loop watchdog check failed")
            number += 1

    def _measure(self, watcher: _Watcher | None, number: int, *, start: float, end: float,
                 wall_s: float, cpu_s: float) -> None:
        span = end - start
        lag = span - self.interval_s
        paused = wall_s - span if wall_s - span >= _WALL_JUMP_S else 0.0
        if lag * 1000 > self.threshold_ms and watcher is not None:
            frozen = watcher.paused_within(start, end, cpu_s)
            lag -= frozen
            paused = max(paused, frozen)
        if paused * 1000 >= self.threshold_ms:
            self._resumed(round(paused * 1000))
        idle = watcher is not None and watcher.idle_tick == number  # the loop was stopped meanwhile
        if lag * 1000 > self.threshold_ms and not idle:
            capture = watcher.capture if watcher is not None else None
            if capture is not None and capture[0] == number:
                tools, stack = capture[1], capture[2]
            else:  # too short for the watcher to see, or no watcher thread
                tools, stack = self._tools(), ""
            self._stalled(LoopStall(ms=round(lag * 1000), tools=tools, stack=stack))

    def _stalled(self, stall: LoopStall) -> None:
        self.stall_count += 1
        self.max_stall_ms = max(self.max_stall_ms, stall.ms)
        self.last_stall = stall
        critical = self.critical_ms is not None and stall.ms >= self.critical_ms
        self._log.log(logging.ERROR if critical else logging.WARNING, "%s %s", STALL_TAG,
                      stall.describe())
        self._act(self._on_stall, stall)

    def _critical(self, stall: LoopStall) -> None:
        """Called from the watcher thread while the loop is still blocked."""
        self.critical_count += 1
        self._log.error("%s ongoing %s", STALL_TAG, stall.describe())
        self._act(self._on_critical, stall)

    def _resumed(self, paused_ms: int) -> None:
        self.resume_count += 1
        self._log.info("%s paused_ms=%d (the whole process was paused, e.g. sleep or standby; "
                       "not a stall)", RESUME_TAG, paused_ms)
        self._act(self._on_resume, paused_ms)

    def _tools(self) -> tuple[str, ...]:
        if self._context is None:
            return ()
        try:
            # Masked before the cut, so no part of a secret survives it.
            names = {redact_secrets(str(name))[:_NAME_MAX] for name in self._context()}
        except Exception:  # pylint: disable=broad-exception-caught
            return ("?",)
        return tuple(sorted(names))

    def _act(self, action: Callable[[Any], object] | None, value: Any) -> None:
        if action is None:
            return
        try:
            action(value)
        except Exception:  # pylint: disable=broad-exception-caught
            self._log.exception("Loop watchdog action %s failed",
                                getattr(action, "__name__", type(action).__name__))


LAG_TAG = "LOOP_LAG"
LOOP_LAG_INTERVAL_S = 0.1
LOOP_LAG_MAX_S = 300.0  # the sampler stops after this long whatever until() says
LOOP_LAG_WARN_MS = 200


class _PauseWatcher(_Watcher):
    """
    C-53: the watchdog's watcher thread beside sample_loop_lag, for its pause detection only: it keeps the spans in
    which it could not run either while the process used almost no CPU (a pause of the whole process), which
    paused_within() reads. It captures no stall and reports nothing critical.
    """

    def _observe(self, now: float) -> None:
        return None


def _start_pause_watcher(loop: asyncio.AbstractEventLoop, *, interval_s: float, threshold_s: float,
                         log: logging.Logger, label: str) -> _PauseWatcher | None:
    """The started _PauseWatcher of a sample_loop_lag run, None when no thread can start (then a pause of the whole
    process is taken off only where the wall clock shows it)."""
    watcher = _PauseWatcher(loop=loop, loop_thread=threading.current_thread(), interval_s=interval_s,
                            threshold_s=threshold_s, critical_s=None, tools=lambda: (),
                            on_critical=lambda _stall: None, log=log, name=f"lag-{label}")
    try:
        watcher.thread.start()
    except RuntimeError as e:
        log.debug("%s pause watcher not started (%s)", LAG_TAG, e)
        return None
    return watcher


async def sample_loop_lag(until: Callable[[], bool], *, label: str, interval_s: float = LOOP_LAG_INTERVAL_S,
                          max_s: float = LOOP_LAG_MAX_S, log: logging.Logger | None = None) -> int:
    """
    C-53: sleeps interval_s in a loop on the running event loop while until() is False (at most max_s) and measures
    how late each wake-up is: the loop could not run callbacks meanwhile. A pause of the whole process (laptop sleep or
    Modern Standby, a debugger) is never counted as lag. On Windows time.monotonic counts a system sleep
    (GetTickCount64 in Python 3.12), so the loop wakes late by the sleep's length: as in LoopWatchdog, a watcher thread
    runs beside the sampler, and the time in which it could not run either while the process used almost no CPU is
    taken off the sample it fell into (a GIL-holding busy call or a blocking wait stays lag). Where the monotonic clock
    skips a sleep, the wall clock running ahead of it is only noted. Logs once, when it stops (also when it is
    cancelled at the job's end): 'LOOP_LAG max_ms=<worst drift> during=<label> samples=<n>', followed by
    ' paused_ms=<n> (...)' when such a pause fell into the sampling, at INFO, or at WARNING from LOOP_LAG_WARN_MS on;
    nothing when until() held from the start. A failing until() stops the sampling. Returns the worst drift in ms.
    """
    out = log or logger

    def finished() -> bool:
        try:
            return bool(until())
        except Exception:  # pylint: disable=broad-exception-caught
            return True

    if finished():
        return 0  # nothing to watch: no sample, no watcher thread, no line
    threshold_s = LOOP_LAG_WARN_MS / 1000
    watcher = _start_pause_watcher(asyncio.get_running_loop(), interval_s=interval_s, threshold_s=threshold_s,
                                   log=out, label=label)
    worst = 0.0
    paused_total = 0.0
    samples = 0
    started = time.monotonic()  # the clock of the watcher's spans (and of the default event loops)
    try:
        while not finished() and time.monotonic() - started < max_s:
            start, wall, cpu = time.monotonic(), _wall_clock(), _cpu_clock()
            await asyncio.sleep(interval_s)
            end = time.monotonic()
            lag = end - start - interval_s
            ahead = (_wall_clock() - wall) - (end - start)
            paused = ahead if ahead >= _WALL_JUMP_S else 0.0  # skipped by the monotonic clock: none of it is lag
            if lag >= threshold_s and watcher is not None:
                frozen = watcher.paused_within(start, end, _cpu_clock() - cpu)
                lag -= frozen
                paused = max(paused, frozen)
            worst = max(worst, lag)
            paused_total += paused
            samples += 1
    finally:
        if watcher is not None:
            watcher.halt.set()
        if samples:
            worst_ms = int(round(worst * 1000))
            paused_ms = int(round(paused_total * 1000))
            level = logging.WARNING if worst_ms >= LOOP_LAG_WARN_MS else logging.INFO
            if paused_ms:
                out.log(level, "%s max_ms=%d during=%s samples=%d paused_ms=%d (the whole process was paused, e.g. "
                        "sleep or standby; not counted as lag)", LAG_TAG, worst_ms, label, samples, paused_ms)
            else:
                out.log(level, "%s max_ms=%d during=%s samples=%d", LAG_TAG, worst_ms, label, samples)
    return int(round(worst * 1000))
