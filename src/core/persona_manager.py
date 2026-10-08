import os
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from livekit.agents import llm

from services.ai_core.jarvis_identity import jarvis_id
from services.ai_core.jarvis_prompt import compose_instructions, normalize_turn_mode
from services.ai_core.jarvis_reasoning import context_analyzer
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("PERSONA-MANAGER")

PROJECT_START_DATE = datetime(2025, 8, 2)
TURN_MODE_ENV = "JARVIS_TURN_MODE"  # K-69: 'gated' after the VR-22 spike decision, else 'fast'
OWNER_CONTACT_ENV = "JARVIS_OWNER_CONTACT"  # K-69 v32: the CV contact line, only in the gitignored .env
# The gated runtime (C-66) does not exist yet: until it does, 'gated' is treated as 'fast', so the prompt's wake rule,
# today the only wake-word gate for speech, is never dropped by an env setting alone.
GATED_RUNTIME_AVAILABLE = False
_gated_warned = False


def project_age_days(now: datetime | None = None) -> int:
    """Days since the project started (2 August 2025), for the prompts' project duration line."""
    return ((now or datetime.now()) - PROJECT_START_DATE).days


def runtime_turn_mode() -> str:
    """The turn mode the prompt is composed for: JARVIS_TURN_MODE, with 'gated' only once a gated runtime exists."""
    global _gated_warned  # pylint: disable=global-statement
    mode = normalize_turn_mode(os.getenv(TURN_MODE_ENV))
    if mode == "gated" and not GATED_RUNTIME_AVAILABLE:
        if not _gated_warned:
            logger.warning("%s=gated but no gated runtime yet (C-66): the prompt keeps the fast-mode wake rule",
                           TURN_MODE_ENV)
            _gated_warned = True
        return "fast"
    return mode


def build_instructions(assistant: Any, persona: str, *, wake_word: bool | None = None) -> str:
    """
    C-49: the assistant's full instructions for a persona ('jarvis' | 'anna') from
    jarvis_prompt.compose_instructions with its live context: date and city, identity facts, the wake-word mode
    (wake_word, else the assistant's), JARVIS_TURN_MODE, the outputs folder, JARVIS_CV_PATH, and for ANNA her mood
    and the user and Sir backgrounds from the identity store.
    """
    anna_context = None
    if persona == "anna":
        state = jarvis_id.get_anna_state()
        anna_context = {"mood": state.get("mood"), "is_upset": state.get("is_upset"),
                        "user_background": jarvis_id.data.get("user_background"),
                        "sir_background": jarvis_id.data.get("sir_background")}
    if wake_word is None:
        wake_word = bool(getattr(assistant, "_wake_word_mode", True))
    return compose_instructions(
        persona,
        current_date=getattr(assistant, "current_date", None) or datetime.now().strftime("%Y-%m-%d"),
        current_city=getattr(assistant, "current_city", None) or "",
        project_age_days=project_age_days(),
        identity_context=jarvis_id.get_context(),
        turn_mode=runtime_turn_mode(),
        wake_word=wake_word,
        outputs_dir=getattr(config, "outputs_dir", None) or "Jarvis_Outputs",
        cv_path=getattr(config, "cv_path", None),
        anna_context=anna_context,
        project_root=getattr(config, "project_root", None) or "",
        owner_contact=os.getenv(OWNER_CONTACT_ENV, ""),
    )


@runtime_checkable
class AssistantProtocol(Protocol):
    """Protocol for the assistant to decouple PersonaManager from AgentCore."""
    llm: Any
    async def update_instructions(self, instructions: str) -> None: ...
    @property
    def memory_extractor(self) -> Any: ...

class PersonaManager:
    """
    Handles persona state and instruction/voice updates for the assistant.
    """

    def __init__(self, assistant_ref: AssistantProtocol) -> None:
        self._assistant = assistant_ref
        self._gf_mode_active: bool = False

    @property
    def is_anna(self) -> bool:
        """Returns True if the assistant is currently in Anna mode."""
        return self._gf_mode_active

    @property
    def active_persona_name(self) -> str:
        """Returns the name of the currently active persona."""
        return "anna" if self._gf_mode_active else "jarvis"

    async def set_persona(self, is_anna: bool) -> None:
        """Switches the assistant's persona."""
        if self._gf_mode_active == is_anna:
            return

        self._gf_mode_active = is_anna
        await self.update_assistant_persona()

    async def update_assistant_persona(self) -> None:
        """Applies current persona settings to the assistant. C-49: both personas get the composed instructions
        (every tool protocol included) and the assistant's update_instructions sends them to the live session."""
        instr = build_instructions(self._assistant, self.active_persona_name)
        voice = "Aoede" if self._gf_mode_active else "Charon"  # Standard Capitalized Gemini Voices

        await self._assistant.update_instructions(instr)
        # Update underlying LLM model property
        llm_obj = getattr(self._assistant, "llm", None) or getattr(self._assistant, "_local_llm", None)
        if hasattr(llm_obj, "voice"):
             llm_obj.voice = voice

        # Update session modality and voice
        active_session = getattr(self._assistant, "_active_session", None)
        if active_session:
            try:
                # pylint: disable=protected-access
                self._update_session_options(voice, instr)
            except (AttributeError, RuntimeError) as e:
                logger.warning("Failed to sync session settings: %s", e)

    def _update_session_options(self, voice: str, _instructions: str) -> None:
        """Syncs voice and instructions while avoiding keyword errors on wrapper sessions."""
        try:
            # Sync session options struct if present
            session_options = getattr(self._assistant, "_session_options", None)
            if session_options:
                session_options.voice_id = voice
                session_options.response_modality = "audio"

            active_session = getattr(self._assistant, "_active_session", None)
            if not active_session:
                return

            # C-49: the instructions already went to the live session through the assistant's update_instructions
            # (AgentSession.update_options takes no instructions; that call only raised a swallowed TypeError).

            # Update Voice (Requires Multimodal RealtimeSession)
            # We dig for the internal RT session to find 'voice' parameter support
            rt_session = None
            if hasattr(active_session, "_session"):
                 # Direct session (RealtimeSession)
                 rt_session = getattr(active_session, "_session")
            elif hasattr(active_session, "_activity"):
                 activity = getattr(active_session, "_activity")
                 if hasattr(activity, "_rt_session"):
                      rt_session = getattr(activity, "_rt_session")
            
            # If the top level WAS the rt_session all along
            if not rt_session and hasattr(active_session, "update_options"):
                 rt_session = active_session

            if rt_session:
                try:
                    # Gemini Multimodal Live uses 'voice' in update_options
                    # We call it with voice only to avoid duplicate instruction pushes if already done
                    rt_session.update_options(voice=voice)
                    logger.info("Voice switched to: %s on internal RT session.", voice)
                except TypeError as e:
                    logger.error("Internal session doesn't support 'voice' update: %s", e)
            else:
                 logger.warning("No RealtimeSession found for voice update.")

        except Exception as e:
            logger.error("Session sync failed critically: %s", e)

    async def handle_anna_upset_state(self, text: str, turn_ctx: Any) -> None:
        """Processes Anna's upset state based on input."""
        if not self._gf_mode_active:
            return
        state = jarvis_id.get_anna_state()
        if not state.get("is_upset"):
            return
        if "sorry" in text.lower():
            await jarvis_id.set_anna_mood("loving", False)
            # C-49: ANNA is active already (set_persona would return at once): compose her prompt again so the
            # live session gets the new mood
            await self.update_assistant_persona()
        else:
            turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                role="assistant", content=["Demand sorry."],
            ))

    async def inject_emotional_context(self, text: str, turn_ctx: Any, history: list[Any]) -> None:
        """Injects emotional hints into the reasoning context."""
        try:
            mem = await self._assistant.memory_extractor.memory.get_recent_context(max_messages=5)
            curr = context_analyzer.analyze_context(text, mem)
            if self._gf_mode_active and curr.get("user_mood") == "upset":
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="assistant", content=["Manao him."],
                ))
        except (AttributeError, KeyError, RuntimeError) as e:
            logger.error("Emotional context injection failed: %s", e)
