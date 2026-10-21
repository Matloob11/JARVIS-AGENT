"""C-86: the plan progress line on the agent side.

services/utils/plan_progress.py normalizes {current, total, action, status, timestamp} for the agent and the bridge
alike; services/ai_core/autonomous_planner.py sends it through jarvis_bridge.notify_answered and tells the model only
what really happened: a success only when the bridge answered that it emitted the line ({status: 'ok', emitted:
'plan_progress'}), never for an older bridge's {status: 'ok'} (the line dropped as an unknown type) or a refusal (C-86
review). Logs carry the numbers and the status, never the action text (K-29). notify_answered is a stand-in here, or
the real one inside a suppressed (probe) context: nothing reaches a network, the live bridge or a real env file.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import inspect
import logging
import time
from typing import Any

import pytest

from services.ai_core import autonomous_planner as planner
from services.ai_core import tool_placement as tp
from services.utils import jarvis_bridge, probe_context
from services.utils.plan_progress import (
    PLAN_ACTION_MAX,
    PLAN_PROGRESS_ANSWER,
    PLAN_STEPS_MAX,
    PlanProgressError,
    plan_action,
    plan_progress_from_notify,
    plan_progress_payload,
    plan_progress_shown,
    plan_status,
)

SECRET_ACTION = "Ali ko 0300-PRIVATE wala email bhejna"  # stands in for the owner's own words


# ---------------------------------------------------------------- the normalizer


def test_a_valid_line_is_the_flat_payload():
    assert plan_progress_payload(2, 3, "Heal", "IN_PROGRESS", timestamp=1791460000.5) == {
        "current": 2, "total": 3, "action": "Heal", "status": "IN_PROGRESS", "timestamp": 1791460000.5}


@pytest.mark.parametrize("word, status", [
    ("IN_PROGRESS", "IN_PROGRESS"), ("in_progress", "IN_PROGRESS"), ("In Progress", "IN_PROGRESS"),
    ("in-progress", "IN_PROGRESS"), ("running", "IN_PROGRESS"), ("started", "IN_PROGRESS"), (None, "IN_PROGRESS"),
    ("", "IN_PROGRESS"), ("  ", "IN_PROGRESS"),
    ("COMPLETED", "COMPLETED"), ("completed", "COMPLETED"), ("Complete", "COMPLETED"), ("done", "COMPLETED"),
    ("SUCCESS", "COMPLETED"), ("finished", "COMPLETED"),
    ("FAILED", "FAILED"), ("fail", "FAILED"), ("Error", "FAILED"), ("failure", "FAILED"), ("cancelled", "FAILED"),
])
def test_status_words_any_case_and_synonyms(word: Any, status: str):
    assert plan_status(word) == status


@pytest.mark.parametrize("word", ["maybe", "half done", "IN_PROGRESS!", 7, True, ["done"]])
def test_an_unknown_status_is_refused_without_its_value(word: Any):
    with pytest.raises(PlanProgressError) as refused:
        plan_status(word)
    assert refused.value.field == "status" and str(word) not in str(refused.value)


@pytest.mark.parametrize("current, total, shown", [
    (1, 3, (1, 3)), (3, 3, (3, 3)), (0, 3, (1, 3)), (5, 3, (3, 3)), (10 ** 400, 3, (3, 3)), ("2", "3", (2, 3)),
    (" 2 ", 3.0, (2, 3)), (2.0, "3.0", (2, 3)), (1, PLAN_STEPS_MAX, (1, PLAN_STEPS_MAX)),
])
def test_steps_are_whole_numbers_kept_within_one_to_total(current: Any, total: Any, shown: tuple[int, int]):
    line = plan_progress_payload(current, total, "x")
    assert (line["current"], line["total"]) == shown


@pytest.mark.parametrize("current, total, field", [
    (1, 0, "total"), (1, -2, "total"), (1, PLAN_STEPS_MAX + 1, "total"), (1, 2.5, "total"), (1, True, "total"),
    (1, "three", "total"), (1, None, "total"), (1, float("nan"), "total"), (1, float("inf"), "total"),
    (-1, 3, "current"), (1.5, 3, "current"), (True, 3, "current"), ("first", 3, "current"), (None, 3, "current"),
    ({"n": 1}, 3, "current"),
])
def test_unusable_steps_are_refused_naming_the_argument(current: Any, total: Any, field: str):
    with pytest.raises(PlanProgressError) as refused:
        plan_progress_payload(current, total, SECRET_ACTION)
    assert refused.value.field == field and SECRET_ACTION not in str(refused.value)


def test_the_action_is_printable_collapsed_and_cut():
    assert plan_action("  Scan\n\tthe\x00 disk\u202e now\u200b ") == "Scan the disk now"
    assert plan_action("x" * 500) == "x" * PLAN_ACTION_MAX
    assert plan_action(None) == "" and plan_action(42) == "42"
    assert plan_progress_payload(1, 3, None)["action"] == ""


def test_the_timestamp_is_epoch_seconds_else_now():
    assert plan_progress_payload(1, 3, timestamp=1791460000123)["timestamp"] == 1791460000.123  # ms
    assert plan_progress_payload(1, 3, timestamp="1791460000.25")["timestamp"] == 1791460000.25
    for unusable in (None, "soon", True, float("nan"), -5, "2026-10-20T14:00:00Z"):
        assert abs(plan_progress_payload(1, 3, timestamp=unusable)["timestamp"] - time.time()) < 5


def test_the_bridge_reads_the_same_payload_with_its_aliases():
    line = plan_progress_from_notify({"current": 3, "total": 3, "action": "Verify", "status": "done",
                                      "timestamp": 1791460000.0, "extra": "dropped"})
    assert line == {"current": 3, "total": 3, "action": "Verify", "status": "COMPLETED", "timestamp": 1791460000.0}
    alias = plan_progress_from_notify({"step_index": 2, "total_steps": 3, "ts": 1791460000})
    assert (alias["current"], alias["total"], alias["status"], alias["timestamp"]) == (2, 3, "IN_PROGRESS",
                                                                                      1791460000.0)
    for not_an_object in (None, "1/3", [1, 3], 7):
        with pytest.raises(PlanProgressError) as refused:
            plan_progress_from_notify(not_an_object)
        assert refused.value.field == "payload"
    with pytest.raises(PlanProgressError) as refused:
        plan_progress_from_notify({"current": 1, "action": SECRET_ACTION})
    assert refused.value.field == "total"


# ---------------------------------------------------------------- the planner and its tool


@pytest.fixture
def planner_log():
    """The AUTONOMOUS-PLANNER logger's records, DEBUG included (setup_logger loggers do not propagate)."""
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler, logger = _Keep(level=logging.DEBUG), planner.logger
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(old_level)


@pytest.fixture
def bridge(monkeypatch):
    """jarvis_bridge.notify_answered replaced by a recorder whose result the test sets (bridge.result); by default the
    bridge's answer after it emitted the line. notify_event must not be used: its 2xx proves no route."""
    state: dict[str, Any] = {"result": (jarvis_bridge.NOTIFY_DELIVERED, dict(PLAN_PROGRESS_ANSWER)), "sent": []}

    async def fake_notify(event_type: str, payload: Any) -> Any:
        state["sent"].append((event_type, payload))
        result = state["result"]
        if isinstance(result, BaseException):
            raise result
        return result

    async def not_this_one(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("the plan progress line needs the bridge's answer (notify_answered), not notify_event")

    monkeypatch.setattr(jarvis_bridge, "notify_answered", fake_notify)
    monkeypatch.setattr(jarvis_bridge, "notify_event", not_this_one)
    return state


def _texts(records: list[logging.LogRecord]) -> str:
    return "\n".join(record.getMessage() for record in records)


async def test_a_line_the_bridge_took_is_a_success(bridge, planner_log):
    result = await planner.tool_report_plan_progress(2, 3, SECRET_ACTION)
    [(event_type, payload)] = bridge["sent"]
    assert event_type == "plan_progress"
    assert set(payload) == {"current", "total", "action", "status", "timestamp"}
    assert (payload["current"], payload["total"], payload["action"], payload["status"]) == (
        2, 3, SECRET_ACTION, "IN_PROGRESS")
    assert result["status"] == "success" and result["tool"] == "tool_report_plan_progress"
    assert result["data"] == {"current": 2, "total": 3, "plan_status": "IN_PROGRESS", "delivered": True}
    assert "2/3" in result["message"]
    assert "PLAN_PROGRESS 2/3 IN_PROGRESS delivered" in _texts(planner_log)
    assert SECRET_ACTION not in _texts(planner_log)


@pytest.mark.parametrize("result", [
    (jarvis_bridge.NOTIFY_FAILED, None), (jarvis_bridge.NOTIFY_QUEUED, None), (None, None), ("weird", None),
    jarvis_bridge.NOTIFY_DELIVERED,  # notify_event's bare outcome: no answer to read
    None, {"status": "ok"}, (jarvis_bridge.NOTIFY_DELIVERED,), RuntimeError("stand-in broke"),
])
async def test_a_line_that_did_not_go_out_is_never_a_success(bridge, planner_log, result: Any):
    bridge["result"] = result
    reply = await planner.tool_report_plan_progress(1, 3, SECRET_ACTION, "running")
    assert reply["status"] == "degraded" and reply["error_code"] == "UI_NOTIFY_FAILED"
    assert reply["data"] == {"current": 1, "total": 3, "plan_status": "IN_PROGRESS", "delivered": False}
    assert "nahi pohancha" in reply["message"] and "jari" in reply["message"]  # the plan itself goes on
    warnings = [r for r in planner_log if r.levelno == logging.WARNING]
    assert warnings and "1/3 IN_PROGRESS not delivered" in _texts(warnings)
    assert SECRET_ACTION not in _texts(planner_log)


@pytest.mark.parametrize("answer", [
    {"status": "ok"},  # a bridge without the route: 'UNKNOWN EVENT', answered ok all the same (K-39)
    {"status": "ignored", "reason": "bad_payload"},  # the bridge refused the line
    {"status": "ok", "emitted": "something_else"}, {"emitted": "plan_progress"}, ["ok"], "ok", None, 200,
])
async def test_a_2xx_that_does_not_confirm_the_emit_is_never_a_success(bridge, planner_log, answer: Any):
    """The review's scenario: the PC worker restarted with this code before the VPS bridge was redeployed. Every line
    is dropped there as an UNKNOWN EVENT with 200 {status: 'ok'}, and the tool used to say 'dashboard ko bhej diya'."""
    bridge["result"] = (jarvis_bridge.NOTIFY_DELIVERED, answer)
    reply = await planner.tool_report_plan_progress(2, 3, SECRET_ACTION)
    assert reply["status"] == "degraded" and reply["error_code"] == "UI_NOTIFY_FAILED"
    assert reply["data"]["delivered"] is False and "bhej diya" not in reply["message"]
    assert "2/3 IN_PROGRESS not delivered: the bridge did not confirm it (unconfirmed answer)" in _texts(planner_log)
    assert SECRET_ACTION not in _texts(planner_log)


def test_only_the_bridges_emit_answer_counts_as_shown():
    assert plan_progress_shown(dict(PLAN_PROGRESS_ANSWER)) and plan_progress_shown(
        {"status": "ok", "emitted": "plan_progress", "extra": 1})
    for answer in ({"status": "ok"}, {"status": "ignored", "reason": "bad_payload"}, {"emitted": "plan_progress"},
                   {"status": "OK", "emitted": "plan_progress"}, None, "ok", ["ok"]):
        assert not plan_progress_shown(answer)
    with pytest.raises(TypeError):
        PLAN_PROGRESS_ANSWER["status"] = "changed"  # type: ignore[index]  # read-only: the bridge answers a copy


async def test_a_probe_job_sends_nothing_and_says_so(monkeypatch, planner_log):
    """K-57: the real notify_answered inside a probe or canary context; no HTTP at all."""
    monkeypatch.setattr(jarvis_bridge, "get_http_client", lambda: pytest.fail("no HTTP from a probe"))
    token = probe_context.bridge_suppressed.set(True)
    try:
        result = await planner.tool_report_plan_progress(1, 3, SECRET_ACTION)
    finally:
        probe_context.bridge_suppressed.reset(token)
    assert result["status"] == "degraded" and result["error_code"] == "UI_SUPPRESSED"
    assert result["data"]["delivered"] is False
    assert "probe or canary job" in _texts(planner_log) and SECRET_ACTION not in _texts(planner_log)


@pytest.mark.parametrize("args, field", [
    ((1, 3, SECRET_ACTION, "maybe"), "status"), ((1, 0, SECRET_ACTION), "total"),
    ((-1, 3, SECRET_ACTION), "current"), ((1, 500, SECRET_ACTION), "total"),
])
async def test_values_that_cannot_be_shown_are_refused_before_any_notify(bridge, planner_log, args: tuple,
                                                                         field: str):
    result = await planner.tool_report_plan_progress(*args)
    assert bridge["sent"] == []
    assert result["status"] == "error" and result["error_code"] == "INVALID_ARG"
    assert result["message"].startswith("Plan progress nahi bheja")
    assert f"refused: bad {field}" in _texts(planner_log) and SECRET_ACTION not in _texts(planner_log)
    assert SECRET_ACTION not in result["message"]


async def test_a_completed_line_and_lenient_numbers(bridge):
    result = await planner.tool_report_plan_progress("3", 3.0, "Verify", "done")
    assert result["status"] == "success" and result["data"]["plan_status"] == "COMPLETED"
    assert bridge["sent"][0][1]["current"] == 3 and bridge["sent"][0][1]["status"] == "COMPLETED"
    await planner.tool_report_plan_progress(0, 3, "Scan")  # a 0-based first step is shown as step 1
    assert bridge["sent"][-1][1]["current"] == 1


def test_the_tool_keeps_the_signature_and_doc_the_desktop_manifest_records():
    """The manifest (K-71) pins the tool's parameters and docstring; C-86 changed only its body."""
    entry = tp.manifest_tools(tp.load_manifest())["tool_report_plan_progress"]
    tool = planner.tool_report_plan_progress
    assert tool.__doc__ == entry["doc"]
    assert [(p.name, p.default if p.default is not inspect.Parameter.empty else None)
            for p in inspect.signature(tool).parameters.values()] == [
        (p["name"], p["default"]) for p in entry["params"]]
    assert tp.live_param_schema_sha256(tool) == entry["param_schema_sha256"]
    assert entry["placement"] == "vps"


def test_the_planner_has_no_private_http_path_left():
    source = inspect.getsource(planner)
    for gone in ("httpx", "X-Vortex-Token", "security_manager", "generate_signature"):
        assert gone not in source, gone
