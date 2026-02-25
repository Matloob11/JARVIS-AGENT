"""
# agent_core.py
Core logic for the BrainAssistant agent.
"""

# pylint: disable=protected-access, broad-exception-caught

import re
import asyncio
import json
from typing import Any, Optional

from livekit.agents import Agent, AgentSession, StopResponse, llm
from livekit.plugins import google

from jarvis_logger import setup_logger
from jarvis_prompt import BEHAVIOR_PROMPT, ANNA_BEHAVIOR_PROMPT
from jarvis_reasoning import (
    analyze_user_intent, generate_smart_response, process_with_advanced_reasoning,
    context_analyzer
)
from jarvis_search import (
    get_formatted_datetime, search_internet
)
from jarvis_get_weather import get_weather
from jarvis_notepad_automation import (
    create_template_code, open_notepad_simple, run_cmd_command, write_custom_code
)
from jarvis_window_ctrl import (
    close, create_folder, lock_screen, maximize_window, minimize_window,
    open_app, open_notepad_file, restart_system, save_notepad, shutdown_system,
    sleep_system, folder_file, open_outputs_folder
)
from jarvis_file_opener import play_file, play_video, play_music
from jarvis_system_info import get_laptop_info
from jarvis_whatsapp_automation import automate_whatsapp
from jarvis_youtube_automation import automate_youtube
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
from jarvis_reminders import list_reminders, set_reminder
from jarvis_researcher import perform_web_research, autonomous_research_and_email
from jarvis_self_healing import autonomous_self_repair
from jarvis_bug_hunter import tool_investigate_recent_bugs
from jarvis_diagnostics import tool_perform_diagnostics
from jarvis_youtube_downloader import download_youtube_media
from agent_memory import MemoryExtractor

logger = setup_logger("JARVIS-CORE")

INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT


class BrainAssistant(Agent):
    """
    Enhanced Assistant with reasoning capabilities and integrated tool suite.
    """

    def __init__(self, chat_ctx: Any, current_date: Optional[str] = None,
                 current_city: Optional[str] = None) -> None:
        """
        Initialize the BRAIN assistant with context, LLM, and tools.
        """
        identity_context = jarvis_id.get_context()
        prompt_with_info = f"{INSTRUCTIONS_PROMPT}\n{identity_context}"

        if current_date and current_city:
            prompt_with_info = prompt_with_info.format(
                current_date=current_date,
                current_city=current_city
            )

        self.memory_extractor = MemoryExtractor()
        self.conversation_history: list[dict] = []
        self._wake_word_mode = True
        self._active_session: Optional[AgentSession] = None
        self._muted = False
        self._gf_mode_active = False

        super().__init__(
            chat_ctx=chat_ctx,
            instructions=prompt_with_info,
            llm=google.realtime.RealtimeModel(
                voice="charon", model="models/gemini-2.5-flash-native-audio-latest"),
            tools=[
                search_internet, get_formatted_datetime, get_weather,
                create_template_code, write_custom_code, run_cmd_command,
                open_notepad_simple, shutdown_system, restart_system,
                sleep_system, lock_screen, create_folder, folder_file,
                open_outputs_folder, open_app, close, minimize_window,
                maximize_window, save_notepad, open_notepad_file,
                play_file, play_video, play_music, get_laptop_info,
                automate_whatsapp, move_cursor_tool, mouse_click_tool,
                scroll_cursor_tool, type_text_tool, press_key_tool,
                press_hotkey_tool, control_volume_tool, set_volume_tool,
                swipe_gesture_tool, automate_youtube,
                analyze_screen, ask_about_document, download_images,
                zip_files, send_email, set_reminder, list_reminders,
                perform_web_research, autonomous_research_and_email,
                autonomous_self_repair, tool_investigate_recent_bugs,
                tool_perform_diagnostics, tool_generate_image,
                generate_qr_code, start_file_access_server,
                stop_file_access_server, download_youtube_media,
                tool_update_user_background, tool_update_sir_background,
                llm.function_tool(self.tool_set_wake_word_mode),
                llm.function_tool(self.tool_change_voice),
                llm.function_tool(self.tool_toggle_gf_mode),
            ]
        )

    # ... Rest of BrainAssistant methods ...
    async def tool_set_wake_word_mode(self, active: bool) -> dict:
        """Toggle the strict wake word enforcement mode."""
        self._wake_word_mode = active
        status = "active" if active else "disabled"
        return {
            "status": "success",
            "mode": status,
            "message": f"Wake word mode {status} ho gaya hai, Sir Matloob."
        }

    async def tool_change_voice(self, voice_name: str) -> dict:
        """Change the AI's speaking voice."""
        valid_voices = ["alloy", "echo", "shimmer", "ash",
                        "ballad", "coral", "sage", "verse", "charon", "aoede"]
        if voice_name.lower() not in valid_voices:
            return {"status": "error", "message": f"Voice '{voice_name}' invalid. Use one of: {', '.join(valid_voices)}"}

        # Update voice in the session if active
        if self._active_session:
            try:
                # Accessing internal RealtimeSession for dynamic voice change
                if hasattr(self._active_session, "_activity") and self._active_session._activity:
                    rt_session = getattr(
                        self._active_session._activity, "_rt_session", None)
                    if rt_session:
                        rt_session.update_options(voice=voice_name.lower())
                        logger.info(f"Voice changed to: {voice_name}")
            except Exception as e:
                logger.error(f"Failed to change voice dynamically: {e}")

        return {
            "status": "success",
            "voice": voice_name,
            "message": f"Voice change karkay '{voice_name}' kar di gai hai, Sir."
        }

    async def _update_persona_instructions(self):
        """Generates and applies the correct instructions for the active persona."""
        if self._gf_mode_active:
            state = jarvis_id.get_anna_state()
            instr = ANNA_BEHAVIOR_PROMPT.format(
                mood=state["mood"],
                is_upset=state["is_upset"],
                user_background=jarvis_id.data.get("user_background")
            )
            voice = "aoede"
        else:
            instr = f"{BEHAVIOR_PROMPT}\n{jarvis_id.get_context()}"
            voice = "charon"

        # The session will handle instruction updates via update_instructions
        # Update current prompt using the standard method
        await self.update_instructions(instr)

        if self._active_session:
            try:
                # Note: Voice update still requires reaching into the underlying RealtimeSession
                # as AgentSession doesn't support 'voice' in update_options.
                if hasattr(self._active_session, "_activity") and self._active_session._activity:
                    rt_session = getattr(
                        self._active_session._activity, "_rt_session", None)
                    if rt_session:
                        logger.info(f"Setting session voice to: {voice}")
                        rt_session.update_options(voice=voice)
            except Exception as e:
                logger.warning(f"Failed to sync persona/voice: {e}")

    async def tool_toggle_gf_mode(self, active: bool) -> dict:
        """Toggle the romantic GF (Anna) persona."""
        self._gf_mode_active = active
        await self._update_persona_instructions()
        msg = "Anna activate ho gayi hain, Sir." if active else "Jarvis wapas aa gaya hai, Sir."
        return {"status": "success", "active": active, "message": msg}

    def attach_session(self, session: AgentSession):
        """Link the active session to this assistant."""
        self._active_session = session

    async def process_with_reasoning(self, user_input: str) -> str:
        """Process user input with advanced reasoning."""
        try:
            intent = await analyze_user_intent(user_input)
            mem = await self.memory_extractor.memory.get_recent_context(max_messages=10)
            sem = await self.memory_extractor.memory.get_semantic_context(query=user_input, n_results=3)
            return await generate_smart_response(user_input, intent, mem, sem)
        except (AttributeError, ValueError, json.JSONDecodeError):
            return "Sir, main samajh gaya."

    async def _handle_persona_switch(self, is_anna, is_jarvis):
        """Logic to switch personas dynamically."""
        if is_anna and not self._gf_mode_active:
            self._gf_mode_active = True
            await self._update_persona_instructions()
        elif is_jarvis and self._gf_mode_active:
            self._gf_mode_active = False
            await self._update_persona_instructions()

    def _extract_text_from_message(self, message: Any) -> str:
        """Safely extracts text."""
        try:
            raw = getattr(message, 'content', '')
            if isinstance(raw, list):
                return " ".join([getattr(p, 'text', str(p)) for p in raw]).lower()
            return str(raw).lower()
        except (AttributeError, TypeError, ValueError):
            return ""

    async def _handle_wake_word(self, text: str) -> tuple[bool, bool]:
        """Checks for wake words."""
        is_j = bool(re.search(r"\bjarvis\b", text, re.IGNORECASE))
        is_a = bool(
            re.search(r"\b(anna|babu|jaan|myra|kiara|zoya)\b", text, re.IGNORECASE))
        if not is_j and not is_a:
            return False, False
        await self._handle_persona_switch(is_a, is_j)
        return True, is_a

    async def _inject_emotional_context(self, text: str, turn_ctx: Any):
        """Injects emotional hints."""
        try:
            mem = await self.memory_extractor.memory.get_recent_context(max_messages=5)
            curr = context_analyzer.analyze_context(text, mem)
            if self._gf_mode_active and curr.get("user_mood") == "upset":
                turn_ctx.chat_ctx.items.append(llm.ChatMessage(
                    role="assistant", content="Manao him."))
        except Exception as e:
            logger.exception("Reasoning error: %s", e)

    async def _handle_anna_upset_state(self, text: str, turn_ctx: Any):
        """Processes Anna's upset state."""
        if not self._gf_mode_active:
            return
        state = jarvis_id.get_anna_state()
        if not state.get("is_upset"):
            return
        if "sorry" in text.lower():
            await jarvis_id.set_anna_mood("loving", False)
            await self.tool_toggle_gf_mode(True)
        else:
            turn_ctx.chat_ctx.items.append(llm.ChatMessage(
                role="assistant", content="Demand sorry."))

    async def _inject_reasoning_and_memory(self, text: str, turn_ctx: Any, is_anna: bool):
        """Injects reasoning and memory."""
        try:
            sem = await self.memory_extractor.memory.get_semantic_context(query=text, n_results=3)
            if sem:
                turn_ctx.chat_ctx.items.append(llm.ChatMessage(
                    role="system", content=f"[LONG-TERM MEMORY CONTEXT]: {sem}"))
            res = await process_with_advanced_reasoning(text, self.conversation_history, is_anna=is_anna)
            if res.get("is_agentic") and res.get("plan"):
                turn_ctx.chat_ctx.items.append(llm.ChatMessage(
                    role="system", content=f"[EXECUTION PLAN]: {res['plan']}"))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Reasoning error: %s", e)

    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Called when user turn completed."""
        text = self._extract_text_from_message(new_message)
        if self._wake_word_mode:
            detected, is_a = await self._handle_wake_word(text)
            if not detected:
                raise StopResponse()
            await self._handle_anna_upset_state(text, turn_ctx)
            if self._muted:
                raise StopResponse()
            await self._inject_emotional_context(text, turn_ctx)
            new_message.content = text
            await self._inject_reasoning_and_memory(text, turn_ctx, is_anna=is_a)
            self.conversation_history.append({"role": "user", "content": text})
            if len(self.conversation_history) > 20:
                self.conversation_history.pop(0)
            try:
                return await super().on_user_turn_completed(turn_ctx, new_message)
            except (StopResponse, asyncio.CancelledError):
                raise
            except Exception as e:
                logger.error("Turn error: %s", e)
                raise StopResponse() from e
        return await super().on_user_turn_completed(turn_ctx, new_message)
