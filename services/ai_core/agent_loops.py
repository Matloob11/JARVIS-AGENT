"""
# agent_loops.py
Background loops for reminders, bug hunting, and UI communication.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import socketio

from services.ai_core.jarvis_vision import vision_system
from services.ai_core.swarm_manager import swarm_coordinator
from services.automation.jarvis_reminders import check_due_reminders
from services.ai_core.jarvis_vector_memory import jarvis_vector_db
from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_autonomous import autonomous_protector
from services.utils.jarvis_bridge import notify_transcription, notify_ui
from services.utils.jarvis_bug_hunter import monitor_logs
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard

if TYPE_CHECKING:
    from livekit.agents import AgentSession

    from src.core.agent_core import BrainAssistant

# Setup logger
logger = setup_logger("JARVIS-LOOPS")

# Module-level state for adaptation loop
_throttled_tasks: set[str] = set()

async def start_adaptation_loop() -> None:
    """
    Autonomous Load Adaptation: Throttles non-essential services during stress.
    Self-Monitoring + Self-Healing synergy.
    """
    # Track which tasks were throttled to allow self-healing recovery later
    if not hasattr(start_adaptation_loop, "_throttled_tasks"):
        start_adaptation_loop._throttled_tasks = set()

    while True:
        try:
            stress = adaptive_engine.analyze_system_load()

            if stress in ["CRITICAL", "STRESSED"]:
                # Throttling non-essential high-load tasks
                logger.critical("🚨 SYSTEM OVERLOAD: Throttling high-load services...")
                for task_name in ["reminder_loop", "bug_hunter_loop", "memory_loop"]:
                    task = autonomous_protector.get_task(task_name)
                    if task and not task.done():
                        logger.warning("📉 Suspending non-essential task: %s", task_name)
                        task.cancel()
                        _throttled_tasks.add(task_name)

            elif stress == "HEALTHY" and _throttled_tasks:
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
            sleep_time = 30 if stress != "HEALTHY" else 90
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
    """Check for due reminders and trigger proactive responses."""
    while True:
        # check_due_reminders is blocking (file IO), run in thread
        due = await asyncio.to_thread(check_due_reminders)
        for item in due:
            logger.info("🔔 Triggering proactive reminder: %s", item['message'])
            await session.say(
                f"Sir ko proactively yaad dilayein (Natural Urdu main): '{item['message']}'",
                allow_interruptions=True,
            )
        await asyncio.sleep(30)


async def start_bug_hunter_loop(session: "AgentSession") -> None:
    """Monitor error logs and notify the user about issues."""
    while True:
        try:
            async def on_error_detected(error_block: str) -> None:
                logger.warning("🚨 AI Bug Hunter detected a system error.")
                await session.say(
                    (
                        "Sir ko Roman Urdu main batayein ke ek system error mila hai "
                        "aur uska analysis dain. "
                        f"Error details:\n{error_block[:1000]}"
                    ),
                    allow_interruptions=True,
                )

            await monitor_logs(on_error_detected)
        except Exception as e:
            logger.error("🛑 Bug hunter loop failure: %s", e)
            await asyncio.sleep(60) # Longer wait for log system recovery


class UIBridgeListener:
    """Manages Socket.IO connection and command handling for the UI Bridge."""
    def __init__(self, assistant: "BrainAssistant") -> None:
        self.assistant = assistant
        self.sio = socketio.AsyncClient()
        self._is_reconnecting = False

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
                logger.warning("🛑 STOP command received. Shutdown sequence initiated.")
                if hasattr(self.assistant, "_stop_event"):
                    self.assistant._stop_event.set() # type: ignore[attr-defined]
                else:
                    autonomous_protector.shutdown_signal.set()
            elif cmd_type == "persona_change":
                logger.info("🎭 Switching persona to: %s", payload)
                await self.assistant.tool_toggle_gf_mode(payload == "anna")
            elif cmd_type == "clear_memory":
                logger.info("🧠 Clearing short-term memory...")
                await self.assistant.memory_extractor.memory.clear()
            elif cmd_type == "chat":
                if payload:
                    logger.info("💬 Chat message received: %s", payload)
                    new_task = asyncio.create_task(self._task_wrapper(payload))
                    self.assistant._spawned_tasks.add(new_task) # type: ignore[attr-defined]
                    new_task.add_done_callback(self.assistant._spawned_tasks.discard) # type: ignore[attr-defined]
            elif cmd_type == "swarm":
                if payload:
                    logger.info("🌀 Swarm Mode triggered with: %s", payload)
                    swarm_task = asyncio.create_task(swarm_coordinator.execute_swarm(payload))
                    self.assistant._spawned_tasks.add(swarm_task) # type: ignore[attr-defined]
                    swarm_task.add_done_callback(self.assistant._spawned_tasks.discard) # type: ignore[attr-defined]
        except Exception as e:
            logger.error("❌ Error processing UI command %s: %s", cmd_type, e, exc_info=True)

    async def _task_wrapper(self, payload: str) -> None:
        try:
            user_msg_id = str(uuid.uuid4())
            await notify_transcription("user", payload, msg_id=user_msg_id)
            safe_message = vortex_guard.sanitize_input(payload)
            response = await self.assistant.handle_user_query(safe_message)

            # pylint: disable=protected-access
            if self.assistant._active_session: # type: ignore[attr-defined]
                logger.info("🎤 Responding to chat via Voice Engine...")
                await self.assistant._active_session.say(response, allow_interruptions=True) # type: ignore[attr-defined]
            else:
                await notify_ui("agent_message", {"text": response})
        except Exception as e:
            logger.error("Task execution failure: %s", e)

    async def on_vision_frame(self, data: Any) -> None:
        frame = data.get("frame") if isinstance(data, dict) else data
        command = data.get("command") if isinstance(data, dict) else None

        if hasattr(self.assistant, 'vision_handler'):
            self.assistant.vision_handler.last_vision_frame = frame # type: ignore[attr-defined]
        else:
            self.assistant.last_vision_frame = frame # type: ignore[attr-defined]

        vision_system.update_webcam_frame(frame)

        if self.assistant._active_session: # type: ignore[attr-defined]
            if command == "check_intelligence":
                await self.assistant._active_session.say("Sir, metrics check kar raha hoon.", allow_interruptions=True) # type: ignore[attr-defined]
            elif command == "open_archives":
                await self.assistant._active_session.say("Sir, archives open kar raha hoon.", allow_interruptions=True) # type: ignore[attr-defined]

    async def on_connect(self) -> None:
        self._is_reconnecting = False
        logger.info("✅ UI Bridge Connected.")

    async def on_disconnect(self) -> None:
        logger.warning("❌ UI Bridge Disconnected. Retrying...")
        self._is_reconnecting = True

    async def start(self) -> None:
        """Main connection loop."""
        while True:
            try:
                if not self.sio.connected:
                    await self.sio.connect(config.bridge_url, auth={'token': config.security_token})
                while self.sio.connected:
                    await asyncio.sleep(1)
                logger.warning("🔄 UI Bridge lost. Retrying via autonomous restart...")
                break
            except Exception as e:
                logger.error("SocketIO connection error: %s", e)
                await asyncio.sleep(5)
                break

async def start_ui_command_listener(assistant: "BrainAssistant") -> None:
    """Entry point for the UI command listener."""
    listener = UIBridgeListener(assistant)
    await listener.start()
