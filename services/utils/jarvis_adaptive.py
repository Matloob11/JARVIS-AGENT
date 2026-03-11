"""
# services/utils/jarvis_adaptive.py
Autonomous Adaptive Learning Engine for JARVIS.
Learns from user feedback to refine behavior and intent scoring.
"""

import json
import os
import random
from typing import Dict, Optional
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-ADAPTIVE")


class JarvisAdaptiveEngine:
    """
    Autonomous Adaptive Learning Engine for JARVIS.
    Learns from user feedback and interaction history to refine behavior.
    """
    def __init__(self, data_path="conversations/learning_state.json"):
        """Initializes the adaptive engine with state path."""
        self.data_path = data_path
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Loads persistent learning state."""
        if os.path.exists(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (IOError, json.JSONDecodeError):
                logger.warning("Failed to load learning state. resetting.")

        return {
            "intent_weights": {},
            "ab_results": {},
            "successful_patterns": []
        }

    def _save_state(self):
        """Persists learning state."""
        try:
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except IOError as e:
            logger.error("Failed to save learning state: %s", e)

    def select_strategy(self, intent_name: str) -> str:
        """
        Selects a reasoning strategy (A or B) based on historical success.
        Used for autonomous A/B testing of behaviors.
        """
        strategies = ["strategy_a", "strategy_b"]
        history = self.state["ab_results"].get(intent_name, {"a": 0, "b": 0})

        # 1. Exploration phase (if no data, randomize)
        if sum(history.values()) < 10:
            return random.choice(strategies)

        # 2. Exploitation phase (select best performing)
        return "strategy_a" if history["a"] >= history["b"] else "strategy_b"

    def record_feedback(self, intent_name: str, strategy: str, score: float):
        """Updates internal weights based on interaction score."""
        if intent_name not in self.state["ab_results"]:
            self.state["ab_results"][intent_name] = {"a": 0, "b": 0}

        strat_key = "a" if strategy == "strategy_a" else "b"
        self.state["ab_results"][intent_name][strat_key] += score

        # Update General Intent Weight
        current_weight = self.state["intent_weights"].get(intent_name, 1.0)
        new_weight = current_weight + (0.05 * score)
        self.state["intent_weights"][intent_name] = max(
            0.2, min(2.0, new_weight))

        logger.info("Adaptive update for %s: Weight=%.2f",
                    intent_name, self.state["intent_weights"][intent_name])
        self._save_state()

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def log_interaction(self, session_id: str, user_input: str,
                              response: Optional[str], success: bool,
                              error_message: Optional[str] = None):
        """Async helper to log interaction results."""
        _ = session_id  # Reserved for future session-specific learning
        pattern = {
            "input": user_input,
            "success": success,
            "has_response": response is not None,
            "error": error_message
        }
        if success:
            self.state["successful_patterns"].append(pattern)
            if len(self.state["successful_patterns"]) > 1000:
                self.state["successful_patterns"].pop(0)

        self._save_state()

    def get_intent_adjustment(self, intent_name: str) -> float:
        """Returns a multiplier for intent confidence scoring."""
        return self.state["intent_weights"].get(intent_name, 1.0)


# Global Singleton
adaptive_engine = JarvisAdaptiveEngine()
