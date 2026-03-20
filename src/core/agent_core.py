"""
# agent_core.py
Core logic for the BrainAssistant agent.
"""

# pylint: disable=protected-access, broad-exception-caught

import os
import re
import asyncio
import uuid
import time
import types   # For SimpleNamespace
from collections import defaultdict
from typing import Any, Optional
from datetime import datetime
from livekit.agents import Agent, AgentSession, StopResponse, llm
from livekit import rtc
from livekit.plugins import google

from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_prompt import BEHAVIOR_PROMPT
from services.ai_core.jarvis_reasoning import (
    process_with_advanced_reasoning
)
from services.ai_core.jarvis_plugin_manager import JarvisPluginManager, jarvis_tool
from services.ai_core.agent_memory import MemoryExtractor
from services.ai_core.jarvis_identity import jarvis_id
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_telemetry import telemetry
from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_security import vortex_guard
from services.utils.jarvis_audit import jarvis_audit
from services.ai_core.voice_fingerprint import voice_id_engine
try:
    from .bridge_notifier import bridge_notifier
    from .persona_manager import PersonaManager
    from .vision_handler import VisionHandler
except (ImportError, ValueError):
    from src.core.bridge_notifier import bridge_notifier
    from src.core.persona_manager import PersonaManager
    from src.core.vision_handler import VisionHandler

logger = setup_logger("JARVIS-CORE")
# pylint: disable=unused-import

INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT


# pylint: disable=too-many-instance-attributes
class BrainAssistant(Agent):
    """
    Enhanced Assistant with reasoning capabilities and integrated tool suite.
    """

    def __init__(self, chat_ctx: Any, current_date: Optional[str] = None,
                 current_city: Optional[str] = None, user_id: Optional[str] = None) -> None:
        """
        Initialize the BRAIN assistant with context, LLM, and tools.
        """
        identity_context = jarvis_id.get_context()
        prompt_with_info = f"{INSTRUCTIONS_PROMPT}\n{identity_context}"

        # Use format_map with defaultdict to safely handle any extra curly braces in prompt
        prompt_with_info = prompt_with_info.format_map(
            defaultdict(str, current_date=current_date or '', current_city=current_city or '')
        )

        # Resilient user_id initialization
        if user_id:
            self.user_id = user_id
        else:
            try:
                # In newer LiveKit agents, chat_ctx might be a SimpleNamespace or llm.ChatContext
                # Trying to find identity in various possible locations
                self.user_id = getattr(getattr(chat_ctx, 'participant', None), 'identity', None)
                if not self.user_id:
                    self.user_id = os.getenv("USER_NAME") or "User"
            except (AttributeError, KeyError):
                self.user_id = os.getenv("USER_NAME") or "User"
        self._last_user_speak_pulse = 0.0
        self.memory_extractor = MemoryExtractor(self.user_id)
        self.conversation_history: list[dict] = []
        self._wake_word_mode = True
        self._active_session: Optional[AgentSession] = None
        self._muted = False
        self._audio_buffer = bytearray()
        self._audio_sample_rate = 16000 # Default
        self._last_speaker_verified = True
        self.voice_id_engine = voice_id_engine

        # Session options for modality sync
        self._session_options = types.SimpleNamespace(
            voice_id='jarvis_v2',
            response_modality='audio'
        )

        self.plugin_manager = JarvisPluginManager()
        # Plugin discovery now points to the root-level services directory
        package_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'services'))
        self.plugin_manager.discover_plugins(package_path)

        # Initialize Modular Handlers
        self.persona_manager = PersonaManager(self)
        self.vision_handler = VisionHandler(self)
        self.bridge_notifier = bridge_notifier
        self._background_tasks = set()

        # Wrap internal tools to avoid 'self' in signature
        all_tools = self.plugin_manager.get_livekit_tools()

        async def wrap_set_wake_word_mode(active: bool) -> dict:
            return await self.tool_set_wake_word_mode(active)
        wrap_set_wake_word_mode.__doc__ = self.tool_set_wake_word_mode.__doc__

        async def wrap_change_voice(voice_name: str) -> dict:
            return await self.tool_change_voice(voice_name)
        wrap_change_voice.__doc__ = self.tool_change_voice.__doc__

        async def wrap_toggle_gf_mode(active: bool) -> dict:
            return await self.persona_manager.set_persona(active)
        wrap_toggle_gf_mode.__doc__ = self.tool_toggle_gf_mode.__doc__

        async def wrap_analyze_surroundings() -> dict:
            return await self.tool_analyze_surroundings()
        wrap_analyze_surroundings.__doc__ = self.tool_analyze_surroundings.__doc__

        all_tools.extend([
            llm.function_tool(wrap_set_wake_word_mode),
            llm.function_tool(wrap_change_voice),
            llm.function_tool(wrap_toggle_gf_mode),
            llm.function_tool(wrap_analyze_surroundings),
        ])

        super().__init__(
            chat_ctx=chat_ctx,
            instructions=prompt_with_info,
            llm=google.realtime.RealtimeModel(
                voice="charon", model="models/gemini-2.5-flash-native-audio-latest"),
            tools=all_tools
        )

    async def tool_toggle_gf_mode(self, active: bool) -> dict:
        """Toggle GF (Anna) persona mode."""
        return await self.persona_manager.set_persona(active)

    async def tool_set_wake_word_mode(self, active: bool) -> dict:
        """Toggle the strict wake word enforcement mode."""
        self._wake_word_mode = active
        status = "active" if active else "disabled"
        return {
            "status": "success",
            "mode": status,
            "message": f"Wake word mode {status} ho gaya hai, Sir Matloob."
        }

    async def tool_analyze_surroundings(self, query: str = "Describe the current camera view in detail.") -> dict:
        """
        Captures a frame from the system camera and performs advanced visual analytics.
        Use this when the user explicitly asks to 'analyze surroundings', 'what do you see', etc.
        """
        logger.info("🛠️ Tool: analyze_surroundings called with query: %s", query)
        
        # Perform direct analysis using the vision handler
        analysis = await self.vision_handler.analyze_current_frame(query)
        
        return {
            "status": "success",
            "message": f"Sir, maine camera view analyze kar liya hai. Report yeh hai:\n\n{analysis}",
            "analysis": analysis
        }

    async def tool_change_voice(self, voice_name: str) -> dict:
        """Change the AI's speaking voice."""
        valid_voices = ["alloy", "echo", "shimmer", "ash",
                        "ballad", "coral", "sage", "verse", "charon", "aoede"]
        if voice_name.lower() not in valid_voices:
            msg = f"Voice '{voice_name}' invalid. Use one of: {', '.join(valid_voices)}"
            return {"status": "error", "message": msg}

        self.llm.voice = voice_name.lower()

        # Update voice in the session if active
        if self._active_session:
            try:
                self._active_session.set_voice(voice_name.lower())
            except Exception as e:
                logger.error("Failed to update session voice: %s", e)
        return {"status": "success", "voice": voice_name.lower()}

    def _run_back(self, coro):
        """Helper to run a coroutine in background with tracking."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def set_muted(self, active: bool):
        """Set the muted state and manage audio track subscriptions."""
        self._muted = active
        if active:
            self._audio_buffer.clear()
            logger.info("🔇 Assistant MUTED. Audio buffer cleared.")
        else:
            logger.info("🔊 Assistant UNMUTED.")

        # If we have an active session, sync audio track subscriptions
        if self._active_session and self._active_session.room:
            for participant in self._active_session.room.remote_participants.values():
                for publication in participant.track_publications.values():
                    if publication.kind == rtc.TrackKind.KIND_AUDIO:
                        try:
                            publication.set_subscribed(not active)
                            logger.info("🎤 Audio track %s subscription set to: %s", 
                                        publication.sid, not active)
                        except Exception as e:
                            logger.error("Failed to update subscription for %s: %s", publication.sid, e)

        self._run_back(self.bridge_notifier.notify_event("mute_sync", active))

    def set_wake_word_mode(self, active: bool):
        """Toggle the strict wake word enforcement mode."""
        self._wake_word_mode = active
        status = "active" if active else "disabled"
        logger.info("Wake word mode set to: %s", status)
        self._run_back(self.bridge_notifier.notify_wake_word(active))

    @property
    def muted(self) -> bool:
        """Returns the current muted state."""
        return self._muted

    @property
    def wake_word_mode(self) -> bool:
        """Returns the current wake word mode."""
        return self._wake_word_mode

    def get_state(self) -> dict:
        """Returns a snapshot of the current agent state."""
        return {
            "muted": self._muted,
            "wake_word_active": self._wake_word_mode,
            "active_persona": self.persona_manager.active_persona_name,
            "voice": self.llm.voice,
            "modality": "audio" if self.persona_manager.is_anna else "text"
        }

    def attach_session(self, session: AgentSession):
        """Link the active session to this assistant."""
        self._active_session = session
        self.vision_handler.start_loop()

    async def process_with_reasoning(self, user_input: str) -> str:
        """Process user input with advanced reasoning pipeline."""
        session_id = str(uuid.uuid4())
        health_monitor.record_heartbeat("backend_core")
        try:
            telemetry.start_interaction(session_id, user_input)

            # Use unified reasoning pipeline
            logger.info("Analyzing input: '%s...'", user_input[:40])
            
            res = await process_with_advanced_reasoning(
                user_input,
                history=self.conversation_history,
                is_anna=self.persona_manager.is_anna
            )

            self._run_back(self.bridge_notifier.notify_reasoning(res))
            logger.info("Response generated via Cognitive Engine")
            telemetry.end_interaction(session_id, success=True)
            return res.get("generated_response", "Sir, main samajh gaya.")
        except Exception as e:
            logger.error("Cognitive Engine Error: %s", e)
            telemetry.end_interaction(session_id, success=False)
            return "Sir, main madad karne ke liye ready hun."

    async def _handle_persona_switch(self, is_anna, is_jarvis):
        """Logic to switch personas dynamically."""
        if is_anna and not self.persona_manager.is_anna:
            await self.persona_manager.set_persona(True)
        elif is_jarvis and self.persona_manager.is_anna:
            await self.persona_manager.set_persona(False)

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
        is_a = bool(re.search(r"\b(anna|babu|jaan|myra|kiara|zoya)\b",
                              text, re.IGNORECASE))
        if not is_j and not is_a:
            return False, False
        await self._handle_persona_switch(is_a, is_j)
        return True, is_a



    async def _inject_memory_context(self, text: str, turn_ctx: Any, is_anna: bool):
        """Retrieves and injects long-term memory into chat context."""
        n_results = 5 if is_anna else 3
        raw_sem = await self.memory_extractor.memory.get_semantic_context(
            query=text, n_results=n_results)

        threshold = 0.55 if is_anna else 0.65
        filtered_sem = [m["document"]
                        for m in raw_sem if m["score"] >= threshold]

        if filtered_sem:
            context_str = "\n".join(filtered_sem)
            turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                role="system", content=[f"[LONG-TERM MEMORY CONTEXT]: {context_str}"]))
            logger.info("🧠 Scored Memory Injected: %d items",
                        len(filtered_sem))

    async def _inject_reasoning_and_memory(self, text: str, turn_ctx: Any,
                                           is_anna: bool = False):
        """Injects reasoning and memory with confidence filtering."""
        try:
            await self._inject_memory_context(text, turn_ctx, is_anna)
            res = await process_with_advanced_reasoning(
                text, self.conversation_history, is_anna=is_anna)

            asyncio.create_task(self.bridge_notifier.notify_reasoning(res))
            if res.get("is_agentic") and res.get("plan"):
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="system", content=[f"[EXECUTION PLAN]: {res['plan']}"]))
        except Exception as e:
            logger.error("Reasoning injection error: %s", e)

    async def _notify_ui_persona(self, persona: str):
        """Sends notification to UI Bridge about persona change."""
        await self.bridge_notifier.notify_persona(persona)

    async def handle_user_query(self, query: str) -> str:
        """EntryPoint for user queries."""
        start_time = datetime.now()
        sanitized = vortex_guard.sanitize_input(query)
        threat = vortex_guard.detect_threat(sanitized)

        if threat:
            logger.critical("🚨 Security Threat Blocked: %s", threat)
            jarvis_audit.log_threat(threat, source="UserQuery")
            return f"I detected a security risk: {threat}."

        response = await self.process_with_reasoning(sanitized)
        duration = (datetime.now() - start_time).total_seconds()
        jarvis_audit.log_event("QUERY_PROCESSED", f"Duration={duration:.2f}s")
        return response

    def add_audio_frame(self, frame: rtc.AudioFrame):
        """Accumulates audio frames for speaker verification."""
        if self._muted:
            return

        # Update sample rate from incoming frame
        if hasattr(frame, "sample_rate"):
            self._audio_sample_rate = frame.sample_rate

        # Notify UI about user speaking (simple pulse detection)
        now = time.time()
        if not hasattr(self, "_last_user_speak_pulse"):
            self._last_user_speak_pulse = 0
            
        if now - self._last_user_speak_pulse > 0.5:
            # If frame contains non-zero data, consider user speaking
            has_audio = any(v != 0 for v in frame.data[:100]) # Quick check
            if has_audio:
                asyncio.create_task(self.bridge_notifier.notify_user_speaking(True))
                self._last_user_speak_pulse = now

        self._audio_buffer.extend(frame.data)
        # Keep only last 10 seconds of audio (approx 320k bytes for 16kHz mono)
        if len(self._audio_buffer) > 320000:
            bytes_to_keep = 320000
            self._audio_buffer = self._audio_buffer[-bytes_to_keep:]

    async def verify_speaker_identity(self) -> bool:
        """Verifies if the current audio buffer matches the enrolled user."""
        # SECURE BY DEFAULT: If no audio is captured, it is NOT verified.
        if not self._audio_buffer:
            logger.warning("🛡️ Voice ID: No audio captured in buffer. ACCESS DENIED.")
            return False

        # Require at least 1.0 seconds of audio for a reliable check
        # (1.0s * 16000 samples/s * 2 bytes/sample = 32000 bytes)
        if len(self._audio_buffer) < 32000:
            logger.warning("🛡️ Voice ID: Audio segment too short for verification. ACCESS DENIED.")
            return False

        # Take the accumulated buffer and verify
        # Threshold 0.70 is stricter for "Matloob Boss" voice authorization
        audio_bytes = bytes(self._audio_buffer)
        is_match, score = await self.voice_id_engine.verify_bytes(audio_bytes, threshold=0.70)
        logger.info("🛡️ Voice Security Check: Score=%.4f | Threshold=0.70 | Match=%s", score, is_match)

        self._last_speaker_verified = is_match
        percentage = score * 100

        if is_match:
            logger.info("🛡️ Voice ID Match: %.2f%% - ACCESS GRANTED", percentage)
        else:
            logger.warning("🛡️ Voice ID Match: %.2f%% - ACCESS DENIED", percentage)

        self._audio_buffer.clear() # Reset for next turn
        return is_match

    # pylint: disable=too-many-locals
    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Called when user turn completed with full system integration."""
        text = self._extract_text_from_message(new_message)
        sanitized_text = vortex_guard.sanitize_input(text)
        threat = vortex_guard.detect_threat(sanitized_text)

        if threat:
            logger.warning("🚨 Security Threat Blocked: %s", threat)
            jarvis_audit.log_threat(threat, source="VoiceStream")
            raise StopResponse()

        # 1. Mute Check (Instant abort if system is muted via UI)
        if self._muted:
            logger.info("🔇 Turn aborted: System is currently MUTED via UI.")
            raise StopResponse()

        # 2. Strict Voice Identification (Matloob Boss Only)
        is_authorized = await self.verify_speaker_identity()
        if not is_authorized:
            logger.warning("⛔ VOICE REFUSED: Authorized Voice Pattern (Matloob Boss) NOT FOUND. Shutting down turn.")
            self._run_back(self.bridge_notifier.notify_thinking("REJECTED"))
            raise StopResponse()

        logger.info("✅ IDENTITY VERIFIED: Access Granted to Matloob Boss.")

        if self._wake_word_mode:
            detected, is_anna = await self._handle_wake_word(sanitized_text)
            if not detected:
                raise StopResponse()
        else:
            is_anna = False

        self._run_back(self.bridge_notifier.notify_thinking("START"))
        if self._muted:
            raise StopResponse()

        try:
            await self.persona_manager.handle_anna_upset_state(sanitized_text, turn_ctx)
            await self.persona_manager.inject_emotional_context(sanitized_text, turn_ctx, self.conversation_history)
            await self.vision_handler.handle_vision_query(sanitized_text, new_message, turn_ctx)
            await self.vision_handler.inject_window_context(turn_ctx)
            await self._inject_reasoning_and_memory(sanitized_text, turn_ctx, is_anna)

            self.conversation_history.append(
                {"role": "user", "content": sanitized_text})
            if len(self.conversation_history) > 20:
                self.conversation_history.pop(0)

            health_monitor.check_anomalies()
            # Super call for legacy tool execution
            response = await super().on_user_turn_completed(turn_ctx, new_message)

            # Telemetry is handled in the underlying handle_user_query / process_with_reasoning
            # But we log final UI status here
            asyncio.create_task(adaptive_engine.log_interaction(
                "BOT_TURN", sanitized_text, str(response), success=True))
            return response
        except StopResponse:
            # Clean lifecycle control — re-raise as-is
            raise
        except (ValueError, RuntimeError, AttributeError, TypeError) as e:
            logger.error("Turn Error (recoverable): %s", e, exc_info=True)
            telemetry.end_interaction("ERROR_TURN", success=False)
            raise StopResponse() from e
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.critical("Turn Error (unexpected): %s", e, exc_info=True)
            telemetry.end_interaction("ERROR_TURN", success=False)
            raise StopResponse() from e

    async def shutdown(self):
        """Gracefully shuts down the assistant and its modular services."""
        logger.info("Assistant shutting down...")
        # 1. Stop Vision Loop
        await self.vision_handler.stop_loop()
        # 2. Close Bridge Notifier client
        await self.bridge_notifier.aclose()
        # 3. Cancel background tasks
        if self._background_tasks:
            logger.info("Cancelling %d background tasks...", len(self._background_tasks))
            for task in self._background_tasks:
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        # 4. Clear state
        self._active_session = None
        logger.info("Assistant offline.")


if __name__ == "__main__":
    print("✅ BrainAssistant class loaded successfully.")
