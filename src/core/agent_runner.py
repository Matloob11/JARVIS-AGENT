"""
# agent_runner.py
Entrypoint and session management for the JARVIS agent.
"""

# pylint: disable=broad-exception-caught, wrong-import-position, wrong-import-order, ungrouped-imports
# ruff: noqa: E402

import asyncio
import functools
import inspect
import logging
import math
import os
import socket
import sys
import threading
import time
import traceback
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
from services.utils.jarvis_autonomous import (
    TaskProtector,
    autonomous_protector,
    job_protector,
    release_job_protector,
)
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
from services.utils.jarvis_logger import setup_logger, start_ui_log_shipping
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

from src.core import gemini_resume_guard
from src.core.jarvis_runtime import is_error_close_reason
from src.core.conversation_router import ConversationRouter, preview
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


def session_room_options(text_input_cb: Callable[..., Any] | None = None) -> Any:
    """
    The room options of the voice session. close_on_disconnect is off: by default livekit-agents closes the
    whole AgentSession when the linked participant leaves on purpose (a page refresh), and since the bridge mints a
    new identity for every token the owner always comes back as another participant, so the agent stayed in the
    room but never listened again (2026-10-03). With it off the session stays up and follow_owner_reconnects()
    links the owner's new identity.
    C-42 (K-59): text_input_cb (the ConversationRouter's) answers 'lk.chat' instead of LiveKit's default, so
    typed text from the room takes the same path as typed text from the UI. None keeps LiveKit's default.
    C-23 (K-60 v9): delete_room_on_close=False explicitly: closing a session never deletes the room (the bridge owns
    it), whatever a later livekit-agents default says.
    """
    if text_input_cb is None:
        return lk_room_io.RoomOptions(close_on_disconnect=False, delete_room_on_close=False)
    return lk_room_io.RoomOptions(close_on_disconnect=False, delete_room_on_close=False,
                                  text_input=lk_room_io.TextInputOptions(text_input_cb=text_input_cb))


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


PC_VITALS_INTERVAL_S = 10.0


def _pc_vitals_payload() -> dict[str, Any]:
    """This laptop's vitals_pc for the bridge (K-40): cpu, ram, disk, cores and ram_total_gb from the Desktop
    Node's reader, plus disk_label ('C:'), host and ts. Blocking (about 0.3 s), so it runs in a thread."""
    from src.core.desktop.sensors import collect_vitals  # pylint: disable=import-outside-toplevel

    drive = (os.environ.get("SystemDrive") or "C:").strip()[:1].upper()
    label = f"{drive if drive.isalpha() else 'C'}:" if sys.platform == "win32" else "/"
    return {**collect_vitals(), "disk_label": label, "host": socket.gethostname(), "ts": round(time.time(), 3)}


async def _pc_vitals_loop(notify_pc_vitals: Callable[[dict[str, Any]], Awaitable[Any]],
                          interval: float = PC_VITALS_INTERVAL_S) -> None:
    """Sends this laptop's vitals to the UI every 10 s (Quick Stats, Agents > Machines). Local mode only: in vps
    mode the container's own figures would be the VPS's, and the Desktop Node reports the laptop's (K-74)."""
    while True:
        try:
            await notify_pc_vitals(await asyncio.to_thread(_pc_vitals_payload))
        except Exception as e:
            logger.error("PC vitals loop error: %s", e)
        await asyncio.sleep(interval)


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
                                  mode: str | None = None,
                                  protector: TaskProtector | None = None) -> list[asyncio.Task[Any]]:
    """Starts all background loops and monitors via a task protector: the job's own (C-33, from the entrypoint),
    else the global autonomous protector.

    mode (default JARVIS_DESKTOP_MODE): remote mode (K-72) starts no reminder loop (agent-svc owns
    reminders) and no clipboard monitor (the Desktop Node runs it); local mode starts everything.
    """
    local = (mode or desktop_mode()) == DESKTOP_MODE_LOCAL
    tasks = protector if protector is not None else autonomous_protector
    logger.info("🔄 Starting protected background tasks...")

    # Launch background loops via the protector to ensure resilience
    await tasks.run_protected("memory_loop", start_memory_loop, assistant, assistant.memory_extractor)
    await tasks.run_protected("heartbeat_loop", start_heartbeat_loop)
    await tasks.run_protected("ui_command_listener", start_ui_command_listener, assistant)

    # 💥 NEW: Self-Monitoring & Proactive Analysis Loops
    if local:
        await tasks.run_protected("reminder_loop", start_reminder_loop, session)
    await tasks.run_protected("bug_hunter_loop", start_bug_hunter_loop, session)
    await tasks.run_protected("memory_storage_loop", start_memory_storage_loop, assistant)

    # 🧠 ADAPTIVE BRAIN: Throttles the bug hunter during sustained stress (C-33: on this job's protector)
    await tasks.run_protected("adaptation_loop", start_adaptation_loop, tasks)

    # 🚑 HEALING ENGINE: Detects heart-stops and triggers recovery
    await tasks.run_protected("healing_loop", healing_engine.start_healing_loop)

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
    managed_tasks = list(tasks._tasks.values())
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


CLEANUP_STEP_TIMEOUT_S = 10.0  # each teardown step (assistant shutdown, session close) waits at most this long


async def _bounded_call(name: str, method: Any) -> None:
    """Calls method() and awaits it when it returns an awaitable, for at most CLEANUP_STEP_TIMEOUT_S; never raises
    (C-33: a wedged session or assistant cannot hold the job's teardown hostage). A cancel passes through."""
    if not callable(method):
        return
    try:
        result = method()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, CLEANUP_STEP_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Cleanup step %s failed: %s", name, type(e).__name__)


async def _cleanup_session_resources(session: AgentSession, tasks: list[asyncio.Task[Any]],
                                     protector: TaskProtector | None = None,
                                     assistant: Any = None) -> None:
    """Safely cleans up background tasks and session objects. C-33: the JOB's protector (never another job's loops;
    the global one only for an old caller), then the assistant's own loops (task monitor, vision), then the session
    with aclose() (AgentSession has no stop() in livekit-agents 1.4.6; that call failed silently before)."""
    logger.info("🧹 Performing session cleanup...")

    # Stop this job's protected loops (the UI listener's cancel closes its bridge socket)
    await (protector if protector is not None else autonomous_protector).cancel_all()
    release_job_protector(protector)

    if assistant is not None:
        await _bounded_call("assistant.shutdown", getattr(assistant, "shutdown", None))

    if session:
        logger.info("🛑 Closing AgentSession...")
        await _bounded_call("session.aclose", getattr(session, "aclose", None))

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


# ------------------------------------------------------------------ C-23: the job's session state and its end
# K-68 v9: the agent mirrors its session in the participant attribute jarvis.session, which the bridge's room owner
# reads (voice_ready needs 'running'; an agent without the attribute counts as running).
SESSION_ATTR = "jarvis.session"
SESSION_STARTING, SESSION_RUNNING, SESSION_RESTARTING = "starting", "running", "restarting"
SESSION_PAUSED, SESSION_CLOSED = "paused", "closed"
SESSION_ATTR_TIMEOUT_S = 5.0
JOB_END_NOTIFY_S = 3.0                  # notify job_end waits at most this long before ctx.shutdown
END_PAUSED = "paused_by_owner"          # pause_agent (K-60 v9)
END_BY_COMMAND = "ended_by_command"     # the stop event without a reason (an assistant before C-23)
END_SESSION_FAILED = "session_failed"   # every session attempt of this job failed
END_ROOM_DISCONNECTED = "room_disconnected"  # C-33: the room connection is gone (its event or the liveness check)
END_SESSION_ERROR = "session_error"          # C-33: the AgentSession closed itself on an unrecoverable error
END_JOB_SHUTDOWN = "job_shutdown"            # C-33: LiveKit itself shut the job down (e.g. a worker shutdown)
FRAMEWORK_TEARDOWN_WAIT_S = 8.0              # how long a LiveKit-initiated shutdown waits for our teardown


async def _write_session_attr(ctx: Any, value: str) -> bool:
    """Sets jarvis.session on the agent's own participant (at most SESSION_ATTR_TIMEOUT_S); never raises."""
    try:
        local = ctx.room.local_participant
        await asyncio.wait_for(local.set_attributes({SESSION_ATTR: value}), SESSION_ATTR_TIMEOUT_S)
    except Exception as e:  # pylint: disable=broad-exception-caught  # best effort: the room may be gone (a cancel
        # is a BaseException and passes)
        logger.debug("SESSION attribute %s not set: %s", value, type(e).__name__)
        return False
    return True


# Strong references until each write is done. On Windows every job runs in its own thread and event loop
# (livekit-agents' THREAD executor), so the set is shared under a lock and each job only ever waits on its own
# loop's writes.
_session_attr_tasks: set[asyncio.Task[bool]] = set()
_session_attr_lock = threading.Lock()


def _forget_session_attr_task(task: asyncio.Task[bool]) -> None:
    with _session_attr_lock:
        _session_attr_tasks.discard(task)


def _set_session_attr(ctx: Any, value: str) -> asyncio.Task[bool] | None:
    """jarvis.session, best effort and never blocking: a task of its own (None outside a running loop)."""
    try:
        task = asyncio.ensure_future(_write_session_attr(ctx, value))
    except RuntimeError:
        return None
    with _session_attr_lock:
        _session_attr_tasks.add(task)
    task.add_done_callback(_forget_session_attr_task)
    return task


def _pending_session_attr_tasks() -> list[asyncio.Task[bool]]:
    """This job's attribute writes still running: only the running loop's (another job's belong to another loop)."""
    loop = asyncio.get_running_loop()
    with _session_attr_lock:
        return [task for task in _session_attr_tasks if not task.done() and task.get_loop() is loop]


def _job_id_of(ctx: Any) -> str:
    try:
        job_id = ctx.job.id
    except Exception:  # pylint: disable=broad-exception-caught  # a stand-in context without a job
        return ""
    return job_id if isinstance(job_id, str) else ""


async def _report_job_end(ctx: Any, reason: str) -> None:
    """notify job_end {job_id, reason} (K-40/K-60 v9), best effort and at most JOB_END_NOTIFY_S: the bridge's room
    owner then stops counting this job as in flight (LiveKit v1.13.7 never marks a job ended)."""
    job_id = _job_id_of(ctx)
    if not job_id:
        return
    try:
        from services.utils.jarvis_bridge import notify_event  # pylint: disable=import-outside-toplevel
        await asyncio.wait_for(notify_event("job_end", {"job_id": job_id, "reason": reason}), JOB_END_NOTIFY_S)
    except Exception as e:  # pylint: disable=broad-exception-caught  # never delays the end beyond the timeout
        logger.debug("JOB END: job_end not reported: %s", type(e).__name__)


def _is_console_job(ctx: Any) -> bool:
    """livekit-agents' console mode runs a fake job: it is never shut down from here."""
    try:
        return bool(ctx.is_fake_job())
    except Exception:  # pylint: disable=broad-exception-caught
        return False


async def _end_job(ctx: Any, ended: str | None, framework_shutdown: bool = False) -> None:
    """
    C-23 (K-60 v9): after the session loop ends (not on cancel, not in console mode) the job ends instead of staying
    in the room without a session: jarvis.session 'paused' for a pause (else 'closed'), notify job_end, then
    ctx.shutdown(reason) - 'paused_by_owner', 'ended_by_command', 'session_failed' after the last failed attempt, and
    (C-33, K-60 v16) 'session_error' or 'room_disconnected'. The bridge then dispatches a fresh job (with backoff), or
    none while paused. Never JobContext.delete_room.
    framework_shutdown (C-33): LiveKit is already shutting the job down (room gone, worker shutdown). The attribute
    write and the job_end notify are skipped then: the room cannot take them, the bridge's room owner stops counting
    a job whose agent it saw leave, and LiveKit only holds the loop for a few seconds (FRAMEWORK_TEARDOWN_WAIT_S).
    """
    reason = ended or END_SESSION_FAILED
    if _is_console_job(ctx):
        logger.info("JOB END (%s): console mode keeps the job", reason)
        return
    if not framework_shutdown:
        pending = _pending_session_attr_tasks()
        if pending:  # an earlier 'starting'/'running' write must never land after the last one
            await asyncio.wait(pending, timeout=SESSION_ATTR_TIMEOUT_S)
        await _write_session_attr(ctx, SESSION_PAUSED if reason == END_PAUSED else SESSION_CLOSED)
        await _report_job_end(ctx, reason)
    logger.warning("JOB END: %s; ctx.shutdown", reason)
    try:
        ctx.shutdown(reason=reason)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("JOB END: ctx.shutdown failed: %s", type(e).__name__)


# ------------------------------------------------------------------ C-33 (step A): a job never outlives its room
# 06-08 Oct (Hp laptop): after a laptop sleep or a network loss the rtc engine gave up ('failed to reconnect to the
# livekit room') without ever emitting 'disconnected', and the Gemini session closed itself on an unrecoverable error.
# Neither LiveKit nor this entrypoint ended those jobs: seven kept every loop and their bridge listener for days
# (worker active_jobs=8; one dashboard SIM search ran twice, with two Allow dialogs). A job now ends
# (request_end -> _end_job -> ctx.shutdown) on its session's close with reason ERROR, on the room's 'disconnected'
# event, and on this liveness check; the bridge (C-23 always mode) then dispatches a fresh job.
ROOM_LIVENESS_INTERVAL_S = 5.0
ROOM_DEAD_AFTER_S = 90.0       # seconds of AWAKE time without a usable room before the job ends
ROOM_PROBE_EVERY = 6           # every 6th check (~30 s) asks the rtc engine itself (a local FFI call, no traffic)
ROOM_PROBE_TIMEOUT_S = 10.0


def _room_connected(room: Any) -> bool | None:
    """True/False from the room's connection state; None when the room has no usable state (a stand-in)."""
    state = getattr(room, "connection_state", None)
    if isinstance(state, bool) or not isinstance(state, int):
        return None
    return state == rtc.ConnectionState.CONN_CONNECTED


async def _rtc_engine_alive(room: Any) -> bool | None:
    """True when room.get_rtc_stats() answers within ROOM_PROBE_TIMEOUT_S; False when it fails (a closed engine fails
    even when no state change ever reached Python); None without a usable probe."""
    probe = getattr(room, "get_rtc_stats", None)
    if not callable(probe):
        return None
    try:
        pending = probe()
        if not inspect.isawaitable(pending):
            return None
        await asyncio.wait_for(pending, ROOM_PROBE_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("ROOM probe failed: %s", type(e).__name__)
        return False
    return True


def _room_watchable(room: Any) -> bool:
    """True when the room offers a connection state or an async engine probe (a real rtc.Room); a stand-in without
    either has nothing to watch."""
    return _room_connected(room) is not None or inspect.iscoroutinefunction(getattr(room, "get_rtc_stats", None))


async def _room_liveness_loop(room: Any, end_job: Callable[[str], Any],
                              interval: float = ROOM_LIVENESS_INTERVAL_S,
                              dead_after: float = ROOM_DEAD_AFTER_S) -> None:
    """Calls end_job(END_ROOM_DISCONNECTED) once the room has been unusable for dead_after seconds of awake time:
    the connection state was not CONNECTED for that many consecutive checks, or the engine probe failed that many
    consecutive times. Checks are COUNTED, not timed, so after a laptop sleep the first check counts once and the rtc
    engine keeps its full reconnect budget; a healthy reading resets its counter."""
    if not _room_watchable(room):
        logger.debug("ROOM liveness: nothing to watch on %s", type(room).__name__)
        return
    state_needed = max(1, math.ceil(dead_after / interval))
    probe_needed = max(1, math.ceil(dead_after / (interval * ROOM_PROBE_EVERY)))
    state_misses = probe_failures = checks = 0
    while True:
        await asyncio.sleep(interval)
        checks += 1
        connected = _room_connected(room)
        if connected is False:
            state_misses += 1
        elif connected is True:
            state_misses = 0
        if checks % ROOM_PROBE_EVERY == 0:
            alive = await _rtc_engine_alive(room)
            if alive is False:
                probe_failures += 1
            elif alive is True:
                probe_failures = 0
        if state_misses >= state_needed or probe_failures >= probe_needed:
            logger.warning("ROOM_DEAD: no usable room connection for ~%.0f s of awake time (state misses %d, probe "
                           "failures %d); ending the job", dead_after, state_misses, probe_failures)
            end_job(END_ROOM_DISCONNECTED)
            return


async def _close_loop_bridge_client() -> None:
    """C-39 step 2 (K-57): closes this job loop's bridge HTTP client at the very end of the job; never raises."""
    try:
        from services.utils.jarvis_bridge import aclose_loop_client  # pylint: disable=import-outside-toplevel
        await asyncio.wait_for(aclose_loop_client(), 3.0)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("bridge client close: %s", type(e).__name__)


# ------------------------------------------------------------------ C-32: worker prewarm (design §1.1)
# The heavy singletons load ONCE per executor (WorkerOptions.prewarm_fnc), BEFORE any job: plugin
# discovery (JarvisPluginManager is a lock-guarded singleton, so a second prewarm is a cheap
# re-entry), the silero VAD with the exact values the entrypoint used inline, and the city (5 s cap,
# config.user_city fallback; in remote mode get_current_city never calls ipinfo — K-72, plan 8690).
# A job reads them from ctx.proc.userdata['tools'|'vad'|'city']; a missing key falls back to the old
# inline path (PREWARM_MISS), so the worker stays bootable even if prewarm never ran (dev console
# mode, tests). K-69: a worker's prewarm hook may only warm models — Chroma stays in the job
# (_prewarm_vector_memory).

PREWARM_CITY_TIMEOUT_S = 5.0


def _prewarm_city() -> str:
    """The city for the instructions, resolved once at prewarm. No job loop runs yet (prewarm is
    synchronous, before the executor serves jobs), so asyncio.run; a slow or failed lookup falls
    back to config.user_city within PREWARM_CITY_TIMEOUT_S."""

    async def _city() -> str:
        return await asyncio.wait_for(get_current_city(), PREWARM_CITY_TIMEOUT_S)

    try:
        city = asyncio.run(_city())
    except Exception as e:  # the fallback is today's USER_CITY default; never blocks the prewarm
        logger.warning("PREWARM city fell back to USER_CITY: %s", type(e).__name__)
        return config.user_city
    return city if isinstance(city, str) and city else config.user_city


def prewarm(proc: Any) -> None:
    """C-32: fills JobProcess.userdata['tools'|'vad'|'city'] once per executor. Each part is
    independent: a failed one is logged and left unset, and that job boots inline (PREWARM_MISS) —
    a broken prewarm must never keep the worker from serving jobs."""
    started = time.monotonic()
    userdata = proc.userdata
    try:
        from services.ai_core.jarvis_plugin_manager import (  # pylint: disable=import-outside-toplevel
            JarvisPluginManager,
        )
        plugin_manager = JarvisPluginManager()
        plugin_manager.discover_plugins(os.path.join(config.project_root, "services"))
        if desktop_mode() != DESKTOP_MODE_LOCAL:
            remote_compat.install_arg_hooks(plugin_manager)  # C-124: before the LiveKit conversion
        userdata["tools"] = plugin_manager.get_livekit_tools()
    except Exception as e:
        logger.warning("PREWARM tools failed (the job discovers inline): %s", e)
    try:
        # Tune VAD to be less sensitive to background noise and prevent stuttering — the exact
        # values the entrypoint loaded inline before C-32 (behavior identical).
        userdata["vad"] = silero.VAD.load(
            min_speech_duration=0.2,  # Sir Matloob's PC - higher threshold reduces noise jitter
            min_silence_duration=1.0,
        )
    except Exception as e:
        logger.warning("PREWARM vad failed (the job loads inline): %s", e)
    userdata["city"] = _prewarm_city()
    logger.info("PREWARM_DONE tools=%d in %.1fs", len(userdata.get("tools") or ()), time.monotonic() - started)


def _proc_userdata(ctx: Any) -> dict[str, Any]:
    """The prewarmed singletons of this job's executor (JobProcess.userdata). A context without
    them (console/dev mode, tests, a stand-in) yields {} and the entrypoint boots inline."""
    userdata = getattr(getattr(ctx, "proc", None), "userdata", None)
    return userdata if isinstance(userdata, dict) else {}


def _print_startup_banner() -> None:
    """Prints the JARVIS startup banner."""
    print("\n" + "="*50)
    print("🚀 JARVIS SYSTEMS ONLINE")
    print("🔱 Elite AI Assistant | Sir Matloob Edition")
    print("🛡️ Security: Enabled | 🧠 Brain: Active")
    print("🎤 Standing by for wake word: 'Jarvis'")
    print("="*50 + "\n")


async def entrypoint(ctx: agents.JobContext):
    """Main job entrypoint. Up to 5 session attempts; the job ends (ctx.shutdown) when the assistant's stop event is
    set (pause_agent: 'paused_by_owner'; C-33: 'session_error' when the session closes on an unrecoverable error,
    'room_disconnected' when the room is gone) or when every attempt failed ('session_failed'), never by starting
    another session in place (C-23, K-60). Each attempt runs its loops on its own task protector (C-33)."""
    # K-29 backstop for _mask_cli_log_handler(): masks any root handler added since then.
    redact_root_handlers()
    # C-64: the UI log shipper of this process (once per process: a Windows job thread finds it running, a Linux
    # job process starts its own).
    start_ui_log_shipping()
    net_guard.harden_process()  # C-124: again in the job (PR_SET_DUMPABLE does not survive an exec)
    local = desktop_mode() == DESKTOP_MODE_LOCAL
    max_retries = 5
    attempt = 0
    ended: str | None = None
    cancelled = False

    # C-33: livekit-agents never awaits or cancels this entrypoint when IT ends the job (room 'disconnected', a worker
    # shutdown): it runs the shutdown callbacks, cancels its own tasks and stops the loop, which would cut our
    # teardown off. This callback asks the current attempt to end and holds the loop (bounded) until it has.
    entry_task = asyncio.current_task()
    job_state: dict[str, Any] = {"request_end": None, "protector": None, "framework_shutdown": False}

    async def _hold_for_teardown(reason: str) -> None:
        job_state["framework_shutdown"] = True
        request_end = job_state["request_end"]
        if callable(request_end):
            request_end(END_ROOM_DISCONNECTED if "disconnect" in str(reason).lower() else END_JOB_SHUTDOWN)
        if entry_task is not None and entry_task is not asyncio.current_task() and not entry_task.done():
            await asyncio.wait({entry_task}, timeout=FRAMEWORK_TEARDOWN_WAIT_S)
        if entry_task is None or not entry_task.done():  # the teardown did not finish in time: the cheap parts
            release_job_protector(job_state["protector"])
            await _close_loop_bridge_client()

    try:
        ctx.add_shutdown_callback(_hold_for_teardown)
    except Exception as e:  # pylint: disable=broad-exception-caught  # a stand-in context without callbacks
        logger.debug("no shutdown callback: %s", type(e).__name__)

    while attempt < max_retries:
        session: AgentSession | None = None
        router: ConversationRouter | None = None
        assistant: Any = None
        protector: TaskProtector | None = None  # C-33: this attempt's own loops (never the global protector)
        room_handlers: list[tuple[str, Any]] = []  # C-33: removed in finally, so a retry never doubles them
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
                notify_pc_vitals,
                notify_thinking,
                notify_ui,
            )
            logger.info("Attempting to start session (Timeout: 60s)...")
            # INCREASED TIMEOUT: From 10s to 60s for high-load systems (Sir Matloob's Dell PC)
            await asyncio.wait_for(ctx.connect(), timeout=60.0)
            logger.info("✅ Connected to room: %s", ctx.room.name)
            _set_session_attr(ctx, SESSION_STARTING)  # K-68 v9 (best effort, never blocking)
            # C-137: loads in its own thread while the session starts (C-124: the JARVIS room's job only)
            _prewarm_vector_memory(getattr(ctx.room, "name", None))
            # C-72: speaker-ID model loads in its own thread (C-124: the JARVIS room's job only)
            _prewarm_voice_id(getattr(ctx.room, "name", None))

            # Now perform secondary initialization (the date is computed locally — no network call)
            current_dt_res = await get_formatted_datetime()

            # --- C-32: the prewarmed singletons of this executor (prewarm()). A missing key boots
            # inline exactly as before C-32 and logs PREWARM_MISS (dev console mode, tests, or a
            # job that arrived before/without prewarm).
            userdata = _proc_userdata(ctx)

            # --- Plugin & Tool Discovery (Centralized; once per process since C-32) ---
            from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
            plugin_manager = JarvisPluginManager()
            external_tools = userdata.get("tools")
            if external_tools is None:
                logger.warning("PREWARM_MISS key=%s", "tools")
                # Root-level services directory
                package_path = os.path.join(config.project_root, "services")
                plugin_manager.discover_plugins(package_path)
                if not local:
                    remote_compat.install_arg_hooks(plugin_manager)  # C-124: VPS argument hooks
                external_tools = plugin_manager.get_livekit_tools()
                logger.info("🛠️ Discovered %d external tools from plugins.", len(external_tools))
            if not local:
                remote_compat.self_test()  # K-72: every shim still bound once discovery imported the tools

            vad = userdata.get("vad")
            if vad is None:
                logger.warning("PREWARM_MISS key=%s", "vad")
                # Tune VAD to be less sensitive to background noise and prevent stuttering
                vad = silero.VAD.load(
                    min_speech_duration=0.2, # Sir Matloob's PC - higher threshold reduces noise jitter
                    min_silence_duration=1.0,
                )

            city = userdata.get("city")
            if not isinstance(city, str) or not city:
                logger.warning("PREWARM_MISS key=%s", "city")
                city = await get_current_city()

            # USER PREFERRED EXPERIMENTAL MODEL: models/gemini-2.5-flash-native-audio-latest
            llm_model = google.realtime.RealtimeModel(
                voice="Charon", # Standardized Capitalized ID
                model="models/gemini-2.5-flash-native-audio-latest",
            )
            # 2026-10-06: after a network blip the plugin replayed a stale session-resumption handle on
            # every reconnect (APIError/close 1008 'Requested entity was not found') until the session
            # died. The guard drops the stale handle so the plugin's own retry reconnects fresh; no new
            # restart loop, and the 5-attempt session cap of this entrypoint still applies (K-60 v9).
            gemini_resume_guard.guard_realtime_model(llm_model)

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
            # C-23: the stop event comes with the assistant, before the UI listener (_start_background_tasks) can
            # receive a pause_agent; one that arrives before the wait below is never lost.
            stop_event = getattr(assistant, "_stop_event", None)
            if not isinstance(stop_event, asyncio.Event):
                stop_event = asyncio.Event()
                assistant._stop_event = stop_event  # pylint: disable=protected-access

            # C-33: every end path goes through this (an assistant before C-23 has no request_end)
            def _request_end(reason: str, _assistant=assistant, _stop=stop_event) -> None:
                end = getattr(_assistant, "request_end", None)
                if callable(end):
                    end(reason)
                    return
                if getattr(_assistant, "end_reason", None) is None:
                    try:
                        _assistant.end_reason = reason
                    except AttributeError:
                        pass
                _stop.set()

            job_state["request_end"] = _request_end
            # C-42: typed chat (UI and lk.chat) goes into this job's session through the router, and the
            # router emits every transcript message once (conversation_item_added).
            router = ConversationRouter(assistant, spawn=track)
            assistant.attach_router(router)

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
            room_handlers.append(("data_received", on_data_received))


            logger.info("[AGENT] Connecting room and starting session...")
            await session.start(room=ctx.room, agent=assistant,
                                room_options=session_room_options(router.text_input_cb))
            logger.info("[AGENT] Session started successfully.")
            _set_session_attr(ctx, SESSION_RUNNING)  # K-68 v9: the bridge's voice_ready needs it
            router.attach(session)
            harden_room_input(session, ctx.room)  # C-124: owner-only RPC and text (VPS; PC opt-in)
            follow_owner_reconnects(session, ctx.room)  # the owner comes back under a new identity
            assistant.attach_session(session)

            # C-33 (step A): the job ends when its session or its room is gone; the bridge dispatches a fresh job.
            @session.on("close")
            def _on_session_close(event: Any, _end=_request_end) -> None:
                if is_error_close_reason(getattr(event, "reason", None)):
                    logger.warning("SESSION closed on an unrecoverable error; ending the job")
                    _end(END_SESSION_ERROR)

            def _on_room_disconnected(*_args: Any, _end=_request_end) -> None:
                _end(END_ROOM_DISCONNECTED)

            ctx.room.on("disconnected")(_on_room_disconnected)
            room_handlers.append(("disconnected", _on_room_disconnected))
            track(_room_liveness_loop(ctx.room, _request_end))

            # --- Location and PC vitals loops --- (local mode only, K-72 / K-40)
            if local:
                track(_location_loop(notify_location))
                track(_pc_vitals_loop(notify_pc_vitals))


            # --- Speaker Identification Subscription ---
            @ctx.room.on("track_published")
            def on_track_published(publication: rtc.TrackPublication, participant: rtc.RemoteParticipant, _assistant=assistant):
                if publication.kind == rtc.TrackKind.KIND_AUDIO:
                    track(_process_user_audio(publication, _assistant))
            room_handlers.append(("track_published", on_track_published))

            # Subscribe to existing tracks
            for participant in ctx.room.remote_participants.values():
                for publication in participant.track_publications.values():
                    if publication.kind == rtc.TrackKind.KIND_AUDIO:
                        track(_process_user_audio(publication, assistant))
            # --------------------------------------------

            # --- Transcript messages (K-58) ---
            # C-42: conversation_item_added is the single source of chat messages (voice user text with the
            # item id, typed text never twice, replies with turn_id); a final transcription is only logged.
            @session.on("user_input_transcribed")
            def on_user_transcription(event: agents.voice.UserInputTranscribedEvent, _assistant=assistant):
                if not event.transcript or _assistant.muted:
                    return
                if event.is_final:
                    logger.info("🎤 User Transcription (Final, %d chars): %s", len(event.transcript),
                                preview(event.transcript))
                    logger.debug("🎤 User Transcription (Final): %s", event.transcript)
                else:
                    # Optional: Just log locally for debugging, don't flood the UI
                    logger.debug("🎤 Partial Input: %s", event.transcript)

            @session.on("conversation_item_added")
            def on_conversation_item_added(event: agents.voice.ConversationItemAddedEvent,
                                           _router: ConversationRouter = router):
                _router.on_conversation_item_added(event)

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
            protector = job_protector()
            job_state["protector"] = protector
            bg_tasks = await _start_background_tasks(session, assistant, protector=protector)
            for bt in bg_tasks:
                active_tasks.add(bt)
                bt.add_done_callback(active_tasks.discard)

            # C-78: the document index warms after a delay, so its walk never overlaps the start-up walks
            rag_warm_delay = _rag_warm_delay()
            if local and rag_warm_delay is not None:
                track(_warm_document_index(rag_warm_delay))

            # C-23 (K-60 v9): wait until the job is asked to end (pause_agent, or C-62's shutdown_agent); 'stop'
            # only interrupts speech and never sets this. The job then ends: no other session in this job.
            await stop_event.wait()
            ended = getattr(assistant, "end_reason", None) or END_BY_COMMAND
            logger.info("SESSION ending: %s", ended)
            break

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Shutdown signal received.")
            cancelled = True
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
                _set_session_attr(ctx, SESSION_RESTARTING)
                retry_delay = 5 * attempt
                logger.info("🔄 Retrying in %ds...", retry_delay)
                await asyncio.sleep(retry_delay)
        finally:
            if router is not None:
                router.detach()  # typed chat gets the offline answer until the next session attaches
            await _cleanup_session_resources(session, list(active_tasks), protector=protector, assistant=assistant)
            room_off = getattr(ctx.room, "off", None)
            for event_name, handler in room_handlers if callable(room_off) else ():
                try:
                    room_off(event_name, handler)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.debug("room.off(%s) failed: %s", event_name, type(e).__name__)

    if not cancelled:
        # C-23: a paused, ended or failed job leaves the room (ctx.shutdown) instead of staying without a session,
        # which kept LiveKit from dispatching a fresh one.
        await _end_job(ctx, ended, framework_shutdown=job_state["framework_shutdown"])
    await _close_loop_bridge_client()


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


def _env_number(name: str, default: Any, cast: Callable[[str], Any]) -> Any:
    """An env knob as a number (K-69 names); empty or malformed keeps the default and logs the
    variable's name, never a value that could be a secret."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        logger.warning("%s is not a number; using %s", name, default)
        return default


def build_worker_options(
    entrypoint_fnc: Callable[[agents.JobContext], Awaitable[None]] | None = None,
) -> agents.WorkerOptions:
    """WorkerOptions for the single PC worker (health server on 127.0.0.1:8081 by default).

    Shared by both start paths: src.core.agent imports it from here, so this module never has to
    import src.core.agent (that would be an import cycle). The explicit port also replaces dev
    mode's random one. agent_name (K-69/K-72 v9, worker_agent_name): JARVIS_AGENT_NAME, else '' (automatic
    dispatch) in local mode and 'jarvis' in remote mode; the PC root .env says jarvis-pc from C-23 phase 3.
    check_runtime_profile() runs first.

    C-32 (K-69): prewarm_fnc warms the executor before any job; JARVIS_PREWARM_TIMEOUT (120),
    JARVIS_IDLE_PROCS (1 on the PC; the VPS compose says 2 — K-75) and JARVIS_LOAD_THRESHOLD
    ('inf': the production default 0.7 would mark the owner's only worker full; the one LiveKit
    warning 'inf' logs in production is accepted, design §1.1). The K-44 PC guard stays byte for
    byte: load_fnc = pc_worker_load (always 0.0) in every mode but remote.
    """
    check_runtime_profile()
    options: dict[str, Any] = {
        "entrypoint_fnc": entrypoint if entrypoint_fnc is None else entrypoint_fnc,
        "host": os.getenv("JARVIS_WORKER_HOST") or "127.0.0.1",
        "port": int(os.getenv("JARVIS_WORKER_PORT") or "8081"),
        "agent_name": worker_agent_name(),
        "prewarm_fnc": prewarm,
        "initialize_process_timeout": _env_number("JARVIS_PREWARM_TIMEOUT", 120.0, float),
        "num_idle_processes": _env_number("JARVIS_IDLE_PROCS", 1, int),
        "load_threshold": _env_number("JARVIS_LOAD_THRESHOLD", float("inf"), float),
    }
    if desktop_mode() != DESKTOP_MODE_REMOTE:
        options["load_fnc"] = pc_worker_load
    else:
        # Remote-mode extras (K-75, plan 8690), env-driven, so local behavior does not change:
        # JARVIS_SHUTDOWN_PROC_TIMEOUT (20) and JARVIS_JOB_MEMORY_WARN_MB (only when set; the VPS
        # compose says 3000).
        options["shutdown_process_timeout"] = _env_number("JARVIS_SHUTDOWN_PROC_TIMEOUT", 20.0, float)
        job_memory_warn_mb = _env_number("JARVIS_JOB_MEMORY_WARN_MB", 0, int)
        if job_memory_warn_mb > 0:
            options["job_memory_warn_mb"] = job_memory_warn_mb
    return agents.WorkerOptions(**options)


REMOTE_AGENT_NAME = "jarvis"  # K-72: the VPS brain's lk.agent.name


def worker_agent_name() -> str:
    """The lk.agent.name this worker registers (C-23, K-69 v9): JARVIS_AGENT_NAME, else '' (automatic dispatch, the
    PC worker until C-23 phase 3) in local mode and 'jarvis' in remote mode, so a missing line never breaks the
    dispatch."""
    name = (os.getenv("JARVIS_AGENT_NAME") or "").strip()
    if name:
        return name
    return REMOTE_AGENT_NAME if desktop_mode() == DESKTOP_MODE_REMOTE else ""


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
