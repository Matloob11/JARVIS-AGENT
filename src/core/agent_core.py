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
from collections import deque
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, StopResponse, llm
from services.ai_core.agent_memory import MemoryExtractor
from services.ai_core.jarvis_identity import jarvis_id
from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
from services.ai_core.jarvis_prompt import ANNA_NAMES, BEHAVIOR_PROMPT
from services.ai_core.jarvis_reasoning import (
    analyze_user_intent,
    generate_smart_response,
    process_with_advanced_reasoning,
)
from services.ai_core.voice_fingerprint import voice_id_engine
from services.utils.jarvis_adaptive import adaptive_engine
from services.utils.jarvis_audit import jarvis_audit
from services.utils.jarvis_autonomous import resilient_tool
from services.utils.jarvis_config import DEFAULT_USER_ID, config, is_local_mode
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_resilience import resilience_manager
from services.utils.jarvis_security import vortex_guard
from services.utils.jarvis_telemetry import telemetry
from src.core.gemini_resume_guard import restart_fresh
from src.core.session_policy import ChatTruncator  # C-114

try:
    from .bridge_notifier import bridge_notifier
    from .persona_manager import PersonaManager, append_turn_note, build_instructions
    from .voices import persona_voice, resolve_voice, voices_help
except (ImportError, ValueError):
    from src.core.bridge_notifier import bridge_notifier
    from src.core.persona_manager import PersonaManager, append_turn_note, build_instructions
    from src.core.voices import persona_voice, resolve_voice, voices_help

# Optional handler: when it cannot load (e.g. a desktop-only import on a headless host) the agent runs
# without vision instead of failing to import.
_vision_import_error: Exception | None = None
try:
    from src.core.vision_handler import VisionHandler
except Exception as _exc:  # pylint: disable=broad-exception-caught
    VisionHandler = None  # type: ignore[assignment,misc]
    _vision_import_error = _exc

from services.automation.jarvis_task_manager import task_manager

logger = setup_logger("JARVIS-CORE")
# pylint: disable=unused-import

_handler_failures: set[str] = set()


def _optional_handler_failed(what: str, exc: BaseException) -> None:
    """An optional handler never stops the agent; each kind of failure is logged once per process."""
    if what in _handler_failures:
        logger.debug("%s failed again: %s: %s", what, type(exc).__name__, exc)
        return
    _handler_failures.add(what)
    logger.warning("%s unavailable (%s: %s); continuing without it", what, type(exc).__name__, exc)


class _NoVisionHandler:
    """Stands in for VisionHandler when it cannot load: no frames, no window context, no loop."""

    def __init__(self) -> None:
        self.last_vision_frame: str | None = None
        self.last_vision_frame_ts: float | None = None
        self.active_window_context: dict[str, Any] = {}

    def start_loop(self) -> None:
        """Nothing to poll."""

    async def stop_loop(self) -> None:
        """Nothing to stop."""

    def on_window_context(self, event: Any) -> None:
        """Window events are dropped."""

    async def handle_vision_query(self, text: str, new_message: Any, turn_ctx: Any) -> None:
        """No frame to inject."""

    async def inject_window_context(self, turn_ctx: Any) -> None:
        """No window context to inject."""

    async def analyze_current_frame(self, prompt: str) -> str:
        return "Error: Sir, vision system abhi available nahi hai."


if _vision_import_error is not None:
    _optional_handler_failed("VisionHandler", _vision_import_error)


def _vision_analysis_failed(analysis: str) -> bool:
    """
    True when analyze_current_frame's string reports a failure (C-48). jarvis_vision.analysis_failed
    keeps a real answer that starts with 'Error' (an error dialog in view); without the vision module
    (the stand-in above) every 'Error...' string is a failure.
    """
    try:
        from services.ai_core.jarvis_vision import analysis_failed  # pylint: disable=import-outside-toplevel
    except Exception:  # pylint: disable=broad-exception-caught
        return analysis.startswith("Error")
    return analysis_failed(analysis)


def needs_forced_interrupt(session: Any) -> bool:
    """C-51 (K-60 'stop'): True when the session's current speech does not allow interruptions (livekit-agents 1.4.6
    SpeechHandle.allow_interruptions False); AgentSession.interrupt() refuses such a speech unless force=True."""
    speech = getattr(session, "current_speech", None)
    return speech is not None and getattr(speech, "allow_interruptions", True) is False


INSTRUCTIONS_PROMPT = BEHAVIOR_PROMPT
# C-49: the agent states at which new instructions may reach the live session (no reply or tool call running)
INSTRUCTIONS_SAFE_STATES = ("listening", "idle")
# One wake-word list for the prompt and the code (jarvis_prompt.WAKE_WORDS)
_JARVIS_WAKE = re.compile(r"\bjarvis\b", re.IGNORECASE)
_ANNA_WAKE = re.compile(r"\b(" + "|".join(name.lower() for name in ANNA_NAMES) + r")\b", re.IGNORECASE)
# C-48: a camera analysis goes through up to three vision providers; 15 s (the breaker default) cut it off.
ANALYZE_SURROUNDINGS_TIMEOUT_S = 60.0

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
                 current_city: str | None = None, user_id: str | None = None, probe: bool = False) -> None:
        """
        Initialize the BRAIN assistant with context, LLM, and tools.
        """
        self.current_date = current_date or datetime.now().strftime("%Y-%m-%d")
        self.current_city = current_city or ''
        # C-49: one composition for every persona and turn mode (jarvis_prompt.compose_instructions); a job starts
        # as JARVIS with the wake word on.
        prompt_with_info = build_instructions(self, "jarvis", wake_word=True)

        # Initializing core agent state. C-52 (K-69): the memory identity is JARVIS_USER_ID (agent_runner passes it;
        # unset: 'matloob'), never a room participant's identity.
        self.user_id: str = user_id or config.user_id or DEFAULT_USER_ID
        self._chat_ctx = chat_ctx

        self._last_user_speak_pulse: float = 0.0
        self.memory_extractor = MemoryExtractor(self.user_id)
        self.conversation_history: list[dict[str, Any]] = []
        self._wake_word_mode: bool = True
        self._active_session: AgentSession | None = None
        self._conversation_router: Any = None  # C-42: typed chat goes into the session through it
        # C-114: cuts the chat context to its last 40 items every 20 conversation items (agent_runner counts them)
        self.chat_truncator = ChatTruncator(self)
        # C-49: a session update (instructions, voice) waits for a safe point: no reply or tool call running
        self._pending_instructions: str | None = None
        self._pending_voice: str | None = None
        # C-50: the persona this job last sent the dashboard (persona_change); None until the job's first announce
        self._notified_persona: str | None = None
        # C-54 (K-61): set by the job's settings service (agent_settings.SettingsService.note_live_change); a tool or a
        # spoken wake word that changes persona, voice or wake word calls it, so the change is saved and synced at once
        self.on_live_settings_change: Any = None
        # C-23 (K-60 v9): set by request_end; the job's entrypoint waits on it and then ends the job. Made with the
        # assistant, so a pause_agent that arrives before the entrypoint waits is never lost.
        self._stop_event: asyncio.Event = asyncio.Event()
        self.end_reason: str | None = None
        self._muted: bool = False
        # C-51 (K-60): the job's legacy voice-ID audio tap (agent_runner.UserAudioTaps: pause()/resume()), and whether a
        # mute without a session unsubscribed the remote audio tracks (unmute subscribes them again)
        self._audio_tap: Any = None
        self._tracks_unsubscribed: bool = False
        self._audio_buffer: deque[int] = deque(maxlen=96000)
        self._audio_sample_rate: int = 16000 # Default
        self._last_speaker_verified: bool = True
        self.voice_id_engine = voice_id_engine
        self._local_llm: Any = llm_instance or types.SimpleNamespace(voice=persona_voice("jarvis"))

        # Session options for modality sync
        self._session_options = types.SimpleNamespace(
            voice_id='jarvis_v2',
            response_modality='audio',
        )

        self._is_user_currently_speaking: bool = False
        self._last_verification_score: float = 1.0 # default verified
        self._session_verification_count: int = 0

        # C-32: discovery runs once per worker process (agent_runner.prewarm, or the entrypoint's
        # PREWARM_MISS fallback) before any BrainAssistant exists — never again per job here. The
        # singleton manager stays for the tools-info paths (e.g. running_tools); the assistant
        # itself uses the tools passed in (tools=external_tools).
        self.plugin_manager = JarvisPluginManager()

        # Initialize Modular Handlers
        self.persona_manager = PersonaManager(self)
        self.vision_handler = self._make_vision_handler()
        self.bridge_notifier = bridge_notifier
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._spawned_tasks: set[asyncio.Task[Any]] = set()
        self.last_vision_frame: bytes | None = None
        self.last_vision_frame_ts: float | None = None

        # K-57 (C-39): a probe or canary job runs no task monitor and no vision loop
        self._probe = bool(probe)
        # Start Advanced Task Monitor when constructed inside an active event loop. Local mode only:
        # with the VPS brain agent-svc checks due tasks (K-72).
        if is_local_mode() and not self._probe:
            try:
                asyncio.get_running_loop()
                self._run_back(self._task_monitor_loop())
            except RuntimeError:
                logger.debug("Task monitor deferred: no running event loop during init.")

        # C-48: resilient_tool reuses this cached breaker, so analyze_surroundings gets 60 s, not 15 s.
        resilience_manager.get_breaker("analyze_surroundings",
                                       execution_timeout=ANALYZE_SURROUNDINGS_TIMEOUT_S)

        # --- Wrap internal tools within this scope to capture 'self' ---
        # C-54 (K-61): each of these three tools tells the job's settings service afterwards (_live_settings_changed),
        # so a voice, persona or wake word the model changed is saved and reaches the bridge's copy at once
        @resilient_tool("set_wake_word_mode")
        async def wrap_set_wake_word_mode(active: bool) -> dict[str, Any]:
            """Toggle the strict wake word enforcement mode."""
            result = await self.tool_set_wake_word_mode(active)
            self._live_settings_changed("set_wake_word_mode")
            return result

        @resilient_tool("change_voice")
        async def wrap_change_voice(voice_name: str) -> dict[str, Any]:
            """Change the AI's speaking voice (from the next reply)."""
            result = await self.tool_change_voice(voice_name)
            self._live_settings_changed("change_voice")
            return result

        # C-50: the model sees every real Gemini voice and the OpenAI-style aliases
        wrap_change_voice.__doc__ = f"Change the AI's speaking voice (from the next reply). {voices_help()}"

        @resilient_tool("toggle_persona")
        async def wrap_toggle_gf_mode(active: bool) -> dict[str, Any]:
            """Toggle between JARVIS (Butler) and ANNA (Persona) modes."""
            result = await self.persona_manager.set_persona(active)
            self._live_settings_changed("toggle_persona")
            return result

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



    def _make_vision_handler(self) -> Any:
        """VisionHandler, or a stand-in without vision when it cannot load or start."""
        if VisionHandler is not None:
            try:
                return VisionHandler(self)
            except Exception as e:  # pylint: disable=broad-exception-caught
                _optional_handler_failed("VisionHandler", e)
        return _NoVisionHandler()

    async def prune_chat_context(self) -> None:
        """
        C-114: the old name, kept as a thin wrapper over the job's ChatTruncator (src/core/session_policy.py), which
        cuts the chat context for real, under its lock: the live realtime session gets its own copy back truncated to
        the last 40 items (removals only, so nothing is sent to Gemini again) and this assistant's own copy is set to
        its own last 40 directly (never pushed through update_chat_ctx); in both, the turns re-seeded from memory go
        whole with their '[MEMORY]' note or not at all. agent_runner runs it every 20 conversation items in every
        mode. This method used to edit a copy of the messages (nothing was ever removed) and only ran from
        on_user_turn_completed, which Gemini's server-side turn detection never calls.
        """
        await self.chat_truncator.truncate()

    async def update_instructions(self, instructions: str) -> None:
        """
        Dynamically updates the assistant's system instructions (PersonaManager: JARVIS <-> ANNA; the wake-word
        toggle). C-49: kept at once for the next session start (a rebuilt session included); a live session gets
        them at the next safe point (update_session).
        """
        await self.update_session(instructions=instructions)

    async def update_session(self, *, instructions: str | None = None, voice: str | None = None) -> None:
        """
        C-49: new instructions and/or a new voice for the live Gemini session, applied together at a safe point (no
        reply and no tool call running: a persona switch arrives inside the toggle_persona tool call) in ONE fresh
        reconnect: livekit's Agent.update_instructions, the session's own update_options(voice), then
        restart_fresh, so the instructions become the session's system instruction (a mid-session update is only
        appended) while the plugin replays the chat history. With no live session they are stored for the next one.
        """
        persona = self.persona_manager.active_persona_name
        if instructions is not None:
            self._instructions = instructions
        if voice is not None:
            self._session_options.voice_id = voice
        if getattr(self, "_activity", None) is None:
            # C-50: what is stored now replaces anything still pending from a closed session
            if instructions is not None:
                self._pending_instructions = None
            if voice is not None:
                self._pending_voice = None
            await self._store_for_next_session(instructions, voice, persona)
            self._announce_persona(persona)
            return
        if instructions is not None:
            self._pending_instructions = instructions
        if voice is not None:
            self._pending_voice = voice
        if self._session_busy():
            self._retry_after_current_speech()
            logger.info("INSTRUCTIONS_UPDATE persona=%s sent=deferred state=%s", persona,
                        getattr(self._active_session, "agent_state", None))
            return
        await self._apply_pending_session_update()

    async def _store_for_next_session(self, instructions: str | None, voice: str | None, persona: str) -> None:
        if instructions is not None:
            try:
                await super().update_instructions(instructions)  # no live session: livekit only stores them
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("INSTRUCTIONS_UPDATE persona=%s store failed (%s)", persona, type(e).__name__)
            logger.info("INSTRUCTIONS_UPDATE persona=%s chars=%d sent=stored", persona, len(instructions))
        if voice is not None:
            update = getattr(self._local_llm, "update_options", None)  # the model's options: the next session
            if callable(update):
                try:
                    update(voice=voice)
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning("VOICE_UPDATE store failed (%s)", type(e).__name__)
            logger.info("VOICE_UPDATE persona=%s voice=%s sent=stored", persona, voice)

    def _announce_persona(self, persona: str) -> None:
        """C-50: persona_change goes to the dashboard once per real switch, after the switch reached the session (or
        was stored for the next one), in the background: never inside the toggle_persona tool call, and never for a
        wake-word, mood or voice refresh of the same persona."""
        if persona == self._notified_persona:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # a caller without an event loop: the next switch or job start sends it
        self._notified_persona = persona
        self._run_back(self._notify_ui_persona(persona))

    @property
    def reply_persona(self) -> str:
        """C-52: the persona the live session answers as: the one the dashboard was last told (a switch is told once it
        reached the session, C-50), else the active one. A reply that is still running when a persona switch is asked
        for comes from the old persona, so the memory records it for that one."""
        return self._notified_persona or self.persona_manager.active_persona_name

    def announce_persona(self, *, again: bool = False) -> None:
        """C-50: persona_change for the active persona: at a job's start (agent_runner; the bridge keeps the last job's
        persona while every job starts as JARVIS) and, with again, when the persona that is active already is asked
        for (a stale dashboard, or a UI that waits for persona_update before it shows the switch)."""
        if again:
            self._notified_persona = None
        self._announce_persona(self.persona_manager.active_persona_name)

    def _live_instructions(self) -> Any:
        """The instructions the live Gemini session connected or was updated with (its options, shared with the
        model; AgentActivity sets them when the session starts), else None."""
        rt_session = getattr(getattr(self, "_activity", None), "realtime_llm_session", None)
        return getattr(getattr(rt_session, "_opts", None), "instructions", None)

    async def carry_pending_voice(self) -> None:
        """C-50: before a rebuilt AgentSession connects (agent_runner's session factory, the old activity is gone), a
        voice still pending from the closed session goes into the model options, so the new session connects with it
        instead of restarting after its first reply."""
        if self._pending_voice is not None and getattr(self, "_activity", None) is None:
            await self.update_session(voice=self._pending_voice)

    def _session_busy(self) -> bool:
        """True while a reply or a tool call runs: the agent state says so, or the current speech is not done (a
        realtime tool call runs while the state still says 'listening')."""
        session = self._active_session
        state = getattr(session, "agent_state", None)
        if state is not None and state not in INSTRUCTIONS_SAFE_STATES:
            return True
        if getattr(session, "user_state", None) == "speaking":
            return True  # C-50: a fresh reconnect now would cut off what the user is saying
        speech = getattr(session, "current_speech", None)
        done = getattr(speech, "done", None)
        return callable(done) and not done()

    def _retry_after_current_speech(self) -> None:
        """Tries again when the running speech ends (a finished one never re-arms: the next 'listening' state does)."""
        speech = getattr(self._active_session, "current_speech", None)
        add_done = getattr(speech, "add_done_callback", None)
        if callable(add_done) and not speech.done():
            add_done(lambda _speech: self._run_back(self._apply_when_safe()))

    async def on_agent_state(self, state: str) -> None:
        """The session's agent_state_changed (agent_runner): a pending update goes out once the agent listens."""
        if state in INSTRUCTIONS_SAFE_STATES:
            await self._apply_when_safe()

    async def _apply_when_safe(self) -> None:
        if self._pending_instructions is None and self._pending_voice is None:
            return
        if self._session_busy():
            self._retry_after_current_speech()
            return
        await self._apply_pending_session_update()

    async def _apply_pending_session_update(self) -> None:
        instructions, self._pending_instructions = self._pending_instructions, None
        voice, self._pending_voice = self._pending_voice, None
        if instructions is None and voice is None:
            return
        persona = self.persona_manager.active_persona_name
        activity = getattr(self, "_activity", None)
        if activity is None:  # C-50: the session closed meanwhile (a C-33 rebuild): kept for the next one
            await self._store_for_next_session(instructions, voice, persona)
            self._announce_persona(persona)
            return
        rt_session = getattr(activity, "realtime_llm_session", None)
        if instructions is not None:
            try:
                await super().update_instructions(instructions)
            except Exception as e:  # pylint: disable=broad-exception-caught  # never breaks the switch
                logger.warning("INSTRUCTIONS_UPDATE persona=%s not sent to the live session (%s); kept for the next",
                               persona, type(e).__name__)
                instructions = None
        if voice is not None:
            voice = await self._send_voice(rt_session, voice, persona)
        if instructions is None and voice is None:
            return
        reason = "+".join(name for name, value in (("instructions", instructions), ("voice", voice)) if value)
        restarted = restart_fresh(rt_session, reason)
        logger.info("INSTRUCTIONS_UPDATE persona=%s sent=live update=%s voice=%s restart=%s", persona, reason,
                    voice or "-", "fresh" if restarted else "none")
        self._announce_persona(persona)

    async def _send_voice(self, rt_session: Any, voice: str, persona: str) -> str | None:
        """C-50: the voice through the live session's own update_options (it compares with the options it shares with
        the model, then marks a restart); None when there is nothing to reconnect for: the voice is live already,
        the session has no update_options (then it is stored for the next session) or the update failed."""
        update = getattr(rt_session, "update_options", None)
        if not callable(update):
            await self._store_for_next_session(None, voice, persona)
            return None
        if getattr(getattr(rt_session, "_opts", None), "voice", None) == voice:
            logger.info("VOICE_UPDATE persona=%s voice=%s already live: no reconnect", persona, voice)
            return None
        try:
            update(voice=voice)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("VOICE_UPDATE %s failed (%s)", voice, type(e).__name__)
            return None
        return voice

    async def refresh_instructions(self) -> None:
        """C-49: composes the active persona's instructions again (after the wake-word mode changed) and sends them
        to the live session."""
        await self.update_instructions(build_instructions(self, self.persona_manager.active_persona_name))

    async def tool_toggle_gf_mode(self, active: bool) -> dict[str, Any]:
        """Toggle GF (Anna) persona mode."""
        await self.persona_manager.set_persona(active)
        return {"status": "success", "mode": "Anna" if active else "Jarvis"}

    async def tool_set_wake_word_mode(self, active: bool) -> dict[str, Any]:
        """Toggle the strict wake word enforcement mode."""
        self._wake_word_mode = active
        await self.refresh_instructions()  # C-49: the wake rule is in the prompt only while the mode is on
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
        if not isinstance(analysis, str):
            analysis = str(analysis)

        # C-48: a failed analysis is an error, never 'success' with the error text as the report.
        if _vision_analysis_failed(analysis):
            return {
                "status": "error",
                "message": f"Sir, camera view analyze nahi ho saka: {analysis}",
                "analysis": analysis,
            }

        return {
            "status": "success",
            "message": f"Sir, maine camera view analyze kar liya hai. Report yeh hai:\n\n{analysis}",
            "analysis": analysis,
        }

    async def tool_change_voice(self, voice_name: str) -> dict[str, Any]:
        """
        Change the AI's speaking voice (C-50): any of the 30 Gemini Live voices or an OpenAI-style alias (case does
        not matter). The switch applies from the next reply: the confirmation is still spoken in the old voice, then
        the session reconnects fresh with the new one (update_session). An unknown name lists the valid voices.
        """
        voice = resolve_voice(voice_name)
        if voice is None:
            return {"status": "error", "message": f"Voice '{voice_name}' nahi mili. {voices_help()}"}
        await self.request_voice(voice)
        return {"status": "success", "voice": voice, "applies": "next reply",
                "message": f"Sir, agle jawab se meri awaaz {voice} hogi."}

    async def request_voice(self, voice: str) -> None:
        """C-50: the voice for the live session from the next safe point (update_session); never the shared
        RealtimeModel options first, which would leave the live session nothing to compare and so no restart."""
        llm_any: Any = getattr(self, "llm", None) or self._local_llm
        if llm_any is not None and hasattr(llm_any, "voice"):
            llm_any.voice = voice
        await self.update_session(voice=voice)

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
        """
        Set the muted state (C-51, K-60). Mute turns the live session's microphone input off
        (session.input.set_audio_enabled(False): RoomIO keeps the track subscribed and drops its frames, so nothing
        reaches the model), unmute turns it on again. Only while no session is attached are the remote audio tracks
        unsubscribed instead (the pre-C-51 path, which went behind RoomIO's back), and the next unmute subscribes them
        again. Mute also clears the voice-ID buffer and pauses the legacy audio tap (agent_runner), unmute restarts
        it. Typed chat is answered either way: it never uses the microphone. mute_sync tells the dashboard.
        """
        self._muted = active
        if active:
            self._audio_buffer.clear()
            logger.info("🔇 Assistant MUTED. Audio buffer cleared.")
        else:
            logger.info("🔊 Assistant UNMUTED.")

        if self._active_session is not None:
            self._set_session_input(not active)
            if not active and self._tracks_unsubscribed:
                self._set_track_subscriptions(True)  # a mute without a session had unsubscribed them
        else:
            self._set_track_subscriptions(not active)
        self._sync_audio_tap(active)

        self._run_back(self.bridge_notifier.notify_event("mute_sync", active))

    def _set_session_input(self, enabled: bool) -> bool:
        """C-51: the live session's microphone input on or off (livekit-agents AgentInput.set_audio_enabled). False
        when the session has no such switch or it failed."""
        session_input = getattr(self._active_session, "input", None)
        if not callable(getattr(session_input, "set_audio_enabled", None)):
            logger.warning("MUTE: the session has no audio input switch; microphone %s not applied",
                           "on" if enabled else "off")
            return False
        try:
            session_input.set_audio_enabled(enabled)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("MUTE: microphone input switch failed (%s)", type(e).__name__)
            return False
        logger.info("🎤 Session microphone input %s", "ON" if enabled else "OFF")
        return True

    def _mute_room(self) -> Any:
        """The job's room for the no-session mute path: an older integration's session.room, else the job context's
        (an AgentSession has no room in livekit-agents 1.4.6); None outside a job."""
        room = getattr(self._active_session, "room", None)
        if room is not None:
            return room
        try:
            from livekit.agents import get_job_context  # pylint: disable=import-outside-toplevel
            return get_job_context().room
        except Exception:  # pylint: disable=broad-exception-caught  # RuntimeError: not inside a job
            return None

    def _set_track_subscriptions(self, subscribed: bool) -> None:
        """The no-session mute path (pre-C-51): every remote audio publication of the room (un)subscribed."""
        participants = getattr(self._mute_room(), "remote_participants", None)
        if not hasattr(participants, "values"):
            return
        changed = 0
        for participant in list(participants.values()):
            for publication in list(getattr(participant, "track_publications", {}).values()):
                if getattr(publication, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                    continue
                try:
                    publication.set_subscribed(subscribed)
                    changed += 1
                    logger.info("🎤 Audio track %s subscription set to: %s", publication.sid, subscribed)
                except Exception as e:
                    logger.error("Failed to update subscription for %s: %s", getattr(publication, "sid", "?"), e)
        if subscribed:
            self._tracks_unsubscribed = False
        elif changed:
            self._tracks_unsubscribed = True

    def _sync_audio_tap(self, muted: bool) -> None:
        """C-51: mute pauses the job's legacy voice-ID audio tap, unmute restarts it (attach_audio_tap)."""
        action = getattr(self._audio_tap, "pause" if muted else "resume", None) if self._audio_tap is not None else None
        if not callable(action):
            return
        try:
            action()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("MUTE: audio tap %s failed (%s)", "pause" if muted else "resume", type(e).__name__)

    def attach_audio_tap(self, tap: Any) -> None:
        """C-51 (K-60): the job's legacy voice-ID audio tap (pause()/resume(), agent_runner.UserAudioTaps); set_muted
        pauses and restarts it."""
        self._audio_tap = tap

    def set_wake_word_mode(self, active: bool, *, refresh: bool = True) -> None:
        """Toggle the strict wake word enforcement mode; wake_word_sync tells the dashboard. C-54: refresh=False leaves
        the instructions to the caller (a job's start composes them once, with the saved persona, so the dashboard is
        told only the persona the job runs as)."""
        self._wake_word_mode = active
        if refresh:
            self._run_back(self.refresh_instructions())  # C-49: the prompt follows the mode
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

    @property
    def conversation_router(self) -> Any:
        """The job's ConversationRouter (C-42), when one is attached."""
        return getattr(self, "_conversation_router", None)

    def attach_router(self, router: Any) -> None:
        """C-42: typed questions (handle_user_query, UI chat) go through this router into the live session."""
        self._conversation_router = router

    def request_end(self, reason: str) -> None:
        """C-23 (K-60 v9): ends this job ('paused_by_owner' from pause_agent; 'terminated_by_owner' from a
        shutdown_agent the owner confirmed, C-51). The first reason wins; the entrypoint then closes the session and
        calls ctx.shutdown(reason) instead of starting another session."""
        if self.end_reason is None:
            self.end_reason = reason
        self._stop_event.set()

    async def interrupt_speech(self, wait_s: float = 3.0) -> bool:
        """C-23 (K-60 v9) 'stop': interrupts the current and queued speech of the live session (AgentSession.interrupt)
        and waits for it at most wait_s; the session keeps listening and the job never ends here. C-51: force=True
        only for a speech that does not allow interruptions (the current one, or a queued one that made the plain
        interrupt fail), which 'stop' could not cut before. False when no session is attached or the interrupt
        failed."""
        session = self._active_session
        if session is None:
            return False
        replying = self._session_busy()  # before the interrupt: a reply (or a tool call's reply) was under way
        force = needs_forced_interrupt(session)
        try:
            result = session.interrupt(force=True) if force else session.interrupt()
        except RuntimeError as e:
            # livekit-agents 1.4.6: a queued speech that does not allow interruptions refuses the plain interrupt; a
            # session without an activity is not running, and then nothing is forced either
            if force or getattr(session, "_activity", None) is None:
                logger.debug("STOP: interrupt unavailable (%s)", type(e).__name__)
                return False
            force = True
            try:
                result = session.interrupt(force=True)
            except Exception as forced_error:  # pylint: disable=broad-exception-caught
                logger.debug("STOP: forced interrupt unavailable (%s)", type(forced_error).__name__)
                return False
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("STOP: interrupt unavailable (%s)", type(e).__name__)
            return False
        if force:
            logger.info("STOP: forced interrupt (a speech that does not allow interruptions)")
        if asyncio.isfuture(result) or asyncio.iscoroutine(result):
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.ensure_future(result)), wait_s)
            except asyncio.TimeoutError:
                logger.debug("STOP: interrupt still finishing after %.0f s", wait_s)
            except Exception as e:  # pylint: disable=broad-exception-caught  # (a cancel is a BaseException)
                logger.debug("STOP: interrupt failed (%s)", type(e).__name__)
        if replying:
            self._cut_server_reply()
        return True

    def _cut_server_reply(self) -> None:
        """C-51 live check (09 Oct): with Gemini's automatic activity detection the plugin's interrupt() sends the
        server nothing, so a stopped answer went on being generated server side and the next typed question waited
        behind it (no reply within 45 s). While the realtime session's current generation is still open, a fresh
        reconnect (C-49 restart_fresh) ends it; the plugin replays the chat history, the stopped answer's spoken part
        included. A generation the server had already finished (only the playout was left) needs nothing."""
        rt_session = getattr(getattr(self, "_activity", None), "realtime_llm_session", None)
        generation = getattr(rt_session, "_current_generation", None)  # pinned livekit-plugins-google 1.4.6 members
        if generation is None or getattr(generation, "_done", False) is True:
            return
        if restart_fresh(rt_session, "stop"):
            logger.info("STOP: the stopped reply is cut server side too (fresh reconnect)")

    def attach_session(self, session: AgentSession) -> None:
        """
        Stores the active LiveKit session for dynamic updates and starts vision context polling.
        Called by AgentRunner after session start.
        """
        self._active_session = session
        # C-49: a new session starts with the current self._instructions, so pending ones it already has are dropped.
        # C-50: kept when its Gemini session connected before they were set (a switch that raced a rebuild's start()).
        if self._pending_instructions is not None and self._pending_instructions == self._live_instructions():
            self._pending_instructions = None
        if self._pending_instructions is not None or self._pending_voice is not None:
            # C-50: the new session emitted its first 'listening' inside start(), before agent_runner registered
            # agent_state_changed: without this the update would wait for one more reply
            self._run_back(self._apply_when_safe())
        # C-51, a backstop in both directions: agent_runner switches a session started while muted off before
        # session.start, but a mute or an unmute that arrived while start() ran reached only the previous session
        enabled = getattr(getattr(session, "input", None), "audio_enabled", None)
        if isinstance(enabled, bool):
            if enabled == self._muted:
                self._set_session_input(not self._muted)
        elif self._muted:
            self._set_session_input(False)
        logger.info("Live AgentSession attached to BrainAssistant (User: %s)", self.user_id)
        if getattr(self, "_probe", False):
            return  # K-57 (C-39): no vision loop in a probe or canary job
        try:
            self.vision_handler.start_loop()
        except Exception as e:  # pylint: disable=broad-exception-caught
            _optional_handler_failed("Vision loop", e)

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
        """Logic to switch personas dynamically. C-54: a spoken switch is saved and synced at once (the settings
        service's hook), like the toggle_persona tool."""
        if is_anna and not self.persona_manager.is_anna:
            await self.persona_manager.set_persona(True)
            self._live_settings_changed("wake_word_persona")
        elif is_jarvis and self.persona_manager.is_anna:
            await self.persona_manager.set_persona(False)
            self._live_settings_changed("wake_word_persona")

    def _live_settings_changed(self, reason: str) -> None:
        """C-54 (K-61): a tool or a spoken wake word changed the persona, voice or wake word of the live session only.
        The job's settings service (on_live_settings_change, src/core/agent_settings) saves it and sends settings_sync
        in a task of its own, so the bridge's copy never falls behind the live state and a later settings_bootstrap
        cannot undo it. Nothing without a service; never raises."""
        hook = getattr(self, "on_live_settings_change", None)
        if not callable(hook):
            return
        try:
            hook(reason)  # pylint: disable=not-callable  # checked just above
        except Exception as e:  # pylint: disable=broad-exception-caught  # never breaks the tool or the turn
            logger.debug("SETTINGS live change not passed on (%s)", type(e).__name__)

    def _extract_text_from_message(self, message: Any) -> str:
        """Safely extracts text."""
        try:
            raw = getattr(message, 'content', '')
            if isinstance(raw, list):
                parts = []
                for part in raw:
                    if isinstance(part, str):
                        parts.append(part)
                    elif hasattr(part, "text"):
                        parts.append(str(part.text))
                    elif hasattr(part, "content"):
                        parts.append(str(part.content))
                return " ".join(parts).lower()
            return str(raw).lower()
        except (AttributeError, TypeError, ValueError):
            return ""

    def _append_turn_context_message(self, turn_ctx: Any, message: llm.ChatMessage) -> str:
        """Append a system/context message to a turn context: livekit's bare llm.ChatContext (items) or a legacy
        holder with .chat_ctx. C-50: returns the message text, '' when nothing could take it."""
        text = append_turn_note(turn_ctx, message)
        if not text:
            logger.debug("No appendable turn chat context found for message injection.")
        return text

    async def _handle_wake_word(self, text: str) -> tuple[bool, bool]:
        """Checks for wake words."""
        is_j = bool(_JARVIS_WAKE.search(text))
        is_a = bool(_ANNA_WAKE.search(text))  # C-49: the same names the prompt's wake rule lists
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
            if self._append_turn_context_message(turn_ctx, llm.ChatMessage(
                    role="system", content=[f"[LONG-TERM MEMORY CONTEXT]: {context_str}"])):
                logger.info("🧠 Scored Memory Injected: %d items", len(filtered_sem))

    async def _inject_reasoning_and_memory(self, text: str, turn_ctx: Any,
                                           is_anna: bool = False):
        """Injects reasoning and memory with confidence filtering."""
        try:
            await self._inject_memory_context(text, turn_ctx, is_anna)
            res = await process_with_advanced_reasoning(
                text, self.conversation_history, is_anna=is_anna)

            self._run_back(self.bridge_notifier.notify_reasoning(res))
            if res.get("is_agentic") and res.get("plan"):
                self._append_turn_context_message(turn_ctx, llm.ChatMessage(
                    role="system", content=[f"[EXECUTION PLAN]: {res['plan']}"]))
        except Exception as e:
            logger.error("Reasoning injection error: %s", e)

    async def _notify_ui_persona(self, persona: str):
        """Sends notification to UI Bridge about persona change."""
        await self.bridge_notifier.notify_persona(persona)

    async def handle_user_query(self, query: str) -> str:
        """
        EntryPoint for typed user queries. C-42: with a ConversationRouter attached the query goes into the
        live session (the same brain, tools and memory as voice) and the router emits the messages itself,
        so this returns ''. Without a router: the old template path.
        """
        router = self.conversation_router
        if router is not None:
            await router.handle_text(query, origin="query")
            return ""
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

        sample_rate = int(getattr(self, "_audio_sample_rate", 16000) or 16000)
        required_bytes = sample_rate * 2

        # Require at least 1.0 seconds of 16-bit PCM audio for a reliable check.
        if len(self._audio_buffer) < required_bytes:
            logger.warning("🛡️ Voice ID: Audio segment too short for verification. ACCESS DENIED.")
            return False

        # Convert deque of integers (bytes) back to a single bytes object
        audio_payload = bytes(self._audio_buffer)
        is_match, score = await self.voice_id_engine.verify_bytes(
            audio_payload,
            sample_rate=sample_rate,
            threshold=0.65,
        )
        
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
        thinking_started = False
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
        thinking_started = True
        if self._muted:
            raise StopResponse()

        try:
            # Parallelize non-blocking context injections to reduce turnaround time
            async def run_prep():
                results = await asyncio.gather(
                    self._handle_anna_upset_state(sanitized_text, turn_ctx),
                    self._inject_emotional_context(sanitized_text, turn_ctx),
                    self.vision_handler.handle_vision_query(sanitized_text, new_message, turn_ctx),
                    self.vision_handler.inject_window_context(turn_ctx),
                    self._inject_reasoning_and_memory(sanitized_text, turn_ctx, is_anna),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    if isinstance(result, Exception):
                        logger.warning("Turn preparation step failed: %s", result)

            await run_prep()

            self.conversation_history.append(
                {"role": "user", "content": sanitized_text})
            if len(self.conversation_history) > 20:
                self.conversation_history.pop(0)

            # Super call for legacy tool execution
            response = await super().on_user_turn_completed(turn_ctx, new_message)

            # C-114: the chat context is cut by the job's ChatTruncator (every 20 conversation items, agent_runner)

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
        finally:
            if thinking_started:
                self._run_back(self.bridge_notifier.notify_thinking("STOP"))

    async def shutdown(self):
        """Gracefully shuts down the assistant and its modular services."""
        logger.info("Assistant shutting down...")
        # 1. Stop Vision Loop
        try:
            await self.vision_handler.stop_loop()
        except Exception as e:  # pylint: disable=broad-exception-caught
            _optional_handler_failed("Vision loop stop", e)
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
