"""Desktop Node tool executor (plan task C-120 step 5; K-11 v4, K-16, K-70, K-76).

- ToolLoop: a dedicated thread with its own event loop for the tools, so a tool never blocks the link loop.
  Its default executor is a CountingThreadPool: every piece of thread work (asyncio.to_thread,
  run_in_executor(None, ...)) is counted against the call whose task submitted it (a ContextVar), so the
  node knows when a call's thread work, for example a cancelled typing thread, has really ended.
- The desktop_input lock (K-16) is held until that thread work ends, not only until the tool's coroutine
  returns. After a cancel it waits at most DEGRADE_AFTER_S; thread work still running then makes the executor
  'degraded': every input-lock tool is refused (DESKTOP_DEGRADED) until that work ends.
- Executor.cancel answers through the call's outcome; the node sends 'cancelled' within CANCEL_ANSWER_S or
  the real result (K-70 cancel).
- SeqGate keeps the order of the laptop calls of one reply: seq {speech_id, n} (stamped by the agent's
  proxies) holds a later call up to SEQ_HOLD_S for the earlier ones.
- Tools are reached only through a ToolProvider (describe(name) -> ToolSpec): RegistryToolProvider loads
  the real laptop tools of the K-71 table from the plugin registry and runs them through their K-11 wrapper
  with _confirmed=True ('without asking again': the ConfirmFlow approval came from the agent, K-76);
  OperationRegistry holds the pc.* operations (pc.ping here, the rest is C-122). Tests use fakes only.

The executor never sees the link: the node turns an exec into an ExecCall, and a CallOutcome into the K-70
result body (result_body()).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import importlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from services.ai_core import tool_placement

logger = logging.getLogger("JARVIS-DESKTOP.executor")

INPUT_LOCK = "desktop_input"
DEGRADE_AFTER_S = 10.0     # K-70 executor: lock kept at most this long after a cancel, then 'degraded'
CANCEL_ANSWER_S = 5.0      # K-70: a cancel is answered 'cancelled' within 5 s, or with the real result
SEQ_HOLD_S = 0.5           # K-70/K-71 ordering: a later call of one reply waits at most this long
SEQ_IDLE_S = 120.0
LOCK_WAIT_MAX_S = 120.0
TIMEOUT_MAX_S = 3600.0
TOOL_THREADS = 8
_POLL_S = 0.02

ACTUATION_CLASSES = frozenset({tool_placement.INPUT, tool_placement.MESSAGING, tool_placement.CODE_EXEC,
                               tool_placement.AVAILABILITY, tool_placement.EXPOSURE})

# Call outcome statuses (the K-70 result body 'status') and effects.
COMPLETED, CANCELLED, TIMEOUT, FAILED, NOT_STARTED = "completed", "cancelled", "timeout", "failed", "not_started"
RESULT_STATUSES = frozenset({COMPLETED, CANCELLED, TIMEOUT, FAILED, NOT_STARTED})
EFFECT_NONE, EFFECT_DONE, EFFECT_POSSIBLE = "none", "done", "possible"

_MESSAGES = {
    "DESKTOP_BUSY": "Sir, laptop par abhi doosra kaam chal raha hai, thori der baad dobara koshish karein.",
    "DESKTOP_DEGRADED": "Sir, laptop par pichla kaam abhi ruka nahi, is liye typing aur clicks abhi band hain.",
    "STOPPED_BY_USER": "Sir, laptop ka yeh kaam rok diya gaya.",
    "DESKTOP_OFFLINE": "Sir, laptop ka rabta toot gaya tha, is liye yeh kaam rok diya gaya.",
    "TIMEOUT": "Sir, laptop par yeh kaam waqt par khatam nahi hua.",
    "TOOL_FAILED": "Sir, laptop par yeh kaam nahi ho saka.",
    "CANCELLED": "Sir, laptop ka yeh kaam cancel ho gaya.",
}

_current_call: contextvars.ContextVar[str | None] = contextvars.ContextVar("jarvis_desktop_call", default=None)


def current_call_id() -> str | None:
    """The call whose task (or thread work) is running in this context, if any."""
    return _current_call.get()


def k12_error(tool: str, code: str, message: str | None = None, *, status: str = "error") -> dict[str, Any]:
    """A K-12 ToolResult for a refusal or a failure the node produces itself."""
    return {"status": status, "tool": tool, "error_code": code,
            "message": message or _MESSAGES.get(code, _MESSAGES["TOOL_FAILED"])}


# --- counting thread pool and tool loop ----------------------------------------------------------------------

class CountingThreadPool(concurrent.futures.ThreadPoolExecutor):
    """A ThreadPoolExecutor that counts unfinished work per call (current_call_id() at submit time)."""

    def __init__(self, max_workers: int = TOOL_THREADS, thread_name_prefix: str = "desktop-tool") -> None:
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._counts: Counter[str | None] = Counter()
        self._count_lock = threading.Lock()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> concurrent.futures.Future[Any]:
        call_id = _current_call.get()
        with self._count_lock:
            self._counts[call_id] += 1
        try:
            future = super().submit(fn, *args, **kwargs)
        except BaseException:
            self._done(call_id)
            raise
        future.add_done_callback(lambda _f: self._done(call_id))
        return future

    def _done(self, call_id: str | None) -> None:
        with self._count_lock:
            self._counts[call_id] -= 1
            if self._counts[call_id] <= 0:
                del self._counts[call_id]

    def pending(self, call_id: str | None = None) -> int:
        """Unfinished thread work of one call (call_id=None: of work submitted outside any call)."""
        with self._count_lock:
            return self._counts.get(call_id, 0)

    def total(self) -> int:
        with self._count_lock:
            return sum(self._counts.values())


class ToolLoop:
    """The tool event loop thread; its default executor is a CountingThreadPool."""

    def __init__(self, *, max_workers: int = TOOL_THREADS, name: str = "desktop-tool-loop") -> None:
        self.pool = CountingThreadPool(max_workers=max_workers)
        self.loop = asyncio.new_event_loop()
        self.loop.set_default_executor(self.pool)
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> ToolLoop:
        if not self._thread.is_alive():
            self._thread.start()
            self._ready.wait(10.0)
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        try:
            self.loop.run_forever()
        finally:
            try:
                pending = [task for task in asyncio.all_tasks(self.loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.wait(pending, timeout=CANCEL_ANSWER_S))
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            finally:
                self.loop.close()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def call_soon(self, callback: Callable[..., Any], *args: Any) -> None:
        self.loop.call_soon_threadsafe(callback, *args)

    def submit(self, coro: Awaitable[Any]) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)  # type: ignore[arg-type]

    def stop(self, timeout: float = 10.0) -> None:
        if self._thread.is_alive():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout)
        self.pool.shutdown(wait=False, cancel_futures=True)


# --- tools ---------------------------------------------------------------------------------------------------

Invoke = Callable[[Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    """What the node knows about one laptop tool or pc.* operation.

    cls: the K-76 class floor (tool_placement); param_schema_sha256: of the node's LIVE function (K-71 drift
    check); exclusive: 'desktop_input' for the K-16 input lock; risk: the K-16 risk, a level or a callable on
    the arguments.
    """

    name: str
    cls: str
    param_schema_sha256: str
    invoke: Invoke = field(repr=False, compare=False)
    exclusive: str | None = None
    timeout_s: float = 15.0
    idempotent: bool = False
    risk: Any = "low"

    @property
    def needs_input_lock(self) -> bool:
        return self.exclusive == INPUT_LOCK

    @property
    def actuation(self) -> bool:
        return self.cls in ACTUATION_CLASSES

    def call_risk(self, args: Mapping[str, Any]) -> str:
        """The K-16 risk of a call: a fixed level, or the callable's answer; anything unusable is 'high'."""
        risk = self.risk
        if callable(risk):
            try:
                risk = risk(dict(args))
            except Exception:  # pylint: disable=broad-exception-caught  # K-11: a failing risk callable is high
                return "high"
        return risk if risk in ("low", "medium", "high") else "high"


class ToolProvider(Protocol):
    async def describe(self, name: str) -> ToolSpec | None:
        """The spec of a tool or pc.* operation this node can run, else None."""


_PC_OPS = {op.name: op for op in tool_placement.PC_OPERATIONS}


class OperationRegistry:
    """The pc.* operations this node implements (K-70 internal operations; C-122 adds all but pc.ping)."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, name: str, handler: Invoke, *, exclusive: str | None = None,
                 timeout_s: float | None = None) -> ToolSpec:
        op = _PC_OPS.get(name)
        if op is None:
            raise ValueError(f"{name} is not a K-70 pc.* operation (tool_placement.PC_OPERATIONS)")
        spec = ToolSpec(name=name, cls=op.cls, param_schema_sha256=op.manifest_entry()["param_schema_sha256"],
                        invoke=handler, exclusive=exclusive,
                        timeout_s=float(timeout_s if timeout_s is not None else op.timeout), idempotent=op.idempotent)
        self._specs[name] = spec
        return spec

    def names(self) -> list[str]:
        return sorted(self._specs)

    def snapshot(self) -> dict[str, ToolSpec]:
        return dict(self._specs)

    def restore(self, specs: Mapping[str, ToolSpec]) -> None:
        """Back to a snapshot (a module whose install(node) failed keeps none of its operations)."""
        self._specs = dict(specs)

    async def describe(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)


class CompositeProvider:
    """Asks each provider in turn (the pc.* operations first)."""

    def __init__(self, *providers: ToolProvider) -> None:
        self.providers = list(providers)

    async def describe(self, name: str) -> ToolSpec | None:
        for provider in self.providers:
            spec = await provider.describe(name)
            if spec is not None:
                return spec
        return None


class RegistryToolProvider:
    """The real laptop tools: imports the modules that the committed K-71 manifest names for the laptop-capable
    entries of tool_placement.PLACEMENTS, then serves their K-11 wrappers from the plugin registry. Loading runs
    once, in a worker thread, on first use. import_module and registry are injectable (tests use fakes)."""

    def __init__(self, *, import_module: Callable[[str], Any] = importlib.import_module,
                 registry: Callable[[], Any] | None = None,
                 manifest: Callable[[], Mapping[str, Any]] = tool_placement.load_manifest,
                 placements: Mapping[str, tool_placement.ToolPlacement] | None = None) -> None:
        self._import = import_module
        self._registry = registry or self._plugin_manager
        self._manifest = manifest
        self._placements = placements if placements is not None else tool_placement.PLACEMENTS
        self._specs: dict[str, ToolSpec] | None = None
        self._load_lock = threading.Lock()
        self.problems: list[str] = []

    def _plugin_manager(self) -> Any:
        return self._import("services.ai_core.jarvis_plugin_manager").plugin_manager

    def _laptop_modules(self) -> list[str]:
        modules: set[str] = set()
        for tool in self._manifest().get("tools") or ():
            entry = self._placements.get(tool.get("name", ""))
            if entry is not None and tool_placement.laptop_capable(entry) and tool.get("module"):
                modules.add(tool["module"])
        return sorted(modules)

    def load(self) -> dict[str, ToolSpec]:
        with self._load_lock:
            if self._specs is not None:
                return self._specs
            specs: dict[str, ToolSpec] = {}
            try:
                modules = self._laptop_modules()
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the node stays up, tools refused
                self.problems.append(f"manifest: {type(exc).__name__}")
                logger.error("Desktop tools not loaded: the K-71 manifest is unusable (%s)", type(exc).__name__)
                modules = []
            for module in modules:
                try:
                    self._import(module)
                except Exception as exc:  # pylint: disable=broad-exception-caught  # one bad module, not all
                    self.problems.append(f"{module}: {type(exc).__name__}")
                    logger.error("Desktop tool module %s failed to load (%s)", module, type(exc).__name__)
            tools = self._registry().get_tools() if modules else []
            for wrapper in tools:
                spec = self._spec(wrapper)
                if spec is not None:
                    specs[spec.name] = spec
            self._specs = specs
            logger.info("Desktop tools loaded: %d laptop tools from %d modules (%d problems)", len(specs),
                        len(modules), len(self.problems))
            return specs

    def _spec(self, wrapper: Any) -> ToolSpec | None:
        name = getattr(wrapper, "__name__", "")
        entry = self._placements.get(name)
        if entry is None or not tool_placement.laptop_capable(entry) or entry.cls is None:
            return None
        meta = dict(getattr(wrapper, "__jarvis_meta__", None) or {})
        try:
            schema = tool_placement.live_param_schema_sha256(wrapper)
        except Exception:  # pylint: disable=broad-exception-caught  # an unhashable signature never matches
            schema = ""
        timeout = meta.get("timeout")

        async def invoke(args: Mapping[str, Any], _wrapper: Any = wrapper) -> Any:
            # K-11 v4: run through the wrapper (lock, kill switch, timeout, audit) without asking again.
            return await _wrapper(**dict(args), _confirmed=True)

        return ToolSpec(name=name, cls=entry.cls, param_schema_sha256=schema, invoke=invoke,
                        exclusive=meta.get("exclusive"),
                        timeout_s=float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else 15.0,
                        idempotent=entry.idempotent, risk=meta.get("risk", "low"))

    async def describe(self, name: str) -> ToolSpec | None:
        specs = self._specs
        if specs is None:
            specs = await asyncio.to_thread(self.load)
        return specs.get(name)


# --- calls ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExecCall:
    """One accepted laptop call (built by the node from a verified exec)."""

    call_id: str
    tool: str
    args: Mapping[str, Any]
    spec: ToolSpec
    timeout_s: float
    lock_wait_s: float = 0.0
    cls: str = ""
    room: str = ""
    origin: str = "llm"
    peer: str = ""
    seq: Mapping[str, Any] | None = None

    @property
    def needs_input_lock(self) -> bool:
        return self.spec.needs_input_lock


@dataclass(frozen=True)
class CallOutcome:
    """How a call ended: status (COMPLETED, CANCELLED, TIMEOUT, FAILED, NOT_STARTED), the K-12 result, whether
    the tool started, the effect (none/done/possible), the error code and how long it took."""

    status: str
    result: Mapping[str, Any]
    started: bool
    effect: str
    code: str | None = None
    duration_ms: int = 0


class CallHandle:
    """A call in the executor; outcome is a concurrent Future set exactly once (from the tool loop)."""

    def __init__(self, call: ExecCall, clock: Callable[[], float]) -> None:
        self.call = call
        self.outcome: concurrent.futures.Future[CallOutcome] = concurrent.futures.Future()
        self.task: asyncio.Task[None] | None = None
        self.created = clock()
        self.run_started: float | None = None
        self.cancel_reason: str | None = None
        self.cancel_requested: float | None = None
        self.last_progress = self.created

    @property
    def call_id(self) -> str:
        return self.call.call_id

    @property
    def running(self) -> bool:
        return self.run_started is not None

    @property
    def done(self) -> bool:
        return self.outcome.done()

    @property
    def phase(self) -> str:
        if self.done:
            return "done"
        return "running" if self.running else ("waiting_lock" if self.call.needs_input_lock else "starting")

    def settle(self, outcome: CallOutcome) -> bool:
        """Sets the outcome once; False when it was set already."""
        try:
            self.outcome.set_result(outcome)
        except concurrent.futures.InvalidStateError:
            return False
        return True


def _cancel_code(reason: str | None) -> str:
    return reason if reason in _MESSAGES else "CANCELLED"


class Executor:
    """Runs ExecCalls on the tool loop (see the module docstring). on_running(handle) is called from the tool
    loop when a call's tool starts (the node writes the WAL 'running' state there)."""

    def __init__(self, tool_loop: ToolLoop, *, degrade_after_s: float = DEGRADE_AFTER_S,
                 clock: Callable[[], float] = time.monotonic,
                 on_running: Callable[[CallHandle], None] | None = None) -> None:
        self.tool_loop = tool_loop
        self._degrade_after = degrade_after_s
        self._clock = clock
        self._on_running = on_running
        self._calls: dict[str, CallHandle] = {}
        self._calls_lock = threading.Lock()
        self._input_lock: asyncio.Lock | None = None  # created on the tool loop
        self._stuck: set[str] = set()  # calls whose thread work outlived the cap (degraded)
        self._background: set[asyncio.Task[None]] = set()
        self.last_finished: dict[str, Any] = {}

    # --- state ---------------------------------------------------------------------------------------------

    @property
    def degraded(self) -> bool:
        return bool(self._stuck)

    def get(self, call_id: str) -> CallHandle | None:
        with self._calls_lock:
            return self._calls.get(call_id)

    def in_flight(self) -> list[CallHandle]:
        with self._calls_lock:
            return [handle for handle in self._calls.values() if not handle.done]

    def stats(self) -> dict[str, Any]:
        handles = self.in_flight()
        return {"in_flight": len(handles), "running": sum(1 for h in handles if h.running),
                "threads": self.tool_loop.pool.total(), "degraded": self.degraded,
                "busy_tools": sorted({h.call.tool for h in handles if h.running})}

    # --- start and cancel ------------------------------------------------------------------------------------

    def start(self, call: ExecCall) -> CallHandle:
        """Starts a call on the tool loop; an in-flight call_id returns its existing handle (idempotent)."""
        with self._calls_lock:
            existing = self._calls.get(call.call_id)
            if existing is not None and not existing.done:
                return existing
            handle = CallHandle(call, self._clock)
            self._calls[call.call_id] = handle

        def create() -> None:
            task = self.tool_loop.loop.create_task(self._run(handle), name=f"desktop-call:{call.tool}")
            task.add_done_callback(lambda _task: self._settle_unstarted(handle))
            handle.task = task

        self.tool_loop.call_soon(create)
        return handle

    def _settle_unstarted(self, handle: CallHandle) -> None:
        """Done callback of a call's task. A task cancelled before its first step never ran _run, not even its
        finally, so its outcome is set here: it never started."""
        if not handle.done:
            code = _cancel_code(handle.cancel_reason)
            handle.settle(self._outcome(handle, NOT_STARTED, k12_error(handle.call.tool, code, status="cancelled"),
                                        EFFECT_NONE, code))
            self.last_finished = {"tool": handle.call.tool, "status": NOT_STARTED, "at": time.time()}

    def cancel(self, call_id: str, reason: str = "CANCELLED") -> CallHandle | None:
        """Cancels an in-flight call once (the first reason stands); its outcome follows (the node answers
        within CANCEL_ANSWER_S)."""
        handle = self.get(call_id)
        if handle is None or handle.done or handle.cancel_reason is not None:
            return handle
        handle.cancel_reason, handle.cancel_requested = reason, self._clock()

        def cancel_task() -> None:
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()

        self.tool_loop.call_soon(cancel_task)
        return handle

    def cancel_where(self, predicate: Callable[[CallHandle], bool], reason: str) -> list[CallHandle]:
        handles = [handle for handle in self.in_flight() if predicate(handle)]
        for handle in handles:
            self.cancel(handle.call_id, reason)
        return handles

    def forget(self, call_id: str) -> None:
        """Drops a finished call from the in-memory table (the WAL keeps its state)."""
        with self._calls_lock:
            handle = self._calls.get(call_id)
            if handle is not None and handle.done:
                del self._calls[call_id]

    @staticmethod
    async def wait(handle: CallHandle, timeout: float | None = None) -> CallOutcome | None:
        """The outcome, or None when it is not there within timeout (awaitable from any loop). Shielded: a
        timeout here must never cancel the outcome future itself."""
        try:
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(handle.outcome)), timeout)
        except TimeoutError:
            return None

    # --- the call's task (tool loop) ---------------------------------------------------------------------------

    def _lock(self) -> asyncio.Lock:
        if self._input_lock is None:
            self._input_lock = asyncio.Lock()
        return self._input_lock

    async def _run(self, handle: CallHandle) -> None:
        token = _current_call.set(handle.call_id)
        call = handle.call
        held = False
        outcome: CallOutcome | None = None
        try:
            if call.needs_input_lock:
                held, outcome = await self._take_input_lock(handle)
                if outcome is not None:
                    return
            if self._on_running is not None:
                try:
                    self._on_running(handle)  # the write-ahead 'running' record comes before the tool acts
                except Exception:  # pylint: disable=broad-exception-caught  # no record, no run
                    logger.exception("The 'running' record of %s failed; the tool does not run", call.tool)
                    outcome = self._not_started(handle, "DESKTOP_DEGRADED")
                    return
            handle.run_started = self._clock()
            result = await asyncio.wait_for(call.spec.invoke(call.args), timeout=call.timeout_s)
            outcome = self._outcome(handle, COMPLETED, self._k12(call.tool, result), EFFECT_DONE)
        except asyncio.CancelledError:
            code = _cancel_code(handle.cancel_reason)
            outcome = self._outcome(handle, CANCELLED if handle.running else NOT_STARTED,
                                    k12_error(call.tool, code, status="cancelled"),
                                    EFFECT_POSSIBLE if handle.running else EFFECT_NONE, code)
        except TimeoutError:
            outcome = self._outcome(handle, TIMEOUT, k12_error(call.tool, "TIMEOUT"), EFFECT_POSSIBLE, "TIMEOUT")
        except Exception as exc:  # pylint: disable=broad-exception-caught  # a tool bug is a failed call
            logger.error("Desktop tool %s raised %s", call.tool, type(exc).__name__)
            outcome = self._outcome(handle, FAILED, k12_error(call.tool, "TOOL_FAILED"), EFFECT_POSSIBLE,
                                    "TOOL_FAILED")
        finally:
            if outcome is None:  # cannot happen; keeps the promise that every call gets an outcome
                outcome = self._outcome(handle, FAILED, k12_error(call.tool, "TOOL_FAILED"), EFFECT_POSSIBLE,
                                        "TOOL_FAILED")
            if held:
                self._release_later(handle)
            handle.settle(outcome)
            self.last_finished = {"tool": call.tool, "status": outcome.status, "at": time.time()}
            _current_call.reset(token)

    async def _take_input_lock(self, handle: CallHandle) -> tuple[bool, CallOutcome | None]:
        call = handle.call
        if self.degraded:
            return False, self._not_started(handle, "DESKTOP_DEGRADED")
        lock = self._lock()
        try:
            # Not wait_for: wait_for(..., 0) cancels the acquire before it runs, so lock_wait_s 0 ('do not wait')
            # would refuse even a free lock. asyncio.timeout takes a free lock at once and refuses a held one.
            async with asyncio.timeout(max(0.0, call.lock_wait_s)):
                await lock.acquire()
        except TimeoutError:
            return False, self._not_started(handle, "DESKTOP_BUSY")
        if self.degraded:  # a cancelled call left thread work behind while this one waited
            self._release_later(handle)
            return False, self._not_started(handle, "DESKTOP_DEGRADED")
        return True, None

    def _not_started(self, handle: CallHandle, code: str) -> CallOutcome:
        return self._outcome(handle, NOT_STARTED, k12_error(handle.call.tool, code), EFFECT_NONE, code)

    def _outcome(self, handle: CallHandle, status: str, result: Mapping[str, Any], effect: str,
                 code: str | None = None) -> CallOutcome:
        started = handle.run_started if handle.run_started is not None else handle.created
        return CallOutcome(status=status, result=result, started=handle.running, effect=effect, code=code,
                           duration_ms=max(0, int((self._clock() - started) * 1000)))

    @staticmethod
    def _k12(tool: str, result: Any) -> Mapping[str, Any]:
        """A tool's return value as a K-12 dict (legacy tools return text)."""
        if isinstance(result, Mapping):
            return result
        if result is None:
            return {"status": "success", "tool": tool, "message": ""}
        return {"status": "success", "tool": tool, "message": str(result)}

    def _release_later(self, handle: CallHandle) -> None:
        task = asyncio.get_running_loop().create_task(self._release_after_threads(handle))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _release_after_threads(self, handle: CallHandle) -> None:
        """Keeps the input lock until the call's thread work has ended; at most DEGRADE_AFTER_S after the
        cancel (or the end of its coroutine), then the executor is degraded until that work ends."""
        pool, call_id = self.tool_loop.pool, handle.call_id
        try:
            deadline = (handle.cancel_requested or self._clock()) + self._degrade_after
            while pool.pending(call_id) and self._clock() < deadline:
                await asyncio.sleep(_POLL_S)
            if pool.pending(call_id):
                self._stuck.add(call_id)
                logger.warning("DESKTOP_DEGRADED: %s left thread work running %.0f s after its end; input tools "
                               "are refused until it stops", handle.call.tool, self._degrade_after)
                watcher = asyncio.get_running_loop().create_task(self._clear_when_idle(call_id))
                self._background.add(watcher)
                watcher.add_done_callback(self._background.discard)
        finally:
            self._lock().release()

    async def _clear_when_idle(self, call_id: str) -> None:
        while self.tool_loop.pool.pending(call_id):
            await asyncio.sleep(0.1)
        self._stuck.discard(call_id)
        logger.info("Desktop executor recovered: the stuck thread work has ended")


# --- ordering ------------------------------------------------------------------------------------------------

@dataclass
class _Speech:
    floor: int = 0                       # every n below floor has passed
    passed: set[int] = field(default_factory=set)
    waiters: dict[int, asyncio.Event] = field(default_factory=dict)
    touched: float = 0.0


class SeqGate:
    """Per-reply order of laptop calls (seq {speech_id, n}); used on the link loop before a call is accepted.

    wait_turn(seq) returns at once when every lower n of that speech has passed, else after at most hold_s (or
    the start deadline): a call the agent never sent (refused at the agent, placed on the VPS) holds the next
    one only once. A late lower n then runs at once. Returns True when the call was in order. A call passes
    only with done(seq), which the node calls once the call is accepted (started on the executor) or refused,
    so a later call can never overtake an earlier one that is still being accepted."""

    def __init__(self, *, hold_s: float = SEQ_HOLD_S, idle_s: float = SEQ_IDLE_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._hold, self._idle, self._clock = hold_s, idle_s, clock
        self._speeches: dict[str, _Speech] = {}

    @staticmethod
    def _key(seq: Mapping[str, Any] | None) -> tuple[str, int] | None:
        if not isinstance(seq, Mapping):
            return None
        speech, n = seq.get("speech_id"), seq.get("n")
        if not isinstance(speech, str) or not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return None
        return speech, n

    def _speech(self, speech_id: str) -> _Speech:
        now = self._clock()
        for key in [key for key, item in self._speeches.items() if now - item.touched > self._idle and
                    not item.waiters]:
            del self._speeches[key]
        item = self._speeches.setdefault(speech_id, _Speech())
        item.touched = now
        return item

    async def wait_turn(self, seq: Mapping[str, Any] | None, *, deadline: float | None = None) -> bool:
        key = self._key(seq)
        if key is None:
            return True
        speech_id, n = key
        item = self._speech(speech_id)
        if n <= item.floor:
            return True
        hold = self._hold if deadline is None else max(0.0, min(self._hold, deadline - self._clock()))
        event = item.waiters.setdefault(n, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=hold)
            return True
        except TimeoutError:
            item.floor = max(item.floor, n)  # give up on the gap below n, once
            self._advance(item)
            return False
        finally:
            if item.waiters.get(n) is event:
                del item.waiters[n]

    def done(self, seq: Mapping[str, Any] | None) -> None:
        """The call has passed: accepted, refused or a duplicate (idempotent)."""
        key = self._key(seq)
        if key is not None:
            item = self._speech(key[0])
            item.passed.add(key[1])
            self._advance(item)

    @staticmethod
    def _advance(item: _Speech) -> None:
        while item.floor in item.passed:
            item.floor += 1
        item.passed = {value for value in item.passed if value >= item.floor}
        for waiting_n, event in list(item.waiters.items()):
            if waiting_n <= item.floor:
                event.set()


# --- result bodies -------------------------------------------------------------------------------------------

def result_body(outcome: CallOutcome, *, blobs: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The K-70 result body (without call_id, which the node adds): status, K-12 result, duration_ms, effect,
    blobs."""
    return {"status": outcome.status, "result": dict(outcome.result), "duration_ms": int(outcome.duration_ms),
            "effect": outcome.effect, "blobs": [dict(blob) for blob in blobs]}
