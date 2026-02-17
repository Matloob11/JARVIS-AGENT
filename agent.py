"""
# agent.py
Jarvis Agent Module

The main entry point for the AI Assistant. Handles job management, session initialization,
and routing between different tool modules and the LLM.
"""

import asyncio
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, StopResponse
from livekit.plugins import google

# --- Tool & Prompt Modules ---
from jarvis_file_opener import play_file
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
    sleep_system
)
from jarvis_vision import analyze_screen
from jarvis_rag import ask_about_document
from jarvis_advanced_tools import download_images, zip_files, send_email
from keyboard_mouse_ctrl import (
    control_volume_tool, mouse_click_tool, move_cursor_tool, press_hotkey_tool,
    press_key_tool, scroll_cursor_tool, set_volume_tool, swipe_gesture_tool,
    type_text_tool
)
from jarvis_clipboard import ClipboardMonitor

# --- Memory System ---
from memory_store import ConversationMemory

load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================
INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT  # Use same prompt as agent.py


class MemoryExtractor:
    """
    Handles extracting and saving conversation context to memory store.
    """

    def __init__(self, user_id: str = None):
        """
        Initialize the memory extractor with a user ID.
        """
        self.user_id = user_id or os.getenv("USER_NAME", "User")
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
                    success = self.memory.save_conversation(conversation_data)
                    if success:
                        print(f"💾 Memory saved: {role} message")

                self.conversation_count = len(chat_ctx)

        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"❌ Memory extraction error: {e}")

    def clear_context(self):
        """Placeholder to satisfy pylint R0903 (too-few-public-methods)."""
        self.conversation_count = 0


class BrainAssistant(Agent):
    """
    Enhanced Assistant with reasoning capabilities and integrated tool suite.
    """

    def __init__(self, chat_ctx, current_date: str = None, current_city: str = None) -> None:
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
        self.conversation_history = []
        self._wake_word_mode = True  # Default to True as per user request

        @agents.function_tool
        def set_wake_word_mode(active: bool) -> str:
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
                open_app,
                close,
                minimize_window,
                maximize_window,
                save_notepad,
                open_notepad_file,
                play_file,
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

            # Get relevant memory context
            memory_context = self.memory_extractor.memory.get_recent_context(
                max_messages=10)

            # Generate smart response with context
            smart_response = await generate_smart_response(
                user_input,
                intent_analysis,
                memory_context
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
            # Split by common sentence delimiters
            sentences = re.split(r'[.!?;]|\n', text)
            filtered_sentences = [s.strip()
                                  for s in sentences if "jarvis" in s]

            if not filtered_sentences:
                print(
                    f"🤫 Strict Mode: IGNORING speech (No Wake Word): '{text}'")
                # Explicitly stop the response and prevent further processing
                raise StopResponse()

            # Join filtered text back and replace message content
            filtered_text = ". ".join(filtered_sentences)
            print(f"👂 Filtered Wake Word Detected: '{filtered_text}'")
            new_message.content = filtered_text

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


async def entrypoint(ctx: agents.JobContext):
    """
    Main job entrypoint. Initializes session, handles greetings, and starts memory loop.
    """
    session = None
    try:
        # Detect environment context
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
                current_date=current_date,
                current_city=current_city
            ),
        )

        # Brain mode activated log (Internal only)
        print("🧠 Brain mode activated! System is quiet until 'Jarvis' is mentioned.")

        # Start continuous memory extraction loop
        memory_extractor = MemoryExtractor()

        async def memory_loop():
            while True:
                try:
                    if session is None or not hasattr(session, 'history'):
                        await asyncio.sleep(5)
                        continue

                    history_items = session.history.items

                    # Filter history to only include turns that have "Jarvis"
                    # OR turns where the assistant replied (to keep context consistent)
                    filtered_history = []
                    for item in history_items:
                        role = getattr(item, 'role', '').lower()
                        # Properly extract text from content (handling parts)
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
                except Exception as e:  # pylint: disable=broad-exception-caught
                    print(f"Memory loop error: {e}")
                    await asyncio.sleep(10)

        memory_task = asyncio.create_task(memory_loop())

        # Proactive Clipboard Monitor
        clip_monitor = ClipboardMonitor()

        async def on_clipboard_detected(solution):
            """Callback for clipboard error detection."""
            print(f"📋 Clipboard Detection: {solution}")
            # Add to history so LLM knows about it
            session.history.append(
                role="assistant",
                content=f"[SYSTEM NOTIFICATION: CLIPBOARD ERROR DETECTED]\n{solution}"
            )
            # Proactively speak/suggest if possible (Agent logic)
            # For now, printing to console and appending to ctx is the safest method
            # without disrupting active voice turns.

        clip_task = asyncio.create_task(
            clip_monitor.start(on_clipboard_detected))

        # Wait until the job is cancelled or room disconnected
        try:
            await asyncio.Event().wait()
        finally:
            memory_task.cancel()
            clip_task.cancel()
            try:
                await asyncio.gather(memory_task, clip_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[agent.entrypoint] Exception: {exc}")
    finally:
        if session:
            try:
                # Check if session is actually still running
                # session.stop() can error if the loop is already closing
                await session.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

# ==============================================================================
# MAIN RUNNER
# ==============================================================================
if __name__ == "__main__":
    try:
        # pylint: disable=unexpected-keyword-arg, no-value-for-parameter
        opts = agents.WorkerOptions(entrypoint_fnc=entrypoint)
    except TypeError:
        opts = agents.WorkerOptions(entrypoint=entrypoint)
    agents.cli.run_app(opts)
