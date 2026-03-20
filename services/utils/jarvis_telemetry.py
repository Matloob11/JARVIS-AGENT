"""
# services/utils/jarvis_telemetry.py
Production telemetry system for JARVIS.
Tracks user interaction patterns, success rates, and system performance.
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, Any, Optional
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-TELEMETRY")


class JarvisTelemetry:
    """
    Production telemetry system for JARVIS.
    Tracks user interaction patterns, success rates, and system performance.
    """
    def __init__(self, log_path="logs/telemetry.jsonl"):
        """Initializes the telemetry logger with a target log file."""
        self.log_path = log_path
        _dir = os.path.dirname(self.log_path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        self.session_data = {}

    def start_interaction(self, session_id: str, user_input: str):
        """Records the start of a user interaction."""
        self.session_data[session_id] = {
            "start_time": time.time(),
            "user_input": user_input,
            "latency": 0,
            "success": None,
            "confusion_detected": False,
            "steps": []
        }

    def record_step(self, session_id: str, step_name: str,
                    metadata: Optional[Dict] = None):
        """Logs a specific step in the reasoning process."""
        if session_id in self.session_data:
            self.session_data[session_id]["steps"].append({
                "name": step_name,
                "timestamp": time.time(),
                "metadata": metadata or {}
            })

    def end_interaction(self, session_id: str, success: bool = True,
                        confusion: bool = False):
        """Finalizes an interaction record and writes to local storage."""
        if session_id not in self.session_data:
            return

        data = self.session_data.pop(session_id)
        data["end_time"] = time.time()
        data["latency"] = data["end_time"] - data["start_time"]
        data["success"] = success
        data["confusion_detected"] = confusion
        data["timestamp"] = datetime.now().isoformat()

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
            logger.debug("Telemetry recorded: session %s (Latency: %.2fs)",
                         session_id, data["latency"])
        except (IOError, OSError, ValueError) as e:
            logger.error("Failed to write telemetry: %s", e)

    def get_production_metrics(self) -> Dict[str, Any]:
        """Calculates high-level metrics from recent telemetry."""
        if not os.path.exists(self.log_path):
            return {"status": "NO_DATA"}

        interactions = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    interactions.append(json.loads(line))
        except (IOError, json.JSONDecodeError):
            pass

        if not interactions:
            return {"status": "EMPTY"}

        recent = interactions[-100:]  # Last 100 interactions
        success_rate = sum(1 for i in recent if i["success"]) / len(recent)
        avg_latency = sum(i["latency"] for i in recent) / len(recent)
        confusion_rate = sum(1 for i in recent if i["confusion_detected"]) / len(recent)

        return {
            "total_samples": len(recent),
            "success_rate": success_rate,
            "avg_latency": avg_latency,
            "confusion_rate": confusion_rate,
            "status": "HEALTHY" if success_rate > 0.9 else "DEGRADED"
        }


# Global Singleton
telemetry = JarvisTelemetry()
