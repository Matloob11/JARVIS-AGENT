"""Plan task C-60 (OP-25): settings persistence and routing on the bridge, the offline command queue and the camera
frame limits (contracts K-46 C-SETTINGS-1, K-03, K-61, K-05 and K-77, v51).

src/core/bridge_settings.py holds the rules (checked here directly); src/core/ui_bridge.py applies them: a settings
command is stored with a revision the bridge owns (one epoch per bridge.db, a counter that only grows and catches up
with an agent echo above it) and travels as settings_update with it; the agent's settings_sync never overwrites an
owner change it has not applied; GET /settings answers the owner's session or agent credentials
only; chat, settings_update and clear_memory wait in bridge.db while no agent can take them (and while bridge.db cannot
give them back) and reach the next agent in order after its settings_bootstrap; a legacy-token socket is an agent only
when it declares the agent role; camera frames go to the agent sockets only. The handlers run against a temporary
bridge.db with a recorded sio.emit; the end-to-end test runs the real app under uvicorn on 127.0.0.1 with throwaway
credentials, so nothing reaches the live bridge, a real env file or real chat data.
"""
# pylint: disable=protected-access,redefined-outer-name,too-many-lines
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import secrets
import socket
import sqlite3
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import socketio
import uvicorn

from services.utils import jarvis_security
from services.utils.jarvis_config import config
from src.core import bridge_settings
from src.core.bridge_desktop import INTERNAL_PORT
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_settings import Echo, SettingsRejected
from src.core.bridge_store import BridgeStore

AGENT_TOKEN = "c60-agent-token-" + "a" * 24
HMAC_SECRET = "c60-hmac-secret-" + "b" * 24
LEGACY_TOKEN = "c60-legacy-token-" + "c" * 24
JWT_SECRET = "c60-session-jwt-secret-" + "d" * 40
ROOM = "jarvis-hybrid"
UI_ORIGIN = "https://dashboard.c60.invalid"
# What the agent's legacy-token fallback sends (services/ai_core/agent_loops.py, K-38 v51): the agent role beside the
# token. Only such a socket is a legacy agent; any other legacy-token socket is a UI socket (C-60 review).
LEGACY_AGENT_AUTH = {"token": LEGACY_TOKEN, "role": "agent"}


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Records(logging.Handler):
    """Collects the records of the bridge's 'UI-BRIDGE' logger (it does not propagate, so caplog never sees them)."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def count(self, text: str, level: int = logging.DEBUG) -> int:
        return sum(text in record.getMessage() for record in self.records if record.levelno >= level)


@pytest.fixture
def records():
    handler = Records()
    logger = logging.getLogger("UI-BRIDGE")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old_level)


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store on a fake clock, no sockets, throwaway credentials, a recorded sio.emit
    (event, data, to, skip_sid) and the rooms each socket joined."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    monkeypatch.setattr(config, "env", "development")
    module = importlib.import_module("src.core.ui_bridge")
    clock = FakeClock(time.time())
    path = tmp_path / "bridge.db"
    emitted: list[tuple[str, Any, Any, Any]] = []
    joined: dict[str, list[str]] = {}

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room"), kwargs.get("skip_sid")))

    async def fake_enter_room(sid: str, room: str, **_kwargs: Any) -> None:
        joined.setdefault(sid, []).append(room)

    async def fake_save_session(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name, value in (("store", BridgeStore(path, clock=clock)),
                        ("bridge_manager", module.BridgeStateManager()),
                        ("_clients", {}), ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN), ("_agent_pc_vitals", None),
                        ("_agent_pc_vitals_mono", 0.0), ("_latest_vps_vitals", None)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.sio, "enter_room", fake_enter_room)
    monkeypatch.setattr(module.sio, "save_session", fake_save_session)
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True),
                        ("session_jwt_secret", JWT_SECRET)):
        monkeypatch.setattr(config, name, value)

    async def restart() -> None:
        """The bridge process restarts: the store is reopened (its settings epoch and revision stay as they are) and
        the volatile state and sockets are gone."""
        await module.store.close()
        monkeypatch.setattr(module, "store", BridgeStore(path, clock=clock))
        monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())
        monkeypatch.setattr(module, "_clients", {})

    ns = SimpleNamespace(module=module, clock=clock, emitted=emitted, joined=joined, restart=restart, path=path)
    yield ns
    await module.store.close()


@pytest.fixture
def owner(bridge, monkeypatch):
    """An enabled room owner (as in the VPS bridge) on the bridge's own callables; any LiveKit call fails the test."""
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel

    def make(env_mode: str | None = None) -> Any:
        settings = bridge_rooms.RoomSettings(api_url="http://livekit.c60.invalid:7880", key="c60-k", secret="c60-s",
                                             room=ROOM, env_mode=env_mode, role_ok=True)
        module = bridge.module
        manager = bridge_rooms.RoomManager(
            settings, store=lambda: module.store, emit_ui=module.emit_ui,
            send_agent_command=lambda data, sid=None: module._send_agent_command(data, sid),
            agent_job_sockets=module._agent_job_sockets, ui_presence=module._ui_presence,
            on_status=module._publish_agent_status, api_factory=lambda: pytest.fail("no LiveKit call expected"),
            on_resume=lambda: module._flush_after_unpause())  # as ui_bridge wires its room owner
        monkeypatch.setattr(module, "rooms", manager)
        return manager

    bridge.make_owner = make
    return bridge


def _ui(ns: Any, sid: str = "sid-ui") -> None:
    """An owner UI socket (role 'ui', as a jarvis_session cookie gives)."""
    ns.module._clients[sid] = ns.module.SocketClient(role="ui", ip="203.0.113.7", session_id="s-c60")


async def _agent(ns: Any, sid: str = "sid-agent", **extra: Any) -> None:
    auth = {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "abc123", **extra}
    assert await ns.module.handle_connect(sid, {}, auth) is True


async def _command(ns: Any, cmd_type: str, payload: Any = None, sid: str = "sid-ui") -> dict[str, Any]:
    return await ns.module.handle_ui_command(sid, {"type": cmd_type, "payload": payload})


def _sent(ns: Any, event: str = "agent_command") -> list[tuple[Any, Any]]:
    return [(data, to) for name, data, to, _skip in ns.emitted if name == event]


async def _notify(ns: Any, event_type: str, payload: Any) -> dict[str, Any]:
    """An agent notify on the internal listener (the network is its credential, K-39 v4)."""
    transport = httpx.ASGITransport(app=ns.module.app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://bridge.test:{INTERNAL_PORT}") as client:
        resp = await client.post("/notify", content=json.dumps({"type": event_type, "payload": payload}).encode())
    assert resp.status_code == 200, resp.text
    return resp.json()


def _meta(ns: Any) -> Any:
    return ns.module.store.settings_with_meta()


# ---------------------------------------------------------------- the rules (bridge_settings)


def test_settings_update_keeps_known_keys_and_types_and_maps_the_aliases():
    clean = bridge_settings.parse_settings_update({
        "voice": " puck ", "language": "ur-PK", "persona": " ANNA ", "wake_word_active": False, "voice_id": True,
        "emotionalIntelligence": False, "style": " formal ", "wake_followup_s": "12", "voice_id_threshold": 0.5,
        "voice_id_reject": "Polite", "turn_mode": "gated", "transcript_script": "native",
        "speak_typed_replies": "never", "quiet_hours": "22:00-07:00", "confirm_level": "strict", "muted": True,
        "vision_mode": "on_request", "automation": {"desktop_control": False, "voice_confirm_high": False},
        "saved": ["voice"], "updated_ts": 5})
    assert clean == {
        "voice": "puck", "language": "ur-PK", "persona": "anna", "wake_word": False, "voice_id": True,
        "emotional_intelligence": False, "style": "formal", "wake_followup_s": 12, "voice_id_threshold": 0.5,
        "voice_id_reject": "polite", "turn_mode": "gated", "transcript_script": "native",
        "speak_typed_replies": "never", "quiet_hours": "22:00-07:00", "confirm_level": "strict", "muted": True,
        "vision_mode": "on_request", "automation": {"desktop_control": False, "voice_confirm_high": False}}
    # the canonical key wins over its alias, whatever the order
    assert bridge_settings.parse_settings_update({"emotional_intelligence": True, "emotionalIntelligence": False}) == {
        "emotional_intelligence": True}
    assert bridge_settings.parse_settings_update({"quiet_hours": None, "language": "auto"}) == {
        "quiet_hours": "", "language": "auto"}


@pytest.mark.parametrize("payload, reason, field", [
    ("persona=anna", "bad_payload", None),
    ({}, "bad_payload", None),
    ({"saved": ["voice"]}, "bad_payload", None),  # only the agent's output keys: nothing to change
    ({f"k{n}": 1 for n in range(40)}, "bad_payload", None),
    ({"rm_rf": True}, "unknown_setting", "rm_rf"),
    ({"voice": "Puck", "guarded_tools": []}, "unknown_setting", "guarded_tools"),
    ({"persona": "bob"}, "bad_value", "persona"),
    ({"wake_word": "yes"}, "bad_value", "wake_word"),
    ({"voice_id": 1}, "bad_value", "voice_id"),
    ({"voice": "<script>"}, "bad_value", "voice"),
    ({"voice": 7}, "bad_value", "voice"),
    ({"language": "../../etc"}, "bad_value", "language"),
    ({"style": "x" * 41}, "bad_value", "style"),
    ({"wake_followup_s": 61}, "bad_value", "wake_followup_s"),
    ({"wake_followup_s": True}, "bad_value", "wake_followup_s"),
    ({"voice_id_threshold": "nan"}, "bad_value", "voice_id_threshold"),
    ({"quiet_hours": "always"}, "bad_value", "quiet_hours"),
    # C-60 review: digit shapes alone are not times; the agent refuses these, so the bridge does too
    ({"quiet_hours": "25:00-07:00"}, "bad_value", "quiet_hours"),
    ({"quiet_hours": "9:75-10:00"}, "bad_value", "quiet_hours"),
    ({"quiet_hours": "10:00-10:00"}, "bad_value", "quiet_hours"),
    ({"quiet_hours": " 7:05 - 07:05 "}, "bad_value", "quiet_hours"),
    ({"quiet_hours": "24:00-06:00"}, "bad_value", "quiet_hours"),
    ({"quiet_hours": "22:00-07:60"}, "bad_value", "quiet_hours"),
    ({"quiet_hours": 2200}, "bad_value", "quiet_hours"),
    ({"turn_mode": "slow"}, "bad_value", "turn_mode"),
    ({"vision_mode": "always"}, "bad_value", "vision_mode"),
    ({"automation": {"desktop_control": "off"}}, "bad_value", "automation"),
    ({"automation": {"rm_rf": True}}, "bad_value", "automation"),
    ({"automation": {}}, "bad_value", "automation"),
])
def test_junk_settings_are_rejected_with_a_reason_and_the_field(payload, reason, field):
    with pytest.raises(SettingsRejected) as caught:
        bridge_settings.parse_settings_update(payload)
    assert (caught.value.reason, caught.value.field) == (reason, field) and caught.value.message


@pytest.mark.parametrize("value", [
    "22:00-07:00", " 9:05 - 7:00 ", "0:00-23:59", "23:59-0:00", "7:5-8:00", "25:00-07:00", "9:75-10:00",
    "10:00-10:00", " 7:05 - 07:05 ", "24:00-06:00", "22:00-07:60", "123:00-1:00", "22:00", "", "  ", None, 2200,
    "22:00-07:00x", "２２:００-０７:００", " " * 40 + "22:00-07:00", "22:00" + " " * 40 + "-07:00",
])
def test_quiet_hours_follow_the_agents_rule(value):
    """C-60 review: the bridge stored any 'D:DD-D:DD' shape ('25:00-07:00', '9:75-10:00', '10:00-10:00') as the owner's
    value; the agent refuses those, and a bootstrap whose only field it refuses sends no settings_sync, so the copy (and
    every UI) showed it until a later sync. Now the bridge keeps exactly what the agent keeps (hours below 24, minutes
    below 60, start and end differ, zero-padded) and refuses everything else as 'bad_value'."""
    from src.core import agent_settings  # pylint: disable=import-outside-toplevel

    agent_value, _error = agent_settings._check_quiet_hours(value)
    if agent_value is None:
        with pytest.raises(SettingsRejected) as caught:
            bridge_settings.parse_settings_update({"quiet_hours": value})
        assert (caught.value.reason, caught.value.field) == ("bad_value", "quiet_hours")
        assert caught.value.message == "quiet_hours must be 'HH:MM-HH:MM' (start and end differ) or empty"
    else:
        assert bridge_settings.parse_settings_update({"quiet_hours": value}) == {"quiet_hours": agent_value}


def test_quiet_hours_are_kept_zero_padded_as_the_agent_saves_them():
    assert bridge_settings.parse_settings_update({"quiet_hours": " 9:05 - 7:00 "}) == {"quiet_hours": "09:05-07:00"}
    assert bridge_settings.parse_settings_update({"quiet_hours": None}) == {"quiet_hours": ""}


async def test_junk_quiet_hours_with_no_agent_are_refused_and_never_stored_or_queued(bridge):
    """C-60 review: with no agent connected a bad quiet_hours was stored as the owner's value (by 'ui', with a
    revision), queued and shown in init_state, GET /settings and the bridge's settings_sync. Now it is refused with
    reason bad_value and field quiet_hours, the sender gets settings_sync errors (never the value), and nothing is
    stored or queued."""
    _ui(bridge)
    for junk in ("25:00-07:00", "9:75-10:00", "10:00-10:00"):
        ack = await _command(bridge, "settings_update", {"quiet_hours": junk})
        assert ack == {"status": "rejected", "reason": "bad_value", "field": "quiet_hours"}
    syncs = _sent(bridge, "settings_sync")
    assert [to for _sync, to in syncs] == ["sid-ui"] * 3
    assert all(set(sync["errors"]) == {"quiet_hours"} and "pending" not in sync for sync, _to in syncs)
    assert not any(junk in json.dumps(syncs) for junk in ("25:00", "9:75", "10:00-10:00"))
    settings, meta = await _meta(bridge)
    assert settings == {} and meta["revision"] == 0 and await bridge.module.store.count_pending() == 0
    assert await _command(bridge, "settings_update", {"quiet_hours": "22:00-7:00"}) == {"status": "queued"}
    assert (await _meta(bridge))[0] == {"quiet_hours": "22:00-07:00"}


def test_the_legacy_commands_parse_like_the_agent():
    assert bridge_settings.parse_persona_change(" Anna ") == {"persona": "anna"}
    assert bridge_settings.parse_persona_change({"persona": "JARVIS"}) == {"persona": "jarvis"}
    for junk in ("bob", None, 1, {"persona": 2}):
        with pytest.raises(SettingsRejected):
            bridge_settings.parse_persona_change(junk)
    assert bridge_settings.wake_word_partial(False, True) == {"wake_word": False}
    assert bridge_settings.wake_word_partial(None, True) == {"wake_word": False}  # no payload: the toggle
    assert bridge_settings.wake_word_partial(None, False) == {"wake_word": True}
    for junk in ("false", 0, {"wake_word": True}):
        with pytest.raises(SettingsRejected):
            bridge_settings.wake_word_partial(junk, True)
    assert bridge_settings.current_wake_word({}, {}) is True  # K-46 default
    assert bridge_settings.current_wake_word({}, {"wake_word_active": False}) is False  # the agent's last sync
    assert bridge_settings.current_wake_word({"wake_word": True}, {"wake_word_active": False}) is True


def test_the_view_puts_the_k46_defaults_under_the_stored_object():
    meta = {"epoch": "e1", "revision": 4, "delivered": 0,
            "keys": {"language": {"rev": 4, "by": "ui", "src": ""}, "voice": {"rev": 2, "by": "agent", "src": "a1"}}}
    assert bridge_settings.settings_view({}, meta) == {
        "voice_id": True, "wake_word": True, "persona": "jarvis", "voice": "Charon", "revision": 4,
        "revision_epoch": "e1"}
    # the agent's last persona/wake/mute notifies under the stored object; no JARVIS default voice for ANNA
    view = bridge_settings.settings_view({"language": "ur", "revision": 99}, meta,
                                         {"active_persona": "anna", "wake_word_active": False, "muted": True})
    assert view == {"voice_id": True, "wake_word": False, "persona": "anna", "muted": True, "language": "ur",
                    "revision": 4, "revision_epoch": "e1"}
    stored = {"persona": "jarvis", "voice": "Puck"}
    assert bridge_settings.settings_view(stored, meta, {"active_persona": "anna"})["voice"] == "Puck"
    agent_view = bridge_settings.settings_view(stored, meta, with_revisions=True)
    assert agent_view["revisions"] == {"language": {"rev": 4, "by": "ui"}, "voice": {"rev": 2, "by": "agent",
                                                                                   "src": "a1"}}
    assert bridge_settings.bootstrap_payload({}, meta) is None  # nothing stored: no bootstrap
    assert bridge_settings.bootstrap_payload(stored, meta) == {**stored, "revision": 4, "revision_epoch": "e1",
                                                               "revisions": agent_view["revisions"]}


def test_every_ui_change_takes_a_new_revision_stamped_by_ui():
    meta = {"epoch": "e1", "revision": 0, "delivered": 0, "keys": {}}
    current = {"automation": {"desktop_control": True, "clipboard_helper": True}}
    writes, meta, by, revision = bridge_settings.ui_merge(
        current, meta, {"persona": "anna", "muted": True, "automation": {"desktop_control": False}})
    assert (by, revision, meta["revision"]) == ("ui", 1, 1)
    assert writes == {"persona": "anna", "muted": True,
                      "automation": {"desktop_control": False, "clipboard_helper": True}}  # merged key by key
    assert meta["keys"] == {"persona": {"rev": 1, "by": "ui", "src": ""},
                            "automation": {"rev": 1, "by": "ui", "src": ""}}  # muted is a control: no stamp
    _writes, meta, _by, revision = bridge_settings.ui_merge({"persona": "anna"}, meta, {"persona": "anna"})
    assert revision == 2 and meta["keys"]["persona"]["rev"] == 2  # the same value again still takes a revision


def test_an_agent_value_never_overwrites_a_newer_ui_change():
    meta = {"epoch": "e1", "revision": 5, "delivered": 3,
            "keys": {"persona": {"rev": 5, "by": "ui", "src": ""}, "voice": {"rev": 2, "by": "ui", "src": ""}}}
    current = {"persona": "anna", "voice": "Puck"}
    incoming = {"persona": "jarvis", "voice": "Kore", "language": "ur", "saved": ["persona"], "updated_ts": 9.0,
                "revision": 77}

    def merge(echo: Echo) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        writes, new_meta, by, kept = bridge_settings.agent_merge(current, meta, incoming, echo)
        assert by == "agent"
        return writes, new_meta, kept

    # the agent applied voice (rev 2) but not persona (rev 5): persona is kept, its other values land
    writes, new_meta, kept = merge(Echo("per_key", "e1", 2, {"voice": 2}, "agentA"))
    assert kept == {"persona": "anna"}
    assert writes == {"voice": "Kore", "language": "ur", "saved": ["persona"], "updated_ts": 9.0}
    assert new_meta["revision"] == 6 and new_meta["keys"]["voice"] == {"rev": 6, "by": "agent", "src": "agentA"}
    assert new_meta["keys"]["language"] == {"rev": 6, "by": "agent", "src": "agentA"}
    assert new_meta["keys"]["persona"] == {"rev": 5, "by": "ui", "src": ""}
    # once it says it applied revision 5, its value wins (the agent refused or changed it since)
    _writes, _meta2, kept = merge(Echo("per_key", "e1", 5, {"persona": 5, "voice": 2}, "agentA"))
    assert kept == {}
    assert merge(Echo("single", "e1", 5))[2] == {} and merge(Echo("single", "e1", 4))[2] == {"persona": "anna"}
    # another epoch's echo applied nothing of this one; an agent without the echo: what the bridge delivered
    assert merge(Echo("per_key", "other", 9, {"persona": 9, "voice": 9}))[2] == {"persona": "anna", "voice": "Puck"}
    assert merge(Echo())[2] == {"persona": "anna"}  # delivered 3: voice (2) was delivered, persona (5) was not
    # values the copy holds already change nothing (no new revision)
    same = bridge_settings.agent_merge(current, meta, {"persona": "anna"}, Echo())
    assert same[0] == {} and same[1]["revision"] == 5 and same[3] == {}
    # the other brain's change is kept the same way until this agent took it; this agent's own change never is
    by_vps = {**meta, "keys": {"voice": {"rev": 4, "by": "agent", "src": "vps"}}}
    assert bridge_settings.agent_merge(current, by_vps, {"voice": "Kore"}, Echo("per_key", "e1", 0, {}, "pc"))[3] == {
        "voice": "Puck"}
    assert bridge_settings.agent_merge(current, by_vps, {"voice": "Kore"},
                                       Echo("per_key", "e1", 4, {"voice": 4}, "pc"))[3] == {}
    assert bridge_settings.agent_merge(current, by_vps, {"voice": "Kore"}, Echo("per_key", "e1", 0, {}, "vps"))[3] == {}
    # an older agent (no echo, no instance) keeps its C-54 word on what older agents wrote (src '')
    by_old = {**meta, "keys": {"voice": {"rev": 4, "by": "agent", "src": ""}}}
    assert bridge_settings.agent_merge(current, by_old, {"voice": "Kore"}, Echo())[3] == {}
    assert bridge_settings.agent_merge(current, by_old, {"voice": "Kore"}, Echo("per_key", "e1", 0, {}, "pc"))[3] == {
        "voice": "Puck"}  # a C-60 agent takes it from its bootstrap first


def test_parse_echo_reads_the_top_level_of_a_settings_sync():
    assert bridge_settings.parse_echo({"settings": {}}) == Echo()
    echo = bridge_settings.parse_echo({"revision": 4, "revision_epoch": "e1", "instance": "abc123",
                                       "revisions": {"voice": 4, "persona": True, "x": -1}})
    assert echo == Echo("per_key", "e1", 4, {"voice": 4}, "abc123")
    assert bridge_settings.parse_echo({"revision": 3, "revision_epoch": "e1"}) == Echo("single", "e1", 3)
    assert bridge_settings.parse_echo({"instance": "../x"}).instance == ""


def test_the_relay_shows_the_kept_values_and_drops_revision_noise():
    payload = {"settings": {"persona": "jarvis", "voice": "Kore"}, "applied": {"voice": "applied"}}
    assert bridge_settings.relay_agent_sync(payload, {}) is payload  # nothing to change: as sent
    relayed = bridge_settings.relay_agent_sync(
        {**payload, "applied": {"voice": "applied", "revision": "unsupported", "revision_epoch": "unsupported"}},
        {"persona": "anna"})
    assert relayed["settings"] == {"persona": "anna", "voice": "Kore"} and relayed["applied"] == {"voice": "applied"}
    assert bridge_settings.relay_agent_sync({"persona": "jarvis", "applied": True}, {"persona": "anna"}) == {
        "persona": "anna", "applied": True}


# ---------------------------------------------------------------- settings_update through the bridge


async def test_a_settings_update_is_stored_with_a_revision_and_forwarded_with_it(bridge):
    _ui(bridge)
    await _agent(bridge)
    bridge.emitted.clear()
    ack = await _command(bridge, "settings_update", {"persona": "anna", "emotionalIntelligence": False})
    assert ack == {"status": "sent"}
    settings, meta = await _meta(bridge)
    assert settings == {"persona": "anna", "emotional_intelligence": False} and meta["revision"] == 1
    assert meta["delivered"] == 1
    [(command, to)] = _sent(bridge)
    assert to == "agent" and command == {"type": "settings_update", "payload": {
        "persona": "anna", "emotional_intelligence": False, "revision": 1, "revision_epoch": meta["epoch"]}}
    assert [skip for name, _d, _t, skip in bridge.emitted if name == "agent_command"] == ["sid-ui"]
    assert _sent(bridge, "settings_sync") == []  # the agent answers for itself
    [row] = await bridge.module.store.recent_audit(action_prefix="settings_update")
    assert json.loads(row["detail"]) == {"keys": ["emotional_intelligence", "persona"], "revision": 1,
                                         "via": "settings_update"}  # key names only, never a value (K-29)
    rows = [tuple(r) for r in _rows(bridge, "SELECT key, updated_by FROM settings ORDER BY key")]
    assert rows == [("emotional_intelligence", "ui"), ("persona", "ui")]


def _rows(ns: Any, sql: str) -> list[Any]:
    conn = sqlite3.connect(ns.path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


async def test_with_no_agent_a_settings_update_is_stored_queued_and_survives_a_restart(bridge):
    """The C-60 acceptance's first half: Persona ANNA + language Urdu while no agent is connected. The bridge stores
    them on top of the K-46 defaults, keeps the update for the agent, answers every UI with its own settings_sync (the
    stored values with pending {field: 'queued'}; the panel keeps the persona only once the UI honours that, Cursor
    request C-60), and after a bridge restart and a reload init_state and GET /settings still show them; the next
    agent gets the bootstrap first, then the queued update."""
    _ui(bridge)
    ack = await _command(bridge, "settings_update", {"persona": "anna", "language": "ur"})
    assert ack == {"status": "queued"}
    [(sync, to)] = _sent(bridge, "settings_sync")
    assert to == "ui" and sync["source"] == "bridge" and "applied" not in sync
    assert sync["pending"] == {"language": "queued", "persona": "queued"}
    assert (sync["settings"]["persona"], sync["settings"]["language"], sync["settings"]["voice_id"]) == (
        "anna", "ur", True)
    assert await bridge.module.store.count_pending() == 1

    await bridge.restart()
    bridge.emitted.clear()
    # the reloaded dashboard (a browser tab: never an agent peer, so the queue stays for the agent)
    assert await bridge.module.handle_connect("sid-reload", {"HTTP_ORIGIN": UI_ORIGIN}, {"token": LEGACY_TOKEN})
    [state] = [data for name, data, to, _skip in bridge.emitted if name == "init_state"]
    settings, meta = await _meta(bridge)
    assert state["settings"] == {"voice_id": True, "wake_word": True, "persona": "anna", "language": "ur",
                                 "revision": 1, "revision_epoch": meta["epoch"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.module.app),
                                 base_url=f"http://bridge.test:{INTERNAL_PORT}") as client:
        got = (await client.get("/settings")).json()
    assert got == {**state["settings"], "revisions": {"persona": {"rev": 1, "by": "ui"},
                                                      "language": {"rev": 1, "by": "ui"}}}

    bridge.emitted.clear()
    await _agent(bridge)
    commands = [command for command, to in _sent(bridge) if to == "sid-agent"]
    assert [command["type"] for command in commands] == ["settings_bootstrap", "settings_update"]
    assert commands[0]["payload"]["revisions"] == {"persona": {"rev": 1, "by": "ui"},
                                                   "language": {"rev": 1, "by": "ui"}}
    assert commands[1]["payload"] == {"persona": "anna", "language": "ur", "revision": 1,
                                      "revision_epoch": meta["epoch"]}
    assert settings == {"persona": "anna", "language": "ur"}
    assert await bridge.module.store.count_pending() == 0 and (await _meta(bridge))[1]["delivered"] == 1


async def test_junk_settings_update_is_refused_and_the_sender_gets_the_error(bridge):
    _ui(bridge)
    await _agent(bridge)
    bridge.emitted.clear()
    ack = await _command(bridge, "settings_update", {"persona": "bob"})
    assert ack == {"status": "rejected", "reason": "bad_value", "field": "persona"}
    [(sync, to)] = _sent(bridge, "settings_sync")
    assert to == "sid-ui" and sync["errors"] == {"persona": "persona must be one of: jarvis, anna"}
    assert "bob" not in json.dumps(sync)
    assert _sent(bridge) == [] and (await _meta(bridge)) [0] == {}
    ack = await _command(bridge, "settings_update", {"rm\nrf": 1})
    assert ack == {"status": "rejected", "reason": "unknown_setting", "field": "rmrf"}
    assert await _command(bridge, "settings_update", ["voice"]) == {"status": "rejected", "reason": "bad_payload"}
    assert (await _meta(bridge))[1]["revision"] == 0  # nothing stored, no revision taken


async def test_persona_change_and_wake_word_toggle_update_the_copy_the_same_way(bridge):
    _ui(bridge)
    await _agent(bridge)
    bridge.emitted.clear()
    assert await _command(bridge, "persona_change", "ANNA") == {"status": "sent"}
    assert await _command(bridge, "wake_word_toggle", None) == {"status": "sent"}  # no payload: the stored mode flips
    assert await _command(bridge, "wake_word_toggle", None) == {"status": "sent"}
    assert await _command(bridge, "wake_word_toggle", False) == {"status": "sent"}
    epoch = (await _meta(bridge))[1]["epoch"]
    assert [command for command, _to in _sent(bridge)] == [
        {"type": "settings_update", "payload": {"persona": "anna", "revision": 1, "revision_epoch": epoch}},
        {"type": "settings_update", "payload": {"wake_word": False, "revision": 2, "revision_epoch": epoch}},
        {"type": "settings_update", "payload": {"wake_word": True, "revision": 3, "revision_epoch": epoch}},
        {"type": "settings_update", "payload": {"wake_word": False, "revision": 4, "revision_epoch": epoch}}]
    assert (await _meta(bridge))[0] == {"persona": "anna", "wake_word": False}
    rows = await bridge.module.store.recent_audit(action_prefix="settings_update")
    assert sorted(json.loads(row["detail"])["via"] for row in rows) == [
        "persona_change", "wake_word_toggle", "wake_word_toggle", "wake_word_toggle"]
    # junk is refused as for settings_update; a toggle starts from the agent's last wake_word_sync when nothing is set
    assert (await _command(bridge, "persona_change", "bob"))["status"] == "rejected"
    assert (await _command(bridge, "wake_word_toggle", "off"))["status"] == "rejected"


async def test_a_toggle_starts_from_the_agents_last_wake_word_sync(bridge):
    _ui(bridge)
    await bridge.module.store.kv_set("wake_word_active", False)
    assert await _command(bridge, "wake_word_toggle") == {"status": "queued"}
    assert (await _meta(bridge))[0] == {"wake_word": True}


# ---------------------------------------------------------------- the agent's settings_sync against the owner's change


async def test_the_agents_sync_never_overwrites_a_newer_owner_change(bridge):
    _ui(bridge)
    await _command(bridge, "settings_update", {"persona": "anna"})  # no agent: stored at revision 1, queued
    epoch = (await _meta(bridge))[1]["epoch"]
    bridge.emitted.clear()
    # a job that has not taken it yet answers its bridge connect with its own state (a fresh agent: an empty echo)
    stale = {"settings": {"persona": "jarvis", "voice": "Charon", "saved": [], "updated_ts": 0.0},
             "applied": {}, "revision": 0, "revision_epoch": "", "revisions": {}, "instance": "agentA0001"}
    await _notify(bridge, "settings_sync", stale)
    settings, meta = await _meta(bridge)
    assert settings["persona"] == "anna" and settings["voice"] == "Charon"
    assert meta["keys"]["persona"] == {"rev": 1, "by": "ui", "src": ""}
    assert meta["keys"]["voice"] == {"rev": 2, "by": "agent", "src": "agentA0001"}
    [(relayed, to)] = _sent(bridge, "settings_sync")
    assert to == "ui" and relayed["settings"]["persona"] == "anna"  # the panel keeps the owner's choice
    # it took revision 1 and still says JARVIS (it refused or changed it since): now its word counts
    taken = dict(stale, settings={"persona": "jarvis"}, revision=1, revision_epoch=epoch, revisions={"persona": 1})
    await _notify(bridge, "settings_sync", taken)
    settings, meta = await _meta(bridge)
    assert settings["persona"] == "jarvis" and meta["keys"]["persona"] == {"rev": 3, "by": "agent",
                                                                          "src": "agentA0001"}


async def test_an_agent_without_the_echo_works_sensibly(bridge):
    """An agent built before C-60 sends no echo: the bridge holds only what it has not delivered to any agent yet; once
    sent (live, in a bootstrap or a queue flush), the older agent's answer is its state again."""
    _ui(bridge)
    await _command(bridge, "settings_update", {"voice": "Puck"})  # queued: never delivered
    await _notify(bridge, "settings_sync", {"settings": {"voice": "Charon", "persona": "jarvis"}})
    assert (await _meta(bridge))[0]["voice"] == "Puck"
    await _agent(bridge)  # the bootstrap and the queued update go out: delivered
    await _notify(bridge, "settings_sync", {"settings": {"voice": "Kore"}, "applied": {"revision": "unsupported"}})
    assert (await _meta(bridge))[0]["voice"] == "Kore"
    [relayed] = [data for data, _to in _sent(bridge, "settings_sync")][-1:]
    assert relayed["applied"] == {}  # the older agent's 'revision: unsupported' is never shown


class _Personas:
    def __init__(self, owner: "_Assistant") -> None:
        self.owner, self.is_anna = owner, False

    @property
    def active_persona_name(self) -> str:
        return "anna" if self.is_anna else "jarvis"

    async def set_persona(self, is_anna: bool, *, voice: str | None = None) -> None:
        self.is_anna = is_anna
        if voice:
            self.owner._session_options.voice_id = voice


class _Assistant:
    def __init__(self) -> None:
        self.persona_manager = _Personas(self)
        self._session_options = SimpleNamespace(voice_id="jarvis_v2")
        self.wake_word_mode, self.muted = True, False
        self.voice_id_engine = SimpleNamespace(is_enrolled=False)

    async def request_voice(self, voice: str) -> None:
        self._session_options.voice_id = voice

    def set_wake_word_mode(self, active: bool, *, refresh: bool = True) -> None:  # pylint: disable=unused-argument
        self.wake_word_mode = active

    def set_muted(self, active: bool) -> None:
        self.muted = active


async def test_clock_skew_between_two_agents_never_drops_a_change(bridge, tmp_path, monkeypatch):
    """K-46 v39 known gap, end to end: the PC brain's clock runs an hour ahead, the VPS brain's an hour behind. The
    owner's persona change reaches the VPS agent; back on the PC, its own last save looks newer than the copy, so the
    clock rule dropped the change. The bridge's revision is newer, so the PC agent takes it."""
    from src.core import agent_settings  # pylint: disable=import-outside-toplevel

    real_time = time.time
    skew = {"now": 0.0}
    monkeypatch.setattr(agent_settings, "time", SimpleNamespace(time=lambda: real_time() + skew["now"],
                                                                sleep=time.sleep))

    def agent(name: str) -> Any:
        path = tmp_path / f"{name}.json"

        async def notify(event_type: str, payload: Any) -> None:
            await _notify(bridge, event_type, json.loads(json.dumps(payload)))

        service = agent_settings.SettingsService(SimpleNamespace(assistant=_Assistant(), voice_link=None),
                                                 agent_settings.load(path), path=path, notify=notify)
        return SimpleNamespace(service=service, sid=f"sid-{name}", seen=0)

    def received(peer: Any) -> list[Any]:
        return [command for command, to in _sent(bridge) if to in (peer.sid, "agent")]

    async def connect(peer: Any, clock_offset: float) -> None:
        skew["now"] = clock_offset
        peer.seen = len(received(peer))  # what went to room 'agent' before it connected never reached it
        await _agent(bridge, peer.sid)

    async def pump(peer: Any) -> None:
        """The agent_commands this peer received since the last pump, through its settings service."""
        mine = received(peer)
        for command in mine[peer.seen:]:
            if command["type"] == "settings_bootstrap":
                await peer.service.bootstrap(command["payload"])
            elif command["type"] == "settings_update":
                await peer.service.update(command["payload"])
        peer.seen = len(mine)

    pc, vps = agent("pc"), agent("vps")
    _ui(bridge)
    await connect(pc, 3600.0)
    await _command(bridge, "settings_update", {"voice": "Puck"})
    await pump(pc)
    await bridge.module.handle_disconnect(pc.sid)

    await connect(vps, -3600.0)  # the brain switch: the VPS agent connects
    await pump(vps)
    assert vps.service.get("voice") == "Puck"  # taken from the bootstrap by its revision
    await _command(bridge, "settings_update", {"persona": "anna"})
    await pump(vps)
    assert vps.service.get("persona") == "anna"
    await bridge.module.handle_disconnect(vps.sid)

    await connect(pc, 3600.0)  # back to the PC brain
    [bootstrap] = [command["payload"] for command, to in _sent(bridge)
                   if to == pc.sid and command["type"] == "settings_bootstrap"][-1:]
    assert bootstrap["updated_ts"] < pc.service.settings.updated_ts  # the copy looks older to the PC's clock
    assert pc.service._clock_fields(bootstrap, quiet=True) == {}  # so the clock rule alone would drop it
    await pump(pc)
    assert pc.service.get("persona") == "anna"
    assert (await _meta(bridge))[0]["persona"] == "anna"


RESTORED_EPOCH = "c60database01"


async def _restore_backup(ns: Any, persona: str) -> None:
    """bridge.db as a backup left it (backup_vps.sh, a volume snapshot): the same epoch, revision 50, persona set by
    the owner at revision 40. The revisions 51..80 the agent took afterwards are gone with the restore."""
    await ns.module.store.merge_settings({"persona": persona}, by="ui")
    await ns.module.store.kv_set("settings_revision", {"epoch": RESTORED_EPOCH, "revision": 50, "delivered": 50,
                                                       "keys": {"persona": {"rev": 40, "by": "ui", "src": ""}}})


def test_an_echo_above_the_bridges_newest_revision_catches_the_counter_up():
    """C-60 (K-46 v51), the Lamport catch-up: an echo of this database that names a revision above the bridge's newest
    (bridge.db went back: restored or copied) raises the counter to it first, so the agent's changes and every later
    change take revisions above anything it has seen; the conflict rule is unchanged (an owner change this agent never
    took is kept). An echo of another epoch, one within the counter, or none at all raises nothing."""
    meta = {"epoch": "e1", "revision": 51, "delivered": 51,
            "keys": {"persona": {"rev": 51, "by": "ui", "src": ""}, "voice": {"rev": 40, "by": "ui", "src": ""}}}
    current = {"persona": "jarvis", "voice": "Puck"}
    echo = Echo("per_key", "e1", 80, {"voice": 80, "persona": 30}, "agentA")
    assert bridge_settings.caught_up(meta, echo) == 80
    writes, new_meta, _by, kept = bridge_settings.agent_merge(current, meta, {"persona": "anna", "voice": "Kore"}, echo)
    assert kept == {"persona": "jarvis"} and writes == {"voice": "Kore"}  # it took persona at 30, never the owner's 51
    assert new_meta["revision"] == 81 and new_meta["keys"]["voice"] == {"rev": 81, "by": "agent", "src": "agentA"}
    assert new_meta["epoch"] == "e1" and new_meta["keys"]["persona"] == meta["keys"]["persona"]
    same = bridge_settings.agent_merge(current, meta, {"persona": "jarvis"}, echo)  # nothing to write: still raised
    assert same[0] == {} and same[1]["revision"] == 80 and same[1]["keys"] == meta["keys"]
    assert bridge_settings.caught_up(meta, Echo("single", "e1", 90)) == 90
    for other in (Echo("per_key", "e0", 99, {"persona": 99}), Echo(), Echo("per_key", "e1", 51, {"persona": 51}),
                  Echo("single", "", 99)):
        assert bridge_settings.caught_up(meta, other) == 51
        assert bridge_settings.agent_merge(current, meta, {"persona": "jarvis"}, other)[1]["revision"] == 51
    # a revision past MAX_REVISION (2**53 - 1) is no revision of this bridge: never read, never raised to
    assert bridge_settings.parse_echo({"revision_epoch": "e1", "revisions": {"voice": 2**53}}).revisions == {}
    assert bridge_settings.parse_echo({"revision_epoch": "e1", "revision": 2**53}) == Echo()
    assert "revision_base" not in bridge_settings.bootstrap_payload(current, meta)


async def test_an_echo_past_the_bridges_newest_revision_catches_the_counter_up_once(bridge, records):
    """C-60 (K-46 v51): bridge.db restored from a backup keeps its epoch while its counter went back (50) below what
    the agent took (persona JARVIS at 80: the owner's change it applied before the restore). Its settings_sync echoes
    persona 80: the bridge raises its counter to 80 first (WARNING, stored in the same transaction), takes the agent's
    value (newer than the restored copy's persona at 40) at 81 and keeps the epoch. The owner's next change gets 82,
    above anything the agent has seen, and the same stale echo arriving again raises nothing and changes nothing."""
    await _restore_backup(bridge, "anna")
    _ui(bridge)
    await _agent(bridge)
    bridge.emitted.clear()
    stale = {"settings": {"persona": "jarvis"}, "applied": {}, "revision": 80, "revision_epoch": RESTORED_EPOCH,
             "revisions": {"persona": 80}, "instance": "c60agentpc001"}
    await _notify(bridge, "settings_sync", stale)
    settings, meta = await _meta(bridge)
    assert settings["persona"] == "jarvis"
    assert meta["keys"]["persona"] == {"rev": 81, "by": "agent", "src": "c60agentpc001"}
    assert (meta["epoch"], meta["revision"]) == (RESTORED_EPOCH, 81)
    assert records.count("SETTINGS revision caught up from 50 to 80", logging.WARNING) == 1
    [(relayed, _to)] = _sent(bridge, "settings_sync")
    assert relayed["settings"]["persona"] == "jarvis"
    bridge.emitted.clear()
    assert await _command(bridge, "settings_update", {"persona": "anna"}) == {"status": "sent"}
    [(command, _to)] = _sent(bridge)
    assert (command["payload"]["revision"], command["payload"]["revision_epoch"]) == (82, RESTORED_EPOCH)
    await _notify(bridge, "settings_sync", stale)  # the same stale echo again (it waited in a retry queue)
    settings, again = await _meta(bridge)
    assert settings["persona"] == "anna" and again["revision"] == 82  # the owner's newer change is kept
    assert records.count("SETTINGS revision caught up", logging.WARNING) == 1


async def test_a_restored_bridge_db_catches_up_and_the_owners_next_change_outranks_the_agent(bridge, tmp_path):
    """C-60 (K-46/K-61 v51), end to end with a real settings service: bridge.db is restored from a backup (revision
    50, persona JARVIS set by the owner at 40) while the agent took the owner's ANNA at revision 80. The agent keeps its
    record (the restored persona is older than what it took) and its settings_sync at connect echoes revision 80: the
    bridge catches its counter up and takes the agent's newer ANNA back into the copy. Every later owner change gets a
    revision above 80, so the agent takes it instead of skipping it as taken already, and the copy keeps it."""
    from src.core import agent_settings  # pylint: disable=import-outside-toplevel
    await _restore_backup(bridge, "jarvis")
    path = tmp_path / "agent.json"
    agent_settings.save({"persona": "anna"}, "ui", path, bridge={"epoch": RESTORED_EPOCH, "revs": {"persona": 80}},
                        instance="c60agentpc001")  # what it took before the restore
    assistant = _Assistant()
    assistant.persona_manager.is_anna = True
    service, pump = _agent_service(bridge, path, assistant)
    _ui(bridge)
    await _agent(bridge)
    await pump()  # the bootstrap of revision 50: its persona (40) is older than the record (80), so nothing is taken
    assert service.get("persona") == "anna" and service.settings.bridge_revs == {"persona": 80}
    await service.sync("bridge_connect")  # the UIBridgeListener's settings_sync at connect
    settings, meta = await _meta(bridge)
    assert settings["persona"] == "anna" and meta["revision"] == 81 and meta["epoch"] == RESTORED_EPOCH
    for persona in ("jarvis", "anna"):
        assert await _command(bridge, "settings_update", {"persona": persona}) == {"status": "sent"}
        await pump()
        await service.sync("request_state")
        settings, meta = await _meta(bridge)
        stamp = meta["keys"]["persona"]
        assert service.get("persona") == persona and settings["persona"] == persona
        assert stamp["by"] == "ui" and stamp["rev"] > 80 and service.settings.bridge_revs["persona"] == stamp["rev"]
    assert meta["epoch"] == RESTORED_EPOCH == service.settings.bridge_epoch


async def test_a_queued_update_from_before_a_restore_is_handled_by_revision_order(bridge, tmp_path):
    """C-60 (K-61 v51): the restored backup still holds two queued settings_update rows (voice Puck at revision 45,
    wake_word off at 48); before the restore the agent took a newer voice (Kore at 70) but never a wake_word. The rows
    keep their revision and epoch (no row is ever moved). After the bootstrap the queued voice is left out (older than
    the record) and the wake_word is taken once (its bootstrap copy is newer than the record, the queued copy then
    taken already); the agent's settings_sync catches the counter up, its Kore lands in the copy, and the owner's next
    change outranks it."""
    from src.core import agent_settings  # pylint: disable=import-outside-toplevel
    module = bridge.module
    await module.store.merge_settings({"voice": "Puck", "wake_word": False}, by="ui")
    await module.store.kv_set("settings_revision", {"epoch": RESTORED_EPOCH, "revision": 50, "delivered": 44,
                                                    "keys": {"voice": {"rev": 45, "by": "ui", "src": ""},
                                                             "wake_word": {"rev": 48, "by": "ui", "src": ""}}})
    for fields, revision in (({"voice": "Puck"}, 45), ({"wake_word": False}, 48)):
        await module.store.queue_command("settings_update", {**fields, "revision": revision,
                                                             "revision_epoch": RESTORED_EPOCH}, ttl_s=600, cap=100)
    path = tmp_path / "agent.json"
    agent_settings.save({"voice": "Kore"}, "ui", path, bridge={"epoch": RESTORED_EPOCH, "revs": {"voice": 70}},
                        instance="c60agentpc001")  # what it took before the restore
    assistant = _Assistant()
    assistant._session_options.voice_id = "Kore"  # the job runs with it
    service, pump = _agent_service(bridge, path, assistant)
    _ui(bridge)
    await _agent(bridge)
    assert [(command["type"], command["payload"].get("revision")) for command, to in _sent(bridge)
            if to == "sid-agent"] == [("settings_bootstrap", 50), ("settings_update", 45), ("settings_update", 48)]
    await pump()
    assert service.get("voice") == "Kore" and assistant._session_options.voice_id == "Kore"  # never back to Puck
    assert service.get("wake_word") is False and assistant.wake_word_mode is False
    assert (service.settings.bridge_epoch, service.settings.bridge_revs) == (RESTORED_EPOCH,
                                                                             {"voice": 70, "wake_word": 48})
    await service.sync("bridge_connect")
    settings, meta = await _meta(bridge)
    assert settings["voice"] == "Kore" and meta["keys"]["voice"] == {"rev": 71, "by": "agent", "src": "c60agentpc001"}
    assert settings["wake_word"] is False and meta["keys"]["wake_word"] == {"rev": 48, "by": "ui", "src": ""}
    assert meta["epoch"] == RESTORED_EPOCH and await module.store.count_pending() == 0
    assert await _command(bridge, "settings_update", {"voice": "Leda"}) == {"status": "sent"}
    await pump()
    assert service.get("voice") == "Leda" and service.settings.bridge_revs["voice"] > 71


def _agent_service(ns: Any, path: Any, assistant: Any, notify: Any = None) -> tuple[Any, Any]:
    """A real agent settings service on its own settings file whose settings_sync reaches this bridge (or goes to
    notify), and a pump that feeds it the agent_commands sent to 'sid-agent' or room 'agent' since its last pump."""
    from src.core import agent_settings  # pylint: disable=import-outside-toplevel

    async def to_bridge(event_type: str, payload: Any) -> None:
        await _notify(ns, event_type, json.loads(json.dumps(payload)))

    service = agent_settings.SettingsService(SimpleNamespace(assistant=assistant, voice_link=None),
                                             agent_settings.load(path), path=path, notify=notify or to_bridge)
    seen = [0]

    async def pump() -> None:
        mine = [command for command, to in _sent(ns) if to in ("sid-agent", "agent")]
        for command in mine[seen[0]:]:
            if command["type"] == "settings_bootstrap":
                await service.bootstrap(command["payload"])
            elif command["type"] == "settings_update":
                await service.update(command["payload"])
        seen[0] = len(mine)

    return service, pump


@pytest.mark.parametrize("sync_first", [True, False], ids=["sync-before-bootstrap", "bootstrap-before-sync"])
async def test_any_number_of_bridge_restarts_change_nothing(bridge, tmp_path, sync_first):
    """C-60 (K-46/K-61 v51): the epoch belongs to bridge.db and the counter only grows, so bridge restarts (three here)
    change nothing: the same epoch, revision and per-key stamps, and the agent's record goes on. A voice the agent
    switched itself while the bridge was down (its settings_sync waited in the retry queue) is never undone by the
    bootstrap after the restarts, and the bridge takes that sync whether it arrives before or after the bootstrap."""
    held: list[tuple[str, Any]] = []
    bridge_down = [False]

    async def notify(event_type: str, payload: Any) -> None:
        payload = json.loads(json.dumps(payload))
        if bridge_down[0]:
            held.append((event_type, payload))  # jarvis_bridge's retry queue keeps a critical type for later
        else:
            await _notify(bridge, event_type, payload)

    async def flush_retry_queue() -> None:
        for event_type, payload in held:
            await _notify(bridge, event_type, payload)
        held.clear()

    assistant = _Assistant()
    service, pump = _agent_service(bridge, tmp_path / "agent.json", assistant, notify)
    _ui(bridge)
    await _agent(bridge)
    assert await _command(bridge, "settings_update", {"voice": "Puck"}) == {"status": "sent"}
    await pump()
    stored_before, before = await _meta(bridge)
    assert service.get("voice") == "Puck" and stored_before["voice"] == "Puck"
    bridge_down[0] = True
    for _restart in range(3):
        await bridge.restart()
        assert await _meta(bridge) == (stored_before, before)
    assistant._session_options.voice_id = "Kore"  # the agent's own switch (change_voice) while the bridge is down
    await service._save_live_change("change_voice")
    assert [event for event, _payload in held] == ["settings_sync"]
    bridge_down[0] = False
    _ui(bridge)
    if sync_first:
        await flush_retry_queue()
    await _agent(bridge)
    await pump()
    if not sync_first:
        await flush_retry_queue()
    await service.sync("bridge_connect")
    settings, meta = await _meta(bridge)
    assert service.get("voice") == "Kore" and assistant._session_options.voice_id == "Kore"
    assert settings["voice"] == "Kore"
    assert meta["keys"]["voice"]["by"] == "agent" and meta["keys"]["voice"]["src"] == service.settings.instance
    assert meta["epoch"] == before["epoch"] == service.settings.bridge_epoch and meta["revision"] > before["revision"]
    assert service.settings.bridge_revs["voice"] >= before["keys"]["voice"]["rev"]  # its record went on, never reset


async def test_an_agent_with_a_record_of_another_epoch_follows_the_bridge_copy_and_its_own_change_still_wins(
        bridge, tmp_path):
    """C-60 (K-46/K-61 v51): the agent's record names another epoch (it last worked with another or a recreated bridge
    database, whose revisions were far higher). The bootstrap of this database is followed for every field it has a
    revision for (the owner's persona and voice, never skipped as 'older' than the other database's numbers), the
    record starts again with this epoch and its revisions, the queued copy of the same change is then left out, and the
    agent's own later change (a voice it switches itself) lands in the copy; the owner's next change is taken again."""
    from src.core import agent_settings  # pylint: disable=import-outside-toplevel
    _ui(bridge)
    assert await _command(bridge, "settings_update", {"persona": "anna", "voice": "Puck"}) == {"status": "queued"}
    path = tmp_path / "agent.json"
    agent_settings.save({"persona": "jarvis", "voice": "Kore"}, "ui", path, instance="c60agentpc001",
                        bridge={"epoch": "c60otherdb001", "revs": {"persona": 70, "voice": 90}})
    assistant = _Assistant()
    assistant._session_options.voice_id = "Kore"
    service, pump = _agent_service(bridge, path, assistant)
    await _agent(bridge)
    await pump()
    epoch = (await _meta(bridge))[1]["epoch"]
    assert (service.get("persona"), service.get("voice")) == ("anna", "Puck")
    assert assistant.persona_manager.is_anna and assistant._session_options.voice_id == "Puck"
    assert (service.settings.bridge_epoch, service.settings.bridge_revs) == (epoch, {"persona": 1, "voice": 1})
    await service.sync("bridge_connect")
    assistant._session_options.voice_id = "Leda"  # the agent switches the voice itself (change_voice)
    await service._save_live_change("change_voice")
    settings, meta = await _meta(bridge)
    assert settings["voice"] == "Leda" and meta["epoch"] == epoch
    assert meta["keys"]["voice"]["by"] == "agent" and meta["keys"]["voice"]["src"] == "c60agentpc001"
    assert await _command(bridge, "settings_update", {"persona": "jarvis"}) == {"status": "sent"}
    await pump()
    assert service.get("persona") == "jarvis" and service.settings.bridge_revs["persona"] == meta["revision"] + 1


async def test_a_legacy_token_agent_gets_the_settings_bootstrap_too(bridge):
    """C-60 review: an agent build without the agent link v2 keys falls back to the legacy token (declaring the agent
    role beside it). It never got a settings_bootstrap, so an owner change whose queued copy expired never reached it,
    and its syncs left the owner's value in the bridge's copy (and on every UI) as if in force while it ran its own.
    Now it gets the bootstrap like an owner-room agent; an older agent's answer after it is then its real state."""
    module = bridge.module
    _ui(bridge)
    assert await _command(bridge, "settings_update", {"persona": "anna"}) == {"status": "queued"}
    bridge.clock.now += module.PENDING_TTL_S + 1  # the queued copy expires
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    epoch = (await _meta(bridge))[1]["epoch"]
    assert [command for command, to in _sent(bridge) if to == "sid-old-agent"] == [
        {"type": "settings_bootstrap", "payload": {"persona": "anna", "revision": 1, "revision_epoch": epoch,
                                                   "revisions": {"persona": {"rev": 1, "by": "ui"}}}}]
    assert (await _meta(bridge))[1]["delivered"] == 1
    bridge.emitted.clear()
    await _notify(bridge, "settings_sync", {"settings": {"persona": "jarvis"}})  # an agent without the echo
    assert (await _meta(bridge))[0]["persona"] == "jarvis"
    [(relayed, _to)] = _sent(bridge, "settings_sync")
    assert relayed["settings"]["persona"] == "jarvis"  # what it runs, never the owner's value as if in force


# ---------------------------------------------------------------- GET /settings


def _signed(raw: bytes = b"") -> dict[str, str]:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    return {"X-Agent-Token": AGENT_TOKEN, "X-Agent-Timestamp": ts, "X-Agent-Nonce": nonce,
            "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, raw)}


async def test_get_settings_answers_the_owner_session_or_agent_credentials_only(bridge):
    await bridge.module.store.merge_settings({"voice": "Puck"}, by="agent")
    token, _record = await bridge.module.owner_auth.mint_session(label="c60-test")
    transport = httpx.ASGITransport(app=bridge.module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as public, \
            httpx.AsyncClient(transport=transport, base_url=f"http://bridge.test:{INTERNAL_PORT}") as internal:
        nobody = await public.get("/settings")
        legacy = await public.get("/settings", headers={"X-Vortex-Token": LEGACY_TOKEN})
        forged = await public.get("/settings", headers={**_signed(), "X-Agent-Signature": "0" * 64})
        session = await public.get("/settings", headers={"Cookie": f"jarvis_session={token}"})
        expired = await public.get("/settings", headers={"Cookie": "jarvis_session=x.y.z"})
        headers = _signed()
        agent = await public.get("/settings", headers=headers)
        replay = await public.get("/settings", headers=headers)
        on_internal = await internal.get("/settings")
    for refused in (nobody, legacy, forged, expired, replay):
        assert (refused.status_code, refused.json()) == (401, {"error": "auth_required"})
    epoch = (await _meta(bridge))[1]["epoch"]
    expected = {"voice_id": True, "wake_word": True, "persona": "jarvis", "voice": "Puck", "revision": 0,
                "revision_epoch": epoch, "revisions": {}}
    for ok in (session, agent, on_internal):
        assert ok.status_code == 200 and ok.json() == expected


# ---------------------------------------------------------------- the offline queue


async def test_queued_commands_reach_the_next_agent_in_order_after_its_bootstrap(bridge):
    _ui(bridge)
    first = {"id": "u-1", "text": "salam"}
    assert await _command(bridge, "chat", first) == {"status": "queued"}
    assert await _command(bridge, "settings_update", {"voice": "Puck"}) == {"status": "queued"}
    assert await _command(bridge, "chat", {"id": "u-2", "text": "kya haal hai"}) == {"status": "queued"}
    assert await _command(bridge, "clear_memory", {"include_vector": False}) == {"status": "queued"}
    assert _sent(bridge) == []
    await _agent(bridge)
    sent = [command for command, to in _sent(bridge) if to == "sid-agent"]
    assert [(c["type"], (c["payload"] or {}).get("id")) for c in sent] == [
        ("settings_bootstrap", None), ("chat", "u-1"), ("settings_update", None), ("chat", "u-2"),
        ("clear_memory", None)]
    assert sent[1]["payload"] == first and await bridge.module.store.count_pending() == 0
    bridge.emitted.clear()  # the agent is ready: the next one goes live
    assert await _command(bridge, "chat", {"id": "u-3", "text": "aur?"}) == {"status": "sent"}
    assert [to for _c, to in _sent(bridge)] == ["agent"]


async def test_queued_commands_expire_after_10_minutes(bridge, records):
    _ui(bridge)
    await _command(bridge, "chat", {"id": "u-old", "text": "purana"})
    bridge.clock.now += bridge.module.PENDING_TTL_S - 1
    await _command(bridge, "chat", {"id": "u-new", "text": "naya"})
    bridge.clock.now += 2  # the first is 601 s old, the second 2 s
    await _agent(bridge)
    assert [command["payload"]["id"] for command, to in _sent(bridge) if to == "sid-agent"] == ["u-new"]
    assert records.count("1 queued command(s) expired") == 1
    assert records.count("purana") == records.count("naya") == 0  # message text is never logged (K-29)


async def test_a_chat_already_queued_is_not_queued_twice(bridge):
    _ui(bridge)
    chat = {"id": "u-1", "text": "salam"}
    assert await _command(bridge, "chat", chat) == {"status": "queued"}
    assert await _command(bridge, "chat", chat) == {"status": "queued"}  # the UI's retry of the same row
    assert await _command(bridge, "chat", "legacy string") == {"status": "queued"}  # no id: never deduped
    assert await _command(bridge, "chat", "legacy string") == {"status": "queued"}
    assert await bridge.module.store.count_pending() == 3
    flood = {"id": "u-big", "text": "x" * (bridge.module.PENDING_PAYLOAD_MAX_BYTES + 1)}
    assert await _command(bridge, "chat", flood) == {"status": "rejected", "reason": "too_large"}
    assert await bridge.module.store.count_pending() == 3


async def test_a_full_queue_drops_the_oldest_with_a_warning(bridge, monkeypatch, records):
    monkeypatch.setattr(bridge.module, "PENDING_MAX", 3)
    _ui(bridge)
    for n in range(5):
        assert await _command(bridge, "chat", {"id": f"u-{n}", "text": "x"}) == {"status": "queued"}
    assert records.count("QUEUE full", logging.WARNING) == 2
    await _agent(bridge)
    assert [command["payload"]["id"] for command, to in _sent(bridge) if to == "sid-agent"] == ["u-2", "u-3", "u-4"]


async def test_the_queue_survives_a_bridge_restart_and_the_echo_resolves_the_queued_row(bridge):
    """A typed message with the agent stopped is 'queued'; the bridge restarts; the agent connects, gets it and echoes
    the user message with the same id (K-41), which the bridge stores as a typed message (source 'text')."""
    _ui(bridge)
    assert await _command(bridge, "chat", {"id": "u-42", "text": "salam"}) == {"status": "queued"}
    await bridge.restart()
    await _agent(bridge)
    assert [command for command, to in _sent(bridge) if to == "sid-agent"] == [
        {"type": "chat", "payload": {"id": "u-42", "text": "salam"}}]
    bridge.emitted.clear()
    await _notify(bridge, "transcription", {"id": "u-42", "role": "user", "text": "salam", "final": True})
    [(message, to)] = _sent(bridge, "new_message")
    assert to == "ui" and (message["id"], message["role"], message["source"]) == ("u-42", "user", "text")


async def test_a_paused_room_still_queues_and_a_job_kept_out_gets_nothing(owner):
    manager = owner.make_owner()
    await manager.set_paused(True, by="ui")
    _ui(owner)
    manager._take_kick()
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    assert manager._take_kick() is None  # nothing is dispatched for it while paused
    await _agent(owner, "sid-paused")  # a job that starts while paused: only pause_agent
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel
    assert [command for command, to in _sent(owner) if to == "sid-paused"] == [dict(bridge_rooms.PAUSE_COMMAND)]
    assert await _command(owner, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}  # still kept out
    assert await _command(owner, "stop", {}) == {"status": "agent_offline"}
    assert await owner.module.store.count_pending() == 2
    # Wake: the job that connected while paused is ending, so it gets none of it; the next job gets both, in order
    assert await _command(owner, "start", {}) == {"status": "sent"}
    assert [c["type"] for c, to in _sent(owner) if to == "sid-paused"] == ["pause_agent"]
    await owner.module.handle_disconnect("sid-paused")
    await _agent(owner, "sid-next")
    assert [c["payload"]["id"] for c, to in _sent(owner) if to == "sid-next"] == ["u-1", "u-2"]


async def test_after_wake_a_legacy_agent_connected_during_the_pause_gets_the_queue(owner):
    manager = owner.make_owner()
    await manager.set_paused(True, by="ui")
    _ui(owner)
    await _command(owner, "chat", {"id": "u-1", "text": "one"})
    assert await owner.module.handle_connect("sid-old-agent", {}, dict(LEGACY_AGENT_AUTH)) is True
    assert owner.module._clients["sid-old-agent"].ready is False
    assert await _command(owner, "start", {}) == {"status": "sent"}
    assert [c for c, to in _sent(owner) if to == "sid-old-agent" and c["type"] == "chat"] == [
        {"type": "chat", "payload": {"id": "u-1", "text": "one"}}]
    assert owner.module._clients["sid-old-agent"].ready is True


async def test_wake_never_hands_the_queue_to_the_job_the_pause_ended(owner, records):
    """C-60 review: job A is connected and ready; the owner presses Pause (the bridge's pause_agent ends A's job,
    K-60 v9), types a chat and clears the memory (both 'queued') and presses Wake while A's socket is still connected
    (its teardown, or up to the ping window when its event loop is blocked). Neither goes to A, and A takes no live
    command either ('queued', never a 'sent' that nothing answers): they reach the next job at its connect, in order."""
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await _agent(owner, "sid-job-a")
    owner.emitted.clear()
    await manager.set_paused(True, by="ui")
    assert _sent(owner) == [(dict(bridge_rooms.PAUSE_COMMAND), "agent")]
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    assert await _command(owner, "clear_memory", {"include_vector": False}) == {"status": "queued"}
    assert await _command(owner, "start", {}) == {"status": "sent"}  # Wake: the bridge did it; A is still there
    assert [(command["type"], to) for command, to in _sent(owner)] == [("pause_agent", "agent")]  # none to A
    assert await module.store.count_pending() == 2
    assert await _command(owner, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}
    job_a = module._clients["sid-job-a"]
    assert job_a.ending and not job_a.ready
    assert records.count("PAUSE 1 agent socket(s) told to end") == 1
    assert await module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "agent_offline"}
    await module.handle_disconnect("sid-job-a")  # A's teardown ends
    await _agent(owner, "sid-job-b")
    assert [(c["type"], (c["payload"] or {}).get("id")) for c, to in _sent(owner) if to == "sid-job-b"] == [
        ("chat", "u-1"), ("clear_memory", None), ("chat", "u-2")]
    assert await module.store.count_pending() == 0
    assert await _command(owner, "chat", {"id": "u-3", "text": "three"}) == {"status": "sent"}


@pytest.mark.parametrize("path", ["admin_cli_pause", "observe_to_always"])
async def test_the_other_room_wide_pauses_end_the_connected_job_the_same_way(owner, path):
    """C-60 review: the admin CLI's pause (read from kv) and a mode that leaves observe while paused send the same
    pause_agent as the dashboard's Pause, so the job they reach counts as ending too: what waits at Wake goes to the
    next job, never to it."""
    from src.core import bridge_rooms, bridge_store  # pylint: disable=import-outside-toplevel
    manager = owner.make_owner(env_mode="observe" if path == "observe_to_always" else None)
    module = owner.module
    _ui(owner)
    if path == "observe_to_always":
        await manager.set_paused(True, by="admin_cli", notify_agents=False)  # kept and reported while observing
    await _agent(owner, "sid-job-a")  # not kept out (observe, or no pause yet): ready
    await manager._poll_kv()  # the running owner's first kv read
    if path == "admin_cli_pause":
        await module.store.kv_set(bridge_store.KV_PAUSED, True)  # python -m src.core.bridge_rooms pause
    else:
        await module.store.kv_set(bridge_store.KV_ROOM_MODE, "always")  # python -m src.core.bridge_rooms mode always
    owner.emitted.clear()
    await manager._poll_kv()  # within 5 s the running owner applies it
    assert _sent(owner) == [(dict(bridge_rooms.PAUSE_COMMAND), "agent")]
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    assert await _command(owner, "start", {}) == {"status": "sent"}  # Wake while job A's socket is still there
    assert [(c["type"], to) for c, to in _sent(owner)] == [("pause_agent", "agent")]
    assert module._clients["sid-job-a"].ending
    await module.handle_disconnect("sid-job-a")
    await _agent(owner, "sid-job-b")
    assert [c["payload"]["id"] for c, to in _sent(owner) if to == "sid-job-b" and c["type"] == "chat"] == ["u-1"]


async def test_wake_readies_a_legacy_agent_that_connected_during_the_pause_beside_an_ending_job(owner):
    """C-60 review: the job the pause ended is still connected at Wake. The legacy agent that connected during the
    pause (never told to end) takes commands again and gets the queue; the ending job gets neither."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await _agent(owner, "sid-job-a")
    await manager.set_paused(True, by="ui")
    assert await module.handle_connect("sid-old-agent", {}, dict(LEGACY_AGENT_AUTH)) is True
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    owner.emitted.clear()
    assert await _command(owner, "start", {}) == {"status": "sent"}
    assert [(c["type"], to) for c, to in _sent(owner)] == [("chat", "sid-old-agent"), ("start", "agent")]
    old_agent, job_a = module._clients["sid-old-agent"], module._clients["sid-job-a"]
    assert old_agent.ready and not old_agent.ending and not job_a.ready and job_a.ending
    assert await _command(owner, "chat", {"id": "u-2", "text": "two"}) == {"status": "sent"}


async def test_a_pause_never_lands_in_the_middle_of_a_connects_queue_flush(owner, monkeypatch):
    """C-60 review: the room owner's pause_agent takes the send lock. The owner presses Pause while a new job's queue
    flush is going out: the pause_agent follows the flushed commands, and the job counts as ending once the flush is
    done (before, its flush marked it ready after it had been told to end, so Wake and live commands went to it)."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    take_pending, send_agent_command = module.store.take_pending, module._send_agent_command
    flushing, release, pause_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_take() -> Any:
        flushing.set()
        await release.wait()
        return await take_pending()

    async def watched_send(data: Any, sid: Any = None) -> None:
        pause_sent.set()  # the room owner is sending its pause_agent now
        await send_agent_command(data, sid)

    monkeypatch.setattr(module.store, "take_pending", slow_take)
    monkeypatch.setattr(module, "_send_agent_command", watched_send)
    connect = asyncio.create_task(_agent(owner, "sid-joining"))
    await asyncio.wait_for(flushing.wait(), 5)  # the connect holds the send lock, its flush in flight
    pause = asyncio.create_task(manager.set_paused(True, by="ui"))
    await asyncio.wait_for(pause_sent.wait(), 5)
    for _ in range(10):
        await asyncio.sleep(0)
    assert [command for command, _to in _sent(owner) if command["type"] == "pause_agent"] == []  # waits its turn
    release.set()
    await asyncio.wait_for(asyncio.gather(connect, pause), 5)
    assert [(c["type"], to) for c, to in _sent(owner)] == [("chat", "sid-joining"), ("pause_agent", "agent")]
    joining = module._clients["sid-joining"]
    assert joining.ending and not joining.ready
    monkeypatch.setattr(module.store, "take_pending", take_pending)
    assert await _command(owner, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}
    assert await _command(owner, "start", {}) == {"status": "sent"}
    assert [c["type"] for c, to in _sent(owner) if to == "sid-joining"] == ["chat"]  # nothing more to the ending job
    assert await module.store.count_pending() == 1


async def test_observe_mode_never_keeps_the_agent_out(owner):
    manager = owner.make_owner(env_mode="observe")
    await manager.set_paused(True, by="cli", notify_agents=False)  # the admin CLI's pause, kept and reported
    _ui(owner)
    await _agent(owner)
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "sent"}


async def test_an_agent_that_is_still_connecting_gets_no_live_command_before_the_queue(bridge):
    _ui(bridge)
    module = bridge.module
    module._clients["sid-joining"] = client = module.SocketClient(role="agent", ip="10.0.0.2", room=ROOM, ready=False)
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    async with module._send_lock():
        await module._start_agent_peer("sid-joining", client)
    assert client.ready and [c["payload"]["id"] for c, to in _sent(bridge) if to == "sid-joining"] == ["u-1"]
    assert await _command(bridge, "chat", {"id": "u-2", "text": "two"}) == {"status": "sent"}


async def test_other_types_are_answered_agent_offline_and_bridge_commands_still_sent(bridge):
    _ui(bridge)
    bridge.module._clients["sid-ui"].commands = TokenBucket(1000.0)  # more than 10 commands in this second
    for cmd_type in ("stop", "mute", "unmute", "swarm", "shutdown_agent", "task_cancel", "feedback", "job_cancel",
                     "voice_take_over", "start"):  # start: the room owner is off here (the PC's local bridge)
        assert await _command(bridge, cmd_type, {}) == {"status": "agent_offline"}, cmd_type
    assert await _command(bridge, "sim_lookup", "03001234567") == {"status": "agent_offline"}
    await bridge.module.store.add_notification({"id": "rem-1", "description": "chai"})
    assert await _command(bridge, "notification_ack", {"id": "rem-1"}) == {"status": "sent"}  # the bridge did it
    assert (await bridge.module.store.get_notification("rem-1"))["acked_by"] == "ui"
    assert _sent(bridge) == [] and await bridge.module.store.count_pending() == 0
    # request_state: with no agent the bridge answers with its own copy
    assert await _command(bridge, "request_state", {}) == {"status": "agent_offline"}
    [(sync, to)] = _sent(bridge, "settings_sync")
    assert to == "sid-ui" and sync["source"] == "bridge" and sync["settings"]["persona"] == "jarvis"


def _hold(ns: Any, monkeypatch: Any, name: str, started: asyncio.Event, release: asyncio.Event) -> None:
    """The store call `name` waits (started set) until release is set, then runs; later calls pass straight through."""
    real = getattr(ns.module.store, name)

    async def held(*args: Any, **kwargs: Any) -> Any:
        started.set()
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(ns.module.store, name, held)


AGENT_AUTH = {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "abc123"}


@pytest.mark.parametrize("moment", ["waiting_for_the_send_lock", "reading_the_settings", "taking_the_queue"])
async def test_an_agent_socket_that_leaves_during_its_connect_keeps_nothing_and_the_queue_waits(
        bridge, monkeypatch, moment):
    """C-60 review: python-socketio runs a socket's disconnect handler while its connect handler is still suspended:
    while the connect waits for the send lock (another peer's bootstrap or flush, a settings write, a pause), or while
    its bootstrap or queue flush waits on bridge.db. A socket that leaves then (the client's wait_timeout, a network
    drop, the job ending) never stays in the table as an agent that takes commands (agent_status online, every later
    command 'sent' and dropped), gets nothing more, counts no settings delivery, and the whole queue stays, in order,
    for the next agent."""
    module = bridge.module
    _ui(bridge)
    assert await _command(bridge, "settings_update", {"persona": "anna"}) == {"status": "queued"}
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    bridge.emitted.clear()
    started, release = asyncio.Event(), asyncio.Event()
    lock = module._send_lock()
    if moment == "waiting_for_the_send_lock":
        await lock.acquire()  # another peer's start, a settings write or a pause holds it
        started.set()
    else:
        _hold(bridge, monkeypatch, "settings_with_meta" if moment == "reading_the_settings" else "take_pending",
              started, release)
    connect = asyncio.create_task(module.handle_connect("sid-agent", {}, dict(AGENT_AUTH)))
    await asyncio.wait_for(started.wait(), 5)
    for _ in range(5):
        await asyncio.sleep(0)
    assert not connect.done()
    await module.handle_disconnect("sid-agent")  # its socket closes meanwhile
    if lock.locked() and moment == "waiting_for_the_send_lock":
        lock.release()
    release.set()
    assert await asyncio.wait_for(connect, 5) is False
    assert "sid-agent" not in module._clients and module.build_agent_status()["online"] is False
    # the bootstrap went out only while the socket was still there (before its flush took the queue)
    expect_bootstrap = moment == "taking_the_queue"
    assert [command["type"] for command, _to in _sent(bridge)] == (["settings_bootstrap"] if expect_bootstrap else [])
    assert (await _meta(bridge))[1]["delivered"] == (1 if expect_bootstrap else 0)
    assert await module.store.count_pending() == 2
    assert await _command(bridge, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}  # never 'sent'
    await _agent(bridge, "sid-next")
    assert [(c["type"], (c["payload"] or {}).get("id")) for c, to in _sent(bridge) if to == "sid-next"] == [
        ("settings_bootstrap", None), ("settings_update", None), ("chat", "u-1"), ("chat", "u-2")]
    assert await module.store.count_pending() == 0


async def test_a_queue_flush_whose_socket_left_puts_back_only_what_it_had_not_sent(bridge, monkeypatch, records):
    """C-60 review: the socket leaves in the middle of a flush (between two sends). What went out before stays sent
    (it may have reached the agent, so it is never sent twice); the rest goes back to the head of the queue, before
    anything queued later."""
    module = bridge.module
    _ui(bridge)
    for n in (1, 2, 3):
        assert await _command(bridge, "chat", {"id": f"u-{n}", "text": "x"}) == {"status": "queued"}
    emit = module.sio.emit

    async def leaving_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        await emit(event, data, **kwargs)
        if event == "agent_command" and kwargs.get("to") == "sid-agent" and (data.get("payload") or {}).get(
                "id") == "u-1":
            await module.handle_disconnect("sid-agent")  # the socket closes right after the first send

    monkeypatch.setattr(module.sio, "emit", leaving_emit)
    assert await module.handle_connect("sid-agent", {}, dict(AGENT_AUTH)) is False
    assert [c["payload"]["id"] for c, to in _sent(bridge) if to == "sid-agent"] == ["u-1"]
    assert records.count("2 queued command(s) back at the head of the queue") == 1
    assert await _command(bridge, "chat", {"id": "u-4", "text": "x"}) == {"status": "queued"}
    await _agent(bridge, "sid-next")
    assert [c["payload"]["id"] for c, to in _sent(bridge) if to == "sid-next"] == ["u-2", "u-3", "u-4"]


async def test_restored_commands_go_back_to_the_head_with_their_own_expiry(bridge):
    store = bridge.module.store
    for n in (1, 2):
        await store.queue_command("chat", {"id": f"u-{n}"}, ttl_s=600, cap=100)
    taken, _expired = await store.take_pending()
    bridge.clock.now += 100
    await store.queue_command("chat", {"id": "u-3"}, ttl_s=600, cap=100)
    assert await store.restore_pending(taken[1:]) == 1  # u-1 went out; u-2 comes back
    entries, _expired = await store.take_pending()
    assert [entry["payload"]["id"] for entry in entries] == ["u-2", "u-3"]  # at the head, before what came later
    assert entries[0]["expires_ts"] == taken[1]["expires_ts"]  # its own expiry: never refreshed by the restore
    assert await store.restore_pending(entries) == 2
    bridge.clock.now += 501  # past u-2's expiry (t0 + 600), not u-3's (t0 + 700)
    entries, expired = await store.take_pending()
    assert [entry["payload"]["id"] for entry in entries] == ["u-3"] and expired == 1


async def _until(predicate: Any, timeout: float = 5.0) -> None:
    """Waits (real time: store calls run in threads) until predicate() holds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("the condition was not reached in time")
        await asyncio.sleep(0.01)


def _flaky_take(ns: Any, monkeypatch: Any, failures: int) -> dict[str, int]:
    """take_pending fails its first `failures` calls (bridge.db locked), then works; returns the call counter."""
    real, calls = ns.module.store.take_pending, {"n": 0}

    async def take() -> Any:
        calls["n"] += 1
        if calls["n"] <= failures:
            raise sqlite3.OperationalError("database is locked")
        return await real()

    monkeypatch.setattr(ns.module.store, "take_pending", take)
    return calls


async def _retries_done(ns: Any) -> None:
    """The retry tasks of failed queue flushes (this test's loop) have ended."""
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(asyncio.gather(*(task for task in ns.module._queue_retries if task.get_loop() is loop)), 5)


def _retry_gate(ns: Any, monkeypatch: Any) -> asyncio.Event:
    """Each retry of a failed queue flush waits until the returned event is set (then it is cleared for the next)."""
    gate = asyncio.Event()

    async def wait(_delay: float) -> None:
        await gate.wait()
        gate.clear()

    monkeypatch.setattr(ns.module, "_queue_retry_wait", wait)
    return gate


async def test_a_queue_that_cannot_be_read_at_connect_is_retried_and_never_overtaken(bridge, monkeypatch, records):
    """C-60 review: bridge.db busy or locked while an agent connects. take_pending failed, stored() gave an empty
    queue, the peer was marked ready while the queue stayed, and later commands went out live ahead of the queued ones
    (which waited for the next connect or end of a pause and could expire unsent). Now the peer takes no live command
    while the flush is tried again: a chat meanwhile waits behind the queued one ('queued'), another type is
    'agent_offline', and once the store answers the agent gets them in order and then takes live commands."""
    module = bridge.module
    _ui(bridge)
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    calls = _flaky_take(bridge, monkeypatch, failures=2)  # the connect's flush and the first retry
    gate = _retry_gate(bridge, monkeypatch)
    await _agent(bridge)
    agent = module._clients["sid-agent"]
    assert not agent.ready and agent.flush_owed and agent.retrying and calls["n"] == 1
    assert records.count("QUEUE the queued commands could not be read", logging.WARNING) == 1
    assert await _command(bridge, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}  # never overtakes
    assert await _command(bridge, "stop", {}) == {"status": "agent_offline"}
    assert await module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "agent_offline"}
    assert _sent(bridge) == [] and await module.store.count_pending() == 2
    gate.set()  # the first retry: still locked
    await _until(lambda: calls["n"] == 2)
    await _until(lambda: not gate.is_set())
    assert not agent.ready and _sent(bridge) == []
    gate.set()  # the second retry: the store answers
    await _until(lambda: agent.ready)
    assert [(c["payload"]["id"], to) for c, to in _sent(bridge)] == [("u-1", "sid-agent"), ("u-2", "sid-agent")]
    assert not agent.flush_owed and await module.store.count_pending() == 0
    assert records.count("went out on a retry") == 1
    await _retries_done(bridge)
    assert not agent.retrying
    bridge.emitted.clear()
    assert await _command(bridge, "chat", {"id": "u-3", "text": "three"}) == {"status": "sent"}
    assert [to for _c, to in _sent(bridge)] == ["agent"]


async def test_after_the_retries_run_out_the_agent_takes_live_commands_and_the_queue_goes_first(
        bridge, monkeypatch, records):
    """C-60 review: a broken bridge.db never keeps a connected agent from live commands. After the last retry it takes
    them anyway (WARNING); while the store still cannot give the queue a live command goes as it is (it could not be
    queued behind it either), and once the store answers the queue goes before the next live command, in order."""
    module = bridge.module
    _ui(bridge)
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    retries = len(module.QUEUE_RETRY_DELAYS_S)
    calls = _flaky_take(bridge, monkeypatch, failures=retries + 2)  # the connect, every retry, one live try
    gate = _retry_gate(bridge, monkeypatch)
    await _agent(bridge)
    agent = module._clients["sid-agent"]
    for attempt in range(retries):
        gate.set()
        await _until(lambda n=attempt + 2: calls["n"] == n)
    await _retries_done(bridge)
    assert agent.ready and agent.flush_owed and not agent.retrying
    assert records.count("QUEUE still unreadable after", logging.WARNING) == 1
    assert await _command(bridge, "chat", {"id": "u-2", "text": "two"}) == {"status": "sent"}  # still unreadable
    assert agent.flush_owed and calls["n"] == retries + 2
    assert await _command(bridge, "chat", {"id": "u-3", "text": "three"}) == {"status": "sent"}  # the store answers
    assert [(c["payload"]["id"], to) for c, to in _sent(bridge)] == [
        ("u-2", "agent"), ("u-1", "sid-agent"), ("u-3", "agent")]
    assert not agent.flush_owed and await module.store.count_pending() == 0


async def test_a_queue_that_cannot_be_read_at_the_end_of_a_pause_is_retried_the_same_way(owner, monkeypatch):
    """C-60 review: the same for the end of a pause (_flush_after_unpause): the legacy agent that waited stays not
    ready while the queue cannot be read, a chat meanwhile waits behind the queued one, and the retry sends both in
    order before any live command."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await manager.set_paused(True, by="ui")
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    calls = _flaky_take(owner, monkeypatch, failures=1)
    gate = _retry_gate(owner, monkeypatch)
    assert await _command(owner, "start", {}) == {"status": "sent"}  # Wake: the queue read fails
    old_agent = module._clients["sid-old-agent"]
    assert not old_agent.ready and old_agent.flush_owed and calls["n"] == 1
    assert await _command(owner, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}
    gate.set()
    await _until(lambda: old_agent.ready)
    assert [c["payload"]["id"] for c, to in _sent(owner) if c["type"] == "chat" and to == "sid-old-agent"] == [
        "u-1", "u-2"]
    await _retries_done(owner)
    assert await _command(owner, "chat", {"id": "u-3", "text": "three"}) == {"status": "sent"}


def _watch(ns: Any, monkeypatch: Any, name: str) -> asyncio.Event:
    """An event set when the bridge's module function `name` is called (the room owner looks it up on every call)."""
    called, real = asyncio.Event(), getattr(ns.module, name)

    async def watched(*args: Any, **kwargs: Any) -> Any:
        called.set()
        return await real(*args, **kwargs)

    monkeypatch.setattr(ns.module, name, watched)
    return called


# python-socketio over aiohttp: the agent's handshake, and any Python tool's (no Origin, no browser User-Agent)
PYTHON_AGENT = {"HTTP_USER_AGENT": "Python/3.12 aiohttp/3.13.3"}


async def test_a_pause_while_a_legacy_agents_connect_waits_never_marks_it_ending(owner, monkeypatch):
    """C-60 review: an agent socket is registered before its connect waits for the send lock. The room owner's
    pause_agent that goes out first never reaches that socket (it is in no room yet), so the pause must not count it
    as told to end: its own start follows the pause and keeps it waiting, and when the pause ends it gets the queue and
    takes commands (marked ending, it would never take one again while connected)."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    pausing = _watch(owner, monkeypatch, "_send_agent_command")
    lock = module._send_lock()
    await lock.acquire()  # another peer's start holds it
    pause = asyncio.create_task(manager.set_paused(True, by="ui"))
    await asyncio.wait_for(pausing.wait(), 5)  # the pause_agent waits for the lock
    for _ in range(5):
        await asyncio.sleep(0)
    connect = asyncio.create_task(module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)))
    for _ in range(5):
        await asyncio.sleep(0)
    assert module._clients["sid-old-agent"].connecting  # registered, waiting behind the pause
    lock.release()
    await asyncio.wait_for(pause, 5)
    assert await asyncio.wait_for(connect, 5) is True
    old_agent = module._clients["sid-old-agent"]
    assert not old_agent.ending and not old_agent.ready  # never told to end: it waits for the end of the pause
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    assert await _command(owner, "start", {}) == {"status": "sent"}  # Wake
    assert old_agent.ready
    assert [c["payload"]["id"] for c, to in _sent(owner) if to == "sid-old-agent" and c["type"] == "chat"] == ["u-1"]


async def test_wake_leaves_a_legacy_agent_whose_connect_waits_to_its_own_start(owner, monkeypatch):
    """C-60 review: Wake's un-pause step runs while a legacy agent's connect waits for the send lock behind it. It
    leaves that socket to its own start, so the agent gets its settings_bootstrap and the queued commands once, in
    that order (readied early by Wake, it got them before it had joined its rooms, and its start a second
    bootstrap)."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await manager.set_paused(True, by="ui")
    assert await _command(owner, "settings_update", {"persona": "anna"}) == {"status": "queued"}
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    resuming = _watch(owner, monkeypatch, "_flush_after_unpause")
    lock = module._send_lock()
    await lock.acquire()
    wake = asyncio.create_task(_command(owner, "start", {}))
    await asyncio.wait_for(resuming.wait(), 5)  # the un-pause step waits for the lock
    for _ in range(5):
        await asyncio.sleep(0)
    connect = asyncio.create_task(module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)))
    for _ in range(5):
        await asyncio.sleep(0)
    lock.release()
    assert await asyncio.wait_for(wake, 5) == {"status": "sent"}
    assert await asyncio.wait_for(connect, 5) is True
    assert [(c["type"], (c["payload"] or {}).get("id")) for c, to in _sent(owner) if to == "sid-old-agent"] == [
        ("settings_bootstrap", None), ("settings_update", None), ("chat", "u-1")]


def _reached(ns: Any, to: Any, skip: Any) -> list[str]:
    """The connected sids a recorded emit reaches, as python-socketio resolves it: the members of a room (the rooms
    each socket joined) other than skip_sid (one sid or a list), or the one sid it names."""
    if to not in ("ui", "agent"):
        return [to] if to in ns.module._clients else []
    skipped = set(skip) if isinstance(skip, list) else {skip}
    return [sid for sid, rooms in ns.joined.items()
            if to in rooms and sid not in skipped and sid in ns.module._clients]


async def test_a_job_the_pause_ended_gets_no_live_command_beside_the_next_job(owner):
    """C-60 review: Pause ends job A, whose socket stays in room 'agent' while it tears down (a slow end-of-job save, a
    blocked loop); Wake; the next job B connects and is ready meanwhile. A typed chat, a settings change and a camera
    frame reach B only, so A never runs the turn or the tool a second time. A repeated Pause still reaches both (an
    agent still there is told once more)."""
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await _agent(owner, "sid-job-a")
    await manager.set_paused(True, by="ui")
    assert await _command(owner, "start", {}) == {"status": "sent"}  # Wake while A's socket is still there
    await _agent(owner, "sid-job-b")
    owner.emitted.clear()
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "sent"}
    assert await _command(owner, "settings_update", {"voice": "Puck"}) == {"status": "sent"}
    assert await module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "sent"}
    reached = [(name, _reached(owner, to, skip)) for name, _data, to, skip in owner.emitted
               if name in ("agent_command", "agent_vision_frame")]
    assert reached == [("agent_command", ["sid-job-b"]), ("agent_command", ["sid-job-b"]),
                       ("agent_vision_frame", ["sid-job-b"])]
    assert module._clients["sid-job-a"].ending and not module._clients["sid-job-b"].ending
    owner.emitted.clear()
    await manager.set_paused(True, by="ui")
    [(_name, data, to, skip)] = [item for item in owner.emitted if item[0] == "agent_command"]
    assert data == dict(bridge_rooms.PAUSE_COMMAND) and _reached(owner, to, skip) == ["sid-job-a", "sid-job-b"]


async def test_a_python_legacy_client_is_a_ui_socket_and_never_takes_the_queue(bridge):
    """C-60 review: a legacy-token socket with no Origin and no browser User-Agent was taken for the owner's agent. A
    Python tool on the legacy token (deploy/windows-local/test_chat_once.py in its default mode, a production test
    script, an old dashboard client) joined room 'agent', swallowed the offline queue at its connect, got the owner's
    commands and camera frames, and the bridge answered 'sent' with no agent there. The real agents connect with
    agent_token and the agent's legacy fallback declares the agent role, so any other legacy-token socket is a UI
    socket: room 'ui' only and init_state, the queue stays for the agent, the owner's chat waits ('queued'), frames
    are 'agent_offline', and the tool's own chat reaches the real agent once one is there."""
    module = bridge.module
    _ui(bridge)
    module._clients["sid-ui"].frames = TokenBucket(1000.0)  # more than one camera frame in this second
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    # python-socketio over aiohttp (test_chat_once.py), and a client that sends no header at all
    for sid, environ in (("sid-script", dict(PYTHON_AGENT)), ("sid-bare", {})):
        assert await module.handle_connect(sid, environ, {"token": LEGACY_TOKEN}) is True
        script = module._clients[sid]
        assert not script.legacy_agent and not module._agent_peer(script) and script.ready
        assert bridge.joined[sid] == ["ui"]
        assert [name for name, _data, to, _skip in bridge.emitted if to == sid] == ["init_state"]  # a UI's welcome
    assert await module.store.count_pending() == 1 and _sent(bridge) == []  # the queue stays for the agent
    assert await _command(bridge, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}  # never 'sent'
    assert await _command(bridge, "chat", {"id": "u-3", "text": "three"}, sid="sid-script") == {"status": "queued"}
    assert await _command(bridge, "stop", {}, sid="sid-script") == {"status": "agent_offline"}
    assert await module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "agent_offline"}
    assert _sent(bridge) == [] and _sent(bridge, "agent_vision_frame") == []
    await _agent(bridge)
    assert [(c["type"], c["payload"]["id"]) for c, to in _sent(bridge) if to == "sid-agent"] == [
        ("chat", "u-1"), ("chat", "u-2"), ("chat", "u-3")]
    bridge.emitted.clear()
    assert await _command(bridge, "chat", {"id": "u-4", "text": "four"}, sid="sid-script") == {"status": "sent"}
    assert await module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "sent"}
    reached = [(name, _reached(bridge, to, skip)) for name, _data, to, skip in bridge.emitted
               if name in ("agent_command", "agent_vision_frame")]
    assert reached == [("agent_command", ["sid-agent"]), ("agent_vision_frame", ["sid-agent"])]


async def test_a_legacy_agent_never_counts_as_the_agent_for_its_own_command(bridge):
    """C-60 review: a legacy agent (the agent role declared beside the legacy token) is an agent peer, but never for a
    command it sends itself: with no other agent its chat and settings change wait ('queued', never 'sent' past itself
    to nobody), other types and camera frames are 'agent_offline', and the next agent gets the queue."""
    module = bridge.module
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    old_agent = module._clients["sid-old-agent"]
    assert old_agent.legacy_agent and module._agent_peer(old_agent) and old_agent.ready
    assert bridge.joined["sid-old-agent"] == ["ui", "agent"]
    bridge.emitted.clear()
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}, sid="sid-old-agent") == {"status": "queued"}
    assert await _command(bridge, "settings_update", {"voice": "Puck"}, sid="sid-old-agent") == {"status": "queued"}
    assert await _command(bridge, "stop", {}, sid="sid-old-agent") == {"status": "agent_offline"}
    assert await module.handle_vision_frame("sid-old-agent", {"frame": "data:x"}) == {"status": "agent_offline"}
    assert _sent(bridge) == [] and _sent(bridge, "agent_vision_frame") == []
    await _agent(bridge)
    assert [(c["type"], (c["payload"] or {}).get("id")) for c, to in _sent(bridge) if to == "sid-agent"] == [
        ("settings_bootstrap", None), ("chat", "u-1"), ("settings_update", None)]


@pytest.mark.parametrize("path", ["admin_cli_unpause", "observe_while_paused"])
async def test_every_end_of_the_pause_readies_a_legacy_agent_that_waited(owner, path):
    """C-60 review: only the dashboard's Wake ran the un-pause step. The admin CLI's unpause (read from kv) and a
    switch to observe mode while paused end the pause too (the room owner's on_resume): the legacy agent that connected
    during it gets its settings_bootstrap and the queued commands in order and takes commands again (it stayed not
    ready, so its commands were 'queued' until they expired)."""
    from src.core import bridge_store  # pylint: disable=import-outside-toplevel
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await manager.set_paused(True, by="ui")
    await manager._poll_kv()  # the running owner's first kv read
    assert await _command(owner, "settings_update", {"persona": "anna"}) == {"status": "queued"}
    assert await _command(owner, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    owner.emitted.clear()
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    assert not module._clients["sid-old-agent"].ready and _sent(owner) == []
    if path == "admin_cli_unpause":
        await module.store.kv_set(bridge_store.KV_PAUSED, False)  # python -m src.core.bridge_rooms unpause
    else:
        await module.store.kv_set(bridge_store.KV_ROOM_MODE, "observe")  # python -m src.core.bridge_rooms mode observe
    await manager._poll_kv()  # within 5 s the running owner applies it
    assert [(c["type"], (c["payload"] or {}).get("id")) for c, to in _sent(owner) if to == "sid-old-agent"] == [
        ("settings_bootstrap", None), ("settings_update", None), ("chat", "u-1")]
    assert module._clients["sid-old-agent"].ready and await module.store.count_pending() == 0
    assert await _command(owner, "chat", {"id": "u-2", "text": "two"}) == {"status": "sent"}


# ---------------------------------------------------------------- rooms and camera frames


async def test_a_legacy_browser_tab_joins_room_ui_only(bridge, records):
    """Only a legacy agent (the agent role declared, from no browser tab) joins room 'agent' beside room 'ui'. A legacy
    dashboard tab, a tab that declares the agent role (logged) and a Python tool that declares nothing join room 'ui'
    only (C-60 review)."""
    module = bridge.module
    assert await module.handle_connect("sid-tab", {"HTTP_ORIGIN": UI_ORIGIN}, {"token": LEGACY_TOKEN}) is True
    assert await module.handle_connect("sid-tab-agent", {"HTTP_USER_AGENT": BROWSER_UA}, dict(LEGACY_AGENT_AUTH))
    assert await module.handle_connect("sid-script", {}, {"token": LEGACY_TOKEN}) is True
    assert await module.handle_connect("sid-old-agent", {}, dict(LEGACY_AGENT_AUTH)) is True
    await _agent(bridge)
    assert bridge.joined == {"sid-tab": ["ui"], "sid-tab-agent": ["ui"], "sid-script": ["ui"],
                             "sid-old-agent": ["ui", "agent"], "sid-agent": ["agent"]}
    assert [sid for sid, client in module._clients.items() if module._agent_peer(client)] == [
        "sid-old-agent", "sid-agent"]  # the old PC agent without the agent link v2 keys keeps working
    assert records.count("a legacy-token handshake from a browser declares the agent role", logging.WARNING) == 1
    assert module._declares_agent({"token": LEGACY_TOKEN, "role": "agent"})
    assert not any(module._declares_agent(auth) for auth in (None, {}, {"role": "ui"}, {"token": LEGACY_TOKEN}))


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 "
              "Safari/537.36")


async def test_a_legacy_tab_polling_from_the_bridges_own_origin_is_no_agent(bridge):
    """C-60 review: a browser leaves Origin off a same-origin GET, and the Engine.IO polling handshake is one. A
    legacy-token dashboard served from the bridge's own origin with polling first (socket.io-client's default) sends no
    Origin; its browser User-Agent still makes it a tab: room 'ui' only, it takes no queued command at connect, and
    with no agent a command is 'queued', never 'sent' to the tab. The old PC agent (python-socketio over aiohttp: a
    Python User-Agent, no Origin, the agent role declared beside the legacy token) stays an agent peer."""
    module = bridge.module
    _ui(bridge)
    assert await _command(bridge, "chat", {"id": "u-1", "text": "one"}) == {"status": "queued"}
    assert await module.handle_connect("sid-tab", {"HTTP_USER_AGENT": BROWSER_UA}, {"token": LEGACY_TOKEN}) is True
    assert _sent(bridge) == [] and await module.store.count_pending() == 1  # the queue stays for the agent
    assert await _command(bridge, "chat", {"id": "u-2", "text": "two"}) == {"status": "queued"}
    assert await module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "agent_offline"}
    assert [name for name, _data, to, _skip in bridge.emitted if to == "sid-tab"] == ["init_state"]  # a tab's welcome
    tab = module._clients["sid-tab"]
    assert not tab.legacy_agent and not module._agent_peer(tab) and bridge.joined["sid-tab"] == ["ui"]
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    assert module._agent_peer(module._clients["sid-old-agent"]) and bridge.joined["sid-old-agent"] == ["ui", "agent"]
    assert [c["payload"]["id"] for c, to in _sent(bridge) if to == "sid-old-agent"] == ["u-1", "u-2"]
    assert module._browser_handshake({"HTTP_ORIGIN": UI_ORIGIN, "HTTP_USER_AGENT": "Python/3.12 aiohttp/3.13.3"})
    assert not module._browser_handshake({}) and not module._browser_handshake({"HTTP_USER_AGENT": "curl/8.4"})


async def test_camera_frames_are_checked_and_go_to_the_agent_sockets_only(bridge, records):
    module = bridge.module
    _ui(bridge)
    frame = {"frame": "data:image/jpeg;base64,AAAA", "ts": 1760000000000, "junk": "x" * 10}
    assert await module.handle_vision_frame("sid-ui", frame) == {"status": "agent_offline"}  # no agent: dropped
    await _agent(bridge)
    clock = FakeClock(1000.0)
    module._clients["sid-ui"].frames = TokenBucket(module.VISION_FRAMES_PER_S, clock=clock)
    bridge.emitted.clear()
    assert await module.handle_vision_frame("sid-ui", frame) == {"status": "sent"}
    assert bridge.emitted == [("agent_vision_frame", {"frame": frame["frame"], "ts": frame["ts"]}, "agent",
                               "sid-ui")]
    assert await module.handle_vision_frame("sid-ui", frame) == {"status": "rejected", "reason": "rate_limited"}
    clock.now += 1.0
    too_big = {"frame": "x" * (module.VISION_FRAME_MAX_CHARS + 1)}
    for _ in range(3):
        assert await module.handle_vision_frame("sid-ui", too_big) == {"status": "rejected", "reason": "too_large"}
    assert records.count("vision_frame over", logging.WARNING) == 1  # once per socket
    for junk in ({"frame": ""}, {"frame": {"a": 1}}, {"ts": 1}, None, 7):
        assert await module.handle_vision_frame("sid-ui", junk) == {"status": "rejected", "reason": "bad_payload"}
    assert await module.handle_vision_frame("sid-ui", frame) == {"status": "sent"}  # the size refusals cost nothing
    assert await module.handle_vision_frame("sid-agent", frame) == {"status": "rejected", "reason": "not_allowed"}


async def test_no_frame_while_the_room_owner_keeps_the_agent_out(owner):
    manager = owner.make_owner()
    _ui(owner)
    await _agent(owner)
    await manager.set_paused(True, by="ui")
    owner.emitted.clear()
    assert await owner.module.handle_vision_frame("sid-ui", {"frame": "data:x"}) == {"status": "agent_offline"}
    assert _sent(owner, "agent_vision_frame") == []


# ---------------------------------------------------------------- C-125 announce and UI counts under C-60's rules


def _announce(attempt: str) -> dict[str, Any]:
    """agent-svc's ladder step 1 (K-77): svc_deliver step 'announce' for one attempt id."""
    return {"step": "announce", "id": attempt, "text": "C60-ANNOUNCE-TEXT", "kind": "reminder"}


def _announces(ns: Any) -> list[Any]:
    """The sids that got agent_command 'announce'."""
    return [to for data, to in _sent(ns) if data.get("type") == "announce"]


async def test_an_announce_goes_only_to_an_owner_room_job_that_takes_commands(owner):
    """C-125 under C-60's delivery rules: the announce goes to the newest owner-room job that takes commands now. A
    legacy agent (no owner-room job), a job whose connect has not finished (its bootstrap and queue flush wait for the
    send lock), any job while the room owner holds a pause, and a job a pause_agent of the bridge ended (its socket
    still connected at Wake) never get it: the answer is 'no_agent' with nothing emitted, so agent-svc settles the
    attempt at once and goes on to the laptop instead of waiting for a result that never comes."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    owner.emitted.clear()
    assert await _notify(owner, "svc_deliver", _announce("r:1:aa#a1")) == {"status": "no_agent", "agents": 0}
    module._clients["sid-joining"] = module.SocketClient(role="agent", ip="10.0.0.2", room=ROOM, ready=False,
                                                         connecting=True)
    assert await _notify(owner, "svc_deliver", _announce("r:1:aa#a2")) == {"status": "no_agent", "agents": 0}
    del module._clients["sid-joining"]
    await _agent(owner, "sid-job-a")
    owner.emitted.clear()
    assert await _notify(owner, "svc_deliver", _announce("r:1:aa#a3")) == {"status": "sent", "agents": 1}
    assert _announces(owner) == ["sid-job-a"]
    await manager.set_paused(True, by="ui")  # Pause: job A is told to end
    owner.emitted.clear()
    assert await _notify(owner, "svc_deliver", _announce("r:1:aa#a4")) == {"status": "no_agent", "agents": 0}
    assert await _command(owner, "start", {}) == {"status": "sent"}  # Wake while job A's socket is still connected
    owner.emitted.clear()
    assert await _notify(owner, "svc_deliver", _announce("r:1:aa#a5")) == {"status": "no_agent", "agents": 0}
    assert _announces(owner) == []
    await _agent(owner, "sid-job-b")
    owner.emitted.clear()
    assert await _notify(owner, "svc_deliver", _announce("r:1:aa#a6")) == {"status": "sent", "agents": 1}
    assert _announces(owner) == ["sid-job-b"]


async def test_an_announce_while_the_owner_holds_a_pause_is_no_agent(owner):
    """A pause the room owner holds keeps every agent out (K-44 v9), even a job that connected and was never told to
    end yet: no announce goes out while it lasts, and agent-svc moves on to the laptop."""
    manager = owner.make_owner()
    module = owner.module
    _ui(owner)
    await _agent(owner, "sid-job-a")
    await manager.set_paused(True, by="ui")
    module._clients["sid-job-a"].ending, module._clients["sid-job-a"].ready = False, True  # as if never told
    owner.emitted.clear()
    assert await _notify(owner, "svc_deliver", _announce("r:2:bb#a1")) == {"status": "no_agent", "agents": 0}
    assert _announces(owner) == []


async def test_an_announce_is_picked_and_sent_under_the_send_lock(bridge):
    """The announce waits for the send lock and picks its job only then, so it never lands in the middle of a connect's
    bootstrap and queue flush or a pause_agent: a job told to end while the lock was held does not get it."""
    module = bridge.module
    _ui(bridge)
    await _agent(bridge, "sid-job-a")
    bridge.emitted.clear()
    lock = module._send_lock()
    await lock.acquire()  # a connect's start, a settings write or a pause holds it
    try:
        announce = asyncio.create_task(_notify(bridge, "svc_deliver", _announce("r:3:cc#a1")))
        await _until(lambda: bool(getattr(lock, "_waiters", None)))  # the announce waits for the lock
        assert not announce.done() and _announces(bridge) == []
        module._clients["sid-job-a"].ending = True  # what the holder did: a pause_agent of the bridge reached it
    finally:
        lock.release()
    assert await asyncio.wait_for(announce, 5) == {"status": "no_agent", "agents": 0}
    assert _announces(bridge) == []
    module._clients["sid-job-a"].ending = False
    assert await _notify(bridge, "svc_deliver", _announce("r:3:cc#a2")) == {"status": "sent", "agents": 1}


async def test_a_legacy_agent_is_no_ui_client(bridge, monkeypatch):
    """C-125 under C-60: agent-svc's ui_clients and ui_connects (svc_poll and the step-3 answer), the room owner's
    on_demand demand, GET /status and the last-UI time count UI sockets only: a dashboard (role 'ui') and a legacy-token
    socket that is no legacy agent, never the old PC agent on the legacy token (agent-svc would hold a reminder for a
    dashboard's ack that never comes, and a connected agent would keep the room demanded)."""
    module = bridge.module
    monkeypatch.setattr(module, "_ui_connects", 0)
    monkeypatch.setattr(module, "_ui_last_seen", None)
    assert await module.handle_connect("sid-old-agent", dict(PYTHON_AGENT), dict(LEGACY_AGENT_AUTH)) is True
    poll = await _notify(bridge, "svc_poll", {})
    assert (poll["ui_clients"], poll["ui_connects"]) == (0, 0) and module._ui_presence() == (0, None)
    assert await module.handle_connect("sid-tab", {"HTTP_USER_AGENT": BROWSER_UA}, {"token": LEGACY_TOKEN}) is True
    _ui(bridge)  # a cookie session's dashboard
    poll = await _notify(bridge, "svc_poll", {})
    assert (poll["ui_clients"], poll["ui_connects"]) == (2, 1) and module._ui_presence()[0] == 2
    answer = await _notify(bridge, "svc_deliver", {"step": "notify", "id": "r:4:dd", "description": "C60-NOTE"})
    assert answer["ui_clients"] == 2
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url=f"http://bridge.test:{INTERNAL_PORT}") as client:
        assert (await client.get("/status")).json()["ui_clients"] == 2
    await module.handle_disconnect("sid-old-agent")  # an agent leaving is no UI leaving
    assert module._ui_last_seen is None
    await module.handle_disconnect("sid-tab")
    assert isinstance(module._ui_last_seen, float)


# ---------------------------------------------------------------- end to end: no dashboard tab gets a frame


class Peer:
    """A python-socketio client that records every event it receives."""

    def __init__(self) -> None:
        self.sio = socketio.AsyncClient(reconnection=False)
        self.events: list[tuple[str, Any]] = []
        self.sio.on("*", self._record)

    async def _record(self, event: str, *args: Any) -> None:
        self.events.append((event, args[0] if args else None))

    def got(self, name: str) -> list[Any]:
        return [data for event, data in self.events if event == name]

    async def wait_for(self, name: str, timeout: float = 5.0) -> Any:
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.got(name):
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"no {name!r} within {timeout} s; got {[e for e, _d in self.events]}")
            await asyncio.sleep(0.02)
        return self.got(name)[0]


async def _idle() -> None:
    await asyncio.Event().wait()


@pytest.fixture
async def live(monkeypatch, tmp_path):
    """The real bridge app on a free 127.0.0.1 port (uvicorn in this event loop) with a temporary store and throwaway
    credentials; sio.emit and the rooms are real."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    monkeypatch.setattr(config, "env", "development")
    module = importlib.import_module("src.core.ui_bridge")
    for name, value in (("store", BridgeStore(tmp_path / "bridge.db")), ("bridge_manager", module.BridgeStateManager()),
                        ("_clients", {}), ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True),
                        ("session_jwt_secret", JWT_SECRET)):
        monkeypatch.setattr(config, name, value)
    for name in ("vitals_heartbeat", "telemetry_heartbeat", "user_speaking_monitor", "store_retention_loop"):
        monkeypatch.setattr(module, name, _idle)
    for name, value in (("start_service_task", True), ("service_task_handle", None), ("service_task_event", None)):
        monkeypatch.setattr(module.sio.eio, name, value)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    monkeypatch.setattr(module.sio.eio, "cors_allowed_origins", [url])  # the legacy tab's Origin is allowed
    server = uvicorn.Server(uvicorn.Config(module.app_asgi, log_level="warning", lifespan="on",
                                           timeout_graceful_shutdown=2))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):
        if server.started or task.done():
            break
        await asyncio.sleep(0.01)
    assert server.started, "the test bridge did not start"
    peers: list[Peer] = []

    def peer() -> Peer:
        peers.append(Peer())
        return peers[-1]

    yield SimpleNamespace(url=url, peer=peer, module=module)
    for p in peers:
        if p.sio.connected:
            await p.sio.disconnect()
    await module.sio.shutdown()
    server.should_exit = True
    await asyncio.wait_for(task, 15)
    sock.close()
    await module.store.close()


async def test_end_to_end_camera_frames_reach_the_agent_and_no_dashboard_tab(live):
    """Real sockets: a camera frame and a chat reach the agent and the legacy agent (the agent role declared beside the
    legacy token), never a dashboard tab or a Python tool on the legacy token that declares nothing (C-60 review)."""
    module = live.module
    token, _record = await module.owner_auth.mint_session(label="c60-e2e")
    cookie = {"Cookie": f"jarvis_session={token}"}
    ui, other_tab, legacy_tab, polling_tab, script, old_agent, agent = (live.peer() for _ in range(7))
    # a fresh headers dict per connect: python-engineio moves the Cookie header out of the dict it is given
    await ui.sio.connect(live.url, headers=dict(cookie), transports=["websocket"], wait_timeout=5)
    await other_tab.sio.connect(live.url, headers=dict(cookie), transports=["websocket"], wait_timeout=5)
    await legacy_tab.sio.connect(live.url, headers={"Origin": live.url}, auth={"token": LEGACY_TOKEN},
                                 transports=["websocket"], wait_timeout=5)
    # a legacy tab served from the bridge's own origin, polling first: a same-origin GET carries no Origin, only the
    # browser's User-Agent (C-60 review)
    await polling_tab.sio.connect(live.url, headers={"User-Agent": BROWSER_UA}, auth={"token": LEGACY_TOKEN},
                                  transports=["polling"], wait_timeout=5)
    # test_chat_once.py in its default mode: python-socketio on the legacy token, no Origin, aiohttp's User-Agent
    await script.sio.connect(live.url, auth={"token": LEGACY_TOKEN}, transports=["websocket"], wait_timeout=5)
    await old_agent.sio.connect(live.url, auth=dict(LEGACY_AGENT_AUTH), transports=["websocket"], wait_timeout=5)
    await agent.sio.connect(live.url, auth={"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "abc",
                                            "room": ROOM}, transports=["websocket"], wait_timeout=5)
    await ui.wait_for("init_state")
    await polling_tab.wait_for("init_state")
    await script.wait_for("init_state")
    roles = sorted(client.role for client in module._clients.values())
    assert roles == ["agent", "legacy", "legacy", "legacy", "legacy", "ui", "ui"]

    frame = {"frame": "data:image/jpeg;base64,AAAA", "ts": 1}
    assert await ui.sio.call("vision_frame", frame, timeout=5) == {"status": "sent"}
    assert await agent.wait_for("agent_vision_frame") == frame
    assert await old_agent.wait_for("agent_vision_frame") == frame  # the old PC agent (the agent role declared)
    chat = {"type": "chat", "payload": {"id": "u-e2e", "text": "salam"}}
    assert await ui.sio.call("ui_command", chat, timeout=5) == {"status": "sent"}
    assert await agent.wait_for("agent_command") == chat
    assert await old_agent.wait_for("agent_command") == chat
    await asyncio.sleep(0.3)  # anything misrouted would have arrived by now
    for tab in (ui, other_tab, legacy_tab, polling_tab, script):
        assert not tab.got("agent_vision_frame") and not tab.got("agent_command")
    assert sorted(client.legacy_agent for client in module._clients.values() if client.role == "legacy") == [
        False, False, False, True]  # the old PC agent is the only legacy agent peer
