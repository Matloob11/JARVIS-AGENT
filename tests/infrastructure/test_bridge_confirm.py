"""Plan task C-29: ConfirmFlow routing on the bridge (K-14 / K-45).

The agent's broker sends confirm_request and confirm_resolved over /notify (here the internal listener, which
needs no signature); the owner's UI answers with ui_command confirm_response. Only an authenticated owner UI
socket may decide, only a pending, unexpired request, and every decision or refusal is audited. The handlers
run against a temporary store with a recorded sio.emit, so nothing reaches the live bridge.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib
import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from services.utils.jarvis_config import config
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore

LEGACY_TOKEN = "c29-legacy-token-" + "a" * 24
AGENT_TOKEN = "c29-agent-token-" + "b" * 24
HMAC_SECRET = "c29-hmac-secret-" + "c" * 24
INTERNAL_PORT = 5002


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store, no sockets, throwaway credentials and a recorded sio.emit."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    for name, value in (("store", store), ("bridge_manager", module.BridgeStateManager()), ("_clients", {}),
                        ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN), ("_agent_pc_vitals", None),
                        ("_agent_pc_vitals_mono", 0.0), ("_latest_vps_vitals", None)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True), ("env", "development")):
        monkeypatch.setattr(config, name, value)
    yield SimpleNamespace(module=module, store=store, emitted=emitted)
    await store.close()


def _owner_ui(ns: Any, sid: str = "sid-ui") -> None:
    ns.module._clients[sid] = ns.module.SocketClient(role="ui", ip="203.0.113.7", session_id="s-1")


def _legacy(ns: Any, sid: str = "sid-legacy") -> None:
    ns.module._clients[sid] = ns.module.SocketClient(role="legacy", ip="203.0.113.8")


async def _notify(ns: Any, event_type: str, payload: Any) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=ns.module.app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://bridge.test:{INTERNAL_PORT}") as client:
        resp = await client.post("/notify", content=json.dumps({"type": event_type, "payload": payload}).encode())
    assert resp.status_code == 200
    return resp.json()


def _request(confirm_id: str = "c0ffee" * 5 + "ab", **extra: Any) -> dict[str, Any]:
    return {"id": confirm_id, "tool": "run_cmd_command", "summary": "Get-Process chalana hai", "risk": "high",
            "voice_allowed": False, "expires_at": time.time() + 60, "ttl_s": 60,
            "args_preview": {"command": "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5"},
            **extra}


def _events(ns: Any, name: str) -> list[tuple[Any, Any]]:
    return [(data, room) for event, data, room in ns.emitted if event == name]


async def _audit(ns: Any, prefix: str) -> list[dict[str, Any]]:
    return [{**row, "detail": json.loads(row["detail"])} for row in
            await ns.store.recent_audit(action_prefix=prefix)]


# ---------------------------------------------------------------- confirm_request (agent -> bridge -> ui)


async def test_a_confirm_request_is_stored_sent_only_to_ui_and_answered_with_the_owner_ui_count(bridge):
    _owner_ui(bridge)
    _legacy(bridge)  # a legacy socket sees the dialog but can never approve, so it is not counted

    answer = await _notify(bridge, "confirm_request", _request())

    assert answer == {"status": "ok", "ui_clients": 1}
    [(shown, room)] = _events(bridge, "confirm_request")
    assert room == "ui" and shown["tool"] == "run_cmd_command" and shown["risk"] == "high"
    assert shown["args_preview"].startswith('{"command": "Get-Process')
    assert shown["expires_at"] == shown["expires_ts"] and 0 < shown["ttl_s"] <= 60
    assert (await bridge.store.get_confirmation(shown["id"]))["status"] == "pending"
    assert (await bridge.module.build_init_state())["confirmations"] == [shown]


async def test_with_no_owner_ui_the_answer_says_zero_so_the_broker_can_refuse(bridge):
    answer = await _notify(bridge, "confirm_request", _request())
    assert answer == {"status": "ok", "ui_clients": 0}


@pytest.mark.parametrize("change", [
    {"id": "bad id!"}, {"id": ""}, {"tool": ""}, {"tool": 5}, {"risk": "low"}, {"risk": None},
])
async def test_an_unusable_confirm_request_is_ignored(bridge, change):
    answer = await _notify(bridge, "confirm_request", _request(**change))
    assert answer["status"] == "ignored"
    assert not _events(bridge, "confirm_request")
    assert (await bridge.module.build_init_state())["confirmations"] == []


async def test_the_expiry_is_capped_and_an_expired_request_leaves_init_state(bridge):
    far = await _notify(bridge, "confirm_request", _request("a" * 32, expires_at=time.time() + 86400))
    assert far["status"] == "ok"
    [(shown, _room)] = _events(bridge, "confirm_request")
    assert shown["expires_at"] <= time.time() + bridge.module.CONFIRM_TTL_MAX_S + 1

    await _notify(bridge, "confirm_request", _request("b" * 32, expires_at=time.time() - 1))
    pending = (await bridge.module.build_init_state())["confirmations"]
    assert [item["id"] for item in pending] == ["a" * 32]


# ---------------------------------------------------------------- confirm_response (owner ui -> bridge -> agent)


async def test_the_owner_ui_decides_once_and_the_decision_is_forwarded_audited_and_shown(bridge):
    _owner_ui(bridge)
    await _notify(bridge, "confirm_request", _request())
    confirm_id = _request()["id"]
    bridge.emitted.clear()

    ack = await bridge.module.handle_ui_command(
        "sid-ui", {"type": "confirm_response", "payload": {"id": confirm_id, "allow": True, "extra": "x"}})

    assert ack == {"status": "sent"}
    assert ("agent_command", {"type": "confirm_response", "payload": {"id": confirm_id, "allow": True}},
            "agent") in bridge.emitted
    assert _events(bridge, "confirm_resolved") == [
        ({"id": confirm_id, "allow": True, "by": "owner", "reason": "approved"}, "ui")]
    record = await bridge.store.get_confirmation(confirm_id)
    assert (record["status"], record["decided_by"]) == ("allowed", "owner")
    [decision] = await _audit(bridge, "confirm_decision")
    assert decision["detail"] == {"id": confirm_id, "tool": "run_cmd_command", "risk": "high", "allow": True}
    assert (await bridge.module.build_init_state())["confirmations"] == []

    again = await bridge.module.handle_ui_command(
        "sid-ui", {"type": "confirm_response", "payload": {"id": confirm_id, "allow": False}})
    assert again == {"status": "rejected", "reason": "not_pending"}


async def test_a_legacy_socket_may_not_decide_and_the_refusal_is_audited(bridge):
    _legacy(bridge)
    await _notify(bridge, "confirm_request", _request())
    bridge.emitted.clear()

    ack = await bridge.module.handle_ui_command(
        "sid-legacy", {"type": "confirm_response", "payload": {"id": _request()["id"], "allow": True}})

    assert ack == {"status": "rejected", "reason": "not_allowed"}
    assert not [e for e, _d, _r in bridge.emitted if e in ("agent_command", "confirm_resolved")]
    assert (await bridge.store.get_confirmation(_request()["id"]))["status"] == "pending"
    [refusal] = await _audit(bridge, "confirm_response_refused")
    assert refusal["detail"]["reason"] == "not_owner_ui"


async def test_an_agent_socket_cannot_send_ui_commands_at_all(bridge):
    bridge.module._clients["sid-agent"] = bridge.module.SocketClient(role="agent", ip="10.0.0.2")
    ack = await bridge.module.handle_ui_command(
        "sid-agent", {"type": "confirm_response", "payload": {"id": "x" * 32, "allow": True}})
    assert ack == {"status": "rejected", "reason": "not_allowed"}


@pytest.mark.parametrize("payload, reason", [
    ({"id": "c0ffee" * 5 + "ab", "allow": "yes"}, "bad_payload"),
    ({"id": "c0ffee" * 5 + "ab"}, "bad_payload"),
    ({"id": "../etc", "allow": True}, "bad_payload"),
    ("allow", "bad_payload"),
    ({"id": "f" * 32, "allow": True}, "not_pending"),
])
async def test_a_wrong_or_unknown_confirm_response_changes_nothing(bridge, payload, reason):
    _owner_ui(bridge)
    await _notify(bridge, "confirm_request", _request())
    bridge.emitted.clear()

    ack = await bridge.module.handle_ui_command("sid-ui", {"type": "confirm_response", "payload": payload})

    assert ack == {"status": "rejected", "reason": reason}
    assert not [e for e, _d, _r in bridge.emitted if e in ("agent_command", "confirm_resolved")]
    assert (await bridge.store.get_confirmation(_request()["id"]))["status"] == "pending"


async def test_an_expired_request_can_no_longer_be_allowed(bridge):
    _owner_ui(bridge)
    await _notify(bridge, "confirm_request", _request(expires_at=time.time() - 1))
    ack = await bridge.module.handle_ui_command(
        "sid-ui", {"type": "confirm_response", "payload": {"id": _request()["id"], "allow": True}})
    assert ack == {"status": "rejected", "reason": "not_pending"}


# ---------------------------------------------------------------- confirm_resolved (agent -> bridge -> ui)


@pytest.mark.parametrize("payload, by", [
    ({"allow": True, "reason": "approved", "result_status": "success"}, "owner"),
    ({"allow": False, "reason": "voice"}, "owner"),
    ({"allow": False, "reason": "expired"}, "timeout"),
    ({"allow": False, "reason": "no_ui"}, "no_ui"),
    ({"allowed": False, "reason": "cancelled"}, "cancelled"),
])
async def test_the_agents_confirm_resolved_closes_the_row_and_every_dialog(bridge, payload, by):
    await _notify(bridge, "confirm_request", _request())
    bridge.emitted.clear()

    await _notify(bridge, "confirm_resolved", {"id": _request()["id"], **payload})

    [(event, room)] = _events(bridge, "confirm_resolved")
    assert room == "ui" and event["id"] == _request()["id"] and event["by"] == by
    assert event["allow"] is (payload.get("allow", payload.get("allowed")) is True)
    if "result_status" in payload:
        assert event["result_status"] == "success"
    assert (await bridge.store.get_confirmation(_request()["id"]))["status"] != "pending"


async def test_a_confirm_resolved_without_a_usable_id_is_dropped(bridge):
    await _notify(bridge, "confirm_resolved", {"id": "no good", "allow": True})
    await _notify(bridge, "confirm_resolved", "approved")
    assert not _events(bridge, "confirm_resolved")


# ---------------------------------------------------------------- tool_sync progress rows (C-29 step 4)


async def test_a_progress_tool_sync_also_feeds_the_job_row(bridge):
    row = {"id": "job-1", "tool": "download_youtube_media", "details": "Video download", "status": "running",
           "phase": "progress", "progress": 40, "ts": 1.5}
    await _notify(bridge, "tool_sync", row)
    await _notify(bridge, "tool_sync", {**row, "phase": "end", "status": "success", "progress": 100})

    assert [data for data, _room in _events(bridge, "tool_update")] == [
        row, {**row, "phase": "end", "status": "success", "progress": 100}]
    assert _events(bridge, "tool_progress") == [({
        "job_id": "job-1", "tool": "download_youtube_media", "title": "Video download", "status": "running",
        "percent": 40, "message": "Video download", "ts": 1.5}, "ui")]
