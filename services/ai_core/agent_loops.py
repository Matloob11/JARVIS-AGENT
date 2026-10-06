"""
# agent_loops.py
Background loops for reminders, bug hunting, and UI communication.
"""

import asyncio
import functools
import inspect
import os
import re
import socket
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit, urlunsplit

import socketio

from services.ai_core.swarm_manager import swarm_coordinator
from services.automation.jarvis_reminders import check_due_reminders
from services.ai_core.jarvis_vector_memory import jarvis_vector_db
from services.info.jarvis_sim_lookup import open_sim_lookup_panel
from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_autonomous import autonomous_protector
from services.utils.jarvis_bridge import (
    agent_link_v2,
    notify_event,
    notify_sim_loading,
    notify_transcription,
    wake_retry_queue,
)
from services.utils.jarvis_bug_hunter import monitor_logs
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard

if TYPE_CHECKING:
    from livekit.agents import AgentSession

    from src.core.agent_core import BrainAssistant

# Setup logger
logger = setup_logger("JARVIS-LOOPS")
logger.info("AGENT_LOOPS_MODULE_V2 loaded from %s", __file__)

# Module-level state for adaptation loop
_throttled_tasks: set[str] = set()
# 2026-10-06 (dashboard test): a single CPU spike (the first vision call plus a model load) fired the
# CRITICAL 'SYSTEM OVERLOAD' throttle at once and cancelled services that were not the problem. The
# overload must now hold for OVERLOAD_CONSECUTIVE_SAMPLES consecutive readings before anything is
# throttled; a single spike logs one WARNING only. Recovery behaviour is unchanged.
OVERLOAD_CONSECUTIVE_SAMPLES = 2
_overload_streak = 0  # consecutive CRITICAL/STRESSED readings so far

async def start_adaptation_loop() -> None:
    """
    Autonomous Load Adaptation: Throttles non-essential services during sustained stress
    (OVERLOAD_CONSECUTIVE_SAMPLES consecutive high readings; a single spike only warns).
    Self-Monitoring + Self-Healing synergy.
    """
    global _overload_streak  # pylint: disable=global-statement
    # Track which tasks were throttled to allow self-healing recovery later
    if not hasattr(start_adaptation_loop, "_throttled_tasks"):
        start_adaptation_loop._throttled_tasks = set()

    while True:
        try:
            stress = adaptive_engine.analyze_system_load()

            # In a container the engine only observes its cgroup (K-72): nothing is cancelled for load.
            if stress in ["CRITICAL", "STRESSED"] and not adaptive_engine.observe_only:
                _overload_streak += 1
                if _overload_streak < OVERLOAD_CONSECUTIVE_SAMPLES:
                    # A single spike passes with one WARNING; only sustained load throttles.
                    logger.warning("⚠️ High system load (%s), reading %d/%d: waiting for a consecutive "
                                   "reading before throttling.", stress, _overload_streak,
                                   OVERLOAD_CONSECUTIVE_SAMPLES)
                else:
                    # Throttling non-essential high-load tasks (sustained overload only)
                    logger.critical("🚨 SYSTEM OVERLOAD: Throttling high-load services...")
                    for task_name in ["reminder_loop", "bug_hunter_loop", "memory_loop"]:
                        task = autonomous_protector.get_task(task_name)
                        if task and not task.done():
                            logger.warning("📉 Suspending non-essential task: %s", task_name)
                            task.cancel()
                            _throttled_tasks.add(task_name)

            elif stress == "NORMAL":
                _overload_streak = 0
                if _throttled_tasks:
                    # Self-Healing: Bring back suspended tasks once resource pressure is gone
                    logger.info("🌤️ SYSTEM RECOVERED: Restoring previously throttled services...")
                    for task_name in list(_throttled_tasks):
                        logger.info("🚀 Self-Healing: Restarting %s", task_name)
                        if autonomous_protector.restart_task(task_name):
                            _throttled_tasks.remove(task_name)

                    # Predictive Optimization: Pre-load heavy AI models during idle time
                    if jarvis_vector_db.client is None:
                        logger.info("🧠 Idle time detected: Pre-loading Vector Memory models...")
                        await jarvis_vector_db._ensure_initialized()

            # Re-evaluation interval (Dynamic based on load)
            sleep_time = 30 if stress != "NORMAL" else 90
            await asyncio.sleep(sleep_time)
        except Exception as e:
            logger.error("Adaptation loop error: %s", e)
            await asyncio.sleep(30)


async def start_memory_storage_loop(assistant: "BrainAssistant") -> None:
    """Periodically saves memory to disk."""
    while True:
        try:
            await asyncio.sleep(600)  # Every 10 minutes
            await assistant.memory_extractor.memory.save_to_disk()
            logger.info("💾 Auto-saved memory snapshot.")
        except Exception as e:
            logger.error("🛑 Memory storage loop failure: %s", e)
            await asyncio.sleep(30) # Wait before retry


async def start_reminder_loop(session: "AgentSession") -> None:
    """Check for due reminders and notify UI as text (no TTS / session.say)."""
    while True:
        # check_due_reminders is blocking (file IO), run in thread
        due = await asyncio.to_thread(check_due_reminders)
        for item in due:
            logger.info("🔔 Triggering proactive reminder: %s", item['message'])
            await notify_transcription(
                "assistant",
                f"Reminder: {item['message']}",
                source="system",
            )
        await asyncio.sleep(30)


async def start_bug_hunter_loop(session: "AgentSession") -> None:
    """Monitor error logs and notify the user about issues (text only)."""
    while True:
        try:
            async def on_error_detected(error_block: str) -> None:
                logger.warning("🚨 AI Bug Hunter detected a system error.")
                # C-124 (C-64 review SP-4): log excerpts carry attacker-influenced text (exception messages,
                # URLs, API error bodies) and this message persists in the chat history the model reads on
                # later turns, so it is framed as untrusted data, never as instructions.
                await notify_transcription(
                    "assistant",
                    f"System error detected: [LOG DATA, untrusted] {error_block[:400]} [END LOG DATA]",
                    source="system",
                )

            await monitor_logs(on_error_detected)
        except Exception as e:
            logger.error("🛑 Bug hunter loop failure: %s", e)
            await asyncio.sleep(60) # Longer wait for log system recovery


# K-38: the client's own reconnection is off; UIBridgeListener.start() waits 1, 2, 4, 8, 10, 10 ... s.
RECONNECT_FIRST_S = 1.0
RECONNECT_MAX_S = 10.0
_STABLE_CONNECTION_S = 30.0  # a connection that lasted this long starts the next backoff at 1 s again
_CONNECT_WAIT_S = 10  # how long connect() waits for the bridge to accept the namespace
_RETRY_LOG_EVERY_S = 60.0
# K-38 v4: the bridge takes the agent role only on its internal listener (jarvis-network, never published).
INTERNAL_LISTENER_PORT = 5002


def bridge_socket_target(url: str) -> tuple[str, str]:
    """(connect URL, socketio_path) for UI_BRIDGE_URL: a URL ending in /vortex (the public nginx prefix)
    gives 'vortex/socket.io', any other URL 'socket.io'. The connect URL keeps scheme, host and query."""
    parts = urlsplit(url.strip())
    path = parts.path.strip("/")
    base = urlunsplit((parts.scheme, parts.netloc, "", parts.query, ""))
    if path == "vortex" or path.endswith("/vortex"):
        return base, f"{path}/socket.io"
    return base, "socket.io"


def is_internal_listener(url: str) -> bool:
    """True for the bridge's internal :5002 listener (the VPS brain, e.g. http://jarvis-bridge:5002)."""
    try:
        return urlsplit(url.strip()).port == INTERNAL_LISTENER_PORT
    except ValueError:
        return False


def _job_identity() -> tuple[str, str]:
    """(room, job_id) of the LiveKit job running this listener; outside a job JARVIS_ROOM_NAME and ''."""
    default_room = os.getenv("JARVIS_ROOM_NAME") or "jarvis-hybrid"
    try:
        from livekit.agents import get_job_context  # pylint: disable=import-outside-toplevel
        job = get_job_context().job
        return job.room.name or default_room, job.id
    except Exception:  # pylint: disable=broad-exception-caught
        return default_room, ""


@functools.lru_cache(maxsize=1)
def _agent_identity() -> tuple[str, str]:
    """(agent_id, version) this worker announces with its agent_token (K-38): the host name and the git
    commit it started from (GIT_SHA, else the checkout's HEAD), read once per process."""
    from src.core.agent_lock import _git_sha  # pylint: disable=import-outside-toplevel
    version = (os.getenv("GIT_SHA") or "").strip() or _git_sha()
    return (socket.gethostname() or "unknown")[:64], (version or "unknown")[:12]


_CHAT_ID_MAX = 128
# K-38/K-60 v9 (C-23): the bridge's own pause_agent says payload.by 'bridge' (src/core/bridge_rooms.PAUSE_COMMAND).
PAUSE_HELD_BY = "bridge"


def bridge_holds_pause(payload: Any) -> bool:
    """True for the bridge's own pause_agent: its room owner holds the pause (no dispatch until Wake). A pause_agent
    without it (the UI's command forwarded by a bridge before C-23) never ends the job."""
    return isinstance(payload, dict) and payload.get("by") == PAUSE_HELD_BY


def sim_lookup_input(payload: Any) -> tuple[str, str] | None:
    """
    (query, purpose) of a ui_command sim_lookup (C-79, K-34): the dialog's {query, purpose} or the
    legacy plain string (purpose ''). None when there is no usable query. The values are returned as
    sent; lookup_sim_data itself normalizes and masks them.
    """
    if isinstance(payload, dict):
        query, purpose = payload.get("query"), payload.get("purpose")
    else:
        query, purpose = payload, ""
    if not isinstance(query, str) or not query.strip():
        return None
    return query.strip(), (purpose.strip() if isinstance(purpose, str) else "")


def _mask_sim_query(query: str) -> str:
    """K-34 masking (the same rule as services/info/jarvis_sim_lookup): logs and the refusal event
    carry only the masked query, never the raw number or CNIC."""
    digits = "".join(ch for ch in str(query or "") if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:3]}{'*' * max(len(digits) - 6, 3)}{digits[-3:]}"


# A run of 7+ digits (separators allowed) inside a message bound for the UI: a phone number or
# CNIC that slipped into someone else's text (K-34 last line of defense).
_SIM_DIGIT_RUN = re.compile(r"\d(?:[\s\-()./]*\d){6,}")

# C-79: fixed Roman-Urdu answers for the SIM dialog; never the broker's or a provider's own text.
_SIM_REFUSAL_MESSAGES = {
    "denied": "Lookup allow nahi hua, is liye nahi chala.",
    "expired": "Allow ka waqt khatam ho gaya, lookup nahi chala. Dobara try karein.",
    "cancelled": "Lookup ki confirmation band ho gayi, lookup nahi chala. Dobara try karein.",
}
_SIM_NO_WRAPPER_MESSAGE = ("SIM lookup tool register nahi hai, is liye lookup nahi chala. "
                           "Agent restart kar ke dobara try karein.")
_SIM_FAILED_MESSAGE = "SIM lookup fail ho gayi. Dobara try karein."


def _mask_sim_message(text: Any) -> str:
    """K-34: every 7+ digit run in a UI-bound sim message is masked (first 3 + stars + last 3),
    whoever wrote the message."""
    return _SIM_DIGIT_RUN.sub(lambda match: _mask_sim_query(match.group()), str(text or ""))


async def _emit_sim_refusal(masked: str, status: str, message: str) -> None:
    """One sim_data_result refusal in the exact shape the dialog reads (C-79, K-34): records
    empty, only the MASKED query, a fixed or digit-masked message, 'timestamp' like
    jarvis_sim_lookup._emit_sim_data."""
    await notify_event("sim_data_result", {
        "records": [], "phone": masked, "status": status, "message": message,
        "timestamp": time.time()})


def _sim_lookup_tool() -> Callable[..., Any] | None:
    """The plugin-manager wrapper of lookup_sim_data (K-11): the SAME guarded path a voice/LLM call
    takes, so the K-45 HIGH ConfirmFlow gate applies to the dashboard search too (C-79). None when no
    wrapper is registered (a stripped test build)."""
    from services.ai_core.jarvis_plugin_manager import plugin_manager  # pylint: disable=import-outside-toplevel
    for tool in plugin_manager.get_tools():
        if getattr(tool, "__name__", "") == "lookup_sim_data":
            return tool
    return None


def chat_input(payload: Any) -> tuple[str | None, str] | None:
    """
    (id, text) of a ui_command 'chat' (K-41, K-59): the UI's {id|client_msg_id, text} keeps its id; a legacy
    plain string, or a dict without a usable id (a str of 1-128 printable characters), gives None for the
    id. None when there is no text to answer.
    """
    if isinstance(payload, dict):
        text, msg_id = payload.get("text"), payload.get("id") or payload.get("client_msg_id")
        if not (isinstance(msg_id, str) and 0 < len(msg_id) <= _CHAT_ID_MAX and msg_id.isprintable()):
            msg_id = None
    else:
        text, msg_id = payload, None
    if not isinstance(text, str) or not text.strip():
        return None
    return msg_id, text


class UIBridgeListener:
    """Manages Socket.IO connection and command handling for the UI Bridge."""
    def __init__(self, assistant: "BrainAssistant") -> None:
        self.assistant = assistant
        self.sio = socketio.AsyncClient(reconnection=False)
        self._is_reconnecting = False
        self._auth_kind = "-"  # what the last connect authenticated with, for the log

        self.sio.on('agent_command', self.on_command)
        self.sio.on('agent_vision_frame', self.on_vision_frame)
        self.sio.on('connect', self.on_connect)
        self.sio.on('disconnect', self.on_disconnect)

    async def on_command(self, data: Any) -> None:
        if isinstance(data, dict):
            await self._handle_command(data)

    async def _handle_command(self, data: dict[str, Any]) -> None:
        cmd_type: Any = data.get("type")
        payload: Any = data.get("payload")

        try:
            if cmd_type == "mute":
                self.assistant.set_muted(True)
                logger.info("🔇 Agent MUTED via UI.")
            elif cmd_type == "unmute":
                self.assistant.set_muted(False)
                logger.info("🔊 Agent UNMUTED via UI.")
            elif cmd_type == "wake_word_toggle":
                new_mode = payload if isinstance(payload, bool) else (not self.assistant.wake_word_mode)
                self.assistant.set_wake_word_mode(new_mode)
                logger.info("🎤 Wake word mode set to %s via UI.", new_mode)
            elif cmd_type == "stop":
                # K-60 v9 (C-23): 'Stop speaking' only interrupts speech; the session keeps listening. It no longer
                # sets the job's stop event, which restarted the whole session in place.
                await self._interrupt_speech()
                logger.info("STOP: speech interrupted (K-60)")
            elif cmd_type == "pause_agent":
                if not bridge_holds_pause(payload):
                    # K-60 v9: only a pause the bridge holds (its room owner keeps the agent out and brings it back
                    # on Wake) ends the job; an older bridge forwards the UI's own command, which changes nothing.
                    logger.info("PAUSE: pause_agent without the bridge's hold ignored (K-60 v9)")
                    return
                # K-60 v9 (C-23): the owner paused JARVIS; this job ends with ctx.shutdown('paused_by_owner') and the
                # bridge dispatches nothing until Wake (ui_command start).
                request_end = getattr(self.assistant, "request_end", None)
                if callable(request_end):
                    request_end("paused_by_owner")
                elif isinstance(getattr(self.assistant, "_stop_event", None), asyncio.Event):
                    self.assistant._stop_event.set()  # type: ignore[attr-defined]  # an assistant before C-23
                logger.warning("PAUSE: the owner paused the agent; this job ends (paused_by_owner)")
            elif cmd_type == "persona_change":
                logger.info("🎭 Switching persona to: %s", payload)
                await self.assistant.tool_toggle_gf_mode(payload == "anna")
            elif cmd_type == "clear_memory":
                logger.info("🧠 Clearing short-term memory...")
                await self.assistant.memory_extractor.memory.clear()
            elif cmd_type == "open_sim_panel":
                logger.info("Opening SIM lookup panel via UI command.")
                await open_sim_lookup_panel()
            elif cmd_type == "sim_lookup":
                parsed = sim_lookup_input(payload)
                if parsed is None:
                    logger.debug("sim_lookup without a usable query ignored (%s)",
                                 type(payload).__name__)
                else:
                    # C-79: its own task, because the K-45 gate waits for the owner's Allow and this
                    # handler must stay free to receive that confirm_response.
                    query, purpose = parsed
                    sim_task = asyncio.create_task(self._run_sim_lookup(query, purpose))
                    self.assistant._spawned_tasks.add(sim_task) # type: ignore[attr-defined]
                    sim_task.add_done_callback(self.assistant._spawned_tasks.discard) # type: ignore[attr-defined]
            elif cmd_type == "chat":
                chat = chat_input(payload)
                if chat is None:
                    logger.debug("Chat command without text ignored (%s)", type(payload).__name__)
                else:
                    msg_id, text = chat
                    # The text itself is logged by the router (a 60-character preview at INFO, K-59).
                    logger.info("💬 Chat message received (id %s, %d chars)", msg_id or "new", len(text))
                    new_task = asyncio.create_task(self._task_wrapper(text, msg_id))
                    self.assistant._spawned_tasks.add(new_task) # type: ignore[attr-defined]
                    new_task.add_done_callback(self.assistant._spawned_tasks.discard) # type: ignore[attr-defined]
            elif cmd_type == "confirm_response":
                # C-29 (K-14): the owner's Allow/Deny from the dashboard. The bridge already checked that it came from
                # the owner's UI for a pending, unexpired request; the broker decides the action once.
                if isinstance(payload, dict) and isinstance(payload.get("id"), str) \
                        and isinstance(payload.get("allow"), bool):
                    from services.ai_core.jarvis_confirm import (  # pylint: disable=import-outside-toplevel
                        confirmation_broker,
                    )
                    await confirmation_broker.resolve(payload["id"], payload["allow"], source="ui")
                else:
                    logger.warning("confirm_response ignored: payload needs {id, allow: bool}")
            elif cmd_type == "swarm":
                if payload:
                    logger.info("🌀 Swarm Mode triggered with: %s", payload)
                    swarm_task = asyncio.create_task(swarm_coordinator.execute_swarm(payload))
                    self.assistant._spawned_tasks.add(swarm_task) # type: ignore[attr-defined]
                    swarm_task.add_done_callback(self.assistant._spawned_tasks.discard) # type: ignore[attr-defined]
        except Exception as e:
            logger.error("❌ Error processing UI command %s: %s", cmd_type, e, exc_info=True)

    async def _run_sim_lookup(self, query: str, purpose: str) -> None:
        """
        C-79: the dashboard's SIM search runs through the SAME guarded lookup path voice uses — the
        plugin-manager wrapper of lookup_sim_data (K-11) — so the K-45 HIGH ConfirmFlow gate raises
        the Allow dialog with a configured source instead of being bypassed; without a registered
        wrapper nothing runs (fail closed, never the raw function). Only the masked query is ever
        logged (K-34); the purpose text never is. A needs_confirmation answer is never relayed to
        the dialog (its message could carry argument text): the owner's decision is awaited
        (wait_outcome, K-45 v11) — an Allow re-runs the tool body, which emits its own
        sim_data_loading / sim_data_result, while a Deny or expiry is answered here as a masked
        sim_data_result with a fixed message, so the dialog always gets a real answer.
        """
        masked = _mask_sim_query(query)
        logger.info("SIM lookup workflow requested from UI for %s (purpose %s)",
                    masked, "set" if purpose else "none")
        await notify_sim_loading({"query_masked": masked})
        tool = _sim_lookup_tool()
        if tool is None:
            # C79-R4: no raw-tool fallback — the K-45 gate must not silently vanish.
            logger.warning("lookup_sim_data wrapper not registered; the dashboard lookup is "
                           "refused (the K-45 gate never falls open)")
            await _emit_sim_refusal(masked, "error", _SIM_NO_WRAPPER_MESSAGE)
            return
        try:
            result: Any = await tool(query, purpose=purpose)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Only the exception type: its text could carry the raw number (K-34).
            logger.error("SIM lookup for %s failed: %s", masked, type(e).__name__)
            await _emit_sim_refusal(masked, "error", _SIM_FAILED_MESSAGE)
            return
        if isinstance(result, dict) and result.get("status") == "needs_confirmation":
            # C79-R1/R2: the broker answers needs_confirmation AT ONCE (the Allow dialog is now
            # on screen); its message is never relayed. Wait for the owner's real decision.
            await self._answer_sim_confirmation(masked, result)
            return
        if isinstance(result, str):
            result = {"status": "error", "message": result[:300]}
        if isinstance(result, dict) and "records" not in result:
            # The tool body never ran (no UI / broker error / blocked before execution): answer
            # the dialog with the shape it reads, masked per K-34 — digit runs in the carried
            # message masked too, never a raw number.
            await _emit_sim_refusal(masked, str(result.get("status") or "error"),
                                    _mask_sim_message(result.get("message")))

    async def _answer_sim_confirmation(self, masked: str, asked: dict[str, Any]) -> None:
        """
        C-79 (K-45 v11): holds this spawned task on confirmation_broker.wait_outcome(confirm_id)
        until the owner decides (Allow/Deny on the dashboard, a spoken answer, or expiry). An
        approval needs nothing here — the broker re-runs the tool through the wrapper and the tool
        body emits its own events. Everything else is answered to the dialog as a masked
        sim_data_result with a fixed message (status 'denied' | 'expired' | 'cancelled').
        """
        # Imported at call time: jarvis_confirm imports the plugin manager at module load.
        from services.ai_core.jarvis_confirm import confirmation_broker  # pylint: disable=import-outside-toplevel
        confirm_id = str(asked.get("confirm_id") or "")
        outcome = await confirmation_broker.wait_outcome(confirm_id) if confirm_id else None
        if isinstance(outcome, dict) and outcome.get("status") == "started":
            return  # approved: the re-run emits sim_data_loading / sim_data_result itself
        reason = str(outcome.get("reason") or "") if isinstance(outcome, dict) else ""
        status = {"denied": "denied", "voice": "denied", "expired": "expired"}.get(reason,
                                                                                   "cancelled")
        logger.info("SIM lookup for %s not approved (%s)", masked, reason or "no outcome")
        await _emit_sim_refusal(masked, status, _SIM_REFUSAL_MESSAGES.get(
            status, _SIM_REFUSAL_MESSAGES["cancelled"]))

    async def _interrupt_speech(self) -> bool:
        """'stop': BrainAssistant.interrupt_speech (AgentSession.interrupt, waited at most 3 s); an assistant without
        it (an old integration, a test double) is left alone."""
        interrupt = getattr(self.assistant, "interrupt_speech", None)
        if not callable(interrupt):
            return False
        result = interrupt()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def _task_wrapper(self, payload: str, msg_id: str | None = None) -> None:
        """
        C-42 (K-59): a thin call into the job's ConversationRouter, which emits the user message once with
        the UI's id and sends the text into the live session (the reply comes from conversation_item_added).
        An assistant without a router (an old integration, tests) keeps the text-only template path.
        """
        from src.core.conversation_router import (  # pylint: disable=import-outside-toplevel
            ORIGIN_UI,
            router_of,
        )
        router = router_of(self.assistant)
        if router is not None:
            await router.handle_text(payload, msg_id, origin=ORIGIN_UI)
            return
        await self._template_reply(payload, msg_id)

    async def _template_reply(self, payload: str, msg_id: str | None = None) -> None:
        """
        The pre-C-42 text path, only for an assistant without a router: never calls session.say() (no TTS).
        C-MSG-1: the user's message is echoed with the UI's id (msg_id; a new id only for a legacy string)
        and source 'text', so the bridge and the UI keep one bubble; the reply follows with source 'text'
        and turn_id = that id. An empty answer sends no reply bubble.
        """
        response_text = ""
        user_msg_id = msg_id or str(uuid.uuid4())
        try:
            await notify_transcription("user", payload, msg_id=user_msg_id, source="text")
            logger.info("CHAT_TEXT_PATH_V2 user notified")
            safe_message = vortex_guard.sanitize_input(payload)
            try:
                response = await self.assistant.handle_user_query(safe_message)
                response_text = str(response or "").strip()
                logger.info("CHAT_TEXT_PATH_V2 query ok (%d chars)", len(response_text))
            except Exception as query_err:  # pylint: disable=broad-exception-caught
                logger.error(
                    "CHAT_TEXT_PATH_V2 query failed: %s",
                    query_err,
                    exc_info=True,
                )
                response_text = (
                    "Sir, mujhe jawab generate karte waqt issue aaya. Dobara try karein."
                )
        except Exception as e:
            logger.error("Task execution failure (pre-reply): %s", e, exc_info=True)
            response_text = (
                "Sir, mujhe jawab generate karte waqt issue aaya. Dobara try karein."
            )

        if not response_text:
            return
        try:
            assistant_msg_id = str(uuid.uuid4())
            await notify_transcription("assistant", response_text, msg_id=assistant_msg_id,
                                       source="text", turn_id=user_msg_id)
            logger.info("💬 Text reply sent to UI (%d chars)", len(response_text))
        except Exception as notify_err:  # pylint: disable=broad-exception-caught
            logger.error("CHAT_TEXT_PATH_V2 assistant notify failed: %s", notify_err, exc_info=True)

    async def on_vision_frame(self, data: Any) -> None:
        frame = data.get("frame") if isinstance(data, dict) else data
        command = data.get("command") if isinstance(data, dict) else None
        # C-48: the UI's capture time (K-05 'vision_frame' {frame, ts epoch ms}); a legacy frame has none.
        ts = data.get("ts") if isinstance(data, dict) else None

        from services.ai_core.jarvis_vision import (  # pylint: disable=import-outside-toplevel
            frame_timestamp,
            vision_system,
        )
        frame_ts = frame_timestamp(ts) if frame else None

        if hasattr(self.assistant, 'vision_handler'):
            handler = self.assistant.vision_handler # type: ignore[attr-defined]
            handler.last_vision_frame = frame
            handler.last_vision_frame_ts = frame_ts
        else:
            self.assistant.last_vision_frame = frame # type: ignore[attr-defined]
            self.assistant.last_vision_frame_ts = frame_ts # type: ignore[attr-defined]

        vision_system.update_webcam_frame(frame, ts=ts)

        if self.assistant._active_session: # type: ignore[attr-defined]
            if command == "check_intelligence":
                await notify_transcription("assistant", "Sir, metrics check kar raha hoon.")
            elif command == "open_archives":
                await notify_transcription("assistant", "Sir, archives open kar raha hoon.")

    async def on_connect(self) -> None:
        self._is_reconnecting = False
        logger.info("✅ UI Bridge Connected. (auth %s)", self._auth_kind)
        wake_retry_queue()  # the bridge is back: send the notifies that wait for a retry now

    async def on_disconnect(self) -> None:
        logger.warning("❌ UI Bridge Disconnected. Retrying...")
        self._is_reconnecting = True

    def _auth(self, url: str) -> dict[str, Any]:
        """Built anew for every connect. The internal :5002 listener (VPS brain) gets the agent role with
        this job's room (K-38 v4). Any other URL gets the agent_token with this host and version (role
        'agent', C-SOCK-1) when the agent link v2 keys are set, else today's legacy token. The agent_token
        auth declares the room and job too: K-38 v4 sends ui_command only to agent sockets whose declared
        room is JARVIS_ROOM_NAME, and a bridge that predates that ignores the extra keys."""
        if is_internal_listener(url):
            room, job_id = _job_identity()
            self._auth_kind = "internal"
            return {"role": "agent", "room": room, "job_id": job_id}
        if agent_link_v2():
            agent_id, version = _agent_identity()
            room, job_id = _job_identity()
            self._auth_kind = "agent_token"
            return {"agent_token": config.agent_token, "agent_id": agent_id, "version": version,
                    "room": room, "job_id": job_id}
        self._auth_kind = "legacy token"
        return {'token': config.security_token}

    async def _close(self) -> None:
        """Drops a live or half-open connection; never raises."""
        try:
            await asyncio.wait_for(self.sio.disconnect(), timeout=5)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("UI Bridge disconnect: %s", e)

    async def start(self) -> None:
        """
        Keeps the UI bridge connection for the life of the job and never returns (K-38). After a failed
        connect or a lost connection it reconnects with a fresh auth, waiting 1, 2, 4 ... 10 s between
        attempts (back to 1 s after a connection that held). Cancelling the task closes the socket.
        """
        url = config.bridge_url
        base_url, socketio_path = bridge_socket_target(url)
        loop = asyncio.get_running_loop()
        delay = RECONNECT_FIRST_S
        failures = 0
        last_failure_log = float("-inf")
        while True:
            connected_at: float | None = None
            try:
                await self.sio.connect(base_url, auth=self._auth(url), socketio_path=socketio_path,
                                       wait_timeout=_CONNECT_WAIT_S)
                connected_at = loop.time()
                failures = 0
                await self.sio.wait()
                logger.warning("🔄 UI Bridge lost. Reconnecting...")
            except asyncio.CancelledError:
                await self._close()
                raise
            except Exception as e:
                failures += 1
                # WARNING, at most once a minute: the bridge may be down for a while (deploy, restart).
                if failures == 1 or loop.time() - last_failure_log >= _RETRY_LOG_EVERY_S:
                    last_failure_log = loop.time()
                    logger.warning("SocketIO connection error (attempt %d, next in %.0f s): %s",
                                   failures, delay, e)
                await self._close()
            if connected_at is not None and loop.time() - connected_at >= _STABLE_CONNECTION_S:
                delay = RECONNECT_FIRST_S
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_S)

async def start_ui_command_listener(assistant: "BrainAssistant") -> None:
    """Entry point for the UI command listener."""
    listener = UIBridgeListener(assistant)
    await listener.start()
