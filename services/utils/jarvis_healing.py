"""
# services/utils/jarvis_healing.py
Autonomous recovery engine for JARVIS.
Detects failures via health_monitor and triggers restorative actions.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-HEALING")


class JarvisHealingEngine:
    """
    Autonomous recovery engine for JARVIS.
    Detects failures via health_monitor and triggers restorative actions.
    """
    def __init__(self) -> None:
        """Initializes the healing registry and state."""
        self.recovery_registry: dict[str, Callable[[], Coroutine[Any, Any, Any]]] = {}
        self.retry_counts: dict[str, int] = {}
        self.is_healing: bool = False

    def register_recovery_action(self, service_name: str,
                                 action: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        """Registers a coroutine to be called when a service fails."""
        self.recovery_registry[service_name] = action
        self.retry_counts[service_name] = 0

    async def attempt_recovery(self, service_name: str) -> None:
        """Executes the recovery plan for a specific service."""
        if service_name not in self.recovery_registry:
            logger.warning("No recovery plan for service: %s", service_name)
            return

        if self.retry_counts.get(service_name, 0) > 5:
            logger.error("🚨 Critical Failure: %s failed 5 attempts.",
                         service_name)
            return

        self.retry_counts[service_name] = self.retry_counts.get(
            service_name, 0) + 1
        logger.info("🔧 Attempting recovery: %s (Attempt %d/5)",
                    service_name, self.retry_counts[service_name])

        try:
            await self.recovery_registry[service_name]()
            logger.info("✅ Recovery action triggered for %s", service_name)
        except (ValueError, RuntimeError, OSError) as e:
            logger.error("Failed to recover %s: %s", service_name, e)

    async def start_healing_loop(self) -> None:
        """Background loop that monitors health and triggers healing."""
        logger.info("🔱 Healing Engine initialized and standing by.")
        while True:
            try:
                status = health_monitor.get_service_status()
                for service, state in status.items():
                    if state == "OFFLINE":
                        logger.warning("🚑 Service %s is OFFLINE.", service)
                        await self.attempt_recovery(service)
                    elif state == "HEALTHY":
                        if self.retry_counts.get(service, 0) > 0:
                            logger.info("🎖️ Service %s restored.", service)
                            self.retry_counts[service] = 0

                self._check_and_fix_anomalies()
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Healing loop error: %s", e)
            await asyncio.sleep(10)

    def _check_and_fix_anomalies(self) -> None:
        """Identifies and resolves resource anomalies."""
        anomalies = health_monitor.check_anomalies()
        for anomaly in anomalies:
            logger.debug("System Anomaly Detected: %s", anomaly)
            if "High Memory" in anomaly:
                import gc  # pylint: disable=import-outside-toplevel
                gc.collect()
                logger.info("Triggered manual Garbage Collection.")


# Global Singleton
healing_engine = JarvisHealingEngine()
