"""
# agent_core.py
Core logic for the BrainAssistant agent.
"""

# pylint: disable=protected-access, broad-exception-caught

import os
import re
import asyncio
import json
import uuid
from typing import Any, Optional
from datetime import datetime
import httpx
from livekit.agents import Agent, AgentSession, StopResponse, llm
from livekit import rtc
from livekit.plugins import google

from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_prompt import BEHAVIOR_PROMPT, ANNA_BEHAVIOR_PROMPT
from services.ai_core.jarvis_reasoning import (
    process_with_advanced_reasoning, context_analyzer
)
from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
from services.ai_core.agent_memory import MemoryExtractor
from services.ai_core.jarvis_identity import jarvis_id
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_telemetry import telemetry
from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_security import vortex_guard, security_manager
from services.utils.jarvis_audit import jarvis_audit
# from services.ai_core.autonomous_planner import tool_report_plan_progress (Removed as unused)
from services.system.jarvis_window_ctrl import get_active_window_context
from services.ai_core.voice_fingerprint import voice_id_engine

logger = setup_logger("JARVIS-CORE")
# pylint: disable=unused-import

INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT


# pylint: disable=too-many-instance-attributes
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
        self.last_vision_frame = None
        self.active_window_context = {}
        self._proactive_vision_task: Optional[asyncio.Task] = None
        self._audio_buffer = bytearray()
        self._audio_sample_rate = 16000 # Default
        self._last_speaker_verified = True
        self.voice_id_engine = voice_id_engine
        
        # Session options for modality sync
        self._session_options = type('obj', (object,), {
            'voice_id': 'jarvis_v2',
            'response_modality': 'audio'
        })()

        self.plugin_manager = JarvisPluginManager()
        package_path = os.path.join(os.path.dirname(__file__), 'services')
        self.plugin_manager.discover_plugins(package_path)

        # Wrap internal tools to avoid 'self' in signature
        all_tools = self.plugin_manager.get_livekit_tools()

        async def wrap_set_wake_word_mode(active: bool) -> dict:
            return await self.tool_set_wake_word_mode(active)
        wrap_set_wake_word_mode.__doc__ = self.tool_set_wake_word_mode.__doc__

        async def wrap_change_voice(voice_name: str) -> dict:
            return await self.tool_change_voice(voice_name)
        wrap_change_voice.__doc__ = self.tool_change_voice.__doc__

        async def wrap_toggle_gf_mode(active: bool) -> dict:
            return await self.tool_toggle_gf_mode(active)
        wrap_toggle_gf_mode.__doc__ = self.tool_toggle_gf_mode.__doc__

        all_tools.extend([
            llm.function_tool(wrap_set_wake_word_mode),
            llm.function_tool(wrap_change_voice),
            llm.function_tool(wrap_toggle_gf_mode),
        ])

        super().__init__(
            chat_ctx=chat_ctx,
            instructions=prompt_with_info,
            llm=google.realtime.RealtimeModel(
                voice="charon", model="models/gemini-2.5-flash-native-audio-latest"),
            tools=all_tools
        )

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
            msg = f"Voice '{voice_name}' invalid. Use one of: {', '.join(valid_voices)}"
            return {"status": "error", "message": msg}

        self.llm.voice = voice_name.lower()

        # Update voice in the session if active
        if self._active_session:
            try:
                if (hasattr(self._active_session, "_activity") and
                        self._active_session._activity):
                    rt_session = getattr(
                        self._active_session._activity, "_rt_session", None)
                    if rt_session:
                        rt_session.update_options(voice=voice_name.lower())
                        logger.info("Voice changed to: %s", voice_name)
            except Exception as e:
                logger.error("Failed to change voice dynamically: %s", e)

        return {
            "status": "success",
            "voice": voice_name,
            "message": f"Voice change karkay '{voice_name}' kar di gai hai, Sir."
        }

    @property
    def wake_word_mode(self) -> bool:
        """Returns the current wake word mode."""
        return self._wake_word_mode

    @property
    def muted(self) -> bool:
        """Returns the current muted state."""
        return self._muted

    def set_muted(self, active: bool):
        """Set the muted state of the assistant."""
        self._muted = active
        logger.info("Muted set to: %s", active)

    def set_wake_word_mode(self, active: bool):
        """Toggle the strict wake word enforcement mode."""
        self._wake_word_mode = active
        status = "active" if active else "disabled"
        logger.info("Wake word mode set to: %s", status)
        asyncio.create_task(self._notify_ui_event("wake_word_sync", active))

    def get_state(self) -> dict:
        """Returns a snapshot of the current agent state."""
        return {
            "muted": self._muted,
            "wake_word_active": self._wake_word_mode,
            "active_persona": "anna" if self._gf_mode_active else "jarvis",
            "voice": self.llm.voice,
            "modality": "audio" if self._gf_mode_active else "text"
        }

    async def _update_persona_instructions(self):
        """Generates and applies instructions for the active persona."""
        if self._gf_mode_active:
            state = jarvis_id.get_anna_state()
            instr = ANNA_BEHAVIOR_PROMPT.format(
                mood=state["mood"],
                is_upset=state["is_upset"],
                user_background=jarvis_id.data.get("user_background"),
                sir_background=jarvis_id.data.get("sir_background")
            )
            voice = "aoede"
        else:
            instr = f"{BEHAVIOR_PROMPT}\n{jarvis_id.get_context()}"
            voice = "charon"

        await self.update_instructions(instr)
        self.llm.voice = voice

        if self._active_session:
            try:
                # Syncing modality
                self._update_session_modality("ANNA" if self._gf_mode_active else "JARVIS")
                
                # Find the Realtime Session deeper in the object structure for LiveKit 0.22+
                rt_session = None
                if hasattr(self._active_session, "_activity"):
                    rt_session = getattr(
                        self._active_session._activity, "_rt_session", None)

                if rt_session:
                    logger.info("Setting session options: voice=%s", voice)
                    rt_session.update_options(voice=voice)
                
                active_persona = "anna" if self._gf_mode_active else "jarvis"
                asyncio.create_task(self._notify_ui_persona(active_persona))
            except Exception as e:
                logger.warning("Failed to sync persona/voice: %s", e)

    def _update_session_modality(self, persona: str = "JARVIS"):
        """Centralized helper to update session options based on persona."""
        if persona == "ANNA":
            self._session_options.voice_id = "anna_v3_premium"
            self._session_options.response_modality = "audio"
        else:
            self._session_options.voice_id = "jarvis_v2"
            self._session_options.response_modality = "text"
        logger.info("Session modality updated for persona: %s", persona)

    async def _notify_ui_event(self, event_type: str, payload: Any):
        """Generic helper to notify STONIX UI with signed payloads."""
        try:
            payload_data = {"type": event_type, "payload": payload}
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }
            url = f"{config.bridge_url}/notify"
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload_data, headers=headers, timeout=1.0)
        except Exception as e:
            logger.debug("UI Notification failed (%s): %s", event_type, e)

    async def _notify_ui_reasoning(self, reasoning_result: dict):
        """Sends intelligence/reasoning metadata to the UI Bridge."""
        await self._notify_ui_event("reasoning", {
            "intent": reasoning_result.get("intent_analysis", {}).get("primary_intent"),
            "confidence": reasoning_result.get(
                "intent_analysis", {}).get("confidence_scores", {}),
            "plan": reasoning_result.get("plan", []),
            "is_ambiguous": reasoning_result.get(
                "intent_analysis", {}).get("is_ambiguous", False)
        })

    async def tool_toggle_gf_mode(self, active: bool) -> dict:
        """Toggle the romantic GF (Anna) persona."""
        self._gf_mode_active = active
        await self._update_persona_instructions()
        msg = "Anna activate ho gayi hain, Sir." if active else "Jarvis wapas aa gaya hai, Sir."
        return {"status": "success", "active": active, "message": msg}

    def attach_session(self, session: AgentSession):
        """Link the active session to this assistant."""
        self._active_session = session
        if not self._proactive_vision_task:
            self._proactive_vision_task = asyncio.create_task(
                self._proactive_vision_loop())

    async def _proactive_vision_loop(self):
        """Background loop for environment awareness."""
        logger.info("🔭 AuraView 2.0: Proactive awareness loop started.")
        while True:
            try:
                ctx = await get_active_window_context()
                if ctx.get("status") == "success":
                    self.active_window_context = ctx
                    logger.debug("Active Window: %s", ctx.get("title"))
                health_monitor.record_heartbeat("vision_loop")
            except Exception as e:
                logger.error("Vision loop error: %s", e)
            await asyncio.sleep(60)

    async def process_with_reasoning(self, user_input: str) -> str:
        """Process user input with advanced reasoning pipeline."""
        session_id = str(uuid.uuid4())
        health_monitor.record_heartbeat("backend_core")
        try:
            telemetry.start_interaction(session_id, user_input)
            
            # Use unified reasoning pipeline
            res = await process_with_advanced_reasoning(
                user_input,
                history=self.conversation_history,
                is_anna=self._gf_mode_active
            )
            
            asyncio.create_task(self._notify_ui_reasoning(res))
            telemetry.end_interaction(session_id, success=True)
            return res.get("generated_response", "Sir, main samajh gaya.")
        except Exception as e:
            logger.error("Cognitive Engine Error: %s", e)
            telemetry.end_interaction(session_id, success=False)
            return "Sir, main madad karne ke liye ready hun."

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
        is_a = bool(re.search(r"\b(anna|babu|jaan|myra|kiara|zoya)\b",
                              text, re.IGNORECASE))
        if not is_j and not is_a:
            return False, False
        await self._handle_persona_switch(is_a, is_j)
        return True, is_a

    async def _inject_emotional_context(self, text: str, turn_ctx: Any):
        """Injects emotional hints."""
        try:
            mem = await self.memory_extractor.memory.get_recent_context(
                max_messages=5)
            curr = context_analyzer.analyze_context(text, mem)
            if self._gf_mode_active and curr.get("user_mood") == "upset":
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="assistant", content=["Manao him."]))
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
                role="assistant", content=["Demand sorry."]))

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

            asyncio.create_task(self._notify_ui_reasoning(res))
            if res.get("is_agentic") and res.get("plan"):
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="system", content=[f"[EXECUTION PLAN]: {res['plan']}"]))
        except Exception as e:
            logger.error("Reasoning injection error: %s", e)

    async def _handle_vision_query(self, text, new_message, turn_ctx):
        """Processes and injects vision data if query is detected."""
        kw = ["vision", "dekh", "see", "view", "camera", "nazar", "peeche"]
        is_vision = any(w in text for w in kw)

        if is_vision and self.last_vision_frame:
            logger.info("Vision query detected. Injecting frame.")
            raw_data = self.last_vision_frame
            if isinstance(raw_data, str) and "," in str(raw_data):
                b64_data = str(raw_data).split(",")[1]
            else:
                b64_data = str(raw_data)

            img_content = llm.ImageContent(
                image=b64_data, mime_type="image/jpeg")
            new_message.content = [text, img_content]
            await turn_ctx.add_message(
                role="system", content="[VISION SYSTEM ACTIVE] Describe frame.")
        else:
            new_message.content = text

    async def _handle_window_context(self, turn_ctx):
        """Injects environment awareness context."""
        if (self.active_window_context and
                self.active_window_context.get("status") == "success"):
            title = self.active_window_context.get("title")
            system_msg = f"[ENVIRONMENT]: User is focused on: '{title}'."
            turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                role="system", content=[system_msg]
            ))

    async def _notify_ui_persona(self, persona: str):
        """Sends notification to UI Bridge about persona change."""
        await self._notify_ui_event("persona_change", persona)

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
        # Update sample rate from incoming frame
        if hasattr(frame, "sample_rate"):
            self._audio_sample_rate = frame.sample_rate
            
        self._audio_buffer.extend(frame.data)
        # Keep only last 10 seconds of audio (approx 320k bytes for 16kHz mono)
        if len(self._audio_buffer) > 320000:
            self._audio_buffer = self._audio_buffer[-320000:]

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
        # Threshold 0.72 is stricter for production security
        audio_bytes = bytes(self._audio_buffer)
        # 4. Voice Identity Enforcement (Final Security Layer)
        is_match, score = self.voice_id_engine.verify_bytes(audio_bytes, threshold=0.65)
        logger.info("Voice Security Check: Score=%.4f | Threshold=0.65 | Match=%s", score, is_match)
        
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

        # Speaker Verification Check
        logger.info("🛡️ Running Voice Fingerprint check...")
        is_authorized = await self.verify_speaker_identity()
        if not is_authorized:
            logger.warning("⛔ Unauthorized Voice detected. Ignoring command.")
            raise StopResponse()
        
        logger.info("✅ Voice Identity Verified.")

        if self._wake_word_mode:
            detected, is_anna = await self._handle_wake_word(sanitized_text)
            if not detected:
                raise StopResponse()
        else:
            is_anna = False

        asyncio.create_task(self._notify_ui_event("thinking", "START"))
        if self._muted:
            raise StopResponse()

        try:
            await self._handle_anna_upset_state(sanitized_text, turn_ctx)
            await self._inject_emotional_context(sanitized_text, turn_ctx)
            await self._handle_vision_query(sanitized_text, new_message, turn_ctx)
            await self._handle_window_context(turn_ctx)
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
        except Exception as e:
            logger.error("Turn Error: %s", e)
            telemetry.end_interaction("ERROR_TURN", success=False)
            raise StopResponse() from e


if __name__ == "__main__":
    print("✅ BrainAssistant class loaded successfully.")
