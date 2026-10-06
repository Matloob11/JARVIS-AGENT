"""
# src/core/jarvis_runtime.py
JarvisRuntime — the per-job session owner (C-33; design .claude/wip/jarvisruntime/design.md §1.2).

One JarvisRuntime per job. It owns exactly ONE live AgentSession at a time and fixes the C-33
defects of the old `while attempt < 5` entrypoint: ctx.connect() once per job, session.start retried
(not the whole job), handlers registered once, session errors rebuilt IN PLACE under a restart cap,
and a bounded, idempotent teardown that always ends in ctx.shutdown(reason).

The design keeps the class in src/core/agent_runner.py; STEP 1 (new-modules-only) builds it here so
no existing file changes — the wiring step imports it from agent_runner (or moves it, owner's call,
design Q6) and passes the real collaborators. Everything is duck-typed/injected so this module is
importable and unit-testable WITHOUT the livekit runtime and has no import-time side effects.

Seams the wiring step fills (see design §1.2 for what each maps to in agent_runner today):
- session_factory() -> session        build RealtimeModel + BrainAssistant-agent + AgentSession
                                      (fresh per start attempt and per error rebuild)
- start_session(session)              awaits session.start(room, agent, room_options=...);
                                      default: session.start()
- on_session_started(session, rebuilt) register the session handlers (user_input_transcribed,
                                      conversation_item_added, agent_state_changed) and re-attach
                                      router/assistant/voice_link; called after EVERY successful
                                      start, with rebuilt=True on an error rebuild
- protector                           per-job TaskProtector (run_protected/cancel_all) — NEVER the
                                      global autonomous_protector (cross-job interference, design §4.6)
- listener                            the job's UIBridgeListener (or its JobListenerHandle): aclose()
- assistant                           BrainAssistant: _stop_event/end_reason (C-23 bridge), shutdown()
- voice_link                          VoiceLink (C-35): close() at teardown; exposed for voice_take_over
- set_session_attr(value)             jarvis.session attribute writes (K-68 v9), best effort
- end_job(reason)                     the C-23 end block (jarvis.session paused/closed, notify
                                      job_end, ctx.shutdown); default: ctx.shutdown(reason=reason)

Lifecycle:
- start(): connect once (60 s cap) -> session via factory, start retried <= 3 with 2/4 s between and
  session.aclose() after every failed attempt -> runtime handlers registered ONCE (room
  'disconnected' -> closed; session 'close'/'error' -> the restart path) -> stop-event bridge
  (watches the assistant's Event, never swaps it — a pause_agent set before the runtime waits is not
  lost, C-23) -> RUNTIME_STARTED. All attempts failed -> reason 'session_failed'. A ctx.connect
  failure (or the 60 s cap) -> logged, reason 'connect_failed', start() returns False — never a
  bare exception that would leave reason None for the entrypoint's final aclose.
- session close reason ERROR (or an error event with recoverable=False): rebuild a fresh session in
  place, <= 3 restarts per rolling 10 min with 2/4/8 s backoff, 'RUNTIME_SESSION_RESTART n=%d';
  beyond the cap -> closed('session_error'). The worker process never dies for a session error.
- wait_closed(): await the closed event; returns the reason.
- aclose(reason) — exact bounded order (idempotent, tolerates partial start):
  protector.cancel_all -> listener.aclose (5 s) -> assistant.shutdown (10 s) ->
  session.aclose (10 s) -> room.off for every tracked handler (+ voice_link.close) ->
  'RUNTIME_CLOSED reason=%s' -> end_job / ctx.shutdown(reason).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("JARVIS-RUNTIME")

# Timing (seconds). Injectable in __init__ so unit tests run instantly.
CONNECT_TIMEOUT_S = 60.0
SESSION_START_RETRIES = 3
BACKOFF_S = (2.0, 4.0, 8.0)
RESTART_CAP = 3
RESTART_WINDOW_S = 600.0
SESSION_CLOSE_TIMEOUT_S = 10.0
LISTENER_CLOSE_TIMEOUT_S = 5.0
ASSISTANT_SHUTDOWN_TIMEOUT_S = 10.0

# Job end reasons (values mirror src/core/agent_runner.py — not imported: that module needs livekit).
END_CONNECT_FAILED = "connect_failed"   # ctx.connect raised or hit the 60 s cap (new reason;
                                        # flagged in NOTES.md for the K-60 reason-wording bump)
END_SESSION_FAILED = "session_failed"   # every session.start attempt of this job failed
END_SESSION_ERROR = "session_error"     # session errors beyond the restart cap
END_ROOM_DISCONNECTED = "room_disconnected"
END_BY_COMMAND = "ended_by_command"     # the stop event without a reason (an assistant before C-23)

# jarvis.session attribute values (K-68 v9); written through the set_session_attr hook.
SESSION_STARTING = "starting"
SESSION_RUNNING = "running"
SESSION_RESTARTING = "restarting"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _is_error_close_reason(reason: Any) -> bool:
    """True for livekit-agents' CloseReason.ERROR, duck-typed: enum name, enum repr or plain string."""
    token = getattr(reason, "name", None)
    if not isinstance(token, str):
        token = getattr(reason, "value", None) if not isinstance(reason, str) else reason
        token = token if isinstance(token, str) else str(reason)
    return token.strip().upper().rsplit(".", 1)[-1] == "ERROR"


class JarvisRuntime:
    """One per job: owns the session, the per-job protector, the listener and the teardown order."""

    def __init__(
        self,
        ctx: Any,
        *,
        session_factory: Callable[[], Any],
        start_session: Callable[[Any], Any] | None = None,
        on_session_started: Callable[..., Any] | None = None,
        protector: Any = None,
        listener: Any = None,
        assistant: Any = None,
        router: Any = None,
        voice_link: Any = None,
        set_session_attr: Callable[[str], Any] | None = None,
        end_job: Callable[[str], Any] | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        session_close_timeout_s: float = SESSION_CLOSE_TIMEOUT_S,
        listener_close_timeout_s: float = LISTENER_CLOSE_TIMEOUT_S,
        assistant_shutdown_timeout_s: float = ASSISTANT_SHUTDOWN_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        # No I/O in __init__ (design §1.2).
        self.ctx = ctx
        self.session: Any = None
        self.assistant = assistant
        self.router = router
        self.listener = listener
        self.voice_link = voice_link
        self.protector = protector
        self.closed = asyncio.Event()
        self.reason: str | None = None
        self._session_factory = session_factory
        self._start_session_hook = start_session
        self._on_session_started = on_session_started
        self._set_session_attr_hook = set_session_attr
        self._end_job_hook = end_job
        self._connect_timeout_s = connect_timeout_s
        self._session_close_timeout_s = session_close_timeout_s
        self._listener_close_timeout_s = listener_close_timeout_s
        self._assistant_shutdown_timeout_s = assistant_shutdown_timeout_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._restarts: deque[float] = deque()  # session restart timestamps in the rolling window
        self._room_handlers: list[tuple[str, Any]] = []  # for ctx.room.off in aclose
        self._session_handlers: list[tuple[str, Any]] = []  # re-registered per session object
        self._restart_task: asyncio.Task[Any] | None = None
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._started = False
        self._closing = False
        self._aclosed = False

    # ---- lifecycle ----------------------------------------------------------------------------

    async def start(self) -> bool:
        """Connect once, start the session (retried), register handlers once, bridge the stop
        event. Returns False when every session.start attempt failed (reason 'session_failed')."""
        if self._started:
            raise RuntimeError("JarvisRuntime.start() called twice (one runtime per job)")
        self._started = True
        connect = getattr(self.ctx, "connect", None)
        if callable(connect):
            try:
                await asyncio.wait_for(_maybe_await(connect()), self._connect_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # A connect failure (including the 60 s cap) must not escape with reason None:
                # the entrypoint's `finally: aclose(rt.reason)` would then end the job with the
                # misleading END_BY_COMMAND default. Truthful reason + return False instead.
                logger.error("ctx.connect failed: %s", exc)
                self._close_with(END_CONNECT_FAILED)
                return False
        self._set_session_attr(SESSION_STARTING)
        if not await self._start_session_with_retries():
            self._close_with(END_SESSION_FAILED)
            return False
        self._register_room_handlers()
        self._watch_stop_event()
        self._set_session_attr(SESSION_RUNNING)
        logger.info("RUNTIME_STARTED job=%s room=%s", self._job_id(), self._room_name())
        return True

    async def wait_closed(self) -> str | None:
        await self.closed.wait()
        return self.reason

    # ---- session start / restart --------------------------------------------------------------

    async def _run_session_start(self, session: Any) -> None:
        if self._start_session_hook is not None:
            await _maybe_await(self._start_session_hook(session))
            return
        start = getattr(session, "start", None)
        if not callable(start):
            raise RuntimeError("session has no start()")
        await _maybe_await(start())

    async def _start_session_with_retries(self) -> bool:
        """session.start retried up to SESSION_START_RETRIES times (2/4 s between attempts), with
        session.aclose() after every failed attempt and a FRESH session per attempt (C-33: never
        reuse a half-started session)."""
        for attempt in range(1, SESSION_START_RETRIES + 1):
            session = await _maybe_await(self._session_factory())
            try:
                await self._run_session_start(session)
            except asyncio.CancelledError:
                await self._safe_session_aclose(session)
                raise
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("session.start failed (attempt %d/%d): %s",
                               attempt, SESSION_START_RETRIES, exc)
                await self._safe_session_aclose(session)
                if attempt < SESSION_START_RETRIES:
                    await self._sleep(BACKOFF_S[attempt - 1])
                continue
            self.session = session
            await self._hook_session(session, rebuilt=False)
            return True
        return False

    async def _hook_session(self, session: Any, *, rebuilt: bool) -> None:
        """Registers the runtime's own session handlers on THIS session object and hands it to the
        wiring callback (router/assistant/voice_link re-attach). Room handlers are NOT touched on a
        rebuild — that is what kills the double-listener bug (design §4.2)."""
        self._session_handlers = []
        on = getattr(session, "on", None)
        if callable(on):
            on("close", self._on_session_close)
            on("error", self._on_session_error)
            self._session_handlers = [("close", self._on_session_close),
                                      ("error", self._on_session_error)]
        if self._on_session_started is not None:
            await _maybe_await(self._on_session_started(session, rebuilt))

    def _unhook_session(self, session: Any) -> None:
        off = getattr(session, "off", None)
        if not callable(off):
            return
        for event, handler in self._session_handlers:
            try:
                off(event, handler)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("session.off(%s) failed: %s", event, exc)

    def _on_session_close(self, event: Any = None) -> None:
        if self._closing or self.closed.is_set():
            return
        if _is_error_close_reason(getattr(event, "reason", None)):
            self._schedule_restart()

    def _on_session_error(self, event: Any = None) -> None:
        if self._closing or self.closed.is_set():
            return
        error = getattr(event, "error", None)
        if getattr(error, "recoverable", None) is False:  # C-114 forward-compat path
            self._schedule_restart()

    def _schedule_restart(self) -> None:
        task = self._restart_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            return  # one restart at a time
        self._restart_task = asyncio.get_running_loop().create_task(self._restart_session())

    async def _restart_session(self) -> None:
        """The in-place rebuild for a session error: cap RESTART_CAP per rolling RESTART_WINDOW_S,
        backoff 2/4/8 s, fresh RealtimeModel+AgentSession via the factory. The worker process never
        dies for a session error; beyond the cap the JOB ends ('session_error') and the bridge
        decides about a fresh one (design §4.1)."""
        if self._closing or self.closed.is_set():
            return
        now = self._monotonic()
        while self._restarts and now - self._restarts[0] > RESTART_WINDOW_S:
            self._restarts.popleft()
        if len(self._restarts) >= RESTART_CAP:
            logger.warning("session error beyond the restart cap (%d per %.0f s): ending the job",
                           RESTART_CAP, RESTART_WINDOW_S)
            self._close_with(END_SESSION_ERROR)
            return
        self._restarts.append(now)
        n = len(self._restarts)
        logger.warning("RUNTIME_SESSION_RESTART n=%d", n)
        self._set_session_attr(SESSION_RESTARTING)
        await self._sleep(BACKOFF_S[min(n, len(BACKOFF_S)) - 1])
        if self._closing or self.closed.is_set():
            return
        old = self.session
        if old is not None:
            self._unhook_session(old)
            await self._safe_session_aclose(old)
        session: Any = None
        try:
            session = await _maybe_await(self._session_factory())
            await self._run_session_start(session)
        except asyncio.CancelledError:
            if session is not None:
                await self._safe_session_aclose(session)
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("session rebuild failed: %s", exc)
            if session is not None:
                # A rebuilt session that failed mid-start can hold a Gemini websocket and audio
                # resources — aclose it, exactly like _start_session_with_retries does after
                # every failed attempt (C-33 rule: never leak a half-started session).
                await self._safe_session_aclose(session)
            self._schedule_restart()  # counts against the same cap on the next pass
            return
        self.session = session
        await self._hook_session(session, rebuilt=True)
        self._set_session_attr(SESSION_RUNNING)

    # ---- room handlers / stop event -----------------------------------------------------------

    def room_on(self, event: str, handler: Any) -> None:
        """Registers a room handler and tracks it for room.off at teardown (wiring uses this for
        data_received/track_published etc. so nothing outlives the job)."""
        room = getattr(self.ctx, "room", None)
        on = getattr(room, "on", None) if room is not None else None
        if not callable(on):
            return
        on(event, handler)
        self._room_handlers.append((event, handler))

    def _register_room_handlers(self) -> None:
        self.room_on("disconnected", self._on_room_disconnected)

    def _on_room_disconnected(self, *_args: Any) -> None:
        self._close_with(END_ROOM_DISCONNECTED)

    def _watch_stop_event(self) -> None:
        """C-23 bridge (compat until VR-15): the assistant's _stop_event stays THE Event the UI path
        sets; the runtime WATCHES it (never swaps it), so a pause_agent that arrived before the
        runtime waits is not lost (design §4.7)."""
        stop_event = getattr(self.assistant, "_stop_event", None)
        if not isinstance(stop_event, asyncio.Event):
            return

        async def _stop_event_bridge() -> None:
            await stop_event.wait()
            reason = getattr(self.assistant, "end_reason", None) or END_BY_COMMAND
            self._close_with(reason)

        task = asyncio.get_running_loop().create_task(_stop_event_bridge())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ---- teardown -----------------------------------------------------------------------------

    def _close_with(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        self.closed.set()

    async def aclose(self, reason: str | None = None) -> None:
        """Bounded teardown in the design's exact order; idempotent and tolerant of a partial
        start. ctx.shutdown (or the end_job hook, which owns the C-23 end block) always runs."""
        if self._aclosed:
            return
        self._aclosed = True
        self._closing = True
        final = reason or self.reason or END_BY_COMMAND
        if self.reason is None:
            self.reason = final
        try:
            # 0. stop our own background tasks (restart task + stop-event bridge)
            for task in [self._restart_task, *self._bg_tasks]:
                if task is not None and not task.done():
                    task.cancel()
            pending = [t for t in [self._restart_task, *self._bg_tasks] if t is not None]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            # 1. per-job protector: cannot touch another job's loops (design §4.6)
            await self._teardown_step("protector.cancel_all",
                                      getattr(self.protector, "cancel_all", None))
            # 2. the job's bridge listener (C-34): the old AsyncClient must stop reconnecting
            await self._teardown_step("listener.aclose", getattr(self.listener, "aclose", None),
                                      timeout=self._listener_close_timeout_s)
            # 3. assistant shutdown (task monitor + vision loop + notifier)
            await self._teardown_step("assistant.shutdown", getattr(self.assistant, "shutdown", None),
                                      timeout=self._assistant_shutdown_timeout_s)
            # 4. the session itself (never the nonexistent .stop())
            if self.session is not None:
                self._unhook_session(self.session)
                await self._teardown_step("session.aclose", getattr(self.session, "aclose", None),
                                          timeout=self._session_close_timeout_s)
            # 5. room handlers off (ours + the voice link's)
            voice_close = getattr(self.voice_link, "close", None)
            if callable(voice_close):
                try:
                    voice_close()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.debug("voice_link.close failed: %s", exc)
            room = getattr(self.ctx, "room", None)
            off = getattr(room, "off", None) if room is not None else None
            for event, handler in self._room_handlers:
                if callable(off):
                    try:
                        off(event, handler)
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        logger.debug("room.off(%s) failed: %s", event, exc)
            self._room_handlers = []
        finally:
            # 6. the end block always runs: RUNTIME_CLOSED + end_job / ctx.shutdown
            self._close_with(final)
            logger.info("RUNTIME_CLOSED reason=%s", final)
            try:
                if self._end_job_hook is not None:
                    await _maybe_await(self._end_job_hook(final))
                else:
                    shutdown = getattr(self.ctx, "shutdown", None)
                    if callable(shutdown):
                        await _maybe_await(shutdown(reason=final))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("job end failed: %s", exc, exc_info=True)

    async def _teardown_step(self, name: str, method: Any, timeout: float | None = None) -> None:
        """One bounded, never-raising teardown call (design §4.10: a wedged session cannot hold
        aclose hostage)."""
        if not callable(method):
            return
        try:
            result = method()
            if inspect.isawaitable(result):
                if timeout is None:
                    await result
                else:
                    await asyncio.wait_for(result, timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("teardown step %s failed: %s", name, exc)

    async def _safe_session_aclose(self, session: Any) -> None:
        await self._teardown_step("session.aclose", getattr(session, "aclose", None),
                                  timeout=self._session_close_timeout_s)

    # ---- small helpers ------------------------------------------------------------------------

    def _set_session_attr(self, value: str) -> None:
        if self._set_session_attr_hook is None:
            return
        try:
            self._set_session_attr_hook(value)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("set_session_attr(%s) failed: %s", value, exc)

    def _job_id(self) -> str:
        job = getattr(self.ctx, "job", None)
        job_id = getattr(job, "id", None) if job is not None else None
        return job_id if isinstance(job_id, str) else "unknown"

    def _room_name(self) -> str:
        room = getattr(self.ctx, "room", None)
        name = getattr(room, "name", None) if room is not None else None
        return name if isinstance(name, str) else "unknown"
