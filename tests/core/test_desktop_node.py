"""Desktop Node runtime, link and supervisor (plan task C-120; contracts K-70 v5, K-74 v5, K-76, K-53 v4).

An in-process fake bridge relays between the node (through a fake LinkTransport) and a fake agent peer built
from services/utils/desktop_protocol.py, and verifies every notice the node sends with a library Receiver.
Every key is generated for the test, every file lives in tmp_path, every tool is a fake: nothing connects to
the live bridge, starts a Desktop Node or an agent worker, touches the mouse, the keyboard or a window, or
creates a key under %LOCALAPPDATA%. The lock tests run small child Pythons with JARVIS_RUN_DIR=tmp_path; the
supervisor test's 'node' is a tiny child script.
"""
# pylint: disable=redefined-outer-name,protected-access,too-many-lines
from __future__ import annotations

import ast
import asyncio
import dataclasses
import datetime
import functools
import hashlib
import json
import os
import queue
import subprocess
import sys
import textwrap
import threading
import time
import types
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from services.ai_core import tool_placement
from services.utils import desktop_protocol as dp
from services.utils.desktop_protocol import DesktopLinkError
from src.core import agent_lock
from src.core.desktop import EXIT_ELEVATED, EXIT_RESTART, EXIT_STALLED, health, link as link_mod, supervisor
from src.core.desktop import settings as ns
from src.core.desktop import wal as wal_mod
from src.core.desktop.executor import INPUT_LOCK, ToolSpec
from src.core.desktop.node import (
    BaselinePolicy,
    DesktopState,
    KeyLoader,
    MemoryState,
    NodeKeys,
    NodeOptions,
    NodeRuntime,
    install_with_power,
    json_safe,
    manifest_digests,
)

ROOT = Path(__file__).resolve().parents[2]
NODE_ID = "laptop-test"
NODE_IDENT = f"pc:{NODE_ID}"
ROOM = "jarvis-hybrid"
SCHEMA = {name: hashlib.sha256(name.encode()).hexdigest() for name in
          ("type_text_tool", "automate_whatsapp", "get_laptop_info", "chrome_browser_close")}
FAST = NodeOptions(heartbeat_s=0.2, ping_s=30.0, dead_man_s=0.3, cancel_answer_s=0.5, progress_s=0.2,
                   progress_scan_s=0.05, status_every_s=0.2, settings_poll_s=0.1, seq_hold_s=0.3,
                   degrade_after_s=1.0, alert_fold_s=0.3, reconnect_first_s=0.05, reconnect_max_s=0.1, jitter_s=0.0,
                   short_suspend_s=0.4)
OMIT = object()  # an exec body field left out


async def until(predicate: Callable[[], Any], timeout: float = 5.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


# --- fakes -----------------------------------------------------------------------------------------------------

class FakeTools:
    """A ToolProvider of fake laptop tools; a blocked tool waits (in a worker thread, as a typing tool does)."""

    def __init__(self, *, whatsapp_lock: bool = True) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.gates: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

        def spec(name: str, cls: str, exclusive: str | None, risk: str = "low") -> ToolSpec:
            return ToolSpec(name=name, cls=cls, param_schema_sha256=SCHEMA[name],
                            invoke=functools.partial(self._run, name), exclusive=exclusive, timeout_s=30.0, risk=risk)

        self.specs = {
            "type_text_tool": spec("type_text_tool", tool_placement.INPUT, INPUT_LOCK),
            "automate_whatsapp": spec("automate_whatsapp", tool_placement.MESSAGING,
                                      INPUT_LOCK if whatsapp_lock else None, "medium"),
            "get_laptop_info": spec("get_laptop_info", tool_placement.READ, None),
            "chrome_browser_close": spec("chrome_browser_close", tool_placement.DE_ESCALATION, None),
        }

    async def describe(self, name: str) -> ToolSpec | None:
        return self.specs.get(name)

    def block(self, tool: str) -> None:
        self.gates[tool] = threading.Event()

    def release(self, tool: str) -> None:
        self.gates[tool].set()

    def names(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self.calls]

    async def _run(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls.append((name, dict(args)))
        gate = self.gates.get(name)
        if gate is not None:
            await asyncio.to_thread(gate.wait, 10.0)
        return {"status": "success", "tool": name, "message": f"{name} ok"}


class LingeringTools(FakeTools):
    """A blocked tool keeps its call up to linger_s after a cancel, as the K-11 sync wrapper waits up to 5 s for
    its worker thread."""

    def __init__(self, linger_s: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.linger_s = linger_s

    async def _run(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.calls.append((name, dict(args)))
        gate = self.gates.get(name)
        if gate is not None:
            worker = asyncio.ensure_future(asyncio.to_thread(gate.wait, 10.0))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                await asyncio.wait({worker}, timeout=self.linger_s)
                raise
        return {"status": "success", "tool": name, "message": f"{name} ok"}


class FakeTransport:
    """The node's end of one in-process connection to FakeBridge."""

    def __init__(self, bridge: FakeBridge) -> None:
        self.bridge = bridge
        self._receive: Any = None
        self._open = False
        self._closed = asyncio.Event()
        self.auth: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._open and not self._closed.is_set()

    def set_receiver(self, receive: Any) -> None:
        self._receive = receive

    async def open(self, auth: Mapping[str, Any]) -> None:
        """As the C-116 bridge: the node's hello comes in the connect auth and is relayed to the agents."""
        if not self.bridge.accept:
            raise ConnectionRefusedError("bridge down")
        self.auth, self._open = dict(auth), True
        self.bridge.node = self
        self.bridge.schedule(self.bridge.node_sent(auth[link_mod.AUTH_HELLO]))

    async def send(self, payload: str) -> None:
        if not self.connected:
            raise ConnectionError("closed")
        self.bridge.schedule(self.bridge.node_sent(payload))

    async def closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        self._closed.set()
        if self.bridge.node is self:
            self.bridge.node = None

    async def deliver(self, payload: Any) -> None:
        if self.connected and self._receive is not None:
            await self._receive(payload)


class FakeBridge:
    """Relays node <-> agents; verifies the node's hellos and notices like the C-116 bridge."""

    def __init__(self, keys: Mapping[str, Any]) -> None:
        self.rx = dp.Receiver(dp.BRIDGE_IDENT, keys, None)
        self.node: FakeTransport | None = None
        self.agents: list[FakeAgent] = []
        self.accept = True
        self.hellos: list[dict[str, Any]] = []
        self.notices: list[dict[str, Any]] = []
        self.rejected: list[str] = []
        self._tasks: set[asyncio.Task[Any]] = set()

    def transport(self) -> FakeTransport:
        return FakeTransport(self)

    def schedule(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def node_sent(self, payload: str) -> None:
        env = json.loads(payload)
        if env["typ"] in ("hello", "notice"):
            try:
                verified = self.rx.verify(payload)
            except DesktopLinkError as err:
                self.rejected.append(err.reason)
                return
            if verified.typ == "hello":
                self.rx.bind(verified.sess)
                self.hellos.append(dict(verified.body))
            else:
                self.notices.append(dict(verified.body))
                return
        for agent in list(self.agents):
            if env["to"] in ("agent:*", agent.ident):
                await agent.receive(payload)

    async def agent_sent(self, env: Mapping[str, Any]) -> None:
        if self.node is not None:
            await self.node.deliver(dp.canonical_json(dict(env)).decode("utf-8"))

    async def hint(self, typ: str, body: Mapping[str, Any]) -> None:
        if self.node is not None:
            await self.node.deliver(json.dumps(dp.build_bridge_hint(typ, body, to=NODE_IDENT)))

    def drop_node(self) -> None:
        if self.node is not None:
            self.node._closed.set()
            self.node = None

    def notices_of(self, kind: str) -> list[dict[str, Any]]:
        return [notice for notice in self.notices if notice["kind"] == kind]


class FakeAgent:
    """An agent peer (job or svc) built from desktop_protocol: answers hellos and pings, opens what it gets."""

    def __init__(self, bridge: FakeBridge, key: Any, pc_public: Any, tmp: Path, *, peer_id: str = "job-1",
                 job_id: str = "job-1", room: str = ROOM, skew_s: float = 0.0, auto_ack: bool = False) -> None:
        self.bridge, self.peer_id, self.job_id, self.room = bridge, peer_id, job_id, room
        self.ident = f"agent:{peer_id}"
        self.skew_s, self.auto_ack = skew_s, auto_ack
        self.signer = dp.LinkSigner(self.ident, key)
        self.store = dp.SeenStore(tmp / f"agent-{peer_id}.db", namespace=peer_id, wall=self.wall)
        self.rx = dp.Receiver(self.ident, {"pc": pc_public}, self.store, clock=dp.ClockEstimator(wall=self.wall),
                              wall=self.wall)
        self.session: dp.PeerSession | None = None
        self.hellos: list[dict[str, Any]] = []
        self.messages: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.rejects: list[str] = []
        self.pings = 0
        self._join_eph: Any = None

    def wall(self) -> float:
        return time.time() + self.skew_s

    async def receive(self, payload: str) -> None:
        try:
            env = self.rx.verify(payload)
        except DesktopLinkError as err:
            self.rejects.append(err.reason)
            return
        if env.typ == "hello":
            await self._on_hello(env)
        elif env.typ == "ping":
            self.pings += 1
            assert self.session is not None
            await self.send(self.signer.build("pong", dp.pong_body(env, received_at=self.wall()), to=env.sender,
                                              sess=self.session.sess, now=self.wall()))
        elif env.typ != "pong":
            assert self.session is not None
            body = dp.open_body(env, self.session.key)
            self.messages.append((env.typ, dict(env.header), body))
            if self.auto_ack and env.typ in ("result", "result_resend"):
                await self.ack_result(str(env.call_id))

    async def join(self) -> None:
        """A peer that starts after the node: an agent_hello that names no session yet."""
        self._join_eph = dp.new_ephemeral_key()
        await self.send(self.signer.build(
            "agent_hello", dp.agent_hello_body(peer_id=self.peer_id, job_id=self.job_id, room=self.room,
                                               eph=self._join_eph), to=NODE_IDENT, room=self.room, now=self.wall()))

    async def _on_hello(self, env: dp.VerifiedEnvelope) -> None:
        self.hellos.append(dict(env.body))
        if self._join_eph is not None:  # the node answered our join: the key matches the eph we sent
            eph, self._join_eph = self._join_eph, None
            self.session = dp.accept_node_hello(env, own_eph=eph, peer_id=self.peer_id)
            self.rx.bind(self.session.sess, env.sender)
            return
        eph = dp.new_ephemeral_key()
        self.session = dp.accept_node_hello(env, own_eph=eph, peer_id=self.peer_id)
        self.rx.bind(self.session.sess, env.sender)
        await self.send(self.signer.build(
            "agent_hello", dp.agent_hello_body(peer_id=self.peer_id, job_id=self.job_id, room=self.room, eph=eph,
                                               sess=self.session.sess),
            to=env.sender, room=self.room, now=self.wall()))

    async def send(self, env: Mapping[str, Any]) -> None:
        await self.bridge.agent_sent(env)

    def exec(self, tool: str, args: Mapping[str, Any] | None = None, *, call_id: str, n: int | None = None,
             approval: Mapping[str, Any] | None = None, schema: str | None = None, now: float | None = None,
             start_by: float | None = None, origin: str = "llm", room: str = ROOM, timeout_s: Any = 10,
             lock_wait_s: Any = 5, session: dp.PeerSession | None = None) -> dict[str, Any]:
        args = dict(args or {})
        body: dict[str, Any] = {"args": args, "args_sha256": dp.args_sha256(args), "timeout_s": timeout_s,
                                "param_schema_sha256": SCHEMA.get(tool, "0" * 64) if schema is None else schema}
        if lock_wait_s is not OMIT:
            body["lock_wait_s"] = lock_wait_s
        if approval is not None:
            body["approval"] = dict(approval)
        session = session or self.session
        assert session is not None
        return self.signer.build("exec", body, to=NODE_IDENT, sess=session.sess, room=room, origin=origin,
                                 session_key=session.key, call_id=call_id, tool=tool,
                                 seq=None if n is None else {"speech_id": "sp-1", "n": n},
                                 start_by=start_by, now=self.wall() if now is None else now)

    async def cancel(self, call_id: str) -> None:
        assert self.session is not None
        await self.send(self.signer.build("cancel", {"call_id": call_id}, to=NODE_IDENT, sess=self.session.sess,
                                          session_key=self.session.key, call_id=call_id, now=self.wall()))

    async def ack_result(self, call_id: str) -> None:
        assert self.session is not None
        await self.send(self.signer.build("result_ack", {}, to=NODE_IDENT, sess=self.session.sess, call_id=call_id,
                                          now=self.wall()))

    async def control(self, kind: str) -> None:
        assert self.session is not None
        await self.send(self.signer.build("control", {"kind": kind}, to=NODE_IDENT, sess=self.session.sess,
                                          now=self.wall()))

    def of(self, typ: str, call_id: str | None = None) -> list[dict[str, Any]]:
        return [body for kind, header, body in self.messages
                if kind == typ and (call_id is None or header.get("call_id") == call_id)]

    async def wait(self, typ: str, call_id: str, timeout: float = 5.0) -> dict[str, Any]:
        await until(lambda: self.of(typ, call_id), timeout, f"{typ} {call_id}")
        return self.of(typ, call_id)[0]

    def close(self) -> None:
        self.store.close()


def approval_for(args: Mapping[str, Any], *, digest: str | None = None) -> dict[str, Any]:
    return {"confirm_id": "c" * 32, "channel": "ui", "risk": "medium",
            "args_sha256": dp.args_sha256(dict(args)) if digest is None else digest}


class Harness:
    """A node with fake tools on a fake bridge, and one fake agent."""

    def __init__(self, tmp: Path, *, options: NodeOptions = FAST, env_text: str = "", state: str | None = "full",
                 skew_s: float = 0.0, tools: FakeTools | None = None, pc_key: Any = None, agent_key: Any = None,
                 store_age_s: float = 3600.0) -> None:
        self.tmp = tmp
        self.data_root = tmp / "repo"
        self.run_dir = self.data_root / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if not (self.data_root / ".env").exists():
            (self.data_root / ".env").write_text(env_text, encoding="utf-8")
        self.pc_key = pc_key or dp.generate_signing_key()
        self.agent_key = agent_key or dp.generate_signing_key()
        self.bridge = FakeBridge({"pc": self.pc_key.public_key(), "agent": self.agent_key.public_key()})
        # The node's seen store was opened store_age_s ago (wall and monotonic together, so no clock step),
        # as on a node that has been running: the refusal floor lies before every test message.
        shift = {"s": -store_age_s}
        seen = dp.SeenStore(self.run_dir / wal_mod.WAL_FILE, namespace=NODE_IDENT,
                            wall=lambda: time.time() + shift["s"], mono=lambda: time.monotonic() + shift["s"])
        shift["s"] = 0.0
        self.config = types.SimpleNamespace(desktop_control_enabled=True, clipboard_helper_enabled=True,
                                            clipboard_speak=False)
        self.tools = tools or FakeTools()
        self.node = NodeRuntime(
            node_id=NODE_ID, keys=NodeKeys(self.pc_key, self.agent_key.public_key()), data_root=self.data_root,
            run_dir=self.run_dir, settings_source=ns.SettingsSource(self.data_root / ".env", self.data_root),
            transport_factory=self.bridge.transport, tool_provider=self.tools, options=options, config=self.config,
            git_sha="0123abcd", manifest_sha256="m" * 64, param_schema_sha256="p" * 64, seen=seen)
        if state is not None:
            self.node.set_state_source(MemoryState(state))
        self.agent = FakeAgent(self.bridge, self.agent_key, self.pc_key.public_key(), tmp, skew_s=skew_s)
        self.bridge.agents.append(self.agent)
        self.task: asyncio.Task[int] | None = None

    async def start(self) -> Harness:
        self.task = asyncio.ensure_future(self.node.run())
        await until(lambda: self.agent.session is not None and self.agent.ident in self.node.link.peers
                    and self.node.clock.offset_s is not None, what="handshake and first pong")
        return self

    async def send(self, env: Mapping[str, Any]) -> None:
        await self.agent.send(env)

    async def stop(self) -> int:
        code = 0
        if self.task is not None:
            self.node.request_stop()
            code = await asyncio.wait_for(self.task, 15.0)
        self.node.close()
        self.agent.close()
        return code


@pytest.fixture
async def harness(tmp_path):
    made: list[Harness] = []

    async def make(**kwargs: Any) -> Harness:
        item = Harness(tmp_path, **kwargs)
        made.append(item)
        return await item.start()

    yield make
    for item in made:
        if item.task is not None and not item.task.done():
            await item.stop()


# --- handshake, exec, result -----------------------------------------------------------------------------------

async def test_handshake_then_exec_ack_result_and_result_ack(harness):
    h = await harness()
    hello = h.agent.hellos[0]
    assert hello["node_id"] == NODE_ID and hello["proto"] == [1] and hello["mode"] == "full"
    assert hello["locked"] is False and hello["git_sha"] == "0123abcd" and hello["version"]
    assert {"boot_id", "manifest_sha256", "param_schema_sha256", "eph_pub", "sess", "wal_report"} <= set(hello)
    assert not {"presence", "presence_s"} & set(hello)  # K-76 v5: presence stays on the node
    assert h.bridge.notices_of("desktop_state")[0] == {"kind": "desktop_state", "mode": "full", "locked": False,
                                                       "panic": False}
    assert h.agent.pings >= 1  # a ping right after the handshake measured the offset

    args = {"text": "SECRET-TEXT-123"}
    await h.send(h.agent.exec("type_text_tool", args, call_id="call-basic-1"))
    ack = await h.agent.wait("ack", "call-basic-1")
    assert ack == {"call_id": "call-basic-1", "accepted": True, "risk": "low", "cls": "input"}
    result = await h.agent.wait("result", "call-basic-1")
    assert result["status"] == "completed" and result["effect"] == "done"
    assert result["result"] == {"status": "success", "tool": "type_text_tool", "message": "type_text_tool ok"}
    assert h.tools.calls == [("type_text_tool", args)]
    assert (await h.node.wal.aget("call-basic-1")).state == wal_mod.STATE_COMPLETED
    assert await h.node.wal.aoutbox_size() == 1
    await h.agent.ack_result("call-basic-1")
    await until(lambda: h.node.wal.outbox_size() == 0, what="outbox emptied by result_ack")

    await until(lambda: (h.node.status_file.read() or {}).get("last_exec"), what="status file")
    status = h.node.status_file.read()
    assert status["link"] == "connected" and status["mode"] == "full" and status["peers"] == 1
    audit = (h.data_root / health.AUDIT_FILE).read_text(encoding="utf-8")
    assert "exec_accepted" in audit and "exec_finished" in audit
    assert "SECRET-TEXT-123" not in audit and "SECRET-TEXT-123" not in json.dumps(status)


async def test_replays_and_retried_call_ids_never_run_twice(harness):
    h = await harness()
    env = h.agent.exec("get_laptop_info", call_id="call-replay-1")
    await h.send(env)
    await h.agent.wait("result", "call-replay-1")
    await h.send(env)  # the same envelope again: a replay at check 7, no answer at all
    await asyncio.sleep(0.3)
    assert len(h.agent.of("ack", "call-replay-1")) == 1
    await h.send(h.agent.exec("get_laptop_info", call_id="call-replay-1"))  # a retry: new id, same call_id
    resend = await h.agent.wait("result_resend", "call-replay-1")
    assert resend["call_id"] == "call-replay-1" and resend["result"]["status"] == "completed"
    assert h.tools.names() == ["get_laptop_info"]


async def test_a_stale_exec_gets_a_refusal_unless_the_wal_holds_its_call(harness):
    h = await harness()
    await h.send(h.agent.exec("get_laptop_info", call_id="call-stale-1", now=h.agent.wall() - 200))
    refusal = await h.agent.wait("ack", "call-stale-1")
    assert refusal == {"call_id": "call-stale-1", "accepted": False, "code": "DESKTOP_REJECTED"}
    assert h.tools.calls == []

    await h.send(h.agent.exec("get_laptop_info", call_id="call-ran-1"))
    await h.agent.wait("result", "call-ran-1")
    await h.send(h.agent.exec("get_laptop_info", call_id="call-ran-1", now=h.agent.wall() - 200))
    resend = await h.agent.wait("result_resend", "call-ran-1")  # its real outcome, never a refusal
    assert resend["result"]["status"] == "completed"
    assert [body for body in h.agent.of("ack", "call-ran-1") if body["accepted"] is False] == []


async def test_clock_skew_is_refused_and_raises_the_warning(harness):
    h = await harness(skew_s=90.0)
    assert h.node.clock.offset_s == pytest.approx(90.0, abs=1.0)
    await h.send(h.agent.exec("get_laptop_info", call_id="call-skew-1"))
    assert await h.agent.wait("ack", "call-skew-1") == {"call_id": "call-skew-1", "accepted": False,
                                                        "code": "CLOCK_SKEW"}
    alert = (await until(lambda: h.bridge.notices_of("alert"), what="alert notice"))[0]
    assert alert["code"] == "CLOCK_SKEW" and alert["level"] == "warning"
    assert h.tools.calls == []


async def test_a_missed_start_window_is_refused_and_recorded(harness):
    h = await harness()
    await h.send(h.agent.exec("get_laptop_info", call_id="call-late-1", start_by=h.agent.wall() - 1.0))
    assert (await h.agent.wait("ack", "call-late-1"))["code"] == "START_WINDOW_MISSED"
    entry = await h.node.wal.aget("call-late-1")
    assert entry.state == wal_mod.STATE_START_WINDOW_MISSED and not entry.started
    assert h.tools.calls == []


async def test_lock_wait_zero_or_missing_takes_a_free_input_lock(harness):
    h = await harness()
    for call_id, text, lock_wait in (("call-lw-zero", "a", 0), ("call-lw-none", "b", OMIT)):
        await h.send(h.agent.exec("type_text_tool", {"text": text}, call_id=call_id, lock_wait_s=lock_wait))
        assert (await h.agent.wait("result", call_id))["status"] == "completed", call_id
    assert [args["text"] for _name, args in h.tools.calls] == ["a", "b"]

    h.tools.block("type_text_tool")  # held now: 0 means 'do not wait', so DESKTOP_BUSY at once
    await h.send(h.agent.exec("type_text_tool", {"text": "long"}, call_id="call-lw-holder"))
    await until(lambda: len(h.tools.calls) == 3, what="the lock holder running")
    started = time.monotonic()
    await h.send(h.agent.exec("type_text_tool", {"text": "c"}, call_id="call-lw-busy", lock_wait_s=0))
    busy = await h.agent.wait("result", "call-lw-busy")
    assert busy["status"] == "not_started" and busy["result"]["error_code"] == "DESKTOP_BUSY"
    assert time.monotonic() - started < 1.0 and len(h.tools.calls) == 3
    h.tools.release("type_text_tool")
    assert (await h.agent.wait("result", "call-lw-holder"))["status"] == "completed"


async def test_an_unencodable_tool_result_still_ends_with_a_result(harness):
    tools = FakeTools()

    async def odd(_args: Mapping[str, Any]) -> dict[str, Any]:  # what canonical JSON cannot carry as it is
        return {"status": "success", "tool": "get_laptop_info", "message": "ok \ud800",
                "data": {"secs_left": float("nan"), "day": datetime.date(2026, 10, 2), "tags": ("a",), 7: b"raw"}}

    tools.specs["get_laptop_info"] = dataclasses.replace(tools.specs["get_laptop_info"], invoke=odd)
    h = await harness(tools=tools)
    await h.send(h.agent.exec("get_laptop_info", call_id="call-odd-1"))
    result = await h.agent.wait("result", "call-odd-1")
    assert result["status"] == "completed" and result["effect"] == "done"
    assert result["result"]["message"] == "ok �"
    assert result["result"]["data"] == {"secs_left": None, "day": "2026-10-02", "tags": ["a"], "7": "raw"}
    assert (await h.node.wal.aget("call-odd-1")).state == wal_mod.STATE_COMPLETED
    assert await h.node.wal.aoutbox_size() == 1  # kept for result_resend like any result
    assert json_safe({"a": [1]}, depth=dp.MAX_JSON_DEPTH) == "[nested too deep]"


# --- policy ----------------------------------------------------------------------------------------------------

async def test_before_c121_the_node_is_read_only(harness):
    h = await harness(state=None)  # the C-120 baseline: MemoryState('read'), BaselinePolicy
    assert isinstance(h.node.policy, BaselinePolicy) and h.agent.hellos[0]["mode"] == "read"
    await h.send(h.agent.exec("type_text_tool", {"text": "x"}, call_id="call-ro-1"))
    assert (await h.agent.wait("ack", "call-ro-1"))["code"] == "DESKTOP_READ_ONLY"
    await h.send(h.agent.exec("get_laptop_info", call_id="call-ro-2"))
    assert (await h.agent.wait("result", "call-ro-2"))["status"] == "completed"
    await h.send(h.agent.exec("chrome_browser_close", call_id="call-ro-3"))  # de-escalation always runs
    assert (await h.agent.wait("result", "call-ro-3"))["status"] == "completed"
    await h.send(h.agent.exec("pc.ping", call_id="call-ro-4",
                              schema=tool_placement.PC_OPERATIONS[0].manifest_entry()["param_schema_sha256"]))
    ping = await h.agent.wait("result", "call-ro-4")
    assert ping["result"]["tool"] == "pc.ping" and ping["result"]["data"]["node_id"] == NODE_ID


async def test_a_registered_pc_operation_runs_and_its_blobs_go_to_the_result_body(harness):
    h = await harness()
    blob = {"blob_id": "b" * 32, "sha256": "c" * 64, "size": 10, "mime": "audio/wav"}

    async def speak_local(args: Mapping[str, Any]) -> dict[str, Any]:  # what C-122 registers
        return {"status": "success", "tool": "pc.speak_local", "message": "spoken", "blobs": [blob]}

    spec = h.node.register_operation("pc.speak_local", speak_local)
    await h.send(h.agent.exec("pc.speak_local", {"text": "salam"}, call_id="call-op-1",
                              schema=spec.param_schema_sha256))
    result = await h.agent.wait("result", "call-op-1")
    assert result["blobs"] == [blob] and "blobs" not in result["result"]
    assert result["result"]["message"] == "spoken"


async def test_approvals_manifest_and_body_checks(harness):
    h = await harness()
    args = {"contact": "Test", "message": "hi"}
    cases = [
        (h.agent.exec("automate_whatsapp", args, call_id="call-pol-1"), "APPROVAL_REQUIRED"),
        (h.agent.exec("automate_whatsapp", args, call_id="call-pol-2", approval=approval_for(args, digest="0" * 64)),
         "APPROVAL_INVALID"),
        (h.agent.exec("type_text_tool", call_id="call-pol-3", schema="f" * 64), "MANIFEST_MISMATCH"),
        (h.agent.exec("no_such_tool", call_id="call-pol-4"), "MANIFEST_MISMATCH"),
        (h.agent.exec("get_laptop_info", call_id="call-pol-5", timeout_s=-1), "BAD_ARGS"),
        (h.agent.exec("type_text_tool", call_id="call-pol-6", origin="canary"), "DESKTOP_READ_ONLY"),
        (h.agent.exec("type_text_tool", call_id="call-pol-7", room="jarvis-probe-1759400000"), "DESKTOP_READ_ONLY"),
    ]
    for env, code in cases:
        await h.send(env)
        assert (await h.agent.wait("ack", env["call_id"]))["code"] == code, env["call_id"]
    alerts = {notice["code"]: notice["level"] for notice in
              await until(lambda: len(h.bridge.notices_of("alert")) >= 2 and h.bridge.notices_of("alert"))}
    assert alerts == {"APPROVAL_INVALID": "critical", "MANIFEST_MISMATCH": "warning"}
    assert h.tools.calls == []

    await h.send(h.agent.exec("automate_whatsapp", args, call_id="call-pol-8", approval=approval_for(args)))
    assert (await h.agent.wait("ack", "call-pol-8"))["accepted"] is True
    assert (await h.agent.wait("result", "call-pol-8"))["status"] == "completed"
    # a refused call_id may come back with its approval (C-119 re-sends after ConfirmFlow)
    await h.send(h.agent.exec("automate_whatsapp", args, call_id="call-pol-1", approval=approval_for(args)))
    await until(lambda: [b for b in h.agent.of("ack", "call-pol-1") if b["accepted"]], what="accepted re-send")


async def test_the_kill_switch_comes_from_the_root_env_within_one_poll(tmp_path, harness):
    h = await harness(env_text="JARVIS_DESKTOP_CONTROL=false\n")
    assert h.node.settings.desktop_control is False
    await h.send(h.agent.exec("type_text_tool", {"text": "x"}, call_id="call-kill-1"))
    assert (await h.agent.wait("ack", "call-kill-1"))["code"] == "DESKTOP_CONTROL_DISABLED"
    await h.send(h.agent.exec("get_laptop_info", call_id="call-kill-2"))  # reads still run
    assert (await h.agent.wait("result", "call-kill-2"))["status"] == "completed"

    (h.data_root / ".env").write_text("JARVIS_DESKTOP_CONTROL=true\n", encoding="utf-8")
    await until(lambda: h.node.settings.desktop_control, timeout=2.0, what="two settings polls")
    assert h.config.desktop_control_enabled is True
    await h.send(h.agent.exec("type_text_tool", {"text": "x"}, call_id="call-kill-3"))
    assert (await h.agent.wait("result", "call-kill-3"))["status"] == "completed"

    (h.data_root / ".env").write_text("JARVIS_DESKTOP_CONTROL=false\n", encoding="utf-8")
    await until(lambda: not h.node.settings.desktop_control, timeout=1.0, what="the kill switch at the first poll")
    await h.send(h.agent.exec("type_text_tool", {"text": "x"}, call_id="call-kill-4"))
    assert (await h.agent.wait("ack", "call-kill-4"))["code"] == "DESKTOP_CONTROL_DISABLED"


# --- cancel, stop, dead-man --------------------------------------------------------------------------------------

async def test_a_cancel_is_answered_within_the_window(harness):
    h = await harness()
    h.tools.block("type_text_tool")
    await h.send(h.agent.exec("type_text_tool", {"text": "long"}, call_id="call-cancel-1"))
    await h.agent.wait("ack", "call-cancel-1")
    await until(lambda: h.tools.names() == ["type_text_tool"], what="tool running")
    started = time.monotonic()
    await h.agent.cancel("call-cancel-1")
    result = await h.agent.wait("result", "call-cancel-1", timeout=3.0)
    assert time.monotonic() - started < FAST.cancel_answer_s + 1.0
    assert result["status"] == "cancelled" and result["effect"] == "possible"
    h.tools.release("type_text_tool")


async def test_a_cancel_while_the_call_is_judged_or_accepted_is_never_lost(harness):
    h = await harness()
    judging = asyncio.Event()

    class SlowPolicy:  # stands in for C-121's async checks
        async def check(self, _call: Any) -> None:
            judging.set()
            await asyncio.sleep(0.8)

    h.node.set_policy(SlowPolicy())
    await h.send(h.agent.exec("type_text_tool", {"text": "x"}, call_id="call-judged-1"))
    await asyncio.wait_for(judging.wait(), 5.0)
    await h.agent.cancel("call-judged-1")
    assert await h.agent.wait("ack", "call-judged-1") == {"call_id": "call-judged-1", "accepted": False,
                                                          "code": "DESKTOP_REJECTED"}

    h.node.set_policy(BaselinePolicy(h.node))
    accept, accepting = h.node.wal.aaccept, asyncio.Event()

    async def slow_accept(*args: Any, **kwargs: Any) -> bool:  # the WAL write of the acceptance is slow
        accepting.set()
        await asyncio.sleep(0.8)
        return await accept(*args, **kwargs)

    h.node.wal.aaccept = slow_accept
    await h.send(h.agent.exec("type_text_tool", {"text": "y"}, call_id="call-judged-2"))
    await asyncio.wait_for(accepting.wait(), 5.0)
    await h.agent.cancel("call-judged-2")
    assert (await h.agent.wait("ack", "call-judged-2"))["accepted"] is True  # the WAL holds it as accepted
    result = await h.agent.wait("result", "call-judged-2")
    assert result["status"] == "not_started" and result["effect"] == "none"
    assert result["result"]["error_code"] == "CANCELLED"
    assert h.tools.calls == []  # neither call ever ran


async def test_a_cancel_or_a_stop_never_stops_the_link_from_reading(harness):
    tools = LingeringTools(4.0, whatsapp_lock=False)
    h = await harness(tools=tools, options=dataclasses.replace(FAST, cancel_answer_s=2.0))
    tools.block("type_text_tool")
    await h.send(h.agent.exec("type_text_tool", {"text": "long"}, call_id="call-linger-1"))
    await until(lambda: tools.names() == ["type_text_tool"], what="the typing call running")
    started = time.monotonic()
    await h.agent.cancel("call-linger-1")
    await h.send(h.agent.exec("get_laptop_info", call_id="call-next-1"))  # the next command, at once
    assert (await h.agent.wait("result", "call-next-1"))["status"] == "completed"
    assert time.monotonic() - started < 1.5 and not h.agent.of("result", "call-linger-1")
    assert (await h.agent.wait("result", "call-linger-1"))["status"] == "cancelled"  # within cancel_answer_s

    args = {"contact": "Test", "message": "hi"}
    tools.block("automate_whatsapp")
    await h.send(h.agent.exec("automate_whatsapp", args, call_id="call-linger-2", approval=approval_for(args)))
    await until(lambda: "automate_whatsapp" in tools.names(), what="the send step running")
    started = time.monotonic()
    await h.bridge.hint("control", {"kind": "desktop_stop"})
    await h.send(h.agent.exec("get_laptop_info", call_id="call-next-2"))  # reads still run in mode read
    assert (await h.agent.wait("result", "call-next-2"))["status"] == "completed"
    assert time.monotonic() - started < 1.5 and not h.agent.of("result", "call-linger-2")
    stopped = await h.agent.wait("result", "call-linger-2")
    assert stopped["status"] == "cancelled" and stopped["result"]["error_code"] == "STOPPED_BY_USER"
    tools.release("type_text_tool")
    tools.release("automate_whatsapp")


async def test_progress_is_sent_while_a_call_runs(harness):
    h = await harness()
    h.tools.block("type_text_tool")
    await h.send(h.agent.exec("type_text_tool", {"text": "long"}, call_id="call-prog-1"))
    progress = await h.agent.wait("progress", "call-prog-1")
    assert progress["state"] == "running" and progress["elapsed_ms"] >= 0
    h.tools.release("type_text_tool")
    await h.agent.wait("result", "call-prog-1")


async def test_desktop_stop_tightens_to_read_and_cancels_in_flight_calls(harness):
    h = await harness()
    h.tools.block("type_text_tool")
    await h.send(h.agent.exec("type_text_tool", {"text": "long"}, call_id="call-stop-1"))
    await until(h.tools.names, what="tool running")
    await h.bridge.hint("control", {"kind": "desktop_stop"})
    result = await h.agent.wait("result", "call-stop-1", timeout=3.0)
    assert result["status"] == "cancelled" and result["result"]["error_code"] == "STOPPED_BY_USER"
    assert h.node.state.current().mode == "read"
    await until(lambda: any(n["mode"] == "read" for n in h.bridge.notices_of("desktop_state")), what="state notice")
    await h.send(h.agent.exec("type_text_tool", {"text": "x"}, call_id="call-stop-2"))
    assert (await h.agent.wait("ack", "call-stop-2"))["code"] == "DESKTOP_READ_ONLY"
    assert h.node.state.tighten("full", source="bridge") is False  # remote callers only tighten
    h.tools.release("type_text_tool")


async def test_dead_man_cancels_input_calls_and_a_messaging_step_completes(harness):
    h = await harness(tools=FakeTools(whatsapp_lock=False))
    h.tools.block("type_text_tool")
    h.tools.block("automate_whatsapp")
    args = {"contact": "Test", "message": "hi"}
    await h.send(h.agent.exec("type_text_tool", {"text": "long"}, call_id="call-dm-input"))
    await h.send(h.agent.exec("automate_whatsapp", args, call_id="call-dm-msg", approval=approval_for(args)))
    await until(lambda: sorted(h.tools.names()) == ["automate_whatsapp", "type_text_tool"], what="both running")

    h.bridge.accept = False
    h.bridge.drop_node()  # the link is down from here on
    await until(lambda: (wal_entry := h.node.wal.get("call-dm-input")) and wal_entry.state == "completed",
                timeout=3.0, what="dead-man cancel")
    assert h.node.wal.get("call-dm-msg").state == wal_mod.STATE_RUNNING  # the send step goes on
    h.tools.release("automate_whatsapp")
    await until(lambda: h.node.wal.get("call-dm-msg").state == "completed", what="messaging completed")

    h.bridge.accept = True  # the link returns: both results come again as result_resend
    resent = await h.agent.wait("result_resend", "call-dm-input")
    assert resent["result"]["status"] == "cancelled"
    assert resent["result"]["result"]["error_code"] == "DESKTOP_OFFLINE"
    assert (await h.agent.wait("result_resend", "call-dm-msg"))["result"]["status"] == "completed"
    h.tools.release("type_text_tool")


async def test_reconnect_opens_a_new_session_and_old_session_execs_are_refused(harness):
    h = await harness()
    first_sess, old_session = h.node.link.sess, h.agent.session
    old_exec = h.agent.exec("get_laptop_info", call_id="call-old-sess")
    h.bridge.drop_node()
    await until(lambda: h.node.link.sess != first_sess and h.agent.session is not old_session
                and h.agent.ident in h.node.link.peers, what="reconnect and new handshake")
    assert len(h.bridge.hellos) == 2 and h.node.link.connects == 2
    await h.send(old_exec)  # signed for the old session: refused (check 6) in the new one
    assert (await h.agent.wait("ack", "call-old-sess"))["code"] == "DESKTOP_REJECTED"
    await h.send(h.agent.exec("get_laptop_info", call_id="call-new-sess"))
    assert (await h.agent.wait("result", "call-new-sess"))["status"] == "completed"


async def test_results_are_resent_after_a_restart_and_a_crashed_call_shows_interrupted(tmp_path, harness):
    first = await harness()
    await first.send(first.agent.exec("get_laptop_info", call_id="call-before-restart"))
    await first.agent.wait("result", "call-before-restart")  # never acknowledged
    fields = {"tool": "type_text_tool", "room": ROOM, "origin": "llm", "peer": first.agent.ident}
    await first.node.wal.aaccept("call-crashed", **fields)  # as a node killed during a call leaves it
    await asyncio.to_thread(first.node.wal.mark_running, "call-crashed")
    await first.stop()

    second = Harness(tmp_path, pc_key=first.pc_key, agent_key=first.agent_key)
    try:
        await second.start()
        report = {item["call_id"]: item["state"] for item in second.agent.hellos[0]["wal_report"]}
        assert report["call-crashed"] == "interrupted" and report["call-before-restart"] == "completed"
        resent = await second.agent.wait("result_resend", "call-before-restart")
        assert resent["result"]["status"] == "completed" and resent["original_ts"] > 0
        await second.send(second.agent.exec("type_text_tool", call_id="call-crashed"))  # the agent retries it
        crashed = (await second.agent.wait("result_resend", "call-crashed"))["result"]
        assert crashed["status"] == "failed" and crashed["effect"] == "possible"  # a K-70 result status
        assert crashed["result"]["error_code"] == "DESKTOP_RESULT_UNKNOWN"
        assert second.tools.calls == []  # never run a second time
    finally:
        await second.stop()


# --- notices, power ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run_mode", ["active", "canary"])
async def test_the_desktop_state_heartbeat_runs_in_every_run_mode(harness, run_mode):
    h = await harness(options=dataclasses.replace(FAST, run_mode=run_mode), state="read")
    before = len(h.bridge.notices_of("desktop_state"))
    await asyncio.sleep(0.9)
    assert len(h.bridge.notices_of("desktop_state")) - before >= 3
    assert not h.bridge.rejected  # every notice passed the bridge's receiver checks
    assert set(h.bridge.notices_of("desktop_state")[-1]) == {"kind", "mode", "locked", "panic"}
    assert link_mod.HEARTBEAT_S == 10.0 and NodeOptions().heartbeat_s == 10.0


async def test_suspend_resume_and_shutdown_power_notices(harness):
    h = await harness(options=dataclasses.replace(FAST, short_suspend_s=1.5))
    await h.node.link.on_power("suspend")
    await until(lambda: h.bridge.notices_of("power"), what="suspend notice")
    assert h.bridge.notices_of("power")[-1] == {"kind": "power", "state": "suspend"}
    await asyncio.sleep(0.05)
    quiet_from = len(h.bridge.notices)
    await asyncio.sleep(0.45)  # two heartbeats would have gone out: nothing at all is sent while suspended
    assert len(h.bridge.notices) == quiet_from
    pings = h.agent.pings
    await h.node.link.on_power("resume")  # a short sleep: the socket survived
    await until(lambda: h.bridge.notices_of("power")[-1]["state"] == "resume", what="resume notice")
    await until(lambda: h.agent.pings > pings, what="a ping right after the resume")

    hellos = len(h.bridge.hellos)
    await h.node.link.on_power("suspend")
    await asyncio.sleep(1.5 + 0.2)  # a long sleep: reconnect at once with a new hello
    await h.node.link.on_power("resume")
    await until(lambda: len(h.bridge.hellos) > hellos, what="a new hello")

    await until(lambda: h.agent.ident in h.node.link.peers, what="handshake")
    await h.node.link.on_power("shutdown")
    await until(lambda: h.bridge.notices_of("power")[-1]["state"] == "shutdown", what="shutdown notice")
    await asyncio.sleep(0.05)
    last = len(h.bridge.notices)
    await asyncio.sleep(0.5)
    assert len(h.bridge.notices) == last  # the last message of the connection


async def test_no_reconnect_while_suspended_and_none_after_a_shutdown(harness):
    h = await harness()
    await h.node.link.on_power("suspend")
    hellos = len(h.bridge.hellos)
    h.bridge.drop_node()  # the socket dies during the sleep
    await asyncio.sleep(0.5)
    assert len(h.bridge.hellos) == hellos  # no new hello before the wake
    await h.node.link.on_power("resume")
    await until(lambda: len(h.bridge.hellos) > hellos, what="a new hello after the wake")
    await until(lambda: h.agent.ident in h.node.link.peers, what="handshake")

    await h.node.link.on_power("logoff")
    await until(lambda: h.bridge.notices_of("power")[-1]["state"] == "logoff", what="logoff notice")
    hellos = len(h.bridge.hellos)
    h.bridge.drop_node()
    await asyncio.sleep(0.5)
    assert len(h.bridge.hellos) == hellos and h.node.link.status() == "ended"


def test_the_power_listener_maps_windows_messages_without_a_window():
    seen: list[str] = []
    listener = link_mod.PowerListener(seen.append)
    assert listener.handle_message(link_mod.WM_POWERBROADCAST, link_mod.PBT_APMSUSPEND, 0) == 1
    assert listener.handle_message(link_mod.WM_POWERBROADCAST, link_mod.PBT_APMRESUMEAUTOMATIC, 0) == 1
    assert listener.handle_message(link_mod.WM_QUERYENDSESSION, 0, 0) == 1  # never blocks the session end
    assert listener.handle_message(link_mod.WM_ENDSESSION, 1, link_mod.ENDSESSION_LOGOFF) == 0
    assert listener.handle_message(link_mod.WM_ENDSESSION, 1, 0) == 0
    assert listener.handle_message(link_mod.WM_ENDSESSION, 0, 0) == 0  # the session goes on: nothing
    listener.add_handler(0x02B1, lambda wparam, lparam: 7)  # C-121's WM_WTSSESSION_CHANGE
    assert listener.handle_message(0x02B1, 7, 0) == 7
    assert seen == ["suspend", "resume", "logoff", "shutdown"]


async def test_a_late_svc_peer_gets_a_hello_and_events_route_by_kind(tmp_path, harness):
    h = await harness()
    assert await h.node.link.set_current("location", {"city": "Lahore", "ts": 1.0}) is False  # no svc peer: dropped
    svc = FakeAgent(h.bridge, h.agent_key, h.pc_key.public_key(), tmp_path, peer_id="svc-1", job_id="svc", room="")
    h.bridge.agents.append(svc)
    try:
        await svc.join()
        await until(lambda: svc.session is not None and "agent:svc-1" in h.node.link.peers, what="svc handshake")
        assert svc.session.sess == h.node.link.sess and svc.hellos[0]["node_id"] == NODE_ID
        location = await until(lambda: [body for typ, _h, body in svc.messages if typ == "event"], what="location")
        assert location[0] == {"kind": "location", "city": "Lahore", "ts": 1.0}  # re-sent after its agent_hello

        assert await h.node.link.send_event("window_context", {"title": "Notes", "ts": 2.0})
        context = await until(lambda: [body for typ, _h, body in h.agent.messages if typ == "event"], what="context")
        assert context[0]["kind"] == "window_context"  # the owner-room job peer only
        assert [body["kind"] for typ, _h, body in svc.messages if typ == "event"] == ["location"]
        assert h.node.link.event_peer("log_batch").ident == "agent:svc-1"
        assert h.node.link.event_peer("clipboard_suggestion").ident == h.agent.ident
        assert h.node.link.event_peer("vitals_pc") is None
    finally:
        svc.close()


def test_build_runtime_wires_the_production_parts(tmp_path):
    from services.utils.jarvis_loopwatch import LoopWatchdog  # pylint: disable=import-outside-toplevel
    from src.core.desktop.node import build_runtime  # pylint: disable=import-outside-toplevel

    (tmp_path / ".env").write_text("JARVIS_LINK_URL=https://jarvis.example.test\n", encoding="utf-8")
    root = ns.DataRoot(tmp_path, tmp_path / "run")
    source = ns.SettingsSource(tmp_path / ".env", tmp_path)
    source.load()
    cfg = ns.NodeConfig(data_root=tmp_path, run_dir=tmp_path / "run", node_id="laptop-x")
    keys = NodeKeys(dp.generate_signing_key(), dp.generate_signing_key().public_key())
    runtime = build_runtime(run_mode="canary", root=root, source=source, cfg=cfg, keys=keys, config=None)
    try:
        transport = runtime.link._factory()
        assert isinstance(transport, link_mod.SocketIOTransport)
        assert (transport._url, transport._path) == ("https://jarvis.example.test", "vortex/socket.io")
        assert runtime.run_mode == "canary" and runtime.node_id == "laptop-x" and len(runtime.manifest_sha256) == 64
        watchdog = runtime._watchdog_factory(runtime)
        assert isinstance(watchdog, LoopWatchdog) and watchdog.critical_ms == 60_000.0
        assert (tmp_path / "run" / wal_mod.WAL_FILE).exists()  # the repo's run/, never the snapshot
    finally:
        runtime.close()
    (tmp_path / ".env").write_text("JARVIS_LINK_URL=http://jarvis.example.test\n", encoding="utf-8")
    source.load()
    with pytest.raises(ns.NodeConfigError):
        build_runtime(run_mode="active", root=root, source=source, cfg=cfg, keys=keys, config=None)


async def test_seq_keeps_the_order_of_one_reply(harness):
    h = await harness()
    await h.send(h.agent.exec("get_laptop_info", {"i": 1}, call_id="call-seq-1", n=1))
    await asyncio.sleep(0.05)
    await h.send(h.agent.exec("get_laptop_info", {"i": 0}, call_id="call-seq-0", n=0))
    await h.agent.wait("result", "call-seq-1")
    await h.agent.wait("result", "call-seq-0")
    assert [args["i"] for _name, args in h.tools.calls] == [0, 1]


async def test_alerts_fold_repeats_into_one_notice_with_count(harness):
    h = await harness()
    h.node.raise_alert("DESKTOP_RATE_LIMITED", tool="type_text_tool")
    first = await until(lambda: h.bridge.notices_of("alert"), what="first alert")
    assert first[0] == {"kind": "alert", "code": "DESKTOP_RATE_LIMITED", "level": "warning", "tool": "type_text_tool"}
    for _ in range(3):  # repeats within the fold window: one notice with count when it ends
        h.node.raise_alert("DESKTOP_RATE_LIMITED", tool="type_text_tool")
    await asyncio.sleep(0.1)
    assert len(h.bridge.notices_of("alert")) == 1
    folded = await until(lambda: len(h.bridge.notices_of("alert")) >= 2 and h.bridge.notices_of("alert"),
                         timeout=2.0, what="folded alert")
    assert folded[1]["count"] == 3 and len(folded) == 2
    with pytest.raises(ValueError):
        h.node.raise_alert("NOT_A_CODE")


async def test_state_changes_and_alerts_from_another_thread_reach_the_bridge(harness):
    h = await harness(options=dataclasses.replace(FAST, heartbeat_s=30.0))  # no heartbeat carries the change
    state = h.node.state
    errors: list[BaseException] = []

    def tray_thread() -> None:  # C-121's tray, panic hotkey and session handlers run on their own threads
        try:
            state.set(DesktopState(mode="full", locked=True))
            h.node.raise_alert("DESKTOP_RATE_LIMITED", tool="type_text_tool")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(exc)

    thread = threading.Thread(target=tray_thread)
    thread.start()
    thread.join()
    assert not errors
    await until(lambda: any(notice["locked"] for notice in h.bridge.notices_of("desktop_state")), timeout=2.0,
                what="the desktop_state notice of the change")
    alert = (await until(lambda: h.bridge.notices_of("alert"), timeout=2.0, what="the alert"))[0]
    assert alert["code"] == "DESKTOP_RATE_LIMITED"


# --- node plumbing ---------------------------------------------------------------------------------------------------

async def test_restart_only_settings_and_the_stall_watchdog(tmp_path):
    h = Harness(tmp_path, env_text="JARVIS_OUTPUTS_DIR=Out\n")
    stalls: list[int] = []
    h.node._stall_action = stalls.append
    try:
        (h.data_root / ".env").write_text("JARVIS_OUTPUTS_DIR=Other\n", encoding="utf-8")
        assert h.node.poll_settings() is False  # applied once a second poll reads the same file
        assert h.node.poll_settings() is True
        assert h.node.exit_code == EXIT_RESTART
        h.node.on_stall(types.SimpleNamespace(ms=61_000))
        assert stalls == [EXIT_STALLED]
    finally:
        h.node.close()
        h.agent.close()


def test_optional_modules_are_installed_through_their_hook(tmp_path):
    h = Harness(tmp_path)
    installed: list[str] = []

    def fake_import(name: str) -> Any:
        short = name.rsplit(".", 1)[-1]
        if short == "policy":
            return types.SimpleNamespace(install=lambda node: installed.append("policy"))
        if short == "sensors":
            raise RuntimeError("broken")
        if short == "mirror":
            return types.SimpleNamespace()
        raise ModuleNotFoundError(name=name)

    try:
        result = h.node.install_plugins(import_module=fake_import)
        assert result == {"policy": "installed", "capabilities": "absent", "sensors": "error",
                          "mirror": "no install()", "backup_pull": "absent"}
        assert installed == ["policy"]
    finally:
        h.node.close()
        h.agent.close()


def test_a_failed_install_keeps_nothing_and_the_power_listener_comes_first(tmp_path):
    h = Harness(tmp_path, state=None)
    seen: dict[str, Any] = {}

    class FakePower:  # a PowerListener stand-in: no window
        hwnd = 4242
        started = False

        def start(self) -> bool:
            self.started = True
            return True

    async def speak(_args: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "success", "tool": "pc.speak_local", "message": "spoken"}

    def install(node: NodeRuntime) -> None:  # a C-121 install that fails half-way
        seen["power"] = (node.power_listener.hwnd, node.power_listener.started)
        node.set_state_source(MemoryState("full"))
        node.set_policy(types.SimpleNamespace(check=lambda _call: None))
        node.register_operation("pc.speak_local", speak)
        node.add_task(lambda: asyncio.sleep(0), name="sensor")
        node.add_hint_listener(lambda _hint: None)
        raise RuntimeError("half installed")

    def fake_import(name: str) -> Any:
        if name.endswith(".policy"):
            return types.SimpleNamespace(install=install)
        raise ModuleNotFoundError(name=name)

    try:
        result = install_with_power(h.node, FakePower(), import_module=fake_import)
        assert result["policy"] == "error" and seen["power"] == (4242, True)
        assert isinstance(h.node.policy, BaselinePolicy) and h.node.state.current().mode == "read"  # fail closed
        assert "pc.speak_local" not in h.node.operations.names()
        assert not h.node._extra_tasks and not h.node._hint_listeners
    finally:
        h.node.close()
        h.agent.close()


def test_the_key_loader_uses_the_dpapi_interface(tmp_path):
    home = tmp_path / "node_home"
    home.mkdir()
    pc_key, agent_key = dp.generate_signing_key(), dp.generate_signing_key()
    (home / ns.KEYS_FILE).write_bytes(b"wrapped")
    (home / ns.SECRETS_FILE).write_bytes(b"wrapped-secrets")
    loaded: list[Path] = []

    def load_pc_key(path: Path) -> Any:
        loaded.append(path)
        return pc_key

    def unprotect(_blob: bytes) -> bytes:
        return ns.node_secrets_document({"PINCHTAB_TOKEN": "throwaway"})

    cfg = ns.NodeConfig(data_root=tmp_path, run_dir=tmp_path / "run", agent_public_key=dp.public_key_hex(agent_key),
                        pc_public_key=dp.public_key_hex(pc_key))
    keys = KeyLoader(home, load_pc_key=load_pc_key, unprotect=unprotect).load(cfg)
    assert loaded == [home / ns.KEYS_FILE] and keys.signing_key is pc_key
    assert dp.public_key_hex(keys.agent_public_key) == dp.public_key_hex(agent_key)
    assert keys.secrets == {"PINCHTAB_TOKEN": "throwaway"} and "throwaway" not in repr(keys)
    with pytest.raises(ns.NodeConfigError):
        KeyLoader(home, load_pc_key=load_pc_key, unprotect=unprotect).load(
            dataclasses.replace(cfg, pc_public_key=dp.public_key_hex(agent_key)))
    with pytest.raises(ns.NodeConfigError):
        KeyLoader(home, load_pc_key=load_pc_key).load(dataclasses.replace(cfg, agent_public_key=None))
    assert KeyLoader.__init__.__kwdefaults__["load_pc_key"] is dp.load_pc_key_file


def test_manifest_digests_of_the_committed_manifest():
    manifest_sha, schema_sha = manifest_digests()
    assert len(manifest_sha) == 64 and len(schema_sha) == 64
    assert manifest_digests(lambda: (_ for _ in ()).throw(OSError("x"))) == ("", "")


# --- supervisor and locks ----------------------------------------------------------------------------------------

def _child_env(run_dir: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("PYTEST_CURRENT_TEST", "JARVIS_NO_LOCK"):
        env.pop(name, None)
    env.update({"JARVIS_RUN_DIR": str(run_dir), "PYTHONIOENCODING": "utf-8", **extra})
    return env


PC_BRAIN_LOCKS = ("from src.core import agent_lock\n"  # what src/core/agent.py's __main__ does
                  "agent_lock.acquire_or_exit()\n"
                  "agent_lock.acquire_actor_or_exit(agent_lock.HOLDER_PC_BRAIN)\n")
NODE_LOCKS = ("import sys\nfrom src.core.desktop import supervisor\n"
              "code = supervisor.acquire_locks()\n"
              "if code: sys.exit(code)\n")
HOLD = "print('HELD', flush=True)\nimport sys\nsys.stdin.readline()\n"


class LockHolder:
    def __init__(self, code: str, run_dir: Path) -> None:
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with  # lives until stop()
            [sys.executable, "-c", code + HOLD], cwd=ROOT, env=_child_env(run_dir), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.proc.stdout.readline()), daemon=True).start()
        try:
            line = lines.get(timeout=60).strip()
        except queue.Empty:
            line = ""
        if line != "HELD":
            self.stop()
            raise AssertionError(f"lock holder failed: {line!r}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)


def _run(code: str, run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code + "print('ACQUIRED')\n"], cwd=ROOT, env=_child_env(run_dir),
                          capture_output=True, text=True, timeout=60, check=False)


@pytest.mark.parametrize("first,second,holder_name", [(PC_BRAIN_LOCKS, NODE_LOCKS, "pc-brain"),
                                                      (NODE_LOCKS, PC_BRAIN_LOCKS, "desktop-node")],
                         ids=["pc-brain-first", "node-first"])
def test_the_node_supervisor_and_a_pc_brain_contend_for_the_actor_lock(tmp_path, first, second, holder_name):
    holder = LockHolder(first, tmp_path)
    try:
        result = _run(second, tmp_path)
        assert result.returncode == 3, result.stderr
        assert "ACQUIRED" not in result.stdout
        assert f"already driven by {holder_name}" in result.stderr
    finally:
        holder.stop()
    assert _run(second, tmp_path).returncode == 0  # free again once the first one has gone


def test_a_second_supervisor_exits_3_and_the_agent_entry_takes_both_locks(tmp_path):
    holder = LockHolder(NODE_LOCKS, tmp_path)
    try:
        result = _run(NODE_LOCKS, tmp_path)
        assert result.returncode == 3 and "desktop_node.lock" in result.stderr
    finally:
        holder.stop()
    tree = ast.parse((ROOT / "src" / "core" / "agent.py").read_text(encoding="utf-8"))
    main_block = next(node for node in tree.body if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test))
    calls = [ast.unparse(node.value.func) for node in main_block.body if isinstance(node, ast.Expr)]
    assert calls[:2] == ["acquire_or_exit", "acquire_actor_or_exit"]  # before any livekit import


def test_the_supervisor_refuses_an_elevated_token(tmp_path, monkeypatch):
    home = tmp_path / "node_home"
    home.mkdir()
    data_root = tmp_path / "repo"
    data_root.mkdir()
    (home / ns.NODE_CONFIG_FILE).write_text(json.dumps({"data_root": str(data_root)}), encoding="utf-8")
    monkeypatch.setenv(ns.NODE_HOME_ENV, str(home))
    monkeypatch.setattr(supervisor, "is_elevated", lambda: True)
    monkeypatch.setattr(supervisor, "_setup_logging", lambda _root: None)
    started: list[Any] = []
    monkeypatch.setattr(supervisor.Supervisor, "run", lambda self: started.append(self) or 0)
    assert supervisor.main([]) == EXIT_ELEVATED
    assert not started and not (data_root / "run").exists()  # no lock, token or child before the check


def test_is_elevated_reads_the_token_without_raising():
    assert supervisor.is_elevated() in (True, False)


FAKE_NODE = textwrap.dedent("""
    import json, os, sys, time
    run_dir = os.environ["JARVIS_RUN_DIR"]
    count_file = os.path.join(run_dir, "starts.txt")
    starts = int(open(count_file).read()) + 1 if os.path.exists(count_file) else 1
    open(count_file, "w").write(str(starts))
    with open(os.path.join(run_dir, "desktop_node.json"), "w") as handle:
        json.dump({"updated_at": time.time(), "link": "connected", "node_id": "fake", "mode": "read"}, handle)
    env_ok = "GOOGLE_API_KEY" not in os.environ and os.environ.get("JARVIS_NODE_SUPERVISED") == "1"
    open(os.path.join(run_dir, "env_ok.txt"), "w").write(str(env_ok))
    if starts == 1:
        sys.exit(75)  # a restart-only setting changed
    line = sys.stdin.readline()
    open(os.path.join(run_dir, "stopped_by.txt"), "w").write(line.strip())
    sys.exit(0)
""")


def test_the_supervisor_restarts_the_node_and_stops_on_the_token(tmp_path):
    data_root = tmp_path / "repo"
    data_root.mkdir()
    cfg = ns.NodeConfig(data_root=data_root, run_dir=data_root / "run")
    env = _child_env(cfg.run_dir, GOOGLE_API_KEY="throwaway")
    sup = supervisor.Supervisor(cfg, command=[sys.executable, "-c", FAKE_NODE], health_port=0, base_env=env,
                                backoff_first_s=5.0, tick_s=0.05, restrict_token=lambda _path: None)
    old_run_dir = os.environ.get(ns.RUN_DIR_ENV)
    os.environ[ns.RUN_DIR_ENV] = str(cfg.run_dir)  # as main() does, for agent_lock.run_dir()
    result: dict[str, int] = {}
    thread = threading.Thread(target=lambda: result.update(code=sup.run()), daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 30
        while not ((cfg.run_dir / "starts.txt").exists()
                   and (cfg.run_dir / "starts.txt").read_text() == "2"):
            assert time.monotonic() < deadline, "the node was not restarted"
            time.sleep(0.05)
        assert sup.restarts == 1 and sup.last_exit == EXIT_RESTART  # restarted at once, not after the backoff
        token = health.read_token(cfg.run_dir / health.TOKEN_FILE)
        url = f"http://127.0.0.1:{sup.server.port}"
        with urllib.request.urlopen(f"{url}/health", timeout=5) as response:
            doc = json.loads(response.read())
        assert doc["supervisor"]["restarts"] == 1 and doc["node_id"] == "fake"
        bad = urllib.request.Request(f"{url}/stop", method="POST", headers={health.TOKEN_HEADER: "wrong"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(bad, timeout=5)  # pylint: disable=consider-using-with  # it raises
        assert refused.value.code == 403
        good = urllib.request.Request(f"{url}/stop", method="POST", headers={health.TOKEN_HEADER: token})
        with urllib.request.urlopen(good, timeout=5) as response:
            assert json.loads(response.read()) == {"status": "stopping"}
        thread.join(30)
        assert result == {"code": 0}
        assert (cfg.run_dir / "stopped_by.txt").read_text() == "stop"
        assert (cfg.run_dir / "env_ok.txt").read_text() == "True"  # secrets scrubbed from the child
        assert not (cfg.run_dir / health.TOKEN_FILE).exists() and not agent_lock.probe(agent_lock.ACTOR_LOCK_FILE)
    finally:
        sup.request_stop()
        thread.join(10)
        if old_run_dir is None:
            os.environ.pop(ns.RUN_DIR_ENV, None)
        else:
            os.environ[ns.RUN_DIR_ENV] = old_run_dir


def test_the_health_server_refuses_foreign_hosts():
    stopped: list[bool] = []
    server = health.HealthServer(host="127.0.0.1", port=0, status=lambda: {"status": "ok"},
                                 stop=lambda: stopped.append(True), token=lambda: "t" * 43)
    port = server.start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)  # pylint: disable=consider-using-with  # it raises
        assert refused.value.code == 403
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            assert json.loads(response.read()) == {"status": "ok"}
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nothing", timeout=5)  # pylint: disable=consider-using-with
        assert missing.value.code == 404 and not stopped
    finally:
        server.close()


def test_the_audit_log_keeps_its_lines_while_rotation_is_blocked(tmp_path):
    path = tmp_path / "logs" / "desktop_audit.jsonl"
    log = health.AuditLog(path, max_bytes=300)
    tries: list[int] = []

    def held_open() -> None:  # Windows: a viewer holds the file or a backup open, so the rename fails
        tries.append(1)
        raise PermissionError(13, "in use")

    log._rotate = held_open
    for index in range(12):
        log.record("exec_refused", call_id=f"call-{index:02d}", code="DESKTOP_LOCKED")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 12  # no line lost
    assert len(tries) == 1  # tried again only after ROTATE_RETRY_S, not on every line
    del log._rotate  # the viewer let go
    log._rotate_after = 0.0
    log.record("exec_refused", call_id="call-12", code="DESKTOP_LOCKED")
    assert len(path.with_name(f"{path.name}.1").read_text(encoding="utf-8").splitlines()) == 12
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL")
def test_the_stop_token_is_readable_by_the_current_user_only(tmp_path):
    # pylint: disable=c-extension-no-member
    import win32api  # pylint: disable=import-outside-toplevel,import-error
    import win32security  # pylint: disable=import-outside-toplevel,import-error

    path = tmp_path / health.TOKEN_FILE
    token = health.new_token_file(path)
    assert health.read_token(path) == token and len(token) >= 40
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert dacl.GetAceCount() == 1
    process_token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    user_sid = win32security.GetTokenInformation(process_token, win32security.TokenUser)[0]
    assert dacl.GetAce(0)[2] == user_sid
    control = descriptor.GetSecurityDescriptorControl()[0]
    assert control & win32security.SE_DACL_PROTECTED  # nothing inherited from the folder


def test_the_child_gets_the_data_root_the_snapshot_and_no_secrets(tmp_path):
    cfg = ns.NodeConfig(data_root=tmp_path, run_dir=tmp_path / "run")
    base = {"PATH": "x", "GOOGLE_API_KEY": "throwaway", "PYTHONPATH": "C:\\elsewhere", "PYTHONHOME": "y"}
    env = supervisor.child_environment(base, cfg, code_root=tmp_path / "snapshot", node_sha="abc")
    assert env["PYTHONPATH"] == str(tmp_path / "snapshot") and "PYTHONHOME" not in env
    assert env[ns.DATA_ROOT_ENV] == str(tmp_path) and env[ns.RUN_DIR_ENV] == str(tmp_path / "run")
    assert "GOOGLE_API_KEY" not in env and env["JARVIS_NODE_SUPERVISED"] == "1"
    assert env[supervisor.NODE_SHA_ENV] == "abc"
    command = supervisor.node_command("canary", python=sys.executable)
    assert command[1:] == ["-P", "-m", "src.core.desktop", "node", "--canary"]
    windowed = Path(sys.executable).with_name("pythonw.exe")
    if windowed.is_file():
        assert supervisor.child_python(str(windowed)).lower().endswith("python.exe")


async def test_the_socketio_transport_round_trip_on_loopback():
    """The real python-socketio transport against a local server (127.0.0.1 only, no TLS)."""
    import socketio  # pylint: disable=import-outside-toplevel
    from aiohttp import web  # pylint: disable=import-outside-toplevel

    server = socketio.AsyncServer(async_mode="aiohttp")
    app = web.Application()
    server.attach(app)
    got: list[tuple[str, Any]] = []
    auths: list[Any] = []

    async def on_connect(sid: str, _environ: Any, auth: Any) -> None:
        auths.append(auth)

    async def on_any(event: str, sid: str, data: Any) -> None:
        got.append((event, data))
        await server.emit("anything", f"echo:{data}", to=sid, namespace="/desktop")

    server.on("connect", on_connect, namespace="/desktop")
    server.on("*", on_any, namespace="/desktop")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    transport = link_mod.SocketIOTransport(f"http://127.0.0.1:{port}", "socket.io")
    received: list[Any] = []

    async def receive(payload: Any) -> None:
        received.append(payload)

    transport.set_receiver(receive)
    try:
        await transport.open({link_mod.AUTH_HELLO: '{"typ":"hello"}'})
        assert transport.connected and auths == [{"hello": '{"typ":"hello"}'}]
        await transport.send('{"typ":"notice"}')
        await until(lambda: received, what="echo")
        assert got == [("msg", '{"typ":"notice"}')] and received == ['echo:{"typ":"notice"}']
        sid = next(iter(server.manager.rooms["/desktop"][None]))
        await server.disconnect(sid, namespace="/desktop")
        await asyncio.wait_for(transport.closed(), 5.0)
        assert not transport.connected
    finally:
        await transport.close()
        await runner.cleanup()
