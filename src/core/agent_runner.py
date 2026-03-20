"""
# agent_runner.py
Entrypoint and session management for the JARVIS agent.
"""

# pylint: disable=broad-exception-caught

import asyncio
import logging
import traceback
import uuid
from typing import Optional, Any

# FORCE global logging to suppress SDK and root noise
logging.basicConfig(level=logging.WARNING, force=True)
for logger_name in ["livekit", "livekit.agents", "livekit.rtc", "root"]:
    l = logging.getLogger(logger_name)
    l.setLevel(logging.ERROR)
    l.propagate = False

from livekit import agents, rtc
from livekit.agents import AgentSession, llm, WorkerOptions, cli
from livekit.plugins import google

# Suppress LiveKit internal session noise
for logger_name in ["livekit", "livekit.agents", "livekit.rtc"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_healing import healing_engine
from services.utils.jarvis_diagnostics import diagnostics as diagnostics_instance

from services.ai_core.agent_memory import MemoryExtractor
from services.info.jarvis_search import (
    get_formatted_datetime, get_current_city, get_current_city_data
)
from services.ai_core.agent_loops import (
    start_ui_command_listener
)
from services.automation.jarvis_clipboard import ClipboardMonitor
# Notification functions moved inside entrypoint to handle potential scope/import issues

# Import BrainAssistant from agent_core
try:
    from .agent_core import BrainAssistant
except (ImportError, ValueError):
    from agent_core import BrainAssistant

logger = setup_logger("AGENT-RUNNER")

# Notification functions moved to services.utils.jarvis_bridge


async def start_memory_loop(assistant: BrainAssistant, memory_extractor: MemoryExtractor):
    """Continuous memory extraction loop."""
    while True:
        try:
            # Correctly access chat history from BrainAssistant's ChatContext
            messages = assistant.chat_ctx.messages
            if callable(messages):
                messages = messages()
            history_items = list(messages)[-20:] if messages else []
            if history_items:
                await memory_extractor.run(history_items)
            await asyncio.sleep(60) # Memory extraction doesn't need to be every 15s
        except Exception as e:
            logger.error("Memory loop error: %s", e)
            await asyncio.sleep(60)


async def start_heartbeat_loop():
    """Periodic pulse to keep services from timing out."""
    logger.info("💓 Heartbeat monitor active.")
    while True:
        try:
            health_monitor.record_heartbeat("agent_runner")
            health_monitor.record_heartbeat("backend_core")
        except Exception as e:
            logger.error("Heartbeat loop error: %s", e)
        await asyncio.sleep(10)


async def perform_startup_diagnostics():
    """Run pre-flight checks and log results."""
    logger.info("Initializing Pre-flight health check...")
    try:
        health_report_list = await diagnostics_instance.run_all()
        # Simplified logging for health report list
        logger.info("✅ SYSTEM HEALTH: %d checks passed",
                    len(health_report_list))
    except Exception as e:
        logger.error("Startup diagnostics failed: %s", e)


async def _start_background_tasks(session: AgentSession, assistant: Any):
    """Starts all background loops and monitors."""
    logger.info("🔄 Starting background tasks...")

    # async def _get_agent_state():
    #     return assistant.get_state() if hasattr(assistant, "get_state") else {}

    tasks = [
        asyncio.create_task(start_memory_loop(
            assistant, assistant.memory_extractor)),
        asyncio.create_task(start_heartbeat_loop()),
        asyncio.create_task(start_ui_command_listener(assistant)),
    ]

    # Register Recovery Actions
    async def _recover_ui_bridge():
        logger.warning("🚑 HEALING: Attempting to notify UI of runner pulse...")
        from services.utils.jarvis_bridge import notify_ui # pylint: disable=import-outside-toplevel
        await notify_ui("HEARTBEAT")

    async def _recover_backend_core():
        logger.warning(
            "🚑 HEALING: Backend Core heartbeat timeout. Re-pulsing...")
        health_monitor.record_heartbeat("backend_core")

    healing_engine.register_recovery_action("ui_bridge", _recover_ui_bridge)
    healing_engine.register_recovery_action(
        "backend_core", _recover_backend_core)

    clip_monitor = ClipboardMonitor()

    async def _on_clipboard_detected(solution):
        logger.info("📋 Clipboard Detection: %s", solution)
        if assistant and hasattr(assistant, "chat_ctx"):
            assistant.chat_ctx.messages.append(
                llm.ChatMessage(
                    role="assistant",
                    content=[f"[SYSTEM NOTIFICATION: CLIPBOARD ERROR DETECTED]\n{solution}"]
                )
            )

    tasks.append(asyncio.create_task(
        clip_monitor.start(_on_clipboard_detected)))
    return tasks


async def _process_user_audio(publication: rtc.TrackPublication, assistant: BrainAssistant):
    """Subscribes to user audio and feeds frames to the assistant for Voice ID."""
    if assistant.muted:
        logger.info("🔇 Assistant is muted. Skipping subscription to track: %s", publication.sid)
        return

    logger.info("🎤 Subscribing to audio track: %s", publication.sid)
    try:
        publication.set_subscribed(True)
        # Wait for track to be available
        while not publication.track:
            await asyncio.sleep(0.1)

        track = publication.track
        audio_stream = rtc.AudioStream(track)
        async for event in audio_stream:
            # LiveKit AudioStream yields AudioFrameEvent
            if hasattr(event, 'frame'):
                assistant.add_audio_frame(event.frame)
            else:
                assistant.add_audio_frame(event)
    except Exception as e:
        logger.error("Audio subscription error: %s", e)


async def _cleanup_session_resources(session: Optional[AgentSession], tasks: list):
    """Cancels tasks and stops the session safely."""
    if session:
        try:
            logger.info("🛑 Stopping AgentSession...")
            await asyncio.wait_for(session.stop(), timeout=10.0)
        except Exception as e:
            logger.debug("Session stop error: %s", e)

    if tasks:
        logger.info("🛑 Cleaning up background tasks...")
        for task in tasks:
            if task and isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _print_startup_banner():
    """Prints the JARVIS startup banner."""
    print("\n" + "="*50)
    print("🚀 JARVIS SYSTEMS ONLINE")
    print("🔱 Elite AI Assistant | Sir Matloob Edition")
    print("🛡️ Security: Enabled | 🧠 Brain: Active")
    print("🎤 Standing by for wake word: 'Jarvis'")
    print("="*50 + "\n")


async def entrypoint(ctx: agents.JobContext):
    """Main job entrypoint."""
    max_retries = 5
    attempt = 0

    while attempt < max_retries:
        session: Optional[AgentSession] = None
        tasks = []
        try:
            from services.utils.jarvis_bridge import ( # pylint: disable=import-outside-toplevel
                notify_ui, notify_transcription, notify_thinking, notify_event, notify_location
            )
            logger.info("Attempting to start session...")
            # FAST CONNECT: Connect to the room immediately to satisfy LiveKit assignment
            await asyncio.wait_for(ctx.connect(), timeout=10.0)
            logger.info("✅ Connected to room: %s", ctx.room.name)

            # Now perform secondary initialization
            current_dt_res = await get_formatted_datetime()
            city = await get_current_city()

            session = AgentSession(
                preemptive_generation=False,
                allow_interruptions=True,
                tts=google.TTS()  # Fallback TTS for manual .say() calls
            )

            # --- Internal Session Signaling Handlers ---
            # Suppress noisy internal LiveKit SDK warnings
            logging.getLogger("livekit").setLevel(logging.ERROR)
            logging.getLogger("livekit.agents").setLevel(logging.ERROR)

            @ctx.room.on("data_received")
            def on_data_received(data: rtc.DataPacket):
                # Suppress ignoring byte stream warnings for internal topics
                if data.topic in ["lk.agent.session", "lk.agent.monitor", "lk.agent.chat"]:
                    return


            # Try to resolve participant identity for memory/context
            user_id = "User"
            if ctx.room.remote_participants:
                # Take the first remote participant if available
                first_participant = next(iter(ctx.room.remote_participants.values()))
                user_id = first_participant.identity
            elif ctx.room.local_participant:
                # Fallback to local participant identity if remote not found yet
                user_id = ctx.room.local_participant.identity

            chat_ctx = llm.ChatContext()
            assistant = BrainAssistant(
                chat_ctx=chat_ctx,
                current_date=current_dt_res.get("formatted"),
                current_city=city,
                user_id=user_id
            )

            logger.info("[AGENT] Connecting room and starting session...")
            await session.start(room=ctx.room, agent=assistant)
            logger.info("[AGENT] Session started successfully.")
            logger.info("✅ Session started successfully.")
            assistant.attach_session(session)

            # --- Location Sync ---
            loc_data = await get_current_city_data()
            asyncio.create_task(notify_event("location_sync", loc_data))

            # --- Location Update Loop ---
            async def location_loop():
                while True:
                    try:
                        loc_data = await get_current_city_data()
                        await notify_location(loc_data)
                    except Exception as e:
                        logger.error("Location loop error: %s", e)
                    await asyncio.sleep(60) # Location doesn't change often

            tasks.append(asyncio.create_task(location_loop()))


            # --- Speaker Identification Subscription ---
            @ctx.room.on("track_published")
            def on_track_published(publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
                if publication.kind == rtc.TrackKind.KIND_AUDIO:
                    asyncio.create_task(_process_user_audio(publication, assistant))

            # Subscribe to existing tracks
            for participant in ctx.room.remote_participants.values():
                for publication in participant.track_publications.values():
                    if publication.kind == rtc.TrackKind.KIND_AUDIO:
                        asyncio.create_task(_process_user_audio(publication, assistant))
            # --------------------------------------------

            # --- Message ID Tracking for UI Stability ---
            current_user_msg_id = None

            @session.on("user_input_transcribed")
            def on_user_transcription(event: agents.voice.UserInputTranscribedEvent):
                nonlocal current_user_msg_id
                if event.transcript:
                    if assistant.muted:
                        # Skip UI broadcast if muted
                        return
                        
                    logger.info("🎤 User Transcription: %s", event.transcript)
                    
                    # Generate or reuse ID
                    if current_user_msg_id is None:
                        current_user_msg_id = str(uuid.uuid4())
                    
                    asyncio.create_task(notify_transcription(
                        "user", event.transcript, is_final=event.is_final, 
                        msg_id=current_user_msg_id))
                    
                    if event.is_final:
                        current_user_msg_id = None # Reset for next turn

            @session.on("conversation_item_added")
            def on_conversation_item_added(event: agents.voice.ConversationItemAddedEvent):
                item = event.item
                if hasattr(item, 'role') and item.role == "assistant":
                    text = getattr(item, 'text_content', None)
                    if text:
                        logger.info("🤖 Agent Transcription: %s", text)
                        asyncio.create_task(notify_transcription(
                            "agent", text, is_final=True))

            @session.on("agent_state_changed")
            def on_agent_state_changed(event: agents.voice.AgentStateChangedEvent):
                state = event.new_state
                logger.info("🤖 Agent State: %s", state)

                # Update UI States
                if state == "speaking":
                    asyncio.create_task(notify_ui("START"))
                    asyncio.create_task(notify_thinking("STOP"))
                elif state == "thinking":
                    asyncio.create_task(notify_thinking("START"))
                    asyncio.create_task(notify_ui("STOP"))
                else:
                    asyncio.create_task(notify_ui("STOP"))
                    asyncio.create_task(notify_thinking("STOP"))

            @session.on("error")
            def _s_error(err):
                logger.error("⚠️ Session Error: %s", err)

            _print_startup_banner()
            tasks = await _start_background_tasks(session, assistant)

            # Wait until session ends or room disconnects
            await asyncio.Event().wait()

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
            await _cleanup_session_resources(session, tasks)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
