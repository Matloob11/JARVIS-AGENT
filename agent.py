"""
# agent.py
Jarvis Agent Module

The main entry point for the AI Assistant. Handles job management, session initialization,
and routing between different tool modules and the LLM.
"""

import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession
from livekit.plugins import google

# --- Tool & Prompt Modules ---
from jarvis_file_opener import play_file
from jarvis_get_weather import get_weather
from jarvis_notepad_automation import (
    create_template_code, open_notepad_simple, run_cmd_command, write_custom_code
)
from jarvis_prompt import REPLY_PROMPTS, BEHAVIOR_PROMPT
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
from keyboard_mouse_ctrl import (
    control_volume_tool, mouse_click_tool, move_cursor_tool, press_hotkey_tool,
    press_key_tool, scroll_cursor_tool, set_volume_tool, swipe_gesture_tool,
    type_text_tool
)

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

        super().__init__(
            chat_ctx=chat_ctx,
            instructions=formatted_instructions,
            llm=google.realtime.RealtimeModel(
                voice="charon", model="gemini-2.5-flash-native-audio-latest"),
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
            ]
        )

        # Initialize memory and reasoning
        self.memory_extractor = MemoryExtractor()
        self.conversation_history = []

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
            llm=google.realtime.RealtimeModel(
                voice="charon", model="gemini-2.5-flash-native-audio-latest"),
            preemptive_generation=False,
            allow_interruptions=True,
        )

        await session.start(
            room=ctx.room,
            agent=BrainAssistant(
                chat_ctx=session.history.items if hasattr(
                    session, 'history') else [],
                current_date=current_date,
                current_city=current_city
            ),
        )

        # Send initial greeting
        hour = datetime.now().hour
        greeting_prefix = (
            "Good morning!" if 5 <= hour < 12 else
            "Good afternoon!" if 12 <= hour < 18 else
            "Good evening!"
        )
        intro = f"{greeting_prefix}\n{REPLY_PROMPTS}\n🧠 Brain mode activated!"
        await session.generate_reply(instructions=intro)

        # Start continuous memory extraction loop
        memory_extractor = MemoryExtractor()

        async def memory_loop():
            while True:
                try:
                    history_items = session.history.items if hasattr(
                        session, 'history') else []
                    await memory_extractor.run(history_items)
                    await asyncio.sleep(5)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    print(f"Memory loop error: {e}")
                    await asyncio.sleep(10)

        memory_task = asyncio.create_task(memory_loop())

        # Wait until the job is cancelled or room disconnected
        try:
            await asyncio.Event().wait()
        finally:
            memory_task.cancel()
            try:
                await memory_task
            except asyncio.CancelledError:
                pass

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[agent.entrypoint] Exception: {exc}")
    finally:
        if session:
            try:
                await session.stop()  # pylint: disable=no-member
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
        # pylint: disable=unexpected-keyword-arg
        opts = agents.WorkerOptions(entrypoint=entrypoint)
    agents.cli.run_app(opts)
