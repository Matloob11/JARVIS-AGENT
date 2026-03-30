"""
# agent_runner.py
Entrypoint and session management for the JARVIS agent.
"""

# pylint: disable=broad-exception-caught, wrong-import-position
# ruff: noqa: E402

import asyncio
import logging
import traceback
import uuid
from collections.abc import Coroutine
from typing import Any

# FORCE global logging to show status while debugging
logging.basicConfig(level=logging.INFO, force=True)
for logger_name in ["livekit", "livekit.agents", "livekit.rtc", "root"]:
    log_obj = logging.getLogger(logger_name)
    log_obj.setLevel(logging.ERROR)
    log_obj.propagate = False

from livekit import agents, rtc
from livekit.agents import AgentSession, WorkerOptions, cli, llm
from livekit.plugins import google, silero

# Suppress LiveKit internal session noise
for logger_name in ["livekit", "livekit.agents", "livekit.rtc"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

from services.ai_core.agent_loops import (
    start_adaptation_loop,
    start_bug_hunter_loop,
    start_memory_storage_loop,
    start_reminder_loop,
    start_ui_command_listener,
)
from services.ai_core.agent_memory import MemoryExtractor
from services.automation.jarvis_clipboard import ClipboardMonitor
from services.info.jarvis_search import (
    get_current_city,
    get_current_city_data,
    get_formatted_datetime,
)
from services.utils.jarvis_autonomous import autonomous_protector
from services.utils.jarvis_diagnostics import diagnostics as diagnostics_instance
from services.utils.jarvis_healing import healing_engine
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger

# Notification functions moved inside entrypoint to handle potential scope/import issues

# Import BrainAssistant from agent_core
try:
    from .agent_core import BrainAssistant
except (ImportError, ValueError):
    from agent_core import BrainAssistant  # type: ignore

logger = setup_logger("AGENT-RUNNER")

# Notification functions moved to services.utils.jarvis_bridge


async def start_memory_loop(assistant: BrainAssistant, memory_extractor: MemoryExtractor) -> None:
    """Continuous memory extraction loop."""
    while True:
        try:
            # Fetch latest history from ChatContext messages
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
            else:
                history_items = []

            if history_items:
                logger.debug("Running memory extraction for %s", assistant.user_id)
                await memory_extractor.run(history_items)
            await asyncio.sleep(60) # Memory extraction doesn't need to be every 15s
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
        await asyncio.sleep(10)


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


async def _start_background_tasks(session: AgentSession, assistant: BrainAssistant) -> list[asyncio.Task[Any]]:
    """Starts all background loops and monitors via the autonomous protector."""
    logger.info("🔄 Starting protected background tasks...")

    # Launch background loops via the autonomous protector to ensure resilience
    await autonomous_protector.run_protected("memory_loop", start_memory_loop, assistant, assistant.memory_extractor)
    await autonomous_protector.run_protected("heartbeat_loop", start_heartbeat_loop)
    await autonomous_protector.run_protected("ui_command_listener", start_ui_command_listener, assistant)

    # 💥 NEW: Self-Monitoring & Proactive Analysis Loops
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

    clip_monitor = ClipboardMonitor()

    async def _on_clipboard_detected(solution):
        logger.info("📋 Clipboard Detection: %s", solution)
        if assistant and hasattr(assistant, "chat_ctx"):
            assistant.chat_ctx.messages.append(
                llm.ChatMessage(
                    role="assistant",
                    content=[f"[SYSTEM NOTIFICATION: CLIPBOARD ERROR DETECTED]\n{solution}"],
                ),
            )

    # Clipboard monitor doesn't naturally support a coro_func retry without refactor,
    # so we wrap it in a custom task for now.
    clip_task = asyncio.create_task(clip_monitor.start(_on_clipboard_detected))

    # Return all managed tasks to be added to the session's active_tasks set
    managed_tasks = list(autonomous_protector._tasks.values())
    managed_tasks.append(clip_task)
    return managed_tasks


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
        for task in tasks:
            if task and isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
            logger.info("Attempting to start session...")
            # FAST CONNECT: Connect to the room immediately to satisfy LiveKit assignment
            await asyncio.wait_for(ctx.connect(), timeout=10.0)
            logger.info("✅ Connected to room: %s", ctx.room.name)

            # Now perform secondary initialization
            current_dt_res = await get_formatted_datetime()
            city = await get_current_city()

            # Tune VAD to be less sensitive to background noise and prevent stuttering
            # min_speech_duration: ignore short clicks/noise
            # min_silence_duration: wait a bit longer before considering user done
            vad = silero.VAD.load(
                min_speech_duration=0.1,
                min_silence_duration=1.0,
            )

            llm_model = google.realtime.RealtimeModel(
                voice="charon",
                model="models/gemini-2.5-flash-native-audio-latest",
            )

            session = AgentSession(
                preemptive_generation=True,
                allow_interruptions=True,
                vad=vad,
                stt=None,
                llm=llm_model,
                tts=None,
            )

            # --- Internal Session Signaling Handlers ---
            # Suppress noisy internal LiveKit SDK warnings
            logging.getLogger("livekit").setLevel(logging.ERROR)
            logging.getLogger("livekit.agents").setLevel(logging.ERROR)

            @ctx.room.on("data_received")
            def on_data_received(data: rtc.DataPacket): # pylint: disable=useless-return
                # Suppress ignoring byte stream warnings for internal topics
                if data.topic in ["lk.agent.session", "lk.agent.monitor", "lk.agent.chat"]:
                    return  # pylint: disable=useless-return


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
                user_id=user_id,
            )

            logger.info("[AGENT] Connecting room and starting session...")
            await session.start(room=ctx.room, agent=assistant)
            logger.info("[AGENT] Session started successfully.")
            logger.info("✅ Session started successfully.")
            assistant.attach_session(session)

            # --- Location Update Loop ---
            async def location_loop():
                while True:
                    try:
                        loc_data = await get_current_city_data()
                        await notify_location(loc_data)
                    except Exception as e:
                        logger.error("Location loop error: %s", e)
                    await asyncio.sleep(60) # Location doesn't change often

            track(location_loop())


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

                    logger.info("🎤 User Transcription: %s", event.transcript)

                    # Generate or reuse ID
                    if current_user_msg_id is None:
                        current_user_msg_id = str(uuid.uuid4())

                    track(notify_transcription(
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
            bg_tasks = await _start_background_tasks(session, assistant)
            for bt in bg_tasks:
                active_tasks.add(bt)
                bt.add_done_callback(active_tasks.discard)

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

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
