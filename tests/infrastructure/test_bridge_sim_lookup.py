"""Plan task C-79: the SIM ui_command on the bridge (K-03 / K-34).

ui_command sim_lookup comes only from the owner's authenticated UI socket, its payload is validated
(the legacy string <= 20 chars or {query <= 20 chars, purpose <= 200 chars}; the query an 11-digit
03x number or a 13-digit CNIC after normalization; the purpose required once sim_panel_open said the
source requires one), the accepted command is audited masked and forwarded only to room 'agent', and
junk is answered with the sim_data_result shape the dialog reads. sim_panel_open's provider state
lands in init_state for the standalone dialog. The handlers run against a temporary store with a
recorded sio.emit, so nothing reaches the live bridge. Fake digits only.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from services.utils.jarvis_config import config
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore

LEGACY_TOKEN = "c79-legacy-token-" + "a" * 24
AGENT_TOKEN = "c79-agent-token-" + "b" * 24
HMAC_SECRET = "c79-hmac-secret-" + "c" * 24
INTERNAL_PORT = 5002

FAKE_PHONE = "03001234567"          # fake digits only
FAKE_PHONE_MASKED = "030*****567"
FAKE_CNIC = "3520212345671"         # fake digits only
FAKE_CNIC_MASKED = "352*******671"


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
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True), ("env", "development")):
        monkeypatch.setattr(config, name, value)
    yield SimpleNamespace(module=module, store=store, emitted=emitted)
    await store.close()


def _owner_ui(ns: Any, sid: str = "sid-ui") -> None:
    ns.module._clients[sid] = ns.module.SocketClient(role="ui", ip="203.0.113.7", session_id="s-1")
    # C-60 (K-03 v51): a forwarded search needs an agent that can take it (else 'agent_offline', nothing forwarded;
    # tests/infrastructure/test_bridge_settings.py covers that), so these tests run with the owner room's agent
    ns.module._clients["sid-owner-agent"] = ns.module.SocketClient(role="agent", ip="10.0.0.3",
                                                                   room=ns.module._owner_room())


def _legacy(ns: Any, sid: str = "sid-legacy") -> None:
    ns.module._clients[sid] = ns.module.SocketClient(role="legacy", ip="203.0.113.8")


async def _notify(ns: Any, event_type: str, payload: Any) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=ns.module.app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://bridge.test:{INTERNAL_PORT}") as client:
        resp = await client.post("/notify", content=json.dumps({"type": event_type, "payload": payload}).encode())
    assert resp.status_code == 200
    return resp.json()


async def _sim_lookup(ns: Any, payload: Any, sid: str = "sid-ui") -> dict[str, Any]:
    return await ns.module.handle_ui_command(sid, {"type": "sim_lookup", "payload": payload})


def _events(ns: Any, name: str) -> list[tuple[Any, Any]]:
    return [(data, room) for event, data, room in ns.emitted if event == name]


async def _audit(ns: Any, prefix: str) -> list[dict[str, Any]]:
    return [{**row, "detail": json.loads(row["detail"])} for row in
            await ns.store.recent_audit(action_prefix=prefix)]


# ---------------------------------------------------------------- accepted payloads: audit + forward


async def test_the_dialogs_dict_payload_is_audited_masked_and_forwarded_only_to_the_agent(bridge):
    _owner_ui(bridge)

    ack = await _sim_lookup(bridge, {"query": f" {FAKE_PHONE} ", "purpose": " identity verification "})

    assert ack == {"status": "sent"}
    [(command, room)] = _events(bridge, "agent_command")
    assert room == "agent"
    assert command == {"type": "sim_lookup",
                       "payload": {"query": FAKE_PHONE, "purpose": "identity verification"}}
    [row] = await _audit(bridge, "sim_lookup")
    assert row["action"] == "sim_lookup"
    assert row["detail"] == {"query_masked": FAKE_PHONE_MASKED, "purpose_chars": 21}
    # K-34: the raw number and the purpose text never land in the audit table
    assert FAKE_PHONE not in json.dumps(row["detail"]) and "identity" not in json.dumps(row["detail"])


async def test_a_legacy_string_payload_keeps_its_shape(bridge):
    _owner_ui(bridge)
    ack = await _sim_lookup(bridge, f"  {FAKE_PHONE} ")
    assert ack == {"status": "sent"}
    [(command, room)] = _events(bridge, "agent_command")
    assert room == "agent" and command == {"type": "sim_lookup", "payload": FAKE_PHONE}
    [row] = await _audit(bridge, "sim_lookup")
    assert row["detail"] == {"query_masked": FAKE_PHONE_MASKED, "purpose_chars": 0}


async def test_a_cnic_query_with_separators_normalizes_and_passes(bridge):
    _owner_ui(bridge)
    ack = await _sim_lookup(bridge, {"query": "35202-1234567-1", "purpose": "record check"})
    assert ack == {"status": "sent"}
    [row] = await _audit(bridge, "sim_lookup")
    assert row["detail"]["query_masked"] == FAKE_CNIC_MASKED


# ---------------------------------------------------------------- junk: the shaped refusal the UI reads


@pytest.mark.parametrize("payload", [
    "0" * 25,                                   # C-79 acceptance: a 25-char query is rejected
    {"query": "0" * 25, "purpose": ""},
    "1234567",                                  # neither 11-digit 03x nor 13-digit CNIC
    "12345678901",                              # 11 digits but not 03x
    f"{FAKE_PHONE}XXXXXXXXXX",                  # over 20 chars even though the digits fit
    {"query": 5, "purpose": ""}, {"query": "", "purpose": "x"}, {"purpose": "x"}, None, 42, ["x"],
    {"query": FAKE_PHONE, "purpose": "p" * 201},
])
async def test_junk_is_rejected_with_a_validation_error_result_and_nothing_is_forwarded(bridge, payload):
    _owner_ui(bridge)

    ack = await _sim_lookup(bridge, payload)

    assert ack == {"status": "rejected", "reason": "bad_payload"}
    assert not _events(bridge, "agent_command")
    [(refusal, room)] = _events(bridge, "sim_data_result")
    assert room == "ui"
    assert refusal["records"] == [] and refusal["status"] == "validation_error" and refusal["message"]
    assert "timestamp" in refusal  # C79-R5/F5: the same field name as _emit_sim_data
    assert FAKE_PHONE not in json.dumps(refusal)  # masked per K-34, whatever came in
    [row] = await _audit(bridge, "sim_lookup_refused")
    assert row["detail"]["reason"] == "bad_payload"


# ---------------------------------------------------------------- role: the owner's UI only


async def test_a_legacy_or_agent_socket_may_not_search_and_the_refusal_is_audited(bridge):
    _legacy(bridge)
    bridge.module._clients["sid-agent"] = bridge.module.SocketClient(role="agent", ip="10.0.0.2")

    ack = await _sim_lookup(bridge, FAKE_PHONE, sid="sid-legacy")
    assert ack == {"status": "rejected", "reason": "not_allowed"}
    assert await _sim_lookup(bridge, FAKE_PHONE, sid="sid-agent") == {"status": "rejected",
                                                                      "reason": "not_allowed"}
    assert not _events(bridge, "agent_command")
    # F2: the legacy dashboard drops acks, so the role refusal also answers in the dialog's shape
    # (login required), instead of a silent 20 s spin; only the legacy socket got one (the agent
    # socket died at handle_ui_command's generic role gate before the sim handler).
    [(refusal, room)] = _events(bridge, "sim_data_result")
    assert room == "ui" and refusal["records"] == [] and refusal["status"] == "validation_error"
    assert "login" in refusal["message"].lower() and "timestamp" in refusal
    assert FAKE_PHONE not in json.dumps(refusal)
    refusals = await _audit(bridge, "sim_lookup_refused")
    assert [row["detail"]["reason"] for row in refusals] == ["not_owner_ui"]


# ---------------------------------------------------------------- purpose required once the dialog says so


async def test_once_sim_panel_open_requires_a_purpose_the_bridge_enforces_it(bridge):
    _owner_ui(bridge)
    await _notify(bridge, "sim_panel_open", {"message": "ready", "provider": "pak-sim-api",
                                             "configured": True, "requires_purpose": True})
    bridge.emitted.clear()

    short = await _sim_lookup(bridge, {"query": FAKE_PHONE, "purpose": "abc"})
    legacy_string = await _sim_lookup(bridge, FAKE_PHONE)
    ok = await _sim_lookup(bridge, {"query": FAKE_PHONE, "purpose": "identity verification"})

    assert short == {"status": "rejected", "reason": "purpose_required"}
    assert legacy_string == {"status": "rejected", "reason": "purpose_required"}
    assert ok == {"status": "sent"}
    refusals = _events(bridge, "sim_data_result")
    assert len(refusals) == 2 and all(data["status"] == "validation_error" for data, _room in refusals)
    [(command, _room)] = _events(bridge, "agent_command")
    assert command["payload"]["purpose"] == "identity verification"


async def test_without_that_state_a_purposeless_query_still_passes(bridge):
    _owner_ui(bridge)  # a fresh bridge: nothing said the source requires a purpose
    assert await _sim_lookup(bridge, FAKE_PHONE) == {"status": "sent"}


# ---------------------------------------------------------------- provider state for the standalone dialog


async def test_sim_panel_opens_provider_state_lands_in_init_state(bridge):
    state = await bridge.module.build_init_state()
    assert (state["sim_provider"], state["sim_provider_configured"], state["sim_requires_purpose"]) \
        == ("", False, False)

    await _notify(bridge, "sim_panel_open", {"message": "ready", "provider": "pak-sim-api",
                                             "configured": True, "requires_purpose": True})

    state = await bridge.module.build_init_state()
    assert (state["sim_provider"], state["sim_provider_configured"], state["sim_requires_purpose"]) \
        == ("pak-sim-api", True, True)
    [(panel, room)] = _events(bridge, "sim_panel_open")
    assert room == "ui" and panel["provider"] == "pak-sim-api"  # the live event is untouched


async def test_a_sim_data_result_refreshes_the_stored_provider_state_too(bridge):
    # F3: the REAL emitter (_emit_sim_data) sends only 'provider' — never 'configured' — so the
    # stored configured flag is derived from the provider name and can never go stale.
    await _notify(bridge, "sim_panel_open", {"provider": "pak-sim-api", "configured": True,
                                             "requires_purpose": True})
    await _notify(bridge, "sim_data_result", {"records": [], "phone": FAKE_PHONE_MASKED,
                                              "status": "blocked", "message": "provider not configured",
                                              "provider": "not_configured", "timestamp": 1.0})
    state = await bridge.module.build_init_state()
    assert (state["sim_provider"], state["sim_provider_configured"]) == ("not_configured", False)
    assert state["sim_status"] == "blocked" and state["sim_records"] == []


async def test_a_configured_provider_name_derives_configured_true_and_an_explicit_bool_wins(bridge):
    await _notify(bridge, "sim_data_result", {"records": [], "phone": FAKE_PHONE_MASKED,
                                              "status": "success", "message": "ok",
                                              "provider": "pak-sim-api", "timestamp": 1.0})
    state = await bridge.module.build_init_state()
    assert (state["sim_provider"], state["sim_provider_configured"]) == ("pak-sim-api", True)

    # an explicit configured bool (sim_panel_open always carries one) wins over the derivation
    await _notify(bridge, "sim_panel_open", {"provider": "pak-sim-api", "configured": False,
                                             "requires_purpose": False})
    state = await bridge.module.build_init_state()
    assert (state["sim_provider"], state["sim_provider_configured"]) == ("pak-sim-api", False)


async def test_a_result_without_provider_fields_keeps_the_stored_state(bridge):
    await _notify(bridge, "sim_panel_open", {"provider": "pak-sim-api", "configured": True,
                                             "requires_purpose": True})
    await _notify(bridge, "sim_data_result", {"records": [], "phone": FAKE_PHONE_MASKED,
                                              "status": "success", "message": "ok"})
    state = await bridge.module.build_init_state()
    assert (state["sim_provider"], state["sim_provider_configured"], state["sim_requires_purpose"]) \
        == ("pak-sim-api", True, True)


# ---------------------------------------------------------------- the validator itself


async def test_parse_sim_lookup_accepts_only_the_k34_patterns(bridge):
    parse = bridge.module.parse_sim_lookup
    assert parse(FAKE_PHONE, False) == (FAKE_PHONE, FAKE_PHONE_MASKED, "", "")
    assert parse({"query": FAKE_CNIC, "purpose": "record check"}, True) == (
        {"query": FAKE_CNIC, "purpose": "record check"}, FAKE_CNIC_MASKED, "", "")
    assert parse("0" * 25, False)[2] == "bad_payload"
    assert parse("0312-3456789", False)[2] == ""          # 11 digits, 03x, 12 chars as typed
    assert parse({"query": FAKE_PHONE, "purpose": "p" * 200}, True)[2] == ""
    assert parse({"query": FAKE_PHONE, "purpose": "p" * 201}, True)[2] == "bad_payload"
    assert parse({"query": FAKE_PHONE, "purpose": None}, False)[2] == ""
    assert parse(FAKE_PHONE, True)[2] == "purpose_required"


async def test_mask_sim_query_follows_the_k34_rule(bridge):
    mask = bridge.module.mask_sim_query
    assert mask(FAKE_PHONE) == FAKE_PHONE_MASKED
    assert mask("0300-123 4567") == FAKE_PHONE_MASKED
    assert mask(FAKE_CNIC) == FAKE_CNIC_MASKED
    assert mask("042") == "***" and mask("") == ""


async def test_every_k34_mask_helper_agrees_on_the_same_vectors(bridge):
    """C79-R5: the mask rule exists in several Claude-lane copies next to the Cursor-lane
    original; this pins them to each other so none can drift silently."""
    from services.ai_core import agent_loops  # pylint: disable=import-outside-toplevel
    from services.ai_core.jarvis_plugin_manager import (  # pylint: disable=import-outside-toplevel
        mask_sensitive_digits,
    )
    from services.info.jarvis_sim_lookup import _mask_query  # pylint: disable=import-outside-toplevel

    helpers = (bridge.module.mask_sim_query, agent_loops._mask_sim_query, _mask_query,
               mask_sensitive_digits)
    vectors = ("", "0", "42", "0421", "03001", "030012", FAKE_PHONE, "0300-123 4567",
               "(0300) 123.45.67", FAKE_CNIC, "35202-1234567-1", "1" * 20, "a0b3c0d0e1f2g3h4i5j6k7")
    for vector in vectors:
        masks = {helper(vector) for helper in helpers}
        assert len(masks) == 1, f"mask helpers disagree on {vector!r}: {masks}"
