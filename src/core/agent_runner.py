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
    UIBridgeListener,
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
from services.utils import jarvis_announcer
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
    DEFAULT_USER_ID,
    DESKTOP_MODE_LOCAL,
    DESKTOP_MODE_REMOTE,
    ROLE_AGENT,
    RuntimeProfile,
    config,
    desktop_mode,
    runtime_profile,
)
from services.utils.jarvis_logger import setup_logger, start_ui_log_shipping
from services.utils.jarvis_loopwatch import LoopWatchdog, sample_loop_lag  # C-31, C-53
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

from src.core import agent_settings  # C-54 (K-61); registers settings_update, settings_bootstrap, request_state
from src.core import gemini_resume_guard
from src.core import session_policy  # C-114: compression, truncation, re-seed, context refresh, idle close
from src.core import memory_recall  # C-53: the RECENT MEMORY block in the instructions at job start
from src.core.jarvis_runtime import JarvisRuntime
from src.core.voices import persona_voice  # C-50
from src.core.voice_link import VoiceLink  # C-35; registers ui_command voice_take_over
from services.utils.probe_context import bridge_suppressed, is_probe_room  # K-57 (C-39)
from src.core.conversation_router import ConversationRouter, preview, reply_persona, router_of
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


async def _announce_clipboard_tip(payload: Any) -> None:
    """C-55 (K-18/K-19): the tip in the UI chat and a tool row. It is spoken only with JARVIS_CLIPBOARD_SPEAK=1, and
    then as a fixed line: the copied text is untrusted and never goes to the model as instructions. Never raises."""
    try:
        if isinstance(payload, dict):
            excerpt = str(payload.get("error_excerpt") or "").strip()
            solution = str(payload.get("solution") or "").strip()
        else:
            excerpt, solution = "", str(payload or "").strip()
        text = (f"Clipboard par error: {excerpt}. Mumkina hal: {solution[:300]}" if excerpt
                else f"Clipboard tip: {solution[:300]}")
        await jarvis_announcer.announce(
            text, priority="low", speak=bool(getattr(config, "clipboard_speak", False)), kind="clipboard",
            spoken_text="Sir, clipboard ke error ka ek mumkina hal chat mein likh diya hai.")
        from services.utils.jarvis_bridge import notify_tool_action  # pylint: disable=import-outside-toplevel
        await notify_tool_action("clipboard_helper", excerpt[:200] or "tip", phase="end", status="success")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("clipboard tip not announced: %s", type(e).__name__)


def clipboard_tip_text(payload: Any) -> str:
    """The K-19 ClipboardSuggestion (or the old plain str) as one text: the copied error, the search,
    the tip and its source; the same information the model got before."""
    if isinstance(payload, dict):
        fields = (("error_excerpt", "error"), ("query", "search"), ("solution", "tip"), ("source_url", "source"))
        return "\n".join(f"{label}: {payload[key]}" for key, label in fields if payload.get(key))
    return "" if payload is None else str(payload)


MEMORY_LOOP_INTERVAL_S = 10.0  # C-52 (C-114 step 8): a final transcript reaches the memory file within about 10 s
MEMORY_FLUSH_TIMEOUT_S = 5.0   # the job's end waits at most this long for its last memory write


async def _memory_items(assistant: Any) -> list[Any]:
    """C-52: the job's messages for its memory extractor: the router's turn_log (every K-58 transcript message once,
    with its id, C-42) while a ConversationRouter is attached, else the assistant's chat context messages (an assistant
    without a router); never a turn re-seeded from memory at job start (C-114: ids 'reseed-*')."""
    router = router_of(assistant)
    if router is not None:
        return session_policy.without_reseeded(list(router.turn_log))
    history_items: list[Any] = []
    history_list = getattr(assistant, 'chat_ctx', None)
    if history_list and hasattr(history_list, 'messages'):
        # Handle both property, synchronous method, and asynchronous coroutine
        msg_source = history_list.messages
        messages = msg_source() if callable(msg_source) else msg_source
        # Critical Fix: LiveKit messages may return a coroutine
        if asyncio.iscoroutine(messages) or hasattr(messages, '__await__'):
            messages = await messages
        # Make sure messages is iterable and not a ChatContext object itself
        if not isinstance(messages, list):
            if hasattr(messages, "messages"):
                messages = messages.messages
            elif not hasattr(messages, "__iter__"):
                messages = []
        history_items = session_policy.without_reseeded(messages) if messages else []
    if not history_items:
        history = getattr(assistant, "history", None)
        items = getattr(history, "items", None)
        if callable(items):
            items = items()
        if asyncio.iscoroutine(items) or hasattr(items, "__await__"):
            items = await items
        if isinstance(items, list) or (items and hasattr(items, "__iter__")):
            history_items = session_policy.without_reseeded(items)
    return history_items


async def start_memory_loop(assistant: BrainAssistant | Any, memory_extractor: MemoryExtractor | None = None) -> None:
    """C-52: every MEMORY_LOOP_INTERVAL_S the job's new messages (_memory_items: the router's turn_log) go to the
    memory file; the extractor saves each message once, by its id, in one bundle per pass (before C-52 it read a
    20-item window every 120 s and stopped after the first 20 messages). The extractor is bound to the same source and
    to the assistant's reply persona, so its flush at the job's end and in the memory storage loop sees what this loop
    sees. The turns re-seeded from memory at job start (C-114) are never saved again."""
    memory_extractor = memory_extractor or MemoryExtractor(getattr(assistant, "user_id", None))
    bind = getattr(memory_extractor, "bind", None)
    if callable(bind) and not inspect.iscoroutinefunction(bind):  # a test's AsyncMock extractor has nothing to bind
        bind(source=functools.partial(_memory_items, assistant), persona=functools.partial(reply_persona, assistant))
    while True:
        try:
            history_items = await _memory_items(assistant)
            if history_items:
                await memory_extractor.run(history_items)
        except Exception as e:
            logger.error("Memory loop error: %s", type(e).__name__)
        await asyncio.sleep(MEMORY_LOOP_INTERVAL_S)


async def _flush_memory(assistant: Any, reason: str) -> int:
    """C-52 (C-114 step 8): at the job's end (its runtime teardown, and LiveKit's shutdown callback, which may stop the
    loop before the teardown is done) the messages the memory loop has not written yet go to the memory file, so the
    next job's re-seed finds them. Bounded by MEMORY_FLUSH_TIMEOUT_S; never raises; a probe job writes nothing."""
    flush = getattr(getattr(assistant, "memory_extractor", None), "flush", None)
    if not callable(flush):
        return 0
    try:
        result = flush()
        written = await asyncio.wait_for(result, MEMORY_FLUSH_TIMEOUT_S) if inspect.isawaitable(result) else 0
    except asyncio.CancelledError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught  # TimeoutError too: the save itself goes on
        logger.warning("MEMORY flush at the job end (%s) failed: %s", reason, type(e).__name__)
        return 0
    written = written if isinstance(written, int) and not isinstance(written, bool) else 0
    if written:
        logger.info("MEMORY flush at the job end (%s): %d message(s) written", reason, written)
    return written


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


async def _location_loop(notify_location: Callable[[Any], Awaitable[Any]],
                         on_location: Callable[[Any], Any] | None = None) -> None:
    """Sends the ipinfo location to the UI every 60 s. Local mode only: from the VPS ipinfo answers for
    the data centre, and the Desktop Node reports the device location instead (K-72). C-114: on_location (the job's
    SessionPolicy) gets each reading too, so a city change reaches the instructions without a second ipinfo call."""
    while True:
        try:
            loc_data = await get_current_city_data()
            if on_location is not None:
                on_location(loc_data)
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
    """Memory identity (K-69, C-52): JARVIS_USER_ID in every mode and brain (unset: 'matloob'), so the PC and the VPS
    share one memory file and every job of the owner writes the same one. The room's participant identity is never the
    memory key any more (it made a new memory file per job), and only its kind ('owner-*', _identity_kind) is logged:
    an identity never goes into a log line (AGENT-RUNNER's INFO lines reach the UI log panel)."""
    user_id = config.user_id or DEFAULT_USER_ID
    try:
        remote_participants = list((getattr(room, "remote_participants", None) or {}).values())
        identity = getattr(remote_participants[0], "identity", None) if remote_participants else None
    except Exception:  # pylint: disable=broad-exception-caught  # a stand-in room: nothing to log
        identity = None
    logger.info("MEMORY user=%s (room participant %s: never the memory key)", user_id,
                _identity_kind(identity) if isinstance(identity, str) and identity else "none yet")
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
                                  protector: TaskProtector | None = None,
                                  listener: UIBridgeListener | None = None) -> list[asyncio.Task[Any]]:
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
    # C-34: the job's own listener (JarvisRuntime acloses it at the end), else a new one
    await tasks.run_protected("ui_command_listener", start_ui_command_listener, assistant, listener)

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
            if not getattr(config, "clipboard_helper_enabled", True):  # K-19: the switch stops tips too
                return
            tip = clipboard_tip_text(solution)
            logger.info("📋 Clipboard tip ready (%d chars)", len(tip))
            await _announce_clipboard_tip(solution)
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


VECTOR_LAG_LABEL = "vector_startup_and_first_write"


def _prewarm_vector_memory(room_name: str | None = None,
                           spawn: Callable[[Coroutine[Any, Any, Any]], Any] | None = None) -> None:
    """
    C-137: starts the vector memory (Chroma, the sentence-transformers import and its model) in its
    background thread at job start, so no event loop runs that import and the first recall or write
    finds the memory ready. Once per process: a later job finds it running or ready. It opens Chroma,
    so it runs here in the job (K-69); a worker's prewarm hook may only call warm_model().
    C-124: in remote mode only the job serving JARVIS_ROOM_NAME (jarvis-hybrid) opens Chroma; a job for
    another room (canary, probe) runs without vector recall.
    C-53: spawn (the job's task tracker) runs the loop-lag sampler while the start-up runs and until the
    first write of the process has finished ('LOOP_LAG max_ms=<n>', jarvis_loopwatch.sample_loop_lag);
    a job that finds the memory ready and written to already samples nothing.
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
        watch = getattr(jarvis_vector_db, "lag_watch_needed", None)
        if spawn is not None and callable(watch) and watch() is True:
            spawn(sample_loop_lag(lambda: not watch(), label=VECTOR_LAG_LABEL, log=logger))
    except Exception as e:  # the memory then starts at its first use, still off the loop
        logger.warning("VECTOR_PREWARM not started: %s", e)


def _vector_model_prewarm_allowed() -> bool:
    """False under pytest: a test that runs the worker prewarm hook never loads the real embedding model (the conftest
    stand-in covers only VectorMemory.prewarm)."""
    return "pytest" not in sys.modules and "PYTEST_CURRENT_TEST" not in os.environ


def _prewarm_vector_model() -> None:
    """
    C-53 step 2 (K-69, C-32's 'Prewarm loads MiniLM ... but never opens Chroma'): the worker's prewarm hook starts the
    embedding model's load (the slow sentence-transformers import and all-MiniLM-L6-v2) in its own background thread
    (VectorMemory.warm_model) and never waits for it, so the job that serves jarvis-hybrid finds the import done and
    opens Chroma in its own start-up thread. Never opens Chroma; never raises.
    """
    if not _vector_model_prewarm_allowed():
        return
    try:
        from services.ai_core.jarvis_vector_memory import (  # pylint: disable=import-outside-toplevel
            jarvis_vector_db,
        )
        jarvis_vector_db.warm_model()
    except Exception as e:  # pylint: disable=broad-exception-caught  # the job's start-up loads it instead
        logger.warning("VECTOR_MODEL prewarm not started: %s", type(e).__name__)


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
        try:
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
        finally:
            # C-51: a tap paused by mute (cancelled) releases its stream; the track stays subscribed for RoomIO
            await _close_audio_stream(audio_stream)
    except Exception as e:
        logger.error("Audio subscription error: %s", e)


AUDIO_STREAM_CLOSE_TIMEOUT_S = 2.0


async def _close_audio_stream(stream: Any) -> None:
    """Closes one rtc.AudioStream of the voice-ID tap, bounded; never raises (a cancel still passes on)."""
    if not callable(getattr(stream, "aclose", None)):
        return
    try:
        await asyncio.wait_for(stream.aclose(), AUDIO_STREAM_CLOSE_TIMEOUT_S)
    except Exception as e:  # pylint: disable=broad-exception-caught  # (a cancel is a BaseException and passes)
        logger.debug("audio tap stream close: %s", type(e).__name__)


class UserAudioTaps:
    """
    C-51 (K-60): the job's legacy voice-ID audio taps (_process_user_audio), one task per remote audio track sid.
    BrainAssistant.set_muted pauses them (each task cancelled and its AudioStream closed; the track itself stays
    subscribed, because RoomIO reads the same track) and resumes them on unmute for the audio tracks still in the
    room. No tap starts while the assistant is muted.
    """

    def __init__(self, assistant: Any, room: Any,
                 spawn: Callable[[Coroutine[Any, Any, Any]], asyncio.Task[Any]]) -> None:
        self.assistant = assistant
        self.room = room
        self._spawn = spawn
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def add(self, publication: Any) -> bool:
        """Starts the tap for one remote audio publication, once per track and never while muted. True when one
        started."""
        if getattr(publication, "kind", None) != rtc.TrackKind.KIND_AUDIO or getattr(self.assistant, "muted", False):
            return False
        sid = str(getattr(publication, "sid", "") or id(publication))
        running = self._tasks.get(sid)
        if running is not None and not running.done():
            return False
        task = self._spawn(_process_user_audio(publication, self.assistant))
        self._tasks[sid] = task
        task.add_done_callback(functools.partial(self._forget, sid))
        return True

    def _forget(self, sid: str, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(sid) is task:
            del self._tasks[sid]

    def add_room_tracks(self) -> int:
        """A tap for every remote audio track already in the room; returns how many started."""
        started = 0
        participants = getattr(self.room, "remote_participants", None)
        for participant in list(participants.values()) if hasattr(participants, "values") else []:
            for publication in list(getattr(participant, "track_publications", {}).values()):
                if self.add(publication):
                    started += 1
        return started

    def pause(self) -> int:
        """Mute: every running tap is cancelled; returns how many."""
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        self._tasks.clear()
        if running:
            logger.info("🔇 Voice-ID audio tap paused (%d track(s))", len(running))
        return len(running)

    def resume(self) -> int:
        """Unmute: the taps start again for the audio tracks still in the room; returns how many."""
        started = self.add_room_tracks()
        if started:
            logger.info("🔊 Voice-ID audio tap resumed (%d track(s))", started)
        return started

    @property
    def running(self) -> int:
        """How many taps run now."""
        return sum(1 for task in self._tasks.values() if not task.done())


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

    # Stop this job's protected loops (the UI listener's cancel closes its bridge socket). Bounded: on 08 Oct a loop
    # that swallowed its cancel held this teardown forever and the job never ended.
    await _bounded_call("protector.cancel_all", (protector if protector is not None else autonomous_protector).cancel_all)
    release_job_protector(protector)

    if session:
        jarvis_announcer.detach(session)  # C-55: only this job's session (another job's stays attached)
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
            # Bounded like every teardown step (C-33, 08 Oct): a task that swallows its cancel never holds the job's end
            done, pending = await asyncio.wait(real_tasks, timeout=CLEANUP_STEP_TIMEOUT_S)
            for task in done:
                if not task.cancelled():
                    task.exception()  # retrieved, as gather(return_exceptions=True) did
            if pending:
                logger.warning("🛑 %d task(s) ignored their cancel; the job ends without them", len(pending))


# ------------------------------------------------------------------ C-33 step B: a session that is not the live one
ROOMIO_AUDIO_TRACK = "roomio_audio"  # livekit-agents' RoomIO output track (session_room_options names no other)
UNPUBLISH_TIMEOUT_S = 5.0
_ROOMIO_PARTS = ("_audio_input", "_video_input", "_audio_output", "_pre_connect_audio_handler")


def _agent_audio_sids(room: Any) -> list[str]:
    """The sids of the agent's own published RoomIO audio tracks."""
    publications = getattr(getattr(room, "local_participant", None), "track_publications", None)
    if not hasattr(publications, "items"):
        return []
    return [sid for sid, pub in list(publications.items()) if getattr(pub, "name", None) == ROOMIO_AUDIO_TRACK]


def _room_listeners_of(room: Any, session: Any) -> list[tuple[str, Any]]:
    """(event, callback) of the room listeners that the session's RoomIO and its streams still hold (livekit-agents
    1.4.6 never removes RoomIO's participant_disconnected handler nor its audio input's track_unpublished one)."""
    events = getattr(room, "_events", None)
    if not isinstance(events, dict):
        return []
    registered = [(event, callback) for event, callbacks in list(events.items()) for callback in list(callbacks)]
    owners: set[int] = set()
    for _event, callback in registered:
        room_io = getattr(callback, "__self__", None)
        if room_io is not None and getattr(room_io, "_agent_session", None) is session:
            owners.add(id(room_io))
            owners.update(id(part) for part in (getattr(room_io, name, None) for name in _ROOMIO_PARTS)
                          if part is not None)
    return [(event, callback) for event, callback in registered
            if getattr(callback, "__self__", None) is not None and id(callback.__self__) in owners]


async def _release_session(session: Any, agent: Any, room: Any) -> None:
    """
    C-33 step B (JarvisRuntime close_session hook): closes an AgentSession that is NOT the live one (a failed start
    attempt, the old session of an in-place rebuild, a failed rebuild) together with what livekit-agents 1.4.6 leaves
    behind for it, because the next session of this job starts the SAME BrainAssistant in the SAME room:
    - aclose() returns at once for a session whose start failed half way: its client events handler keeps the 'lk.chat'
      text handler (the next session's typed input is then refused), its RoomIO stays up, and the shared assistant
      keeps the dead activity, so every later start fails ('cannot start agent: an activity is already running');
    - RoomIO never unpublishes its 'roomio_audio' track: after a rebuild a silent agent track would stay in the room,
      and the dashboard's one <audio> element can pick it after a reconnect;
    - RoomIO's participant_disconnected and its audio input's track_unpublished handlers stay on the room.
    Runs before the next session exists, so every agent audio track is stale here. Bounded; never raises.
    """
    stale_tracks = _agent_audio_sids(room)
    await _bounded_call("session.aclose", getattr(session, "aclose", None))
    events = getattr(session, "_client_events_handler", None)
    if events is not None:  # aclose() of a started session closes it and clears this: a half-started one
        if getattr(events, "_started", False):
            await _bounded_call("client_events.aclose", getattr(events, "aclose", None))
        elif getattr(events, "_text_stream_handler_registered", False):
            try:
                room.unregister_text_stream_handler(TEXT_INPUT_TOPIC)
                events._text_stream_handler_registered = False  # pylint: disable=protected-access
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.debug("RUNTIME lk.chat handler not released: %s", type(e).__name__)
        session._client_events_handler = None  # pylint: disable=protected-access
    room_io = getattr(session, "_room_io", None)
    if room_io is not None:  # likewise
        await _bounded_call("room_io.aclose", getattr(room_io, "aclose", None))
        session._room_io = None  # pylint: disable=protected-access
    activity = getattr(agent, "_activity", None)
    if activity is not None and getattr(activity, "_session", None) is session:
        if getattr(session, "_started", False):
            # Still started, i.e. closing (an in-flight tool keeps its drain): livekit clears the activity itself when
            # that close ends. Forcing it here would let that late cleanup null the NEXT session's activity.
            logger.warning("RUNTIME the replaced session still holds the assistant (closing); left to livekit")
        else:
            # The orphan of a failed start: nothing in livekit closes it any more
            await _bounded_call("activity.aclose", getattr(activity, "aclose", None))
            if getattr(agent, "_activity", None) is activity:
                agent._activity = None  # pylint: disable=protected-access
    for event, callback in _room_listeners_of(room, session):
        try:
            room.off(event, callback)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("RUNTIME room listener %s not removed: %s", event, type(e).__name__)
    local = getattr(room, "local_participant", None)
    unpublished = 0
    for sid in stale_tracks:
        try:
            if sid in getattr(local, "track_publications", {}):
                await asyncio.wait_for(local.unpublish_track(sid), UNPUBLISH_TIMEOUT_S)
                unpublished += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("RUNTIME stale agent track not unpublished: %s", type(e).__name__)
    logger.info("RUNTIME released a replaced session (%d stale agent track(s) unpublished)", unpublished)


# ------------------------------------------------------------------ C-23: the job's session state and its end
# K-68 v9: the agent mirrors its session in the participant attribute jarvis.session, which the bridge's room owner
# reads (voice_ready needs 'running'; an agent without the attribute counts as running). C-114: every write also sets
# jarvis.session_ts, the write's time in whole epoch seconds as text (LiveKit attributes are strings), in the same
# set_attributes call; the bridge and the UI read only the keys they know, so they ignore it.
SESSION_ATTR = "jarvis.session"
SESSION_TS_ATTR = "jarvis.session_ts"
SESSION_STARTING, SESSION_RUNNING, SESSION_RESTARTING = "starting", "running", "restarting"
SESSION_PAUSED, SESSION_CLOSED = "paused", "closed"
SESSION_IDLE = session_policy.SESSION_IDLE  # C-114: written by the job's SessionPolicy right before an idle close
SESSION_ATTR_TIMEOUT_S = 5.0
JOB_END_NOTIFY_S = 3.0                  # notify job_end waits at most this long before ctx.shutdown
END_PAUSED = "paused_by_owner"          # pause_agent (K-60 v9)
END_BY_COMMAND = "ended_by_command"     # the stop event without a reason (an assistant before C-23)
END_SESSION_FAILED = "session_failed"   # every session attempt of this job failed
END_ROOM_DISCONNECTED = "room_disconnected"  # C-33: the room connection is gone (its event or the liveness check)
END_SESSION_ERROR = "session_error"          # C-33: the AgentSession closed itself on an unrecoverable error
END_JOB_SHUTDOWN = "job_shutdown"            # C-33: LiveKit itself shut the job down (e.g. a worker shutdown)
END_IDLE = session_policy.END_IDLE           # C-114: nobody in the room for JARVIS_IDLE_CLOSE_MIN, or the 24 h recycle
FRAMEWORK_TEARDOWN_WAIT_S = 8.0              # how long a LiveKit-initiated shutdown waits for our teardown


async def _write_session_attr(ctx: Any, value: str) -> bool:
    """Sets jarvis.session and, in the same call, jarvis.session_ts (C-114: now in whole epoch seconds, as text) on the
    agent's own participant (at most SESSION_ATTR_TIMEOUT_S); never raises."""
    try:
        local = ctx.room.local_participant
        attributes = {SESSION_ATTR: value, SESSION_TS_ATTR: str(int(time.time()))}
        await asyncio.wait_for(local.set_attributes(attributes), SESSION_ATTR_TIMEOUT_S)
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


def _retire_desktop_link(job_id: str) -> None:
    """C-119 (VPS brain): the job's Desktop Link stops for good as the job ends, before its process drains
    (desktop_proxy.end_job): every job of a room is the bridge peer job-<room>, so a link that reconnected from a
    draining process would push the next job's link off the bridge. Never raises."""
    try:
        from services.ai_core import desktop_proxy  # pylint: disable=import-outside-toplevel
        desktop_proxy.end_job(job_id)
    except Exception as e:  # pylint: disable=broad-exception-caught  # a job always ends
        logger.error("JOB END: Desktop Link not retired: %s", type(e).__name__)


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
    ctx.shutdown(reason) - 'paused_by_owner', 'ended_by_command', 'session_failed' after the last failed attempt,
    (C-33, K-60 v16) 'session_error' or 'room_disconnected', and (C-114) 'idle'. The bridge then dispatches a fresh job
    (with backoff), or none while paused. Never JobContext.delete_room.
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


# ------------------------------------------------------------------ C-75 (TD-19): spoken confirmations
# The model itself calls confirm_action, so page, search or clipboard text injected into the conversation could
# "approve" a medium-risk action (a WhatsApp send) without Sir saying anything. The confirm broker approves a spoken
# confirmation only after a FINAL voice transcript newer than the request says yes (services/ai_core/jarvis_confirm.py
# _user_affirmed); these helpers feed it and switch the check on when a job's session starts, so it fails closed.
# Only the voice STT path passes here: typed chat, lk.chat and RPC text never reach user_input_transcribed.


def _confirm_broker() -> Any:
    from services.ai_core.jarvis_confirm import confirmation_broker  # pylint: disable=import-outside-toplevel
    return confirmation_broker


def _enable_voice_confirm_check() -> None:
    """From this job's start on, confirm_action approves only after Sir's own spoken yes (never raises)."""
    try:
        _confirm_broker().enable_transcript_check()
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("voice confirmation check not enabled: %s", type(e).__name__)


def _note_final_transcript(event: Any, assistant: Any) -> None:
    """Passes one FINAL user transcript to the confirm broker (the broker keeps the last 20); a partial one, an empty
    one or one heard while the assistant is muted is ignored. Never raises."""
    text = getattr(event, "transcript", None)
    if not isinstance(text, str) or not text.strip() or not getattr(event, "is_final", False):
        return
    if getattr(assistant, "muted", False):
        return
    try:
        _confirm_broker().note_user_transcript(text)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("transcript not noted for confirmations: %s", type(e).__name__)


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
    a broken prewarm must never keep the worker from serving jobs. C-53: it also starts the vector
    memory's embedding model load in a background thread (_prewarm_vector_model; never Chroma)."""
    started = time.monotonic()
    userdata = proc.userdata
    try:
        from services.ai_core.jarvis_plugin_manager import (  # pylint: disable=import-outside-toplevel
            JarvisPluginManager,
        )
        plugin_manager = JarvisPluginManager()
        plugin_manager.discover_plugins(os.path.join(config.project_root, "services"))
        if desktop_mode() != DESKTOP_MODE_LOCAL:
            from services.ai_core import desktop_proxy  # pylint: disable=import-outside-toplevel
            desktop_proxy.install(plugin_manager)  # C-119: laptop proxies, registered once (never update_tools)
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
    _prewarm_vector_model()  # C-53: the embedding model loads in its own thread (never Chroma, K-69); not awaited
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


def _job_room_name(ctx: Any) -> str:
    """The job's room name, known before the connect (the job's own record, else ctx.room)."""
    try:
        name = ctx.job.room.name
    except Exception:  # pylint: disable=broad-exception-caught  # a stand-in context without a job record
        name = None
    if not isinstance(name, str) or not name:
        name = getattr(getattr(ctx, "room", None), "name", "")
    return name if isinstance(name, str) else ""


DEFAULT_REALTIME_MODEL = "models/gemini-2.5-flash-native-audio-latest"


def _realtime_model_name() -> str:
    """K-69: JARVIS_REALTIME_MODEL, else the pinned native-audio model (typed chat needs mid-session client content)."""
    return (os.getenv("JARVIS_REALTIME_MODEL") or "").strip() or DEFAULT_REALTIME_MODEL


async def _load_job_settings() -> agent_settings.AgentSettings:
    """C-54 (K-61): the saved settings a new job starts with, read in a thread (a probe job reads them too but never
    writes or syncs, K-57). load() gives the defaults for a missing or broken file; anything else that fails gives
    them too, with one WARNING, because this runs before the job's session exists and an error here would end every
    job as 'session_failed' (C-54 review)."""
    try:
        return await asyncio.to_thread(agent_settings.load)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # a settings problem never stops a job
        logger.warning("SETTINGS not loaded (%s); the job starts with the defaults", type(exc).__name__)
        return agent_settings.AgentSettings()


async def entrypoint(ctx: agents.JobContext):
    """Main job entrypoint (C-33 step B): one JarvisRuntime per job (src/core/jarvis_runtime.py).

    ctx.connect() runs once (60 s cap; a failure ends the job as 'connect_failed'); session.start is retried 3 times
    with a fresh AgentSession each time ('session_failed' after the last); an unrecoverable session error rebuilds the
    session IN PLACE, at most 3 times per 10 minutes ('session_error' beyond the cap); the room's 'disconnected' event
    or the liveness check ends the job ('room_disconnected'); the assistant's stop event ends it too (pause_agent:
    'paused_by_owner', K-60). Teardown is bounded and ends in _end_job (jarvis.session 'closed'/'paused', job_end,
    ctx.shutdown); room handlers are registered once per job and removed at the end; each job runs its loops on its
    own task protector. A cancelled job is cleaned up but never shut down again. C-114 (src/core/session_policy.py):
    the RealtimeModel compresses its context, the last turns from memory are re-seeded before the first start, the
    chat context is cut every 20 conversation items (the re-seeded turns whole with their memory note or not at all),
    and the job's SessionPolicy refreshes the instructions on a new date or city and ends the job ('idle') when nobody
    has been in the room (no linked participant and no person the bridge counts: kind STANDARD, not 'agent-*' or
    'probe-*') and nothing was said for JARVIS_IDLE_CLOSE_MIN minutes (default 30 on the VPS brain, off on the PC
    brain) or, once it is 24 h old, at its first 10 minutes without conversation; never while work runs, and never in
    console mode. C-53: the re-seed is off (v43); before the first session.start the job's instructions get a RECENT
    MEMORY data block instead (src/core/memory_recall.py: the newest saved messages of the persona the job runs as),
    and the vector memory's start-up is watched by a loop-lag sampler."""
    # K-57 (C-39): a probe or canary job (jarvis-probe-<epoch>, jarvis-canary-<epoch>) sets the suppression before
    # it creates any task: nothing it does reaches the bridge, the UI log, memory or the identity file, and it runs
    # no listener or background loop. The user's own job never sets it.
    probe = is_probe_room(_job_room_name(ctx))
    if probe:
        bridge_suppressed.set(True)
        logger.info("PROBE_JOB room=%s", _job_room_name(ctx))
    # K-29 backstop for _mask_cli_log_handler(): masks any root handler added since then.
    redact_root_handlers()
    # C-64: the UI log shipper of this process (once per process: a Windows job thread finds it running, a Linux
    # job process starts its own).
    start_ui_log_shipping()
    net_guard.harden_process()  # C-124: again in the job (PR_SET_DUMPABLE does not survive an exec)
    local = desktop_mode() == DESKTOP_MODE_LOCAL
    desktop_job = str(getattr(getattr(ctx, "job", None), "id", "") or "")  # C-119: the job id the link is used under
    from services.utils.jarvis_bridge import (  # pylint: disable=import-outside-toplevel
        notify_location,
        notify_pc_vitals,
        notify_thinking,
        notify_ui,
    )

    # ULTRA-STRICT: Centralized Task Tracking Set (cancelled at the job's end)
    active_tasks: set[asyncio.Task] = set()

    def track(coro: Coroutine[Any, Any, Any], _active_tasks: set[asyncio.Task] = active_tasks) -> asyncio.Task[Any]:
        t: asyncio.Task[Any] = asyncio.create_task(coro)
        _active_tasks.add(t)
        t.add_done_callback(_active_tasks.discard)
        return t

    # Per-job parts, built once after the connect (the first session_factory call) and shared by every session.
    job: dict[str, Any] = {"assistant": None, "router": None, "model": None, "vad": None, "session": None,
                           "protector": None, "plugin_manager": None, "request_end": None, "truncator": None,
                           "policy": None, "framework_shutdown": False, "cancelled": False}
    entry_task = asyncio.current_task()

    async def _build_job_parts() -> None:
        logger.info("✅ Connected to room: %s", ctx.room.name)
        # C-137: loads in its own thread while the session starts (C-124: the JARVIS room's job only); C-53: the job's
        # loop-lag sampler runs meanwhile and until the first write (a task of this job)
        _prewarm_vector_memory(getattr(ctx.room, "name", None), track)
        # C-72: speaker-ID model loads in its own thread (C-124: the JARVIS room's job only)
        _prewarm_voice_id(getattr(ctx.room, "name", None))
        # Now perform secondary initialization (the date is computed locally — no network call)
        current_dt_res = await get_formatted_datetime()
        # --- C-32: the prewarmed singletons of this executor (prewarm()). A missing key boots inline exactly as
        # before C-32 and logs PREWARM_MISS (dev console mode, tests, or a job that arrived before/without prewarm).
        userdata = _proc_userdata(ctx)
        from services.ai_core.jarvis_plugin_manager import JarvisPluginManager  # pylint: disable=import-outside-toplevel
        plugin_manager = JarvisPluginManager()
        external_tools = userdata.get("tools")
        if external_tools is None:
            logger.warning("PREWARM_MISS key=%s", "tools")
            plugin_manager.discover_plugins(os.path.join(config.project_root, "services"))
            if not local:
                from services.ai_core import desktop_proxy  # pylint: disable=import-outside-toplevel
                desktop_proxy.install(plugin_manager)  # C-119: laptop proxies, before the arg hooks
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
                min_speech_duration=0.2,  # Sir Matloob's PC - higher threshold reduces noise jitter
                min_silence_duration=1.0,
            )
        city = userdata.get("city")
        if not isinstance(city, str) or not city:
            logger.warning("PREWARM_MISS key=%s", "city")
            city = await get_current_city()
        job_settings = await _load_job_settings()  # C-54 (K-61): this agent's saved settings, else the defaults
        # One RealtimeModel per job (K-69 JARVIS_REALTIME_MODEL): the assistant (an Agent) holds it as its llm, and
        # every rebuilt AgentSession opens a fresh realtime session from it.
        llm_model = google.realtime.RealtimeModel(
            # C-54: the saved voice, else the saved persona's (C-50: JARVIS_VOICE_JARVIS, else Charon)
            voice=job_settings.voice or persona_voice("jarvis"),
            model=_realtime_model_name(),
            # C-114: Gemini's server-side sliding window (JARVIS_CTX_TRIGGER_TOKENS, default 24000 -> 16000 tokens)
            context_window_compression=session_policy.compression_config(),
        )
        # 2026-10-06: after a network blip the plugin replayed a stale session-resumption handle on every reconnect
        # (APIError/close 1008 'Requested entity was not found') until the session died. The guard drops the stale
        # handle so the plugin's own retry reconnects fresh.
        gemini_resume_guard.guard_realtime_model(llm_model)
        session_policy.install_truncate_warning_filter()  # C-114: the plugin's WARNING on every truncation
        user_id = _resolve_user_id(ctx.room)  # memory identity: JARVIS_USER_ID, else the participant identity
        assistant = BrainAssistant(
            chat_ctx=llm.ChatContext(),
            llm_instance=llm_model,
            tools=external_tools,
            current_date=current_dt_res.get("formatted"),
            current_city=city,
            user_id=user_id,
            probe=probe,
        )
        # C-23: the stop event comes with the assistant, before the UI listener can receive a pause_agent; the
        # runtime WATCHES it (never swaps it), so one that arrives before the runtime waits is never lost.
        stop_event = getattr(assistant, "_stop_event", None)
        if not isinstance(stop_event, asyncio.Event):
            stop_event = asyncio.Event()
            assistant._stop_event = stop_event  # pylint: disable=protected-access

        # Every end path goes through this (an assistant before C-23 has no request_end)
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

        # C-42: typed chat (UI and lk.chat) goes into this job's session through the router, and the router emits
        # every transcript message once (conversation_item_added).
        router = ConversationRouter(assistant, spawn=track)
        assistant.attach_router(router)
        # C-114: the assistant's chat truncator (a stand-in gets its own), its cuts tracked as this job's tasks
        truncator = getattr(assistant, "chat_truncator", None)
        if not isinstance(truncator, session_policy.ChatTruncator):
            truncator = session_policy.ChatTruncator(assistant)
        truncator.spawn = track
        if not probe:
            # C-114: the last turns from memory before the first session.start (once per job: a C-33 rebuild and a
            # retried start reuse these parts); K-57: a probe or canary job starts empty. C-52: the replies of the
            # other persona than the saved one this job starts as (C-54) stay out
            await session_policy.reseed_chat_ctx(assistant, persona=job_settings.get("persona"))
        # C-114: the job's date/city refresh and idle rules, built here so every session's handlers report their
        # conversation activity to it; its loop runs on the job's protector once the session runs (never in a probe)
        policy = session_policy.SessionPolicy(
            assistant,
            voice_link=voice_link,  # the idle close: its linked participant, and the people of its room (ctx.room)
            session=lambda: job["session"],
            request_end=_request_end,
            set_session_attr=lambda value: _set_session_attr(ctx, value),  # K-68 'idle' before the end
            now_formatted=get_formatted_datetime,
            watch_city=True,
            # local: the location loop feeds it (ipinfo); remote: this module's get_current_city_data is
            # jarvis_location's (remote_compat), the Desktop Node's location that agent-svc caches (K-72)
            read_location=None if local else get_current_city_data,
            # this job's background jobs and any running tool call keep it (ending it would cancel them)
            background_busy=functools.partial(session_policy.background_work, plugin_manager.running_tools),
            # console mode keeps its job (C-23), so no idle rules there. The PC brain's bridge ('always' dispatch)
            # refills an empty room at once: its idle close is off unless JARVIS_IDLE_CLOSE_MIN is set (the 24 h
            # recycle stays); the VPS brain's on_demand bridge dispatches only on demand: 30 minutes there.
            idle_close_min=0 if _is_console_job(ctx) else None,
            idle_close_default_min=0 if local else session_policy.IDLE_CLOSE_DEFAULT_MIN,
            sleep=asyncio.sleep,  # read now, not at import: tests swap asyncio.sleep
        )
        # Set to WARNING to see important SDK signals but ignore trivial ones
        logging.getLogger("livekit").setLevel(logging.WARNING)
        _quiet_livekit_agents_logger()
        job.update(assistant=assistant, router=router, model=llm_model, vad=vad, plugin_manager=plugin_manager,
                   request_end=_request_end, truncator=truncator, policy=policy)
        runtime.assistant, runtime.router = assistant, router
        # C-54 (K-61): the job's settings service (ui_command settings_update / settings_bootstrap / request_state and
        # the legacy commands reach it as runtime.settings); the saved wake word and persona and the audio-out policy
        # apply here, before session.start (instructions and the model's voice are stored for the session)
        runtime.settings = agent_settings.SettingsService(runtime, job_settings, probe=probe)
        await runtime.settings.apply_at_start()
        if not probe:
            # C-53: the recall of earlier turns (the re-seed is off since v43) goes into the instructions instead: the
            # newest saved messages as a RECENT MEMORY data block, read and built off the loop, composed with the
            # persona the job now runs as and stored for the first session.start (never into a running session, so it
            # never costs a Gemini reconnect); K-57: a probe or canary job starts without it
            await memory_recall.inject_memory_summary(assistant)

    async def _session_factory() -> Any:
        if job["assistant"] is None:
            await _build_job_parts()
        else:
            carry = getattr(job["assistant"], "carry_pending_voice", None)
            if callable(carry):  # C-50: a voice still pending from the closed session: the new one connects with it
                await carry()
        # Realtime model owns in-room voice. Do not attach Cloud TTS for chat (AI Studio keys are not Cloud TTS
        # credentials; chat uses text notify).
        return AgentSession(
            preemptive_generation=True,
            allow_interruptions=True,
            vad=job["vad"],
            stt=None,
            llm=job["model"],
            tts=None,
        )

    async def _start_session(session: Any) -> None:
        logger.info("[AGENT] Connecting room and starting session...")
        voice_link.prepare(session)  # C-41: with nobody listening the session starts with its audio output off
        if getattr(job["assistant"], "muted", False):
            # C-51 (K-60): a session (re)built while muted starts with its microphone input off. AgentInput keeps the
            # switch until RoomIO sets its input stream inside start(), so that stream starts detached and buffers no
            # frame. Switched off only after start() (attach_session, kept as a backstop), the frames buffered while
            # start() ran would still reach the model.
            session.input.set_audio_enabled(False)
            logger.info("MUTE: the new session starts with its microphone input off")
        # record=False: these jobs never record (self-hosted LiveKit), and a rebuilt session of the same job must not
        # trip livekit-agents' 'only one primary AgentSession' check, which only applies while recording.
        await session.start(room=ctx.room, agent=job["assistant"], record=False,
                            room_options=session_room_options(job["router"].text_input_cb))
        logger.info("[AGENT] Session started successfully.")

    def _on_session_started(session: Any, rebuilt: bool) -> None:
        """After EVERY successful session.start (first start and each in-place rebuild): this session's handlers and
        the per-session hand-overs; room handlers are NOT touched here (they are registered once per job)."""
        assistant, router = job["assistant"], job["router"]
        job["session"] = session
        router.attach(session)
        if not probe:  # a probe room holds only the probe (C-40): its lk.chat must reach the session
            harden_room_input(session, ctx.room)  # C-124: owner-only RPC and text (VPS; PC opt-in); idempotent
        assistant.attach_session(session)
        announce = getattr(assistant, "announce_persona", None)
        if not rebuilt and callable(announce):
            announce()  # C-50: a new job tells the dashboard its persona (the bridge keeps the last job's)
        _enable_voice_confirm_check()  # C-75: confirm_action needs Sir's own spoken yes from now on
        jarvis_announcer.attach(session)  # C-55 (K-18): results, jobs and tips are shown and spoken here
        jarvis_announcer.set_assistant(assistant)
        if not local:  # C-119: the process's Desktop Link, the stop phrases and the lost-output watch (VPS brain)
            from services.ai_core import desktop_proxy  # pylint: disable=import-outside-toplevel
            desktop_proxy.attach_session(session, assistant, room=_job_room_name(ctx), job_id=desktop_job)

        # --- Transcript messages (K-58) ---
        # C-42: conversation_item_added is the single source of chat messages (voice user text with the item id,
        # typed text never twice, replies with turn_id); a final transcription is only logged.
        @session.on("user_input_transcribed")
        def on_user_transcription(event: agents.voice.UserInputTranscribedEvent, _assistant=assistant,
                                  _policy: session_policy.SessionPolicy = job["policy"]):
            _policy.note_activity()  # C-114: the user speaks
            if not event.transcript or _assistant.muted:
                return
            if event.is_final:
                logger.info("🎤 User Transcription (Final, %d chars): %s", len(event.transcript),
                            preview(event.transcript))
                logger.debug("🎤 User Transcription (Final): %s", event.transcript)
                _note_final_transcript(event, _assistant)  # C-75: spoken confirmations need this
            else:
                # Optional: Just log locally for debugging, don't flood the UI
                logger.debug("🎤 Partial Input: %s", event.transcript)

        @session.on("conversation_item_added")
        def on_conversation_item_added(event: agents.voice.ConversationItemAddedEvent,
                                       _router: ConversationRouter = router,
                                       _truncator: session_policy.ChatTruncator = job["truncator"],
                                       _policy: session_policy.SessionPolicy = job["policy"]):
            _router.on_conversation_item_added(event)
            _truncator.note_item()  # C-114: every 20 items the chat context is cut to its last 40
            _policy.note_activity()  # C-114: a voice or typed message or a reply restarts both idle counts

        @session.on("agent_state_changed")
        def on_agent_state_changed(event: agents.voice.AgentStateChangedEvent, _assistant=assistant,
                                   _policy: session_policy.SessionPolicy = job["policy"]):
            state = event.new_state
            logger.info("🤖 Agent State: %s", state)
            _policy.note_activity()  # C-114: the agent listens again, thinks or speaks
            on_state = getattr(_assistant, "on_agent_state", None)
            if callable(on_state):  # C-49: pending instructions reach the session once the agent listens
                track(on_state(state))
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

        # C-35 (K-56): the job's VoiceLink: room handlers once per job, then one relink pass now (people already in
        # the room; a rebuilt RoomIO starts unlinked), ROOMIO_RELINK + jarvis.linked_identity on every switch
        voice_link.attach(session)
        if rebuilt:
            logger.info("RUNTIME session rebuilt: handlers and hand-overs re-attached")

    async def _end_runtime_job(reason: str) -> None:
        """The runtime's end_job hook: the job's own leftovers, then the C-23 end block (skipped on cancel)."""
        if not local:  # C-119: the Desktop Link never reconnects from this job's process again (also on cancel)
            _retire_desktop_link(desktop_job)
        if not probe:  # C-52: the last messages, before the next job can start
            await _flush_memory(job["assistant"], reason)
        settings = getattr(runtime, "settings", None)
        if settings is not None:  # C-54: a voice, persona or wake word a tool changed survives the restart
            await settings.persist_live_state()
        router = job["router"]
        if router is not None:
            router.detach()  # typed chat gets the offline answer until the next job attaches
        if job["session"] is not None:
            jarvis_announcer.detach(job["session"])  # C-55: only this job's session
        await _cleanup_session_resources(None, list(active_tasks), protector=job["protector"])
        if job["cancelled"]:
            logger.info("JOB END (%s): cancelled, not shut down again", reason)
            return
        await _end_job(ctx, reason, framework_shutdown=job["framework_shutdown"])

    runtime = JarvisRuntime(
        ctx,
        session_factory=_session_factory,
        start_session=_start_session,
        on_session_started=_on_session_started,
        # a failed attempt or a replaced session, with what livekit-agents leaves behind for it (C-33 review)
        close_session=lambda session: _release_session(session, job["assistant"], ctx.room),
        set_session_attr=lambda value: _set_session_attr(ctx, value),  # K-68 v9 (best effort, never blocking)
        end_job=_end_runtime_job,
        sleep=asyncio.sleep,  # read now, not at import: tests swap asyncio.sleep
    )
    # C-35: owner-only identities where room input is owner-only (the VPS brain; the PC with JARVIS_RPC_OWNER_ONLY)
    # C-40: a probe job serves its own 'probe-*' participant (its room holds nobody else)
    voice_link = VoiceLink(ctx, strict_owner=rpc_owner_only() and not probe, allow_probe=probe)
    runtime.voice_link = voice_link  # the teardown closes it; voice_take_over reaches it through the listener

    # C-33: livekit-agents never awaits or cancels this entrypoint when IT ends the job (room 'disconnected', a worker
    # shutdown): it runs the shutdown callbacks, cancels its own tasks and stops the loop, which would cut our
    # teardown off. This callback asks the job to end and holds the loop (bounded) until it has.
    async def _hold_for_teardown(reason: str) -> None:
        job["framework_shutdown"] = True
        if not local:  # C-119: at once, as the teardown may outlast LiveKit's wait (the process then drains)
            _retire_desktop_link(desktop_job)
        why = END_ROOM_DISCONNECTED if "disconnect" in str(reason).lower() else END_JOB_SHUTDOWN
        # C-52: the memory flush first, in case LiveKit stops the loop before our teardown is done. This callback runs
        # outside the job task's context (the K-57 probe flag is not set here), hence the explicit check.
        if not probe:
            await _flush_memory(job["assistant"], why)
        request_end = job["request_end"]
        if callable(request_end):
            request_end(why)
        runtime.request_close(why)  # also before the assistant exists
        if entry_task is not None and entry_task is not asyncio.current_task() and not entry_task.done():
            await asyncio.wait({entry_task}, timeout=FRAMEWORK_TEARDOWN_WAIT_S)
        if entry_task is None or not entry_task.done():  # the teardown did not finish in time: the cheap parts
            release_job_protector(job["protector"])
            await _close_loop_bridge_client()

    try:
        ctx.add_shutdown_callback(_hold_for_teardown)
    except Exception as e:  # pylint: disable=broad-exception-caught  # a stand-in context without callbacks
        logger.debug("no shutdown callback: %s", type(e).__name__)

    try:
        logger.info("Attempting to start session (Timeout: 60s)...")
        if await runtime.start():
            assistant = job["assistant"]

            def on_data_received(data: rtc.DataPacket):  # pylint: disable=useless-return
                # Suppress ignoring byte stream warnings for internal topics
                if data.topic in ["lk.agent.session", "lk.agent.monitor", "lk.agent.chat"]:
                    return  # pylint: disable=useless-return
            runtime.room_on("data_received", on_data_received)  # removed by the runtime's teardown

            # --- Speaker Identification Subscription --- (never in a probe: it can enroll voice samples)
            # C-51 (K-60): one tap per audio track; mute pauses the taps and unmute restarts them (set_muted)
            audio_taps = UserAudioTaps(assistant, ctx.room, track)
            if not probe and callable(getattr(assistant, "attach_audio_tap", None)):
                assistant.attach_audio_tap(audio_taps)

            def on_track_published(publication: rtc.TrackPublication, participant: rtc.RemoteParticipant,
                                   _taps=audio_taps):
                if publication.kind == rtc.TrackKind.KIND_AUDIO and not probe:
                    _taps.add(publication)
            runtime.room_on("track_published", on_track_published)

            # The owner coming back under a new identity is relinked by the job's VoiceLink (C-35, attached above)
            track(_room_liveness_loop(ctx.room, job["request_end"]))  # C-33 step A: a dead room ends the job

            # Subscribe to existing tracks
            if not probe:
                audio_taps.add_room_tracks()

            # --- Location and PC vitals loops --- (local mode only, K-72 / K-40; never in a probe, K-57)
            if local and not probe:
                track(_location_loop(notify_location, job["policy"].note_location))  # C-114: the city check too
                track(_pc_vitals_loop(notify_pc_vitals))

            _start_loop_watchdog(ctx, track, job["plugin_manager"].running_tools)
            if not probe:  # K-57: a probe runs no listener and no background loop
                _print_startup_banner()
                protector = job_protector()
                job["protector"] = runtime.protector = protector
                # C-34: one bridge listener per job; the runtime's teardown acloses it (no reconnect afterwards)
                listener = UIBridgeListener(assistant, runtime=runtime)
                runtime.listener = listener
                bg_tasks = await _start_background_tasks(job["session"], assistant, protector=protector,
                                                         listener=listener)
                for bt in bg_tasks:
                    active_tasks.add(bt)
                    bt.add_done_callback(active_tasks.discard)
                await protector.run_protected("session_policy", job["policy"].run)  # C-114: this job's protector only

                # C-78: the document index warms after a delay, so its walk never overlaps the start-up walks
                rag_warm_delay = _rag_warm_delay()
                if local and rag_warm_delay is not None:
                    track(_warm_document_index(rag_warm_delay))

            # C-23 (K-60): wait until the job is asked to end (pause_agent, a shutdown_agent the owner confirmed (C-51),
            # a lost room, the session error cap); 'stop' only interrupts speech and never sets this.
            ended = await runtime.wait_closed()
            logger.info("SESSION ending: %s", ended)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("🛑 Shutdown signal received.")
        job["cancelled"] = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("⚠️ Job setup failed [%s]: %s", type(exc).__name__, exc)
        logger.error(traceback.format_exc())
        runtime.request_close(END_SESSION_FAILED)
    finally:
        await runtime.aclose(runtime.reason)
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
