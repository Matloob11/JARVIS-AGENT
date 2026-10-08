"""
# bridge_notifier.py
Consolidated bridge notifier that routes to the centralized services.utils.jarvis_bridge.

notify_reasoning sends the Reasoning panel's event in the K-67 (CT-VC-REASONING) shape: plan as readable
'Action: description' strings (today's UI renders them as text) plus plan_steps [{step, action, description}],
the top-5 intent confidences (0-1), is_ambiguous, and for a user message its source and msg_id (= turn_id).
"""
import logging
import time
from typing import Any

from services.utils.jarvis_bridge import (
    notify_event,
    notify_thinking,
    notify_user_speaking,
)

logger = logging.getLogger("BRIDGE-NOTIFIER")

PLAN_TEXT_MAX = 200  # characters per plan string
CONFIDENCE_TOP = 5
REASONING_SOURCES = ("voice", "text")


def plan_texts(plan: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """K-67: (plan strings 'Action: description', plan_steps [{step, action, description}]) from jarvis_reasoning's
    plan: dict steps with 'description' (or 'desc'); a plain string step is kept as its text."""
    strings: list[str] = []
    steps: list[dict[str, Any]] = []
    for number, item in enumerate(plan if isinstance(plan, list) else [], 1):
        if isinstance(item, dict):
            action = str(item.get("action") or "").strip()
            description = str(item.get("description") or item.get("desc") or "").strip()
            step = item.get("step")
            step = step if isinstance(step, int) and not isinstance(step, bool) else number
        else:
            action, description, step = "", str(item).strip(), number
        text = f"{action}: {description}" if action and description else (action or description)
        if not text:
            continue
        strings.append(text[:PLAN_TEXT_MAX])
        steps.append({"step": step, "action": action, "description": description[:PLAN_TEXT_MAX]})
    return strings, steps


def top_confidence(scores: Any, limit: int = CONFIDENCE_TOP) -> dict[str, float]:
    """The `limit` highest intent scores, each clamped to 0..1; anything that is no number is left out."""
    if not isinstance(scores, dict):
        return {}
    clean: dict[str, float] = {}
    for name, value in scores.items():
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:  # not NaN
            clean[str(name)] = min(max(number, 0.0), 1.0)
    return dict(sorted(clean.items(), key=lambda item: item[1], reverse=True)[:limit])


def reasoning_payload(reasoning_result: Any, source: str | None = None, msg_id: str | None = None,
                      ts: float | None = None) -> dict[str, Any]:
    """The K-67 'reasoning' payload of a jarvis_reasoning result."""
    if not isinstance(reasoning_result, dict):
        reasoning_result = {"intent_analysis": {}, "plan": []}
    intent_analysis = reasoning_result.get("intent_analysis", {})
    if not isinstance(intent_analysis, dict):
        intent_analysis = {"primary_intent": str(intent_analysis)}
    plan, plan_steps = plan_texts(reasoning_result.get("plan", []))
    payload: dict[str, Any] = {
        "intent": intent_analysis.get("primary_intent"),
        "confidence": top_confidence(intent_analysis.get("confidence_scores", {})),
        "is_ambiguous": bool(intent_analysis.get("is_ambiguous", False)),
        "plan": plan,
        "plan_steps": plan_steps,
        "ts": time.time() if ts is None else ts,
    }
    if source in REASONING_SOURCES:
        payload["source"] = source
    if isinstance(msg_id, str) and msg_id:
        payload["msg_id"] = payload["turn_id"] = msg_id
    return payload


class BridgeNotifier:
    """
    Compatibility wrapper for notify_event and other bridge functions.
    Consolidates logic into services.utils.jarvis_bridge.
    """

    async def notify_event(self, event_type: str, payload: Any) -> None:
        await notify_event(event_type, payload)

    async def notify_reasoning(self, reasoning_result: dict[str, Any], source: str | None = None,
                               msg_id: str | None = None) -> None:
        """Sends intelligence/reasoning metadata to the UI Bridge (K-67)."""
        await self.notify_event("reasoning", reasoning_payload(reasoning_result, source=source, msg_id=msg_id))

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
