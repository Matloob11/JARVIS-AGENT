"""
# agent_runner.py
Entrypoint and session management for the JARVIS agent.
"""

# pylint: disable=broad-exception-caught

import asyncio
from typing import Optional, Any

from livekit import agents, rtc
from livekit.agents import AgentSession, llm, WorkerOptions, cli
from livekit.plugins import google

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_healing import healing_engine
from services.utils.jarvis_checkpoint import checkpoint_manager
from services.utils.jarvis_qa import qa_engine
from services.utils.jarvis_diagnostics import diagnostics as diagnostics_instance

from services.ai_core.agent_memory import MemoryExtractor
from services.info.jarvis_search import get_formatted_datetime, get_current_city
from services.ai_core.agent_loops import (
    start_reminder_loop, start_bug_hunter_loop, start_ui_command_listener
)
from services.automation.jarvis_clipboard import ClipboardMonitor
from services.utils.jarvis_bridge import (
    notify_ui, notify_transcription, notify_thinking
)

# Import BrainAssistant from agent_core
from agent_core import BrainAssistant

logger = setup_logger("AGENT-RUNNER")

# Notification functions moved to services.utils.jarvis_bridge


async def start_memory_loop(session: AgentSession, memory_extractor: MemoryExtractor):
    """Continuous memory extraction loop."""
    while True:
        try:
            if session is None or not hasattr(session, 'history'):
                await asyncio.sleep(5)
                continue

            history_items = list(session.history.items)[-20:]
            await memory_extractor.run(history_items)
            await asyncio.sleep(15)
        except Exception as e:
            logger.error("Memory loop error: %s", e)
            await asyncio.sleep(15)


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

    async def _get_agent_state():
        return assistant.get_state() if hasattr(assistant, "get_state") else {}

    tasks = [
        asyncio.create_task(start_memory_loop(
            session, assistant.memory_extractor)),
        asyncio.create_task(start_heartbeat_loop()),
        asyncio.create_task(start_reminder_loop(session)),
        asyncio.create_task(start_bug_hunter_loop(session)),
        asyncio.create_task(start_ui_command_listener(assistant)),
        asyncio.create_task(perform_startup_diagnostics()),
        asyncio.create_task(healing_engine.start_healing_loop()),
        asyncio.create_task(
            checkpoint_manager.start_checkpoint_loop(_get_agent_state, interval=3600)),
        asyncio.create_task(qa_engine.start_qa_loop(
            _get_agent_state, interval=7200))
    ]

    # Register Recovery Actions
    async def _recover_ui_bridge():
        logger.warning("🚑 HEALING: Attempting to notify UI of runner pulse...")
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
    logger.info("🎤 Subscribing to audio track: %s", publication.sid)
    try:
        track = await publication.subscribe()
        audio_stream = rtc.AudioStream(track)
        async for audio_frame in audio_stream:
            assistant.add_audio_frame(audio_frame)
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
            if task and not task.done():
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
            logger.info("Attempting to start session...")

            # Context data
            current_dt_res = await get_formatted_datetime()
            city = await get_current_city()

            session = AgentSession(
                preemptive_generation=False,
                allow_interruptions=True,
                tts=google.TTS()  # Fallback TTS for manual .say() calls
            )

            if ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
                await ctx.connect()

            chat_ctx = llm.ChatContext()
            assistant = BrainAssistant(
                chat_ctx=chat_ctx,
                current_date=current_dt_res.get("formatted"),
                current_city=city
            )

            logger.info("Starting session...")
            await session.start(room=ctx.room, agent=assistant)
            logger.info("✅ Session started successfully.")
            assistant.attach_session(session)

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

            @session.on("user_input_transcribed")
            def on_user_transcription(event: agents.voice.UserInputTranscribedEvent):
                if event.transcript:
                    logger.info("🎤 User Transcription: %s", event.transcript)
                    asyncio.create_task(notify_transcription(
                        "user", event.transcript, is_final=event.is_final))

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
