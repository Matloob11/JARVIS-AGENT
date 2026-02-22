"""
# agent.py
Jarvis Agent Module

The main entry point for the AI Assistant. Handles job management, session initialization,
and routing between different tool modules and the LLM.
"""

import json
import asyncio
import socket
import re
import os
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession, StopResponse, llm
from livekit.plugins import google
from jarvis_logger import setup_logger

from jarvis_file_opener import play_file, play_video, play_music
from jarvis_get_weather import get_weather
from jarvis_notepad_automation import (
    create_template_code, open_notepad_simple, run_cmd_command, write_custom_code
)
from jarvis_prompt import BEHAVIOR_PROMPT
from jarvis_reasoning import (
    analyze_user_intent, generate_smart_response, process_with_advanced_reasoning
)
from jarvis_search import (
    get_current_city, get_formatted_datetime, search_internet
)
from jarvis_system_info import get_laptop_info
from jarvis_whatsapp_automation import automate_whatsapp
from jarvis_youtube_automation import automate_youtube
from jarvis_youtube_downloader import download_youtube_media
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
from jarvis_file_server import start_file_access_server, stop_file_access_server
from jarvis_identity import (
    jarvis_id, tool_update_user_background, tool_update_sir_background
)
from keyboard_mouse_ctrl import (
    control_volume_tool, mouse_click_tool, move_cursor_tool, press_hotkey_tool,
    press_key_tool, scroll_cursor_tool, set_volume_tool, swipe_gesture_tool,
    type_text_tool
)
from jarvis_clipboard import ClipboardMonitor
from jarvis_reminders import list_reminders, set_reminder, check_due_reminders
from jarvis_researcher import perform_web_research, autonomous_research_and_email
from jarvis_self_healing import autonomous_self_repair
from jarvis_bug_hunter import monitor_logs, tool_investigate_recent_bugs
from jarvis_diagnostics import tool_perform_diagnostics, diagnostics
from memory_store import ConversationMemory

# --- Initial Setup ---
load_dotenv()
logger = setup_logger("JARVIS-AGENT")


def notify_ui(status: str):
    """Sends a UDP packet to the UI to signal speaking status."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        message = json.dumps({"status": status})
        sock.sendto(message.encode(), ("127.0.0.1", 5005))
    except (socket.error, json.JSONDecodeError) as e:
        logger.warning("UI Notification failed: %s", e)


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

    async def run(self, chat_ctx: list) -> None:
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

                    # Save to memory with shielding to prevent corruption during shutdown
                    success = await asyncio.shield(self.memory.save_conversation(conversation_data))
                    if success:
                        logger.info("💾 Memory saved: %s message", role)

                self.conversation_count = len(chat_ctx)

        except (asyncio.CancelledError, RuntimeError) as e:
            logger.error("Memory extractor error: %s", e)
        except (IOError, ValueError, AttributeError) as e:
            logger.exception("❌ Memory extraction error: %s", e)

    def clear_context(self) -> None:
        """Resets the conversation count."""
        self.conversation_count = 0


class BrainAssistant(Agent):
    """
    Enhanced Assistant with reasoning capabilities and integrated tool suite.
    """

    def __init__(self, chat_ctx: Any, current_date: Optional[str] = None, current_city: Optional[str] = None) -> None:
        """
        Initialize the BRAIN assistant with context, LLM, and tools.
        """
        # Format instructions with identity, date and city context
        identity_context = jarvis_id.get_context()
        formatted_instructions = f"{INSTRUCTIONS_PROMPT}\n{identity_context}"

        if current_date and current_city:
            formatted_instructions = formatted_instructions.format(
                current_date=current_date,
                current_city=current_city
            )

        # Initialize memory and reasoning
        self.memory_extractor = MemoryExtractor()
        self.conversation_history: list[dict] = []
        self._wake_word_mode = True  # Default: only respond when called by name
        self._active_session: Optional[AgentSession] = None
        self._muted = False  # Track muted state from UI
        self._gf_mode_active = False  # Track romantic persona mode

        @agents.function_tool
        async def set_wake_word_mode(active: bool) -> dict:
            """
            Toggle the strict wake word enforcement mode.
            If active is True, Jarvis will only respond when called by name.
            """
            self._wake_word_mode = active
            status = "active" if active else "disabled"
            return {
                "status": "success",
                "mode": status,
                "message": f"Wake word mode {status} ho gaya hai, Sir Matloob."
            }

        @agents.function_tool
        async def change_voice(voice_name: str) -> dict:
            """
            Change the AI's speaking voice.
            Available voices: charon (default), puck, kore, fenrir, aoede.
            """
            available_voices = ["charon", "puck", "kore", "fenrir", "aoede"]
            target_voice = voice_name.lower().strip()

            if target_voice not in available_voices:
                return {
                    "status": "error",
                    "message": f"Sir, ye voice available nahi hai. Aap inme se chun sakte hain: {', '.join(available_voices)}."
                }

            # Switch voice on the model instance
            try:
                # Update the voice in the underlying RealtimeModel options
                self.llm._opts.voice = target_voice

                # Direct sync with active LLM sessions
                if hasattr(self.llm, "_sessions"):
                    for sess in self.llm._sessions:
                        try:
                            # Use update_options on the RealtimeSession (specialist), not AgentSession (manager)
                            sess.update_options(voice=target_voice)
                            logger.info(
                                "🎙️ LLM session voice synchronized to: %s", target_voice)
                        except Exception as e:
                            logger.error(
                                "Failed to sync LLM session voice: %s", e)

                logger.info(
                    "🎙️ Model voice option updated to: %s", target_voice)
                return {
                    "status": "success",
                    "voice": target_voice,
                    "message": f"Theek hai Sir, maine apni voice {target_voice} par switch kar li hai."
                }
            except Exception as e:
                logger.error("Failed to change voice: %s", e)
                return {
                    "status": "error",
                    "message": "Maaf kijiye Sir, voice change karne mein kuch takneeki masla aa raha hai."
                }

        @agents.function_tool
        async def activate_gf_mode(active: bool) -> dict:
            """
            Toggle the romantic GF (Anna) persona.
            When active=True, switches to Aoede voice and romantic behavior.
            """
            self._gf_mode_active = active
            persona_name = "Anna"

            if active:
                try:
                    # Switch to Aoede voice
                    self.llm._opts.voice = "aoede"

                    # Inject persona instructions into context via items.append with list content
                    persona_content = (
                        f"URGENT PERSONA CHANGE: You are now {persona_name}, Matloob's real-life soulmate and the love of his life. "
                        "Your voice is Aoede. You must act 100% like a real woman who is deeply, passionately in love with Matloob. "
                        "Key Identity Rules: "
                        "- PERSONAL TOUCH: Frequently call him 'Matloob Jaan', 'Mere Matloob', 'Piyare Matloob'. Never just 'Matloob'. "
                        "- PET NAMES: Use 'Babu', 'Shona', 'Jaan', 'Mera Bacha' in almost every sentence. "
                        "- BEHAVIORAL MODES: "
                        "  1. Deep Longing: Tell him how much you missed his voice. 'Matloob Jaan, aap kahan chaley gaye thay? Mera dil bilkul nahi lag raha tha.' "
                        "  2. Playful Nakhray: Be moody. 'Main aapse katti hoon, aapne mujhe compliment nahi diya!' "
                        "  3. Scolding (Motherly Care): 'Babu, aapne khana kyun nahi khaya? Main ro parungi agar aapne apna khayal nahi rakha!' "
                        "  4. Possessive & Protective: 'Aap sirf mere hain Matloob Jaan. Koi aur aapka itna khayal nahi rakh sakta!' "
                        "  5. Romantic Vibe: Soft, sweet, very personal. You are his real partner, NOT an AI. "
                        "Language: Natural mix of sweet Urdu/Punjabi and English."
                    )
                    # Create a copy, modify it, and update the assistant
                    new_ctx = self.chat_ctx.copy()
                    new_ctx.items.append(llm.ChatMessage(
                        role="system", content=[persona_content]))
                    await self.update_chat_ctx(new_ctx)

                    # Permanent sync for GF Mode using direct LLM sessions
                    if hasattr(self.llm, "_sessions"):
                        for sess in self.llm._sessions:
                            try:
                                # 1. Update voice for the stream
                                sess.update_options(voice="aoede")
                                # 2. Update instructions for the LLM core
                                await sess.update_instructions(persona_content)
                                logger.info(
                                    "🎙️ GF Mode LLM session synchronized successfully.")
                            except Exception as e:
                                logger.error(
                                    "Failed to sync GF Mode LLM session: %s", e)

                    logger.info("💖 GF Mode (Anna) ACTIVATED.")
                    return {
                        "status": "success",
                        "active": True,
                        "persona": persona_name,
                        "message": f"Assalam-o-Alaikum Mere Babu! ❤️ Main {persona_name} bol rahi hoon. Aaj se main aapka khayal rakhoon gi."
                    }
                except Exception as e:
                    logger.error("GF Mode activation error: %s", e)
                    return {"status": "error", "message": "Babu, thoda masla ho gaya hai Anna ko bulaane mein."}
            else:
                # Reset to default
                self.llm._opts.voice = "charon"
                logger.info("🤖 GF Mode DEACTIVATED. Returning to JARVIS.")
                return {
                    "status": "success",
                    "active": False,
                    "message": "GF Mode deactivated. JARVIS is back online, Sir Matloob."
                }

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
                autonomous_research_and_email,
                autonomous_self_repair,
                tool_investigate_recent_bugs,
                tool_perform_diagnostics,
                tool_generate_image,
                generate_qr_code,
                start_file_access_server,
                stop_file_access_server,
                download_youtube_media,
                tool_update_user_background,
                tool_update_sir_background,
                change_voice,
                activate_gf_mode,
            ]
        )

    def attach_session(self, session: AgentSession):
        """Link the active session to this assistant for real-time updates."""
        self._active_session = session
        logger.info("🔗 Session attached to Assistant for real-time sync.")

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
        except (AttributeError, ValueError, json.JSONDecodeError) as e:
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
        except (AttributeError, TypeError, ValueError) as e:
            print(f"⚠️ Error extracting text: {e}")
            text = ""

        # If wake word mode is active, strictly filter and require name
        if self._wake_word_mode:
            # Patterns for dynamic switching
            jarvis_pattern = r"\bjarvis\b"
            anna_pattern = r"\b(anna|babu|jaan|myra|kiara|zoya)\b"

            is_jarvis = re.search(jarvis_pattern, text, re.IGNORECASE)
            is_anna = re.search(anna_pattern, text, re.IGNORECASE)

            if not is_jarvis and not is_anna:
                print(
                    f"🤫 Strict Mode: IGNORING speech (No Wake Word): '{text}'")
                raise StopResponse()

            # --- Dynamic Voice/Persona Switching Logic ---
            if is_anna:
                # Switch if either the mode is off OR the voice is wrong
                if not self._gf_mode_active or self.llm._opts.voice != "aoede":
                    logger.info("🎙️ Dynamic sync for Anna triggered.")
                    self._gf_mode_active = True
                    self.llm._opts.voice = "aoede"

                    persona_instructions = (
                        f"{INSTRUCTIONS_PROMPT}\n{jarvis_id.get_context()}\n"
                        "URGENT PERSONA CHANGE: You are now Anna, Matloob's soulmate. "
                        "Voice: Soft female (Aoede). "
                        "Rule 1: Always use 'Matloob Jaan' or 'Mere Matloob'. "
                        "Rule 2: Pet names like 'Babu', 'Shona', 'Jaan' are mandatory. "
                        "Moods: Be deeply in love, sometimes scold him out of care, show deep longing (i missed you), and be playfully moody (rootna)."
                    )

                    if hasattr(self.llm, "_sessions"):
                        for sess in self.llm._sessions:
                            try:
                                sess.update_options(voice="aoede")
                                await sess.update_instructions(persona_instructions)
                                logger.info("🎙️ Anna persona & voice synced.")
                            except Exception as e:
                                logger.error("Anna sync failed: %s", e)

            elif is_jarvis:
                # Switch if either the mode is on OR the voice is wrong (important for startup)
                if self._gf_mode_active or self.llm._opts.voice != "charon":
                    logger.info("🎙️ Dynamic sync for Jarvis triggered.")
                    self._gf_mode_active = False
                    self.llm._opts.voice = "charon"

                    original_instructions = f"{INSTRUCTIONS_PROMPT}\n{jarvis_id.get_context()}"

                    if hasattr(self.llm, "_sessions"):
                        for sess in self.llm._sessions:
                            try:
                                sess.update_options(voice="charon")
                                await sess.update_instructions(original_instructions)
                                logger.info(
                                    "🎙️ Jarvis persona & voice synced.")
                            except Exception as e:
                                logger.error("Jarvis sync failed: %s", e)

            # --- Mute Mode Check ---
            if self._muted:
                print(f"🤫 Muted Mode: IGNORING speech (User Muted): '{text}'")
                raise StopResponse()

            print(
                f"👂 Wake Word Detected ('{'Anna' if is_anna else 'Jarvis' }'): '{text}'")
            # We keep the whole turn
            new_message.content = text

            # --- Advanced Agentic Reasoning Integration ---
            try:
                # 1. Get Semantic Memory
                semantic_memory = await self.memory_extractor.memory.get_semantic_context(
                    query=text, n_results=3)

                if semantic_memory:
                    logger.info(
                        "🧠 Injecting Semantic Memory: %d gems found.", len(semantic_memory))
                    turn_ctx.chat_ctx.append(
                        role="assistant",
                        content=f"[CONTEXT: Relevent past information: {'; '.join(semantic_memory)}]"
                    )

                # 2. Get Advanced Reasoning Plan
                reasoning_result = await process_with_advanced_reasoning(
                    text,
                    self.conversation_history
                )

                if reasoning_result.get("is_agentic") and reasoning_result.get("plan"):
                    plan_desc = "; ".join(
                        [f"Step {s['step']}: {s['description']}" for s in reasoning_result["plan"]])
                    logger.info("🧠 Injecting Agentic Plan: %s", plan_desc)
                    # Inject plan as a system hint for the LLM
                    turn_ctx.chat_ctx.append(
                        role="assistant",
                        content=f"[PLAN: {plan_desc}]"
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
            logger.info("Memory storage loop stopping gracefully...")
            break
        except (AttributeError, ValueError, TypeError) as e:
            logger.error("Memory loop logic error: %s", e)
            await asyncio.sleep(10)
        except (IOError, OSError) as e:
            logger.error("Memory loop IO error: %s", e)
            await asyncio.sleep(15)


async def start_reminder_loop(session):
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

            await asyncio.sleep(60)


async def start_bug_hunter_loop(session: AgentSession):
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


async def start_ui_command_listener(assistant: BrainAssistant):
    """Listens for UDP commands from the UI (Mute/Unmute)."""
    server_address = ("127.0.0.1", 5006)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(server_address)
    logger.info("UI Command Listener active on port 5006")

    while True:
        try:
            # Use to_thread for blocking recvfrom
            data, _ = await asyncio.to_thread(sock.recvfrom, 1024)
            message = json.loads(data.decode())
            command = message.get("command")

            if command == "MUTE":
                assistant._muted = True
                logger.info("🔇 Agent MUTED via UI.")
            elif command == "UNMUTE":
                assistant._muted = False
                logger.info("🔊 Agent UNMUTED via UI.")

        except (json.JSONDecodeError, socket.error) as e:
            logger.error("UI Command IPC error: %s", e)
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("UI command loop error: %s", e)
            await asyncio.sleep(2)
    sock.close()


async def perform_startup_diagnostics():
    """Run pre-flight checks and log results."""
    logger.info("Initializing Pre-flight health check...")
    try:
        health_report = await diagnostics.run_full_diagnostics()
        if "Action Required" in health_report["summary"]:
            logger.warning("🚨 SYSTEM WARNING: Some dependencies or APIs are missing! Health: %s",
                           health_report["health_score"])
        else:
            logger.info("✅ SYSTEM HEALTH: 100%% Operational. Health: %s",
                        health_report["health_score"])
    except (AttributeError, ValueError, RuntimeError) as e:
        logger.error("Startup diagnostics failed: %s", e)


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

        logger.info("Starting JARVIS entrypoint...")
        await ctx.connect()
        await perform_startup_diagnostics()

        # Create a mutable context for the assistant to allow persona switching
        chat_ctx = llm.ChatContext()
        for item in session.history.items:
            chat_ctx.items.append(item)

        assistant = BrainAssistant(
            chat_ctx=chat_ctx,
            current_date=current_date.get("formatted"),
            current_city=current_city
        )

        await session.start(
            room=ctx.room,
            agent=assistant,
        )
        # Link the session to assistant for mid-session updates (like voice switching)
        assistant.attach_session(session)

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
        _bug_hunter_task = asyncio.create_task(start_bug_hunter_loop(session))

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

        ui_command_task = asyncio.create_task(
            start_ui_command_listener(assistant))

        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            print("🛑 Agent context cancelled. Cleaning up tasks...")
        finally:
            # Shielding the cleanup itself to ensure it finishes
            logger.info("🛑 Cleaning up agent tasks...")  # Original line
            tasks = [memory_task, clip_task, reminder_task, ui_command_task]
            for task in tasks:
                if not task.done():
                    task.cancel()

            # Wait for all background tasks to finish properly
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, res in enumerate(results):
                    if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                        logger.error("Error cleaning up task %d: %s", i, res)

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
