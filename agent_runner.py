"""
# agent_runner.py
Entrypoint and session management for the JARVIS agent.
"""

# pylint: disable=broad-exception-caught

import httpx
import asyncio
import socket
import json
from typing import Optional, Any
from livekit import agents, rtc
from livekit.agents import AgentSession, llm
from jarvis_logger import setup_logger
from jarvis_diagnostics import diagnostics
from jarvis_search import get_current_city, get_formatted_datetime
from jarvis_clipboard import ClipboardMonitor
from agent_memory import MemoryExtractor
from agent_loops import (
    start_reminder_loop, start_bug_hunter_loop, start_ui_command_listener
)
from agent_core import BrainAssistant
from jarvis_window_ctrl import (
    maximize_window, minimize_window
)
from jarvis_instrumentation import setup_instrumentation

# Initialize instrumentation for next-level debugging
setup_instrumentation()

logger = setup_logger("JARVIS-RUNNER")


async def notify_ui(status: str):
    """Sends a notification to the STONIX UI Bridge."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post("http://127.0.0.1:5001/notify", json={
                "type": "status",
                "payload": status
            }, timeout=0.1)
    except Exception as e:
        logger.debug("Bridge Notification failed: %s", e)


async def notify_transcription(role: str, text: str):
    """Sends a transcription update to the STONIX UI Bridge."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post("http://127.0.0.1:5001/notify", json={
                "type": "transcription",
                "payload": {
                    "role": role,
                    "text": text,
                    "timestamp": get_formatted_datetime().get("formatted", "").split(" ")[-1]
                }
            }, timeout=0.2)
    except Exception as e:
        logger.debug("Transcription Notification failed: %s", e)


async def start_memory_loop(session: AgentSession):
    """Continuous memory extraction loop."""
    memory_extractor = MemoryExtractor()
    while True:
        try:
            if session is None or not hasattr(session, 'history'):
                await asyncio.sleep(5)
                continue

            history_items = session.history.items
            filtered_history = []
            for item in history_items:
                role = getattr(item, 'role', '').lower()
                raw_content = getattr(item, 'content', '')
                if isinstance(raw_content, list):
                    item_content = " ".join(
                        [getattr(p, 'text', str(p)) for p in raw_content]).lower()
                else:
                    item_content = str(raw_content).lower()

                if role == 'user':
                    if "jarvis" in item_content:
                        filtered_history.append(item)
                else:
                    filtered_history.append(item)

            await memory_extractor.run(filtered_history)
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("Memory storage loop stopping gracefully...")
            break
        except (AttributeError, ValueError, TypeError) as e:
            logger.error("Memory loop logic error: %s", e)
            await asyncio.sleep(10)
        except (IOError, OSError) as e:
            logger.error("Memory loop IO error: %s", e)
            await asyncio.sleep(15)


async def perform_startup_diagnostics():
    """Run pre-flight checks and log results."""
    logger.info("Initializing Pre-flight health check...")
    try:
        health_report = await diagnostics.run_full_diagnostics()
        if "Action Required" in health_report["summary"]:
            logger.warning("🚨 SYSTEM WARNING: Health: %s",
                           health_report["health_score"])
        else:
            logger.info("✅ SYSTEM HEALTH: 100%% Operational. Health: %s",
                        health_report["health_score"])
    except (AttributeError, ValueError, RuntimeError) as e:
        logger.error("Startup diagnostics failed: %s", e)


async def _start_background_tasks(session: AgentSession, assistant: Any):
    """Starts all background loops and monitors."""
    logger.info("🔄 Starting background tasks...")
    tasks = [
        asyncio.create_task(start_memory_loop(session)),
        asyncio.create_task(start_reminder_loop(session)),
        asyncio.create_task(start_bug_hunter_loop(session)),
        asyncio.create_task(start_ui_command_listener(assistant)),
        asyncio.create_task(perform_startup_diagnostics())
    ]

    clip_monitor = ClipboardMonitor()

    async def _on_clipboard_detected(solution):
        print(f"📋 Clipboard Detection: {solution}")
        if session and hasattr(session, "history"):
            session.history.append(
                role="assistant",
                content=f"[SYSTEM NOTIFICATION: CLIPBOARD ERROR DETECTED]\n{solution}"
            )
        try:
            if session:
                session.inference()
        except Exception as e:
            logger.warning("Proactive inference failed: %s", e)

    tasks.append(asyncio.create_task(
        clip_monitor.start(_on_clipboard_detected)))
    return tasks


async def _cleanup_session_resources(session: Optional[AgentSession], tasks: list):
    """Cancels tasks and stops the session safely."""
    # 1. Stop the session first to signal generators to close
    if session:
        try:
            logger.info("🛑 Stopping AgentSession...")
            await asyncio.wait_for(session.stop(), timeout=3.0)
        except (AttributeError, RuntimeError, ValueError, asyncio.TimeoutError) as e:
            logger.debug("Session stop error: %s", e)

    # 2. Cancel and gather background tasks
    if tasks:
        logger.info("🛑 Cleaning up background tasks...")
        for task in tasks:
            if task and not task.done():
                task.cancel()

        try:
            # More aggressive timeout for background tasks
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning("Background task cleanup timed out.")


def _print_startup_banner():
    """Prints the JARVIS startup banner to console."""
    print("\n" + "="*50)
    print("🚀 JARVIS SYSTEMS ONLINE")
    print("🔱 Elite AI Assistant | Sir Matloob Edition")
    print("🛡️ Security: Enabled | 🧠 Brain: Active")
    print("🎤 Standing by for wake word: 'Jarvis'")
    print("="*50 + "\n")


async def entrypoint(ctx: agents.JobContext):
    """
    Main job entrypoint.
    """
    max_retries = 10
    retry_delay = 5
    attempt = 0

    while attempt < max_retries:
        session: Optional[AgentSession] = None
        tasks = []
        try:
            logger.info(
                "Attempting to start session (Attempt %d/%d)...", attempt + 1, max_retries)

            # 1. Parallelize initial data gathering (Date/Time + City)
            current_dt_task = asyncio.create_task(get_formatted_datetime())
            city_task = asyncio.create_task(get_current_city())

            # Wait for both together
            current_dt_result, city = await asyncio.gather(current_dt_task, city_task)

            session = AgentSession(
                preemptive_generation=False,
                allow_interruptions=True,
            )

            if ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
                await ctx.connect()

            # Diagnostics moved to background tasks to speed up startup

            chat_ctx = llm.ChatContext()
            for item in session.history.items:
                chat_ctx.items.append(item)

            assistant = BrainAssistant(
                chat_ctx=chat_ctx,
                current_date=current_dt_result.get("formatted"),
                current_city=city
            )

            await session.start(room=ctx.room, agent=assistant)
            assistant.attach_session(session)

            @session.on("agent_started_speaking")
            def _on_start():
                asyncio.create_task(notify_ui("START"))
                # Auto-focus the current persona UI
                try:
                    active_ui = "A.N.N.A - Core Interface" if assistant._gf_mode_active else "J.A.R.V.I.S"
                    inactive_ui = "J.A.R.V.I.S" if assistant._gf_mode_active else "A.N.N.A - Core Interface"

                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(maximize_window(active_ui))
                        loop.create_task(minimize_window(inactive_ui))
                except Exception as e:
                    logger.warning("Focus on speak failed: %s", e)

            @session.on("agent_stopped_speaking")
            def _on_stop():
                asyncio.create_task(notify_ui("STOP"))

            @session.on("user_transcription")
            def _on_user_transcript(transcript):
                if transcript and transcript.text:
                    asyncio.create_task(
                        notify_transcription("user", transcript.text))

            @session.on("agent_transcription")
            def _on_agent_transcript(transcript):
                if transcript and transcript.text:
                    asyncio.create_task(
                        notify_transcription("agent", transcript.text))

            _print_startup_banner()
            tasks = await _start_background_tasks(session, assistant)
            await asyncio.Event().wait()

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Shutdown signal received.")
            break
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("⚠️ Session error: %s", exc, exc_info=True)
            attempt += 1
            if attempt < max_retries:
                wait = min(retry_delay * (2 ** (attempt - 1)), 60)
                await asyncio.sleep(wait)
        finally:
            await _cleanup_session_resources(session, tasks)
