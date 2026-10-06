"""Plan task C-29 part 2: the confirm_selftest round trip (contracts K-14 / K-45, used live by C-30).

run_confirm_selftest drives ONE dummy medium-risk ConfirmFlow round trip end to end: confirm_request
through the broker's sender, Sir's decision (UI resolve or a spoken haan), broker resolve, and - only
on Allow - the confirm_selftest_marker tool through the real tool wrapper (K-14: timeout, tool_sync,
the K-45 execution audit), which writes the marker file exactly once. Each test gets a fresh broker
whose sender is a fake bridge and whose on_result is a recorder, a scratch copy of the plugin
manager's registry holding the selftest tools, and a marker under tmp_path, so nothing reaches the
network, the live broker, logs/ or the real logs/confirm_selftest.marker.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import importlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from services.ai_core import jarvis_confirm, jarvis_plugin_manager as pm_module
from services.utils import jarvis_audit, jarvis_bridge
from services.utils.jarvis_config import config
from services.utils.jarvis_resilience import resilience_manager


class FakeBridge:
    """The bridge's /notify for ConfirmFlow events, answering like C-29: {status, ui_clients}."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.answers: list[Any] = []  # queued answers: an exception instance is raised instead
        self.default: Any = {"status": "ok", "ui_clients": 1}

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> Any:
        self.sent.append((event_type, dict(payload)))
        answer = self.answers.pop(0) if self.answers else self.default
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.sent if kind == event_type]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """Fast retries, a scratch tool registry holding the selftest tools, and no writes to
    logs/security.log (the approved leg now runs through the real tool wrapper)."""
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_RETRY_DELAY_S", 0)
    monkeypatch.setattr(jarvis_audit.JarvisAudit, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(config, "confirm_timeout_s", 60, raising=False)
    monkeypatch.setattr(config, "voice_confirm_high", False, raising=False)
    monkeypatch.setattr(pm_module.plugin_manager, "tools", list(pm_module.plugin_manager.tools))
    monkeypatch.setattr(resilience_manager, "_breakers", dict(resilience_manager._breakers))
    jarvis_confirm._register_selftest_tools()  # into the scratch registry copy


@pytest.fixture(name="audit", autouse=True)
def _audit_rows(monkeypatch) -> list[tuple[Any, ...]]:
    """The wrapper's TOOL_EXEC audit rows (log_tool), recorded instead of written."""
    rows: list[tuple[Any, ...]] = []
    monkeypatch.setattr(jarvis_audit, "log_tool", lambda *row: rows.append(row))
    return rows


@pytest.fixture(name="sync", autouse=True)
def _sync_events(monkeypatch) -> list[dict[str, Any]]:
    """The wrapper's tool_sync posts, recorded instead of sent to the bridge."""
    events: list[dict[str, Any]] = []

    async def notify_event(event_type: str, payload: Any) -> None:
        if event_type == "tool_sync":
            events.append(payload)

    monkeypatch.setattr(jarvis_bridge, "notify_event", notify_event)
    return events


@pytest.fixture(name="bridge")
def _bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture(name="results")
def _results() -> list[dict[str, Any]]:
    return []


@pytest.fixture(name="broker")
async def _broker(bridge, results):
    async def on_result(result: dict[str, Any]) -> None:
        results.append(result)

    broker = jarvis_confirm.ConfirmationBroker(sender=bridge, on_result=on_result)
    yield broker
    await broker.shutdown()


@pytest.fixture(name="marker")
def _marker(tmp_path) -> Path:
    return tmp_path / "logs" / "confirm_selftest.marker"


async def _until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def _flush_tool_sync() -> None:
    """Waits until the plugin manager's background tool_sync posts are done."""
    while pending := {task for task in pm_module._sync_tasks if not task.done()}:
        await asyncio.wait(pending)


def _selftest(broker, marker: Path, **kwargs: Any) -> "asyncio.Task[dict[str, Any]]":
    return asyncio.ensure_future(
        jarvis_confirm.run_confirm_selftest(broker=broker, marker_path=marker, **kwargs))


# ---------------------------------------------------------------- the round trip


async def test_allow_writes_the_marker_exactly_once_and_reports_the_timings(broker, bridge, marker):
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())

    [request] = broker.pending()
    assert (request["tool"], request["risk"], request["voice_allowed"]) == (
        "confirm_selftest", "medium", True)
    assert bridge.of("confirm_request")[0]["id"] == request["id"]
    assert not marker.exists()  # nothing runs before Sir's Allow

    await broker.resolve(request["id"], True, source="ui")
    result = await task

    assert (result["status"], result["decision"], result["marker"]) == ("success", "allowed", "created")
    assert result["confirm_id"] == request["id"] and result["reason"] == "approved"
    assert 0 <= result["request_ms"] <= result["total_ms"]
    assert 0 <= result["decision_ms"] <= result["total_ms"]
    written = json.loads(marker.read_text(encoding="utf-8"))
    assert written["nonce"] and written["ts"] == pytest.approx(time.time(), abs=30)
    assert len(marker.read_text(encoding="utf-8").splitlines()) == 1


async def test_the_approved_leg_runs_through_the_real_tool_wrapper(broker, marker, sync, audit):
    # SEC-3: no raw coroutine bypass - the approved run is the confirm_selftest_marker tool via
    # the same _run_confirmed seam real approvals use, so tool_sync and the K-45 audit happen.
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())
    await broker.resolve(broker.pending()[0]["id"], True, source="ui")
    result = await task
    assert result["marker"] == "created"

    await _flush_tool_sync()
    steps = [(event["phase"], event["status"]) for event in sync
             if event["tool"] == "confirm_selftest_marker"]
    assert steps == [("start", "running"), ("end", "success")]
    assert any(row[0] == "confirm_selftest_marker" and row[1] == "medium" and row[2] == "success"
               for row in audit)


async def test_deny_leaves_no_marker_and_is_still_a_clear_success(broker, marker, results):
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())

    await broker.resolve(broker.pending()[0]["id"], False, source="ui")
    result = await task

    assert (result["status"], result["decision"], result["marker"]) == ("success", "denied", "absent")
    assert result["reason"] == "denied" and not marker.exists()
    await asyncio.sleep(0.05)
    assert results == []  # no approved run, so nothing reached on_result


async def test_a_spoken_haan_also_closes_the_loop(broker, marker):
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())

    broker.note_user_transcript("haan", ts=time.time() + 1)
    voiced = await broker.confirm_by_voice(broker.pending()[0]["id"])
    result = await task

    assert voiced["status"] == "started"
    assert (result["status"], result["decision"], result["marker"]) == ("success", "allowed", "created")
    assert result["reason"] == "voice"


async def test_no_decision_in_time_is_a_clear_expiry_error(broker, marker):
    result = await jarvis_confirm.run_confirm_selftest(broker=broker, marker_path=marker, ttl_s=0.05)

    assert (result["status"], result["error_code"]) == ("error", "CONFIRM_EXPIRED")
    assert (result["decision"], result["marker"]) == ("expired", "absent")
    assert not marker.exists()


async def test_an_unreachable_bridge_is_reported_with_the_refusal_and_timings(broker, bridge, marker):
    bridge.answers = [ConnectionError("down"), ConnectionError("still down")]

    result = await jarvis_confirm.run_confirm_selftest(broker=broker, marker_path=marker)

    assert (result["status"], result["error_code"]) == ("error", "CONFIRM_UI_UNREACHABLE")
    assert result["tool"] == "confirm_selftest" and result["marker"] == "absent"
    assert result["request_ms"] >= 0 and result["total_ms"] >= result["request_ms"]
    assert not marker.exists() and broker.pending() == []


async def test_a_stale_marker_from_an_earlier_run_is_removed_first(broker, marker):
    marker.parent.mkdir(parents=True)
    marker.write_text("stale\n", encoding="utf-8")
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())

    assert not marker.exists()  # Deny must mean 'no marker', whatever an earlier run left behind
    await broker.resolve(broker.pending()[0]["id"], False, source="ui")
    result = await task

    assert result["stale_marker_removed"] is True
    assert (result["decision"], result["marker"]) == ("denied", "absent")


async def test_a_second_selftest_while_one_is_pending_is_refused_and_cannot_eat_the_marker(
        broker, marker, tmp_path):
    # SEC-6: one selftest at a time, so a second call's stale-marker cleanup can never delete the
    # marker the first run's Allow is about to write (or just wrote).
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())

    second = await jarvis_confirm.run_confirm_selftest(broker=broker,
                                                       marker_path=tmp_path / "other.marker")
    assert (second["status"], second["error_code"]) == ("error", "SELFTEST_ALREADY_RUNNING")
    assert len(broker.pending()) == 1  # the refusal asked nothing new

    await broker.resolve(broker.pending()[0]["id"], True, source="ui")
    result = await task
    assert (result["decision"], result["marker"]) == ("allowed", "created") and marker.exists()

    # the slot is free again afterwards
    after = await jarvis_confirm.run_confirm_selftest(broker=broker, marker_path=marker, ttl_s=0.05)
    assert after["error_code"] == "CONFIRM_EXPIRED" and after["stale_marker_removed"] is True


async def test_a_broker_shutdown_mid_wait_is_a_cancellation_not_a_deny(broker, marker):
    # SEC-6: reason 'cancelled' (session end) is not reported as Sir's Deny.
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())

    await broker.shutdown()
    result = await task

    assert (result["status"], result["error_code"]) == ("error", "CONFIRM_CANCELLED")
    assert (result["decision"], result["marker"]) == ("cancelled", "absent")
    assert not marker.exists()


# ---------------------------------------------------------------- the tiny supports it runs on


async def test_wait_outcome_waits_for_the_decision_and_answers_a_decided_id_at_once(broker, marker):
    task = _selftest(broker, marker)
    await _until(lambda: broker.pending())
    confirm_id = broker.pending()[0]["id"]

    waiter = asyncio.ensure_future(broker.wait_outcome(confirm_id))
    await asyncio.sleep(0.02)
    assert not waiter.done()  # it waits for Sir, it never decides itself

    await broker.resolve(confirm_id, True, source="ui")
    outcome = await waiter
    assert outcome["status"] == "started" and outcome["reason"] == "approved"
    assert await broker.wait_outcome(confirm_id) == outcome  # decided ids answer at once
    await task


async def test_wait_outcome_of_an_unknown_id_is_none(broker):
    assert await broker.wait_outcome("never-asked") is None
    assert await broker.wait_outcome("") is None


def test_selftest_enabled_accepts_exactly_1_only(monkeypatch):
    # SEC-5: the gate is exactly '1', matching the K-45 contract line.
    for value, enabled in (("1", True), (" 1 ", True), ("true", False), ("YES", False),
                           ("on", False), ("0", False), ("", False), ("11", False)):
        monkeypatch.setenv(jarvis_confirm.SELFTEST_ENV, value)
        assert jarvis_confirm.selftest_enabled() is enabled, value
    monkeypatch.delenv(jarvis_confirm.SELFTEST_ENV)
    assert jarvis_confirm.selftest_enabled() is False


def test_the_selftest_tools_register_only_while_the_env_flag_is_set(monkeypatch):
    # C29P2-5: the module-level gate itself, pinned by reloading the module against an isolated
    # registry - without the flag neither selftest tool registers; with it both do.
    fresh = object.__new__(pm_module.JarvisPluginManager)
    fresh.__init__()
    monkeypatch.setattr(pm_module.JarvisPluginManager, "_instance", fresh)
    monkeypatch.setattr(pm_module, "plugin_manager", fresh)

    def names() -> set[str]:
        return {getattr(tool, "__name__", "") for tool in fresh.tools}

    try:
        monkeypatch.setenv(jarvis_confirm.SELFTEST_ENV, "0")
        importlib.reload(jarvis_confirm)
        assert "confirm_action" in names()  # always registered
        assert not {"confirm_selftest", "confirm_selftest_marker"} & names()

        monkeypatch.setenv(jarvis_confirm.SELFTEST_ENV, "1")
        fresh.tools = []
        importlib.reload(jarvis_confirm)
        assert {"confirm_selftest", "confirm_selftest_marker"} <= names()
    finally:
        monkeypatch.undo()
        importlib.reload(jarvis_confirm)  # the real env, registry and broker again
        # services.ai_core.confirmation re-exports the broker by value; rebind it to the reloaded
        # module so the "both import paths give the same broker" invariant keeps holding.
        importlib.reload(importlib.import_module("services.ai_core.confirmation"))


def test_the_marker_lives_under_the_project_logs_folder():
    path = jarvis_confirm.selftest_marker_path()
    assert path.name == "confirm_selftest.marker" and path.parent.name == "logs"
    assert Path(config.project_root) in path.parents
