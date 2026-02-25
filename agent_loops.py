"""
# agent_loops.py
Background loops for reminders, bug hunting, and UI communication.
"""

# pylint: disable=broad-exception-caught

import asyncio
import json
import socket
from typing import TYPE_CHECKING
from jarvis_logger import setup_logger
from jarvis_reminders import check_due_reminders
from jarvis_bug_hunter import monitor_logs

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
                f"Sir ko Roman Urdu main batayein ke ek system error mila hai aur uska analysis dain. "
                f"Error details:\n{error_block[:1000]}"
            ),
            allow_interruptions=True
        )

    await monitor_logs(on_error_detected)


async def start_ui_command_listener(assistant: "BrainAssistant"):
    """Listens for UDP commands from the UI (Mute/Unmute)."""
    server_address = ("127.0.0.1", 5006)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(server_address)
        logger.info("UI Command Listener active on port 5006")
    except socket.error as e:
        logger.error("Could not bind UI listener: %s", e)
        return

    while True:
        try:
            # Use to_thread for blocking recvfrom
            data, _ = await asyncio.to_thread(sock.recvfrom, 1024)
            message = json.loads(data.decode())
            command = message.get("command")

            if command == "MUTE":
                # pylint: disable=protected-access
                assistant._muted = True
                logger.info("🔇 Agent MUTED via UI.")
            elif command == "UNMUTE":
                # pylint: disable=protected-access
                assistant._muted = False
                logger.info("🔊 Agent UNMUTED via UI.")

        except (json.JSONDecodeError, socket.error) as e:
            logger.error("UI Command IPC error: %s", e)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("UI Listener error: %s", e)
            await asyncio.sleep(2)
    sock.close()
