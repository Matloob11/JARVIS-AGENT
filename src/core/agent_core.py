"""
# agent_core.py
Core logic for the BrainAssistant agent.
"""

# pylint: disable=protected-access, broad-exception-caught

import asyncio
import os
import re
import time
import types  # For SimpleNamespace
import uuid
from collections import defaultdict, deque
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, StopResponse, llm
from services.ai_core.agent_memory import MemoryExtractor
from services.ai_core.jarvis_identity import jarvis_id
from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
from services.ai_core.jarvis_prompt import BEHAVIOR_PROMPT
from services.ai_core.jarvis_reasoning import (
    analyze_user_intent,
    generate_smart_response,
    process_with_advanced_reasoning,
)
from services.ai_core.voice_fingerprint import voice_id_engine
from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_audit import jarvis_audit
from services.utils.jarvis_autonomous import resilient_tool
from services.utils.jarvis_config import config
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard
from services.utils.jarvis_telemetry import telemetry

try:
    from .bridge_notifier import bridge_notifier
    from .persona_manager import PersonaManager
    from .vision_handler import VisionHandler
except (ImportError, ValueError):
    from src.core.bridge_notifier import bridge_notifier
    from src.core.persona_manager import PersonaManager
    from src.core.vision_handler import VisionHandler

from services.automation.jarvis_task_manager import task_manager

logger = setup_logger("JARVIS-CORE")
# pylint: disable=unused-import

INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT

# pylint: disable=too-many-instance-attributes
class BrainAssistant(Agent):
    """
    Enhanced Assistant with reasoning capabilities and integrated tool suite.
    """
    user_id: str
    memory_extractor: MemoryExtractor
    conversation_history: list[dict[str, Any]]
    persona_manager: PersonaManager
    vision_handler: VisionHandler
    bridge_notifier: Any # bridge_notifier type can be complex
    _background_tasks: set[asyncio.Task[Any]]
    _spawned_tasks: set[asyncio.Task[Any]]
    last_vision_frame: str | None
    _active_session: AgentSession | None
    _muted: bool
    _wake_word_mode: bool
    voice_id_engine: Any


    def __init__(self, chat_ctx: llm.ChatContext | Any, llm_instance: llm.LLM | None = None,
                 tools: list[llm.FunctionTool] | None = None, current_date: str | None = None,
                 current_city: str | None = None, user_id: str | None = None) -> None:
        """
        Initialize the BRAIN assistant with context, LLM, and tools.
        """
        identity_context = jarvis_id.get_context()
        prompt_with_info = f"{INSTRUCTIONS_PROMPT}\n{identity_context}"

        # Real-time Project Age Calculation
        project_start_date = datetime(2025, 8, 2)
        days_since_start = (datetime.now() - project_start_date).days

        self.current_date = current_date or datetime.now().strftime("%Y-%m-%d")
        self.current_city = current_city or ''

        # Use format_map with defaultdict to safely handle any extra curly braces in prompt
        prompt_with_info = prompt_with_info.format_map(
            defaultdict(str, 
                current_date=self.current_date,
                current_city=self.current_city,
                project_age_days=str(days_since_start)
            ),
        )

        # Initializing core agent state
        self.user_id: str = user_id or os.getenv("USER_NAME") or "User"
        self._chat_ctx = chat_ctx

        # Robustly determine final user_id if not provided
        if not user_id and chat_ctx:
            # chat_ctx might be a SimpleNamespace or llm.ChatContext
            try:
                participant = getattr(chat_ctx, 'participant', None)
                if participant and hasattr(participant, 'identity'):
                    self.user_id = str(participant.identity)
            except AttributeError:
                logger.debug("ChatContext has no accessible participant attribute.")
                participant = None

        self._last_user_speak_pulse: float = 0.0
        self.memory_extractor = MemoryExtractor(self.user_id)
        self.conversation_history: list[dict[str, Any]] = []
        self._wake_word_mode: bool = True
        self._active_session: AgentSession | None = None
        self._muted: bool = False
        self._audio_buffer: deque[int] = deque(maxlen=96000)
        self._audio_sample_rate: int = 16000 # Default
        self._last_speaker_verified: bool = True
        self.voice_id_engine = voice_id_engine
        self._local_llm: Any = llm_instance or types.SimpleNamespace(voice="Charon")

        # Session options for modality sync
        self._session_options = types.SimpleNamespace(
            voice_id='jarvis_v2',
            response_modality='audio',
        )

        self._is_user_currently_speaking: bool = False
        self._last_verification_score: float = 1.0 # default verified
        self._session_verification_count: int = 0

        # Discovering plugins once centrally
        self.plugin_manager = JarvisPluginManager()
        package_path = os.path.join(config.project_root, "services")
        self.plugin_manager.discover_plugins(package_path)

        # Initialize Modular Handlers
        self.persona_manager = PersonaManager(self)
        self.vision_handler = VisionHandler(self)
        self.bridge_notifier = bridge_notifier
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._spawned_tasks: set[asyncio.Task[Any]] = set()
        self.last_vision_frame: bytes | None = None
        
        # Start Advanced Task Monitor when constructed inside an active event loop.
        try:
            asyncio.get_running_loop()
            self._run_back(self._task_monitor_loop())
        except RuntimeError:
            logger.debug("Task monitor deferred: no running event loop during init.")

        # --- Wrap internal tools within this scope to capture 'self' ---
        @resilient_tool("set_wake_word_mode")
        async def wrap_set_wake_word_mode(active: bool) -> dict[str, Any]:
            """Toggle the strict wake word enforcement mode."""
            return await self.tool_set_wake_word_mode(active)

        @resilient_tool("change_voice")
        async def wrap_change_voice(voice_name: str) -> dict[str, Any]:
            """Change the AI's speaking voice. Valid voices include: alloy, echo, shimmer, ash, ballad, coral, sage, verse, charon, aoede."""
            return await self.tool_change_voice(voice_name)

        @resilient_tool("toggle_persona")
        async def wrap_toggle_gf_mode(active: bool) -> dict[str, Any]:
            """Toggle between JARVIS (Butler) and ANNA (Persona) modes."""
            return await self.persona_manager.set_persona(active)

        @resilient_tool("analyze_surroundings")
        async def wrap_analyze_surroundings(query: str = "Describe the current camera view in detail.") -> dict[str, Any]:
            """Captures a frame from the system camera and performs advanced visual analytics. Use this when the user asks 'what do you see' or 'analyze surroundings'."""
            return await self.tool_analyze_surroundings(query)

        # Merge external tools with internal ones
        all_tools = []
        
        # 1. Add Internal Tools (using the correct function_tool factory)
        all_tools.append(llm.function_tool(wrap_set_wake_word_mode))
        all_tools.append(llm.function_tool(wrap_change_voice))
        all_tools.append(llm.function_tool(wrap_toggle_gf_mode))
        all_tools.append(llm.function_tool(wrap_analyze_surroundings))

        # 2. Add External Tools discovered from modules/plugins
        external_tools = tools or []
        all_tools.extend(external_tools)

        # Log tools for verification
        logger.info("🔧 Registering Agent with %d total tools (%d external, 4 internal)", 
                    len(all_tools), len(external_tools))
        for t in all_tools:
            logger.debug("   - Tool: %s", t.info.name)

        # Correct super().__init__ call with mandatory instructions and tool list
        super().__init__(
            instructions=prompt_with_info,
            llm=llm_instance,
            chat_ctx=chat_ctx,
            stt=None,
            tools=all_tools,
            tts=None,
        )



    async def prune_chat_context(self) -> None:
        """
        Prunes the chat context to prevent unbounded memory growth.
        Keeps the system/initial instructions and the last N messages.
        """
        if not self._chat_ctx:
            return

        MAX_MESSAGES = 30
        chat_ctx = self._chat_ctx
        if asyncio.iscoroutine(chat_ctx) or hasattr(chat_ctx, "__await__"):
            chat_ctx = await chat_ctx

        messages = getattr(chat_ctx, "messages", None)
        if callable(messages):
            messages = messages()
        if asyncio.iscoroutine(messages) or hasattr(messages, "__await__"):
            messages = await messages
        if not isinstance(messages, list):
            return

        if len(messages) > MAX_MESSAGES:
            logger.info("Pruning chat context (Size: %s -> %s)", len(messages), MAX_MESSAGES)
            instructions = messages[0]
            recent_messages = messages[-(MAX_MESSAGES - 1):]
            messages[:] = [instructions] + recent_messages
        return

        if len(self._chat_ctx.messages) > MAX_MESSAGES:
            logger.info("🧠 Pruning chat context (Size: %s -> %s)",
                        len(self._chat_ctx.messages), MAX_MESSAGES)
            # Keep index 0 (instructions) and the last N-1 messages
            instructions = self._chat_ctx.messages[0]
            recent_messages = self._chat_ctx.messages[-(MAX_MESSAGES - 1):]
            self._chat_ctx.messages[:] = [instructions] + recent_messages

    async def update_instructions(self, instructions: str) -> None:
        """
        Dynamically updates the assistant's system instructions.
        Used by PersonaManager to switch between JARVIS and ANNA.
        """
        self._instructions = instructions
        logger.info("Assistant instructions updated (Persona: %s)",
                    self.persona_manager.active_persona_name)

    async def tool_toggle_gf_mode(self, active: bool) -> dict[str, Any]:
        """Toggle GF (Anna) persona mode."""
        await self.persona_manager.set_persona(active)
        return {"status": "success", "mode": "Anna" if active else "Jarvis"}

    async def tool_set_wake_word_mode(self, active: bool) -> dict[str, Any]:
        """Toggle the strict wake word enforcement mode."""
        self._wake_word_mode = active
        status = "active" if active else "disabled"
        return {
            "status": "success",
            "mode": status,
            "message": f"Wake word mode {status} ho gaya hai, Sir Matloob.",
        }

    async def tool_analyze_surroundings(self, query: str = "Describe the current camera view in detail.") -> dict[str, Any]:
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
            "analysis": analysis,
        }

    async def tool_change_voice(self, voice_name: str) -> dict[str, Any]:
        """Change the AI's speaking voice."""
        valid_voices = ["alloy", "echo", "shimmer", "ash",
                        "ballad", "coral", "sage", "verse", "charon", "aoede"]
        if voice_name.lower() not in valid_voices:
            msg = f"Voice '{voice_name}' invalid. Use one of: {', '.join(valid_voices)}"
            return {"status": "error", "message": msg}

        # Safe voice update for Mypy
        llm_any: Any = getattr(self, "llm", None) or self._local_llm
        if llm_any and hasattr(llm_any, 'voice'):
            llm_any.voice = voice_name.lower()

        # Update voice in the session if active
        session_any: Any = self._active_session
        if session_any:
            try:
                if hasattr(session_any, 'set_voice'):
                    session_any.set_voice(voice_name.lower())
            except Exception as e:
                logger.error("Failed to update session voice: %s", e)
        return {"status": "success", "voice": voice_name.lower()}

    def _update_session_modality(self, modality: str) -> None:
        """Internal helper to sync modality if the session supports it."""
        if self._active_session:
            self._session_options.response_modality = modality
            logger.info("Session modality target set to: %s", modality)

    def _run_back(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Helper to run a coroutine in background with tracking."""
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def set_muted(self, active: bool) -> None:
        """Set the muted state and manage audio track subscriptions."""
        self._muted = active
        if active:
            self._audio_buffer.clear()
            logger.info("🔇 Assistant MUTED. Audio buffer cleared.")
        else:
            logger.info("🔊 Assistant UNMUTED.")

        # If we have an active session, sync audio track subscriptions
        session_any: Any = self._active_session
        if session_any and hasattr(session_any, 'room') and session_any.room:
            for participant in session_any.room.remote_participants.values():
                for publication in participant.track_publications.values():
                    if publication.kind == rtc.TrackKind.KIND_AUDIO:
                        try:
                            publication.set_subscribed(not active)
                            logger.info("🎤 Audio track %s subscription set to: %s",
                                        publication.sid, not active)
                        except Exception as e:
                            logger.error("Failed to update subscription for %s: %s", publication.sid, e)

        self._run_back(self.bridge_notifier.notify_event("mute_sync", active))

    def set_wake_word_mode(self, active: bool) -> None:
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

    @property
    def chat_ctx(self) -> llm.ChatContext:
        """Returns the current chat context."""
        return self._chat_ctx

    @chat_ctx.setter
    def chat_ctx(self, value: llm.ChatContext):
        """Sets the chat context."""
        self._chat_ctx = value

    @property
    def session(self) -> AgentSession | None:
        """Return the active LiveKit session, when one has been attached."""
        return self._active_session

    @session.setter
    def session(self, value: AgentSession | None) -> None:
        """Compatibility setter for tests and local integrations."""
        self._active_session = value

    def get_state(self) -> dict[str, Any]:
        """Returns a snapshot of the current agent state."""
        llm_any = getattr(self, "llm", None) or self._local_llm
        return {
            "muted": self._muted,
            "wake_word_active": self._wake_word_mode,
            "active_persona": self.persona_manager.active_persona_name,
            "voice": getattr(llm_any, "voice", "Charon"),
            "modality": "audio" if self.persona_manager.is_anna else "text",
        }

    def attach_session(self, session: AgentSession) -> None:
        """
        Stores the active LiveKit session for dynamic updates and starts vision context polling.
        Called by AgentRunner after session start.
        """
        self._active_session = session
        logger.info("Live AgentSession attached to BrainAssistant (User: %s)", self.user_id)
        try:
            self.vision_handler.start_loop()
        except RuntimeError as e:
            logger.debug("Vision loop not started: %s", e)

    async def _task_monitor_loop(self):
        """Background loop focused on proactive task execution."""
        logger.info("📅 Task Monitor initialized. System is now Proactive.")
        while True:
            try:
                due_tasks = await asyncio.to_thread(task_manager.get_due_tasks)
                for task in due_tasks:
                    logger.info("🔔 Task Due: %s", task['description'])
                    
                    # Notify UI Bridge
                    await self.bridge_notifier.notify_event("task_alert", {
                        "id": task['id'],
                        "description": task['description'],
                        "action": task['action']
                    })
                    
                    # Proactive Speech (Simulated injection or notification)
                    # For now, we notify the user via the UI bridge which can trigger a speak event
                    # or we can directly inject a system message to the agent to 'speak' next time user interacts
                    # But for a true JARVIS feel, we want it to interrupt or alert.
                    
                    # Update status in DB
                    await asyncio.to_thread(task_manager.mark_completed, task['id'])
                    
                await asyncio.sleep(45) # Check every 45s
            except Exception as e:
                logger.error("Task Monitor Error: %s", e)
                await asyncio.sleep(60)

    async def process_with_reasoning(self, user_input: str) -> str:
        """Process user input with advanced reasoning pipeline."""
        session_id = str(uuid.uuid4())
        health_monitor.record_heartbeat("backend_core")
        try:
            await telemetry.start_interaction(session_id, user_input)

            # Use explicit reasoning steps so each contract stays testable and observable.
            logger.info("Analyzing input: '%s...'", user_input[:40])

            intent_analysis = await analyze_user_intent(user_input)
            memory_context = await self.memory_extractor.memory.get_recent_context(max_messages=10)
            semantic_context = await self.memory_extractor.memory.get_semantic_context(
                user_input,
                n_results=5,
            )
            if isinstance(semantic_context, list):
                semantic_memory = [
                    item.get("document", str(item)) if isinstance(item, dict) else str(item)
                    for item in semantic_context
                ]
            elif semantic_context:
                semantic_memory = [str(semantic_context)]
            else:
                semantic_memory = []
            response = await generate_smart_response(
                user_input,
                intent_analysis,
                memory_context,
                semantic_memory=semantic_memory,
                is_anna=self.persona_manager.is_anna,
            )

            res = {
                "user_input": user_input,
                "intent_analysis": intent_analysis,
                "plan": [],
                "is_agentic": False,
                "generated_response": response,
            }
            self._run_back(self.bridge_notifier.notify_reasoning(res))
            logger.info("Response generated via Cognitive Engine")
            await telemetry.end_interaction(session_id, success=True)
            return response
        except Exception as e:
            logger.error("Cognitive Engine Error: %s", e)
            await telemetry.end_interaction(session_id, success=False)
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

    async def _update_persona_instructions(self) -> None:
        """Backward-compatible wrapper around the persona manager."""
        await self.persona_manager.update_assistant_persona()

    async def _handle_anna_upset_state(self, text: str, turn_ctx: Any) -> None:
        """Backward-compatible wrapper around Anna persona state handling."""
        legacy_gf_mode = bool(getattr(self, "_gf_mode_active", False))
        if hasattr(self, "_gf_mode_active"):
            self.persona_manager._gf_mode_active = legacy_gf_mode  # pylint: disable=protected-access
        await self.persona_manager.handle_anna_upset_state(text, turn_ctx)
        legacy_items = getattr(getattr(turn_ctx, "chat_ctx", None), "items", None)
        if legacy_gf_mode and "sorry" not in text.lower() and isinstance(legacy_items, list) and not legacy_items:
            legacy_items.append(llm.ChatMessage(role="assistant", content=["Demand sorry."]))

    async def _inject_emotional_context(self, text: str, turn_ctx: Any) -> None:
        """Backward-compatible wrapper around emotional context injection."""
        try:
            await self.persona_manager.inject_emotional_context(
                text,
                turn_ctx,
                self.conversation_history,
            )
        except Exception as e:
            logger.error("Emotional context injection failed: %s", e)

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

            self._run_back(self.bridge_notifier.notify_reasoning(res))
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
            # Only notify UI if state changed to True or if a significant update is needed
            # This throttles the UI Bridge to prevent localhost socket flooding
            if has_audio:
                if not getattr(self, "_is_user_currently_speaking", False):
                    self._run_back(self.bridge_notifier.notify_user_speaking(True))
                    self._is_user_currently_speaking = True
                self._last_user_speak_pulse = now
            elif getattr(self, "_is_user_currently_speaking", False):
                self._run_back(self.bridge_notifier.notify_user_speaking(False))
                self._is_user_currently_speaking = False

        # Add data to deque (it handles the maxlen automatically and efficiently)
        self._audio_buffer.extend(frame.data)

    async def verify_speaker_identity(self) -> bool:
        """Verifies if the current audio buffer matches the enrolled user with caching logic."""
        if os.getenv("JARVIS_TEST_MODE", "").lower() == "true":
            return True

        # FAST PASS: If already verified with extremely high confidence in the same session,
        # we skip the heavy model check for up to 3 subsequent turns to reduce latency.
        # This drastically improves the "stuttering/hanging" sensation during back-and-forth chat.
        current_session_count = getattr(self, "_session_verification_count", 0)
        recent_high_score = getattr(self, "_last_verification_score", 0.0)

        if current_session_count > 0 and recent_high_score > 0.85:
            self._session_verification_count += 1
            if self._session_verification_count % 3 != 0: # Full check every 3 turns
                logger.info("🛡️ Voice ID Fast Pass: High confidence (%.2f%%) cached. Skipping model inference.", recent_high_score * 100)
                return True

        # SECURE BY DEFAULT: If no audio is captured, it is NOT verified.
        if not self._audio_buffer:
            logger.warning("🛡️ Voice ID: No audio captured in buffer. ACCESS DENIED.")
            return False

        # Require at least 1.0 seconds of audio for a reliable check
        if len(self._audio_buffer) < 32000:
            logger.warning("🛡️ Voice ID: Audio segment too short for verification. ACCESS DENIED.")
            return False

        # Convert deque of integers (bytes) back to a single bytes object
        audio_payload = bytes(self._audio_buffer)
        is_match, score = await self.voice_id_engine.verify_bytes(audio_payload, threshold=0.65)
        
        self._last_speaker_verified = is_match
        self._last_verification_score = score
        self._session_verification_count = current_session_count + 1

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
            # Parallelize non-blocking context injections to reduce turnaround time
            async def run_prep():
                await asyncio.gather(
                    self._handle_anna_upset_state(sanitized_text, turn_ctx),
                    self._inject_emotional_context(sanitized_text, turn_ctx),
                    self.vision_handler.handle_vision_query(sanitized_text, new_message, turn_ctx),
                    self.vision_handler.inject_window_context(turn_ctx),
                    self._inject_reasoning_and_memory(sanitized_text, turn_ctx, is_anna),
                )

            await run_prep()

            self.conversation_history.append(
                {"role": "user", "content": sanitized_text})
            if len(self.conversation_history) > 20:
                self.conversation_history.pop(0)

            # Super call for legacy tool execution
            response = await super().on_user_turn_completed(turn_ctx, new_message)

            # Prune context to avoid memory growth over long sessions
            await self.prune_chat_context()

            # Telemetry is handled in the underlying handle_user_query / process_with_reasoning
            # But we log final UI status here
            self._run_back(adaptive_engine.log_interaction(
                "BOT_TURN", sanitized_text, str(response), success=True))
            return response
        except StopResponse:
            # Clean lifecycle control — re-raise as-is
            raise
        except (ValueError, RuntimeError, AttributeError, TypeError) as e:
            logger.error("Turn Error (recoverable): %s", e, exc_info=True)
            await telemetry.end_interaction("ERROR_TURN", success=False)
            raise StopResponse() from e
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.critical("Turn Error (unexpected): %s", e, exc_info=True)
            await telemetry.end_interaction("ERROR_TURN", success=False)
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
