"""Plan task C-116: the bridge's Desktop Link relay ('/desktop', K-70 v5), desktop_status (K-74 v5), the internal
listener :5002 (K-38/K-39/K-48 v4), the persisted link switch and the minimal public health.

The handler tests drive bridge_desktop.DesktopLink directly with a recorded Socket.IO server and a fake clock. The
end-to-end test runs the real bridge app under uvicorn on two free 127.0.0.1 ports (one public, one standing in for
:5002) and talks to it with python-socketio clients. The fake Desktop Node and fake agent peers use the real
desktop_protocol library with keys made for this test run; bridge.db and every seen store are temporary. Nothing
reaches the live bridge, LiveKit, a real env file, a real key or the project's data folder.
"""
# pylint: disable=protected-access,redefined-outer-name,too-many-lines
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import socket
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
import socketio
import uvicorn
import yaml

from services.utils import desktop_protocol as dp
from services.utils.jarvis_config import config
from src.core import bridge_desktop
from src.core.bridge_desktop import DesktopLink, LinkKeys
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import (
    KV_ACTIVE_JOB,
    KV_DESKTOP_LINK,
    KV_DESKTOP_STOP,
    KV_LAST_ACTIVITY_TS,
    KV_NODE_ID,
    KV_NODE_POWER,
    KV_NODE_STATE,
    KV_NODE_VITALS,
    BridgeStore,
)

ROOT = Path(__file__).resolve().parents[2]
AGENT_KEY = dp.generate_signing_key()
PC_KEY = dp.generate_signing_key()
FORGER_KEY = dp.generate_signing_key()
KEYS = LinkKeys(agent=AGENT_KEY.public_key(), pc=PC_KEY.public_key())
LEGACY_TOKEN = "c116-legacy-token-" + "a" * 24
AGENT_TOKEN = "c116-agent-token-" + "b" * 24
HMAC_SECRET = "c116-hmac-secret-" + "c" * 24
ROOM = "jarvis-hybrid"
SECRET_ARG = "c116-plaintext-marker-" + "x" * 12  # appears only inside sealed bodies
PUBLIC_PORT, INTERNAL_PORT = 5001, 5002
NODE_IP, AGENT_IP = "198.51.100.7", "172.18.0.9"


def text(envelope: dict[str, Any]) -> str:
    return dp.canonical_json(envelope).decode("utf-8")


def environ(port: int, ip: str = NODE_IP, real_ip: str | None = None) -> dict[str, Any]:
    """A python-engineio environ as uvicorn produces it: the local port decides the listener."""
    env: dict[str, Any] = {"asgi.scope": {"type": "websocket", "server": ("127.0.0.1", port), "client": (ip, 50000)}}
    if real_ip is not None:
        env["HTTP_X_REAL_IP"] = real_ip
    return env


PUBLIC = environ(PUBLIC_PORT)
INTERNAL = environ(INTERNAL_PORT, AGENT_IP)


class Clock:
    """wall and monotonic clock of the relay in the handler tests (it starts at the real time)."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


class FakeNode:
    """The laptop's Desktop Node side of K-70, on the real library: hello, notices, the handshake, sealed replies."""

    def __init__(self, folder: Path, *, node_id: str = "laptop1", key: Any = PC_KEY) -> None:
        self.node_id, self.ident = node_id, f"pc:{node_id}"
        self.signer = dp.LinkSigner(self.ident, key)
        self.seen = dp.SeenStore(folder / f"node-{node_id}-{id(self)}.db", namespace=node_id)
        self.receiver = dp.Receiver(ident=self.ident, public_keys={"agent": KEYS.agent}, seen=self.seen)
        self.sessions: dict[str, dp.PeerSession] = {}
        self.reconnect()

    def reconnect(self) -> None:
        """A new connection: a fresh ephemeral key and session nonce (K-70)."""
        self.eph, self.sess = dp.new_ephemeral_key(), dp.new_session_nonce()
        self.receiver.bind(self.sess, None)
        self.sessions = {}

    def hello(self, *, to: str = "agent:*", mode: str = "full", locked: bool = False,
              now: float | None = None) -> str:
        body = dp.node_hello_body(node_id=self.node_id, eph=self.eph, sess=self.sess, fields={
            "boot_id": "1" * 32, "version": "1.4.0", "git_sha": "abc1234", "manifest_sha256": "0" * 64,
            "param_schema_sha256": "0" * 64, "mode": mode, "locked": locked, "wal_report": []})
        return text(self.signer.build("hello", body, to=to, sess=self.sess, now=now))

    def notice(self, kind: str, *, now: float | None = None, **fields: Any) -> str:
        return text(self.signer.build("notice", {"kind": kind, **fields}, to="bridge", sess=self.sess, now=now))

    def accept(self, agent_hello: str) -> None:
        env = self.receiver.verify(agent_hello)
        session = dp.accept_agent_hello(env, own_eph=self.eph, sess=self.sess, node_id=self.node_id)
        self.sessions[session.peer_id] = session

    def open(self, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        env = self.receiver.verify(message)
        return dict(env.header), dp.open_body(env, self.sessions[env.sender.partition(":")[2]].key)

    def reply(self, typ: str, peer_id: str, call_id: str, tool: str, body: dict[str, Any]) -> str:
        return text(self.signer.build(typ, {"call_id": call_id, **body}, to=f"agent:{peer_id}", sess=self.sess,
                                      room=ROOM, origin="llm", call_id=call_id, tool=tool,
                                      session_key=self.sessions[peer_id].key))

    def pong(self, ping: str, peer_id: str) -> str:
        env = self.receiver.verify(ping)
        return text(self.signer.build("pong", dp.pong_body(env, received_at=time.time()), to=f"agent:{peer_id}",
                                      sess=self.sess))

    def close(self) -> None:
        self.seen.close()


class FakeAgent:
    """One agent peer (a LiveKit job) of K-70 on the real library."""

    def __init__(self, folder: Path, *, peer_id: str = "job1", room: str = ROOM, key: Any = AGENT_KEY) -> None:
        self.peer_id, self.room, self.ident = peer_id, room, f"agent:{peer_id}"
        self.signer = dp.LinkSigner(self.ident, key)
        self.seen = dp.SeenStore(folder / f"agent-{peer_id}-{id(self)}.db", namespace=peer_id)
        self.receiver = dp.Receiver(ident=self.ident, public_keys={"pc": KEYS.pc}, seen=self.seen)
        self.eph = dp.new_ephemeral_key()
        self.session: dp.PeerSession | None = None

    def agent_hello(self, *, sess: str | None = None, now: float | None = None) -> str:
        body = dp.agent_hello_body(peer_id=self.peer_id, job_id=self.peer_id, room=self.room, eph=self.eph,
                                   sess=sess)
        return text(self.signer.build("agent_hello", body, to="pc:*", room=self.room, sess=sess, now=now))

    def accept(self, hello: str) -> None:
        env = self.receiver.verify(hello)
        self.receiver.bind(env.sess, env.sender)
        self.session = dp.accept_node_hello(env, own_eph=self.eph, peer_id=self.peer_id)

    def exec(self, call_id: str, tool: str = "type_text_tool", args: dict[str, Any] | None = None, *,
             session: dp.PeerSession | None = None) -> str:
        session = session or self.session
        assert session is not None
        args = {"text": SECRET_ARG} if args is None else args
        body = {"call_id": call_id, "tool": tool, "args": args, "args_sha256": dp.args_sha256(args), "timeout_s": 10}
        return text(self.signer.build("exec", body, to=f"pc:{session.node_id}", sess=session.sess, room=self.room,
                                      origin="llm", call_id=call_id, tool=tool, session_key=session.key))

    def ping(self) -> str:
        assert self.session is not None
        return text(self.signer.build("ping", {}, to=f"pc:{self.session.node_id}", sess=self.session.sess))

    def open(self, message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        assert self.session is not None
        env = self.receiver.verify(message)
        return dict(env.header), dp.open_body(env, self.session.key)

    def close(self) -> None:
        self.seen.close()


class FakeSio:
    """Records what the relay sends on '/desktop'; disconnect() runs the relay's handler, as python-socketio does."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any, Any, Any]] = []
        self.disconnected: list[str] = []
        self.link: DesktopLink | None = None

    async def emit(self, event: str, data: Any = None, to: Any = None, namespace: Any = None, **_: Any) -> None:
        self.sent.append((event, data, to, namespace))

    async def disconnect(self, sid: str, namespace: Any = None) -> None:
        self.disconnected.append(sid)
        assert self.link is not None
        await self.link.on_disconnect(sid)

    def msgs(self, sid: str) -> list[str]:
        return [data for event, data, to, _ns in self.sent if event == bridge_desktop.EVENT_MSG and to == sid]

    def hints(self, sid: str, typ: str | None = None) -> list[dp.BridgeHint]:
        hints = [dp.parse_bridge_hint(data) for event, data, to, _ns in self.sent
                 if event == bridge_desktop.EVENT_HINT and to == sid]
        return [hint for hint in hints if typ is None or hint.typ == typ]

    def clear(self) -> None:
        self.sent.clear()


@pytest.fixture
async def relay(tmp_path, monkeypatch):
    """A started DesktopLink with throwaway keys, a temporary store and seen store, a fake clock and its maintenance
    loop stopped (the tests call tick() themselves)."""
    monkeypatch.setenv("JARVIS_BRAIN", "pc")
    clock = Clock()
    store = BridgeStore(tmp_path / "bridge.db")
    ui: list[tuple[str, Any]] = []

    async def emit_ui(event: str, data: Any) -> None:
        ui.append((event, data))

    sio = FakeSio()
    peers: list[Any] = []

    def make(**kwargs: Any) -> DesktopLink:
        link = DesktopLink(sio=sio, store=lambda: store, emit_ui=emit_ui, keys=KEYS,
                           seen_path=str(tmp_path / "desktop_seen.db"), wall=clock, mono=clock, **kwargs)
        sio.link = link
        return link

    async def start(link: DesktopLink) -> DesktopLink:
        await link.start()
        if link._task is not None:
            link._task.cancel()
            await asyncio.gather(link._task, return_exceptions=True)
            link._task = None
        return link

    link = await start(make())
    ns = SimpleNamespace(link=link, sio=sio, ui=ui, clock=clock, store=store, make=make, start=start, peers=peers,
                         path=tmp_path)

    def node(**kwargs: Any) -> FakeNode:
        peers.append(FakeNode(tmp_path, **kwargs))
        return peers[-1]

    def agent(**kwargs: Any) -> FakeAgent:
        peers.append(FakeAgent(tmp_path, **kwargs))
        return peers[-1]

    ns.node, ns.agent = node, agent
    yield ns
    await ns.link.stop()
    for peer in peers:
        peer.close()
    await store.close()


class LogRecords(logging.Handler):
    """The records one logger emitted."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)


@pytest.fixture
def bridge_log():
    """Records of the UI-BRIDGE logger, which bridge_desktop shares. ui_bridge is imported first: its setup_logger
    would drop a handler added before it."""
    importlib.import_module("src.core.ui_bridge")
    log = logging.getLogger("UI-BRIDGE")
    handler, level = LogRecords(), log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    yield handler
    log.removeHandler(handler)
    log.setLevel(level)


def ui_events(ns: Any, name: str) -> list[Any]:
    return [data for event, data in ns.ui if event == name]


async def audit(ns: Any, prefix: str) -> list[dict[str, Any]]:
    rows = await ns.store.recent_audit(limit=500, action_prefix=prefix)
    return [{**row, "detail": json.loads(row["detail"])} for row in rows]


async def connect_node(ns: Any, node: FakeNode, sid: str = "sid-node", env: dict[str, Any] | None = None,
                       **hello: Any) -> None:
    assert await ns.link.on_connect(sid, env or PUBLIC, {"hello": node.hello(**hello)}) is True
    await ns.link.drain()


async def connect_agent(ns: Any, agent: FakeAgent, sid: str = "sid-agent") -> None:
    assert await ns.link.on_connect(sid, INTERNAL, {"hello": agent.agent_hello()}) is True
    await ns.link.drain()


async def handshake(ns: Any, node: FakeNode, agent: FakeAgent, *, node_sid: str = "sid-node",
                    agent_sid: str = "sid-agent") -> None:
    """K-70 handshake through the relay: the node's hello reaches the agent, the agent's agent_hello the node."""
    agent.accept(ns.sio.msgs(agent_sid)[-1])
    await ns.link.on_msg(agent_sid, agent.agent_hello(sess=node.sess))
    node.accept(ns.sio.msgs(node_sid)[-1])


async def linked(ns: Any) -> tuple[FakeNode, FakeAgent]:
    node, agent = ns.node(), ns.agent()
    await connect_agent(ns, agent)
    await connect_node(ns, node)
    await handshake(ns, node, agent)
    ns.sio.clear()
    ns.ui.clear()
    return node, agent


# ---------------------------------------------------------------- keys and the dormant relay


def test_link_keys_come_only_from_the_environment_and_must_be_usable():
    agent_hex, pc_hex = dp.public_key_hex(AGENT_KEY), dp.public_key_hex(PC_KEY)
    keys, why = bridge_desktop.load_link_keys({"DESKTOP_AGENT_PUBLIC_KEY": agent_hex, "DESKTOP_PC_PUBLIC_KEY": pc_hex})
    assert why == "" and keys is not None and dp.public_key_hex(keys.pc) == pc_hex
    assert bridge_desktop.load_link_keys({}) == (
        None, "DESKTOP_AGENT_PUBLIC_KEY and DESKTOP_PC_PUBLIC_KEY are not set")
    assert bridge_desktop.load_link_keys({"DESKTOP_AGENT_PUBLIC_KEY": agent_hex})[1] == (
        "DESKTOP_PC_PUBLIC_KEY is not set")
    identity = "01" + "00" * 31  # the small-order identity point: refused, and the reason never echoes it
    keys, why = bridge_desktop.load_link_keys({"DESKTOP_AGENT_PUBLIC_KEY": agent_hex, "DESKTOP_PC_PUBLIC_KEY": identity})
    assert keys is None and why == "DESKTOP_PC_PUBLIC_KEY is not a usable Ed25519 public key" and identity not in why


@pytest.mark.parametrize("role, keys_set", [("bridge", False), ("agent", True)])
async def test_without_keys_or_off_the_bridge_role_the_relay_stays_off(tmp_path, monkeypatch, role, keys_set):
    monkeypatch.setenv("JARVIS_ROLE", role)
    for name, key in (("DESKTOP_AGENT_PUBLIC_KEY", AGENT_KEY), ("DESKTOP_PC_PUBLIC_KEY", PC_KEY)):
        if keys_set:  # valid keys, but the PC's local bridge never relays the link
            monkeypatch.setenv(name, dp.public_key_hex(key))
        else:
            monkeypatch.delenv(name, raising=False)
    store = BridgeStore(tmp_path / "bridge.db")
    link = DesktopLink(sio=FakeSio(), store=lambda: store, emit_ui=lambda *_: asyncio.sleep(0))
    try:
        await link.start()
        assert not link.active and link.seen is None and link._task is None
        assert not (tmp_path / "desktop_seen.db").exists()
        for env, auth in ((PUBLIC, {"hello": "x"}), (INTERNAL, {"hello": "x"}), (PUBLIC, None)):
            with pytest.raises(socketio.exceptions.ConnectionRefusedError, match="link_unconfigured"):
                await link.on_connect("sid-x", env, auth)
        assert link.status()["state"] == "offline" and link.status()["online"] is False
    finally:
        await link.stop()
        await store.close()


async def test_in_the_bridge_role_the_keys_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ROLE", "bridge")
    monkeypatch.setenv("DESKTOP_AGENT_PUBLIC_KEY", dp.public_key_hex(AGENT_KEY))
    monkeypatch.setenv("DESKTOP_PC_PUBLIC_KEY", dp.public_key_hex(PC_KEY))
    store = BridgeStore(tmp_path / "bridge.db")
    link = DesktopLink(sio=FakeSio(), store=lambda: store, emit_ui=lambda *_: asyncio.sleep(0))
    try:
        await link.start()
        assert link.active and link._task is not None and (tmp_path / "desktop_seen.db").is_file()
    finally:
        await link.stop()
        await store.close()
    assert link._task is None and link.seen is None


async def test_the_seen_store_is_its_own_file_next_to_bridge_db_never_bridge_db(tmp_path):
    store = BridgeStore(tmp_path / "data" / "bridge.db")
    link = DesktopLink(sio=FakeSio(), store=lambda: store, emit_ui=lambda *_: asyncio.sleep(0), keys=KEYS)
    try:
        await link.start()
        assert link.active and isinstance(link.seen, dp.SeenStore)
        assert (tmp_path / "data" / "desktop_seen.db").is_file()
        conn = sqlite3.connect(tmp_path / "data" / "bridge.db")
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "desktop_seen" not in tables and "seen_ids" in tables
    finally:
        await link.stop()
        await store.close()


# ---------------------------------------------------------------- relay: handshake, sealed bodies, routing


async def test_node_and_agent_exchange_sealed_messages_the_bridge_cannot_read(relay):
    node, agent = await linked(relay)
    assert agent.session is not None and node.sessions[agent.peer_id].sess == node.sess

    exec_text = agent.exec("call-0001")
    await relay.link.on_msg("sid-agent", exec_text)
    [relayed] = relay.sio.msgs("sid-node")
    assert relayed == exec_text  # the exact text: the bridge never re-encodes a signed message
    sealed = json.loads(relayed)["body"]
    assert set(sealed) == {"enc", "data"} and SECRET_ARG not in relayed
    header, body = node.open(relayed)
    assert header["call_id"] == "call-0001" and body["args"] == {"text": SECRET_ARG}
    assert relay.link.status()["busy_tool"] == "type_text_tool"

    await relay.link.on_msg("sid-node", node.reply("ack", agent.peer_id, "call-0001", "type_text_tool",
                                                   {"accepted": True, "risk": "medium", "cls": "input"}))
    await relay.link.on_msg("sid-node", node.reply("result", agent.peer_id, "call-0001", "type_text_tool",
                                                   {"status": "ok", "result": {"status": "success",
                                                                               "echo": SECRET_ARG}}))
    ack, result = relay.sio.msgs("sid-agent")
    assert agent.open(ack)[1]["accepted"] is True
    assert agent.open(result)[1]["result"]["echo"] == SECRET_ARG
    assert "busy_tool" not in relay.link.status() and relay.link._calls == {}

    # nothing the bridge kept or showed holds the plaintext
    stored = json.dumps([await relay.store.recent_audit(limit=500), await relay.store.kv_get_many(
        (KV_NODE_VITALS, KV_NODE_STATE, KV_NODE_ID, KV_DESKTOP_STOP))], default=str)
    assert SECRET_ARG not in stored and SECRET_ARG not in json.dumps(relay.ui, default=str)
    assert all(SECRET_ARG not in str(sent[1]) for sent in relay.sio.sent)


async def test_routing_follows_to_and_the_call_id_map(relay):
    node, job1 = await linked(relay)
    job2 = relay.agent(peer_id="job2")
    await connect_agent(relay, job2, sid="sid-agent2")
    assert json.loads(relay.sio.msgs("sid-node")[-1])["from"] == "agent:job2"  # its agent_hello reached the node
    # the node answers a late agent peer with a hello addressed to it alone
    await relay.link.on_msg("sid-node", node.hello(to="agent:job2"))
    [unicast] = relay.sio.msgs("sid-agent2")
    assert relay.sio.msgs("sid-agent") == []
    job2.accept(unicast)
    await relay.link.on_msg("sid-agent2", job2.agent_hello(sess=node.sess))
    node.accept(relay.sio.msgs("sid-node")[-1])
    relay.sio.clear()

    await relay.link.on_msg("sid-agent", job1.exec("call-a0001"))
    await relay.link.on_msg("sid-agent2", job2.exec("call-b0001", tool="press_key_tool", args={"key": "a"}))
    assert len(relay.sio.msgs("sid-node")) == 2
    await relay.link.on_msg("sid-node", node.reply("result", "job2", "call-b0001", "press_key_tool",
                                                   {"status": "ok", "result": {"status": "success"}}))
    assert len(relay.sio.msgs("sid-agent2")) == 1 and relay.sio.msgs("sid-agent") == []
    # a reply for an agent peer that is not connected is dropped
    ghost_key = dp.derive_session_key(dp.new_ephemeral_key(), dp.eph_public_hex(dp.new_ephemeral_key()),
                                      sess=node.sess, peer_id="ghost")
    ghost = text(node.signer.build("progress", {"call_id": "call-g0001"}, to="agent:ghost", sess=node.sess,
                                   room=ROOM, origin="llm", call_id="call-g0001", tool="x", session_key=ghost_key))
    sent_before = len(relay.sio.sent)
    await relay.link.on_msg("sid-node", ghost)
    assert len(relay.sio.sent) == sent_before

    # the node drops after acking call-a0001: dl_undeliverable {emitted: true, acked: true} for that call only
    await relay.link.on_msg("sid-node", node.reply("ack", "job1", "call-a0001", "type_text_tool",
                                                   {"accepted": True, "risk": "medium", "cls": "input"}))
    await relay.link.on_disconnect("sid-node")
    [undeliverable] = relay.sio.hints("sid-agent", "dl_undeliverable")
    assert dict(undeliverable.body) == {"call_id": "call-a0001", "emitted": True, "acked": True}
    assert relay.sio.hints("sid-agent2", "dl_undeliverable") == []
    assert relay.link.status()["state"] == "offline"

    # with no node, a new call is reported as never emitted; a call that was relayed before as emitted
    await relay.link.on_msg("sid-agent", job1.exec("call-a0002"))
    await relay.link.on_msg("sid-agent", job1.exec("call-a0001"))
    first, second = relay.sio.hints("sid-agent", "dl_undeliverable")[1:]
    assert (first.body["call_id"], first.body["emitted"]) == ("call-a0002", False)
    assert (second.body["call_id"], second.body["emitted"]) == ("call-a0001", True)


async def test_pings_and_pongs_are_relayed_and_time_the_round_trip(relay):
    node, agent = await linked(relay)
    ping = agent.ping()
    await relay.link.on_msg("sid-agent", ping)
    assert relay.sio.msgs("sid-node") == [ping]
    relay.clock.now += 0.04
    await relay.link.on_msg("sid-node", node.pong(ping, agent.peer_id))
    assert len(relay.sio.msgs("sid-agent")) == 1
    assert relay.link.status()["rtt_ms_p50"] == pytest.approx(40.0, abs=0.5)


async def test_only_calls_still_in_flight_are_failed_when_the_node_drops(relay):
    node, agent = await linked(relay)
    await relay.link.on_msg("sid-agent", agent.exec("call-q0001"))
    # a refusal is one sealed ack and nothing more (K-70); the bridge cannot tell it from an accept
    await relay.link.on_msg("sid-node", node.reply("ack", agent.peer_id, "call-q0001", "type_text_tool",
                                                   {"accepted": False, "code": "DESKTOP_LOCKED"}))
    relay.clock.now += bridge_desktop.CALL_ACTIVE_S + 1
    await relay.link.on_msg("sid-agent", agent.exec("call-q0002"))
    await relay.link.on_disconnect("sid-node")
    assert [dict(h.body) for h in relay.sio.hints("sid-agent", "dl_undeliverable")] == [
        {"call_id": "call-q0002", "emitted": True, "acked": False}]
    assert relay.link._calls == {}


# ---------------------------------------------------------------- refusals, alerts, replay, sessions


async def test_forged_replayed_or_missing_hellos_are_refused_with_the_critical_alert(relay):
    node = relay.node()
    forged = text(dp.LinkSigner("pc:laptop1", FORGER_KEY).build(
        "hello", dp.node_hello_body(node_id="laptop1", eph=node.eph, sess=node.sess), to="agent:*", sess=node.sess))
    assert await relay.link.on_connect("sid-1", PUBLIC, {"hello": forged}) is False
    [alert] = ui_events(relay, "system_alert")
    assert (alert["severity"], alert["code"], alert["count"]) == ("critical", "SIGNATURE_INVALID", 1)
    assert set(alert) == {"id", "severity", "code", "title", "count", "ts"}
    [row] = await audit(relay, "desktop_alert")
    assert row["detail"]["code"] == "SIGNATURE_INVALID" and row["ip"] == NODE_IP

    hello = node.hello()
    assert await relay.link.on_connect("sid-2", PUBLIC, {"hello": hello}) is True
    await relay.link.drain()
    await relay.link.on_disconnect("sid-2")
    assert await relay.link.on_connect("sid-3", PUBLIC, {"hello": hello}) is False  # the same hello again
    assert await relay.link.on_connect("sid-4", PUBLIC, None) is False
    assert await relay.link.on_connect("sid-5", PUBLIC, {"hello": "{not json"}) is False
    codes = [a["code"] for a in ui_events(relay, "system_alert")]
    assert codes[0] == "SIGNATURE_INVALID" and "HELLO_INVALID" in codes
    assert all(a["severity"] == "critical" for a in ui_events(relay, "system_alert"))
    assert relay.link.node_sid is None and "sid-3" not in relay.link.peers
    await relay.link._flush_folds(force=True)
    rows = await audit(relay, "DL_REJECT")
    assert {"signature", "replay", "format"} <= {row["detail"]["reason"] for row in rows}
    assert all(set(row["detail"]) <= {"reason", "code", "authentic", "typ", "count"} for row in rows)


async def test_each_listener_takes_only_its_own_role(relay):
    node, agent = relay.node(), relay.agent()
    # an agent hello on the public listener and a node hello on the internal one fail verification there
    assert await relay.link.on_connect("sid-a", PUBLIC, {"hello": agent.agent_hello()}) is False
    assert await relay.link.on_connect("sid-n", INTERNAL, {"hello": node.hello()}) is False
    assert relay.link.peers == {}
    assert [a["code"] for a in ui_events(relay, "system_alert")] == ["SIGNATURE_INVALID"]  # folded: count 2
    assert relay.link._alerts[("bridge", "SIGNATURE_INVALID")].alert["count"] == 2
    await connect_agent(relay, agent)
    await connect_node(relay, node)
    assert relay.link.peers["sid-agent"].role == "agent" and relay.link.peers["sid-node"].role == "pc"


async def test_replayed_and_cross_session_messages_are_refused(relay):
    node, agent = await linked(relay)
    exec_text = agent.exec("call-r0001")
    await relay.link.on_msg("sid-agent", exec_text)
    await relay.link.on_msg("sid-agent", exec_text)  # replay: never relayed twice
    assert relay.sio.msgs("sid-node") == [exec_text]

    old_session = agent.session
    node.reconnect()  # a new connection rotates the session
    await relay.link.on_disconnect("sid-node")
    await connect_node(relay, node, sid="sid-node2")
    relay.sio.clear()
    await relay.link.on_msg("sid-agent", agent.exec("call-r0002", session=old_session))
    assert relay.sio.msgs("sid-node2") == []
    [hint] = relay.sio.hints("sid-agent", "dl_undeliverable")
    assert dict(hint.body) == {"call_id": "call-r0002", "emitted": False, "acked": False}

    # a notice from the node in its old session is refused too
    stale_session = text(node.signer.build("notice", {"kind": "desktop_state", "mode": "read", "locked": False,
                                                      "panic": False}, to="bridge", sess=old_session.sess))
    await relay.link.on_msg("sid-node2", stale_session)
    assert relay.link.mode == "full"
    await relay.link._flush_folds(force=True)
    rows = await audit(relay, "DL_REJECT")
    by_reason = {row["detail"]["reason"]: row for row in rows}
    assert by_reason["replay"]["detail"]["count"] == 1 and by_reason["session"]["actor"] in (
        "agent:job1", "pc:laptop1")
    assert all(SECRET_ARG not in json.dumps(row) for row in rows)


async def test_dl_reject_rows_fold_per_reason_and_socket(relay):
    node, agent = await linked(relay)
    exec_text = agent.exec("call-f0001")
    await relay.link.on_msg("sid-agent", exec_text)
    for _ in range(5):
        await relay.link.on_msg("sid-agent", exec_text)
    await relay.link.on_msg("sid-agent", "[]")
    assert await audit(relay, "DL_REJECT") == []  # written when the 60 s window ends
    relay.clock.now += 61
    await relay.link.tick()
    rows = await audit(relay, "DL_REJECT")
    assert sorted((row["detail"]["reason"], row["detail"]["count"]) for row in rows) == [("format", 1),
                                                                                       ("replay", 5)]
    assert all(row["actor"] == "agent:job1" and row["ip"] == AGENT_IP for row in rows)
    assert node.sess  # the node session is untouched by an agent's refusals
    assert relay.link._calls == {}  # the unanswered exec went quiet for longer than CALL_ACTIVE_S


async def test_a_bad_signature_on_a_verified_socket_is_critical(relay):
    node, agent = await linked(relay)
    envelope = json.loads(agent.exec("call-s0001"))
    envelope["sig"] = "0" * 128
    await relay.link.on_msg("sid-agent", json.dumps(envelope))
    assert relay.sio.msgs("sid-node") == []
    [alert] = ui_events(relay, "system_alert")
    assert (alert["severity"], alert["code"]) == ("critical", "SIGNATURE_INVALID")
    # the node is told (alert hint), and a forged message from the node's socket counts the same way
    [hint] = relay.sio.hints("sid-node", "status")
    assert hint.body["alert"]["code"] == "SIGNATURE_INVALID"
    forged = text(dp.LinkSigner(node.ident, FORGER_KEY).build("notice", {"kind": "power", "state": "suspend"},
                                                               to="bridge", sess=node.sess))
    await relay.link.on_msg("sid-node", forged)
    assert relay.link.status()["state"] == "online"
    assert relay.link._alerts[("bridge", "SIGNATURE_INVALID")].alert["count"] == 2


async def test_clock_skew_warns_and_never_counts_for_the_ip_limit(relay):
    node = relay.node()
    future = node.hello(now=relay.clock.now + 200)
    assert await relay.link.on_connect("sid-1", PUBLIC, {"hello": future}) is False
    [alert] = ui_events(relay, "system_alert")
    assert (alert["severity"], alert["code"]) == ("warning", "CLOCK_SKEW")
    assert re.search(r"\+200 s", alert["title"])
    assert NODE_IP not in relay.link._failures

    await connect_node(relay, node)
    relay.ui.clear()
    relay.clock.now += 61  # a new fold window
    stale = node.notice("desktop_state", mode="read", locked=False, panic=False, now=relay.clock.now - 300)
    await relay.link.on_msg("sid-node", stale)
    [alert] = ui_events(relay, "system_alert")
    assert (alert["severity"], alert["code"]) == ("warning", "CLOCK_SKEW") and "-300 s" in alert["title"]
    assert relay.link.mode == "full" and NODE_IP not in relay.link._failures


async def test_failed_hellos_are_limited_per_ip_but_a_hello_that_verifies_always_passes(relay):
    node = relay.node()
    forger = dp.LinkSigner("pc:laptop1", FORGER_KEY)

    def forged() -> str:
        return text(forger.build("hello", dp.node_hello_body(node_id="laptop1", eph=node.eph, sess=node.sess),
                                 to="agent:*", sess=node.sess))

    # nginx's X-Real-IP behind loopback; a forger that reaches the proxy may name the laptop's own address
    laptop = environ(PUBLIC_PORT, "127.0.0.1", real_ip="203.0.113.50")
    for n in range(bridge_desktop.FAILED_HELLO_LIMIT):
        assert await relay.link.on_connect(f"sid-f{n}", laptop, {"hello": forged()}) is False
    with pytest.raises(socketio.exceptions.ConnectionRefusedError, match="rate_limited"):
        await relay.link.on_connect("sid-f-next", laptop, {"hello": forged()})
    assert "LINK_RATE_LIMITED" in [a["code"] for a in ui_events(relay, "system_alert")]
    # an authentic hello is judged on its own: a skewed clock stays the warning, never 'rate_limited'
    assert await relay.link.on_connect("sid-skew", laptop, {"hello": node.hello(now=relay.clock.now + 200)}) is False
    assert "CLOCK_SKEW" in [a["code"] for a in ui_events(relay, "system_alert")]
    # the laptop's real hello from that address passes: a verified node is never throttled
    await connect_node(relay, node, env=laptop)
    assert relay.link.node_sid == "sid-node" and relay.link.peers["sid-node"].ip == "203.0.113.50"
    assert not relay.link._throttled("203.0.113.50")
    # X-Real-IP from a peer that is not the proxy is not believed
    assert bridge_desktop.client_ip(environ(PUBLIC_PORT, NODE_IP, real_ip="203.0.113.99")) == NODE_IP


async def test_refusal_rows_are_capped_whatever_the_number_of_addresses(relay):
    node = relay.node()
    forger = dp.LinkSigner("pc:laptop1", FORGER_KEY)
    burst, extra = int(bridge_desktop.AUDIT_ROWS_BURST), 12
    for n in range(burst + extra):  # each from another forged address, so the per-IP limit never trips
        spoofed = environ(PUBLIC_PORT, "127.0.0.1", real_ip=f"198.51.100.{n + 1}")
        forged = text(forger.build("hello", dp.node_hello_body(node_id="laptop1", eph=node.eph, sess=node.sess),
                                   to="agent:*", sess=node.sess))
        assert await relay.link.on_connect(f"sid-{n}", spoofed, {"hello": forged}) is False
    assert relay.link._alerts[("bridge", "SIGNATURE_INVALID")].alert["count"] == burst + extra
    await relay.link._flush_folds(force=True)
    rows = await audit(relay, "DL_REJECT")
    capped = [row for row in rows if row["detail"].get("capped")]
    assert len(rows) - len(capped) == burst
    assert [(row["actor"], row["detail"]["count"]) for row in capped] == [("bridge", extra)]


async def test_a_newer_node_socket_replaces_the_older_and_a_new_node_id_warns(relay):
    node = relay.node()
    await connect_node(relay, node)
    node.reconnect()
    await connect_node(relay, node, sid="sid-node2")
    assert relay.sio.disconnected == ["sid-node"] and relay.link.node_sid == "sid-node2"
    assert [a["code"] for a in ui_events(relay, "system_alert")] == ["DESKTOP_REPLACED"]
    assert relay.link.status()["online"] is True  # the old socket's disconnect did not take the node offline
    assert await relay.store.kv_get(KV_NODE_ID) == "laptop1"

    other = relay.node(node_id="laptop2")
    await connect_node(relay, other, sid="sid-node3")
    codes = [a["code"] for a in ui_events(relay, "system_alert")]
    assert "NEW_NODE_ID" in codes and relay.link.status()["node_id"] == "laptop2"
    assert await relay.store.kv_get(KV_NODE_ID) == "laptop2"
    warnings = await audit(relay, "desktop_alert")
    assert {row["detail"]["code"] for row in warnings} == {"DESKTOP_REPLACED", "NEW_NODE_ID"}


async def test_a_failed_in_band_hello_is_critical_and_alerts_fold_per_source(relay):
    node, agent = await linked(relay)
    hello = node.hello()
    await relay.link.on_msg("sid-node", hello)  # an in-band hello of the current session
    relay.ui.clear()
    await relay.link.on_msg("sid-node", hello)  # replayed on the verified socket: critical, as at connect
    [alert] = ui_events(relay, "system_alert")
    assert (alert["severity"], alert["code"]) == ("critical", "HELLO_INVALID")

    # the laptop's SIGNATURE_INVALID and the bridge's own fold apart, so each writes its audit row
    await relay.link.on_msg("sid-node", node.notice("alert", code="SIGNATURE_INVALID", level="critical"))
    envelope = json.loads(agent.exec("call-s0002"))
    envelope["sig"] = "0" * 128
    await relay.link.on_msg("sid-agent", json.dumps(envelope))
    rows = [row for row in await audit(relay, "desktop_alert") if row["detail"]["code"] == "SIGNATURE_INVALID"]
    assert sorted(row["actor"] for row in rows) == ["bridge", "node"]
    alerts = [a for a in ui_events(relay, "system_alert") if a["code"] == "SIGNATURE_INVALID"]
    assert len({a["id"] for a in alerts}) == 2
    hinted = [h.body["alert"]["code"] for h in relay.sio.hints("sid-node", "status") if "alert" in h.body]
    assert hinted[-1] == "SIGNATURE_INVALID"  # the bridge's detection reaches the laptop's toast too


async def test_a_laptop_alert_message_reaches_the_ui_and_the_audit_but_never_the_log(relay, bridge_log):
    node = (await linked(relay))[0]
    message = "NOTICE-BODY-TEXT C:/Users/x/secret.txt"
    await relay.link.on_msg("sid-node", node.notice("alert", code="CLOCK_SKEW", level="warning", message=message))
    [alert] = ui_events(relay, "system_alert")
    assert alert["title"] == message
    [row] = await audit(relay, "desktop_alert")
    assert row["detail"]["message"] == message
    assert "CLOCK_SKEW" in bridge_log.text() and "NOTICE-BODY-TEXT" not in bridge_log.text()


# ---------------------------------------------------------------- notices and desktop_status


async def test_notices_drive_desktop_status_and_are_never_relayed(relay):
    node = (await linked(relay))[0]
    status = relay.link.status()
    assert (status["state"], status["online"], status["mode"], status["node_id"], status["version"],
            status["git_sha"], status["manifest_ok"], status["brain"]) == (
        "online", True, "full", "laptop1", "1.4.0", "abc1234", True, "pc")
    assert set(status) == {"online", "state", "mode", "locked", "voice_active", "node_id", "version", "git_sha",
                           "manifest_ok", "since", "last_seen", "rtt_ms_p50", "brain"}

    await relay.link.on_msg("sid-node", node.notice("vitals_pc", cpu=12.5, ram=40, disk=71.2, cores=8))
    [vitals] = ui_events(relay, "vitals_pc")
    assert vitals == {"cpu": 12.5, "ram": 40, "disk": 71.2, "cores": 8, "host": "laptop1",
                      "ts": round(relay.clock.now, 3)}
    assert ui_events(relay, "vitals_update") == [vitals] and await relay.store.kv_get(KV_NODE_VITALS) == vitals
    assert relay.link.fresh_pc_vitals() == vitals

    await relay.link.on_msg("sid-node", node.notice("desktop_state", mode="full", locked=True, panic=False))
    assert relay.link.status()["state"] == "locked"
    await relay.link.on_msg("sid-node", node.notice("desktop_state", mode="off", locked=False, panic=True))
    assert relay.link.status()["state"] == "panic" and relay.link.status()["mode"] == "off"
    assert (await relay.store.kv_get(KV_NODE_STATE))["panic"] is True
    await relay.link.on_msg("sid-node", node.notice("desktop_state", mode="full", locked=False, panic=False))
    await relay.link.on_msg("sid-node", node.notice("power", state="suspend"))
    assert relay.link.status()["state"] == "offline"
    assert (await relay.store.kv_get(KV_NODE_POWER))["state"] == "suspend"
    await relay.link.on_msg("sid-node", node.notice("power", state="resume"))
    assert relay.link.status()["state"] == "online"

    await relay.link.on_msg("sid-node", node.notice("alert", code="MANIFEST_MISMATCH", level="warning", count=2))
    assert relay.link.status()["manifest_ok"] is False
    [alert] = [a for a in ui_events(relay, "system_alert") if a["code"] == "MANIFEST_MISMATCH"]
    assert (alert["severity"], alert["count"], alert["title"]) == (
        "warning", 2, bridge_desktop.NODE_ALERT_TITLES["MANIFEST_MISMATCH"])
    rows = await audit(relay, "desktop_alert")
    assert [row["detail"]["code"] for row in rows] == ["MANIFEST_MISMATCH"] and rows[0]["actor"] == "node"

    # the agent peers got status hints only: no notice was relayed, and only alert notices wrote audit rows
    assert relay.sio.msgs("sid-agent") == []
    assert {hint.typ for hint in relay.sio.hints("sid-agent")} == {"status"}
    assert len(await relay.store.recent_audit(limit=500)) == 1
    assert [s["state"] for s in ui_events(relay, "desktop_status")] == [
        "locked", "panic", "online", "offline", "online", "online"]  # the last one: manifest_ok turned false

    # stale after 25 s without a counted message
    relay.clock.now += 26
    await relay.link.tick()
    assert relay.link.status()["state"] == "stale" and ui_events(relay, "desktop_status")[-1]["state"] == "stale"
    await relay.link.on_msg("sid-node", node.notice("desktop_state", mode="full", locked=False, panic=False,
                                                    now=relay.clock.now))
    assert relay.link.status()["state"] == "online"
    relay.clock.now += 5  # the vitals are now 31 s old: no longer fresh, still the last reading
    assert relay.link.fresh_pc_vitals() is None and relay.link.latest_pc_vitals() == vitals


async def test_the_node_notice_rate_is_enforced(relay):
    node = (await linked(relay))[0]
    limit = bridge_desktop.NOTICES_PER_S
    for _ in range(limit + 5):
        await relay.link.on_msg("sid-node", node.notice("vitals_pc", cpu=10, ram=20, disk=30))
    assert len(ui_events(relay, "vitals_pc")) == limit
    assert [a["code"] for a in ui_events(relay, "system_alert")] == ["LINK_RATE_LIMITED"]
    await relay.link._flush_folds(force=True)
    [row] = await audit(relay, "DL_REJECT")
    assert (row["detail"]["reason"], row["detail"]["typ"], row["detail"]["count"]) == ("rate", "notice", 5)
    relay.clock.now += 1.0  # 20 a second again
    await relay.link.on_msg("sid-node", node.notice("vitals_pc", cpu=11, ram=20, disk=30))
    assert len(ui_events(relay, "vitals_pc")) == limit + 1


async def test_desktop_state_reaches_the_kv_also_when_the_hello_reported_it_first(relay):
    node = relay.node()
    await connect_node(relay, node, mode="full")
    await relay.link.on_msg("sid-node", node.notice("desktop_state", mode="full", locked=False, panic=False))
    assert (await relay.store.kv_get(KV_NODE_STATE))["mode"] == "full"
    node.reconnect()
    await connect_node(relay, node, sid="sid-node2", mode="read")
    await relay.link.on_msg("sid-node2", node.notice("desktop_state", mode="read", locked=False, panic=False))
    assert (await relay.store.kv_get(KV_NODE_STATE))["mode"] == "read"
    await relay.link.stop()
    relay.link = await relay.start(relay.make())
    assert relay.link.mode == "read"


async def test_a_node_message_waits_for_its_hello_work(relay):
    node = relay.node()
    assert await relay.link.on_connect("sid-node", PUBLIC, {"hello": node.hello()}) is True
    # no drain: the notice must still be handled after the hello (welcome first, then the state)
    await relay.link.on_msg("sid-node", node.notice("desktop_state", mode="read", locked=True, panic=False))
    assert [hint.typ for hint in relay.sio.hints("sid-node")][0] == "welcome"
    assert relay.link.status()["state"] == "locked" and relay.link.mode == "read"


# ---------------------------------------------------------------- controls: stop, link switch, active job


async def test_ui_desktop_stop_is_persisted_resent_after_reconnect_and_cleared_by_read(relay):
    node = (await linked(relay))[0]
    await relay.link.ui_desktop_stop(by="ui")

    assert (await relay.store.kv_get(KV_DESKTOP_STOP))["by"] == "ui"
    [control] = relay.sio.hints("sid-node", "control")
    assert dict(control.body) == {"kind": "desktop_stop"}
    [status] = relay.sio.hints("sid-agent", "status")
    assert status.body["stop_all"] is True and status.body["desktop_status"]["node_id"] == "laptop1"
    assert ui_events(relay, "desktop_status")
    [row] = await audit(relay, "desktop_stop")
    assert row["actor"] == "ui"

    # a reconnect that still reports full gets the stop again; it survives a bridge restart too
    await relay.link.stop()
    relay.link = await relay.start(relay.make())
    assert relay.link.stop_pending is not None
    node.reconnect()
    relay.sio.clear()
    await connect_node(relay, node, sid="sid-node2")
    assert [dict(h.body) for h in relay.sio.hints("sid-node2", "control")] == [{"kind": "desktop_stop"}]

    # the node reports read: the stop is done and is not sent again
    await relay.link.on_msg("sid-node2", node.notice("desktop_state", mode="read", locked=False, panic=False))
    assert relay.link.stop_pending is None and await relay.store.kv_get(KV_DESKTOP_STOP) is None
    node.reconnect()
    relay.sio.clear()
    await connect_node(relay, node, sid="sid-node3", mode="full")
    assert relay.sio.hints("sid-node3", "control") == []


async def test_a_hello_that_reports_read_ends_a_pending_stop(relay):
    await relay.link.ui_desktop_stop(by="legacy")
    node = relay.node()
    await connect_node(relay, node, mode="read")
    assert relay.link.stop_pending is None and relay.sio.hints("sid-node", "control") == []


async def test_link_off_survives_a_restart_and_the_cli_reports_it(relay, capsys):
    node, agent = await linked(relay)
    db = str(relay.path / "bridge.db")
    assert await asyncio.to_thread(bridge_desktop.main, ["link", "off", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "desktop_link: off" in out and "within 5 s" in out
    assert dp.public_key_hex(PC_KEY) not in out and dp.public_key_hex(AGENT_KEY) not in out

    # the running relay picks the switch up from the kv
    relay.clock.now += bridge_desktop.LINK_POLL_S
    await relay.link.tick()
    assert [dict(h.body) for h in relay.sio.hints("sid-node", "control")] == [{"kind": "link_disabled"}]
    assert relay.sio.disconnected == ["sid-node"] and relay.link.node_sid is None
    assert relay.sio.hints("sid-agent", "status")[-1].body["link"] == "off"
    await relay.link.on_msg("sid-agent", agent.exec("call-l0001"))
    assert relay.sio.hints("sid-agent", "dl_undeliverable")[-1].body["emitted"] is False

    # a restart keeps it off; the node is refused until the switch is on again
    await relay.link.stop()
    relay.link = await relay.start(relay.make())
    node.reconnect()
    with pytest.raises(socketio.exceptions.ConnectionRefusedError, match="link_disabled"):
        await relay.link.on_connect("sid-node2", PUBLIC, {"hello": node.hello()})
    [row] = await audit(relay, "desktop_link")
    assert (row["actor"], row["detail"]) == ("admin_cli", {"state": "off"})

    assert await asyncio.to_thread(bridge_desktop.main, ["link", "status", "--db", db]) == 0
    assert "desktop_link: off" in capsys.readouterr().out
    assert await asyncio.to_thread(bridge_desktop.main, ["link", "on", "--db", db]) == 0
    capsys.readouterr()
    relay.clock.now += bridge_desktop.LINK_POLL_S
    await relay.link.tick()
    await connect_node(relay, node, sid="sid-node3")
    assert relay.link.status()["online"] is True


async def test_the_link_stays_off_until_the_persisted_controls_can_be_read(relay, monkeypatch):
    await relay.link.stop()
    broken = {"now": True}
    read = relay.store.kv_get_many

    async def kv_get_many(keys: Any) -> dict[str, Any]:
        if broken["now"]:
            raise sqlite3.OperationalError("disk I/O error")
        return await read(keys)

    monkeypatch.setattr(relay.store, "kv_get_many", kv_get_many)
    relay.link = await relay.start(relay.make())
    assert relay.link.active and relay.link.link_on is False  # fail closed, never the default 'on'
    node, agent = relay.node(), relay.agent()
    with pytest.raises(socketio.exceptions.ConnectionRefusedError, match="link_disabled"):
        await relay.link.on_connect("sid-node", PUBLIC, {"hello": node.hello()})
    await connect_agent(relay, agent)
    relay.clock.now += bridge_desktop.LINK_POLL_S
    await relay.link.tick()
    assert relay.link.link_on is False
    broken["now"] = False
    relay.clock.now += bridge_desktop.LINK_POLL_S
    await relay.link.tick()
    assert relay.link.link_on is True and relay.sio.hints("sid-agent", "status")[-1].body["link"] == "on"
    await connect_node(relay, node)


async def test_an_off_switch_the_kv_lost_stays_off(relay):
    await relay.link.set_link(False)
    # the kv lost it while the bridge ran (a failed write, a corrupt bridge.db renamed aside): written again
    await relay.store.kv_delete(KV_DESKTOP_LINK)
    relay.clock.now += bridge_desktop.LINK_POLL_S
    await relay.link.tick()
    assert relay.link.link_on is False and await relay.store.kv_get(KV_DESKTOP_LINK) == "off"

    # C-13 renames a corrupt bridge.db aside at boot: the switch it held is gone and counts as off, never on
    await relay.link.stop()
    await relay.store.close()
    (relay.path / "bridge.db").write_bytes(b"this is not a database " * 200)
    relay.link = await relay.start(relay.make())
    assert relay.store.corrupt_renamed == 1
    assert relay.link.link_on is False and await relay.store.kv_get(KV_DESKTOP_LINK) == "off"
    [row] = await audit(relay, "desktop_link")
    assert (row["actor"], row["detail"]["state"]) == ("bridge", "off")


def test_the_cli_needs_an_existing_database(tmp_path, capsys):
    assert bridge_desktop.main(["link", "status", "--db", str(tmp_path / "missing.db")]) == 1
    assert "no bridge database" in capsys.readouterr().err
    assert not (tmp_path / "missing.db").exists()


async def test_active_job_and_owner_activity_are_persisted_and_reapplied(relay):
    node = relay.node()
    await connect_node(relay, node)
    await relay.link.set_active_job("job-42")
    assert await relay.store.kv_get(KV_ACTIVE_JOB) == "job-42"
    assert [dict(h.body) for h in relay.sio.hints("sid-node", "control")] == [{"kind": "active_job",
                                                                                "job_id": "job-42"}]
    await relay.link.note_owner_activity()
    first = await relay.store.kv_get(KV_LAST_ACTIVITY_TS)
    relay.clock.now += 10
    await relay.link.note_owner_activity()  # at most one write a minute
    assert await relay.store.kv_get(KV_LAST_ACTIVITY_TS) == first
    assert relay.link.last_activity_ts == relay.clock.now

    await relay.link.stop()
    relay.link = await relay.start(relay.make())
    assert relay.link.active_job == "job-42" and relay.link.last_activity_ts == first
    node.reconnect()
    relay.sio.clear()
    await connect_node(relay, node, sid="sid-node2")
    [welcome] = relay.sio.hints("sid-node2", "welcome")
    assert welcome.body["active_job"] == "job-42" and welcome.body["link"] == "on"
    assert [dict(h.body) for h in relay.sio.hints("sid-node2", "control")] == [{"kind": "active_job",
                                                                                 "job_id": "job-42"}]


# ---------------------------------------------------------------- ui_bridge: '/', HTTP and the internal listener


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store, throwaway credentials, an active relay (FakeSio on '/desktop') and a
    recorded sio.emit for '/'."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    monkeypatch.setenv("JARVIS_BRAIN", "pc")
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    sio = FakeSio()
    link = DesktopLink(sio=sio, store=lambda: module.store, emit_ui=module.emit_ui, keys=KEYS,
                       seen_path=str(tmp_path / "desktop_seen.db"))
    sio.link = link
    for name, value in (("store", store), ("bridge_manager", module.BridgeStateManager()), ("_clients", {}),
                        ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN), ("desktop", link)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True), ("env", "development")):
        monkeypatch.setattr(config, name, value)
    await link.start()
    link._task.cancel()
    await asyncio.gather(link._task, return_exceptions=True)
    link._task = None
    ns = SimpleNamespace(module=module, store=store, emitted=emitted, link=link, sio=sio, path=tmp_path)
    yield ns
    await link.stop()
    await store.close()


def http(ns: Any, *, internal: bool = False) -> httpx.AsyncClient:
    base = f"http://bridge.test:{INTERNAL_PORT}" if internal else "http://bridge.test"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=ns.module.app), base_url=base)


async def test_public_health_is_only_status_version_and_uptime(bridge):
    async with http(bridge) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"status", "version", "uptime_s"} and body["status"] == "ok"
    assert isinstance(body["uptime_s"], int) and body["uptime_s"] >= 0


async def test_internal_paths_exist_only_on_the_internal_listener(bridge):
    async def probe() -> dict[str, str]:
        return {"ok": "internal"}

    app = bridge.module.app
    app.add_api_route("/internal/c116-probe", probe, methods=["GET"])
    try:
        async with http(bridge) as public, http(bridge, internal=True) as internal:
            assert (await public.get("/internal/c116-probe")).status_code == 404
            assert (await public.get("/internal/c116-probe", headers={"X-Vortex-Token": LEGACY_TOKEN})
                    ).status_code == 404
            assert (await internal.get("/internal/c116-probe")).json() == {"ok": "internal"}
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/internal/c116-probe"]


async def test_the_internal_listener_takes_unsigned_agent_notifies_and_never_the_legacy_token(bridge):
    raw = json.dumps({"type": "transcription", "payload": {"id": "i-1", "role": "agent", "text": "Ji"}}).encode()
    async with http(bridge, internal=True) as internal, http(bridge) as public:
        assert (await internal.post("/notify", content=raw)).json() == {"status": "ok"}
        assert ("new_message", "ui") in [(e, room) for e, _d, room in bridge.emitted]
        legacy = await internal.post("/notify", content=raw, headers={"X-Vortex-Token": LEGACY_TOKEN,
                                                                      "X-Vortex-Signature": "0" * 64})
        assert legacy.status_code == 401
        assert (await internal.post("/notify", content=b"[1, 2]")).status_code == 400
        assert (await internal.post("/notify", content=b"x" * (256 * 1024 + 1))).status_code == 413
        assert (await public.post("/notify", content=raw)).status_code == 401
        assert (await internal.get("/messages", headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 401
        assert (await public.get("/messages", headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 200


async def test_status_has_the_desktop_block_and_needs_credentials(bridge):
    async with http(bridge) as public, http(bridge, internal=True) as internal:
        assert (await public.get("/status")).status_code == 401
        body = (await internal.get("/status")).json()
        assert body["desktop"]["state"] == "offline" and body["brain"] == "pc"
        assert body["status"] == "degraded" and body["db"]["ok"] is True
        assert (await public.get("/status", headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 200

        signed = dp.sign_http_request(PC_KEY, method="GET", path="/vortex/status")
        assert (await public.get("/status", headers=signed)).status_code == 200
        assert (await public.get("/status", headers=signed)).status_code == 401  # the nonce was used
        forged = dp.sign_http_request(FORGER_KEY, method="GET", path="/vortex/status")
        assert (await public.get("/status", headers=forged)).status_code == 401
    await bridge.link._flush_folds(force=True)
    rows = await bridge.store.recent_audit(action_prefix="DL_REJECT")
    assert {json.loads(row["detail"])["reason"] for row in rows} == {"replay", "signature"}


async def test_livekit_token_mints_the_owner_identity_and_ignores_the_client(bridge, monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "c116-lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "c116-lk-secret-" + "d" * 24)
    monkeypatch.setenv("LIVEKIT_URL", "")  # no room ensure: nothing reaches a LiveKit server
    monkeypatch.setenv("LIVEKIT_PUBLIC_URL", "wss://lk.example.test")
    headers = {"X-Vortex-Token": LEGACY_TOKEN}
    async with http(bridge) as client:
        first = (await client.get("/livekit/token?identity=agent-evil", headers=headers)).json()
        second = (await client.get("/livekit/token", headers=headers)).json()
    assert re.fullmatch(r"owner-[0-9a-f]{8}", first["identity"]) and first["identity"] != second["identity"]
    claims = jwt.decode(first["token"], "c116-lk-secret-" + "d" * 24, algorithms=["HS256"],
                        options={"verify_aud": False})
    assert (claims["sub"], claims["name"], claims["attributes"]) == (first["identity"], "Owner", {"role": "owner"})
    assert claims["video"]["room"] == ROOM and first["url"] == "wss://lk.example.test"


async def test_internal_agents_get_ui_commands_only_for_the_owner_room(bridge):
    module = bridge.module
    assert await module.handle_connect("sid-owner", INTERNAL, {"role": "agent", "room": ROOM, "job_id": "j1"})
    assert await module.handle_connect("sid-canary", INTERNAL, {"role": "agent", "room": "canary-1",
                                                                "job_id": "j2"})
    owner, canary = module._clients["sid-owner"], module._clients["sid-canary"]
    assert (owner.role, owner.room, owner.job_id, owner.internal) == ("agent", ROOM, "j1", True)
    assert module._is_owner_agent(owner) and not module._is_owner_agent(canary)
    assert [e for e, _d, _r in bridge.emitted].count("agent_status") == 1  # only the owner-room agent counts
    # no room declared on :5002: never the owner room; a public agent_token without one predates C-15
    assert await module.handle_connect("sid-noroom", INTERNAL, {"role": "agent"})
    assert not module._is_owner_agent(module._clients["sid-noroom"])
    assert await module.handle_connect("sid-pc", PUBLIC, {"agent_token": AGENT_TOKEN, "agent_id": "pc"})
    assert module._is_owner_agent(module._clients["sid-pc"])
    # the internal listener takes neither the legacy token nor an agent_token
    for auth in ({"token": LEGACY_TOKEN}, {"agent_token": AGENT_TOKEN}, None):
        assert await module.handle_connect("sid-bad", INTERNAL, auth) is False
    rows = await bridge.store.recent_audit(action_prefix="socket_auth_failed")
    assert {json.loads(row["detail"])["reason"] for row in rows} == {"internal_agent_only"}
    bridge.emitted.clear()
    await module.handle_disconnect("sid-canary")
    assert not bridge.emitted  # a canary leaving never turns agent_status off


async def test_ui_desktop_stop_and_notification_ack_from_a_ui_socket(bridge):
    module, link = bridge.module, bridge.link
    node = FakeNode(bridge.path)
    try:
        assert await link.on_connect("sid-node", PUBLIC, {"hello": node.hello()}) is True
        await link.drain()
        assert await module.handle_connect("sid-ui", {}, {"token": LEGACY_TOKEN})
        [state] = [data for event, data, _room in bridge.emitted if event == "init_state"]
        assert state["desktop_status"]["state"] == "online" and state["vitals_pc"] is None
        assert await module.handle_connect("sid-agent", {}, {"agent_token": AGENT_TOKEN})
        await bridge.store.add_notification({"id": "rem-1", "description": "tea"})
        bridge.emitted.clear()

        stop = {"type": "desktop_stop", "payload": {}}
        assert await module.handle_ui_command("sid-agent", stop) == {"status": "rejected", "reason": "not_allowed"}
        assert link.stop_pending is None
        assert await module.handle_ui_command("sid-ui", stop) == {"status": "sent"}
        assert link.stop_pending is not None and await bridge.store.kv_get(KV_DESKTOP_STOP) is not None
        assert [dict(h.body) for h in bridge.sio.hints("sid-node", "control")] == [{"kind": "desktop_stop"}]
        assert [e for e, _d, room in bridge.emitted if room == "ui"] == ["desktop_status"]
        assert ("agent_command", stop, "agent") in bridge.emitted

        ack = {"type": "notification_ack", "payload": {"id": "rem-1"}}
        assert await module.handle_ui_command("sid-ui", ack) == {"status": "sent"}
        assert (await bridge.store.get_notification("rem-1"))["acked_by"] == "ui"
        assert isinstance(await bridge.store.kv_get(KV_LAST_ACTIVITY_TS), float)
    finally:
        node.close()


async def test_vitals_update_prefers_a_fresh_laptop_reading(bridge, monkeypatch):
    module, link = bridge.module, bridge.link
    link._vitals, link._vitals_mono = {"cpu": 5, "ram": 6, "disk": 7, "host": "laptop1", "ts": 1.0}, time.monotonic()

    async def first_update() -> Any:
        task = asyncio.create_task(module.vitals_heartbeat())
        try:
            for _ in range(500):
                updates = [d for e, d, _r in bridge.emitted if e == "vitals_update"]
                if updates:
                    return updates[0]
                await asyncio.sleep(0.01)
            raise AssertionError("no vitals_update")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert (await first_update())["host"] == "laptop1"
    bridge.emitted.clear()
    link._vitals_mono = time.monotonic() - bridge_desktop.VITALS_FRESH_S - 1
    assert "host" not in await first_update()  # the bridge's own figures again


async def test_voice_activity_from_notify_events_sets_voice_active(bridge):
    raw = json.dumps({"type": "status", "payload": "START"}).encode()
    assert bridge.link.voice_active() is False
    async with http(bridge, internal=True) as internal:
        assert (await internal.post("/notify", content=raw)).status_code == 200
    assert bridge.link.voice_active() is True and bridge.link.status()["voice_active"] is True


def test_main_opens_the_internal_port_only_for_the_bridge_role(monkeypatch, bridge_log):
    module = importlib.import_module("src.core.ui_bridge")
    bound: list[int] = []
    served: list[list[Any]] = []

    class FakeServer:
        def __init__(self, cfg: Any) -> None:
            self.cfg = cfg

        def run(self, sockets: list[Any]) -> None:
            served.append(sockets)

    monkeypatch.setattr(module, "_listen_socket", lambda port: bound.append(port) or port)
    monkeypatch.setattr(module.uvicorn, "Server", FakeServer)
    monkeypatch.setenv("JARVIS_ROLE", "bridge")
    module.main()
    monkeypatch.setenv("JARVIS_ROLE", "agent")
    module.main()
    assert bound == [5001, 5002, 5001] and served == [[5001, 5002], [5001]]
    # uvicorn's own 'running on' line names only the public port
    assert [r.getMessage() for r in bridge_log.records if "Internal listener" in r.getMessage()] == [
        "Internal listener on 0.0.0.0:5002 (jarvis-network only, never published)"]


def test_main_exits_with_one_error_line_when_a_port_is_taken(monkeypatch, bridge_log):
    module = importlib.import_module("src.core.ui_bridge")

    def taken(port: int) -> Any:
        raise OSError(10048, f"port {port} is in use")

    monkeypatch.setattr(module, "_listen_socket", taken)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 1
    assert [r.levelno for r in bridge_log.records if "Cannot listen" in r.getMessage()] == [logging.ERROR]


@pytest.mark.parametrize("posix", [True, False])
def test_listen_sockets_reuse_the_address_only_on_posix(monkeypatch, posix):
    module = importlib.import_module("src.core.ui_bridge")
    assert module._REUSE_ADDRESS is (os.name == "posix")  # as asyncio's create_server
    options: dict[Any, Any] = {}

    class FakeSocket:  # no real socket: nothing binds on this PC
        def __init__(self, *_args: Any) -> None:
            self.address: Any = None

        def setsockopt(self, level: int, name: int, value: int) -> None:
            options[(level, name)] = value

        def bind(self, address: Any) -> None:
            self.address = address

        def set_inheritable(self, _inheritable: bool) -> None:
            pass

    monkeypatch.setattr(module, "socket", SimpleNamespace(
        socket=FakeSocket, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM, SOL_SOCKET=socket.SOL_SOCKET,
        SO_REUSEADDR=socket.SO_REUSEADDR))
    monkeypatch.setattr(module, "_REUSE_ADDRESS", posix)
    assert module._listen_socket(5001).address == ("0.0.0.0", 5001)
    # on Windows SO_REUSEADDR would let a second bridge bind a port in use instead of failing
    assert ((socket.SOL_SOCKET, socket.SO_REUSEADDR) in options) is posix


def test_compose_and_dockerfile_keep_5002_off_the_host():
    compose = yaml.safe_load((ROOT / "deploy" / "hostinger" / "docker-compose.yml").read_text(encoding="utf-8"))
    bridge_service = compose["services"]["ui-bridge"]
    # the public API stays published (C-131 moves it to 127.0.0.1); 5002 never is
    assert any(str(port).endswith(":5001") for port in bridge_service["ports"])
    assert all("5002" not in str(port) for port in bridge_service["ports"])
    assert [str(port) for port in bridge_service["expose"]] == ["5002"]
    assert bridge_service["oom_score_adj"] == 300
    volumes = bridge_service["volumes"]
    assert "jarvis_outputs:/app/Jarvis_Outputs:ro" in volumes and "jarvis_shared:/app/Jarvis_Shared:ro" in volumes
    assert "jarvis_bridge_data:/app/data" in volumes
    assert bridge_service["environment"]["JARVIS_ROLE"] == "bridge"
    assert bridge_service["environment"]["GIT_SHA"] == "${GIT_SHA:-unknown}"
    assert "DESKTOP_AGENT_PUBLIC_KEY" not in json.dumps(bridge_service)  # bridge.env holds the link keys
    assert bridge_service["healthcheck"]["test"][-1] == "http://127.0.0.1:5001/health"
    dockerfile = (ROOT / "deploy" / "hostinger" / "Dockerfile.bridge").read_text(encoding="utf-8")
    assert re.search(r"^EXPOSE 5001 5002$", dockerfile, re.M)
    assert 'CMD ["python", "-m", "src.core.ui_bridge"]' in dockerfile


# ---------------------------------------------------------------- end to end: uvicorn on two listeners


class Client:
    """A python-socketio client recording every event on '/' and '/desktop'."""

    def __init__(self) -> None:
        self.sio = socketio.AsyncClient(reconnection=False)
        self.events: list[tuple[str, str, Any]] = []
        self._changed = asyncio.Event()
        self.sio.on("*", self._root)
        self.sio.on("*", self._desktop, namespace=bridge_desktop.NAMESPACE)

    async def _root(self, event: str, *args: Any) -> None:
        self.events.append(("/", event, args[0] if args else None))
        self._changed.set()

    async def _desktop(self, event: str, *args: Any) -> None:
        self.events.append((bridge_desktop.NAMESPACE, event, args[0] if args else None))
        self._changed.set()

    async def connect(self, url: str, auth: Any, namespace: str = "/", wait_timeout: float = 5) -> None:
        # a refused namespace makes python-socketio wait the whole wait_timeout before it raises
        await self.sio.connect(url, auth=auth, namespaces=[namespace], transports=["websocket"],
                               wait_timeout=wait_timeout)

    def got(self, namespace: str, event: str) -> list[Any]:
        return [data for ns, name, data in self.events if ns == namespace and name == event]

    async def wait(self, namespace: str, event: str, check: Any = None, timeout: float = 5.0) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            self._changed.clear()
            for data in self.got(namespace, event):
                if check is None or check(data):
                    return data
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(f"no {event!r} on {namespace} within {timeout} s: {self.events[-5:]}")
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                pass


async def _idle() -> None:
    await asyncio.Event().wait()


def _bound() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock


@pytest.fixture
async def live(monkeypatch, tmp_path):
    """The real bridge app on two free 127.0.0.1 ports: one public, one standing in for the internal :5002."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    monkeypatch.setenv("JARVIS_BRAIN", "pc")
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    link = DesktopLink(sio=module.sio, store=lambda: module.store, emit_ui=module.emit_ui, keys=KEYS,
                       seen_path=str(tmp_path / "desktop_seen.db"))
    for name, value in (("store", store), ("bridge_manager", module.BridgeStateManager()), ("_clients", {}),
                        ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN), ("desktop", link)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True), ("env", "development")):
        monkeypatch.setattr(config, name, value)
    for name in ("vitals_heartbeat", "telemetry_heartbeat", "user_speaking_monitor", "store_retention_loop"):
        monkeypatch.setattr(module, name, _idle)
    for name, value in (("start_service_task", True), ("service_task_handle", None),
                        ("service_task_event", None)):
        monkeypatch.setattr(module.sio.eio, name, value)
    public, internal = _bound(), _bound()
    monkeypatch.setattr(bridge_desktop, "INTERNAL_PORT", internal.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(module.app_asgi, log_level="warning", lifespan="on",
                                           timeout_graceful_shutdown=2))
    task = asyncio.create_task(server.serve(sockets=[public, internal]))
    for _ in range(500):
        if (server.started and link.active) or task.done():
            break
        await asyncio.sleep(0.01)
    assert server.started and link.active, "the test bridge did not start"
    clients: list[Client] = []

    def client() -> Client:
        clients.append(Client())
        return clients[-1]

    yield SimpleNamespace(public=f"http://127.0.0.1:{public.getsockname()[1]}",
                          internal=f"http://127.0.0.1:{internal.getsockname()[1]}", module=module, link=link,
                          store=store, client=client, path=tmp_path)
    for c in clients:
        if c.sio.connected:
            await c.sio.disconnect()
    await module.sio.shutdown()
    server.should_exit = True
    await asyncio.wait_for(task, 15)
    public.close()
    internal.close()
    await store.close()


async def test_end_to_end_relay_over_the_two_listeners(live):
    node, agent, stray = FakeNode(live.path), FakeAgent(live.path), FakeAgent(live.path, peer_id="job7")
    try:
        ui, owner, canary = live.client(), live.client(), live.client()
        await ui.connect(live.public, {"token": LEGACY_TOKEN})
        assert (await ui.wait("/", "init_state"))["desktop_status"]["state"] == "offline"
        await owner.connect(live.internal, {"role": "agent", "room": ROOM, "job_id": "job1"})
        await canary.connect(live.internal, {"role": "agent", "room": "canary-1", "job_id": "job9"})

        # the agent peer on the internal listener, the node on the public one
        agent_link, node_link = live.client(), live.client()
        await agent_link.connect(live.internal, {"hello": agent.agent_hello()}, bridge_desktop.NAMESPACE)
        assert dp.parse_bridge_hint(await agent_link.wait(bridge_desktop.NAMESPACE, "hint")).typ == "welcome"
        await node_link.connect(live.public, {"hello": node.hello()}, bridge_desktop.NAMESPACE)
        agent.accept(await agent_link.wait(bridge_desktop.NAMESPACE, "msg"))
        await agent_link.sio.emit("msg", agent.agent_hello(sess=node.sess), namespace=bridge_desktop.NAMESPACE)
        node.accept(await node_link.wait(bridge_desktop.NAMESPACE, "msg"))
        await ui.wait("/", "desktop_status", lambda s: s["state"] == "online")

        # a sealed exec and its result cross the bridge unread
        exec_text = agent.exec("call-e2e001")
        await agent_link.sio.emit("msg", exec_text, namespace=bridge_desktop.NAMESPACE)
        relayed = await node_link.wait(bridge_desktop.NAMESPACE, "msg", lambda m: "call-e2e001" in m)
        assert relayed == exec_text and node.open(relayed)[1]["args"] == {"text": SECRET_ARG}
        await node_link.sio.emit("msg", node.reply("result", "job1", "call-e2e001", "type_text_tool",
                                                   {"status": "ok", "result": {"status": "success"}}),
                                 namespace=bridge_desktop.NAMESPACE)
        result = await agent_link.wait(bridge_desktop.NAMESPACE, "msg",
                                       lambda m: json.loads(m)["typ"] == "result")
        assert agent.open(result)[1]["result"] == {"status": "success"}
        await agent_link.sio.emit("msg", exec_text, namespace=bridge_desktop.NAMESPACE)  # a replay

        # notices reach desktop_status on '/', never another peer
        await node_link.sio.emit("msg", node.notice("desktop_state", mode="full", locked=True, panic=False),
                                 namespace=bridge_desktop.NAMESPACE)
        await ui.wait("/", "desktop_status", lambda s: s["state"] == "locked")

        # the dashboard's Stop desktop reaches the node and the agent peer; chat goes to the owner room only
        assert await ui.sio.call("ui_command", {"type": "desktop_stop", "payload": {}}, timeout=5) == {
            "status": "sent"}
        control = dp.parse_bridge_hint(await node_link.wait(
            bridge_desktop.NAMESPACE, "hint", lambda h: json.loads(h)["typ"] == "control"))
        assert dict(control.body) == {"kind": "desktop_stop"}
        await agent_link.wait(bridge_desktop.NAMESPACE, "hint", lambda h: json.loads(h)["body"].get("stop_all"))
        chat = {"type": "chat", "payload": {"id": "u-1", "text": "salam"}}
        assert await ui.sio.call("ui_command", chat, timeout=5) == {"status": "sent"}
        assert await owner.wait("/", "agent_command", lambda c: c["type"] == "chat") == chat
        assert await owner.sio.call("ui_command", chat, timeout=5) == {"status": "rejected", "reason": "not_allowed"}

        # the agent role is refused on the public listener, and the internal one never takes the legacy token
        for url, auth, namespace in ((live.public, {"hello": stray.agent_hello()}, bridge_desktop.NAMESPACE),
                                     (live.public, {"role": "agent", "room": ROOM, "job_id": "x"}, "/"),
                                     (live.internal, {"token": LEGACY_TOKEN}, "/")):
            with pytest.raises(socketio.exceptions.ConnectionError):
                await live.client().connect(url, auth, namespace, wait_timeout=1)

        await asyncio.sleep(0.3)  # anything misrouted would have arrived by now
        assert not canary.got("/", "agent_command")
        assert not ui.got(bridge_desktop.NAMESPACE, "msg") and not owner.got("/", "desktop_status")
        assert all(json.loads(m)["typ"] != "notice" for m in agent_link.got(bridge_desktop.NAMESPACE, "msg"))
        assert len([m for m in node_link.got(bridge_desktop.NAMESPACE, "msg") if "call-e2e001" in m]) == 1
        assert any(a["severity"] == "critical" for a in ui.got("/", "system_alert"))  # the agent hello on :5001

        await node_link.sio.disconnect()
        await ui.wait("/", "desktop_status", lambda s: s["state"] == "offline")
    finally:
        for peer in (node, agent, stray):
            peer.close()
