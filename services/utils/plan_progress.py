"""
# services/utils/plan_progress.py
The plan progress line (C-86; K-02, K-40): tool_report_plan_progress sends notify 'plan_progress' and the bridge emits
the same flat payload to room 'ui' as 'plan_progress':

    {current: int, total: int, action: str, status: 'IN_PROGRESS'|'COMPLETED'|'FAILED', timestamp: epoch s}

The agent (services/ai_core/autonomous_planner.py) and the bridge (src/core/ui_bridge.py) normalize with these same
functions, so the two sides never disagree. Rules: total is a whole number 1..PLAN_STEPS_MAX; current is a whole number
kept within 1..total (a 0-based first step 0 becomes 1, a step past the end becomes total; a negative one is refused);
status takes the three words in any case plus synonyms (done/complete/success -> COMPLETED, fail/error -> FAILED,
running/started -> IN_PROGRESS); action is printable text, whitespace collapsed, at most PLAN_ACTION_MAX characters.
A refusal names the argument and its rule, never the value: the action is the owner's own text (K-29).
The bridge answers a line it emitted with PLAN_PROGRESS_ANSWER {status: 'ok', emitted: 'plan_progress'}, and the agent
counts a line as shown only with that answer (plan_progress_shown): a bridge without the route answers an unknown type
{status: 'ok'} too, and a refused line {status: 'ignored', reason}.
Stdlib only: the slim bridge image imports it too.
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

PLAN_PROGRESS_EVENT = "plan_progress"  # the notify type and the UI event (K-02: the UI also reads plan_progress_update)
PLAN_STATUSES = ("IN_PROGRESS", "COMPLETED", "FAILED")
PLAN_ACTION_MAX = 200  # characters of a step's action text
PLAN_STEPS_MAX = 100   # the longest plan one progress line shows
# The bridge's answer once it emitted the line to room 'ui' (C-86 review): it names the emit, so an older bridge's
# {status: 'ok'} for a type it does not know never passes for a shown line.
PLAN_PROGRESS_ANSWER: Mapping[str, str] = MappingProxyType({"status": "ok", "emitted": PLAN_PROGRESS_EVENT})

_STATUS_WORDS: dict[str, str] = {
    **dict.fromkeys(("in_progress", "inprogress", "progress", "running", "started", "start", "ongoing", "active",
                     "working", "pending"), "IN_PROGRESS"),
    **dict.fromkeys(("completed", "complete", "done", "success", "succeeded", "successful", "finished", "ok"),
                    "COMPLETED"),
    **dict.fromkeys(("failed", "fail", "failure", "error", "errored", "aborted", "cancelled", "canceled"), "FAILED"),
}
_SEPARATORS = re.compile(r"[\s-]+")
_MS_EPOCH_MIN = 1e11  # larger epoch values are milliseconds (the bridge store's rule)

# The rule of each argument, in the words the model reads back (Roman Urdu, K-12).
_RULES = {
    "total": f"total_steps 1 se {PLAN_STEPS_MAX} tak ka poora number hona chahiye",
    "current": "step_index 1 se total_steps tak ka poora number hona chahiye",
    "status": "status IN_PROGRESS, COMPLETED ya FAILED hona chahiye",
    "payload": "plan progress ek object hona chahiye",
}


class PlanProgressError(ValueError):
    """A plan progress value that cannot be shown. field is 'current', 'total', 'status' or 'payload'; str() is that
    argument's rule, never the value."""

    def __init__(self, field: str) -> None:
        super().__init__(_RULES[field])
        self.field = field


def plan_status(value: Any) -> str:
    """'IN_PROGRESS', 'COMPLETED' or 'FAILED' from a status word in any case ('in progress' and 'in-progress' too) or
    one of its synonyms; None or an empty word is 'IN_PROGRESS'. Raises PlanProgressError('status') otherwise."""
    if value is None:
        return "IN_PROGRESS"
    if not isinstance(value, str):
        raise PlanProgressError("status")
    word = _SEPARATORS.sub("_", value.strip().lower())
    if not word:
        return "IN_PROGRESS"
    try:
        return _STATUS_WORDS[word]
    except KeyError:
        raise PlanProgressError("status") from None


def _whole_number(value: Any, field: str) -> int:
    """A whole number from an int, an integral float (2.0) or a numeric string ('2', ' 3 ', '2.0'). A bool, a fraction,
    NaN, an infinity or anything else raises PlanProgressError(field)."""
    if isinstance(value, bool):
        raise PlanProgressError(field)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        number = value
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise PlanProgressError(field) from None
    else:
        raise PlanProgressError(field)
    if not math.isfinite(number) or not number.is_integer():
        raise PlanProgressError(field)
    return int(number)


def plan_action(value: Any) -> str:
    """The action text as shown: control and format characters become spaces, whitespace runs collapse to one space,
    cut to PLAN_ACTION_MAX characters. None is ''."""
    text = "" if value is None else value if isinstance(value, str) else str(value)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(text.split())[:PLAN_ACTION_MAX]


def _epoch_seconds(value: Any) -> float | None:
    """Epoch seconds from a number or numeric string in seconds or milliseconds; None when unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        seconds = float(value.strip()) if isinstance(value, str) else float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds / 1000.0 if seconds > _MS_EPOCH_MIN else seconds


def plan_progress_payload(current: Any, total: Any, action: Any = "", status: Any = None, *,
                          timestamp: Any = None) -> dict[str, Any]:
    """The flat plan_progress payload {current, total, action, status, timestamp} by the module's rules; timestamp is
    epoch seconds (a number in s or ms), else now, rounded to the millisecond. Raises PlanProgressError."""
    steps = _whole_number(total, "total")
    if not 1 <= steps <= PLAN_STEPS_MAX:
        raise PlanProgressError("total")
    step = _whole_number(current, "current")
    if step < 0:
        raise PlanProgressError("current")
    seconds = _epoch_seconds(timestamp)
    return {
        "current": min(max(step, 1), steps),
        "total": steps,
        "action": plan_action(action),
        "status": plan_status(status),
        "timestamp": round(time.time() if seconds is None else seconds, 3),
    }


def plan_progress_shown(answer: Any) -> bool:
    """True only for the bridge's answer after it emitted the line (PLAN_PROGRESS_ANSWER); never for an older bridge's
    {status: 'ok'} (an unknown type, dropped as 'UNKNOWN EVENT'), a refusal {status: 'ignored', reason} or anything
    that is not the bridge's JSON object."""
    return (isinstance(answer, Mapping) and answer.get("status") == PLAN_PROGRESS_ANSWER["status"]
            and answer.get("emitted") == PLAN_PROGRESS_ANSWER["emitted"])


def plan_progress_from_notify(payload: Any) -> dict[str, Any]:
    """The bridge's side: a notify 'plan_progress' payload normalized by the same rules. current|step_index,
    total|total_steps and timestamp|ts are read (the first name wins). Raises PlanProgressError, also for a payload
    that is not an object."""
    if not isinstance(payload, Mapping):
        raise PlanProgressError("payload")

    def first(*names: str) -> Any:
        return next((payload[name] for name in names if payload.get(name) is not None), None)

    return plan_progress_payload(first("current", "step_index"), first("total", "total_steps"),
                                 payload.get("action"), payload.get("status"),
                                 timestamp=first("timestamp", "ts"))
