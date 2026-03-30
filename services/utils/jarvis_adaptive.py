import asyncio
import json
import random
import threading
from pathlib import Path
from typing import Any, cast

from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-ADAPTIVE")


class JarvisAdaptiveEngine:
    """
    Autonomous Adaptive Learning Engine for JARVIS.
    Learns from user feedback and interaction history to refine behavior.
    """
    def __init__(self, data_path: str = "conversations/learning_state.json") -> None:
        """Initializes the adaptive engine with state path."""
        self._lock = threading.Lock()
        self.data_path: Path = Path(data_path)
        if self.data_path.parent:
            self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self.state: dict[str, Any] = self._load_state()
        self.stress_level: str = "NORMAL" # NORMAL, STRESSED, CRITICAL

    def _load_state(self) -> dict[str, Any]:
        """Loads persistent learning state."""
        if self.data_path.exists():
            try:
                with self.data_path.open(encoding="utf-8") as f:
                    return cast(dict[str, Any], json.load(f))
            except (OSError, json.JSONDecodeError):
                logger.warning("Failed to load learning state. resetting.")

        return {
            "intent_weights": {},
            "ab_results": {},
            "successful_patterns": [],
        }

    def _save_state(self) -> None:
        """Persists learning state with thread-safety."""
        with self._lock:
            try:
                with self.data_path.open("w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2)
            except OSError as e:
                logger.error("Failed to save learning state: %s", e)

    def analyze_system_load(self) -> str:
        """
        AUTONOMOUS ADAPTATION: Detects system stress via task health, circuit states, and real-time vitals.
        Transitions system between performance tiers based on holistic health.
        """
        from services.utils.jarvis_autonomous import autonomous_protector
        from services.utils.jarvis_health import health_monitor
        from services.utils.jarvis_resilience import resilience_manager

        # 1. Component Level Health
        status = autonomous_protector.get_task_status()
        failures = sum(s["failures"] for s in status.values())
        open_circuits = len(resilience_manager._breakers) # Simplified check for total breakers if needed
        # Actually resilience_manager doesn't have a get_open_circuits in the view I saw
        # Let's check the code again... wait, it used len(resilience_manager.get_open_circuits())
        # I should probably use what was there but add Vitals.

        open_circuits = len([b for b in resilience_manager._breakers.values() if b.state.value == "OPEN"])

        # 2. Resource Level Health (Vitals)
        vitals = health_monitor.get_system_vitals()
        cpu_usage = vitals.get("cpu", {}).get("usage", 0.0)
        mem_usage = vitals.get("memory", {}).get("percent", 0.0)
        disk_usage = vitals.get("disk", {}).get("percent", 0.0)

        # 3. Decision Matrix
        if open_circuits > 5 or failures > 20 or cpu_usage > 90 or mem_usage > 90:
            self.stress_level = "CRITICAL"
        elif open_circuits > 2 or failures > 5 or cpu_usage > 75 or mem_usage > 80 or disk_usage > 95:
            self.stress_level = "STRESSED"
        else:
            self.stress_level = "NORMAL"

        if self.stress_level != "NORMAL":
            logger.warning("🧠 Load Adaptation Triggered [%s]: CPU:%.1f%% | MEM:%.1f%% | FAIL:%d",
                           self.stress_level, cpu_usage, mem_usage, failures)

        return self.stress_level

    def select_strategy(self, intent_name: str) -> str:
        """
        Selects a reasoning strategy (A or B) based on historical success.
        Used for autonomous A/B testing of behaviors.
        """
        strategies: list[str] = ["strategy_a", "strategy_b"]
        history: dict[str, float] = self.state["ab_results"].get(intent_name, {"a": 0.0, "b": 0.0})

        # 1. Exploration phase (if no data, randomize)
        if sum(history.values()) < 10:
            return random.choice(strategies)

        # 2. Exploitation phase (select best performing)
        return "strategy_a" if history["a"] >= history["b"] else "strategy_b"

    def record_feedback(self, intent_name: str, strategy: str, score: float) -> None:
        """Updates internal weights based on interaction score."""
        if intent_name not in self.state["ab_results"]:
            self.state["ab_results"][intent_name] = {"a": 0.0, "b": 0.0}

        strat_key: str = "a" if strategy == "strategy_a" else "b"
        self.state["ab_results"][intent_name][strat_key] += score

        # Update General Intent Weight
        current_weight: float = float(self.state["intent_weights"].get(intent_name, 1.0))
        new_weight: float = current_weight + (0.05 * score)
        self.state["intent_weights"][intent_name] = max(
            0.2, min(2.0, new_weight))

        logger.info("Adaptive update for %s: Weight=%.2f",
                    intent_name, self.state["intent_weights"][intent_name])
        self._save_state()

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def log_interaction(self, _session_id: str, _user_input: str,
                               response: str | None, success: bool,
                               error_message: str | None = None) -> None:
        """Async helper to log interaction results."""
        pattern = {
            "has_response": response is not None,
            "error": error_message,
        }
        if success:
            patterns: list[dict[str, Any]] = cast(list[dict[str, Any]], self.state["successful_patterns"])
            patterns.append(pattern)
            if len(patterns) > 1000:
                patterns.pop(0)

        await asyncio.to_thread(self._save_state)

    def get_intent_adjustment(self, intent_name: str) -> float:
        """Returns a multiplier for intent confidence scoring."""
        return float(self.state["intent_weights"].get(intent_name, 1.0))


# Global Singleton
adaptive_engine: JarvisAdaptiveEngine = JarvisAdaptiveEngine()
