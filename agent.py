import sys
import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession, RoomInputOptions, ChatContext, ChatMessage
from livekit.plugins import google, noise_cancellation

# Import your prompt and tool modules (same as agent.py)
from jarvis_prompt import behavior_prompt, Reply_prompts, get_current_city
from jarvis_search import search_internet, get_formatted_datetime
from jarvis_get_weather import get_weather
from jarvis_notepad_automation import (
    create_template_code, write_custom_code, run_cmd_command, open_notepad_simple
)
from Jarvis_window_CTRL import (
    shutdown_system, restart_system, sleep_system, lock_screen, create_folder,
    open, close, minimize_window, maximize_window, save_notepad, open_notepad_file
)
from Jarvis_file_opener import Play_file
from jarvis_system_info import get_laptop_info
from jarvis_whatsapp_automation import automate_whatsapp
from keyboard_mouse_CTRL import (
    move_cursor_tool, mouse_click_tool, scroll_cursor_tool, type_text_tool,
    press_key_tool, press_hotkey_tool, control_volume_tool, swipe_gesture_tool,
    set_volume_tool,
)
from jarvis_youtube_automation import automate_youtube

# Import memory and reasoning modules
from memory_store import ConversationMemory
from jarvis_reasoning import analyze_user_intent, generate_smart_response

load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================
instructions_prompt = behavior_prompt  # Use same prompt as agent.py

class MemoryExtractor:
    def __init__(self, user_id=None):
        self.user_id = user_id or os.getenv("USER_NAME", "User")
        self.memory = ConversationMemory(self.user_id)
        self.conversation_count = 0

    async def run(self, chat_ctx):
        """
        The main loop that checks for and saves new conversations.
        """
        try:
            # Save current conversation context
            if chat_ctx and len(chat_ctx) > self.conversation_count:
                new_messages = chat_ctx[self.conversation_count:]
                
                for message in new_messages:
                    # Create conversation wrapper
                    # Convert ChatMessage to dict if needed
                    msg_content = message.content if hasattr(message, 'content') else str(message)
                    role = message.role if hasattr(message, 'role') else "unknown"
                    
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
                
        except Exception as e:
            print(f"❌ Memory extraction error: {e}")

# ==============================================================================
# ENHANCED ASSISTANT CLASS WITH BRAIN CAPABILITIES
# ==============================================================================
class BrainAssistant(Agent):
    def __init__(self, chat_ctx, current_date: str = None, current_city: str = None) -> None:
        # Format instructions with date and city if provided (like agent.py)
        formatted_instructions = instructions_prompt
        if current_date and current_city:
            formatted_instructions = instructions_prompt.format(
                current_date=current_date,
                current_city=current_city
            )
        
        super().__init__(
            chat_ctx=chat_ctx,
            instructions=formatted_instructions,
            llm=google.realtime.RealtimeModel(voice="charon", model="gemini-2.5-flash-native-audio-latest"),
            tools=[
                # Basic Tools
                search_internet,
                get_formatted_datetime,
                get_weather,
                
                # Notepad & Code Automation - COMPLETE SYSTEM
                create_template_code,
                write_custom_code,
                run_cmd_command,
                open_notepad_simple,

                # System Tools - Reactivated
                shutdown_system,
                restart_system,
                sleep_system,
                lock_screen,
                create_folder,
                open,
                close,
                minimize_window,
                maximize_window,
                save_notepad,
                open_notepad_file,
                Play_file,
                get_laptop_info,
                automate_whatsapp,

                # Keyboard & Mouse Control - WORKING TOOLS
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
            memory_context = self.memory_extractor.memory.get_recent_context(max_messages=10)
            
            # Generate smart response with context
            smart_response = await generate_smart_response(
                user_input, 
                intent_analysis, 
                memory_context
            )
            
            return smart_response
            
        except Exception as e:
            print(f"❌ Reasoning error: {e}")
            user_name = os.getenv("USER_NAME", "Sir")
            return f"{user_name}, main aapka message samajh gaya hun. Kya main aapki madad kar sakta hun?"

# ==============================================================================
# ENTRYPOINT Function (Enhanced with brain.py features)
# ==============================================================================
async def entrypoint(ctx: agents.JobContext):
    session = None
    try:
        # Get current date and city like agent.py
        current_date = await get_formatted_datetime()
        current_city = await get_current_city()

        session = AgentSession(
            llm=google.realtime.RealtimeModel(voice="charon", model="gemini-2.5-flash-native-audio-latest"),
            preemptive_generation=False,
            allow_interruptions=True,
        )

        # Get current chat context
        current_ctx = session.history.items if hasattr(session, 'history') else []

        await session.start(
            room=ctx.room,
            agent=BrainAssistant(
                chat_ctx=current_ctx,
                current_date=current_date,
                current_city=current_city
            ),
        )

        # Enhanced greeting with brain capabilities
        hour = datetime.now().hour
        if Reply_prompts:
            greeting = (
                "Good morning!" if 5 <= hour < 12 else
                "Good afternoon!" if 12 <= hour < 18 else
                "Good evening!"
            )
            intro = f"{greeting}\n{Reply_prompts}\n🧠 Brain mode activated - Advanced reasoning enabled!"
            await session.generate_reply(instructions=intro)

        # Use existing memory extractor from agent
        memory_extractor = ctx.agent.memory_extractor if hasattr(ctx, 'agent') and hasattr(ctx.agent, 'memory_extractor') else MemoryExtractor()
        
        # Continuous memory extraction loop
        async def memory_loop():
            while True:
                try:
                    current_ctx = session.history.items if hasattr(session, 'history') else []
                    await memory_extractor.run(current_ctx)
                    await asyncio.sleep(5)  # Check every 5 seconds
                except Exception as e:
                    print(f"Memory loop error: {e}")
                    await asyncio.sleep(10)
        
        # Start memory loop in background
        memory_task = asyncio.create_task(memory_loop())

        # Wait until cancelled
        await asyncio.Event().wait()
        
        # Cancel memory task
        memory_task.cancel()

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print("[brain.entrypoint] Exception:", exc)
    finally:
        if session:
            try:
                await session.stop()
            except Exception:
                pass

# ==============================================================================
# MAIN RUNNER
# ==============================================================================
if __name__ == "__main__":
    # If no command is provided, default to 'dev'
    if len(sys.argv) <= 1:
        sys.argv.append("dev")
        
    try:
        opts = agents.WorkerOptions(entrypoint=entrypoint)
    except TypeError:
        # Fallback for older versions (same as agent.py)
        opts = agents.WorkerOptions(entrypoint_fnc=entrypoint)
    agents.cli.run_app(opts)