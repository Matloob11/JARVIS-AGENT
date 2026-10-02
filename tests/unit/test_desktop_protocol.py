"""Desktop Link protocol (plan task C-115, contract K-70) and scripts/ops/gen_desktop_keys.py.

Every key is generated for the test or is a published RFC 8032 / RFC 7748 test vector. Nothing reads a real
.env file, touches the network, the VPS or the running agent; SQLite stores and key files live in tmp_path.
"""
# pylint: disable=protected-access,redefined-outer-name
# pylint: disable=too-many-lines  # the C-115 Files line allows this one test file for the whole library
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from services.utils import desktop_protocol as dp
from services.utils.desktop_protocol import DesktopLinkError

ROOT = Path(__file__).resolve().parents[2]
GEN_KEYS_PY = ROOT / "scripts" / "ops" / "gen_desktop_keys.py"
WINDOWS = sys.platform == "win32"
# Published test vectors, not secrets: RFC 8032 section 7.1 TEST 1 and RFC 7748 section 6.1.
RFC8032_SEED = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
RFC8032_PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RFC7748_ALICE = "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
RFC7748_BOB = "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
T0 = 1_759_400_000.0
STORE_AGE_S = 3600.0  # the Link's SeenStores were created this long before T0, as on a node that has been running
NODE, PEER, ROOM, CALL = "pc:laptop", "agent:job-1", "jarvis-hybrid", "c" * 32


class Clock:
    """Fake wall and monotonic clocks, moved by hand."""

    def __init__(self) -> None:
        self.wall_now, self.mono_now = T0, 1000.0

    def wall(self) -> float:
        return self.wall_now

    def mono(self) -> float:
        return self.mono_now

    def advance(self, seconds: float) -> None:
        self.wall_now += seconds
        self.mono_now += seconds


class CountingKey:
    """A public key that counts verify() calls, to show that cheap checks run before the signature."""

    def __init__(self, key: Any) -> None:
        self.key, self.calls = key, 0

    def verify(self, sig: bytes, data: bytes) -> None:
        self.calls += 1
        self.key.verify(sig, data)


class Link:
    """A node, one agent peer and the bridge relay, with the handshake done."""

    def __init__(self, tmp: Path) -> None:
        self.tmp, self.clock = tmp, Clock()
        self.pc_key, self.agent_key = dp.generate_signing_key(), dp.generate_signing_key()
        self.keys = {"pc": self.pc_key.public_key(), "agent": self.agent_key.public_key()}
        self.clock.advance(-STORE_AGE_S)  # created_at, the refusal floor's base, lies before every test message
        self.stores = [self.store(name) for name in ("node", "agent", "bridge")]
        self.clock.advance(STORE_AGE_S)  # wall and monotonic together: no wall-clock step
        self.pc, self.agent = dp.LinkSigner(NODE, self.pc_key), dp.LinkSigner(PEER, self.agent_key)
        self.node_rx = dp.Receiver(NODE, self.keys, self.stores[0], wall=self.clock.wall)
        self.agent_rx = dp.Receiver(PEER, self.keys, self.stores[1], wall=self.clock.wall)
        self.bridge_rx = dp.Receiver("bridge", self.keys, self.stores[2], wall=self.clock.wall)
        self.envs: dict[str, dict[str, Any]] = {}
        self.sess, self.agent_view, self.node_view = self._connect()

    def store(self, namespace: str, filename: str | None = None, **options: Any) -> dp.SeenStore:
        """A SeenStore on the fake clocks (so wall_step_s follows them), in <filename or namespace>.db."""
        return dp.SeenStore(self.tmp / f"{filename or namespace}.db", namespace=namespace, wall=self.clock.wall,
                            mono=self.clock.mono, **options)

    def handshake(self, node_now: float | None = None) -> None:
        """The node reconnects: new session nonce and ephemeral keys, and the agent peer answers. node_now is the
        node clock that signs the hello (default: the VPS clock, which signs the agent_hello)."""
        self.sess, self.agent_view, self.node_view = self._connect(node_now)

    def _connect(self, node_now: float | None = None) -> tuple[str, dp.PeerSession, dp.PeerSession]:
        """A node hello with a new sess and ephemeral key, then the agent peer's agent_hello."""
        sess, now = dp.new_session_nonce(), self.clock.wall_now
        node_eph, agent_eph = dp.new_ephemeral_key(), dp.new_ephemeral_key()
        hello = self.pc.build("hello", dp.node_hello_body(node_id="laptop", eph=node_eph, sess=sess,
                                                          fields={"mode": "full"}),
                              to="agent:*", sess=sess, now=now if node_now is None else node_now)
        self.bridge_rx.verify(hello)
        self.bridge_rx.bind(sess)
        verified = self.agent_rx.verify(hello)
        agent_view = dp.accept_node_hello(verified, own_eph=agent_eph, peer_id="job-1")
        self.agent_rx.bind(agent_view.sess, verified.sender)
        agent_hello = self.agent.build("agent_hello", dp.agent_hello_body(
            peer_id="job-1", job_id="job-1", room=ROOM, eph=agent_eph, sess=sess), to=NODE, room=ROOM, now=now)
        self.bridge_rx.verify(agent_hello)
        self.node_rx.bind(sess)
        node_view = dp.accept_agent_hello(self.node_rx.verify(agent_hello), own_eph=node_eph, sess=sess,
                                          node_id="laptop")
        self.envs.update(hello=hello, agent_hello=agent_hello)
        return sess, agent_view, node_view

    def exec_msg(self, args: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
        args = {"text": "hello"} if args is None else args
        fields: dict[str, Any] = {"to": NODE, "sess": self.sess, "room": ROOM, "origin": "llm",
                                  "session_key": self.agent_view.key, "call_id": CALL, "tool": "type_text_tool",
                                  "seq": {"speech_id": "sp-1", "n": 0}, "now": self.clock.wall_now}
        body = {"args": args, "args_sha256": dp.args_sha256(args), "timeout_s": 5, "lock_wait_s": 1}
        return self.agent.build("exec", body, **(fields | overrides))

    def ping(self, sender: dp.LinkSigner, now: float) -> dict[str, Any]:
        return sender.build("ping", {}, to=NODE if sender is self.agent else PEER, sess=self.sess, now=now)

    def sync_request(self, now: float) -> dict[str, Any]:
        """A plaintext agent message with no effect: freshness tests (a ping skips check 5)."""
        return self.agent.build("sync_request", {}, to=NODE, sess=self.sess, now=now)


@pytest.fixture
def link(tmp_path: Path) -> Iterator[Link]:
    result = Link(tmp_path)
    yield result
    for store in result.stores:
        store.close()


def _sign_raw(key: Ed25519PrivateKey, header: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Signs an envelope without the builder's checks, as a buggy or compromised sender could."""
    env = {**header, "body": body}
    env["sig"] = key.sign(dp.MSG_CONTEXT + dp.canonical_json(env)).hex()
    return env


def _refused(call: Callable[[], Any]) -> DesktopLinkError:
    with pytest.raises(DesktopLinkError) as err:
        call()
    return err.value


# --- canonical JSON and fixed vectors -----------------------------------------------------------------------

@pytest.mark.parametrize(("value", "expected"), [
    ({"b": 1, "a": [True, None, "x"]}, b'{"a":[true,null,"x"],"b":1}'),
    ({"z": {"y": 2, "x": 1}, "a": {}}, b'{"a":{},"z":{"x":1,"y":2}}'),
    ({"k": "☃", "é": "ü"}, '{"k":"☃","é":"ü"}'.encode("utf-8")),
    ({"q": '"\\\n\x01'}, b'{"q":"\\"\\\\\\n\\u0001"}'),
    ({"ts": 1759400000.125, "n": -0.0, "big": 2 ** 64}, b'{"big":18446744073709551616,"n":-0.0,"ts":1759400000.125}'),
    ([], b"[]"),
])
def test_canonical_json_vectors(value: Any, expected: bytes) -> None:
    assert dp.canonical_json(value) == expected
    assert dp.canonical_json(dp.loads_strict(expected)) == expected


def _nested(depth: int) -> Any:
    value: Any = 1
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("value", [float("nan"), {"x": float("inf")}, [-float("inf")], {1: "x"}, {"s": "\ud800"},
                                   {"b": b"x"}, {1, 2}, _nested(40)])
def test_canonical_json_refuses_ambiguous_values(value: Any) -> None:
    with pytest.raises(dp.CanonicalJsonError):
        dp.canonical_json(value)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b"[Infinity]", b"[1e400]", b"\xff",
                                 "﻿{}".encode("utf-8"), b"[" * 40 + b"]" * 40, b"{", b'{"x":"\\ud800"}',
                                 b'{"\\udc00":1}', b'["a\\udfffb"]', b"[" + b"9" * 5000 + b"]"])
def test_loads_strict_refuses(raw: bytes) -> None:
    with pytest.raises(dp.CanonicalJsonError):
        dp.loads_strict(raw)
    assert dp.loads_strict(b'["\\ud83d\\ude00"]') == ["\U0001f600"]  # a surrogate pair is one character


def test_envelope_signature_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixed id, ts and the RFC 8032 key give fixed signed bytes and a fixed Ed25519 signature."""
    key = dp.signing_key_from_hex(RFC8032_SEED)
    assert dp.public_key_hex(key) == RFC8032_PUBLIC
    monkeypatch.setattr(dp, "new_message_id", lambda: "0123456789abcdef0123456789abcdef")
    env = dp.LinkSigner(PEER, key).build("ping", {}, to=NODE, sess="ab" * 16, room=ROOM, now=T0)
    signed = (b'jarvis/desktop/msg/v1|{"body":{},"from":"agent:job-1","id":"0123456789abcdef0123456789abcdef",'
              b'"origin":"internal","room":"jarvis-hybrid","sess":"abababababababababababababababab",'
              b'"to":"pc:laptop","ts":1759400000.0,"typ":"ping","v":1}')
    assert dp.MSG_CONTEXT + dp.canonical_json({k: v for k, v in env.items() if k != "sig"}) == signed
    assert env["sig"] == ("27464c8d764e0e45e97b4cd8208323dd0bfe25afc1ba88f5e5bc3c2b3126ebb3"
                          "83fccbd0940c0266384a3cdfa075f00d12f6ce098c42ff3c5a9c8f6d4db4120c")
    key.public_key().verify(bytes.fromhex(env["sig"]), signed)


def test_session_key_vector() -> None:
    """HKDF-SHA256(X25519, salt = sess || peer_id, info 'jarvis/desktop/aead/v1') with the RFC 7748 keys."""
    alice = X25519PrivateKey.from_private_bytes(bytes.fromhex(RFC7748_ALICE))
    bob = X25519PrivateKey.from_private_bytes(bytes.fromhex(RFC7748_BOB))
    node_side = dp.derive_session_key(alice, dp.eph_public_hex(bob), sess="ab" * 16, peer_id="job-1")
    agent_side = dp.derive_session_key(bob, dp.eph_public_hex(alice), sess="ab" * 16, peer_id="job-1")
    sealed = node_side.seal(b"payload", b"aad")
    expected = ChaCha20Poly1305(bytes.fromhex("99fc6e75e67961917e3fc1809e7f1329c6a937d1acd1d4e8b53a64610b2bd27c"))
    assert expected.decrypt(sealed[:12], sealed[12:], b"aad") == b"payload"
    assert agent_side.open(sealed, b"aad") == b"payload"
    other_peer = dp.derive_session_key(bob, dp.eph_public_hex(alice), sess="ab" * 16, peer_id="job-2")
    assert _refused(lambda: other_peer.open(sealed, b"aad")).reason == "decrypt"
    assert _refused(lambda: dp.derive_session_key(alice, "00" * 32, sess="ab" * 16, peer_id="job-1")).reason == \
        "handshake"  # an all-zero (low-order) peer key is refused


def test_digest_helpers_and_proto_negotiation() -> None:
    assert dp.args_sha256({"b": 1, "a": "x"}) == hashlib.sha256(b'{"a":"x","b":1}').hexdigest()
    assert dp.digest_equal("ABcd", "abCD") and not dp.digest_equal("ab", "ac") and not dp.digest_equal(None, "x")
    assert dp.negotiate_proto([1], [2, 1]) == 1
    for theirs in ([2], ["1", True], []):
        err = _refused(lambda theirs=theirs: dp.negotiate_proto([1], theirs))
        assert (err.reason, err.code) == ("proto", "PROTO_MISMATCH")


# --- envelopes: integrity, confidentiality, replay ----------------------------------------------------------

def test_roundtrip_and_the_bridge_cannot_read_bodies(link: Link) -> None:
    env = link.exec_msg({"text": "SECRET-ARGS-MARKER"})
    assert b"SECRET-ARGS-MARKER" not in dp.canonical_json(env) and env["body"]["enc"] == dp.AEAD_ALG
    relayed = link.bridge_rx.verify(env)  # the relay can verify the header and signature...
    guess = dp.SessionKey(os.urandom(32), sess=link.sess, peer_id="job-1")
    assert _refused(lambda: dp.open_body(relayed, guess)).reason == "decrypt"  # ...but cannot read the body
    got = link.node_rx.verify(env)
    assert dp.open_body(got, link.node_view.key)["args"] == {"text": "SECRET-ARGS-MARKER"}
    assert (got.typ, got.sender, got.call_id, got.sess, got.encrypted) == ("exec", PEER, CALL, link.sess, True)
    assert got.header["start_by"] == pytest.approx(T0 + dp.START_BY_DELAY_S)
    result = link.pc.build("result", {"status": "success", "effect": "done"}, to=PEER, sess=link.sess,
                           session_key=link.node_view.key, call_id=CALL, now=T0 + 1)
    assert dp.open_body(link.agent_rx.verify(result), link.agent_view.key)["effect"] == "done"
    with pytest.raises(TypeError):
        dp.VerifiedEnvelope({}, {}, 0)  # only Receiver.verify() creates one


def _flip_base64(data: str) -> str:
    raw = bytearray(base64.b64decode(data))
    raw[-1] ^= 1
    return base64.b64encode(bytes(raw)).decode("ascii")


TAMPERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "typ": lambda env: env.update(typ="cancel"),
    "id": lambda env: env.update(id="f" * 32),
    "ts": lambda env: env.update(ts=env["ts"] + 1),
    "from": lambda env: env.update({"from": "agent:job-2"}),
    "to": lambda env: env.update(to="pc:other"),
    "sess": lambda env: env.update(sess="e" * 32),
    "room": lambda env: env.update(room="jarvis-canary-1"),
    "origin": lambda env: env.update(origin="scheduler"),
    "call_id": lambda env: env.update(call_id="d" * 32),
    "tool": lambda env: env.update(tool="run_cmd_command"),
    "seq": lambda env: env.update(seq={"speech_id": "sp-1", "n": 7}),
    "start_by": lambda env: env.update(start_by=env["start_by"] + 60),
    "body": lambda env: env["body"].update(data=_flip_base64(env["body"]["data"])),
    "sig": lambda env: env.update(sig=("1" if env["sig"][0] == "0" else "0") + env["sig"][1:]),
    "added_field": lambda env: env.update(note="relay"),
    "removed_field": lambda env: env.pop("call_id"),
    "version": lambda env: env.update(v=2),
}


@pytest.mark.parametrize("change", sorted(TAMPERS))
def test_any_change_by_the_relay_fails_verification(link: Link, change: str) -> None:
    env = link.exec_msg()
    forged = copy.deepcopy(env)
    TAMPERS[change](forged)
    for receiver in (link.node_rx, link.bridge_rx):
        err = _refused(lambda receiver=receiver: receiver.verify(forged))
        assert err.reason in {"signature", "route", "format", "version"}
        assert not err.authentic and err.refusal_code is None  # never answered: it may be forged
    assert dp.open_body(link.node_rx.verify(env), link.node_view.key)["args"] == {"text": "hello"}


def test_plaintext_bodies_handshake_keys_and_foreign_keys_are_bound(link: Link) -> None:
    control = link.agent.build("control", {"kind": "desktop_stop"}, to=NODE, sess=link.sess, now=T0)
    control["body"]["kind"] = "stop_all"
    assert _refused(lambda: link.node_rx.verify(control)).reason == "signature"
    hello = copy.deepcopy(link.envs["hello"])  # a bridge swapping the ephemeral key (MITM) breaks the signature
    hello["body"]["eph_pub"] = dp.eph_public_hex(dp.new_ephemeral_key())
    assert _refused(lambda: link.agent_rx.verify(hello)).reason == "signature"
    for signer in (dp.LinkSigner(PEER, dp.generate_signing_key()), dp.LinkSigner(PEER, link.pc_key)):
        ping = signer.build("ping", {}, to=NODE, sess=link.sess, now=T0)
        assert _refused(lambda ping=ping: link.node_rx.verify(ping)).reason == "signature"


def test_replay_is_refused_in_the_same_and_across_sessions(link: Link) -> None:
    env = link.exec_msg()
    link.node_rx.verify(env)
    err = _refused(lambda: link.node_rx.verify(env))
    assert (err.reason, err.authentic, err.refusal_code) == ("replay", True, None)
    for name, receiver in (("agent_hello", link.node_rx), ("hello", link.agent_rx)):  # session-free hellos
        assert _refused(lambda name=name, receiver=receiver: receiver.verify(link.envs[name])).reason == "replay"
    restarted_store = link.store("node")
    restarted = dp.Receiver(NODE, link.keys, restarted_store, sess=link.sess, wall=link.clock.wall)
    assert _refused(lambda: restarted.verify(env)).reason == "replay"  # persisted across a node restart
    restarted_store.close()
    link.handshake()  # the node reconnects: new session nonce and keys
    err = _refused(lambda: link.node_rx.verify(env))  # check 6 fails, and the id already ran: a replay
    assert (err.reason, err.authentic, err.refusal_code) == ("replay", True, None)
    fresh = link.exec_msg()
    assert dp.open_body(link.node_rx.verify(fresh), link.node_view.key)["args"] == {"text": "hello"}
    closed = dp.SeenStore(link.tmp / "closed.db")
    closed.close()
    unchecked = dp.Receiver(NODE, link.keys, closed, sess=link.sess, wall=link.clock.wall)
    err = _refused(lambda: unchecked.verify(link.exec_msg()))
    assert (err.reason, err.authentic, err.refusal_code) == ("seen_store", True, None)  # fails closed, no answer


def _outcome(receiver: dp.Receiver, env: dict[str, Any]) -> tuple[str, bool, str | None]:
    err = _refused(lambda: receiver.verify(env))
    return err.reason, err.authentic, err.refusal_code


def test_a_refusal_never_contradicts_what_the_node_did(link: Link) -> None:
    """A signed refusal reads 'not executed' (DESKTOP_NOT_STARTED): never for an exec that ran or could still run.

    The compromised bridge may drop the result of an exec that ran, then deliver that exec again after a
    reconnect or after the window. It may also hold a 'future' exec until its time comes.
    """
    ran = link.exec_msg()
    link.node_rx.verify(ran)  # runs; the bridge drops its result
    link.clock.advance(10)
    held = link.exec_msg(call_id="d" * 32)  # signed for this session; the bridge holds it back
    link.handshake()  # the bridge closes the node socket: a new session
    assert _outcome(link.node_rx, ran) == ("replay", True, None)  # check 6 fails, but it ran: no refusal
    assert _outcome(link.node_rx, held) == ("session", True, "DESKTOP_REJECTED")  # never ran: the refusal is true
    assert _outcome(link.node_rx, held) == ("replay", True, None)  # ...and final
    later = link.exec_msg(call_id="e" * 32)
    link.node_rx.verify(later)
    link.clock.advance(121)
    assert _outcome(link.node_rx, later) == ("replay", True, None)  # check 5 fails, but it ran: no refusal
    stale = link.exec_msg(call_id="f" * 32, now=link.clock.wall_now - 121)
    assert _outcome(link.node_rx, stale) == ("stale", True, "DESKTOP_REJECTED")
    early = link.exec_msg(call_id="1" * 32, now=link.clock.wall_now + 180)  # the agent clock is 3 min ahead
    assert _outcome(link.node_rx, early) == ("future", True, "DESKTOP_REJECTED")
    link.clock.advance(61)  # now inside the window: it must not run after its refusal
    assert _outcome(link.node_rx, early) == ("replay", True, None)


def test_a_refusal_needs_the_seen_store(link: Link) -> None:
    """Without an answer from the SeenStore, a check 5 or 6 failure cannot prove the exec never ran."""
    stale = link.exec_msg(now=link.clock.wall_now - 121)
    closed = dp.SeenStore(link.tmp / "closed_late.db")
    closed.close()
    broken = dp.Receiver(NODE, link.keys, closed, sess=link.sess, wall=link.clock.wall)
    assert _outcome(broken, stale) == ("seen_store", True, None)  # fails closed, no answer
    storeless = dp.Receiver(NODE, link.keys, None, sess=link.sess, wall=link.clock.wall)
    assert storeless.refusal_horizon_s == float("-inf")  # no age is young enough without a store
    assert storeless.refusal_floor_s == float("inf")  # and no ts is late enough
    err = _refused(lambda: storeless.verify(stale))
    assert (err.reason, err.authentic, err.refusable, err.refusal_code) == ("stale", True, False, None)
    early = link.exec_msg(now=link.clock.wall_now + 180)
    other_sess = dp.SessionKey(os.urandom(32), sess="e" * 32, peer_id="job-1")
    elsewhere = link.exec_msg(sess=other_sess.sess, session_key=other_sess)
    assert _outcome(storeless, early) == ("future", True, None)  # the same for every check 5/6 failure
    assert _outcome(storeless, elsewhere) == ("session", True, None)
    header = {"v": 1, "typ": "welcome", "id": dp.new_message_id(), "ts": T0, "from": PEER, "to": NODE,
              "sess": link.sess, "room": ROOM, "origin": "llm"}
    wrong_way = _sign_raw(link.agent_key, header, {})  # check 8 never passes later, so refusing it stays true
    assert _outcome(storeless, wrong_way) == ("direction", True, "DESKTOP_REJECTED")


class SeenIdsAdapter:  # pylint: disable=too-few-public-methods
    """What a bridge might write over its C-13 seen_ids table: check-and-add without the K-70 store semantics."""

    def __init__(self) -> None:
        self.ids: set[tuple[str, str]] = set()

    def check_and_add(self, kind: str, value: str, *, sess: str = "") -> bool:
        new = (kind, value) not in self.ids
        self.ids.add((kind, value))
        return new


def test_a_receiver_takes_only_the_library_seen_store(link: Link) -> None:
    """Check 7 and the refusal bounds use SeenStore's retention_s, created_at and wall_step_s, so another store is
    a configuration error at construction, never an AttributeError on a skewed or old-session message. The
    bridge's own library store turns those into the documented refusals."""
    for store in (SeenIdsAdapter(), object(), str(link.tmp / "bridge.db")):
        with pytest.raises(TypeError):
            dp.Receiver(dp.BRIDGE_IDENT, link.keys, store, sess=link.sess)  # type: ignore[arg-type]
    with link.store("bridge", "desktop_seen") as seen:  # its own file on the bridge volume, never bridge.db
        bridge = dp.Receiver(dp.BRIDGE_IDENT, link.keys, seen, sess=link.sess, wall=link.clock.wall)
        skewed = _notice(link, "power", {"state": "resume"}, now=link.clock.wall_now + 180)
        assert _outcome(bridge, skewed)[:2] == ("future", True)  # authentic and refused only for its time: K-74 warns
        assert _outcome(bridge, skewed)[:2] == ("replay", True)  # a replay, never a second clock-skew warning
        old_session = _notice(link, "power", {"state": "resume"}, sess="e" * 32)
        assert _outcome(bridge, old_session)[:2] == ("session", True)


def test_no_refusal_once_the_seen_store_may_have_forgotten_a_run(link: Link) -> None:
    """The SeenStore forgets an id after 24 h, so a new id shows 'never ran' only for a message young enough that
    its id would still be there: refusal_horizon_s = min(retention - 2 x window, 1 h). A bridge that keeps a copy
    of an exec that ran cannot get a signed 'not executed' for it a day later."""
    assert link.node_rx.refusal_horizon_s == dp.REFUSAL_HORIZON_S == 3600.0  # the cap, not 24 h - 240 s
    ran = link.exec_msg()
    link.node_rx.verify(ran)  # runs at T0; the bridge drops its result and keeps a copy
    link.clock.advance(dp.SEEN_RETENTION_S - 60)
    assert _outcome(link.node_rx, ran) == ("replay", True, None)  # T0 + 24 h - 60 s: still in the store
    link.clock.advance(120)  # T0 + 24 h + 60 s: the store has forgotten it
    err = _refused(lambda: link.node_rx.verify(ran))
    assert (err.reason, err.authentic, err.refusable, err.refusal_code) == ("stale", True, False, None)
    assert _outcome(link.node_rx, ran) == ("replay", True, None)  # recorded again all the same
    now = link.clock.wall_now
    for age, code in ((121, "DESKTOP_REJECTED"), (3599.5, "DESKTOP_REJECTED"), (3600, None), (3600.5, None),
                      (dp.SEEN_RETENTION_S + 60, None)):  # never-seen execs
        assert _outcome(link.node_rx, link.exec_msg(now=now - age)) == ("stale", True, code), age
    estimator = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)  # the agent clock is 50 s ahead
    link.node_rx.clock = estimator
    ping = link.ping(link.pc, now)
    estimator.ping_sent(ping)
    pong = link.agent.build("pong", {"echo": ping["id"], "t1": now + 50}, to=NODE, sess=link.sess, now=now + 50)
    link.node_rx.verify(pong)  # a Receiver with a clock hands it every pong it verifies
    assert estimator.offset_s == 50.0
    assert _outcome(link.node_rx, link.exec_msg(now=now + 50 - 3599.5)) == ("stale", True, "DESKTOP_REJECTED")
    assert _outcome(link.node_rx, link.exec_msg(now=now + 50 - 3600)) == ("stale", True, None)  # age on its clock


def test_the_refusal_horizon_follows_a_shorter_retention(link: Link) -> None:
    """Below 1 h + 2 windows of retention the store, not the cap, bounds the horizon: retention - 2 x window."""
    with link.store("node", "short", retention_s=600.0) as short:
        receiver = dp.Receiver(NODE, link.keys, short, sess=link.sess, wall=link.clock.wall)
        assert short.retention_s == 600.0 and receiver.refusal_horizon_s == 600.0 - 2 * dp.FRESHNESS_WINDOW_S
        ran = link.exec_msg()
        receiver.verify(ran)
        link.clock.advance(300)
        assert _outcome(receiver, ran) == ("replay", True, None)
        link.clock.advance(301)  # 601 s after it ran: the id has left this store
        assert _outcome(receiver, ran) == ("stale", True, None)
        now = link.clock.wall_now
        assert _outcome(receiver, link.exec_msg(now=now - 359.5)) == ("stale", True, "DESKTOP_REJECTED")
        assert _outcome(receiver, link.exec_msg(now=now - 360)) == ("stale", True, None)


def test_a_far_future_exec_gets_no_refusal(link: Link) -> None:
    """The horizon bounds |age|: a 'future' exec refused now stays a replay only while the store keeps its id, so
    one dated further ahead than the horizon gets no refusal, as it may still run once its time comes."""
    now = link.clock.wall_now
    for ahead, code in ((121, "DESKTOP_REJECTED"), (3599.5, "DESKTOP_REJECTED"), (3600, None), (7200, None)):
        assert _outcome(link.node_rx, link.exec_msg(now=now + ahead)) == ("future", True, code), ahead
    far = link.exec_msg(now=now + dp.SEEN_RETENTION_S + 100)  # signed 24 h + 100 s ahead (an agent clock or bug)
    err = _refused(lambda: link.node_rx.verify(far))
    assert (err.reason, err.authentic, err.refusable, err.refusal_code) == ("future", True, False, None)
    link.clock.advance(dp.SEEN_RETENTION_S + 50)  # its time has come, and the store has dropped its id
    assert link.node_rx.verify(far).typ == "exec"  # it runs now, and no refusal ever said it would not


def test_a_short_retention_bounds_future_refusals_too(link: Link) -> None:
    """A retention below 1 h + 2 windows bounds the horizon on both sides; one below 2 windows (a negative
    horizon) allows no refusal for a check 5 or 6 failure at all."""
    window = dp.FRESHNESS_WINDOW_S
    with link.store("node", "short", retention_s=600.0) as short:
        receiver = dp.Receiver(NODE, link.keys, short, sess=link.sess, wall=link.clock.wall)
        link.clock.advance(2 * window)  # past the new store's refusal floor
        now = link.clock.wall_now
        assert _outcome(receiver, link.exec_msg(now=now + 359.5)) == ("future", True, "DESKTOP_REJECTED")
        assert _outcome(receiver, link.exec_msg(now=now + 360)) == ("future", True, None)
        far = link.exec_msg(now=now + 600 + 100)  # retention + 100 s ahead
        assert _outcome(receiver, far) == ("future", True, None)
        link.clock.advance(650)  # in its window now, and the 600 s store has dropped its id
        assert receiver.verify(far).typ == "exec"
    with link.store("node", "tiny", retention_s=200.0) as tiny:
        receiver = dp.Receiver(NODE, link.keys, tiny, sess=link.sess, wall=link.clock.wall)
        assert receiver.refusal_horizon_s == 200.0 - 2 * window < 0
        link.clock.advance(2 * window)
        held = link.exec_msg(now=link.clock.wall_now + 130)
        assert _outcome(receiver, held) == ("future", True, None)
        link.clock.advance(205)  # in its window, and the 200 s store has forgotten it
        assert receiver.verify(held).typ == "exec"
        assert _outcome(receiver, link.exec_msg(now=link.clock.wall_now - 121)) == ("stale", True, None)


def test_no_refusal_for_an_exec_that_may_have_run_before_the_store_existed(link: Link) -> None:
    """refusal_floor_s = SeenStore.created_at + 2 x window (with no clock step): a recreated store (a deleted run/
    folder, a new data root or namespace) cannot show that an older exec never ran, as it may have run against the
    store before."""
    window = dp.FRESHNESS_WINDOW_S
    ran = link.exec_msg()
    link.node_rx.verify(ran)  # runs at T0; the bridge drops its result and keeps a copy
    link.clock.advance(10)  # the node restarts on a recreated, empty store and reconnects
    with link.store("node", "recreated") as store:
        node_rx = dp.Receiver(NODE, link.keys, store, wall=link.clock.wall)
        assert store.created_at == T0 + 10 and store.wall_step_s == 0.0
        assert node_rx.refusal_floor_s == T0 + 10 + 2 * window
        link.handshake()
        node_rx.bind(link.sess)
        link.clock.advance(20)  # the bridge delivers the exec that ran again, 30 s after it ran
        assert _outcome(node_rx, ran) == ("session", True, None)  # new to this store, yet it may have run
        assert _outcome(node_rx, ran) == ("replay", True, None)
        link.clock.advance(node_rx.refusal_floor_s - link.clock.wall_now)  # the store is now 2 windows old
        before = link.exec_msg(call_id="d" * 32, now=node_rx.refusal_floor_s - 0.5)
        after = link.exec_msg(call_id="e" * 32)  # signed at the floor: it cannot have run before this store
        link.handshake()  # both held by the bridge across a reconnect
        node_rx.bind(link.sess)
        assert _outcome(node_rx, before) == ("session", True, None)
        assert _outcome(node_rx, after) == ("session", True, "DESKTOP_REJECTED")  # the normal case is unchanged
        link.clock.advance(200)
        now = link.clock.wall_now
        estimator = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
        node_rx.clock = estimator
        ping = link.ping(link.pc, now)
        estimator.ping_sent(ping)
        pong = link.agent.build("pong", {"echo": ping["id"], "t1": now + 50}, to=NODE, sess=link.sess, now=now + 50)
        node_rx.verify(pong)
        assert estimator.offset_s == 50.0  # the agent clock is 50 s ahead
        floor = node_rx.refusal_floor_s  # compared with ts - offset: the floor is on the node's own clock
        assert _outcome(node_rx, link.exec_msg(call_id="f" * 32, now=floor + 50 - 0.5)) == ("stale", True, None)
        assert _outcome(node_rx, link.exec_msg(call_id="1" * 32, now=floor + 50)) == ("stale", True,
                                                                                       "DESKTOP_REJECTED")


def test_the_refusal_floor_rises_with_a_forward_step_of_the_node_clock(link: Link) -> None:
    """created_at is the node clock as it read then, while ts - offset uses the current offset: after a forward
    step of the node clock, an exec that ran before the store existed would look newer than created_at + 2 windows.
    The floor adds SeenStore.wall_step_s (wall against monotonic since the store was opened)."""
    window, jump = dp.FRESHNESS_WINDOW_S, [0.0]

    def node_wall() -> float:
        return link.clock.wall_now + jump[0]

    ran = link.exec_msg()
    link.node_rx.verify(ran)  # runs at T0; the bridge drops its result and keeps a copy
    link.clock.advance(10)  # the node restarts on a recreated, empty store and reconnects
    with dp.SeenStore(link.tmp / "stepped.db", namespace="node", wall=node_wall, mono=link.clock.mono) as store:
        estimator = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
        node_rx = dp.Receiver(NODE, link.keys, store, clock=estimator, wall=node_wall)
        link.handshake()
        node_rx.bind(link.sess)
        link.clock.advance(5)
        jump[0] = 600.0  # then the laptop clock steps 10 min forward (a time sync, a manual change)
        assert store.wall_step_s == 600.0 and node_rx.refusal_floor_s == T0 + 10 + 600 + 2 * window
        assert _probe(link, estimator, node_wall, by_agent=False, receiver=node_rx) == "stale"  # yet measured
        assert estimator.offset_s == pytest.approx(-600, abs=0.1)
        link.clock.advance(25)  # the bridge delivers the exec that ran again, 40 s after it ran
        assert _outcome(node_rx, ran) == ("session", True, None)  # ts - offset is T0 + 600: no false refusal
        link.clock.advance(T0 + 10 + 2 * window + 1 - link.clock.wall_now)  # the store is 2 windows old (VPS time)
        late = link.exec_msg(call_id="e" * 32)  # signed now: it cannot have run before this store
        link.handshake()  # held by the bridge across a reconnect
        node_rx.bind(link.sess)
        assert _outcome(node_rx, late) == ("session", True, "DESKTOP_REJECTED")  # the normal case is unchanged
        jump[0] = -600.0  # a backward step lowers ts - offset, which is safe: it adds nothing
        assert store.wall_step_s == 0.0


def test_no_refusal_while_the_offset_is_unmeasured(link: Link) -> None:
    """The refusal bounds put ts on the node clock with the measured offset, so a check 5 or 6 failure may be
    refused only while the Receiver's ClockEstimator has a sample: not before the first pong, not while it must
    re-measure, not after its sample expired. A Receiver without an estimator keeps K-70's own-clock rule."""
    estimator = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.node_rx.clock = estimator

    def stale() -> tuple[str, bool, str | None]:
        return _outcome(link.node_rx, link.exec_msg(call_id=dp.new_message_id(), now=link.clock.wall_now - 121))

    assert stale() == ("stale", True, None)  # before the first pong
    assert isinstance(_probe(link, estimator, link.clock.wall, by_agent=False), dp.VerifiedEnvelope)
    assert stale() == ("stale", True, "DESKTOP_REJECTED")  # measured: offset 0
    estimator.reset()
    assert stale() == ("stale", True, None)  # must re-measure
    assert isinstance(_probe(link, estimator, link.clock.wall, by_agent=False), dp.VerifiedEnvelope)
    assert stale() == ("stale", True, "DESKTOP_REJECTED")
    link.clock.advance(601)
    assert estimator.offset_s is None and stale() == ("stale", True, None)  # the sample expired
    link.node_rx.clock = None
    assert stale() == ("stale", True, "DESKTOP_REJECTED")  # no estimator: the own clock is the rule


def test_no_false_refusal_for_a_replay_before_the_first_pong(link: Link) -> None:
    """The laptop clock is set 5 min back after two execs ran, then the node restarts on a recreated store, whose
    created_at is read on the stepped clock. With offset 0 an exec that ran would look newer than the store, so
    a replay before the first pong gets no refusal; once the offset is measured, the floor holds."""
    step = [0.0]

    def node_wall() -> float:
        return link.clock.wall_now + step[0]

    ran, ran_too = link.exec_msg(), link.exec_msg(call_id="d" * 32)
    for env in (ran, ran_too):
        link.node_rx.verify(env)  # both run at T0; the bridge drops their results and keeps copies
    link.clock.advance(5)
    step[0] = -300.0  # a bad time sync sets the laptop clock 5 min back
    link.clock.advance(5)  # then the node restarts on a recreated store
    with dp.SeenStore(link.tmp / "stepped_back.db", namespace="node", wall=node_wall, mono=link.clock.mono) as store:
        estimator = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
        node_rx = dp.Receiver(NODE, link.keys, store, clock=estimator, wall=node_wall)
        assert node_rx.refusal_floor_s == T0 + 10 - 300 + 2 * dp.FRESHNESS_WINDOW_S < T0  # below their ts
        link.handshake()
        node_rx.bind(link.sess)
        link.clock.advance(30)  # the bridge replays one before the node's first pong
        assert _outcome(node_rx, ran) == ("future", True, None)  # with offset 0 it would get DESKTOP_REJECTED
        assert _probe(link, estimator, node_wall, by_agent=False, receiver=node_rx) == "future"  # measured...
        assert estimator.offset_s == pytest.approx(300, abs=0.1)
        assert _outcome(node_rx, ran_too) == ("session", True, None)  # ...and ts - offset is below the floor


@pytest.mark.parametrize(("skew", "reason"), [(-121, "stale"), (-119, None), (119, None), (121, "future")])
def test_freshness_uses_the_receivers_own_clock(link: Link, skew: int, reason: str | None) -> None:
    message = link.sync_request(T0 + skew)
    if reason is None:
        assert link.node_rx.verify(message).typ == "sync_request"
        return
    err = _refused(lambda: link.node_rx.verify(message))
    assert (err.reason, err.authentic, err.refusal_code) == (reason, True, "DESKTOP_REJECTED")


def test_freshness_window_follows_the_offset_from_signed_pongs(link: Link) -> None:
    clock, agent_ahead = link.clock, 50.0
    estimator = dp.ClockEstimator(wall=clock.wall, mono=clock.mono)
    link.node_rx.clock = estimator
    ping = link.ping(link.pc, clock.wall_now)
    estimator.ping_sent(ping)
    clock.advance(0.1)
    received = link.agent_rx.verify(ping, now=clock.wall_now + agent_ahead)
    pong = link.agent.build("pong", dp.pong_body(received, received_at=clock.wall_now + agent_ahead), to=NODE,
                            sess=link.sess, now=clock.wall_now + agent_ahead)
    clock.advance(0.1)
    got = link.node_rx.verify(pong)  # the Receiver hands the verified pong to its clock itself...
    assert estimator.offset_s == pytest.approx(agent_ahead, abs=0.01)
    assert estimator.on_pong(got) is None  # ...so feeding it again gives nothing: one ping, one sample
    link.node_rx.verify(link.sync_request(clock.wall_now + agent_ahead + 119))  # raw clock would say future
    err = _refused(lambda: link.node_rx.verify(link.sync_request(clock.wall_now - 75)))
    assert err.reason == "stale"  # raw clock would accept it: 75 s < 120 s


def test_offset_estimator_trusts_only_signed_pongs_to_its_own_pings(link: Link) -> None:
    clock = link.clock
    estimator = dp.ClockEstimator(wall=clock.wall, mono=clock.mono)
    with pytest.raises(TypeError):
        estimator.on_pong({"typ": "pong", "body": {"echo": "a" * 32, "t1": T0}})  # type: ignore[arg-type]
    hint = dp.parse_bridge_hint(dp.build_bridge_hint("welcome", {"server_time": T0 + 3600}, to=NODE))
    with pytest.raises(TypeError):
        estimator.on_pong(hint)  # type: ignore[arg-type]  # the bridge clock is never used
    unasked = link.agent.build("pong", {"echo": "a" * 32, "t1": T0}, to=NODE, sess=link.sess, now=T0)
    assert estimator.on_pong(link.node_rx.verify(unasked)) is None and estimator.offset_s is None
    ping = link.ping(link.pc, clock.wall_now)
    estimator.ping_sent(ping)
    clock.advance(0.2)
    answers = [link.agent.build("pong", {"echo": ping["id"], "t1": clock.wall_now - 0.1}, to=NODE,
                                sess=link.sess, now=clock.wall_now - 0.1) for _ in range(2)]
    assert estimator.on_pong(link.node_rx.verify(answers[0])) == pytest.approx(0.0, abs=0.01)
    assert estimator.on_pong(link.node_rx.verify(answers[1])) is None  # one ping, one sample
    slow = link.ping(link.pc, clock.wall_now)
    estimator.ping_sent(slow)
    clock.advance(dp.PING_MAX_RTT_S + 1)
    late = link.agent.build("pong", {"echo": slow["id"], "t1": clock.wall_now - 3}, to=NODE, sess=link.sess,
                            now=clock.wall_now - 3)
    assert estimator.on_pong(link.node_rx.verify(late)) is None  # RTT above the cap: a relay held it


def test_ntp_offset_rtt_and_clock_skew(link: Link) -> None:
    clock = link.clock
    estimator = dp.ClockEstimator(wall=clock.wall, mono=clock.mono)
    assert estimator.offset_s is None and not estimator.skew_exceeded()
    estimator.check_actuation()  # nothing measured yet: nothing to refuse
    for agent_ahead, skewed in ((70.0, True), (59.0, False)):
        ping = link.ping(link.pc, clock.wall_now)
        estimator.ping_sent(ping)
        t1 = clock.wall_now + 0.05 + agent_ahead  # 50 ms out, 10 ms at the agent, 150 ms back
        pong = link.agent.build("pong", {"echo": ping["id"], "t1": t1}, to=NODE, sess=link.sess, now=t1 + 0.01)
        clock.advance(0.21)
        offset = estimator.on_pong(link.node_rx.verify(pong))
        assert offset == pytest.approx(agent_ahead - 0.05, abs=0.002)  # error <= RTT / 2
        assert estimator.skew_exceeded() is skewed
        if skewed:
            err = _refused(estimator.check_actuation)
            assert (err.code, err.refusal_code) == ("CLOCK_SKEW", "CLOCK_SKEW")
    estimator.check_actuation()
    assert estimator.rtt_ms_p50 == pytest.approx(200.0, abs=2)
    clock.advance(601)
    assert estimator.offset_s is None  # an old sample is not used


def test_an_expired_clock_skew_holds_until_a_new_sample(link: Link) -> None:
    """A sample counts for 600 s, but the node's guard does not take a skew above the 60 s limit back when it
    expires (a relay that drops pongs cannot turn CLOCK_SKEW off): offset_s is None, yet check_actuation refuses
    until a new sample. skew_exceeded(), the agent's fast path, lets it go, so a laptop that went offline with a
    skewed clock reads as offline. An expired sample within the limit leaves nothing to refuse."""
    clock, estimator = link.clock, dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)

    def measure(agent_ahead: float) -> None:
        ping = link.ping(link.pc, clock.wall_now)
        estimator.ping_sent(ping)
        clock.advance(0.1)
        pong = link.agent.build("pong", {"echo": ping["id"], "t1": clock.wall_now + agent_ahead}, to=NODE,
                                sess=link.sess, now=clock.wall_now + agent_ahead)
        assert estimator.on_pong(link.node_rx.verify(pong)) == pytest.approx(agent_ahead, abs=0.1)

    measure(90.0)
    clock.advance(601)
    assert estimator.offset_s is None and not estimator.skew_exceeded()
    assert _refused(estimator.check_actuation).code == "CLOCK_SKEW"
    measure(30.0)  # a new sample within the limit ends it at once
    assert not estimator.skew_exceeded()
    estimator.check_actuation()
    clock.advance(601)
    assert estimator.offset_s is None and not estimator.skew_exceeded()
    estimator.check_actuation()


def test_a_clock_step_beyond_the_window_is_measured_from_signed_pongs(link: Link) -> None:
    """The laptop clock jumps 180 s ahead mid-session. Every other timed message between the node and the VPS now
    fails check 5, but the clock probes still pass: each side measures the step from a signed pong answering its
    own ping and reports CLOCK_SKEW, instead of the link failing without a reason."""
    skew = 180.0

    def node_wall() -> float:
        return link.clock.wall_now + skew

    node_clock = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    agent_clock = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall, link.agent_rx.clock = node_clock, node_wall, agent_clock
    assert _refused(lambda: link.node_rx.verify(link.sync_request(link.clock.wall_now))).reason == "stale"
    state = link.pc.build("notice", {"kind": "desktop_state", "mode": "full", "locked": False, "panic": False},
                          to=dp.BRIDGE_IDENT, sess=link.sess, now=node_wall())
    err = _refused(lambda: link.bridge_rx.verify(state))
    assert (err.reason, err.authentic) == ("future", True)  # the bridge sees the step too (C-116 alerts on it)

    ping = link.agent.build("ping", {}, to=NODE, sess=link.sess, now=link.clock.wall_now)  # the agent measures
    agent_clock.ping_sent(ping)
    link.clock.advance(0.05)
    assert link.bridge_rx.verify(ping).typ == "ping"
    asked = link.node_rx.verify(ping)  # 180 s old on the node clock, but a ping only asks for a pong
    pong = link.pc.build("pong", dp.pong_body(asked, received_at=node_wall()), to=PEER, sess=link.sess,
                         now=node_wall() + 0.01)
    link.clock.advance(0.05)
    assert link.bridge_rx.verify(pong).typ == "pong"  # the bridge relays a pong whatever its ts
    err = _refused(lambda: link.agent_rx.verify(pong))  # 180 s ahead on the agent clock: refused as a message...
    assert (err.reason, err.authentic) == ("future", True)
    assert agent_clock.offset_s == pytest.approx(skew, abs=0.1) and agent_clock.skew_exceeded()  # ...yet measured
    event = link.pc.build("event", {"kind": "location"}, to=PEER, sess=link.sess, session_key=link.node_view.key,
                          now=node_wall())
    assert link.agent_rx.verify(event).typ == "event"  # the agent's window follows the measured offset

    ping = link.pc.build("ping", {}, to=PEER, sess=link.sess, now=node_wall())  # the node measures
    node_clock.ping_sent(ping)
    link.clock.advance(0.05)
    assert link.bridge_rx.verify(ping).typ == "ping"  # 180 s ahead on the VPS clock: still relayed...
    asked = link.agent_rx.verify(ping)  # ...and answered
    pong = link.agent.build("pong", dp.pong_body(asked, received_at=link.clock.wall_now), to=NODE,
                            sess=link.sess, now=link.clock.wall_now + 0.01)
    link.clock.advance(0.05)
    assert link.bridge_rx.verify(pong).typ == "pong"
    err = _refused(lambda: link.node_rx.verify(pong))
    assert (err.reason, err.authentic) == ("stale", True)
    assert node_clock.offset_s == pytest.approx(-skew, abs=0.1) and node_clock.skew_exceeded()
    err = _refused(node_clock.check_actuation)
    assert (err.code, err.refusal_code) == ("CLOCK_SKEW", "CLOCK_SKEW")  # the node actuates nothing, honestly
    assert link.node_rx.verify(link.sync_request(link.clock.wall_now)).typ == "sync_request"


def test_an_out_of_window_pong_counts_only_when_it_answers_our_ping(link: Link) -> None:
    """The echo of one of our own pings, sent to that peer at most 5 s ago, is what proves a pong fresh when its ts
    does not: a forged, unsigned, unasked, misrouted, out-of-session, misshapen, slow or replayed pong never moves
    the offset, and none is ever accepted as a message."""
    skew = 180.0

    def node_wall() -> float:
        return link.clock.wall_now + skew

    estimator = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall = estimator, node_wall

    def asked(to: str = PEER) -> dict[str, Any]:
        ping = link.pc.build("ping", {}, to=to, sess=link.sess, now=node_wall())
        estimator.ping_sent(ping)
        link.clock.advance(0.1)
        return ping

    def answer(ping: dict[str, Any], signer: dp.LinkSigner | None = None, **options: Any) -> dict[str, Any]:
        """A pong on the VPS clock (t1 = its ts), the way the agent answers."""
        fields = {"to": NODE, "sess": link.sess, "now": link.clock.wall_now} | options
        return (signer or link.agent).build("pong", {"echo": ping["id"], "t1": fields["now"]}, **fields)

    ping = asked()
    forged = answer(ping)
    forged["sig"] = ("1" if forged["sig"][0] == "0" else "0") + forged["sig"][1:]
    unsigned = {name: value for name, value in answer(ping).items() if name != "sig"}
    never_sent = link.pc.build("ping", {}, to=PEER, sess=link.sess, now=node_wall())  # not registered
    header = {"v": 1, "typ": "pong", "id": dp.new_message_id(), "ts": link.clock.wall_now, "from": PEER, "to": NODE,
              "sess": link.sess, "room": "", "origin": "internal"}
    padded = _sign_raw(link.agent_key, header, {"echo": ping["id"], "t1": link.clock.wall_now, "note": "x" * 1000})
    for pong, reason in ((forged, "signature"), (unsigned, "format"),
                         (answer(ping, dp.LinkSigner(PEER, dp.generate_signing_key())), "signature"),
                         (answer(ping, sess=dp.new_session_nonce()), "stale"),  # authentic, another session
                         (answer(ping, dp.LinkSigner("agent:job-2", link.agent_key)), "stale"),  # not its peer
                         (padded, "stale"),  # authentic, but not exactly {echo, t1} (check 8's rule)
                         (answer(never_sent), "stale")):  # echoes no ping of ours
        assert _refused(lambda pong=pong: link.node_rx.verify(pong)).reason == reason
        assert estimator.offset_s is None
    without = dp.Receiver(NODE, link.keys, None, sess=link.sess, wall=node_wall)  # no estimator to offer it to
    assert _refused(lambda: without.verify(answer(ping))).reason == "stale" and estimator.offset_s is None
    closed = dp.SeenStore(link.tmp / "closed_clock.db")
    closed.close()
    broken = dp.Receiver(NODE, link.keys, closed, sess=link.sess, clock=estimator, wall=node_wall)
    assert _refused(lambda: broken.verify(answer(ping))).reason == "seen_store"  # unchecked: not offered
    assert estimator.offset_s is None
    good = answer(ping)  # none of the above used up our ping
    err = _refused(lambda: link.node_rx.verify(good))  # used, but still refused as a message
    assert (err.reason, err.authentic) == ("stale", True)
    first = estimator.offset_s
    assert first == pytest.approx(-skew, abs=0.2)
    assert _refused(lambda: link.node_rx.verify(good)).reason == "replay" and estimator.offset_s == first
    late = asked()  # back in the window now: the Receiver hands verified pongs over, under the same rules
    link.clock.advance(dp.PING_MAX_RTT_S)  # a relay held it: the echo no longer proves the pong fresh
    link.node_rx.verify(answer(late))
    assert estimator.offset_s == first
    shared, aimed = asked("agent:*"), asked()
    other_peer = dp.LinkSigner("agent:job-2", link.agent_key)
    link.node_rx.verify(answer(aimed, other_peer))
    assert estimator.offset_s == first  # that ping went to job-1
    pong = answer(shared, other_peer, now=link.clock.wall_now - 1)  # a ping to 'agent:*': any agent peer answers
    got = link.node_rx.verify(pong)
    assert estimator.offset_s == pytest.approx(-skew - 1, abs=0.2) and estimator.on_pong(got) is None


def _probe(link: Link, estimator: dp.ClockEstimator, node_wall: Callable[[], float], *, by_agent: bool,
           receiver: dp.Receiver | None = None) -> dp.VerifiedEnvelope | str:
    """One clock-probe cycle through the bridge, wired only as documented: the pinger (the agent or the node) calls
    ping_sent() and its Receiver (receiver, else the Link's), which has the estimator as its clock, verifies the
    answer. Returns the verified pong, or the reason the pinger refused it."""
    pinger, answerer = (link.agent, link.pc) if by_agent else (link.pc, link.agent)
    pinger_rx, answerer_rx = (link.agent_rx, link.node_rx) if by_agent else (link.node_rx, link.agent_rx)
    pinger_rx = receiver or pinger_rx
    answerer_wall = node_wall if by_agent else link.clock.wall
    ping = link.ping(pinger, link.clock.wall_now if by_agent else node_wall())
    estimator.ping_sent(ping)
    link.clock.advance(0.05)
    assert link.bridge_rx.verify(ping).typ == "ping"
    pong = answerer.build("pong", dp.pong_body(answerer_rx.verify(ping), received_at=answerer_wall()),
                          to=ping["from"], sess=link.sess, now=answerer_wall() + 0.01)
    link.clock.advance(0.05)
    assert link.bridge_rx.verify(pong).typ == "pong"  # the bridge relays a pong whatever its ts
    try:
        return pinger_rx.verify(pong)
    except DesktopLinkError as err:
        return err.reason


def _node_hello(link: Link, now: float) -> dict[str, Any]:
    """A node hello for a new connection (new sess and ephemeral key), signed at `now` on the node clock."""
    sess = dp.new_session_nonce()
    body = dp.node_hello_body(node_id="laptop", eph=dp.new_ephemeral_key(), sess=sess, fields={"mode": "full"})
    return link.pc.build("hello", body, to="agent:*", sess=sess, now=now)


@pytest.mark.parametrize("skew", [5.0, 70.0])
def test_a_receiver_with_a_clock_measures_every_pong_it_verifies(link: Link, skew: float) -> None:
    """The documented wiring is a ClockEstimator on the Receiver plus ping_sent() for every ping, and nothing
    else. A pong inside the window must be measured too: unmeasured, a node clock just 5 s ahead turns every exec
    into START_WINDOW_MISSED (start_by is send + 2.5 s), and 60-120 s of skew never gives CLOCK_SKEW."""
    def node_wall() -> float:
        return link.clock.wall_now + skew

    estimator = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall = estimator, node_wall
    got = _probe(link, estimator, node_wall, by_agent=False)
    assert isinstance(got, dp.VerifiedEnvelope) and got.typ == "pong"  # inside the window: accepted...
    assert estimator.offset_s == pytest.approx(-skew, abs=0.1)  # ...and measured on the way
    assert estimator.on_pong(got) is None  # one ping, one sample: an extra on_pong call is harmless
    start_by = link.node_rx.verify(link.exec_msg()).header["start_by"]  # agent clock + 2.5 s
    unmeasured = dp.start_deadline(start_by, offset=None, wall_now=node_wall(), mono_now=link.clock.mono_now)
    err = _refused(lambda: dp.check_start_window(unmeasured, mono_now=link.clock.mono_now))
    assert err.code == "START_WINDOW_MISSED"  # what the node would answer without the measured offset
    deadline = dp.start_deadline(start_by, offset=estimator.offset_s, wall_now=node_wall(),
                                 mono_now=link.clock.mono_now)
    dp.check_start_window(deadline, mono_now=link.clock.mono_now + 2.4)  # converted: on time
    if skew > dp.CLOCK_SKEW_LIMIT_S:
        assert estimator.skew_exceeded() and _refused(estimator.check_actuation).code == "CLOCK_SKEW"
    else:
        assert not estimator.skew_exceeded()
        estimator.check_actuation()


def test_an_in_window_pong_is_measured_only_after_checks_7_and_8(link: Link) -> None:
    """The Receiver hands an in-window pong to its estimator only once every check passed: a padded pong (check 8)
    and a replayed one (check 7) never move the offset, even when they echo a ping still waiting for its answer."""
    estimator = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    ping = link.ping(link.pc, link.clock.wall_now)
    estimator.ping_sent(ping)
    link.clock.advance(0.1)
    now = link.clock.wall_now + 30  # the agent clock is 30 s ahead: inside the window
    header = {"v": 1, "typ": "pong", "id": dp.new_message_id(), "ts": now, "from": PEER, "to": NODE,
              "sess": link.sess, "room": "", "origin": "internal"}
    padded = _sign_raw(link.agent_key, header, {"echo": ping["id"], "t1": now, "note": "x"})
    seen = link.agent.build("pong", {"echo": ping["id"], "t1": now}, to=NODE, sess=link.sess, now=now)
    assert link.node_rx.verify(seen).typ == "pong"  # recorded before this Receiver had the estimator
    link.node_rx.clock = estimator
    assert _refused(lambda: link.node_rx.verify(padded)).reason == "format"
    assert _refused(lambda: link.node_rx.verify(seen)).reason == "replay"
    assert estimator.offset_s is None  # neither was measured
    answer = link.agent.build("pong", {"echo": ping["id"], "t1": now}, to=NODE, sess=link.sess, now=now)
    link.node_rx.verify(answer)
    assert estimator.offset_s == pytest.approx(30, abs=0.1)  # the ping was still waiting for its answer


def test_a_pong_never_passes_check_5_on_the_raw_clock(link: Link) -> None:
    """Only a hello gets the raw-clock rule. After the laptop clock is set back within a session, a pong that
    fails check 5 with the old offset but passes on the raw clock is still refused as a message; it moves the
    offset only through the out-of-window path, that is, when it answers one of the agent's own pings."""
    skew = [180.0]

    def node_wall() -> float:
        return link.clock.wall_now + skew[0]

    estimator = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.agent_rx.clock, link.node_rx.wall = estimator, node_wall
    assert _probe(link, estimator, node_wall, by_agent=True) == "future"  # refused, yet measured
    assert estimator.offset_s == pytest.approx(180, abs=0.1)
    skew[0] = 0.0  # the laptop clock is set back; the session survives
    unasked = link.pc.build("pong", {"echo": dp.new_message_id(), "t1": node_wall()}, to=PEER, sess=link.sess,
                            now=node_wall())
    err = _refused(lambda: link.agent_rx.verify(unasked))
    assert (err.reason, err.authentic) == ("stale", True)  # although the raw clock would pass it
    assert estimator.offset_s == pytest.approx(180, abs=0.1)  # and it answers no ping of the agent's
    assert _probe(link, estimator, node_wall, by_agent=True) == "stale"  # an answer is refused as a message too...
    assert estimator.offset_s == pytest.approx(0, abs=0.1)  # ...but measured through the out-of-window path
    assert isinstance(_probe(link, estimator, node_wall, by_agent=True), dp.VerifiedEnvelope)  # in the window


def test_after_a_clock_fix_a_reconnect_drops_the_old_offset_and_re_measures(link: Link) -> None:
    """A sample counts for 600 s and a hello is no clock probe. When the laptop clock is set back after a step and
    the node reconnects, its hello and the agent_hello pass on the raw own clock: each side accepts the new
    session, drops its old sample and must re-measure (CLOCK_SKEW until then), so the link is back after the
    first pong of the new session instead of after 600 s."""
    skew = [180.0]

    def node_wall() -> float:
        return link.clock.wall_now + skew[0]

    first_hello = link.envs["hello"]  # signed at T0, before the step
    node_clock = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    agent_clock = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall, link.agent_rx.clock = node_clock, node_wall, agent_clock
    assert _probe(link, agent_clock, node_wall, by_agent=True) == "future"  # refused as messages...
    assert _probe(link, node_clock, node_wall, by_agent=False) == "stale"
    assert agent_clock.offset_s == pytest.approx(180, abs=0.1)  # ...yet measured on both sides
    assert node_clock.offset_s == pytest.approx(-180, abs=0.1)
    err = _refused(lambda: link.bridge_rx.verify(_node_hello(link, node_wall())))  # a reconnect during the step
    assert (err.reason, err.authentic) == ("future", True)  # the bridge's own clock refuses it (C-116 warns)

    link.clock.advance(60)
    skew[0] = 0.0  # the laptop clock is set back; both samples are still valid for 540 s
    in_old_session = link.pc.build("event", {"kind": "location"}, to=PEER, sess=link.sess,
                                   session_key=link.node_view.key, now=node_wall())
    assert _refused(lambda: link.agent_rx.verify(in_old_session)).reason == "stale"  # no raw clock for other types
    assert _refused(lambda: link.agent_rx.verify(_node_hello(link, node_wall() - 200))).reason == "stale"  # nor
    assert _refused(lambda: link.agent_rx.verify(first_hello)).reason == "replay"  # raw clock passes, check 7 not
    assert agent_clock.offset_s == pytest.approx(180, abs=0.1)  # none of these touched the sample

    link.handshake()  # the node reconnects: bridge (own clock), agent and node (raw clock) accept the session
    for estimator in (agent_clock, node_clock):  # both old samples dropped: each must re-measure...
        assert estimator.offset_s is None and estimator.skew_exceeded()
    assert _refused(node_clock.check_actuation).code == "CLOCK_SKEW"  # ...and nothing actuates until then
    for estimator, by_agent in ((agent_clock, True), (node_clock, False)):  # the new session's first probes...
        assert isinstance(_probe(link, estimator, node_wall, by_agent=by_agent), dp.VerifiedEnvelope)
    assert agent_clock.offset_s == pytest.approx(0, abs=0.1) and node_clock.offset_s == pytest.approx(0, abs=0.1)
    node_clock.check_actuation()  # ...so no CLOCK_SKEW any more
    assert link.node_rx.verify(link.exec_msg()).typ == "exec"  # and the link is back, with no restart


def test_a_hello_on_the_raw_clock_must_pass_the_raw_window_itself(link: Link) -> None:
    """The raw-clock rule is K-70's own check, |own clock - ts| <= 120 s, never a window shifted by part of the
    offset. The agent has measured the laptop clock 300 s ahead; then that clock is set back part of the way.
    Verifying such a hello leaves the sample alone: only a handshake with it does not."""
    def node_wall() -> float:
        return link.clock.wall_now + 300

    estimator = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.agent_rx.clock, link.node_rx.wall = estimator, node_wall
    assert _probe(link, estimator, node_wall, by_agent=True) == "future"
    now = link.clock.wall_now
    for ahead, accepted in ((150.0, False), (120.1, False), (119.9, True), (-119.9, True), (-120.1, False)):
        hello = _node_hello(link, now + ahead)  # signed `ahead` s ahead of the agent clock
        if accepted:
            assert link.agent_rx.verify(hello).remeasure is estimator, ahead
        else:
            assert _refused(lambda hello=hello: link.agent_rx.verify(hello)).reason == "stale", ahead
    assert estimator.offset_s == pytest.approx(300, abs=0.1)


def test_an_honest_reconnect_during_a_steady_skew_keeps_both_samples(link: Link) -> None:
    """Hellos that arrive at once pass check 5 with the offset, so a reconnect during a steady 90 s skew keeps both
    samples and CLOCK_SKEW with them. Only a hello that fails with the offset (here one the bridge held 130 s)
    makes the agent re-measure, and CLOCK_SKEW holds until it has."""
    skew = 90.0  # the laptop clock is 90 s ahead, steadily

    def node_wall() -> float:
        return link.clock.wall_now + skew

    node_clock = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    agent_clock = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall, link.agent_rx.clock = node_clock, node_wall, agent_clock
    for estimator, by_agent in ((agent_clock, True), (node_clock, False)):
        assert isinstance(_probe(link, estimator, node_wall, by_agent=by_agent), dp.VerifiedEnvelope)
    link.clock.advance(10)
    link.handshake(node_now=node_wall())  # the node reconnects; the bridge delivers both hellos at once
    assert agent_clock.offset_s == pytest.approx(skew, abs=0.1)
    assert node_clock.offset_s == pytest.approx(-skew, abs=0.1)
    assert agent_clock.skew_exceeded() and _refused(node_clock.check_actuation).code == "CLOCK_SKEW"

    held = _node_hello(link, node_wall())  # the next reconnect: the bridge verifies the hello, then holds it
    sess = dp.new_session_nonce()
    broken = link.pc.build("hello", dp.node_hello_body(node_id="laptop", eph=dp.new_ephemeral_key(), sess=sess)
                           | {"eph_pub": "00" * 32}, to="agent:*", sess=sess, now=node_wall())  # held too
    assert link.bridge_rx.verify(held).typ == "hello"
    link.clock.advance(130)
    failed = link.agent_rx.verify(broken)  # fails with the offset, passes on the raw clock (40 s)...
    assert failed.remeasure is agent_clock
    assert _refused(lambda: dp.accept_node_hello(failed, own_eph=dp.new_ephemeral_key(),
                                                 peer_id="job-1")).reason == "handshake"
    assert agent_clock.offset_s == pytest.approx(skew, abs=0.1)  # ...but its handshake fails: the sample stays
    verified = link.agent_rx.verify(held)
    assert verified.remeasure is agent_clock and agent_clock.offset_s == pytest.approx(skew, abs=0.1)
    dp.accept_node_hello(verified, own_eph=dp.new_ephemeral_key(), peer_id="job-1")
    assert agent_clock.offset_s is None and agent_clock.skew_exceeded()  # C-119 still answers CLOCK_SKEW at once


def _agent_hello(link: Link, signer: dp.LinkSigner, sess: str) -> dict[str, Any]:
    """A new agent peer's agent_hello naming the node session `sess`, signed now on the VPS clock."""
    peer_id = signer.ident.partition(":")[2]
    body = dp.agent_hello_body(peer_id=peer_id, job_id=peer_id, room=ROOM, eph=dp.new_ephemeral_key(), sess=sess)
    return signer.build("agent_hello", body, to=NODE, room=ROOM, now=link.clock.wall_now)


def test_a_hello_held_back_by_the_bridge_never_turns_clock_skew_off(link: Link) -> None:
    """The laptop clock is steadily 90 s behind and the node has measured it: CLOCK_SKEW. A compromised bridge
    holds a genuine agent_hello for 130 s, so it fails check 5 with the offset but passes on the raw clock. Its
    session may start, but then the node must re-measure: CLOCK_SKEW holds until the next pong, so an exec the
    bridge also held is refused, never run, although the start window on the raw clock would let it through.
    A held hello whose handshake fails leaves the sample alone."""
    skew = -90.0

    def node_wall() -> float:
        return link.clock.wall_now + skew

    estimator = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall = estimator, node_wall
    assert isinstance(_probe(link, estimator, node_wall, by_agent=False), dp.VerifiedEnvelope)  # in the window
    assert estimator.offset_s == pytest.approx(90, abs=0.1)
    assert _refused(estimator.check_actuation).code == "CLOCK_SKEW"
    job2 = dp.LinkSigner("agent:job-2", link.agent_key)
    held, elsewhere = _agent_hello(link, job2, link.sess), _agent_hello(link, job2, "e" * 32)
    link.clock.advance(70)
    held_exec = link.exec_msg(call_id="h" * 32)  # job-1's exec; the bridge holds it 60 s
    link.clock.advance(60)

    other = link.node_rx.verify(elsewhere)  # 130 s old: passes check 5 only on the raw clock...
    assert other.remeasure is estimator
    err = _refused(lambda: dp.accept_agent_hello(other, own_eph=dp.new_ephemeral_key(), sess=link.sess,
                                                 node_id="laptop"))
    assert err.reason == "session"  # ...but names another node session: no session starts
    assert estimator.offset_s == pytest.approx(90, abs=0.1)  # so the sample stays
    session = dp.accept_agent_hello(link.node_rx.verify(held), own_eph=dp.new_ephemeral_key(), sess=link.sess,
                                    node_id="laptop")
    assert session.peer_id == "job-2"  # the held hello starts its session...
    assert estimator.offset_s is None and estimator.skew_exceeded()  # ...and the node must re-measure
    got = link.node_rx.verify(held_exec)  # checks 1-8 on the raw clock: the held exec passes them
    deadline = dp.start_deadline(got.header["start_by"], offset=estimator.offset_s, wall_now=node_wall(),
                                 mono_now=link.clock.mono_now)
    dp.check_start_window(deadline, mono_now=link.clock.mono_now)  # nor does the raw-clock start window stop it
    assert _refused(estimator.check_actuation).code == "CLOCK_SKEW"  # CLOCK_SKEW does: it never runs
    assert isinstance(_probe(link, estimator, node_wall, by_agent=False), dp.VerifiedEnvelope)  # the next pong
    assert estimator.offset_s == pytest.approx(90, abs=0.1) and estimator.skew_exceeded()  # the skew, again


def test_after_a_partial_clock_fix_the_new_session_still_gets_clock_skew(link: Link) -> None:
    """No attacker: the laptop clock falls 300 s behind (both sides measure it), is then set only to 100 s
    behind, and the node reconnects. Both hellos pass only on the raw clocks, so both sides must re-measure: the
    first laptop call gets CLOCK_SKEW, never a run, and the next pongs measure the 100 s that remain."""
    skew = [-300.0]

    def node_wall() -> float:
        return link.clock.wall_now + skew[0]

    node_clock = dp.ClockEstimator(wall=node_wall, mono=link.clock.mono)
    agent_clock = dp.ClockEstimator(wall=link.clock.wall, mono=link.clock.mono)
    link.node_rx.clock, link.node_rx.wall, link.agent_rx.clock = node_clock, node_wall, agent_clock
    assert _probe(link, node_clock, node_wall, by_agent=False) == "future"  # both sides measure the step
    assert _probe(link, agent_clock, node_wall, by_agent=True) == "stale"
    link.clock.advance(60)
    skew[0] = -100.0  # set back only part of the way; the node reconnects
    link.handshake(node_now=node_wall())
    for estimator in (agent_clock, node_clock):
        assert estimator.offset_s is None and estimator.skew_exceeded()  # C-119 answers CLOCK_SKEW at once
    assert link.node_rx.verify(link.exec_msg()).typ == "exec"  # an exec sent all the same passes checks 1-8...
    assert _refused(node_clock.check_actuation).code == "CLOCK_SKEW"  # ...and is refused, never run
    for estimator, by_agent in ((agent_clock, True), (node_clock, False)):
        assert isinstance(_probe(link, estimator, node_wall, by_agent=by_agent), dp.VerifiedEnvelope)
    assert agent_clock.offset_s == pytest.approx(-100, abs=0.1)
    assert node_clock.offset_s == pytest.approx(100, abs=0.1)
    assert agent_clock.skew_exceeded() and _refused(node_clock.check_actuation).code == "CLOCK_SKEW"


def test_start_by_conversion_and_window() -> None:
    assert dp.make_start_by(now=T0) == T0 + 2.5
    deadline = dp.start_deadline(T0 + 30 + 2.5, offset=30.0, wall_now=T0, mono_now=500.0)  # agent 30 s ahead
    assert deadline == pytest.approx(502.5)
    dp.check_start_window(deadline, mono_now=502.4)
    err = _refused(lambda: dp.check_start_window(deadline, mono_now=502.6))
    assert (err.reason, err.code, err.refusal_code) == ("start_window", "START_WINDOW_MISSED", "START_WINDOW_MISSED")
    assert dp.start_deadline(T0 + 2.5, offset=None, wall_now=T0 + 3.0, mono_now=500.0) < 500.0  # held 3 s
    assert dp.start_deadline(T0 + 3600, offset=None, wall_now=T0, mono_now=0.0) == dp.START_BY_MAX_AHEAD_S


def test_direction_control_kinds_and_body_consistency(link: Link) -> None:
    err = _refused(lambda: link.pc.build("exec", {"args": {}}, to=PEER, sess=link.sess, call_id=CALL, tool="x",
                                         session_key=link.node_view.key))
    assert (err.reason, err.code) == ("direction", "BAD_ARGS")
    assert _refused(lambda: link.agent.build("result", {}, to=NODE, sess=link.sess, call_id=CALL,
                                             session_key=link.agent_view.key)).reason == "direction"
    assert _refused(lambda: link.exec_msg(to="pc:*")).reason == "direction"  # a sealed body needs one peer
    assert _refused(lambda: link.agent.build("control", {"kind": "desktop_full"}, to=NODE,
                                             sess=link.sess)).reason == "direction"  # loosening never travels
    stop_all = link.agent.build("control", {"kind": "stop_all"}, to=NODE, sess=link.sess, now=T0)
    assert link.node_rx.verify(stop_all).body["kind"] == "stop_all"
    elsewhere = link.agent.build("ping", {}, to="pc:other", sess=link.sess, now=T0)  # signed, but not for us
    assert _refused(lambda: link.node_rx.verify(elsewhere)).reason == "route"
    impostor = dp.LinkSigner("pc:other", link.pc_key).build("ping", {}, to=PEER, sess=link.sess, now=T0)
    assert _refused(lambda: link.agent_rx.verify(impostor)).reason == "route"  # not the bound node
    header = {"v": 1, "typ": "welcome", "id": dp.new_message_id(), "ts": T0, "from": PEER, "to": NODE,
              "sess": link.sess, "room": ROOM, "origin": "llm"}
    err = _refused(lambda: link.node_rx.verify(_sign_raw(link.agent_key, header, {})))
    assert (err.reason, err.authentic, err.refusal_code) == ("direction", True, "DESKTOP_REJECTED")
    bad_sha = link.agent.build("exec", {"args": {"a": 1}, "args_sha256": "0" * 64}, to=NODE, sess=link.sess,
                               session_key=link.agent_view.key, call_id=CALL, tool="x", now=T0)
    mirrored = link.agent.build("exec", {"call_id": "e" * 32}, to=NODE, sess=link.sess,
                                session_key=link.agent_view.key, call_id=CALL, tool="x", now=T0)
    unknown_event = link.pc.build("event", {"kind": "keylog"}, to=PEER, sess=link.sess,
                                  session_key=link.node_view.key, now=T0)
    for env, receiver, key in ((bad_sha, link.node_rx, link.node_view.key), (mirrored, link.node_rx, link.node_view.key),
                               (unknown_event, link.agent_rx, link.agent_view.key)):
        verified = receiver.verify(env)
        assert _refused(lambda verified=verified, key=key: dp.open_body(verified, key)).reason == "format"


def test_size_caps_run_before_parsing_and_signatures(link: Link, monkeypatch: pytest.MonkeyPatch) -> None:
    err = _refused(lambda: link.exec_msg({"text": "x" * (96 * 1024)}))
    assert (err.reason, err.code) == ("size", "BAD_ARGS")
    assert _refused(lambda: link.pc.build("event", {"kind": "log_batch", "lines": ["y" * 1000] * 30}, to=PEER,
                                          sess=link.sess, session_key=link.node_view.key)).reason == "size"
    result = link.pc.build("result", {"status": "success", "result": {"data": "z" * (150 * 1024)}}, to=PEER,
                           sess=link.sess, session_key=link.node_view.key, call_id=CALL, now=T0)
    assert 96 * 1024 < len(dp.canonical_json(result)) <= dp.SIZE_CAPS["result"]
    counting = CountingKey(link.agent_key.public_key())
    receiver = dp.Receiver(NODE, {"agent": counting}, None, sess=link.sess, wall=link.clock.wall)
    parsed: list[bytes] = []
    real_loads = dp.loads_strict
    monkeypatch.setattr(dp, "loads_strict", lambda raw: parsed.append(raw) or real_loads(raw))
    inflated = dict(link.exec_msg(), pad="p" * (96 * 1024))
    assert _refused(lambda: receiver.verify(inflated)).reason == "size"
    too_big = b'{"typ":"event","pad":"' + b"q" * (40 * 1024) + b'"}'
    assert _refused(lambda: receiver.verify(too_big)).reason == "size"  # event cap 32 KB, measured first
    assert _refused(lambda: receiver.verify(b" " * (dp.MAX_ENVELOPE_BYTES + 1))).reason == "size"
    assert counting.calls == 0 and parsed == [too_big]  # only the in-limit message was parsed; nothing verified
    assert receiver.verify(dp.canonical_json(link.exec_msg())).typ == "exec" and counting.calls == 1


def test_aead_failures_never_return_or_reveal_plaintext(link: Link) -> None:
    marker = "PLAINTEXT-MARKER-7c1"
    got = link.node_rx.verify(link.exec_msg({"text": marker}))
    err = _refused(lambda: dp.open_body(got, dp.SessionKey(os.urandom(32), sess=link.sess, peer_id="job-1")))
    shown = "".join(traceback.format_exception(err)) + repr(err) + str(err.args)
    assert err.reason == "decrypt" and err.authentic and marker not in shown and err.__cause__ is None
    assert err.__suppress_context__
    for wrong in (dp.SessionKey(os.urandom(32), sess="e" * 32, peer_id="job-1"),
                  dp.SessionKey(os.urandom(32), sess=link.sess, peer_id="job-2"), None):
        assert _refused(lambda wrong=wrong: dp.open_body(got, wrong)).reason == "session"


def test_sealed_body_is_bound_to_its_header(link: Link) -> None:
    """AAD = the canonical header: a sealed body moved under another (validly signed) header does not open."""
    original = link.exec_msg({"text": "for call c"})
    moved = {name: value for name, value in original.items() if name not in ("body", "sig")} | {"call_id": "d" * 32}
    transplanted = _sign_raw(link.agent_key, moved, original["body"])
    verified = link.node_rx.verify(transplanted)  # the signature is fine: a buggy sender signed it
    assert _refused(lambda: dp.open_body(verified, link.node_view.key)).reason == "decrypt"


def test_blobs_in_chunks_refuse_tampering_reordering_and_truncation(link: Link) -> None:
    data = os.urandom(2 * dp.BLOB_CHUNK_BYTES + 12345)
    blob_id, digest = dp.new_message_id(), hashlib.sha256(data).hexdigest()
    sealed = dp.encrypt_blob(link.node_view.key, blob_id, data)
    assert len(sealed) == dp.encrypted_blob_size(len(data)) == len(data) + 3 * dp.AEAD_OVERHEAD
    assert dp.decrypt_blob(link.agent_view.key, blob_id, sealed, size=len(data), sha256=digest) == data
    empty = dp.encrypt_blob(link.node_view.key, blob_id, b"")
    assert dp.decrypt_blob(link.agent_view.key, blob_id, empty, size=0) == b""
    chunk = dp.BLOB_CHUNK_BYTES + dp.AEAD_OVERHEAD
    flipped = bytearray(sealed)
    flipped[chunk + 100] ^= 1
    cases = [(bytes(flipped), blob_id, None, "decrypt"),
             (sealed[chunk:2 * chunk] + sealed[:chunk] + sealed[2 * chunk:], blob_id, None, "decrypt"),
             (sealed, "f" * 32, None, "decrypt"),  # another blob's id
             (sealed[:-1], blob_id, None, "length"),
             (sealed, blob_id, "0" * 64, "digest")]
    for blob, bid, sha, reason in cases:
        err = _refused(lambda blob=blob, bid=bid, sha=sha: dp.decrypt_blob(link.agent_view.key, bid, blob,
                                                                           size=len(data), sha256=sha))
        assert err.reason == reason


def test_handshake_checks(link: Link) -> None:
    def hello(body_changes: dict[str, Any]) -> dp.VerifiedEnvelope:
        body = dp.node_hello_body(node_id="laptop", eph=dp.new_ephemeral_key(), sess=link.sess) | body_changes
        return link.agent_rx.verify(link.pc.build("hello", body, to="agent:*", sess=link.sess, now=T0))

    eph = dp.new_ephemeral_key()
    assert _refused(lambda: dp.accept_node_hello(hello({"sess": "e" * 32}), own_eph=eph, peer_id="job-1")).reason \
        == "format"
    err = _refused(lambda: dp.accept_node_hello(hello({"proto": [2]}), own_eph=eph, peer_id="job-1"))
    assert (err.code, err.authentic) == ("PROTO_MISMATCH", True)
    assert _refused(lambda: dp.accept_node_hello(hello({"eph_pub": "00" * 32}), own_eph=eph,
                                                 peer_id="job-1")).reason == "handshake"
    stale = link.agent.build("agent_hello", dp.agent_hello_body(peer_id="job-1", job_id="job-1", room=ROOM, eph=eph,
                                                                sess="e" * 32), to=NODE, room=ROOM, now=T0)
    assert _refused(lambda: dp.accept_agent_hello(link.node_rx.verify(stale), own_eph=eph, sess=link.sess,
                                                  node_id="laptop")).reason == "session"
    assert "key" not in repr(link.agent_view) and link.agent_view.sess[:8] in repr(link.agent_view.key)


def test_result_resend_is_fresh_and_deduped_by_call_id(link: Link) -> None:
    record = dp.ResultRecord(call_id=CALL, tool="type_text_tool", room=ROOM, origin="llm", original_ts=T0,
                             finished_at=T0 + 1.5, result={"status": "success", "effect": "done"})
    first = link.pc.build("result", {"call_id": CALL, **record.result}, to=PEER, sess=link.sess, room=ROOM,
                          origin="llm", tool="type_text_tool", session_key=link.node_view.key, call_id=CALL,
                          now=T0 + 1.5)
    link.agent_rx.verify(first)
    agent_seen = link.stores[1]
    assert agent_seen.check_and_add(dp.KIND_CALL_ID, CALL, sess=link.sess)  # the agent settles the call
    old_sess = link.sess
    link.clock.advance(30)
    link.handshake()  # the result_ack was lost and the node reconnected
    resend = dp.build_result_resend(link.pc, record, to=PEER, session_key=link.node_view.key, now=link.clock.wall_now)
    assert resend["id"] != first["id"] and resend["ts"] > first["ts"] and resend["sess"] == link.sess != old_sess
    verified = link.agent_rx.verify(resend)  # a fresh envelope passes the replay check...
    body = dp.open_body(verified, link.agent_view.key)
    assert body == {"call_id": CALL, "original_ts": T0, "finished_at": T0 + 1.5, "result": dict(record.result)}
    assert not agent_seen.check_and_add(dp.KIND_CALL_ID, body["call_id"], sess=link.sess)  # ...the call id dedupes
    assert _refused(lambda: link.agent_rx.verify(first)).reason == "replay"  # old session, and already seen


def test_bridge_hints_are_unsigned_and_tighten_only(link: Link) -> None:
    welcome = dp.build_bridge_hint("welcome", {"server_time": T0, "brain": "vps"}, to="agent:*")
    assert dp.parse_bridge_hint(welcome).body["brain"] == "vps"
    assert dp.parse_bridge_hint(dp.build_bridge_hint("control", {"kind": "desktop_stop"}, to=NODE)).typ == "control"
    assert dp.parse_bridge_hint(dp.build_bridge_hint("dl_undeliverable", {"emitted": False}, to=PEER)).body == {
        "emitted": False}
    refused = [("control", {"kind": "stop_all"}, NODE), ("control", {"kind": "desktop_full"}, NODE),
               ("control", {"kind": "desktop_stop"}, PEER), ("exec", {}, NODE), ("dl_undeliverable", {}, NODE)]
    for typ, body, to in refused:
        assert _refused(lambda typ=typ, body=body, to=to: dp.build_bridge_hint(typ, body, to=to)).reason == "format"
    assert _refused(lambda: dp.parse_bridge_hint(dict(welcome, sig="0" * 128))).reason == "format"
    assert _refused(lambda: link.node_rx.verify(welcome)).reason == "format"  # a hint never verifies


# --- notices: node -> bridge, signed, not encrypted ----------------------------------------------------------

NOTICE_BODIES: dict[str, dict[str, Any]] = {
    "vitals_pc": {"cpu": 21.5, "ram": 63, "disk": 82.0, "disk_label": "C:", "host": "LAPTOP-1", "cores": 8,
                  "ram_total_gb": 15.7},
    "desktop_state": {"mode": "full", "locked": False, "panic": False},
    "power": {"state": "suspend"},
    "alert": {"code": "APPROVAL_INVALID", "level": "critical", "count": 2, "message": "approval ka hash nahi mila",
              "call_id": CALL, "tool": "run_cmd_command", "peer": PEER},
}


def _notice(link: Link, kind: Any, fields: dict[str, Any] | None = None, **options: Any) -> dict[str, Any]:
    body = {"kind": kind, **(NOTICE_BODIES[kind] if fields is None else fields)}
    return link.pc.build("notice", body, **({"to": dp.BRIDGE_IDENT, "sess": link.sess, "now": link.clock.wall_now}
                                           | options))


def _notice_header(link: Link, **changes: Any) -> dict[str, Any]:
    return {"v": 1, "typ": "notice", "id": dp.new_message_id(), "ts": link.clock.wall_now, "from": NODE,
            "to": "bridge", "sess": link.sess, "room": "", "origin": "internal"} | changes


def _changed(kind: str, drop: tuple[str, ...] = (), **changes: Any) -> dict[str, Any]:
    return {name: value for name, value in NOTICE_BODIES[kind].items() if name not in drop} | changes


def test_notices_are_signed_plaintext_only_the_bridge_accepts(link: Link) -> None:
    assert dp.NOTICE_KINDS == set(dp.NOTICE_FIELDS) == set(NOTICE_BODIES)
    for kind, fields in NOTICE_BODIES.items():
        env = _notice(link, kind)
        assert (env["to"], env["room"], env["origin"], env["body"]) == ("bridge", "", "internal", {"kind": kind,
                                                                                                  **fields})
        got = link.bridge_rx.verify(env)
        assert (got.typ, got.sender, got.encrypted, dp.open_body(got, None)) == ("notice", NODE, False,
                                                                                {"kind": kind, **fields})
        for receiver in (link.agent_rx, link.node_rx):  # nothing addressed to the bridge passes anywhere else
            err = _refused(lambda receiver=receiver, env=env: receiver.verify(env))
            assert (err.reason, err.authentic) == ("route", False)
    env = _notice(link, "desktop_state")
    link.bridge_rx.verify(env)
    assert _refused(lambda: link.bridge_rx.verify(env)).reason == "replay"
    forged = copy.deepcopy(env)
    forged["body"]["mode"] = "read"
    assert _refused(lambda: link.bridge_rx.verify(forged)).reason == "signature"
    old = _notice(link, "power", {"state": "resume"})
    link.handshake()  # a new node session, which the bridge binds from the verified hello
    assert _refused(lambda: link.bridge_rx.verify(old)).reason == "session"
    assert link.bridge_rx.verify(_notice(link, "power", {"state": "resume"})).body["state"] == "resume"
    by_agent = _sign_raw(link.agent_key, _notice_header(link, **{"from": PEER}), NOTICE_BODIES["desktop_state"]
                         | {"kind": "desktop_state"})
    err = _refused(lambda: link.bridge_rx.verify(by_agent))
    assert (err.reason, err.authentic) == ("direction", True)  # an agent never speaks for the laptop
    err = _refused(lambda: link.agent.build("notice", {"kind": "power", "state": "resume"}, to="bridge",
                                            sess=link.sess))
    assert (err.reason, err.code) == ("direction", "BAD_ARGS")


BAD_NOTICES: tuple[tuple[Any, dict[str, Any], str], ...] = (
    ("vitals_pc", _changed("vitals_pc", title="Bank - reset password"), "format"),  # an unknown field
    ("desktop_state", _changed("desktop_state", presence_s=3), "format"),  # presence stays on the node
    ("desktop_state", _changed("desktop_state", clipboard="otp 482913"), "format"),
    ("alert", _changed("alert", detail="x" * 30000), "format"),
    ("power", {"state": "suspend", "reason": "lid closed"}, "format"),
    ("vitals_pc", _changed("vitals_pc", drop=("disk",)), "format"),  # a required field missing
    ("desktop_state", _changed("desktop_state", drop=("panic",)), "format"),
    ("power", {}, "format"),
    ("alert", _changed("alert", drop=("level",)), "format"),
    ("alert", {"level": "critical"}, "format"),
    ("vitals_pc", _changed("vitals_pc", cpu=100.5), "format"),
    ("vitals_pc", _changed("vitals_pc", ram=-0.1), "format"),
    ("vitals_pc", _changed("vitals_pc", disk=True), "format"),
    ("vitals_pc", _changed("vitals_pc", cpu="50"), "format"),
    ("vitals_pc", _changed("vitals_pc", cpu=None), "format"),  # never null, optional or not
    ("vitals_pc", _changed("vitals_pc", host=None), "format"),
    ("vitals_pc", _changed("vitals_pc", cores=0), "format"),
    ("vitals_pc", _changed("vitals_pc", cores=4097), "format"),
    ("vitals_pc", _changed("vitals_pc", cores=8.0), "format"),
    ("vitals_pc", _changed("vitals_pc", ram_total_gb=-1), "format"),
    ("vitals_pc", _changed("vitals_pc", ram_total_gb=65536.5), "format"),
    ("vitals_pc", _changed("vitals_pc", host=""), "format"),
    ("vitals_pc", _changed("vitals_pc", host="h" * 65), "format"),
    ("vitals_pc", _changed("vitals_pc", host="LAPTOP\n1"), "format"),
    ("vitals_pc", _changed("vitals_pc", disk_label="C:\u202e"), "format"),  # a bidi override
    ("vitals_pc", _changed("vitals_pc", disk_label="d" * 33), "format"),
    ("desktop_state", _changed("desktop_state", mode="on"), "format"),
    ("desktop_state", _changed("desktop_state", locked=1), "format"),
    ("desktop_state", _changed("desktop_state", panic="no"), "format"),
    ("power", {"state": "sleep"}, "format"),
    ("alert", _changed("alert", code="SIGNATURE_BAD"), "format"),
    ("alert", _changed("alert", level="warning"), "format"),  # APPROVAL_INVALID is critical
    ("alert", {"code": "CLOCK_SKEW", "level": "critical"}, "format"),  # and CLOCK_SKEW a warning
    ("alert", _changed("alert", level="info"), "format"),
    ("alert", _changed("alert", count=0), "format"),
    ("alert", _changed("alert", count=10 ** 6 + 1), "format"),
    ("alert", _changed("alert", count=True), "format"),
    ("alert", _changed("alert", message="m" * 201), "format"),
    ("alert", _changed("alert", message=""), "format"),
    ("alert", _changed("alert", message="two\nlines"), "format"),
    ("alert", _changed("alert", message="zero\u200bwidth"), "format"),
    ("alert", _changed("alert", call_id="short"), "format"),
    ("alert", _changed("alert", tool="run cmd"), "format"),
    ("alert", _changed("alert", peer="bridge"), "format"),
    ("window_context", {"title": "Bank - reset password"}, "direction"),  # sealed kinds never go in plaintext
    ("clipboard_suggestion", {"excerpt": "otp"}, "direction"),
    ("location", {"lat": 31.5, "lng": 74.3}, "direction"),
    ("log_batch", {}, "direction"),
    ("audit", {}, "direction"),
    ("keylog", {}, "direction"),
    (None, {}, "direction"),
    (["vitals_pc"], {}, "direction"),
)


def test_notice_bodies_are_an_exact_allow_list(link: Link) -> None:
    """A kind off the list, or a missing, unknown, mistyped or out-of-range field, is refused when the node builds
    the notice (BAD_ARGS) and when the bridge verifies one signed anyway (check 8, after the signature)."""
    for kind, fields, reason in BAD_NOTICES:
        err = _refused(lambda kind=kind, fields=fields: _notice(link, kind, fields))
        assert (err.reason, err.code) == (reason, "BAD_ARGS"), (kind, fields)
        signed = _sign_raw(link.pc_key, _notice_header(link), {"kind": kind, **fields})
        oversized = len(dp.canonical_json(signed)) > dp.SIZE_CAPS["notice"]  # check 1 refuses it first
        err = _refused(lambda signed=signed: link.bridge_rx.verify(signed))
        assert (err.reason, err.authentic) == (("size", False) if oversized else (reason, True)), (kind, fields)
    no_kind = _sign_raw(link.pc_key, _notice_header(link), {"state": "resume"})
    assert _refused(lambda: link.bridge_rx.verify(no_kind)).reason == "direction"
    good = (("vitals_pc", {"cpu": 0, "ram": 100, "disk": 0.0}),  # the required fields only; int or float
            ("vitals_pc", _changed("vitals_pc", cores=4096, ram_total_gb=65536, host="h" * 64, disk_label="d" * 32)),
            ("vitals_pc", {"cpu": 100.0, "ram": 0.5, "disk": 99.9, "cores": 1, "ram_total_gb": 0}),
            ("desktop_state", {"mode": "off", "locked": True, "panic": True}),
            ("desktop_state", {"mode": "read", "locked": False, "panic": False}),
            ("power", {"state": "resume"}), ("power", {"state": "shutdown"}), ("power", {"state": "logoff"}),
            ("alert", {"code": "SIGNATURE_INVALID", "level": "critical"}),
            ("alert", {"code": "CLOCK_SKEW", "level": "warning", "message": "laptop ki ghari 183 s aage hai"}),
            ("alert", {"code": "MANIFEST_MISMATCH", "level": "warning", "count": 10 ** 6}),
            ("alert", {"code": "DESKTOP_RATE_LIMITED", "level": "warning", "message": "ü" * 200}),
            ("alert", {"code": "APPROVAL_INVALID", "level": "critical", "message": "منظوری غلط", "peer": "pc:x"}))
    for kind, fields in good:
        assert dict(link.bridge_rx.verify(_notice(link, kind, fields)).body) == {"kind": kind, **fields}
    assert {code: dp.ALERT_CODES[code] for code in dp.ALERT_CODES} == {
        "SIGNATURE_INVALID": "critical", "APPROVAL_INVALID": "critical", "CLOCK_SKEW": "warning",
        "MANIFEST_MISMATCH": "warning", "DESKTOP_RATE_LIMITED": "warning"}


def test_only_a_notice_goes_to_the_bridge_and_only_without_room_origin_or_call(link: Link) -> None:
    for options in ({"room": ROOM}, {"origin": "llm"}, {"call_id": CALL}, {"tool": "type_text_tool"},
                    {"seq": {"speech_id": "sp-1", "n": 0}}, {"start_by": T0 + 2}, {"to": "agent:*"}, {"to": PEER},
                    {"to": NODE}, {"sess": None}):
        err = _refused(lambda options=options: _notice(link, "power", {"state": "resume"}, **options))
        assert (err.reason, err.code) == ("format", "BAD_ARGS"), options
    for changes in ({"room": ROOM}, {"origin": "job"}, {"call_id": CALL}, {"to": "agent:*"}, {"typ": "event"},
                    {"typ": "ping"}, {"typ": "result", "call_id": CALL}, {"typ": "hello"}):
        signed = _sign_raw(link.pc_key, _notice_header(link, **changes), {"kind": "power", "state": "resume"})
        err = _refused(lambda signed=signed: link.bridge_rx.verify(signed))
        assert (err.reason, err.authentic) == ("format", False), changes  # check 2, before the signature
    err = _refused(lambda: link.pc.build("event", {"kind": "location"}, to="bridge", sess=link.sess,
                                         session_key=link.node_view.key))
    assert (err.reason, err.code) == ("format", "BAD_ARGS")
    assert _refused(lambda: dp.build_bridge_hint("status", {}, to="bridge")).reason == "format"


def test_a_notice_is_small_by_construction(link: Link) -> None:
    """The largest valid notice fits the 4 KiB notice cap, and a bigger message typed 'notice' is refused before
    it is parsed for a signature check."""
    biggest = {"code": "DESKTOP_RATE_LIMITED", "level": "warning", "count": 10 ** 6, "message": "\U0001f600" * 200,
               "call_id": "c" * 64, "tool": "t" * 64, "peer": "agent:" + "p" * 64}
    env = dp.LinkSigner("pc:" + "n" * 64, link.pc_key).build("notice", {"kind": "alert", **biggest}, to="bridge",
                                                               sess=link.sess, now=T0)
    assert 1024 < len(dp.canonical_json(env)) <= dp.SIZE_CAPS["notice"] == 4 * 1024
    counting = CountingKey(link.pc_key.public_key())
    receiver = dp.Receiver("bridge", {"pc": counting}, None, sess=link.sess, wall=link.clock.wall)
    padded = b'{"typ":"notice","pad":"' + b"q" * (4 * 1024) + b'"}'
    assert _refused(lambda: receiver.verify(padded)).reason == "size" and counting.calls == 0


def test_only_the_sealed_agent_kinds_travel_as_events(link: Link) -> None:
    """Vitals, state, power and alerts go to the bridge as notices (the old 'audit' event is the alert notice); an
    event, sealed to one agent peer, carries only location, window_context, clipboard_suggestion or log_batch."""
    assert dp.EVENT_KINDS == {"location", "window_context", "clipboard_suggestion", "log_batch"}
    for kind in sorted(dp.EVENT_KINDS | dp.NOTICE_KINDS | {"audit"}):
        env = link.pc.build("event", {"kind": kind}, to=PEER, sess=link.sess, session_key=link.node_view.key,
                            now=link.clock.wall_now)
        got = link.agent_rx.verify(env)
        if kind in dp.EVENT_KINDS:
            assert dp.open_body(got, link.agent_view.key)["kind"] == kind
        else:
            assert _refused(lambda got=got: dp.open_body(got, link.agent_view.key)).reason == "format", kind


def test_clock_probe_bodies_are_fixed(link: Link) -> None:
    """A ping skips check 5, so it carries nothing; a pong carries exactly {echo, t1}."""
    for body in ({"note": "x"}, {"echo": "a" * 32}):
        err = _refused(lambda body=body: link.agent.build("ping", body, to=NODE, sess=link.sess))
        assert (err.reason, err.code) == ("format", "BAD_ARGS")
    for body in ({}, {"echo": "a" * 32}, {"echo": "a" * 32, "t1": T0, "x": 1}, {"echo": "A" * 32, "t1": T0},
                 {"echo": "a" * 32, "t1": 0}, {"echo": "a" * 32, "t1": "1"}):
        err = _refused(lambda body=body: link.agent.build("pong", body, to=NODE, sess=link.sess))
        assert (err.reason, err.code) == ("format", "BAD_ARGS"), body
    header = {"v": 1, "typ": "ping", "id": dp.new_message_id(), "ts": T0, "from": PEER, "to": NODE,
              "sess": link.sess, "room": ROOM, "origin": "internal"}
    err = _refused(lambda: link.node_rx.verify(_sign_raw(link.agent_key, header, {"note": "x"})))
    assert (err.reason, err.authentic) == ("format", True)


HOSTILE: tuple[Any, ...] = (
    10 ** 400, -(10 ** 400), 2 ** 64, 1e308, float("nan"), float("inf"), -1.0, 0, 1.5, True, None, "", "x" * 300,
    "\ud800", "\x00", "☃", "agent:*", [], [1], {}, {"a": "\udfff"}, ["\ud800"], {"\ud800": 1},
    {"speech_id": "sp", "n": 10 ** 400}, {"enc": dp.AEAD_ALG, "data": "\ud800"}, {"kind": ["stop_all"]})


def _wire_forms(env: dict[str, Any]) -> list[Any]:
    """The dict a socket.io handler gets, and the JSON a client can send instead: its escapes carry lone
    surrogates, NaN and 400-digit numbers that canonical_json never produces."""
    text = json.dumps(env)
    return [env, text, text.encode("ascii")]


def _only_link_errors(call: Callable[[], Any]) -> None:
    """A result or a DesktopLinkError is fine; any other exception propagates and fails the test."""
    try:
        call()
    except DesktopLinkError:
        pass


def _mutations(raw: bytes, rng: random.Random, count: int) -> Iterator[bytes]:
    """Seeded random byte edits of a valid wire message: flips, deletions and hostile insertions."""
    pieces = (b"\\ud800", b"1e400", b"9" * 400, b"[", b"{", b'"', b",", b"-", b"NaN", b"\xff")
    for _ in range(count):
        mutated = bytearray(raw)
        for _ in range(rng.randint(1, 4)):
            pos, roll = rng.randrange(len(mutated)), rng.random()
            if roll < 0.4:
                mutated[pos] = rng.randrange(256)
            elif roll < 0.6:
                del mutated[pos]
            else:
                mutated[pos:pos] = rng.choice(pieces)
        yield bytes(mutated)


def test_hostile_envelopes_raise_only_desktop_link_errors(link: Link) -> None:
    """Pre-auth robustness: whatever a client sends, verify and parse_bridge_hint raise DesktopLinkError, which
    C-116 and C-121 audit, count per IP and alert on; any other exception would skip all three."""
    receivers = (link.node_rx, link.agent_rx, link.bridge_rx)

    def check(wire: Any) -> None:
        for receiver in receivers:
            _only_link_errors(lambda receiver=receiver: receiver.verify(wire))
        _only_link_errors(lambda: dp.parse_bridge_hint(wire))

    templates = (link.exec_msg(), link.ping(link.agent, T0), _notice(link, "alert"),
                 dp.build_bridge_hint("control", {"kind": "desktop_stop"}, to=NODE))
    rng = random.Random(115)
    for template in templates:
        for name in sorted({*template, "start_by", "seq", "call_id", "extra"}):
            for value in HOSTILE:
                for wire in _wire_forms(template | {name: value}):
                    check(wire)
        for wire in _mutations(dp.canonical_json(template), rng, 150):
            check(wire)
    raw = dp.canonical_json(templates[0])
    for name in ("ts", "start_by"):
        current = f'"{name}":{json.dumps(templates[0][name])}'.encode()
        assert current in raw
        for number in (b"1e999", b"-0", b"9" * 5000, b"1" + b"0" * 400, b"1E+308", b"0.0", b"[]", b'"1"'):
            check(raw.replace(current, f'"{name}":'.encode() + number))
    for value in HOSTILE:  # signed by a buggy agent: the checks after the signature must hold up too
        for typ in ("ping", "control", "sync_request"):
            header = {"v": 1, "typ": typ, "id": dp.new_message_id(), "ts": T0, "from": PEER, "to": NODE,
                      "sess": link.sess, "room": ROOM, "origin": "internal"}
            try:
                signed = _sign_raw(link.agent_key, header, {"kind": value, "x": value})
            except dp.CanonicalJsonError:
                continue  # no signer can even produce it
            for wire in _wire_forms(signed):
                check(wire)
    for value in HOSTILE:  # notices from a buggy node: the field rules after the signature hold up too
        for name in ("kind", "code", "level", "count", "message", "peer", "cpu", "mode", "state"):
            body = {"kind": "alert", "code": "CLOCK_SKEW", "level": "warning", name: value}
            _only_link_errors(lambda body=body: link.pc.build("notice", body, to="bridge", sess=link.sess))
            try:
                signed = _sign_raw(link.pc_key, _notice_header(link), body)
            except dp.CanonicalJsonError:
                continue
            for wire in _wire_forms(signed):
                _only_link_errors(lambda wire=wire: link.bridge_rx.verify(wire))


# --- signed HTTP --------------------------------------------------------------------------------------------

def test_http_signature_vector_and_tampering() -> None:
    key = dp.signing_key_from_hex(RFC8032_SEED)
    public, query = key.public_key(), {"area": "shared", "path": "QR_Codes/a b.png"}
    headers = dp.sign_http_request(key, method="GET", path="/vortex/media/file", query=query, now=T0)
    signed = (b"jarvis/desktop/http/v1|GET|/media/file|area=shared&path=QR_Codes%2Fa%20b.png|1759400000|"
              + headers["X-Desk-Nonce"].encode() + b"|0|" + hashlib.sha256(b"").hexdigest().encode())
    public.verify(bytes.fromhex(headers["X-Desk-Sig"]), signed)  # pins method|path|query|ts|nonce|len|sha256
    base = {"method": "get", "path": "/media/file", "query": "path=QR_Codes%2Fa+b.png&area=shared",
            "public_key": public, "seen": None, "now": T0}
    assert dp.verify_http_headers(headers, **base).length == 0  # nginx strips /vortex; query order is canonical
    for change in ({"method": "POST"}, {"path": "/media/index"}, {"query": "area=outputs&path=x"}):
        assert _refused(lambda change=change: dp.verify_http_headers(headers, **(base | change))).reason == \
            "signature"
    many_fields = "&".join(f"k{i}=v" for i in range(101))
    assert _refused(lambda: dp.verify_http_headers(headers, **(base | {"query": many_fields}))).reason == "format"
    for name, value in (("X-Desk-Ts", str(int(T0) + 1)), ("X-Desk-Nonce", "0" * 32), ("X-Desk-Len", "1"),
                        ("X-Desk-Sha256", "0" * 64)):
        tampered = headers | {name: value}
        assert _refused(lambda tampered=tampered: dp.verify_http_headers(tampered, **base)).reason == "signature"


class BodySource:
    """A request body read in chunks; counts reads to show the headers were checked before any byte."""

    def __init__(self, data: bytes) -> None:
        self.data, self.reads = data, 0

    def chunks(self) -> Iterator[bytes]:
        for start in range(0, len(self.data), 1000):
            self.reads += 1
            yield self.data[start:start + 1000]


def test_http_headers_are_verified_before_the_body(link: Link) -> None:
    public = CountingKey(link.pc_key.public_key())
    store = link.stores[2]
    data = os.urandom(4096)

    def handle(headers: Any, source: BodySource, **options: Any) -> dp.HttpAuth:
        auth = dp.verify_http_headers(headers, method="POST", path="/desktop/blob", query=None, public_key=public,
                                      seen=store, now=T0, **options)
        verifier = dp.HttpBodyVerifier(auth)
        for chunk in source.chunks():
            verifier.update(chunk)
        verifier.finish()
        return auth

    def signed(body: bytes = data, now: float = T0) -> dict[str, str]:
        return dp.sign_http_request(link.pc_key, method="POST", path="/vortex/desktop/blob", body=body, now=now)

    good = signed()
    forged = good | {"X-Desk-Sig": ("1" if good["X-Desk-Sig"][0] == "0" else "0") + good["X-Desk-Sig"][1:]}
    dup = [*good.items(), ("x-desk-sig", good["X-Desk-Sig"])]
    refusals = [(good, {"max_length": 1024}, "size"), (good, {"content_length": 10}, "length"),
                (forged, {}, "signature"), (signed(now=T0 - 121), {}, "stale"), (dup, {}, "format")]
    for headers, options, reason in refusals:
        source = BodySource(data)
        err = _refused(lambda headers=headers, source=source, options=options: handle(headers, source, **options))
        assert err.reason == reason and source.reads == 0
    assert public.calls == 2  # size, length and format refusals never reached the signature check
    source = BodySource(data)
    raw_asgi = [(name.lower().encode(), value.encode()) for name, value in good.items()]
    assert handle(raw_asgi, source).length == 4096 and source.reads == 5
    replay = BodySource(data)
    assert _refused(lambda: handle(good, replay)).reason == "replay" and replay.reads == 0
    longer, changed = BodySource(data + os.urandom(6000)), BodySource(bytes([data[0] ^ 1]) + data[1:])
    assert _refused(lambda: handle(signed(), longer)).reason == "length" and longer.reads == 5  # stops at the cap
    short = BodySource(data[:-1])
    assert _refused(lambda: handle(signed(), short)).reason == "length"
    assert _refused(lambda: handle(signed(), changed)).reason == "digest"
    closed = dp.SeenStore(link.tmp / "closed_http.db")
    closed.close()
    err = _refused(lambda: dp.verify_http_headers(signed(), method="POST", path="/desktop/blob", query=None,
                                                  public_key=public, seen=closed, now=T0))
    assert err.reason == "seen_store"  # fails closed
    assert [dp.HTTP_STATUS[r] for r in ("size", "signature", "stale", "replay", "seen_store")] == [
        413, 403, 401, 409, 503]


def test_hostile_http_requests_raise_only_desktop_link_errors() -> None:
    """X-Desk-* values, paths, queries and methods from a client raise DesktopLinkError (an HTTP status), and
    the raw ASGI forms (bytes raw_path and query_string) verify like the str forms."""
    key = dp.generate_signing_key()
    public = key.public_key()
    good = dp.sign_http_request(key, method="POST", path="/vortex/desktop/blob", query={"kind": "shot"}, body=b"x",
                                now=T0)

    def check(headers: Any = None, method: Any = "POST", path: Any = "/desktop/blob", query: Any = "kind=shot") -> None:
        _only_link_errors(lambda: dp.verify_http_headers(good if headers is None else headers, method=method,
                                                          path=path, query=query, public_key=public, seen=None,
                                                          now=T0))

    for name in dp.HTTP_HEADER_NAMES:
        for value in ("", "1" * 13, "-1", "１２", "0x10", " 1", "1e3", "\ud800", b"\xff\xfe", b"12", "\x00",
                      "a" * 5000, None, 10 ** 400):
            check(headers=good | {name: value})
    for path in (b"/desktop/\xff", bytearray(b"/desktop/blob"), "/desktop/\ud800", "desktop/blob", "", "%", "%zz",
                 "/%ff%fe", None, 7, "/vortex", "//", "/a?b", b"/a?\xff"):
        check(path=path)
    for query in (b"a=\xff", memoryview(b"a=1"), "a=\ud800", "\ud800=1", "a=%ff", "&".join(["k=v"] * 101),
                  [("a", None)], [("a",)], [1, 2], {"a": 1.5}, {"a": b"x"}, {1: "x"}, {"\ud800": "x"}, {"a": True},
                  5, 1.5, [("k", "v")] * 101, iter([("a", "1")] * 1000)):
        check(query=query)
    for method in (b"POST", b"\xffPOST", "PO\ud800ST", "", "P" * 17, "GET POST", None, "ﬀ", "P0ST"):
        check(method=method)
    raw_asgi = [(name.lower().encode(), value.encode()) for name, value in good.items()]
    assert dp.verify_http_headers(raw_asgi, method=b"POST", path=b"/desktop/blob", query=b"kind=shot",
                                  public_key=public, seen=None, now=T0).length == 1
    assert _refused(lambda: dp.canonical_http_query(b"a=1&b=\xff")).reason == "format"
    assert _refused(lambda: dp.canonical_http_query([("k", "v")] * 101)).reason == "format"  # as for a str query
    assert dp.canonical_http_query(b"b=2&a=%41") == dp.canonical_http_query({"a": "A", "b": 2}) == "a=A&b=2"


# --- SeenStore ----------------------------------------------------------------------------------------------

def test_seen_store_sessions_retention_and_persistence(tmp_path: Path) -> None:
    clock, path, sess_a, sess_b = Clock(), tmp_path / "seen.db", "a" * 32, "b" * 32
    with dp.SeenStore(path, namespace="pc:laptop", wall=clock.wall, mono=clock.mono) as store:
        assert store.created_at == T0 and store.wall_step_s == 0.0
        clock.wall_now += 30  # the wall clock steps forward (a time sync), the monotonic one does not
        assert store.wall_step_s == 30.0
        clock.wall_now -= 30
        assert store.check_and_add("id", "x1", sess=sess_a)
        assert not store.check_and_add("id", "x1", sess=sess_a)  # same session
        assert not store.check_and_add("id", "x1", sess=sess_b)  # another session: still a replay...
        assert store.seen("id", "x1", sess=sess_b) and not store.seen("id", "x1", sess="c" * 32)  # ...bound to b
        assert store.check_and_add("call_id", "x1") and store.check_and_add("confirm_id", "x1")
        clock.advance(dp.SEEN_RETENTION_S - 10)
        assert store.seen("id", "x1") and store.purge() == 0  # still remembered just before 24 h
        clock.advance(20)  # 24 h after the first sighting
        assert store.purge() == 4 and not store.seen("id", "x1")
        assert store.check_and_add("id", "x1", sess=sess_a)
        for kind, value, sess in (("ID", "x", ""), ("id", "", ""), ("id", "x", "short")):
            with pytest.raises(ValueError):
                store.check_and_add(kind, value, sess=sess)
    with pytest.raises(RuntimeError):
        store.check_and_add("id", "x2")
    store.close()  # closing twice is fine
    with dp.SeenStore(path, namespace="pc:laptop", wall=clock.wall) as again:
        assert not again.check_and_add("id", "x1")  # persisted across a restart
        assert again.created_at == T0 < clock.wall_now  # written once, kept across opens (and purges)
    with dp.SeenStore(path, namespace="agent:job-1", wall=clock.wall) as other:
        assert other.check_and_add("id", "x1")  # namespaces do not mix
        assert other.created_at == clock.wall_now  # nor do their histories: a new namespace starts now


def test_seen_store_is_thread_safe_with_one_owner_thread(tmp_path: Path) -> None:
    store = dp.SeenStore(tmp_path / "threads.db")
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        mine = [store.check_and_add("id", f"v{i}") for i in range(50)]
        with lock:
            results.extend(mine)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 400 and results.count(True) == 50  # each value is new exactly once

    async def twice() -> list[bool]:
        return list(await asyncio.gather(store.acheck_and_add("id", "z"), store.acheck_and_add("id", "z")))

    assert sorted(asyncio.run(twice())) == [False, True]
    owner = store._thread
    assert owner.name == "desktop-seen-store" and owner.is_alive()  # sqlite3 refuses use from other threads
    store.close()
    assert not owner.is_alive()


# --- keys, DPAPI and gen_desktop_keys.py --------------------------------------------------------------------

def _private_forms(key: Ed25519PrivateKey) -> list[str]:
    """Every encoding of a private key that could leak: hex, base64 (std/url), PKCS8 DER and PEM, raw."""
    seed = key.private_bytes_raw()
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    forms = [seed.hex(), seed.hex().upper(), seed.hex()[:16], base64.b64encode(seed).decode(),
             base64.urlsafe_b64encode(seed).decode(), base64.b64encode(der).decode(), seed.decode("latin-1")]
    return forms + [form.rstrip("=") for form in forms]


def _assert_no_private(output: str | bytes, key: Ed25519PrivateKey) -> None:
    text = output.decode("latin-1") if isinstance(output, bytes) else output
    for form in _private_forms(key):
        assert form not in text
    assert "PRIVATE KEY" not in text


def test_key_helpers_do_not_echo_values(monkeypatch: pytest.MonkeyPatch) -> None:
    key = dp.generate_signing_key()
    assert dp.signing_key_to_hex(dp.signing_key_from_hex(f" {dp.signing_key_to_hex(key).upper()}\n")) == \
        dp.signing_key_to_hex(key)
    assert dp.public_key_hex(dp.public_key_from_hex(dp.public_key_hex(key))) == dp.public_key_hex(key)
    almost = "zz" + dp.signing_key_to_hex(key)[2:]
    with pytest.raises(ValueError) as err:
        dp.signing_key_from_hex(almost)
    assert almost not in str(err.value) and almost[2:] not in str(err.value)
    _assert_no_private(repr(dp.LinkSigner(PEER, key)), key)
    monkeypatch.setattr(dp, "_on_windows", lambda: False)
    with pytest.raises(OSError, match="Windows only"):
        dp.dpapi_protect(b"x")


# The eight small-order edwards25519 points (each y with both sign bits) and non-canonical y >= p encodings.
WEAK_PUBLIC_KEYS = (
    "01" + "00" * 31, "01" + "00" * 30 + "80",  # the identity (order 1)
    "ec" + "ff" * 30 + "7f", "ec" + "ff" * 31,  # order 2
    "00" * 32, "00" * 31 + "80",  # order 4
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",  # order 8
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
    "ed" + "ff" * 30 + "7f", "ee" + "ff" * 30 + "7f", "ff" * 31 + "7f")  # y = p, p + 1, p + 18


@pytest.mark.parametrize("weak", WEAK_PUBLIC_KEYS)
def test_weak_public_keys_are_never_pinned(weak: str) -> None:
    """OpenSSL loads these points, and with a small-order key a fixed forged signature (R a small-order point,
    S = 0) verifies for most or all messages: no loader, Receiver or HTTP check may pin one."""
    with pytest.raises(ValueError, match="usable"):
        dp.public_key_from_hex(weak.upper())
    try:
        careless = Ed25519PublicKey.from_public_bytes(bytes.fromhex(weak))  # a loader that skips the check
    except ValueError:
        return  # this OpenSSL refuses the point itself
    pc_key = dp.generate_signing_key()
    with pytest.raises(ValueError, match="pinned"):
        dp.Receiver(NODE, {"agent": careless, "pc": pc_key.public_key()}, None)
    headers = dp.sign_http_request(pc_key, method="GET", path="/media/index", now=T0)
    with pytest.raises(ValueError, match="pinned"):
        dp.verify_http_headers(headers, method="GET", path="/media/index", query=None, public_key=careless,
                               seen=None, now=T0)


def test_real_public_keys_load() -> None:
    assert dp.public_key_hex(dp.public_key_from_hex(RFC8032_PUBLIC)) == RFC8032_PUBLIC
    for _ in range(64):
        public = dp.public_key_hex(dp.generate_signing_key())
        assert dp.public_key_hex(dp.public_key_from_hex(public)) == public


@pytest.mark.skipif(not WINDOWS, reason="DPAPI exists on Windows only")
def test_dpapi_key_file_roundtrip(tmp_path: Path) -> None:
    key = dp.generate_signing_key()
    path = tmp_path / "keys.dpapi"
    path.write_bytes(dp.dpapi_protect(dp.pc_key_document(key, created=1)))
    raw = path.read_bytes()
    _assert_no_private(raw, key)
    assert b"seed" not in raw and key.private_bytes_raw() not in raw
    assert dp.public_key_hex(dp.load_pc_key_file(path)) == dp.public_key_hex(key)
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(OSError):
        dp.load_pc_key_file(path)
    with pytest.raises(ValueError):
        dp.parse_pc_key_document(b'{"kind":"other"}')


def _load_gen_keys() -> Any:
    spec = importlib.util.spec_from_file_location("gen_desktop_keys", GEN_KEYS_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen_keys() -> Any:
    return _load_gen_keys()


def _env_key(gen_keys: Any, path: Path) -> Ed25519PrivateKey:
    return dp.signing_key_from_hex(gen_keys.env_value(path.read_bytes().decode("utf-8"), "DESKTOP_AGENT_SIGNING_KEY"))


def test_gen_keys_agent_role(gen_keys: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str],
                             monkeypatch: pytest.MonkeyPatch) -> None:
    masks: list[int] = []
    real_umask = os.umask
    monkeypatch.setattr(os, "umask", lambda mask: masks.append(mask) or real_umask(mask))
    env, tmp = tmp_path / ".env.agent", tmp_path / ".env.agent.tmp"
    original = b"FOO=bar\r\nDESKTOP_AGENT_SIGNING_KEY=\r\n"
    env.write_bytes(original)
    args = ["--role", "agent", "--env-file", str(env)]
    assert gen_keys.main(args) == 0
    out = capsys.readouterr()
    key = _env_key(gen_keys, tmp)
    text = tmp.read_bytes().decode("utf-8")
    assert text.startswith("FOO=bar\r\nDESKTOP_AGENT_SIGNING_KEY=") and text.endswith("\r\n")  # bytes kept
    assert f"DESKTOP_AGENT_PUBLIC_KEY={dp.public_key_hex(key)}" in text
    assert f"DESKTOP_AGENT_PUBLIC_KEY={dp.public_key_hex(key)}" in out.out and f"WROTE={tmp}" in out.out
    _assert_no_private(out.out + out.err, key)
    assert env.read_bytes() == original and not list(tmp_path.glob("*.part"))  # the env file itself: untouched
    assert masks[0] == 0o077 and len(masks) == 2  # umask 077 while writing, then restored
    if not WINDOWS:
        assert tmp.stat().st_mode & 0o777 == 0o600
    assert gen_keys.main(args) == 2 and "already exists" in capsys.readouterr().err  # a pending .tmp is kept
    os.replace(tmp, env)  # the deploy step installs it; a re-run keeps the key and writes nothing
    assert gen_keys.main(args) == 0
    out = capsys.readouterr()
    assert f"KEPT={env}" in out.out and dp.public_key_hex(key) in out.out and not tmp.exists()
    _assert_no_private(out.out + out.err, key)
    pc_public = dp.public_key_hex(dp.generate_signing_key())
    assert gen_keys.main([*args, "--pc-public", pc_public.upper()]) == 0
    assert f"DESKTOP_PC_PUBLIC_KEY={pc_public}" in tmp.read_text(encoding="utf-8")
    assert dp.signing_key_to_hex(_env_key(gen_keys, tmp)) == dp.signing_key_to_hex(key)
    tmp.unlink()
    capsys.readouterr()
    assert gen_keys.main([*args, "--pc-public", "zz"]) == 2 and "--pc-public" in capsys.readouterr().err
    assert gen_keys.main([*args, "--pc-public", WEAK_PUBLIC_KEYS[0]]) == 2  # the identity: forgeable
    assert "--pc-public" in capsys.readouterr().err and not tmp.exists()
    assert gen_keys.main([*args, "--rotate"]) == 0
    out, rotated = capsys.readouterr(), _env_key(gen_keys, tmp)
    assert dp.signing_key_to_hex(rotated) != dp.signing_key_to_hex(key)
    _assert_no_private(out.out + out.err, rotated)
    _assert_no_private(out.out + out.err, key)


def test_gen_keys_agent_role_writes_only_next_to_the_env_file(gen_keys: Any, tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str],
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """C-127: PATH.tmp must land next to PATH on the host. A missing directory (nothing mounted there), one that is
    not writable, or an env file on another filesystem than its directory (a file bind-mounted on its own) is
    refused before any key is made or printed, and a public key is printed only once its file is written."""
    missing = tmp_path / "not-mounted" / ".env.agent"
    assert gen_keys.main(["--role", "agent", "--env-file", str(missing)]) == 2
    out = capsys.readouterr()
    assert "does not exist" in out.err and out.out == "" and not missing.parent.exists()
    env = tmp_path / ".env.agent"
    env.write_text("FOO=bar\n", encoding="utf-8")
    args = ["--role", "agent", "--env-file", str(env)]
    monkeypatch.setattr(gen_keys, "_writable", lambda folder: False)
    assert gen_keys.main(args) == 2
    out = capsys.readouterr()
    assert "not writable" in out.err and out.out == ""
    monkeypatch.setattr(gen_keys, "_writable", lambda folder: True)
    real_device, real_write = gen_keys._device, gen_keys.atomic_write
    monkeypatch.setattr(gen_keys, "_device", lambda path: real_device(path) + (Path(path) == env))
    assert gen_keys.main(args) == 2
    out = capsys.readouterr()
    assert "mounted on its own" in out.err and out.out == ""
    monkeypatch.setattr(gen_keys, "_device", real_device)

    def full_disk(path: Path, data: bytes) -> None:
        raise OSError("no space left on the device")

    monkeypatch.setattr(gen_keys, "atomic_write", full_disk)
    assert gen_keys.main(args) == 2
    out = capsys.readouterr()
    assert "no space" in out.err and out.out == ""  # no public key for a key that was not saved
    assert [path.name for path in tmp_path.iterdir()] == [".env.agent"]  # nothing else was written
    monkeypatch.setattr(gen_keys, "atomic_write", real_write)
    monkeypatch.chdir(tmp_path)
    assert gen_keys.main(["--role", "agent", "--env-file", ".env.agent"]) == 0
    printed = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert Path(printed["WROTE"]).is_absolute() and Path(printed["WROTE"]) == tmp_path / ".env.agent.tmp"
    assert (tmp_path / ".env.agent.tmp").exists() and env.read_text(encoding="utf-8") == "FOO=bar\n"


@pytest.mark.skipif(not WINDOWS, reason="DPAPI exists on Windows only")
def test_gen_keys_pc_role_wraps_with_dpapi(gen_keys: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "node" / "keys.dpapi"
    args = ["--role", "pc", "--key-file", str(path)]
    assert gen_keys.main(args) == 0
    out = capsys.readouterr()
    key = dp.load_pc_key_file(path)
    assert f"DESKTOP_PC_PUBLIC_KEY={dp.public_key_hex(key)}" in out.out and f"WROTE={path}" in out.out
    _assert_no_private(out.out + out.err, key)
    _assert_no_private(path.read_bytes(), key)
    assert gen_keys.main(args) == 0 and f"KEPT={path}" in capsys.readouterr().out
    assert dp.public_key_hex(dp.load_pc_key_file(path)) == dp.public_key_hex(key)
    assert gen_keys.main([*args, "--rotate"]) == 0
    out, rotated = capsys.readouterr(), dp.load_pc_key_file(path)
    backups = list(path.parent.glob("keys.dpapi.*.bak"))
    assert "BACKUP=" in out.out and len(backups) == 1
    assert dp.public_key_hex(dp.load_pc_key_file(backups[0])) == dp.public_key_hex(key) != dp.public_key_hex(rotated)
    _assert_no_private(out.out + out.err, rotated)


def test_gen_keys_pc_role_needs_windows(gen_keys: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen_keys, "is_windows", lambda: False)
    path = tmp_path / "keys.dpapi"
    assert gen_keys.main(["--role", "pc", "--key-file", str(path)]) == 2
    assert "needs Windows DPAPI" in capsys.readouterr().err and not path.exists()


def test_gen_keys_cli_prints_only_public_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env.agent"
    proc = subprocess.run([sys.executable, str(GEN_KEYS_PY), "--role", "agent", "--env-file", str(env)],
                          capture_output=True, cwd=tmp_path, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    key = _env_key(_load_gen_keys(), tmp_path / ".env.agent.tmp")
    _assert_no_private(proc.stdout + proc.stderr, key)
    assert key.private_bytes_raw() not in proc.stdout + proc.stderr
    assert f"DESKTOP_AGENT_PUBLIC_KEY={dp.public_key_hex(key)}".encode() in proc.stdout


def test_import_has_no_side_effects(tmp_path: Path) -> None:
    code = ("import sys, threading; import services.utils.desktop_protocol; "
            "print(threading.active_count(), 'win32crypt' in sys.modules)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, timeout=120,
                          check=False, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert proc.stdout.split() == ["1", "False"], proc.stderr
    assert not list(tmp_path.iterdir())  # no files or databases appear
