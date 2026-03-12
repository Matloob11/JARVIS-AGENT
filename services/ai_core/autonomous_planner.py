"""
# autonomous_planner.py
Autonomous Task Planning and progress tracking for JARVIS.
"""

import json
from datetime import datetime
from typing import List, Dict
import httpx

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_config import config
from services.utils.jarvis_security import security_manager
from services.ai_core.jarvis_plugin_manager import jarvis_tool

logger = setup_logger("AUTONOMOUS-PLANNER")


class AutonomousPlanner:
    """
    Manages multi-step autonomous plans and syncs progress with the UI.
    """

    def __init__(self):
        self.active_plans: Dict[str, Dict] = {}

    def create_dynamic_plan(self, user_input: str, _intent_info: Dict) -> List[Dict]:
        """
        Dynamically generates a sequence of steps based on input and intent.
        """
        plan = []
        text = user_input.lower()

        # Simple heuristic-based planning (to be augmented by LLM)
        if "research" in text or "report" in text:
            plan = [
                {"step": 1, "action": "Research",
                    "desc": "Information lookup and deep search."},
                {"step": 2, "action": "Synthesis",
                    "desc": "Analyzing and boiling down data."},
                {"step": 3, "action": "Reporting",
                    "desc": "Writing and delivering the final output."}
            ]
        elif "code" in text or "develop" in text:
            plan = [
                {"step": 1, "action": "Architect", "desc": "Designing system logic."},
                {"step": 2, "action": "Implementation",
                    "desc": "Writing code modules."},
                {"step": 3, "action": "Verification",
                    "desc": "Testing and linting code."}
            ]
        elif "audit" in text or "check" in text:
            plan = [
                {"step": 1, "action": "Scan", "desc": "Identifying system anomalies."},
                {"step": 2, "action": "Heal", "desc": "Triggering autonomous recovery."},
                {"step": 3, "action": "Verify", "desc": "Confirming system stability."}
            ]

        return plan

    async def notify_ui_progress(self, step_index: int, total_steps: int,
                                 action: str, status: str = "IN_PROGRESS"):
        """
        Sends planning progress to STONIX UI via bridge.
        """
        try:
            payload = {
                "type": "plan_progress",
                "payload": {
                    "current": step_index,
                    "total": total_steps,
                    "action": action,
                    "status": status,
                    "timestamp": datetime.now().isoformat()
                }
            }

            json_payload = json.dumps(payload, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = {
                "X-Vortex-Token": config.security_token,
                "X-Vortex-Signature": signature
            }

            async with httpx.AsyncClient() as client:
                await client.post(f"{config.bridge_url}/notify",
                                  json=payload, headers=headers, timeout=1.0)

            logger.info("Plan Progress Updated: Step %d/%d - %s",
                        step_index, total_steps, action)
        except (httpx.HTTPError, ValueError, RuntimeError, TypeError) as e:
            logger.error("Failed to notify UI of plan progress: %s", e)


# Global Singleton
autonomous_planner = AutonomousPlanner()


@jarvis_tool
async def tool_report_plan_progress(step_index: int, total_steps: int,
                                    action: str, status: str = "IN_PROGRESS") -> Dict:
    """
    Reporting tool used by the agent to manually update the UI on autonomous plan progress.
    Args:
        step_index: Current step number (1-based).
        total_steps: Total number of steps in the plan.
        action: Name of the current action being performed.
        status: Current status (IN_PROGRESS, COMPLETED, FAILED).
    """
    await autonomous_planner.notify_ui_progress(step_index, total_steps, action, status)
    return {
        "status": "success",
        "message": f"Plan progress reported: {step_index}/{total_steps} - {action} ({status})."
    }
