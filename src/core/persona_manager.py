from typing import Any, Protocol, runtime_checkable

from livekit.agents import llm

from services.ai_core.jarvis_identity import jarvis_id
from services.ai_core.jarvis_prompt import ANNA_BEHAVIOR_PROMPT, BEHAVIOR_PROMPT
from services.ai_core.jarvis_reasoning import context_analyzer
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("PERSONA-MANAGER")

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
        """Applies current persona settings to the assistant."""
        if self._gf_mode_active:
            state = jarvis_id.get_anna_state()
            
            # Real-time project age calculation
            from datetime import datetime
            project_start_date = datetime(2025, 8, 2)
            days_since_start = (datetime.now() - project_start_date).days

            instr = ANNA_BEHAVIOR_PROMPT.format(
                mood=state["mood"],
                is_upset=state["is_upset"],
                user_background=jarvis_id.data.get("user_background"),
                sir_background=jarvis_id.data.get("sir_background"),
                project_age_days=str(days_since_start)
            )
            voice = "Aoede" # Standard Capitalized Gemini Voice
        else:
            instr = f"{BEHAVIOR_PROMPT}\n{jarvis_id.get_context()}"
            voice = "Charon" # Standard Capitalized Gemini Voice

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

    def _update_session_options(self, voice: str, instructions: str) -> None:
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

            # 1. Update Instructions (Safe for AgentSession)
            if hasattr(active_session, "update_options"):
                try:
                    active_session.update_options(instructions=instructions)
                    logger.debug("Instructions updated on high-level session.")
                except TypeError:
                    # Fallback if AgentSession.update_options doesn't exist/work
                    pass

            # 2. Update Voice (Requires Multimodal RealtimeSession)
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
            await self.set_persona(True)
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
