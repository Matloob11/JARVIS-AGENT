import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from services.utils.jarvis_logger import setup_logger


# Typed models for zero-trust compliance
class TelemetryStep(TypedDict):
    name: str
    timestamp: float
    metadata: dict[str, Any]

class TelemetryInteraction(TypedDict):
    start_time: float
    end_time: float
    user_input: str
    latency: float
    success: bool | None
    confusion_detected: bool
    steps: list[TelemetryStep]
    timestamp: str

logger = setup_logger("JARVIS-TELEMETRY")


class JarvisTelemetry:
    """
    Production telemetry system for JARVIS.
    Tracks user interaction patterns, success rates, and system performance.
    """
    def __init__(self, log_path: str | Path = "logs/telemetry.jsonl") -> None:
        """Initializes the telemetry logger with a target log file."""
        self.log_path: Path = Path(log_path)
        self._lock: asyncio.Lock = asyncio.Lock()

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_data: dict[str, dict[str, Any]] = {}
        self.MAX_SESSION_AGE: float = 3600.0  # 1 hour

    async def start_interaction(self, session_id: str, user_input: str) -> None:
        """Records the start of a user interaction."""
        async with self._lock:
            self.session_data[session_id] = {
                "start_time": time.time(),
                "user_input": user_input,
                "latency": 0.0,
                "success": None,
                "confusion_detected": False,
                "steps": [],
            }
            # Proactive cleanup (10% chance per start)
            if time.time() % 10 < 1:
                await self.cleanup_stale_sessions()

    async def cleanup_stale_sessions(self) -> None:
        """Prunes orphaned sessions from memory atomically."""
        async with self._lock:
            now = time.time()
            to_delete = [
                sid for sid, data in self.session_data.items()
                if now - data.get("start_time", 0) > self.MAX_SESSION_AGE
            ]

            for sid in to_delete:
                logger.warning("🧹 Cleaning up stale telemetry session: %s", sid)
                self.session_data.pop(sid, None)

    async def record_step(self, session_id: str, step_name: str,
                        metadata: dict[str, Any] | None = None) -> None:
        """Logs a specific step in the reasoning process."""
        async with self._lock:
            if session_id in self.session_data:
                steps = self.session_data[session_id]["steps"]
                steps.append({
                    "name": step_name,
                    "timestamp": time.time(),
                    "metadata": metadata or {},
                })

    async def end_interaction(self, session_id: str, success: bool = True,
                            confusion: bool = False) -> None:
        """Finalizes an interaction record and writes to local storage asynchronously."""
        async with self._lock:
            if session_id not in self.session_data:
                return
            data = self.session_data.pop(session_id)

        data["end_time"] = time.time()
        data["latency"] = float(data["end_time"]) - float(data["start_time"])
        data["success"] = success
        data["confusion_detected"] = confusion
        data["timestamp"] = datetime.now().isoformat()

        def _persist() -> None:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data) + "\n")
            except OSError as e:
                logger.error("Failed to write telemetry: %s", e)

        await asyncio.to_thread(_persist)
        logger.debug("Telemetry recorded: session %s (Latency: %.2fs)",
                     session_id, data["latency"])

    async def get_production_metrics(self) -> dict[str, Any]:
        """Calculates high-level metrics from recent telemetry using tail-read."""
        if not self.log_path.exists():
            return {"status": "NO_DATA"}

        def _read_last() -> list[dict[str, Any]]:
            interactions: list[dict[str, Any]] = []
            try:
                with self.log_path.open("rb") as f:
                    # Seek to end and read last 128KB to prevent OOM
                    f.seek(0, 2) # os.SEEK_END
                    size = f.tell()
                    f.seek(max(0, size - 128000))
                    chunk = f.read().decode("utf-8", errors="ignore")
                    for line in chunk.splitlines():
                        if line.strip():
                            try:
                                interactions.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                pass
            return interactions

        interactions = await asyncio.to_thread(_read_last)

        recent: list[dict[str, Any]] = interactions[-100:]  # Last 100 interactions
        size_recent = len(recent)
        if size_recent == 0:
            return {
                "total_samples": 0,
                "success_rate": 0.0,
                "avg_latency": 0.0,
                "confusion_rate": 0.0,
                "status": "HEALTHY",
            }

        success_count: int = sum(1 for i in recent if i.get("success") is True)
        success_rate: float = success_count / size_recent

        total_latency: float = sum(float(i.get("latency", 0.0)) for i in recent)
        avg_latency: float = total_latency / size_recent

        confusion_count: int = sum(1 for i in recent if i.get("confusion_detected") is True)
        confusion_rate: float = confusion_count / size_recent

        return {
            "total_samples": size_recent,
            "success_rate": success_rate,
            "avg_latency": avg_latency,
            "confusion_rate": confusion_rate,
            "status": "HEALTHY" if success_rate > 0.9 else "DEGRADED",
        }


# Global Singleton
telemetry: JarvisTelemetry = JarvisTelemetry()
