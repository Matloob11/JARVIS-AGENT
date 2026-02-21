"""
# agent.py
Jarvis Agent Module

The main entry point for the AI Assistant. Handles job management, session initialization,
and routing between different tool modules and the LLM.
"""

import json
import asyncio
import socket
from typing import Optional, Any
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, StopResponse
from livekit.plugins import google
from jarvis_logger import setup_logger

from jarvis_file_opener import play_file, play_video, play_music
from jarvis_get_weather import get_weather
from jarvis_notepad_automation import (
    create_template_code, open_notepad_simple, run_cmd_command, write_custom_code
)
from jarvis_prompt import BEHAVIOR_PROMPT
from jarvis_reasoning import analyze_user_intent, generate_smart_response
from jarvis_search import (
    get_current_city, get_formatted_datetime, search_internet
)
from jarvis_system_info import get_laptop_info
from jarvis_whatsapp_automation import automate_whatsapp
from jarvis_youtube_automation import automate_youtube
from jarvis_window_ctrl import (
    close, create_folder, lock_screen, maximize_window, minimize_window,
    open_app, open_notepad_file, restart_system, save_notepad, shutdown_system,
    sleep_system, folder_file, open_outputs_folder
)
from jarvis_vision import analyze_screen
from jarvis_rag import ask_about_document
from jarvis_image_gen import tool_generate_image
from jarvis_advanced_tools import download_images, zip_files, send_email
from jarvis_qr_gen import generate_qr_code
from keyboard_mouse_ctrl import (
    control_volume_tool, mouse_click_tool, move_cursor_tool, press_hotkey_tool,
    press_key_tool, scroll_cursor_tool, set_volume_tool, swipe_gesture_tool,
    type_text_tool
)
from jarvis_clipboard import ClipboardMonitor
from jarvis_reminders import list_reminders, set_reminder, check_due_reminders
from jarvis_researcher import perform_web_research
from jarvis_self_healing import autonomous_self_repair
from memory_store import ConversationMemory

# Setup logging
logger = setup_logger("JARVIS-AGENT")


# Load environment variables early for global availability
load_dotenv()


# --- Tool & Prompt Modules ---

# --- Memory System ---

load_dotenv()


def notify_ui(status: str):
    """Sends a UDP packet to the UI to signal speaking status."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        message = json.dumps({"status": status})
        sock.sendto(message.encode(), ("127.0.0.1", 5005))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to notify UI: %s", e)


# ==============================================================================
# CONFIGURATION
# ==============================================================================
INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT  # Use same prompt as agent.py


class MemoryExtractor:
    """
    Handles extracting and saving conversation context to memory store.
    """

    def __init__(self, user_id: Optional[str] = None):
        """
        Initialize the memory extractor with a user ID.
        """
        effective_id = user_id or os.getenv("USER_NAME") or "User"
        self.user_id: str = effective_id
        self.memory = ConversationMemory(self.user_id)
        self.conversation_count = 0

    async def run(self, chat_ctx):
        """
        Process chat context to extract and save new messages to memory.
        """
        try:
            # Save current conversation context
            if chat_ctx and len(chat_ctx) > self.conversation_count:
                new_messages = chat_ctx[self.conversation_count:]

                for message in new_messages:
                    # Convert ChatMessage to dict if needed
                    msg_content = message.content if hasattr(
                        message, 'content') else str(message)
                    role = message.role if hasattr(
                        message, 'role') else "unknown"

                    conversation_data = {
                        "messages": [{"role": role, "content": msg_content}],
                        "timestamp": datetime.now().isoformat(),
                        "user_id": self.user_id
                    }

                    # Save to memory
                    success = await self.memory.save_conversation(conversation_data)
                    if success:
                        logger.info("💾 Memory saved: %s message", role)

                self.conversation_count = len(chat_ctx)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("❌ Memory extraction error: %s", e)

    def clear_context(self):
        """Placeholder to satisfy pylint R0903 (too-few-public-methods)."""
        self.conversation_count = 0


class BrainAssistant(Agent):
    """
    Enhanced Assistant with reasoning capabilities and integrated tool suite.
    """

    def __init__(self, chat_ctx: Any, current_date: Optional[str] = None, current_city: Optional[str] = None) -> None:
        """
        Initialize the BRAIN assistant with context, LLM, and tools.
        """
        # Format instructions with date and city context
        formatted_instructions = INSTRUCTIONS_PROMPT
        if current_date and current_city:
            formatted_instructions = INSTRUCTIONS_PROMPT.format(
                current_date=current_date,
                current_city=current_city
            )

        # Initialize memory and reasoning
        self.memory_extractor = MemoryExtractor()
        self.conversation_history: list[dict] = []
        self._wake_word_mode = True  # Default to True as per user request

        @agents.function_tool
        def set_wake_word_mode(active: bool) -> Any:
            """
            Toggle the strict wake word enforcement mode.
            If active is True, Jarvis will only respond when called by name.
            """
            self._wake_word_mode = active
            status = "active" if active else "disabled"
            return f"Wake word mode {status} ho gaya hai, Sir Matloob."

        super().__init__(
            chat_ctx=chat_ctx,
            instructions=formatted_instructions,
            llm=google.realtime.RealtimeModel(
                voice="charon", model="models/gemini-2.5-flash-native-audio-latest"),
            tools=[
                # Basic Tools
                search_internet,
                get_formatted_datetime,
                get_weather,
                # Notepad & Code Automation
                create_template_code,
                write_custom_code,
                run_cmd_command,
                open_notepad_simple,
                # System Tools
                shutdown_system,
                restart_system,
                sleep_system,
                lock_screen,
                create_folder,
                folder_file,
                open_outputs_folder,
                open_app,
                close,
                minimize_window,
                maximize_window,
                save_notepad,
                open_notepad_file,
                play_file,
                play_video,
                play_music,
                get_laptop_info,
                automate_whatsapp,
                # Keyboard & Mouse Control
                move_cursor_tool,
                mouse_click_tool,
                scroll_cursor_tool,
                type_text_tool,
                press_key_tool,
                press_hotkey_tool,
                control_volume_tool,
                set_volume_tool,
                swipe_gesture_tool,
                automate_youtube,
                set_wake_word_mode,
                analyze_screen,
                ask_about_document,
                download_images,
                zip_files,
                send_email,
                set_reminder,
                list_reminders,
                perform_web_research,
                autonomous_self_repair,
                tool_generate_image,
                generate_qr_code,
            ]
        )

    async def process_with_reasoning(self, user_input: str) -> str:
        """
        Process user input with advanced reasoning capabilities
        """
        try:
            # Analyze user intent
            intent_analysis = await analyze_user_intent(user_input)
            print(f"🧠 Intent Analysis: {intent_analysis}")

            # Get relevant memory context (Recent history + Semantic Long-Term memory)
            memory_context = await self.memory_extractor.memory.get_recent_context(
                max_messages=10)

            semantic_memory = await self.memory_extractor.memory.get_semantic_context(
                query=user_input, n_results=3)

            if semantic_memory:
                print(f"🧠 Semantic Memory Found: {semantic_memory}")

            # Generate smart response with context
            smart_response = await generate_smart_response(
                user_input,
                intent_analysis,
                memory_context,
                semantic_memory
            )

            return smart_response

        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"❌ Reasoning error: {e}")
            user_name = os.getenv("USER_NAME", "Sir")
            hindi_msg = "main aapka message samajh gaya hun. Kya main aapki madad kar sakta hun?"
            return f"{user_name}, {hindi_msg}"

    async def on_user_turn_completed(self, turn_ctx, new_message):
        """
        Called when the user finishes their turn of speaking.
        Strictly enforces wake word detection. Filters transcript line-by-line.
        """
        # Extract text from content safely
        text = ""
        try:
            raw_content = getattr(new_message, 'content', '')
            if isinstance(raw_content, list):
                text = " ".join([getattr(p, 'text', str(p))
                                for p in raw_content]).lower()
            else:
                text = str(raw_content).lower()
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"⚠️ Error extracting text: {e}")
            text = ""

        # If wake word mode is active, strictly filter and require name
        if self._wake_word_mode:
            # Use regex to find "jarvis" as a whole word
            pattern = r"\bjarvis\b"

            if not re.search(pattern, text, re.IGNORECASE):
                print(
                    f"🤫 Strict Mode: IGNORING speech (No Wake Word): '{text}'")
                # Explicitly stop the response and prevent further processing
                raise StopResponse()

            print(f"👂 Wake Word Detected in turn: '{text}'")
            # We keep the whole turn if Jarvis was mentioned
            new_message.content = text

            # --- Advanced Reasoning Integration ---
            try:
                # Retrieve semantic memory for the filtered text
                semantic_memory = await self.memory_extractor.memory.get_semantic_context(
                    query=text, n_results=3)

                if semantic_memory:
                    logger.info(
                        "🧠 Injecting Semantic Memory: %d gems found.", len(semantic_memory))
                    # Inject as a system note so the LLM has context but user doesn't see it as a message
                    turn_ctx.chat_ctx.append(
                        role="assistant",
                        content=f"[CONTEXT: Relevent past information: {'; '.join(semantic_memory)}]"
                    )
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.exception("⚠️ Reasoning injection error: %s", e)

            # Use super() but wrap in try-except for absolute certainty
            try:
                return await super().on_user_turn_completed(turn_ctx, new_message)
            except (StopResponse, asyncio.CancelledError):
                raise
            except Exception as e:
                print(f"⚠️ Error in turn completion: {e}")
                raise StopResponse() from e

        # NORMAL MODE logic (if wake word mode is OFF)
        return await super().on_user_turn_completed(turn_ctx, new_message)


# ==============================================================================
# ENTRYPOINT Function (Enhanced with brain.py features)
# ==============================================================================


async def start_memory_loop(session):
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
            print("Memory loop stopped.")
            break
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Memory loop error: {e}")
            await asyncio.sleep(10)


async def start_reminder_loop(session):
    """Check for due reminders and trigger proactive responses."""
    while True:
        try:
            # check_due_reminders is blocking (file IO), run in thread
            due = await asyncio.to_thread(check_due_reminders)
            for item in due:
                print(f"🔔 Triggering proactive reminder: {item['message']}")
                await session.response.create(
                    instruction=f"Sir ko proactively yaad dilayein (Natural Urdu main): '{item['message']}'"
                )
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            print("Reminder loop stopped.")
            break
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Reminder loop error: {e}")
            await asyncio.sleep(60)


async def entrypoint(ctx: agents.JobContext):
    """
    Main job entrypoint. Initializes session, handles greetings, and starts loops.
    """
    session: Optional[AgentSession] = None
    try:
        current_date = await get_formatted_datetime()
        current_city = await get_current_city()

        session = AgentSession(
            preemptive_generation=False,
            allow_interruptions=True,
        )

        await session.start(
            room=ctx.room,
            agent=BrainAssistant(
                chat_ctx=session.history,
                current_date=current_date.get("formatted"),
                current_city=current_city
            ),
        )

        # Register UI Notification Events
        @session.on("agent_started_speaking")  # type: ignore
        def _on_agent_speech_start():
            notify_ui("START")

        @session.on("agent_stopped_speaking")  # type: ignore
        def _on_agent_speech_stop():
            notify_ui("STOP")

        print("\n" + "="*50)
        print("🚀 JARVIS SYSTEMS ONLINE")
        print("🔱 Elite AI Assistant | Sir Matloob Edition")
        print("🛡️ Security: Enabled | 🧠 Brain: Active")
        print("🎤 Standing by for wake word: 'Jarvis'")
        print("="*50 + "\n")

        memory_task = asyncio.create_task(start_memory_loop(session))
        reminder_task = asyncio.create_task(start_reminder_loop(session))

        clip_monitor = ClipboardMonitor()

        async def on_clipboard_detected(solution):
            """Callback for clipboard error detection. Proactively triggers inference."""
            print(f"📋 Clipboard Detection: {solution}")
            # pylint: disable=no-member
            session.history.append(
                role="assistant",
                content=f"[SYSTEM NOTIFICATION: CLIPBOARD ERROR DETECTED]\n{solution}"
            )
            # Proactively trigger inference so JARVIS speaks the solution
            try:
                # pylint: disable=no-member
                session.inference()
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"⚠️ Failed to trigger proactive inference: {e}")

        clip_task = asyncio.create_task(
            clip_monitor.start(on_clipboard_detected))

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            print("🛑 Agent context cancelled. Cleaning up tasks...")
        finally:
            # Ensure all background tasks are cancelled properly
            tasks = [memory_task, clip_task, reminder_task]
            for t in tasks:
                t.cancel()

            # Wait for tasks to finish with a timeout to avoid hanging
            try:
                await asyncio.wait(tasks, timeout=3.0)
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"⚠️ Error during task wait: {e}")

            # Close clip monitor explicitly if it has a stop method
            if hasattr(clip_monitor, 'stop'):
                clip_monitor.stop()

    except (asyncio.CancelledError, KeyboardInterrupt):
        print("🛑 Received shutdown signal.")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[agent.entrypoint] Exception: {exc}")
    finally:
        if session:
            try:
                # Add check to see if loop is still running before stopping session
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    await session.stop()  # type: ignore # pylint: disable=no-member
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(f"⚠️ Session stop error: {e}")


# ==============================================================================
# MAIN RUNNER
# ==============================================================================
if __name__ == "__main__":
    opts = agents.WorkerOptions(entrypoint_fnc=entrypoint)
    agents.cli.run_app(opts)
