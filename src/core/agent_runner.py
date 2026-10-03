"""
# agent_runner.py
Entrypoint and session management for the JARVIS agent.
"""

# pylint: disable=broad-exception-caught, wrong-import-position, wrong-import-order, ungrouped-imports
# ruff: noqa: E402

import asyncio
import functools
import logging
import os
import traceback
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

# FORCE global logging to show status while debugging
logging.basicConfig(level=logging.INFO, force=True)
# K-29: httpx logs every request URL (query keys included) at INFO.
for logger_name in ["httpx", "httpcore"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)
for logger_name in ["livekit", "livekit.agents", "livekit.rtc", "root"]:
    log_obj = logging.getLogger(logger_name)
    log_obj.setLevel(logging.ERROR)
    log_obj.propagate = False

# Single worker (K-53): a direct start takes the same lock as src.core.agent, before heavy imports.
if __name__ == "__main__":
    from src.core.agent_lock import acquire_or_exit
    acquire_or_exit()

from src.core import agent_lock, livekit_link

# C-136: the environment this worker started with, taken before jarvis_logger loads the .env files. An
# automatic restart hands it to agent_ctl, so the fresh worker reads those files again.
_START_ENV = dict(os.environ)

# K-29: every root handler masks secrets. Kept below the lock: jarvis_logger loads .env.
from services.utils.jarvis_logger import redact_root_handlers

redact_root_handlers()

# K-72 compat layer (C-113), remote mode only: rebinds os.startfile, webbrowser.open and the ipinfo location
# before this module or discovery binds them by value. A no-op on the PC brain.
from services.ai_core import remote_compat

remote_compat.install()

# C-124: VPS agent/svc processes only (Linux, remote mode): child env allow-list, PR_SET_DUMPABLE=0 and,
# with JARVIS_NET_GUARD=1, the egress guard. Runs in the worker and again in every spawned job process
# (it imports this module). The PC worker (Windows, local mode) gets nothing.
from services.utils import net_guard

net_guard.harden_process()

from livekit import agents, rtc
from livekit.agents import AgentSession, cli, llm
from livekit.agents.voice import room_io as lk_room_io
from livekit.agents.cli import cli as livekit_cli_module
from livekit.plugins import google, silero

# Suppress LiveKit internal session noise
for logger_name in ["livekit", "livekit.agents", "livekit.rtc"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)


def _mask_cli_log_handler() -> None:
    """
    K-29: LiveKit's CLI adds its own root handler when the worker starts (JSON on stdout in
    'start' mode, the Rich console in 'dev'). Its _configure_logger is wrapped so that handler
    masks secrets from then on, not only from the first job. A LiveKit release without the hook
    still starts; entrypoint() then masks the handler.
    """
    configure_logger = getattr(livekit_cli_module, "_configure_logger", None)
    if not callable(configure_logger) or getattr(configure_logger, "masks_secrets", False):
        return

    @functools.wraps(configure_logger)
    def configure_and_mask(*args: Any, **kwargs: Any) -> None:
        configure_logger(*args, **kwargs)
        redact_root_handlers()

    configure_and_mask.masks_secrets = True  # type: ignore[attr-defined]
    livekit_cli_module._configure_logger = configure_and_mask  # pylint: disable=protected-access


_mask_cli_log_handler()

# [Safeguard] Patch for LiveKit internal KeyError on flaky reconnections (Sir Matloob's PC)
# The SDK 1.1.2 has a race condition where local_track_published is emitted before local state sync.
_original_on_room_event = rtc.Room._on_room_event
def _safe_on_room_event(self, event):
    try:
        return _original_on_room_event(self, event)
    except KeyError as e:
        if "TR_" in str(e):
             logging.warning("🛡️ Safeguard: Blocked internal LiveKit KeyError for track %s", e)
             return None
        raise
rtc.Room._on_room_event = _safe_on_room_event

from services.ai_core.agent_loops import (
    start_adaptation_loop,
    start_bug_hunter_loop,
    start_memory_storage_loop,
    start_reminder_loop,
    start_ui_command_listener,
)
from services.ai_core.agent_memory import MemoryExtractor
from services.info.jarvis_search import (
    get_current_city,
    get_current_city_data,
    get_formatted_datetime,
)
from services.utils.jarvis_adaptive import in_container
from services.utils.jarvis_autonomous import autonomous_protector
from services.utils.jarvis_diagnostics import diagnostics as diagnostics_instance
from services.utils.jarvis_healing import healing_engine
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_config import (
    BRAIN_VPS,
    DESKTOP_MODE_LOCAL,
    DESKTOP_MODE_REMOTE,
    ROLE_AGENT,
    RuntimeProfile,
    config,
    desktop_mode,
    runtime_profile,
)
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_loopwatch import LoopWatchdog
from services.utils.jarvis_security import redact_secrets
from dotenv import dotenv_values

# The clipboard helper needs a desktop. In remote mode (K-72) the Desktop Node runs it, so this process
# never imports it. The mode is fixed for the life of the process.
if desktop_mode() == DESKTOP_MODE_LOCAL:
    from services.automation.jarvis_clipboard import ClipboardMonitor
else:
    ClipboardMonitor = None  # type: ignore[assignment,misc]

# Notification functions moved inside entrypoint to handle potential scope/import issues

# Import BrainAssistant from agent_core
try:
    from .agent_core import BrainAssistant
except (ImportError, ValueError):
    from agent_core import BrainAssistant  # type: ignore

from src.core.vision_handler import append_chat_message, desktop_data_message

logger = setup_logger("AGENT-RUNNER")

# Notification functions moved to services.utils.jarvis_bridge

DEFAULT_ROOM_NAME = "jarvis-hybrid"
CLIPBOARD_TIP_LABEL = "CLIPBOARD ERROR DETECTED: the clipboard helper's tip for an error the user copied"

# ------------------------------------------------------------------ C-124: room input owner-only
# LiveKit 1.4.6 serves lk.agent.send_message (session.run for any caller), lk.agent.get_chat_history,
# get_session_state and get_agent_info over RPC, the same four over the lk.agent.request text stream,
# and typed text on lk.chat. On the VPS brain only the linked 'owner-*' participant (the identity the
# bridge mints, K-38 v4) may use them. The PC worker keeps today's behaviour unless
# JARVIS_RPC_OWNER_ONLY=1: the live UI's identity there is not yet proven to be 'owner-*'.
RPC_OWNER_ONLY_ENV = "JARVIS_RPC_OWNER_ONLY"
OWNER_IDENTITY_PREFIX = "owner-"
GUARDED_RPC_METHODS = ("lk.agent.send_message", "lk.agent.get_chat_history", "lk.agent.get_session_state",
                       "lk.agent.get_agent_info")
GUARDED_TEXT_TOPICS = ("lk.agent.request",)
TEXT_INPUT_TOPIC = "lk.chat"
RPC_REFUSED_CODE = 403
_OWNER_ONLY_FLAG = "__jarvis_owner_only__"


def rpc_owner_only(mode: str | None = None) -> bool:
    """True when room input is owner-only: always in remote mode (the VPS brain); in local mode only with
    JARVIS_RPC_OWNER_ONLY=1 (default off on the PC)."""
    if (mode or desktop_mode()) == DESKTOP_MODE_REMOTE:
        return True
    return (os.getenv(RPC_OWNER_ONLY_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def _identity_kind(identity: Any) -> str:
    """The prefix of an identity for a log line ('owner', 'web', ...); never the identity itself."""
    text = identity if isinstance(identity, str) else ""
    return f"{text.split('-', 1)[0]}-*" if "-" in text else "unprefixed"


def _linked_identity(session: Any) -> str | None:
    room_io = getattr(session, "_room_io", None)
    linked = getattr(room_io, "linked_participant", None) if room_io is not None else None
    identity = getattr(linked, "identity", None)
    return identity if isinstance(identity, str) else None


def owner_may_use(identity: Any, session: Any) -> bool:
    """True for an 'owner-*' identity that is the linked participant (or any 'owner-*' while nobody is
    linked)."""
    if not isinstance(identity, str) or not identity.startswith(OWNER_IDENTITY_PREFIX):
        return False
    linked = _linked_identity(session)
    return linked is None or linked == identity


def _refuse_log(channel: str, identity: Any) -> None:
    logger.warning("ROOM_INPUT refused on %s from a %s participant (owner-only)", channel,
                   _identity_kind(identity))


def _owner_only_rpc(method: str, handler: Callable[..., Any], session: Any) -> Callable[..., Any]:
    async def guarded(data: rtc.RpcInvocationData) -> Any:
        if not owner_may_use(getattr(data, "caller_identity", None), session):
            _refuse_log(method, getattr(data, "caller_identity", None))
            raise rtc.RpcError(RPC_REFUSED_CODE, "owner only")
        result = handler(data)
        return await result if asyncio.iscoroutine(result) else result

    setattr(guarded, _OWNER_ONLY_FLAG, True)
    return guarded


def _owner_only_stream(topic: str, handler: Callable[..., Any], session: Any) -> Callable[..., Any]:
    def guarded(reader: Any, participant_identity: str) -> Any:
        if not owner_may_use(participant_identity, session):
            _refuse_log(topic, participant_identity)
            return None
        return handler(reader, participant_identity)

    setattr(guarded, _OWNER_ONLY_FLAG, True)
    return guarded


def _owner_only_text_input(callback: Callable[..., Any], session: Any) -> Callable[..., Any]:
    async def guarded(sess: Any, event: Any) -> None:
        identity = getattr(getattr(event, "participant", None), "identity", None)
        if not owner_may_use(identity, session):
            _refuse_log(TEXT_INPUT_TOPIC, identity)
            return
        result = callback(sess, event)
        if asyncio.iscoroutine(result):
            await result

    setattr(guarded, _OWNER_ONLY_FLAG, True)
    return guarded


_LINKABLE_KINDS = (rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, rtc.ParticipantKind.PARTICIPANT_KIND_SIP)


def _may_link(participant: Any) -> bool:
    """A participant the voice session may listen to: a person (standard or SIP), never an agent."""
    return (getattr(participant, "kind", None) in _LINKABLE_KINDS
            and isinstance(getattr(participant, "identity", None), str))


def relink_room_input(session: Any, room: Any) -> str | None:
    """
    Points the session's room input at the owner again after a reconnect under a new identity. The bridge
    mints a new 'owner-<8 hex>' identity for every LiveKit token, so a UI reconnect comes back as another
    participant; livekit-agents' RoomIO keeps waiting for the identity it linked first and ignores the new one,
    so the agent stopped hearing the owner (2026-10-03, after the C-116 bridge went live). When the linked
    participant has left, the newest person still in the room is linked. Returns that identity, else None.
    """
    room_io = getattr(session, "_room_io", None)
    if room_io is None or not callable(getattr(room_io, "set_participant", None)):
        return None
    remote = dict(getattr(room, "remote_participants", None) or {})
    linked = _linked_identity(session)
    if linked is not None and linked in remote:
        return None
    people = [participant for participant in remote.values() if _may_link(participant)]
    if not people:
        return None
    identity = people[-1].identity  # remote_participants keeps join order: the newest one is last
    room_io.set_participant(identity)
    logger.info("ROOM INPUT linked to %s after a reconnect", _identity_kind(identity))
    return identity


def session_room_options() -> Any:
    """
    The room options of the voice session. close_on_disconnect is off: by default livekit-agents closes the
    whole AgentSession when the linked participant leaves on purpose (a page refresh), and since the bridge mints a
    new identity for every token the owner always comes back as another participant, so the agent stayed in the
    room but never listened again (2026-10-03). With it off the session stays up and follow_owner_reconnects()
    links the owner's new identity.
    """
    return lk_room_io.RoomOptions(close_on_disconnect=False)


def follow_owner_reconnects(session: Any, room: Any) -> None:
    """After session.start: re-links the room input when the owner reconnects under a new identity, or when the
    linked participant leaves while another person is still in the room (a second tab or device)."""

    def _on_connected(participant: Any) -> None:
        if _may_link(participant):
            relink_room_input(session, room)

    def _on_disconnected(_participant: Any) -> None:
        relink_room_input(session, room)

    room.on("participant_connected", _on_connected)
    room.on("participant_disconnected", _on_disconnected)
    relink_room_input(session, room)  # the linked person may have left before these handlers existed


def harden_room_input(session: Any, room: Any, mode: str | None = None) -> list[str]:
    """
    C-124 step 3, after session.start: when rpc_owner_only(mode), wraps the lk.agent.* RPCs, the
    lk.agent.request stream and the text-input callback so only the linked owner-* participant gets an
    answer; an RPC that cannot be wrapped is unregistered instead. Returns what it guarded (empty when
    owner-only is off: today's behaviour). Idempotent.
    """
    if not rpc_owner_only(mode):
        return []
    guarded: list[str] = []
    local = getattr(room, "local_participant", None)
    handlers = getattr(local, "_rpc_handlers", None)
    for method in GUARDED_RPC_METHODS:
        try:
            if isinstance(handlers, dict):
                handler = handlers.get(method)
                if handler is None:
                    continue  # not registered: nothing answers it
                if not getattr(handler, _OWNER_ONLY_FLAG, False):
                    local.register_rpc_method(method, _owner_only_rpc(method, handler, session))
                guarded.append(method)
            elif local is not None:
                local.unregister_rpc_method(method)  # the handler cannot be wrapped: take the method away
                guarded.append(f"{method} (removed)")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("ROOM_INPUT could not guard %s: %s", method, type(e).__name__)
    streams = getattr(room, "_text_stream_handlers", None)
    events = getattr(session, "_client_events_handler", None)
    callback = getattr(events, "_text_input_cb", None)
    topics = list(GUARDED_TEXT_TOPICS)
    if callable(callback) and hasattr(events, "register_text_input"):
        if not getattr(callback, _OWNER_ONLY_FLAG, False):
            events.register_text_input(_owner_only_text_input(callback, session))
        guarded.append(f"{TEXT_INPUT_TOPIC} text input")
    else:
        topics.append(TEXT_INPUT_TOPIC)  # no callback to wrap: guard the text stream itself
    for topic in topics:
        handler = streams.get(topic) if isinstance(streams, dict) else None
        if handler is None:
            continue
        try:
            if not getattr(handler, _OWNER_ONLY_FLAG, False):
                room.unregister_text_stream_handler(topic)
                room.register_text_stream_handler(topic, _owner_only_stream(topic, handler, session))
            guarded.append(topic)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("ROOM_INPUT could not guard %s: %s", topic, type(e).__name__)
    logger.info("ROOM_INPUT owner-only: %s", ", ".join(guarded) or "nothing to guard")
    return guarded


def clipboard_tip_text(payload: Any) -> str:
    """The K-19 ClipboardSuggestion (or the old plain str) as one text: the copied error, the search,
    the tip and its source; the same information the model got before."""
    if isinstance(payload, dict):
        fields = (("error_excerpt", "error"), ("query", "search"), ("solution", "tip"), ("source_url", "source"))
        return "\n".join(f"{label}: {payload[key]}" for key, label in fields if payload.get(key))
    return "" if payload is None else str(payload)


async def start_memory_loop(assistant: BrainAssistant | Any, memory_extractor: MemoryExtractor | None = None) -> None:
    """Continuous memory extraction loop."""
    memory_extractor = memory_extractor or MemoryExtractor(getattr(assistant, "user_id", None))
    while True:
        try:
            # Fetch latest history from ChatContext messages
            history_items = []
            history_list = getattr(assistant, 'chat_ctx', None)
            if history_list and hasattr(history_list, 'messages'):
                # Handle both property, synchronous method, and asynchronous coroutine
                msg_source = history_list.messages
                if callable(msg_source):
                    messages = msg_source()
                else:
                    messages = msg_source

                # Critical Fix: LiveKit messages may return a coroutine
                if asyncio.iscoroutine(messages) or hasattr(messages, '__await__'):
                    messages = await messages

                # Make sure messages is iterable and not a ChatContext object itself
                if not isinstance(messages, list):
                    if hasattr(messages, "messages"):
                        messages = messages.messages
                    elif not hasattr(messages, "__iter__"):
                        messages = []

                history_items = list(messages)[-20:] if messages else []

            if not history_items:
                history = getattr(assistant, "history", None)
                items = getattr(history, "items", None)
                if callable(items):
                    items = items()
                if asyncio.iscoroutine(items) or hasattr(items, "__await__"):
                    items = await items
                if isinstance(items, list):
                    history_items = items[-20:]
                elif items and hasattr(items, "__iter__"):
                    history_items = list(items)[-20:]

            if history_items:
                logger.debug("Running memory extraction for %s", getattr(assistant, "user_id", "User"))
                await memory_extractor.run(history_items)
            await asyncio.sleep(120) # Throttled to 2 minutes to ensure smoothness
        except Exception as e:
            logger.error("Memory loop error: %s", e)
            await asyncio.sleep(60)


async def start_heartbeat_loop() -> None:
    """Periodic pulse to keep services from timing out."""
    logger.info("💓 Heartbeat monitor active.")
    while True:
        try:
            health_monitor.record_heartbeat("agent_runner")
            health_monitor.record_heartbeat("backend_core")
            health_monitor.record_heartbeat("autonomous_protector")
        except Exception as e:
            logger.error("Heartbeat loop error: %s", e)
        await asyncio.sleep(30) # Reduced frequency to save resources


async def _location_loop(notify_location: Callable[[Any], Awaitable[Any]]) -> None:
    """Sends the ipinfo location to the UI every 60 s. Local mode only: from the VPS ipinfo answers for
    the data centre, and the Desktop Node reports the device location instead (K-72)."""
    while True:
        try:
            loc_data = await get_current_city_data()
            await notify_location(loc_data)
        except Exception as e:
            logger.error("Location loop error: %s", e)
        await asyncio.sleep(60) # Location doesn't change often


def _resolve_user_id(room: Any) -> str:
    """Memory identity: JARVIS_USER_ID in every mode (K-69 v4), so the PC and the VPS share one memory
    file. When it is unset: the old per-job identity (the first remote participant, else this agent's
    own), with a WARNING."""
    if config.user_id:
        return config.user_id
    user_id = "User"
    remote_participants = list(getattr(room, "remote_participants", {}).values())
    if remote_participants:
        # Take the first remote participant if available
        first_participant = remote_participants[0]
        user_id = first_participant.identity
    elif getattr(room, "local_participant", None):
        # Fallback to local participant identity if remote not found yet
        user_id = room.local_participant.identity
    logger.warning("JARVIS_USER_ID is not set; memory is keyed by this job's participant identity")
    return user_id


async def perform_startup_diagnostics() -> None:
    """Run pre-flight checks and log results."""
    logger.info("Initializing Pre-flight health check...")
    try:
        health_report_list = await diagnostics_instance.run_all()
        # Simplified logging for health report list
        logger.info("✅ SYSTEM HEALTH: %d checks passed",
                    len(health_report_list))
    except Exception as e:
        logger.error("Startup diagnostics failed: %s", e)


async def _start_background_tasks(session: AgentSession, assistant: BrainAssistant,
                                  mode: str | None = None) -> list[asyncio.Task[Any]]:
    """Starts all background loops and monitors via the autonomous protector.

    mode (default JARVIS_DESKTOP_MODE): remote mode (K-72) starts no reminder loop (agent-svc owns
    reminders) and no clipboard monitor (the Desktop Node runs it); local mode starts everything.
    """
    local = (mode or desktop_mode()) == DESKTOP_MODE_LOCAL
    logger.info("🔄 Starting protected background tasks...")

    # Launch background loops via the autonomous protector to ensure resilience
    await autonomous_protector.run_protected("memory_loop", start_memory_loop, assistant, assistant.memory_extractor)
    await autonomous_protector.run_protected("heartbeat_loop", start_heartbeat_loop)
    await autonomous_protector.run_protected("ui_command_listener", start_ui_command_listener, assistant)

    # 💥 NEW: Self-Monitoring & Proactive Analysis Loops
    if local:
        await autonomous_protector.run_protected("reminder_loop", start_reminder_loop, session)
    await autonomous_protector.run_protected("bug_hunter_loop", start_bug_hunter_loop, session)
    await autonomous_protector.run_protected("memory_storage_loop", start_memory_storage_loop, assistant)

    # 🧠 ADAPTIVE BRAIN: Throttles high-load tasks during stress
    await autonomous_protector.run_protected("adaptation_loop", start_adaptation_loop)

    # 🚑 HEALING ENGINE: Detects heart-stops and triggers recovery
    await autonomous_protector.run_protected("healing_loop", healing_engine.start_healing_loop)

    # Register Recovery Actions
    async def _recover_ui_bridge():
        logger.warning("🚑 HEALING: Attempting to notify UI of runner pulse...")
        from services.utils.jarvis_bridge import (
            notify_ui,  # pylint: disable=import-outside-toplevel
        )
        await notify_ui("HEARTBEAT")

    async def _recover_backend_core():
        logger.warning(
            "🚑 HEALING: Backend Core heartbeat timeout. Re-pulsing...")
        health_monitor.record_heartbeat("backend_core")

    healing_engine.register_recovery_action("ui_bridge", _recover_ui_bridge)
    healing_engine.register_recovery_action(
        "backend_core", _recover_backend_core)

    clip_task = None
    if local:
        clip_monitor = ClipboardMonitor()

        async def _on_clipboard_detected(solution):
            # C-124: clipboard text never reaches the log, and the tip reaches the model as quoted,
            # untrusted user-side desktop data, never as a system notification.
            tip = clipboard_tip_text(solution)
            logger.info("📋 Clipboard tip ready (%d chars)", len(tip))
            if assistant and hasattr(assistant, "chat_ctx"):
                # BrainAssistant.chat_ctx is an llm.ChatContext, whose messages is a method: the tip goes
                # into its items list (a plain messages list is used when one is there).
                try:
                    message = desktop_data_message(CLIPBOARD_TIP_LABEL, tip)
                    added = append_chat_message(assistant.chat_ctx, message)
                except RuntimeError:  # a read-only LiveKit chat context
                    added = False
                if not added:
                    logger.debug("Clipboard tip not added: no writable chat context.")

        # Clipboard monitor doesn't naturally support a coro_func retry without refactor,
        # so we wrap it in a custom task for now.
        clip_task = asyncio.create_task(clip_monitor.start(_on_clipboard_detected))

    # Return all managed tasks to be added to the session's active_tasks set
    managed_tasks = list(autonomous_protector._tasks.values())
    if clip_task is not None:
        managed_tasks.append(clip_task)
    return managed_tasks


def _start_loop_watchdog(ctx: agents.JobContext,
                         track_task: Callable[[Coroutine[Any, Any, Any]], asyncio.Task[Any]],
                         running_tools: Callable[[], set[str]]) -> LoopWatchdog:
    """
    C-31: the event-loop stall watchdog of this session, started on the running loop. Its task is
    one of the session's tasks, so _cleanup_session_resources cancels it. When the room
    disconnects, LiveKit ends the job without cancelling this entrypoint, so the cleanup never
    runs; the job's shutdown stops the watchdog then. Either way its watcher thread ends too.
    """
    watchdog = LoopWatchdog(context=running_tools, name="agent")
    track_task(watchdog.run())
    ctx.add_shutdown_callback(watchdog.stop)
    return watchdog


def _prewarm_vector_memory(room_name: str | None = None) -> None:
    """
    C-137: starts the vector memory (Chroma, the sentence-transformers import and its model) in its
    background thread at job start, so no event loop runs that import and the first recall or write
    finds the memory ready. Once per process: a later job finds it running or ready. It opens Chroma,
    so it runs here in the job (K-69); a worker's prewarm hook may only call warm_model().
    C-124: in remote mode only the job serving JARVIS_ROOM_NAME (jarvis-hybrid) opens Chroma; a job for
    another room (canary, probe) runs without vector recall.
    """
    try:
        from services.ai_core.jarvis_vector_memory import (  # pylint: disable=import-outside-toplevel
            jarvis_vector_db,
        )
        if (isinstance(room_name, str) and desktop_mode() == DESKTOP_MODE_REMOTE
                and room_name != (os.getenv("JARVIS_ROOM_NAME") or DEFAULT_ROOM_NAME)):
            jarvis_vector_db.disable("this job does not serve the JARVIS room")
            return
        jarvis_vector_db.prewarm()
    except Exception as e:  # the memory then starts at its first use, still off the loop
        logger.warning("VECTOR_PREWARM not started: %s", e)


def _prewarm_voice_id(room_name: str | None = None) -> None:
    """
    C-72: loads the speaker-ID model (and the master embedding) in its own background thread at job
    start and picks the faster torch thread count, so the first voice check does not pay the model load.
    Never blocks the loop; once per process. Without the model files it logs 'VOICE_ID prewarm skipped'
    once and verification stays lazy (denied) as before. Same gate as the vector prewarm (C-124): in
    remote mode only the job serving JARVIS_ROOM_NAME loads it, so canary/probe jobs do not load ECAPA.
    """
    try:
        if (isinstance(room_name, str) and desktop_mode() == DESKTOP_MODE_REMOTE
                and room_name != (os.getenv("JARVIS_ROOM_NAME") or DEFAULT_ROOM_NAME)):
            return
        from services.ai_core.voice_fingerprint import (  # pylint: disable=import-outside-toplevel
            voice_id_engine,
        )
        voice_id_engine.prewarm()
    except Exception as e:  # verification then loads the model at its first use, still off the loop
        logger.warning("VOICE_ID prewarm not started: %s", e)


RAG_WARM_DELAY_DEFAULT_S = 120.0


def _rag_warm_delay() -> float | None:
    """C-78: seconds before the document index warms (JARVIS_RAG_WARM_DELAY_S, default 120); a negative
    value turns the warm-up off, and a question then starts the index itself."""
    raw = (os.getenv("JARVIS_RAG_WARM_DELAY_S") or "").strip()
    if not raw:
        return RAG_WARM_DELAY_DEFAULT_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning("JARVIS_RAG_WARM_DELAY_S=%r is not a number; using %.0f s", raw, RAG_WARM_DELAY_DEFAULT_S)
        return RAG_WARM_DELAY_DEFAULT_S
    return None if value < 0 else value


async def _warm_document_index(delay_s: float) -> None:
    """
    C-78: after delay_s, loads or builds the local document index for ask_about_document. The walk runs
    in a worker thread (jarvis_rag.DocumentRAG.warm_index), so the voice loop never waits for it, and the
    delay keeps it clear of the other start-up file walks. A failure is logged; the first question then
    starts the index.
    """
    try:
        await asyncio.sleep(delay_s)
        from services.ai_core.jarvis_rag import (  # pylint: disable=import-outside-toplevel
            rag_system,
        )
        await rag_system.warm_index()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("DOC_INDEX warm-up failed: %s", e)


async def _process_user_audio(publication: rtc.TrackPublication, assistant: BrainAssistant) -> None:
    """Subscribes to user audio and feeds frames to the assistant for Voice ID."""
    if assistant.muted:
        logger.info("🔇 Assistant is muted. Skipping subscription to track: %s", publication.sid)
        return

    logger.info("🎤 Subscribing to audio track: %s", publication.sid)
    try:
        # Cast to Any to allow dynamic subscription methods if Mypy's stubs are incomplete
        pub_any: Any = publication
        pub_any.set_subscribed(True)
        # FAST FAIL: Polling without timeout is a production anti-pattern
        async def _wait_for_track_safe():
            total_wait = 0.0
            while not publication.track and total_wait < 10.0:
                await asyncio.sleep(0.1)
                total_wait += 0.1
            return publication.track

        track = await _wait_for_track_safe()
        if not track:
            logger.error("❌ Audio track TIMEOUT: %s failed to load within 10s", publication.sid)
            return

        audio_stream = rtc.AudioStream(track)
        async for event in audio_stream:
            # LiveKit AudioStream yields AudioFrameEvent
            if hasattr(event, 'frame') and event.frame:
                assistant.add_audio_frame(event.frame)
            elif hasattr(event, 'audio_frame') and event.audio_frame:
                assistant.add_audio_frame(event.audio_frame) # type: ignore
            elif hasattr(event, 'data'):
                # Handle direct AudioFrame yields in some SDK versions
                assistant.add_audio_frame(event)  # type: ignore
            # Log only once to avoid flooding
            elif not getattr(assistant, "_audio_format_warned", False):
                logger.warning("Unsupported audio event type: %s", type(event))
                assistant._audio_format_warned = True
    except Exception as e:
        logger.error("Audio subscription error: %s", e)


async def _cleanup_session_resources(session: AgentSession, tasks: list[asyncio.Task[Any]]) -> None:
    """Safely cleans up background tasks and session objects."""
    logger.info("🧹 Performing session cleanup...")

    # Reset autonomous protector to gracefully stop its loops
    await autonomous_protector.cancel_all()

    if session:
        try:
            logger.info("🛑 Stopping AgentSession...")
            session_any: Any = session
            await asyncio.wait_for(session_any.stop(), timeout=10.0)
        except Exception as e:
            logger.debug("Session stop error: %s", e)

    if tasks:
        logger.info("🛑 Cleaning up background tasks...")
        real_tasks = []
        for task in tasks:
            if task and isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
            if isinstance(task, asyncio.Future):
                real_tasks.append(task)
        if real_tasks:
            await asyncio.gather(*real_tasks, return_exceptions=True)


def _print_startup_banner() -> None:
    """Prints the JARVIS startup banner."""
    print("\n" + "="*50)
    print("🚀 JARVIS SYSTEMS ONLINE")
    print("🔱 Elite AI Assistant | Sir Matloob Edition")
    print("🛡️ Security: Enabled | 🧠 Brain: Active")
    print("🎤 Standing by for wake word: 'Jarvis'")
    print("="*50 + "\n")


async def entrypoint(ctx: agents.JobContext):
    """Main job entrypoint."""
    # K-29 backstop for _mask_cli_log_handler(): masks any root handler added since then.
    redact_root_handlers()
    net_guard.harden_process()  # C-124: again in the job (PR_SET_DUMPABLE does not survive an exec)
    local = desktop_mode() == DESKTOP_MODE_LOCAL
    max_retries = 5
    attempt = 0

    while attempt < max_retries:
        session: AgentSession | None = None
        # ULTRA-STRICT: Centralized Task Tracking Set
        active_tasks: set[asyncio.Task] = set()

        def track(coro: Coroutine[Any, Any, Any], _active_tasks: set[asyncio.Task] = active_tasks) -> asyncio.Task[Any]:
            t: asyncio.Task[Any] = asyncio.create_task(coro)
            _active_tasks.add(t)
            t.add_done_callback(_active_tasks.discard)
            return t
        try:
            from services.utils.jarvis_bridge import (  # pylint: disable=import-outside-toplevel
                notify_location,
                notify_thinking,
                notify_transcription,
                notify_ui,
            )
            logger.info("Attempting to start session (Timeout: 60s)...")
            # INCREASED TIMEOUT: From 10s to 60s for high-load systems (Sir Matloob's Dell PC)
            await asyncio.wait_for(ctx.connect(), timeout=60.0)
            logger.info("✅ Connected to room: %s", ctx.room.name)
            # C-137: loads in its own thread while the session starts (C-124: the JARVIS room's job only)
            _prewarm_vector_memory(getattr(ctx.room, "name", None))
            # C-72: speaker-ID model loads in its own thread (C-124: the JARVIS room's job only)
            _prewarm_voice_id(getattr(ctx.room, "name", None))

            # Now perform secondary initialization
            current_dt_res = await get_formatted_datetime()
            city = await get_current_city()
            
            # --- Plugin & Tool Discovery (Centralized) ---
            from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
            plugin_manager = JarvisPluginManager()
            # Root-level services directory
            package_path = os.path.join(config.project_root, "services")
            plugin_manager.discover_plugins(package_path)
            if not local:
                remote_compat.install_arg_hooks(plugin_manager)  # C-124: VPS argument hooks
            external_tools = plugin_manager.get_livekit_tools()
            logger.info("🛠️ Discovered %d external tools from plugins.", len(external_tools))
            if not local:
                remote_compat.self_test()  # K-72: every shim still bound once discovery imported the tools

            # Tune VAD to be less sensitive to background noise and prevent stuttering
            vad = silero.VAD.load(
                min_speech_duration=0.2, # Sir Matloob's PC - higher threshold reduces noise jitter
                min_silence_duration=1.0,
            )

            # USER PREFERRED EXPERIMENTAL MODEL: models/gemini-2.5-flash-native-audio-latest
            llm_model = google.realtime.RealtimeModel(
                voice="Charon", # Standardized Capitalized ID
                model="models/gemini-2.5-flash-native-audio-latest",
            )

            # Memory identity: JARVIS_USER_ID, else the participant identity as before
            user_id = _resolve_user_id(ctx.room)

            chat_ctx = llm.ChatContext()
            
            # BrainAssistant now receives the LLM and the external tools
            # It will add its internal tools (persona, wake-word) automatically
            assistant = BrainAssistant(
                chat_ctx=chat_ctx,
                llm_instance=llm_model,
                tools=external_tools,
                current_date=current_dt_res.get("formatted"),
                current_city=city,
                user_id=user_id,
            )

            # Realtime model owns in-room voice. Do not attach Cloud TTS for chat
            # (AI Studio keys are not Cloud TTS credentials; chat uses text notify).
            session = AgentSession(
                preemptive_generation=True,
                allow_interruptions=True,
                vad=vad,
                stt=None,
                llm=llm_model,
                tts=None,
            )

            # --- Internal Session Signaling Handlers ---
            # Set to WARNING to see important SDK signals but ignore trivial ones
            logging.getLogger("livekit").setLevel(logging.WARNING)
            _quiet_livekit_agents_logger()

            @ctx.room.on("data_received")
            def on_data_received(data: rtc.DataPacket): # pylint: disable=useless-return
                # Suppress ignoring byte stream warnings for internal topics
                if data.topic in ["lk.agent.session", "lk.agent.monitor", "lk.agent.chat"]:
                    return  # pylint: disable=useless-return


            logger.info("[AGENT] Connecting room and starting session...")
            await session.start(room=ctx.room, agent=assistant, room_options=session_room_options())
            logger.info("[AGENT] Session started successfully.")
            harden_room_input(session, ctx.room)  # C-124: owner-only RPC and text (VPS; PC opt-in)
            follow_owner_reconnects(session, ctx.room)  # the owner comes back under a new identity
            assistant.attach_session(session)

            # --- Location Update Loop --- (local mode only, K-72)
            if local:
                track(_location_loop(notify_location))


            # --- Speaker Identification Subscription ---
            @ctx.room.on("track_published")
            def on_track_published(publication: rtc.TrackPublication, participant: rtc.RemoteParticipant, _assistant=assistant):
                if publication.kind == rtc.TrackKind.KIND_AUDIO:
                    track(_process_user_audio(publication, _assistant))

            # Subscribe to existing tracks
            for participant in ctx.room.remote_participants.values():
                for publication in participant.track_publications.values():
                    if publication.kind == rtc.TrackKind.KIND_AUDIO:
                        track(_process_user_audio(publication, assistant))
            # --------------------------------------------

            # --- Message ID Tracking for UI Stability ---
            current_user_msg_id = None

            @session.on("user_input_transcribed")
            def on_user_transcription(event: agents.voice.UserInputTranscribedEvent, _assistant=assistant):
                nonlocal current_user_msg_id
                if event.transcript:
                    if _assistant.muted:
                        # Skip UI broadcast if muted
                        return

                    # 🔋 PERFORMANCE OPTIMIZATION: Only notify UI of FINAL user transcriptions
                    # This prevents the UI from over-rendering one word at a time, which was causing hangs.
                    if event.is_final:
                        logger.info("🎤 User Transcription (Final): %s", event.transcript)

                        # Generate or reuse ID
                        if current_user_msg_id is None:
                            current_user_msg_id = str(uuid.uuid4())

                        track(notify_transcription(
                            "user", event.transcript, is_final=True,
                            msg_id=current_user_msg_id))

                        current_user_msg_id = None # Reset for next turn
                    else:
                        # Optional: Just log locally for debugging, don't flood the UI
                        logger.debug("🎤 Partial Input: %s", event.transcript)

            @session.on("conversation_item_added")
            def on_conversation_item_added(event: agents.voice.ConversationItemAddedEvent):
                item = event.item
                if hasattr(item, 'role') and item.role == "assistant":
                    text = getattr(item, 'text_content', None)
                    if text:
                        logger.info("🤖 Agent Transcription: %s", text)
                        track(notify_transcription(
                            "agent", text, is_final=True))

            @session.on("agent_state_changed")
            def on_agent_state_changed(event: agents.voice.AgentStateChangedEvent):
                state = event.new_state
                logger.info("🤖 Agent State: %s", state)

                # Update UI States
                if state == "speaking":
                    track(notify_ui("START"))
                    track(notify_thinking("STOP"))
                elif state == "thinking":
                    track(notify_thinking("START"))
                    track(notify_ui("STOP"))
                else:
                    track(notify_ui("STOP"))
                    track(notify_thinking("STOP"))

            @session.on("error")
            def _s_error(err: Any) -> None:
                logger.error("⚠️ Session Error: %s", err)

            _print_startup_banner()
            _start_loop_watchdog(ctx, track, plugin_manager.running_tools)
            bg_tasks = await _start_background_tasks(session, assistant)
            for bt in bg_tasks:
                active_tasks.add(bt)
                bt.add_done_callback(active_tasks.discard)

            # C-78: the document index warms after a delay, so its walk never overlaps the start-up walks
            rag_warm_delay = _rag_warm_delay()
            if local and rag_warm_delay is not None:
                track(_warm_document_index(rag_warm_delay))

            # Wait until session ends, room disconnects, or UI stop command
            stop_event = asyncio.Event()
            assistant._stop_event = stop_event # Store on assistant for access

            await stop_event.wait()

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Shutdown signal received.")
            break
        except Exception as exc:
            # Categorize the error for better visibility
            err_type = type(exc).__name__
            logger.error("⚠️ Session attempt %d failed [%s]: %s", attempt + 1, err_type, exc)
            logger.error(traceback.format_exc())

            if "TimeoutError" in str(exc) or "handshake" in str(exc).lower():
                logger.warning("🕒 Handshake timeout detected. System may be under heavy load.")

            attempt += 1
            if attempt < max_retries:
                retry_delay = 5 * attempt
                logger.info("🔄 Retrying in %ds...", retry_delay)
                await asyncio.sleep(retry_delay)
        finally:
            await _cleanup_session_resources(session, list(active_tasks))


# --------------------------------------------------------------------------- C-136: LiveKit link state

_ENV_FILES = (agent_lock.PROJECT_ROOT / ".env", agent_lock.PROJECT_ROOT / "deploy" / "windows-local" / ".env")
_link_monitor: livekit_link.LinkMonitor | None = None


def _restart_env() -> dict[str, str]:
    """This worker's start environment without the keys of the .env files: load_dotenv never overrides,
    so the fresh worker takes them from the files again, with any edit made since this worker started."""
    from_files: set[str] = set()
    for path in _ENV_FILES:
        if path.is_file():
            from_files.update(dotenv_values(path))
    return {name: value for name, value in _START_ENV.items() if name not in from_files}


def start_link_monitor() -> livekit_link.LinkMonitor | None:
    """
    C-136 (src/core/livekit_link.py): the LiveKit link monitor of the PC brain's worker, in the process that
    holds run/agent.lock or whose parent does; elsewhere nothing changes. JARVIS_LINK_RECOVERY=0 keeps the
    state file and the log lines but never asks agent_ctl for a fresh worker.
    """
    global _link_monitor  # pylint: disable=global-statement
    if _link_monitor is not None or desktop_mode() != DESKTOP_MODE_LOCAL or in_container():
        return _link_monitor
    owner = livekit_link.lock_owner()
    if owner is None:
        return None
    recovery = (os.getenv("JARVIS_LINK_RECOVERY") or "1").strip().lower() not in ("0", "false", "no", "off")
    monitor = livekit_link.LinkMonitor(
        livekit_link.link_path(), owner, recovery=recovery, recoveries=livekit_link.inherited_recoveries(),
        base_env=_restart_env, redact=redact_secrets, log=logger.info)
    monitor.start()
    _link_monitor = monitor
    return monitor


def _quiet_livekit_agents_logger() -> None:
    """The entrypoint's WARNING level for livekit.agents, but INFO while the link monitor runs in this
    process (thread job executors, the Windows default): its handler prints no other INFO line."""
    level = logging.INFO if _link_monitor is not None else logging.WARNING
    logging.getLogger(livekit_link.LK_LOGGER).setLevel(level)


def _hook_link_monitor() -> None:
    """
    C-136: start_link_monitor() right after LiveKit's CLI configures logging, which it does in the process
    that runs the LiveKit worker (start mode: this one; dev mode: the reloader's child), never in a job.
    A LiveKit release without the hook starts as before, without the monitor.
    """
    configure_logger = getattr(livekit_cli_module, "_configure_logger", None)
    if not callable(configure_logger) or getattr(configure_logger, "starts_link_monitor", False):
        return

    @functools.wraps(configure_logger)
    def configure_and_monitor(*args: Any, **kwargs: Any) -> None:
        configure_logger(*args, **kwargs)
        try:
            start_link_monitor()
        except Exception as exc:  # the worker starts without the monitor
            logger.warning("LIVEKIT LINK monitor not started: %s", exc)

    configure_and_monitor.starts_link_monitor = True  # type: ignore[attr-defined]
    livekit_cli_module._configure_logger = configure_and_monitor  # pylint: disable=protected-access


_hook_link_monitor()


def check_runtime_profile() -> RuntimeProfile:
    """
    K-72 start gate, run by build_worker_options for both start paths. The worker serves the agent role
    only. Local mode belongs to the PC brain alone: with JARVIS_BRAIN=vps or inside a container it means
    JARVIS_DESKTOP_MODE is missing or misspelt, and the start is refused instead of running the PC
    sensors, reminders and task monitor there. Remote mode (the VPS containers) checks its keys first: a
    missing JARVIS_ENCRYPTION_KEY stops the start, and so does any other missing key in production. The
    PC brain starts as before.
    """
    profile = runtime_profile()
    logger.info("RUNTIME role=%s brain=%s mode=%s", profile.role, profile.brain, profile.mode)
    if profile.role != ROLE_AGENT:
        raise SystemExit(f"JARVIS_ROLE={profile.role}: the LiveKit worker runs only the agent role")
    if profile.mode == DESKTOP_MODE_LOCAL and (profile.brain == BRAIN_VPS or in_container()):
        where = "JARVIS_BRAIN=vps" if profile.brain == BRAIN_VPS else "a container"
        raise SystemExit(f"[JARVIS-ERROR] local desktop mode is the PC brain only; {where} needs "
                         "JARVIS_DESKTOP_MODE=remote")
    if profile.mode == DESKTOP_MODE_REMOTE:
        try:
            config.validate(ROLE_AGENT, DESKTOP_MODE_REMOTE)
        except RuntimeError as e:
            raise SystemExit(f"[JARVIS-ERROR] {e}") from e
    return profile


def build_worker_options(
    entrypoint_fnc: Callable[[agents.JobContext], Awaitable[None]] | None = None,
) -> agents.WorkerOptions:
    """WorkerOptions for the single PC worker (health server on 127.0.0.1:8081 by default).

    Shared by both start paths: src.core.agent imports it from here, so this module never has to
    import src.core.agent (that would be an import cycle). The explicit port also replaces dev
    mode's random one. JARVIS_AGENT_NAME='' keeps automatic dispatch until OP-22 sets 'jarvis'
    (explicit dispatch, K-44). check_runtime_profile() runs first.
    """
    check_runtime_profile()
    options: dict[str, Any] = {
        "entrypoint_fnc": entrypoint if entrypoint_fnc is None else entrypoint_fnc,
        "host": os.getenv("JARVIS_WORKER_HOST") or "127.0.0.1",
        "port": int(os.getenv("JARVIS_WORKER_PORT") or "8081"),
        "agent_name": os.getenv("JARVIS_AGENT_NAME", "").strip(),
    }
    if desktop_mode() != DESKTOP_MODE_REMOTE:
        options["load_fnc"] = pc_worker_load
    return agents.WorkerOptions(**options)


def pc_worker_load() -> float:
    """
    The load the PC worker reports to LiveKit: always 0.0. It is the owner's only worker and serves only
    the JARVIS room, so it must never call itself full. livekit-agents' default load is the CPU use with
    a 0.7 threshold in production, so a busy laptop (2026-10-03 20:14: CPU 95-99 %) was marked full and
    the owner's job was never dispatched; JARVIS stayed out of its room. A second agent is still prevented
    by ensure_room.py and the room watchdog, which dispatch only when the room has no live agent.
    """
    return 0.0


if __name__ == "__main__":
    cli.run_app(build_worker_options(entrypoint))
