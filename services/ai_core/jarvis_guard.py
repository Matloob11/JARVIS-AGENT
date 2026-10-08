"""
# services/ai_core/jarvis_guard.py
K-45 confirmation policy for the agent's LLM tool wrapper (plan task C-29, contract K-45 C-CONFIRM-1).

The bridge already raises every confirm_request to its K-45 floor (src/core/confirm_policy.clamp_risk), but a tool
whose @jarvis_tool decorator sets no risk counted as 'low' in the agent and never asked at all: shutdown, restart,
sleep, the recycle bin, run_cmd_command, automate_whatsapp, the file server and the rest of the K-45 HIGH list ran on
the PC brain with no confirmation. jarvis_plugin_manager's wrapper now passes every call's risk through
effective_risk() before it decides whether to ask:

- K-45 HIGH tools (src/core/confirm_policy.K45_HIGH, the contract list) ask for the dashboard Allow (ConfirmFlow,
  K-14). The wrapper never lowers a risk, so a tool that already declares 'high' or a stricter risk callable keeps it.
- JARVIS_CONFIRM_EXTRA (comma list) adds tools to HIGH; JARVIS_CONFIRM_SKIP (comma list) removes tools from it, except
  shutdown_system, restart_system and run_cmd_command, which can never be skipped (one WARNING names an attempt).
- K-45 MEDIUM tools (type_text_tool, press_hotkey_tool, ...) keep running without a question, "with a UI toast and an
  audit row": audit_risk() raises only their AUDIT level, so the wrapper audits them as medium. Their own decorator
  risk (K-11, e.g. a per-call risk callable) is untouched.

Direct Python calls for explicit UI actions (e.g. the SIM panel's sim_lookup route) do not pass through here, so they
are not asked twice (K-45). The lists live in src/core/confirm_policy (no project imports), shared with the bridge.
"""
from __future__ import annotations

import logging
import os
import threading

from src.core.confirm_policy import K45_HIGH, K45_MEDIUM, RISK_RANK

logger = logging.getLogger("JARVIS-GUARD")

EXTRA_ENV = "JARVIS_CONFIRM_EXTRA"
SKIP_ENV = "JARVIS_CONFIRM_SKIP"
# K-45: these may never be removed from HIGH by JARVIS_CONFIRM_SKIP.
NEVER_SKIP: frozenset[str] = frozenset({"shutdown_system", "restart_system", "run_cmd_command"})

_warned: set[str] = set()
_warned_lock = threading.Lock()


def _env_names(name: str) -> frozenset[str]:
    """The tool names of one comma-list env variable (read on every call: cheap, and a restart is not needed)."""
    return frozenset(part.strip() for part in (os.getenv(name) or "").split(",") if part.strip())


def high_tools() -> frozenset[str]:
    """The tools that need the dashboard Allow now: K-45 HIGH plus JARVIS_CONFIRM_EXTRA minus JARVIS_CONFIRM_SKIP
    (never the NEVER_SKIP three)."""
    skip = _env_names(SKIP_ENV)
    refused = sorted(skip & NEVER_SKIP)
    if refused:
        key = ",".join(refused)
        with _warned_lock:
            first = key not in _warned
            _warned.add(key)
        if first:
            logger.warning("%s may never contain %s (K-45); they stay HIGH", SKIP_ENV, key)
    return (K45_HIGH | _env_names(EXTRA_ENV)) - (skip - NEVER_SKIP)


def effective_risk(tool: str, risk: str) -> str:
    """The risk a call is asked at: 'high' for a HIGH tool whose own risk is lower, else the call's risk unchanged
    (never lowered; an unknown label is left as it is, the wrapper treats it as high anyway)."""
    rank = RISK_RANK.get(risk)
    if rank is not None and rank < RISK_RANK["high"] and tool in high_tools():
        return "high"
    return risk


def audit_risk(tool: str, risk: str) -> str:
    """The level an execution is audited at: a K-45 MEDIUM tool runs without a question but is audited as 'medium';
    everything else keeps the call's risk."""
    rank = RISK_RANK.get(risk)
    if rank is not None and rank < RISK_RANK["medium"] and tool in K45_MEDIUM:
        return "medium"
    return risk
