"""
# bridge_notifier.py
Consolidated bridge notifier that routes to the centralized services.utils.jarvis_bridge.
"""
import logging
from typing import Any

from services.utils.jarvis_bridge import (
    notify_event,
    notify_thinking,
    notify_user_speaking,
)

logger = logging.getLogger("BRIDGE-NOTIFIER")

class BridgeNotifier:
    """
    Compatibility wrapper for notify_event and other bridge functions.
    Consolidates logic into services.utils.jarvis_bridge.
    """

    async def notify_event(self, event_type: str, payload: Any) -> None:
        await notify_event(event_type, payload)

    async def notify_reasoning(self, reasoning_result: dict[str, Any]) -> None:
        """Sends intelligence/reasoning metadata to the UI Bridge."""
        if not isinstance(reasoning_result, dict):
            reasoning_result = {"intent_analysis": {}, "plan": [], "raw": str(reasoning_result)}

        intent_analysis = reasoning_result.get("intent_analysis", {})
        if not isinstance(intent_analysis, dict):
            intent_analysis = {"primary_intent": str(intent_analysis)}

        await self.notify_event("reasoning", {
            "intent": intent_analysis.get("primary_intent"),
            "confidence": intent_analysis.get("confidence_scores", {}),
            "plan": reasoning_result.get("plan", []),
            "is_ambiguous": intent_analysis.get("is_ambiguous", False),
        })

    async def notify_thinking(self, state: str) -> None:
        await notify_thinking(state)

    async def notify_persona(self, persona: str) -> None:
        await self.notify_event("persona_change", persona)

    async def notify_user_speaking(self, active: bool) -> None:
        await notify_user_speaking(active)

    async def notify_wake_word(self, active: bool) -> None:
        await self.notify_event("wake_word_sync", active)

    async def aclose(self) -> None:
        """Compatibility method."""
        pass

# Global instances
bridge_notifier = BridgeNotifier()
