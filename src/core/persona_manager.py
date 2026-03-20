from typing import Any
from livekit.agents import llm
from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_prompt import BEHAVIOR_PROMPT, ANNA_BEHAVIOR_PROMPT
from services.ai_core.jarvis_identity import jarvis_id
from services.ai_core.jarvis_reasoning import context_analyzer

logger = setup_logger("PERSONA-MANAGER")

class PersonaManager:
    """
    Handles persona state and instruction/voice updates for the assistant.
    """

    def __init__(self, assistant_ref):
        self._assistant = assistant_ref
        self._gf_mode_active = False

    @property
    def is_anna(self) -> bool:
        return self._gf_mode_active

    @property
    def active_persona_name(self) -> str:
        return "anna" if self._gf_mode_active else "jarvis"

    async def set_persona(self, is_anna: bool):
        """Switches the assistant's persona."""
        if self._gf_mode_active == is_anna:
            return
        
        self._gf_mode_active = is_anna
        await self.update_assistant_persona()

    async def update_assistant_persona(self):
        """Applies current persona settings to the assistant."""
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

        await self._assistant.update_instructions(instr)
        self._assistant.llm.voice = voice

        # Update session modality and voice
        active_session = getattr(self._assistant, "_active_session", None)
        if active_session:
            try:
                # pylint: disable=protected-access
                self._update_session_options(voice)
                # Notify UI (will be handled by bridge_notifier in agent_core refactor)
            except (AttributeError, RuntimeError) as e:
                logger.warning("Failed to sync session settings: %s", e)

    def _update_session_options(self, voice: str):
        """Syncs voice and modality to the active LiveKit session."""
        try:
            # Sync options struct
            session_options = getattr(self._assistant, "_session_options", None)
            if session_options:
                if self._gf_mode_active:
                    session_options.voice_id = "anna_v3_premium"
                    session_options.response_modality = "audio"
                else:
                    session_options.voice_id = "jarvis_v2"
                    session_options.response_modality = "text"

            # Dynamic session update
            active_session = getattr(self._assistant, "_active_session", None)
            activity = getattr(active_session, "_activity", None)
            if activity:
                rt_session = getattr(activity, "_rt_session", None)
                if rt_session:
                    rt_session.update_options(voice=voice)
                    logger.info("Session voice synced to: %s", voice)
        except (AttributeError, RuntimeError) as e:
            logger.debug("Session option sync failed: %s", e)

    async def handle_anna_upset_state(self, text: str, turn_ctx: Any):
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
                role="assistant", content=["Demand sorry."]
            ))

    async def inject_emotional_context(self, text: str, turn_ctx: Any, history: list):
        """Injects emotional hints into the reasoning context."""
        try:
            mem = await self._assistant.memory_extractor.memory.get_recent_context(max_messages=5)
            curr = context_analyzer.analyze_context(text, mem)
            if self._gf_mode_active and curr.get("user_mood") == "upset":
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="assistant", content=["Manao him."]
                ))
        except (AttributeError, KeyError, RuntimeError) as e:
            logger.error("Emotional context injection failed: %s", e)
