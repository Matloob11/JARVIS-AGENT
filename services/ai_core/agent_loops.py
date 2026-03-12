"""
# agent_loops.py
Background loops for reminders, bug hunting, and UI communication.
"""

import asyncio
import sys
from typing import TYPE_CHECKING
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.automation.jarvis_reminders import check_due_reminders
from services.utils.jarvis_bug_hunter import monitor_logs

if TYPE_CHECKING:
    from agent_core import BrainAssistant
    from livekit.agents import AgentSession

# Setup logger
logger = setup_logger("JARVIS-LOOPS")


async def start_memory_storage_loop(assistant: "BrainAssistant"):
    """Periodically saves memory to disk."""
    while True:
        try:
            await asyncio.sleep(600)  # Every 10 minutes
            await assistant.memory_extractor.memory.save_to_disk()
            logger.info("💾 Auto-saved memory snapshot.")
        except asyncio.CancelledError:
            logger.info("Memory storage loop stopping gracefully...")
            break
        except (AttributeError, ValueError, TypeError) as e:
            logger.error("Memory loop logic error: %s", e)
            await asyncio.sleep(10)
        except (IOError, OSError) as e:
            logger.error("Memory loop IO error: %s", e)
            await asyncio.sleep(15)


async def start_reminder_loop(session: "AgentSession"):
    """Check for due reminders and trigger proactive responses."""
    while True:
        try:
            # check_due_reminders is blocking (file IO), run in thread
            due = await asyncio.to_thread(check_due_reminders)
            for item in due:
                print(f"🔔 Triggering proactive reminder: {item['message']}")
                session.say(
                    f"Sir ko proactively yaad dilayein (Natural Urdu main): '{item['message']}'",
                    allow_interruptions=True
                )
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("Reminder check loop stopping gracefully...")
            break
        except (IOError, OSError, ValueError) as e:
            logger.error("Reminder loop check error: %s", e)
            await asyncio.sleep(60)


async def start_bug_hunter_loop(session: "AgentSession"):
    """Monitor error logs and notify the user about issues."""
    async def on_error_detected(error_block: str):
        logger.warning("🚨 AI Bug Hunter detected a system error.")
        # Proactively trigger a response to analyze and fix
        session.say(
            (
                "Sir ko Roman Urdu main batayein ke ek system error mila hai "
                "aur uska analysis dain. "
                f"Error details:\n{error_block[:1000]}"
            ),
            allow_interruptions=True
        )

    await monitor_logs(on_error_detected)


async def start_ui_command_listener(assistant: "BrainAssistant"):
    """Listens for Socket.IO commands from the STONIX UI Bridge."""
    import socketio # pylint: disable=import-outside-toplevel

    sio = socketio.AsyncClient()

    @sio.on('agent_command')
    async def on_command(data):
        if not isinstance(data, dict):
            return
        await _handle_command(assistant, data)

    async def _handle_command(assistant: "BrainAssistant", data: dict):
        cmd_type = data.get("type")
        payload = data.get("payload")

        if cmd_type == "mute":
            assistant.set_muted(True)
            logger.info("🔇 Agent MUTED via UI.")
        elif cmd_type == "unmute":
            assistant.set_muted(False)
            logger.info("🔊 Agent UNMUTED via UI.")
        elif cmd_type == "wake_word_toggle":
            new_mode = payload if isinstance(payload, bool) else (not assistant.wake_word_mode)
            assistant.set_wake_word_mode(new_mode)
            logger.info("🎤 Wake word mode set to %s via UI.", new_mode)
        elif cmd_type == "stop":
            logger.warning("🛑 STOP command received. Shutdown...")
            sys.exit(0)
        elif cmd_type == "persona_change":
            logger.info("🎭 Switching persona to: %s", payload)
            await assistant.tool_toggle_gf_mode(payload == "anna")
        elif cmd_type == "clear_memory":
            logger.info("🧠 Clearing short-term memory...")
            assistant.memory_extractor.memory.clear()
        elif cmd_type == "settings_update":
            logger.info("⚙️ Settings updated: %s", payload)
        elif cmd_type == "chat":
            if payload:
                # pylint: disable=protected-access
                if assistant._active_session:
                    logger.info("💬 Chat message received: %s", payload)
                    asyncio.create_task(process_ui_message(assistant, payload))

    async def process_ui_message(assistant, message: str):
        """Processes a text message from the UI and responds via voice."""
        try:
            # Notify UI that we are thinking
            from services.utils.jarvis_bridge import notify_transcription # pylint: disable=import-outside-toplevel
            import uuid # pylint: disable=import-outside-toplevel

            user_msg_id = str(uuid.uuid4())
            await notify_transcription("user", message, msg_id=user_msg_id)
            response = await assistant.handle_user_query(message)

            # pylint: disable=protected-access
            if assistant._active_session:
                assistant._active_session.say(response, allow_interruptions=True)
            else:
                logger.warning("⚠️ No active session for voice.")
        except (ValueError, KeyError, RuntimeError, TypeError, AttributeError) as e:
            logger.error("❌ Error processing UI chat: %s", e, exc_info=True)

    @sio.on('agent_vision_frame')
    async def on_vision_frame(data):
        """Update the assistant with the latest camera frame."""
        command = None
        if isinstance(data, dict):
            assistant.last_vision_frame = data.get("frame")
            command = data.get("command")
        else:
            assistant.last_vision_frame = data

        # pylint: disable=protected-access
        if not assistant._active_session:
            return

        if command == "check_intelligence":
            assistant._active_session.say(
                "Sir, main aapke system ki intelligence metrics check kar raha hoon.",
                allow_interruptions=True)
        elif command == "open_archives":
            assistant._active_session.say(
                "Sir, main aapke archives aur memory storage open kar raha hoon.",
                allow_interruptions=True)
        elif command == "open_comm":
            assistant._active_session.say(
                "Sir, communications channel open hai.", allow_interruptions=True)

    vortex_token = config.security_token

    for i in range(5):
        try:
            await sio.connect(config.bridge_url, auth={"token": vortex_token})
            logger.info("✅ Connected to UI Bridge.")
            await sio.wait()
            break
        except (ConnectionError, ValueError, RuntimeError) as e:
            logger.debug("Bridge failed (Attempt %d): %s", i+1, e)
            await asyncio.sleep(2)
    else:
        logger.error("❌ UI Bridge connection failed.")
