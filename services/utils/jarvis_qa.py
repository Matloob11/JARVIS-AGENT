"""
# services/utils/jarvis_qa.py
Autonomous QA Engine for the JARVIS project.
"""

import asyncio
import time
from collections.abc import Callable

from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_qa_reporter import qa_reporter
from services.utils.qa_modules.security_tester import SecurityQA
from services.utils.qa_modules.stress_tester import StressQA

logger = setup_logger("JARVIS-QA")


class JarvisQAEngine:
    """
    Autonomous QA Orchestrator for JARVIS.
    Triggers continuous testing cycles and evaluates system quality.
    """
    def __init__(self):
        self.modules = {
            "stress": StressQA(),
            "security": SecurityQA(),
        }
        self.last_run = 0
        self.is_running = False
        self.quality_history = []

    async def run_full_audit(self):
        """Executes all registered QA modules."""
        if self.is_running:
            return None

        self.is_running = True
        logger.info("🧪 Starting Autonomous Quality Audit...")

        results = {}
        for name, module in self.modules.items():
            try:
                logger.info("Running QA Module: %s", name)
                results[name] = await module.run()
            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.warning("QA Audit cancelled for module: %s", name)
                raise
            except (ValueError, OSError, RuntimeError) as e:
                logger.error("QA Module %s failed: %s", name, e)
                results[name] = {"status": "ERROR", "error": str(e)}

        self.last_run = time.time()
        self.is_running = False

        # Calculate Quality Score
        score = self._calculate_quality_score(results)
        self.quality_history.append({
            "timestamp": self.last_run,
            "score": score,
            "results": results,
        })

        # Generate Report
        qa_reporter.generate_daily_report(self.quality_history)

        logger.info("✅ Quality Audit Complete. System Score: %d/100", score)
        return results

    def _calculate_quality_score(self, results: dict) -> int:
        """Heuristic-based quality scoring."""
        score = 100
        # Deduct for failures
        for res in results.values():
            if res.get("status") != "PASS":
                score -= 20

        # Deduct for high resource usage (from health_monitor)
        vitals = health_monitor.generate_health_report()["vitals"]
        if vitals.get("cpu", {}).get("usage", 0) > 80:
            score -= 10

        return max(0, score)

    async def start_qa_loop(self, get_state_func: Callable | None = None, interval: int = 43200):
        """Background loop for continuous validation."""
        logger.info("🔱 QA Engine active. Cycle: Every %ds", interval)
        while True:
            try:
                await self.run_full_audit()
                if get_state_func:
                    await get_state_func()
            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.warning("QA Loop terminating...")
                raise
            except (ValueError, OSError, RuntimeError) as e:
                logger.error("Error in QA loop: %s", e)

            await asyncio.sleep(interval)


# Global Singleton
qa_engine = JarvisQAEngine()
