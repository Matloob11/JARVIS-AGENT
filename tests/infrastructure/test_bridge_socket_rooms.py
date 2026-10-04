"""Plan task C-14: Socket.IO roles and rooms on the bridge (C-SOCK-1).

The handler tests call the bridge's Socket.IO handlers directly with a recorded sio.emit. The end-to-end tests
run the real bridge app under uvicorn on a free 127.0.0.1 port and talk to it with python-socketio clients
and signed POST /notify requests. Credentials are throwaway values, bridge.db is temporary and the periodic
loops are idle, so nothing reaches the live bridge, a real env file or the project's data folder.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import importlib
import json
import secrets
import socket
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import socketio
import uvicorn

from services.utils import jarvis_security
from services.utils.jarvis_config import config
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore

AGENT_TOKEN = "c14-rooms-agent-token-" + "d" * 24
HMAC_SECRET = "c14-rooms-hmac-secret-" + "e" * 24
LEGACY_TOKEN = "c14-rooms-legacy-token-" + "f" * 24
# What the UI bundle deployed today sends (read from the live bundle on 2 Oct), plus the current dashboard.
OLD_UI_COMMANDS = {"chat", "open_sim_panel", "persona_change", "settings_update", "sim_lookup",
                   "wake_word_toggle", "mute", "unmute"}
DASHBOARD_COMMANDS = {"stop", "start", "pause_agent", "shutdown_agent", "request_state", "voice_take_over",
                      "confirm_response", "clear_memory", "task_cancel", "tasks_refresh", "feedback", "swarm",
                      "job_cancel", "open_job_artifact", "voice_enroll", "desktop_stop", "notification_ack"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store, no connected sockets and throwaway credentials."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())
    monkeypatch.setattr(module, "_clients", {})
    monkeypatch.setattr(module, "_notify_nonces", NonceCache())
    monkeypatch.setattr(module, "_refusal_audit", TokenBucket(1.0, 30.0))
    for name, value in (("_agent_pc_vitals", None), ("_agent_pc_vitals_mono", 0.0), ("_latest_vps_vitals", None)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, "VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "env", "development")
    yield SimpleNamespace(module=module, store=store)
    await store.close()


@pytest.fixture
def direct(bridge, monkeypatch):
    """The handlers called directly; sio.emit recorded as (event, data, room)."""
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    monkeypatch.setattr(bridge.module.sio, "emit", fake_emit)
    bridge.emitted = emitted
    return bridge


async def _connect_agent(ns: Any, sid: str, **extra: Any) -> None:
    auth = {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "abc123", **extra}
    assert await ns.module.handle_connect(sid, {}, auth) is True


async def _connect_legacy(ns: Any, sid: str) -> None:
    assert await ns.module.handle_connect(sid, {}, {"token": LEGACY_TOKEN}) is True


async def _refusals(ns: Any) -> list[dict[str, Any]]:
    rows = await ns.store.recent_audit(action_prefix="socket_auth_failed")
    return [{"actor": row["actor"], **json.loads(row["detail"]), "ip": row["ip"]} for row in rows]


# ---------------------------------------------------------------- roles at connect (handlers called directly)


# K-02 v9 (C-23): agent_status is always the whole object.
AGENT_STATUS_KEYS = {"online", "in_room", "voice_ready", "paused", "restarting", "since", "last_heartbeat", "host",
                     "version", "brain", "agent_name", "session"}


async def test_agent_token_gives_the_agent_role_without_init_state(direct):
    await _connect_agent(direct, "sid-agent")

    client = direct.module._clients["sid-agent"]
    assert (client.role, client.host, client.version) == ("agent", "pc-host", "abc123")
    [(event, status, room)] = direct.emitted
    assert (event, room) == ("agent_status", "ui") and set(status) == AGENT_STATUS_KEYS
    assert (status["online"], status["host"], status["version"]) == (True, "pc-host", "abc123")
    # the room owner is off in this test process: it never looks at LiveKit, so presence is unknown (None, never a
    # false 'absent', R5), not paused, nothing restarting
    assert (status["in_room"], status["voice_ready"], status["paused"], status["restarting"]) == (
        None, None, False, False)
    assert status == direct.module.build_agent_status()
    assert direct.module.ROLE_ROOMS["agent"] == ("agent",)


@pytest.mark.parametrize("environ, auth", [
    ({}, {"token": LEGACY_TOKEN}),
    ({"QUERY_STRING": f"EIO=4&token={LEGACY_TOKEN}&transport=websocket"}, None),
    ({"QUERY_STRING": f"token={LEGACY_TOKEN}"}, {}),
])
async def test_the_legacy_token_joins_both_rooms_and_gets_init_state(direct, environ, auth):
    assert await direct.module.handle_connect("sid-legacy", environ, auth) is True

    assert direct.module._clients["sid-legacy"].role == "legacy"
    assert direct.module.ROLE_ROOMS["legacy"] == ("ui", "agent")
    [(event, state, room)] = direct.emitted
    assert (event, room) == ("init_state", "sid-legacy") and state["persona"] == "jarvis"


async def test_a_query_token_counts_as_sent_and_percent_decoded(direct, monkeypatch):
    token = "c14/legacy+token="
    monkeypatch.setattr(direct.module, "VORTEX_SECURITY_TOKEN", token)
    for query in (f"token={token}", "token=" + quote(token, safe="")):
        assert await direct.module.handle_connect("sid-q", {"QUERY_STRING": query}, None) is True


@pytest.mark.parametrize("environ, auth, reason", [
    ({}, None, "no_credentials"),
    ({}, {}, "no_credentials"),
    ({}, {"agent_token": "wrong"}, "bad_agent_token"),
    ({}, {"agent_token": "", "token": LEGACY_TOKEN}, "bad_agent_token"),  # an agent_token decides on its own
    ({}, {"token": "wrong"}, "bad_token"),
    ({}, {"token": 12345}, "bad_token"),
    ({"QUERY_STRING": "token=wrong"}, None, "bad_token"),
    ({"QUERY_STRING": "token=a=b=c"}, None, "bad_token"),  # crashed the old query parser
    ({}, {"role": "agent", "room": "jarvis-hybrid", "job_id": "j1"}, "no_credentials"),  # :5002 only (C-116)
])
async def test_anything_else_is_refused_and_audited(direct, environ, auth, reason):
    assert await direct.module.handle_connect("sid-x", environ, auth) is False

    assert "sid-x" not in direct.module._clients and not direct.emitted
    assert await _refusals(direct) == [{"actor": "anonymous", "reason": reason, "sid": "sid-x", "ip": "unknown"}]


async def test_the_legacy_token_is_refused_once_the_flag_is_off(direct, monkeypatch):
    monkeypatch.setattr(config, "legacy_token_auth", False)
    assert await direct.module.handle_connect("sid-old", {}, {"token": LEGACY_TOKEN}) is False
    assert [row["reason"] for row in await _refusals(direct)] == ["legacy_off"]
    await _connect_agent(direct, "sid-agent")  # the agent link does not depend on the flag


async def test_an_unset_legacy_token_matches_nothing(direct, monkeypatch):
    monkeypatch.setattr(direct.module, "VORTEX_SECURITY_TOKEN", "")
    for token in ("None", ""):
        assert await direct.module.handle_connect("sid-x", {"QUERY_STRING": f"token={token}"}, None) is False


async def test_refusal_audit_rows_are_rate_limited(direct, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(direct.module, "_refusal_audit", TokenBucket(1.0, 3.0, clock=clock))
    for n in range(10):
        assert await direct.module.handle_connect(f"sid-{n}", {}, None) is False
    assert len(await _refusals(direct)) == 3
    clock.now += 1.0
    assert await direct.module.handle_connect("sid-later", {}, None) is False
    assert len(await _refusals(direct)) == 4


# ---------------------------------------------------------------- routing (handlers called directly)


async def test_ui_command_goes_only_to_room_agent(direct):
    await _connect_legacy(direct, "sid-ui")
    await _connect_agent(direct, "sid-agent")
    direct.emitted.clear()

    command = {"type": "chat", "payload": {"id": "u-1", "text": "hi"}}
    assert await direct.module.handle_ui_command("sid-ui", command) == {"status": "sent"}
    assert direct.emitted == [("agent_command", command, "agent")]
    assert direct.module.bridge_manager.is_chat_id("u-1")
    # today's deployed UI sends chat as a plain string
    assert await direct.module.handle_ui_command("sid-ui", {"type": "chat", "payload": "salam"}) == {
        "status": "sent"}


async def test_an_agent_or_unknown_socket_may_not_send_ui_command(direct):
    await _connect_agent(direct, "sid-agent")
    direct.emitted.clear()
    rejected = {"status": "rejected", "reason": "not_allowed"}
    assert await direct.module.handle_ui_command("sid-agent", {"type": "chat", "payload": "x"}) == rejected
    assert await direct.module.handle_ui_command("sid-ghost", {"type": "chat", "payload": "x"}) == rejected
    assert not direct.emitted


async def test_only_allow_listed_command_types_are_forwarded(direct):
    await _connect_legacy(direct, "sid-ui")
    direct.emitted.clear()
    assert OLD_UI_COMMANDS | DASHBOARD_COMMANDS <= direct.module.UI_COMMAND_TYPES
    for data in ({"type": "rm_rf"}, {"type": ["chat"]}, {"payload": "x"}, "chat", None):
        assert await direct.module.handle_ui_command("sid-ui", data) == {"status": "rejected",
                                                                        "reason": "unknown_type"}
    assert not direct.emitted


async def test_ui_command_is_limited_to_10_a_second_per_socket(direct):
    await _connect_legacy(direct, "sid-a")
    await _connect_legacy(direct, "sid-b")
    clock = FakeClock()
    direct.module._clients["sid-a"].commands = TokenBucket(10.0, clock=clock)
    command = {"type": "mute", "payload": {}}

    assert [await direct.module.handle_ui_command("sid-a", command) for _ in range(10)] == [{"status": "sent"}] * 10
    assert await direct.module.handle_ui_command("sid-a", command) == {"status": "rejected",
                                                                      "reason": "rate_limited"}
    assert await direct.module.handle_ui_command("sid-b", command) == {"status": "sent"}  # per socket
    clock.now += 0.1
    assert await direct.module.handle_ui_command("sid-a", command) == {"status": "sent"}


async def test_vision_frames_go_to_room_agent_at_most_one_a_second(direct):
    await _connect_legacy(direct, "sid-ui")
    await _connect_agent(direct, "sid-agent")
    clock = FakeClock()
    direct.module._clients["sid-ui"].frames = TokenBucket(1.0, clock=clock)
    direct.emitted.clear()
    frame = {"frame": "data:image/jpeg;base64,AAAA", "ts": 1}

    assert await direct.module.handle_vision_frame("sid-ui", frame) == {"status": "sent"}
    assert direct.emitted == [("agent_vision_frame", frame, "agent")]
    assert await direct.module.handle_vision_frame("sid-ui", frame) == {"status": "rejected",
                                                                       "reason": "rate_limited"}
    clock.now += 1.0
    too_big = {"frame": "x" * (direct.module.VISION_FRAME_MAX_CHARS + 1)}
    assert await direct.module.handle_vision_frame("sid-ui", too_big) == {"status": "rejected",
                                                                         "reason": "too_large"}
    assert await direct.module.handle_vision_frame("sid-ui", frame) == {"status": "sent"}
    assert await direct.module.handle_vision_frame("sid-agent", frame) == {"status": "rejected",
                                                                          "reason": "not_allowed"}


async def test_an_agent_socket_may_say_hello_and_nothing_else(direct):
    await _connect_agent(direct, "sid-agent")
    await _connect_legacy(direct, "sid-ui")
    direct.emitted.clear()

    assert await direct.module.handle_agent_hello("sid-agent", {"version": "def456", "host": "pc"}) == {
        "status": "ok"}
    [(event, status, room)] = direct.emitted
    assert (event, room, status["online"], status["version"], status["host"]) == (
        "agent_status", "ui", True, "def456", "pc")
    assert await direct.module.handle_agent_hello("sid-ui", {}) == {"status": "rejected", "reason": "not_allowed"}
    label = await direct.module.handle_agent_hello("sid-agent", {"host": "x\n" * 100})
    assert label == {"status": "ok"} and len(direct.module._clients["sid-agent"].host) <= 64


async def test_agent_status_goes_offline_when_the_last_agent_leaves(direct):
    await _connect_agent(direct, "sid-a1")
    await _connect_agent(direct, "sid-a2")
    await _connect_legacy(direct, "sid-ui")
    assert [event for event, _data, _room in direct.emitted].count("agent_status") == 1  # only the first one
    direct.emitted.clear()

    await direct.module.handle_disconnect("sid-a1")
    await direct.module.handle_disconnect("sid-ui")
    assert not direct.emitted
    await direct.module.handle_disconnect("sid-a2", "client disconnect")
    [(event, status, room)] = direct.emitted
    assert (event, room, status["online"]) == ("agent_status", "ui", False)
    await direct.module.handle_disconnect("sid-ghost")  # never connected: no error
    assert direct.module._clients == {}


def _init_state(emitted: list[tuple[str, Any, Any]], sid: str) -> dict[str, Any]:
    [state] = [data for event, data, room in emitted if event == "init_state" and room == sid]
    return state


async def test_a_ui_that_connects_after_the_agent_gets_agent_status_in_init_state(direct):
    """C-ROOM-1: agent_status is in init_state, or the dashboard shows 'System Unknown' until the agent reconnects.
    K-42 v9: the same whole object as the event."""
    await _connect_agent(direct, "sid-agent")
    await _connect_legacy(direct, "sid-ui")

    [(_event, emitted, _room)] = [item for item in direct.emitted if item[0] == "agent_status"]
    in_state = _init_state(direct.emitted, "sid-ui")["agent_status"]
    assert in_state == emitted and set(in_state) == AGENT_STATUS_KEYS
    assert (in_state["online"], in_state["host"], in_state["version"]) == (True, "pc-host", "abc123")


async def test_init_state_says_offline_while_no_agent_serves_the_owner_room(direct):
    await _connect_agent(direct, "sid-canary", room="canary-room")  # a canary peer never counts
    await _connect_legacy(direct, "sid-ui-1")
    first = _init_state(direct.emitted, "sid-ui-1")["agent_status"]
    assert set(first) == AGENT_STATUS_KEYS and first["online"] is False and first["host"] == ""
    assert not [item for item in direct.emitted if item[0] == "agent_status"]

    await _connect_agent(direct, "sid-agent")
    await direct.module.handle_disconnect("sid-agent")
    [online, offline] = [data for event, data, _room in direct.emitted if event == "agent_status"]
    assert online["online"] is True and offline["online"] is False
    assert offline["since"] >= online["since"] >= first["since"]  # since: when the tuple last changed
    await _connect_legacy(direct, "sid-ui-2")
    assert _init_state(direct.emitted, "sid-ui-2")["agent_status"] == offline


# ---------------------------------------------------------------- C-23 (K-03/K-38/K-44 v9): pause_agent, start, stop


PAUSE_COMMAND = {"type": "pause_agent", "payload": {"reason": "paused", "by": "bridge"}}  # the bridge's own


@pytest.fixture
def owner(direct, monkeypatch):
    """An enabled room owner (as in the VPS bridge) on the bridge's own callables; any LiveKit call fails the test
    (pause and start only change the flag and wake the loop, which does not run here)."""
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel

    def make(env_mode: str | None = None) -> Any:
        settings = bridge_rooms.RoomSettings(api_url="http://livekit.c23.invalid:7880", key="c23-k", secret="c23-s",
                                             room="jarvis-hybrid", env_mode=env_mode, role_ok=True)
        module = direct.module
        manager = bridge_rooms.RoomManager(
            settings, store=lambda: module.store, emit_ui=module.emit_ui,
            send_agent_command=lambda data, sid=None: module._send_agent_command(data, sid),
            agent_job_sockets=module._agent_job_sockets, ui_presence=module._ui_presence,
            on_status=module._publish_agent_status,
            api_factory=lambda: pytest.fail("no LiveKit call expected"))
        monkeypatch.setattr(module, "rooms", manager)
        return manager

    direct.make_owner = make
    monkeypatch.setenv("JARVIS_ROOM_NAME", "jarvis-hybrid")
    return direct


async def test_pause_agent_sets_the_paused_flag_and_the_status_and_sends_the_bridges_own_pause(owner):
    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel

    assert PAUSE_COMMAND == bridge_rooms.PAUSE_COMMAND
    manager = owner.make_owner()
    await _connect_legacy(owner, "sid-ui")
    owner.emitted.clear()
    command = {"type": "pause_agent", "payload": {}}
    assert await owner.module.handle_ui_command("sid-ui", command) == {"status": "sent"}

    assert manager.paused and await owner.store.kv_get("paused") is True
    statuses = [data for event, data, _room in owner.emitted if event == "agent_status"]
    assert [s["paused"] for s in statuses] == [True]
    # the bridge holds the pause: its own pause_agent (payload.by 'bridge') goes out, the UI's own is never forwarded
    assert [(event, data, room) for event, data, room in owner.emitted if event == "agent_command"] == [
        ("agent_command", PAUSE_COMMAND, "agent")]
    [row] = await owner.store.recent_audit(action_prefix="agent_paused")
    assert json.loads(row["detail"]) == {"by": "legacy"}
    owner.emitted.clear()  # a second Pause tells an agent that is still there once more; the flag stays as it is
    assert await owner.module.handle_ui_command("sid-ui", command) == {"status": "sent"}
    assert owner.emitted == [("agent_command", PAUSE_COMMAND, "agent")]
    assert len(await owner.store.recent_audit(action_prefix="agent_paused")) == 1


async def test_stop_never_pauses(owner):
    manager = owner.make_owner()
    await _connect_legacy(owner, "sid-ui")
    owner.emitted.clear()
    assert await owner.module.handle_ui_command("sid-ui", {"type": "stop", "payload": {}}) == {"status": "sent"}
    assert not manager.paused and await owner.store.kv_get("paused") is None
    assert owner.emitted == [("agent_command", {"type": "stop", "payload": {}}, "agent")]
    assert manager._take_kick() is None


async def test_start_clears_the_pause_and_asks_for_an_explicit_ensure(owner):
    manager = owner.make_owner()
    await manager.set_paused(True, by="ui")
    await _connect_legacy(owner, "sid-ui")
    owner.emitted.clear()
    assert await owner.module.handle_ui_command("sid-ui", {"type": "start", "payload": {}}) == {"status": "sent"}
    assert not manager.paused and await owner.store.kv_get("paused") is False
    assert manager._take_kick() == ("start", True, True)  # Wake: explicit, no 8 s confirmation
    assert [data["paused"] for event, data, _room in owner.emitted if event == "agent_status"] == [False]


async def test_an_agent_that_connects_while_paused_gets_pause_agent(owner):
    manager = owner.make_owner()
    await manager.set_paused(True, by="ui")
    owner.emitted.clear()
    await _connect_agent(owner, "sid-agent")
    assert ("agent_command", PAUSE_COMMAND, "sid-agent") in owner.emitted
    assert manager._take_kick() == ("agent_connect", False, False)
    [status] = [data for event, data, _room in owner.emitted if event == "agent_status"]
    assert (status["online"], status["paused"]) == (True, True)
    await _connect_agent(owner, "sid-canary", room="canary-room")  # another room's peer gets nothing
    assert ("agent_command", PAUSE_COMMAND, "sid-canary") not in owner.emitted


async def test_observe_mode_refuses_pause_agent_and_start_only_heals(owner):
    """R6/LS-1/SEC-2: in observe mode nothing would keep a paused agent out or bring it back on Wake, so pause_agent
    is refused and changes nothing (no flag, nothing forwarded); start is forwarded as before and may only heal an
    agentless room (LS-2), never touching the flag."""
    manager = owner.make_owner(env_mode="observe")
    await _connect_legacy(owner, "sid-ui")
    await _connect_agent(owner, "sid-agent")
    assert manager._take_kick() == ("agent_connect", False, False)
    owner.emitted.clear()
    pause = {"type": "pause_agent", "payload": {}}
    assert await owner.module.handle_ui_command("sid-ui", pause) == {"status": "rejected",
                                                                     "reason": "pause_unavailable"}
    assert owner.emitted == [] and not manager.paused and await owner.store.kv_get("paused") is None
    start = {"type": "start", "payload": {}}
    assert await owner.module.handle_ui_command("sid-ui", start) == {"status": "sent"}
    assert owner.emitted == [("agent_command", start, "agent")]
    assert not manager.paused and await owner.store.kv_get("paused") is None
    assert manager._take_kick() == ("start", True, True)  # the observe heal's trigger


async def test_the_local_bridge_without_a_room_owner_refuses_pause_agent(direct):
    """JARVIS_ROLE is not bridge in this test process: the room owner is off, nobody could hold the pause, and the
    agent would end its job for good (SEC-2), so the command is refused; start and stop are forwarded as before."""
    assert not direct.module.rooms.enabled
    await _connect_legacy(direct, "sid-ui")
    direct.emitted.clear()
    command = {"type": "pause_agent", "payload": {}}
    assert await direct.module.handle_ui_command("sid-ui", command) == {"status": "rejected",
                                                                       "reason": "pause_unavailable"}
    assert direct.emitted == []
    for other in ({"type": "start", "payload": {}}, {"type": "stop", "payload": {}}):
        assert await direct.module.handle_ui_command("sid-ui", other) == {"status": "sent"}
    assert direct.emitted == [("agent_command", {"type": "start", "payload": {}}, "agent"),
                              ("agent_command", {"type": "stop", "payload": {}}, "agent")]


async def test_agent_hello_is_rate_limited_per_socket(owner):
    """SEC-8: a looping agent_hello wakes the room owner's loop at most once a second per socket (burst 5)."""
    manager = owner.make_owner()
    await _connect_agent(owner, "sid-agent")
    manager._take_kick()
    answers = [await owner.module.handle_agent_hello("sid-agent", {"version": "abc123"}) for _ in range(8)]
    assert answers[:5] == [{"status": "ok"}] * 5
    assert answers[5:] == [{"status": "rejected", "reason": "rate_limited"}] * 3
    assert manager._take_kick() == ("agent_hello", False, False)


# ---------------------------------------------------------------- end to end: uvicorn + python-socketio


class Peer:
    """A python-socketio client that records every event it receives."""

    def __init__(self) -> None:
        self.sio = socketio.AsyncClient(reconnection=False)
        self.events: list[tuple[str, Any]] = []
        self._changed = asyncio.Event()
        self.sio.on("*", self._record)

    async def _record(self, event: str, *args: Any) -> None:
        self.events.append((event, args[0] if args else None))
        self._changed.set()

    def names(self) -> list[str]:
        return [event for event, _data in self.events]

    async def connect(self, url: str, auth: Any, wait_timeout: float = 5,
                      transports: tuple[str, ...] = ("websocket",)) -> None:
        await self.sio.connect(url, auth=auth, transports=list(transports), wait_timeout=wait_timeout)

    async def wait_for(self, name: str, timeout: float = 5.0) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            self._changed.clear()
            for event, data in self.events:
                if event == name:
                    return data
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(f"no {name!r} within {timeout} s; got {self.names()}")
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                pass


async def _idle() -> None:
    await asyncio.Event().wait()


@pytest.fixture
async def live(bridge, monkeypatch):
    """The real bridge app on a free 127.0.0.1 port (uvicorn in this event loop)."""
    module = bridge.module
    for name in ("vitals_heartbeat", "telemetry_heartbeat", "user_speaking_monitor", "store_retention_loop"):
        monkeypatch.setattr(module, name, _idle)
    for name, value in (("start_service_task", True), ("service_task_handle", None),
                        ("service_task_event", None)):
        monkeypatch.setattr(module.sio.eio, name, value)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
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

    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", peer=peer, module=module, store=bridge.store)
    for p in peers:
        if p.sio.connected:
            await p.sio.disconnect()
    await module.sio.shutdown()
    server.should_exit = True
    await asyncio.wait_for(task, 15)
    sock.close()


async def _signed_notify(url: str, event_type: str, payload: Any) -> httpx.Response:
    raw = json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    headers = {"Content-Type": "application/json", "X-Agent-Token": AGENT_TOKEN, "X-Agent-Timestamp": ts,
               "X-Agent-Nonce": nonce, "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, raw)}
    async with httpx.AsyncClient(timeout=5) as client:
        return await client.post(f"{url}/notify", content=raw, headers=headers)


async def test_rooms_end_to_end(live):
    ui, old_agent, agent = live.peer(), live.peer(), live.peer()
    # today's dashboard: auth.token, polling first and then the websocket upgrade (socket.io-client defaults)
    await ui.connect(live.url, {"token": LEGACY_TOKEN}, transports=("polling", "websocket"))
    await old_agent.connect(live.url, {"token": LEGACY_TOKEN})       # today's PC agent, not yet on C-15
    await ui.wait_for("init_state")
    await agent.connect(live.url, {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "abc123"})
    assert (await ui.wait_for("agent_status"))["online"] is True

    # ui_command reaches room 'agent': the agent socket and, while legacy auth is on, every legacy socket
    command = {"type": "chat", "payload": {"id": "u-42", "text": "salam"}}
    assert await ui.sio.call("ui_command", command, timeout=5) == {"status": "sent"}
    assert await agent.wait_for("agent_command") == command
    assert await old_agent.wait_for("agent_command") == command
    assert await agent.sio.call("ui_command", command, timeout=5) == {"status": "rejected",
                                                                      "reason": "not_allowed"}

    # notify events reach room 'ui' only
    resp = await _signed_notify(live.url, "transcription", {"id": "v-1", "role": "agent", "text": "Ji Sir"})
    assert resp.status_code == 200
    assert (await ui.wait_for("new_message"))["role"] == "assistant"
    await old_agent.wait_for("new_message")  # legacy sockets sit in room 'ui' too (today's broadcast)

    # camera frames reach room 'agent' only
    frame = {"frame": "data:image/jpeg;base64,AAAA", "ts": 1}
    assert await ui.sio.call("vision_frame", frame, timeout=5) == {"status": "sent"}
    assert await agent.wait_for("agent_vision_frame") == frame
    assert await old_agent.wait_for("agent_vision_frame") == frame
    assert await agent.sio.call("agent_hello", {"version": "abc124", "host": "pc-host"}, timeout=5) == {
        "status": "ok"}

    await asyncio.sleep(0.3)  # anything misrouted would have arrived by now
    assert not {"init_state", "new_message", "transcription_update", "agent_status"} & set(agent.names())

    await agent.sio.disconnect()
    for _ in range(200):
        if any(e == "agent_status" and d["online"] is False for e, d in ui.events):
            break
        await asyncio.sleep(0.02)
    assert any(e == "agent_status" and d["online"] is False for e, d in ui.events)


@pytest.mark.parametrize("auth", [None, {"token": "wrong"}, {"agent_token": "wrong"},
                                  {"role": "agent", "room": "jarvis-hybrid", "job_id": "j1"}])
async def test_a_socket_without_valid_credentials_is_refused_and_audited(live, auth):
    peer = live.peer()
    with pytest.raises(socketio.exceptions.ConnectionError):
        await peer.connect(live.url, auth, wait_timeout=1)
    assert not peer.sio.connected
    [row] = await live.store.recent_audit(action_prefix="socket_auth_failed")
    assert row["actor"] == "anonymous" and row["ip"] == "127.0.0.1"
    assert json.loads(row["detail"])["reason"] in {"no_credentials", "bad_token", "bad_agent_token"}


async def test_a_forged_notify_over_http_changes_nothing(live):
    ui = live.peer()
    await ui.connect(live.url, {"token": LEGACY_TOKEN})
    await ui.wait_for("init_state")
    before = live.module.bridge_manager.state["last_runner_contact"]
    raw = b'{"type":"transcription","payload":{"id":"f-1","role":"agent","text":"forged"}}'
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(f"{live.url}/notify", content=raw, headers={
            "Content-Type": "application/json", "X-Agent-Token": "wrong", "X-Agent-Timestamp": str(int(time.time())),
            "X-Agent-Nonce": "a" * 32, "X-Agent-Signature": "0" * 64})
    assert resp.status_code == 401
    await asyncio.sleep(0.3)
    assert "new_message" not in ui.names()
    assert live.module.bridge_manager.state["last_runner_contact"] == before
