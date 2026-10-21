"""
# autonomous_planner.py
Autonomous task planning and the dashboard's plan progress line (C-86) for JARVIS.

tool_report_plan_progress sends notify 'plan_progress' {current, total, action, status, timestamp} through
services.utils.jarvis_bridge.notify_answered: signed with the agent link v2 keys (else the client's fallback: the
legacy headers, none on the internal listener), sent once, never from a probe or canary job (K-57). The bridge emits
the same flat payload to room 'ui' as 'plan_progress' (K-02/K-40) and answers {status: 'ok', emitted: 'plan_progress'}.
services.utils.plan_progress normalizes the values, and the bridge uses the same rules. The tool tells the model only
what really happened: a line counts as shown only with that answer (C-86 review: a bridge without the route answers an
unknown type {status: 'ok'} too, and a refused line {status: 'ignored'}), and anything else is reported as not shown
(K-12 'degraded'), never as a success. Logs carry the step numbers and the status, never the action text (K-29).
"""

from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils import jarvis_bridge
from services.utils.jarvis_logger import setup_logger
from services.utils.plan_progress import (
    PLAN_PROGRESS_EVENT,
    PlanProgressError,
    plan_progress_payload,
    plan_progress_shown,
)

logger = setup_logger("AUTONOMOUS-PLANNER")

PLAN_TOOL = "tool_report_plan_progress"
_OUTCOMES = frozenset({jarvis_bridge.NOTIFY_DELIVERED, jarvis_bridge.NOTIFY_QUEUED, jarvis_bridge.NOTIFY_SUPPRESSED,
                       jarvis_bridge.NOTIFY_FAILED})


class AutonomousPlanner:
    """
    Manages multi-step autonomous plans and syncs progress with the UI.
    """

    def __init__(self):
        self.active_plans: dict[str, dict] = {}

    def create_dynamic_plan(self, user_input: str, _intent_info: dict) -> list[dict]:
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
                    "desc": "Writing and delivering the final output."},
            ]
        elif "code" in text or "develop" in text:
            plan = [
                {"step": 1, "action": "Architect", "desc": "Designing system logic."},
                {"step": 2, "action": "Implementation",
                    "desc": "Writing code modules."},
                {"step": 3, "action": "Verification",
                    "desc": "Testing and linting code."},
            ]
        elif "audit" in text or "check" in text:
            plan = [
                {"step": 1, "action": "Scan", "desc": "Identifying system anomalies."},
                {"step": 2, "action": "Heal", "desc": "Triggering autonomous recovery."},
                {"step": 3, "action": "Verify", "desc": "Confirming system stability."},
            ]

        return plan

    async def notify_ui_progress(self, step_index: Any, total_steps: Any, action: Any,
                                 status: Any = "IN_PROGRESS") -> tuple[str, dict[str, Any]]:
        """
        Sends one plan progress line to the dashboard: normalized by services.utils.plan_progress (raises
        PlanProgressError for a value it cannot show), then notify 'plan_progress' through
        jarvis_bridge.notify_answered. Returns (outcome, payload): outcome is jarvis_bridge's NOTIFY_DELIVERED only
        when the bridge answered that it emitted the line (plan_progress_shown), NOTIFY_SUPPRESSED in a probe or
        canary job, and NOTIFY_FAILED for everything else: not sent, refused, an answer that does not confirm the emit
        (an older bridge's {status: 'ok'} for a type it does not know, a refused line, a page that is not the
        bridge's), or an answer this code cannot read. One log line per call, with the numbers, the status and the
        reason only.
        """
        payload = plan_progress_payload(step_index, total_steps, action, status)
        try:
            sent = await jarvis_bridge.notify_answered(PLAN_PROGRESS_EVENT, payload)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # a stand-in may raise; the real one never
            logger.warning("PLAN_PROGRESS notify raised %s", type(exc).__name__)
            sent = (jarvis_bridge.NOTIFY_FAILED, None)
        outcome, answer = sent if isinstance(sent, tuple) and len(sent) == 2 else (jarvis_bridge.NOTIFY_FAILED, None)
        if not isinstance(outcome, str) or outcome not in _OUTCOMES:  # only a clear 'delivered' counts as arrived
            outcome = jarvis_bridge.NOTIFY_FAILED
        reason = outcome
        if outcome == jarvis_bridge.NOTIFY_DELIVERED and not plan_progress_shown(answer):
            # 2xx, but no 'emitted': an older bridge dropped it as an unknown type, or it refused the line
            outcome, reason = jarvis_bridge.NOTIFY_FAILED, "unconfirmed answer"
        line = (payload["current"], payload["total"], payload["status"])
        if outcome == jarvis_bridge.NOTIFY_DELIVERED:
            logger.info("PLAN_PROGRESS %d/%d %s delivered (action %d chars)", *line, len(payload["action"]))
        elif outcome == jarvis_bridge.NOTIFY_SUPPRESSED:
            logger.info("PLAN_PROGRESS %d/%d %s not sent: probe or canary job (K-57)", *line)
        else:
            logger.warning("PLAN_PROGRESS %d/%d %s not delivered: the bridge did not confirm it (%s)", *line,
                           reason)
        return outcome, payload

    async def report_progress(self, step_index: Any, total_steps: Any, action: Any,
                              status: Any = "IN_PROGRESS") -> dict[str, Any]:
        """tool_report_plan_progress's K-12 result. 'success' only when the bridge answered that it emitted the line
        (it goes to every open dashboard); 'degraded' with UI_NOTIFY_FAILED or UI_SUPPRESSED when it did not go out
        or the bridge did not confirm it (the plan itself goes on); 'error' with INVALID_ARG for a value that cannot
        be shown. data holds the numbers of the line and whether the bridge showed it (delivered)."""
        try:
            outcome, line = await self.notify_ui_progress(step_index, total_steps, action, status)
        except PlanProgressError as exc:
            logger.info("PLAN_PROGRESS refused: bad %s", exc.field)
            return {"status": "error", "tool": PLAN_TOOL, "error_code": "INVALID_ARG",
                    "message": f"Plan progress nahi bheja: {exc}. Sahi value ke saath dobara chalayein."}
        current, total, plan_status = line["current"], line["total"], line["status"]
        data = {"current": current, "total": total, "plan_status": plan_status,
                "delivered": outcome == jarvis_bridge.NOTIFY_DELIVERED}
        if outcome == jarvis_bridge.NOTIFY_DELIVERED:
            return {"status": "success", "tool": PLAN_TOOL, "data": data,
                    "message": f"Plan progress dashboard ko bhej diya: step {current}/{total} ({plan_status})."}
        if outcome == jarvis_bridge.NOTIFY_SUPPRESSED:
            return {"status": "degraded", "tool": PLAN_TOOL, "error_code": "UI_SUPPRESSED", "data": data,
                    "message": "Yeh test (probe) job hai, is mein dashboard nahi: progress nahi dikhaya gaya. "
                               "Kaam jari rakhein."}
        return {"status": "degraded", "tool": PLAN_TOOL, "error_code": "UI_NOTIFY_FAILED", "data": data,
                "message": "Plan progress dashboard tak nahi pohancha (bridge ne qubool nahi kiya). Kaam jari "
                           "rakhein aur Sir ko progress zabani batayein."}


# Global Singleton
autonomous_planner = AutonomousPlanner()


@jarvis_tool
async def tool_report_plan_progress(step_index: int, total_steps: int,
                                    action: str, status: str = "IN_PROGRESS") -> dict:
    """
    Reporting tool used by the agent to manually update the UI on autonomous plan progress.
    Args:
        step_index: Current step number (1-based).
        total_steps: Total number of steps in the plan.
        action: Name of the current action being performed.
        status: Current status (IN_PROGRESS, COMPLETED, FAILED).
    """
    return await autonomous_planner.report_progress(step_index, total_steps, action, status)
