"""
# src/core/bridge_desktop.py
Desktop Link relay of the UI bridge (plan task C-116; contracts K-70 v5 DESKTOP-LINK and K-74 v5 DESKTOP-STATUS,
built on the C-115 library services/utils/desktop_protocol.py).

The bridge relays signed K-70 envelopes between the laptop's Desktop Node and the VPS agent peers on the Socket.IO
namespace '/desktop'. It checks every message with the library's Receiver but holds no private link key: it can
neither forge a message nor read a sealed body, and it relays the exact text it received. Nothing from namespace '/'
reaches '/desktop', and node notices are consumed here, never relayed.

Listeners (K-48 v4): the local port of a connection (ASGI scope['server']) decides what it may do.
- The public listener (5001; nginx /vortex/) takes only the node, role pc.
- The internal listener INTERNAL_PORT (5002; jarvis-network only, never published) takes only agent peers.

Wire format on '/desktop':
- connect auth {"hello": <the node's hello, or an agent peer's agent_hello, signed, as JSON text>}. A socket exists
  only once its hello verified, so every connect needs a fresh hello: a replayed one is refused.
- event 'msg', both ways: one signed envelope as JSON text. The bridge relays the text it received, unchanged.
- event 'hint', bridge -> peer: one unsigned bridge hint as JSON text (welcome, status, control, dl_undeliverable).
  A hint is never authority (K-70).

Routing:
- node -> 'agent:<id>' goes to that agent peer's socket, and 'agent:*' to every agent peer;
- agent -> 'pc:*' or 'pc:<node_id>' goes to the node;
- a node notice (to 'bridge') is consumed.
A node hello opens or rotates the node session: the bridge relays it and rebinds every agent peer's Receiver to the
new sess. An agent peer that connects later gets a node hello only when the node sends one to it: the bridge never
replays a signed message. Each exec's call_id is mapped to its agent socket; when the node drops, every pending call
gets dl_undeliverable {call_id, emitted, acked}, and an exec that cannot be delivered gets emitted false.

Presence and desktop_status (K-74 v5):
- every verified node message except ping and pong counts; stale after 25 s; offline after a power notice
  suspend, shutdown or logoff until a later verified node message or the next hello;
- desktop_status goes to the UI sockets on '/' (event 'desktop_status' and init_state) and to the agent peers as
  'status' hints, on every change;
- the latest vitals_pc, desktop_state and power values are kept in the C-13 kv, overwritten in place;
- an alert notice becomes a system_alert and an audit row (the only notice that writes one).

Persisted controls (C-13 kv, re-applied at boot): desktop_link on/off (the admin CLI below), a UI desktop_stop until
the node reports mode read or off, active_job and last_activity_ts. They fail closed: the link stays off until they
could be read, a switch lost with a corrupt bridge.db counts as off, and an off switch or a stop that the kv lost is
written again from memory.

Alerts (K-74): critical for a failed signature or hello verification; warning for desktop_replaced, a new node_id,
rate-limit trips and clock skew (an authentic node hello or notice refused only as stale or future). Each is a
system_alert to the UI sockets, an audit row and an alert hint to the node; repeats fold per source and code. Failed
hello verifications count per client IP (X-Real-IP only when the TCP peer is the Docker gateway), clock skew never
counts, and every hello is verified before the limit applies: one that verifies always passes, so the node is never
throttled, even from an address a forger used. DL_REJECT and node alert rows are capped whatever the addresses.

Logs carry call ids, tools, sizes and reasons, never bodies, arguments or notice values.

Admin CLI (inside the bridge container; the running bridge applies a change within 5 s):
    python -m src.core.bridge_desktop link off|on|status [--db PATH]
"""
# pylint: disable=too-many-lines  # one relay: handshake, routing, presence, notices, alerts, controls and the CLI

from __future__ import annotations

import argparse
import asyncio
import functools
import ipaddress
import logging
import math
import os
import statistics
import sys
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from socketio.exceptions import ConnectionRefusedError as SocketRefused

from services.utils import desktop_protocol as dp
from services.utils.desktop_protocol import DesktopLinkError, Receiver, SeenStore, VerifiedEnvelope
from services.utils.jarvis_config import ROLE_BRIDGE, active_brain, runtime_role
from src.core.bridge_security import TokenBucket
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
    bridge_db_path,
)

# The bridge's own logger name (see bridge_store): its records never travel back through /notify.
logger = logging.getLogger("UI-BRIDGE")

NAMESPACE = "/desktop"
EVENT_MSG, EVENT_HINT = "msg", "hint"
AUTH_HELLO = "hello"
INTERNAL_PORT = 5002  # K-48 v4: jarvis-network only, never published (read at call time)
ROLE_PC, ROLE_AGENT = "pc", "agent"
SEEN_FILE, SEEN_NAMESPACE = "desktop_seen.db", "bridge"  # K-70 check 7: never bridge.db
ENV_AGENT_KEY, ENV_PC_KEY = "DESKTOP_AGENT_PUBLIC_KEY", "DESKTOP_PC_PUBLIC_KEY"
LINK_ON, LINK_OFF = "on", "off"

STALE_AFTER_S = 25.0      # K-74: stale after 25 s without a counted node message
VOICE_ACTIVE_S = 60.0     # voice_active: speech or a voice turn within the last minute
VITALS_FRESH_S = 30.0     # K-74: the legacy vitals_update prefers a vitals_pc reading younger than this
BUSY_WINDOW_S = 15.0      # busy_tool: an exec with relay or node activity (ack, progress every 10 s) this recent
CALL_TTL_S = 3600.0       # K-70: an agent never sends an exec for a call_id more than 1 h after its first send
CALL_ACTIVE_S = 45.0      # K-70: a running call sends progress every 10 s, and its agent cancels one quiet for 30 s
FOLD_S = 60.0             # K-70/K-74: repeats within 60 s fold into one audit row or one alert, with a count
ALERT_REEMIT_S = 5.0      # a folded alert's growing count reaches the UI at most this often
FAILED_HELLO_LIMIT = 10   # K-70: connects 10/min per IP, and only failed verifications count
TICK_S = 1.0
LINK_POLL_S = 5.0         # the admin CLI's switch reaches the running bridge within this
OWNER_ACTIVITY_WRITE_S = 60.0
TRACKED_MAX = 1024        # per-IP counters, fold windows and relayed pings kept at most
RELAYED_CALLS_MAX = 10_000
EXEC_PER_S, EXEC_PER_MIN = 10.0, 120.0  # K-70 message rates of one agent peer
EVENTS_PER_S, NOTICES_PER_S = 20, 20    # K-70 message rates of the node
AUDIT_ROWS_PER_S, AUDIT_ROWS_BURST = 1.0, 30.0  # DL_REJECT and node alert rows, whatever the number of addresses
LIMITS: dict[str, float] = {"exec_per_s": EXEC_PER_S, "exec_per_min": EXEC_PER_MIN, "events_per_s": EVENTS_PER_S,
                            "notices_per_s": NOTICES_PER_S, "connects_per_min": FAILED_HELLO_LIMIT,
                            "stale_after_s": STALE_AFTER_S}
OFF_POWER_STATES = frozenset({"suspend", "shutdown", "logoff"})
ONLINE_STATES = frozenset({"online", "locked", "panic"})

ALERT_SIGNATURE = "SIGNATURE_INVALID"
ALERT_HELLO = "HELLO_INVALID"
ALERT_REPLACED = "DESKTOP_REPLACED"
ALERT_NEW_NODE = "NEW_NODE_ID"
ALERT_RATE = "LINK_RATE_LIMITED"
ALERT_CLOCK = "CLOCK_SKEW"
SEVERITY_CRITICAL, SEVERITY_WARNING = "critical", "warning"
# Titles for node alert notices that carry no message of their own (K-74 Notices).
NODE_ALERT_TITLES: Mapping[str, str] = {
    "SIGNATURE_INVALID": "The laptop refused a message with an invalid signature",
    "APPROVAL_INVALID": "The laptop refused an approval that does not match its command",
    "CLOCK_SKEW": "The laptop measured a clock skew above 60 s",
    "MANIFEST_MISMATCH": "The laptop's tool manifest does not match the agent's",
    "DESKTOP_RATE_LIMITED": "The laptop hit one of its rate caps",
}


# ---------------------------------------------------------------- listeners and client addresses


def is_internal_scope(scope: Any) -> bool:
    """True when the connection came in on the internal listener: its local port (ASGI scope['server'], which
    uvicorn takes from the socket, never from a header) is INTERNAL_PORT."""
    server = scope.get("server") if isinstance(scope, Mapping) else None
    try:
        return server is not None and int(server[1]) == INTERNAL_PORT
    except (TypeError, ValueError, IndexError):
        return False


def is_internal_environ(environ: Mapping[str, Any]) -> bool:
    """is_internal_scope for a python-engineio environ (its 'asgi.scope')."""
    return is_internal_scope(environ.get("asgi.scope"))


@functools.lru_cache(maxsize=1)
def docker_gateway() -> str | None:
    """This container's default gateway, the Docker network's gateway, from /proc/net/route (None elsewhere). The
    published port's proxy connects from it, so only then is nginx's X-Real-IP the client's address."""
    try:
        with open("/proc/net/route", encoding="ascii") as routes:
            next(routes, None)
            for line in routes:
                cols = line.split()
                if len(cols) > 3 and cols[1] == "00000000" and int(cols[3], 16) & 0x2:
                    return str(ipaddress.IPv4Address(bytes.fromhex(cols[2])[::-1]))
    except (OSError, ValueError):
        pass
    return None


def _trusted_proxy(peer: str) -> bool:
    try:
        if ipaddress.ip_address(peer).is_loopback:
            return True
    except ValueError:
        return False
    return peer == docker_gateway()


def client_ip(environ: Mapping[str, Any]) -> str:
    """The client address for the per-IP limit and audit rows: a valid X-Real-IP when the TCP peer is the Docker
    gateway (or loopback), else the TCP peer itself (K-70 Rates, C-116 step 6)."""
    scope = environ.get("asgi.scope")
    client = scope.get("client") if isinstance(scope, Mapping) else None
    peer = str(client[0]) if client else ""
    real = str(environ.get("HTTP_X_REAL_IP") or "").strip()
    if peer and real and _trusted_proxy(peer):
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            pass
    return peer or "unknown"


# ---------------------------------------------------------------- keys


@dataclass(frozen=True)
class LinkKeys:
    """The pinned Ed25519 public keys of the two link roles; the bridge holds no private link key (K-70 Keys)."""

    agent: Ed25519PublicKey
    pc: Ed25519PublicKey


def load_link_keys(environ: Mapping[str, str] | None = None) -> tuple[LinkKeys | None, str]:
    """(keys, '') from DESKTOP_AGENT_PUBLIC_KEY and DESKTOP_PC_PUBLIC_KEY (bridge.env, K-47 v4), or (None, why not).
    A key that is not canonical or of small order is refused. The reason names variables, never values."""
    env = os.environ if environ is None else environ
    values = {name: str(env.get(name) or "").strip() for name in (ENV_AGENT_KEY, ENV_PC_KEY)}
    missing = [name for name, value in values.items() if not value]
    if missing:
        return None, " and ".join(missing) + (" is" if len(missing) == 1 else " are") + " not set"
    loaded: dict[str, Ed25519PublicKey] = {}
    for name, value in values.items():
        try:
            loaded[name] = dp.public_key_from_hex(value)
        except ValueError:
            return None, f"{name} is not a usable Ed25519 public key"
    return LinkKeys(agent=loaded[ENV_AGENT_KEY], pc=loaded[ENV_PC_KEY]), ""


# ---------------------------------------------------------------- small helpers


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _label(value: Any, limit: int = 64) -> str | None:
    """A node-supplied label (version, git_sha) for desktop_status: printable text up to limit, else None."""
    if isinstance(value, str) and 0 < len(value) <= limit and value.isprintable():
        return value
    return None


def _link_value(value: Any) -> str:
    return LINK_OFF if str(value or "").strip().lower() in (LINK_OFF, "0", "false") else LINK_ON


def _wire_text(message: Any) -> str:
    """The envelope as the JSON text the bridge relays: the received text itself, or the canonical form."""
    if isinstance(message, str):
        return message
    if isinstance(message, (bytes, bytearray, memoryview)):
        return bytes(message).decode("utf-8")
    return dp.canonical_json(dict(message)).decode("utf-8")


def _json_text(value: Mapping[str, Any]) -> str:
    return dp.canonical_json(dict(value)).decode("utf-8")


def _peek(message: Any) -> tuple[str | None, float | None, str | None]:
    """(typ, ts, call_id) of a refused message that was authentic, so already parsed once; never its body."""
    try:
        env = message if isinstance(message, Mapping) else dp.loads_strict(message)
    except (dp.CanonicalJsonError, TypeError, ValueError):
        return None, None, None
    if not isinstance(env, Mapping):
        return None, None, None
    typ, ts, call_id = env.get("typ"), env.get("ts"), env.get("call_id")
    return (typ if isinstance(typ, str) else None, float(ts) if _is_number(ts) else None,
            call_id if isinstance(call_id, str) else None)


def _call_note(env: VerifiedEnvelope) -> str:
    """call_id and tool for a log line (K-70 Logging); never arguments or results."""
    parts = [f"{name}={env.header[name]}" for name in ("call_id", "tool") if name in env.header]
    return (" " + " ".join(parts)) if parts else ""


def _check_hello(env: VerifiedEnvelope, role: str) -> None:
    """A hello of the role's type whose body names its sender (K-70 Messages); else an authentic 'format' refusal."""
    header, body = env.header, env.body
    sender_id = env.sender.partition(":")[2]
    if role == ROLE_PC:
        ok = env.typ == "hello" and body.get("node_id") == sender_id and body.get("sess") == header.get("sess")
    else:
        ok = (env.typ == "agent_hello" and body.get("peer_id") == sender_id
              and body.get("room", header["room"]) == header["room"])
    if not ok:
        raise DesktopLinkError("format", authentic=True)


def _hello_title(role: str, reason: str) -> str:
    who = "laptop" if role == ROLE_PC else "agent"
    if reason == "signature":
        return f"Desktop link: a {who} hello with an invalid signature was refused"
    if reason == "replay":
        return f"Desktop link: a replayed {who} hello was refused"
    return f"Desktop link: a {who} hello failed verification ({reason})"


# ---------------------------------------------------------------- state records


@dataclass
class LinkPeer:
    """One '/desktop' socket whose hello verified; its Receiver is bound to that hello's sender."""

    sid: str
    role: str
    ident: str
    ip: str
    receiver: Receiver
    since: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    room: str = ""
    job_id: str = ""
    execs: TokenBucket = field(default_factory=lambda: TokenBucket(EXEC_PER_S))
    execs_per_min: TokenBucket = field(default_factory=lambda: TokenBucket(EXEC_PER_MIN / 60.0, EXEC_PER_MIN))
    notices: TokenBucket = field(default_factory=lambda: TokenBucket(NOTICES_PER_S))
    events: TokenBucket = field(default_factory=lambda: TokenBucket(EVENTS_PER_S))


@dataclass
class PendingCall:
    """An exec relayed to the node whose result has not come back (the K-70 call_id -> agent map)."""

    call_id: str
    agent_sid: str
    node_sid: str
    tool: str
    first: float  # monotonic, first relay
    last: float   # monotonic, the relay or the node's latest ack or progress
    acked: bool = False


@dataclass
class _Fold:
    """DL_REJECT repeats of one reason on one socket (or from one address) within FOLD_S."""

    first: float
    count: int
    actor: str
    ip: str
    detail: dict[str, Any]


@dataclass
class _AlertFold:
    """One system_alert and its repeats within FOLD_S."""

    alert: dict[str, Any]
    first: float
    emitted_at: float
    emitted_count: int
    ip: str
    source: str


# ---------------------------------------------------------------- the relay


class DesktopLink:  # pylint: disable=too-many-public-methods  # the relay plus its status, controls and alerts
    """The '/desktop' relay, desktop_status (K-74) and the persisted link controls of one bridge process.

    sio is the bridge's socketio.AsyncServer; store() returns its BridgeStore (looked up on every call, so a store
    the bridge replaces is used); emit_ui(event, data) sends to the UI sockets on '/'. With keys=None, start() loads
    DESKTOP_AGENT_PUBLIC_KEY and DESKTOP_PC_PUBLIC_KEY, in the VPS bridge only (JARVIS_ROLE=bridge); without them the
    relay stays off and refuses every '/desktop' connection, while desktop_status still answers (offline) and the UI
    stop is still persisted. wall and mono are the clocks; the Receivers check freshness on wall, as K-70 asks of the
    bridge (no clock estimator).
    """

    def __init__(self, *, sio: Any, store: Callable[[], BridgeStore],
                 emit_ui: Callable[[str, Any], Awaitable[None]], keys: LinkKeys | None = None,
                 seen_path: str | None = None, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self._sio = sio
        self._store = store
        self._emit_ui_fn = emit_ui
        self._wall = wall
        self._mono = mono
        self.keys = keys
        self._seen_path = seen_path
        self.seen: SeenStore | None = None
        self.active = False
        self._started = False
        self._task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        # persisted controls
        self.link_on = True
        self._controls_ok = False  # read once from the kv; until then the link stays off (fail closed)
        self.stop_pending: dict[str, Any] | None = None
        self.active_job: str | None = None
        self.last_activity_ts: float | None = None
        self._activity_written = -math.inf
        # peers and the node session
        self.peers: dict[str, LinkPeer] = {}
        self._agent_sids: dict[str, str] = {}
        self.node_sid: str | None = None
        self.node_sess: str | None = None
        # desktop_status
        self.node_id: str | None = None
        self.version: str | None = None
        self.git_sha: str | None = None
        self.mode, self.locked, self.panic = "off", False, False
        self._persisted_state: tuple[Any, Any, Any] | None = None  # (mode, locked, panic) as the kv holds it
        self.manifest_ok = True
        self.power_off = False
        self.last_seen: float | None = None
        self._seen_mono: float | None = None
        self._state = "offline"
        self._since = wall()
        self._sent_key: Any = None
        self._voice_mono = -math.inf
        self._vitals: dict[str, Any] | None = None
        self._vitals_mono = -math.inf
        # calls, pings, failures and folds
        self._calls: OrderedDict[str, PendingCall] = OrderedDict()
        self._relayed: OrderedDict[str, float] = OrderedDict()
        self._pings: OrderedDict[str, float] = OrderedDict()
        self._rtts: deque[float] = deque(maxlen=15)
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._rejects: OrderedDict[tuple[str, str], _Fold] = OrderedDict()
        self._alerts: dict[tuple[str, str], _AlertFold] = {}  # (source, code)
        self._audit_rows = TokenBucket(AUDIT_ROWS_PER_S, AUDIT_ROWS_BURST, clock=mono)
        self._audit_dropped: dict[str, int] = {}
        self._audit_dropped_since = -math.inf
        self._link_polled = -math.inf

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Lifespan startup (idempotent): re-applies last_activity_ts; with the link keys it also opens the seen
        store (desktop_seen.db next to bridge.db, namespace 'bridge'), re-applies the persisted controls and starts
        the 1 s maintenance loop."""
        if self._started:
            return
        self._started = True
        values = await self._kv_get_many((KV_LAST_ACTIVITY_TS,)) or {}
        if _is_number(values.get(KV_LAST_ACTIVITY_TS)):
            self.last_activity_ts = float(values[KV_LAST_ACTIVITY_TS])
        if self.keys is None:
            if runtime_role() != ROLE_BRIDGE:  # the PC's local bridge (vortex.py) never relays the Desktop Link
                logger.info("Desktop Link relay is off: JARVIS_ROLE is not bridge")
                return
            self.keys, why = load_link_keys()
            if self.keys is None:
                logger.info("Desktop Link relay is off: %s; '/desktop' refuses every connection", why)
                return
        path = self._seen_path or os.path.join(os.path.dirname(os.path.abspath(self._store().path)), SEEN_FILE)
        try:
            self.seen = await asyncio.to_thread(SeenStore, path, namespace=SEEN_NAMESPACE)
        except Exception:  # pylint: disable=broad-exception-caught  # any failure keeps the relay closed
            logger.error("Desktop Link relay is off: its seen store %s cannot be opened", path, exc_info=True)
            return
        self._controls_ok = await self._load_controls()
        if not self._controls_ok:
            logger.critical("Desktop link: the persisted controls cannot be read; the link stays off until they can")
        self.active = True
        self._task = asyncio.create_task(self._maintain(), name="desktop-link")
        logger.info("Desktop Link relay ready: link %s, seen store %s",
                    LINK_ON if self.link_on else LINK_OFF, path)

    async def stop(self) -> None:
        """Lifespan shutdown: stops the loop, writes the folded audit rows and closes the seen store; call it
        before the bridge closes its store."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.drain()
        await self._flush_folds(force=True)
        if self.seen is not None:
            seen, self.seen = self.seen, None
            await asyncio.to_thread(seen.close)
        self.active = False
        self._started = False

    async def drain(self) -> None:
        """Waits for the post-connect work still running."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _load_controls(self) -> bool:
        """Applies the persisted controls; False when they cannot be read, and then the link is off (fail closed,
        tick() reads again). A switch missing after C-13 renamed a corrupt bridge.db aside counts as off and is
        written as off; a database that never held one keeps the default, on. What memory already holds (a stop or
        a job set while the read failed) wins over the kv."""
        values = await self._kv_get_many((KV_DESKTOP_LINK, KV_DESKTOP_STOP, KV_ACTIVE_JOB, KV_NODE_ID,
                                          KV_NODE_STATE, KV_NODE_VITALS))
        if values is None:
            self.link_on = False
            return False
        if KV_DESKTOP_LINK in values:
            self.link_on = _link_value(values[KV_DESKTOP_LINK]) == LINK_ON
        elif getattr(self._store(), "corrupt_renamed", 0):
            self.link_on = False
            await self._kv_set(KV_DESKTOP_LINK, LINK_OFF)
            await self._audit("bridge", "desktop_link", {"state": LINK_OFF, "reason": "lost_with_corrupt_db"})
            logger.critical("Desktop link: its persisted switch was lost with a corrupt bridge.db and counts as off; "
                            "'python -m src.core.bridge_desktop link on' turns the link on again")
        else:
            self.link_on = True
        stop = values.get(KV_DESKTOP_STOP)
        if self.stop_pending is None and isinstance(stop, dict):
            self.stop_pending = dict(stop)
        self.active_job = self.active_job or _label(values.get(KV_ACTIVE_JOB), 128)
        self.node_id = self.node_id or _label(values.get(KV_NODE_ID))
        state = values.get(KV_NODE_STATE)
        if isinstance(state, dict):
            self._persisted_state = (state.get("mode"), state.get("locked"), state.get("panic"))
            if self.last_seen is None:  # no node message since boot: the stored state is the latest known
                self._apply_state(state)
        if self._vitals is None and isinstance(values.get(KV_NODE_VITALS), dict):
            self._vitals = dict(values[KV_NODE_VITALS])  # shown as the last reading, never as a fresh one
        return True

    # ------------------------------------------------------------ Socket.IO handlers ('/desktop')

    async def on_connect(self, sid: str, environ: Mapping[str, Any], auth: Any) -> bool:
        """A '/desktop' connect: the public listener takes the node's hello, the internal one an agent peer's
        agent_hello (auth {"hello": text}). Refusals raise ConnectionRefusedError with a short reason or return
        False after a failed verification (alerted and audited, K-74). The per-IP limit applies only to a hello that
        failed verification, so one that verifies always passes: an address a forger made 'the laptop's' (a forged
        X-Real-IP, a shared NAT) never locks the node out (C-116 step 6)."""
        role = ROLE_AGENT if is_internal_environ(environ) else ROLE_PC
        ip = client_ip(environ)
        if not self.active or self.keys is None or self.seen is None:
            raise SocketRefused("link_unconfigured")
        if role == ROLE_PC and not self.link_on:
            raise SocketRefused("link_disabled")
        text = auth.get(AUTH_HELLO) if isinstance(auth, Mapping) else None
        receiver = Receiver(ident=dp.BRIDGE_IDENT, public_keys={role: getattr(self.keys, role)}, seen=self.seen,
                            sess=self.node_sess, wall=self._wall)
        try:
            if not isinstance(text, (str, bytes)):
                raise DesktopLinkError("format")
            env = await asyncio.to_thread(receiver.verify, text)
            _check_hello(env, role)
        except DesktopLinkError as exc:
            if role == ROLE_PC and not exc.authentic and self._throttled(ip):
                await self._alert(ALERT_RATE, SEVERITY_WARNING,
                                  "Desktop link: too many failed laptop hellos from one address; it waits a minute",
                                  ip=ip)
                raise SocketRefused("rate_limited") from None
            await self._hello_refused(role, ip, text, exc)
            return False
        if role == ROLE_PC and not self.link_on:  # switched off while the hello was checked
            raise SocketRefused("link_disabled")
        peer = LinkPeer(sid, role, env.sender, ip, receiver, self._wall(),
                        notices=TokenBucket(NOTICES_PER_S, clock=self._mono),
                        events=TokenBucket(EVENTS_PER_S, clock=self._mono))
        await peer.lock.acquire()  # released once the post-connect work ran, so this socket's messages wait
        self.peers[sid] = peer
        old = self._take_node(peer, env) if role == ROLE_PC else self._take_agent(peer, env)
        self._spawn(self._after_hello(peer, env, _wire_text(text), old))
        logger.info("Desktop link: %s connected (%s listener) from %s", env.sender,
                    "internal" if role == ROLE_AGENT else "public", ip)
        return True

    async def on_msg(self, sid: str, data: Any = None) -> None:
        """One 'msg' event: verified on this socket's Receiver (in a worker thread: one seen-store write), then
        relayed or, for a notice, consumed. Messages of one socket are handled in arrival order."""
        peer = self.peers.get(sid)
        if peer is None:
            return
        async with peer.lock:
            if self.peers.get(sid) is not peer:
                return
            try:
                env = await asyncio.to_thread(peer.receiver.verify, data)
            except DesktopLinkError as exc:
                await self._message_refused(peer, data, exc)
                return
            try:
                if peer.role == ROLE_PC:
                    await self._from_node(peer, env, _wire_text(data))
                else:
                    await self._from_agent(peer, env, _wire_text(data))
            except DesktopLinkError as exc:
                await self._message_refused(peer, data, exc)
            except Exception:  # pylint: disable=broad-exception-caught  # one bad message never stops the link
                logger.exception("Desktop link: handling %s from %s failed", env.typ, peer.ident)

    async def on_disconnect(self, sid: str, *_reason: Any) -> None:
        peer = self.peers.pop(sid, None)
        if peer is None:
            return
        await self._flush_folds(key=sid)
        if peer.role == ROLE_PC:
            await self._fail_calls(sid)
            if self.node_sid == sid:
                self.node_sid = None
                logger.info("Desktop link: the laptop node %s disconnected", peer.ident)
        else:
            if self._agent_sids.get(peer.ident) == sid:
                del self._agent_sids[peer.ident]
            for call_id in [c for c, call in self._calls.items() if call.agent_sid == sid]:
                del self._calls[call_id]
            logger.info("Desktop link: agent peer %s disconnected", peer.ident)
        await self._status_changed()

    # ------------------------------------------------------------ hellos and sessions

    def _take_node(self, peer: LinkPeer, env: VerifiedEnvelope) -> str | None:
        """Makes peer the node socket (a newer one replaces an older one) and opens its session; returns the
        replaced socket's sid."""
        old = self.node_sid if self.node_sid not in (None, peer.sid) else None
        self.node_sid = peer.sid
        self._failures.pop(peer.ip, None)
        self._open_session(peer, env)
        return old

    def _open_session(self, node: LinkPeer, env: VerifiedEnvelope) -> None:
        """A verified node hello opens or rotates the session: the node socket is bound to its sender, and every
        agent peer's Receiver to the new sess (K-70 checks 3 and 6)."""
        node.receiver.bind(env.sess or "", env.sender)
        node.ident = env.sender
        if env.sess != self.node_sess:
            self.node_sess = env.sess
            for agent in self._agents():
                agent.receiver.bind(env.sess or "", agent.receiver.peer)

    def _take_agent(self, peer: LinkPeer, env: VerifiedEnvelope) -> str | None:
        """Binds an agent peer's socket to its agent_hello (room, job) and returns an older socket of the same
        peer, which this one replaces."""
        peer.receiver.bind(self.node_sess or "", env.sender)
        peer.ident = env.sender
        peer.room = str(env.header.get("room") or "")
        peer.job_id = _label(env.body.get("job_id")) or ""
        for ident in [i for i, s in self._agent_sids.items() if s == peer.sid and i != env.sender]:
            del self._agent_sids[ident]
        old = self._agent_sids.get(env.sender)
        self._agent_sids[env.sender] = peer.sid
        return old if old not in (None, peer.sid) and old in self.peers else None

    async def _after_hello(self, peer: LinkPeer, env: VerifiedEnvelope, text: str, old: str | None) -> None:
        """The I/O after a verified connect hello; it holds the peer's lock, so the peer's own messages wait."""
        try:
            if peer.role == ROLE_PC:
                await self._node_hello(peer, env, text, replaced=old)
            else:
                if old is not None:
                    await self._disconnect(old)
                await self._send_hint(peer, "welcome", self._hint_body())
                await self._relay_to_node(env, text)
        except Exception:  # pylint: disable=broad-exception-caught  # the socket stays usable
            logger.exception("Desktop link: post-connect work for %s failed", peer.ident)
        finally:
            peer.lock.release()

    async def _node_hello(self, node: LinkPeer, env: VerifiedEnvelope, text: str, *, replaced: str | None) -> None:
        """A verified node hello (connect or 'msg'): desktop_status fields, replacement and new-node alerts, the
        welcome, a pending stop, active_job, and the relay to the agent peers."""
        previous, node_id = self.node_id, env.sender.partition(":")[2]
        body = env.body
        self.node_id = node_id
        self.version, self.git_sha = _label(body.get("version")), _label(body.get("git_sha"))
        self._apply_state({"mode": body.get("mode"), "locked": body.get("locked")})
        self.manifest_ok = True
        self._counted()
        if replaced is not None:
            await self._disconnect(replaced)
            await self._alert(ALERT_REPLACED, SEVERITY_WARNING,
                              f"Laptop node {node_id} connected again; its older connection was closed", ip=node.ip)
        if previous and previous != node_id:
            await self._alert(ALERT_NEW_NODE, SEVERITY_WARNING,
                              f"A different laptop node connected: {node_id} (before: {previous})", ip=node.ip)
        if previous != node_id:
            await self._kv_set(KV_NODE_ID, node_id)
        await self._send_hint(node, "welcome", self._hint_body())
        await self._apply_stop(node)
        if self.active_job:
            await self._send_hint(node, "control", {"kind": "active_job", "job_id": self.active_job})
        await self._relay_from_node(env, text)
        await self._status_changed()

    async def _hello_refused(self, role: str, ip: str, text: Any, exc: DesktopLinkError) -> None:
        """A connect hello that failed: clock skew for an authentic stale or future node hello (warning, never
        counted); a seen store that cannot answer is logged; anything else is the critical alert, and an
        unauthentic node hello also counts for the per-IP limit."""
        typ, ts, _ = _peek(text) if exc.authentic else (None, None, None)
        if exc.authentic and exc.reason in ("stale", "future"):
            if role == ROLE_PC:
                await self._clock_skew(ts, ip)
        elif exc.reason == "seen_store":
            logger.error("Desktop link: a %s hello could not be checked: the seen store did not answer", role)
        else:
            if role == ROLE_PC and not exc.authentic:
                self._count_failure(ip)
            await self._alert(ALERT_SIGNATURE if exc.reason == "signature" else ALERT_HELLO, SEVERITY_CRITICAL,
                              _hello_title(role, exc.reason), ip=ip)
        await self._reject(f"ip:{ip}", exc, actor=f"{role}:?", ip=ip, typ=typ or "hello")

    # ------------------------------------------------------------ messages

    async def _from_node(self, node: LinkPeer, env: VerifiedEnvelope, text: str) -> None:
        typ = env.typ
        if typ not in ("ping", "pong"):
            self._counted()
        if typ == "hello":
            _check_hello(env, ROLE_PC)
            self._open_session(node, env)
            await self._node_hello(node, env, text, replaced=None)
            return
        if typ in ("notice", "event") and not (node.notices if typ == "notice" else node.events).take():
            # K-70 Rates: a node loop gone wild costs neither kv writes nor UI emits nor agent relays
            await self._alert(ALERT_RATE, SEVERITY_WARNING, f"Desktop link: the laptop exceeded the {typ} rate",
                              ip=node.ip)
            await self._reject(node.sid, DesktopLinkError("rate", authentic=True), actor=node.ident, ip=node.ip,
                               typ=typ)
            return
        if typ == "notice":
            await self._notice(env)
            await self._status_changed()
            return
        if typ == "pong":
            started = self._pings.pop(str(env.body.get("echo")), None)
            if started is not None:
                self._rtts.append(self._mono() - started)
        self._node_call_activity(env)
        await self._relay_from_node(env, text)
        await self._status_changed()

    async def _from_agent(self, agent: LinkPeer, env: VerifiedEnvelope, text: str) -> None:
        typ = env.typ
        if typ == "agent_hello":
            _check_hello(env, ROLE_AGENT)
            old = self._take_agent(agent, env)
            if old is not None:
                await self._disconnect(old)
            await self._send_hint(agent, "status", self._hint_body())
            await self._relay_to_node(env, text)
            return
        if typ == "exec":
            await self._exec(agent, env, text)
            return
        if typ == "ping" and self._deliverable_node(env) is not None:
            self._pings[env.header["id"]] = self._mono()
            while len(self._pings) > TRACKED_MAX:
                self._pings.popitem(last=False)
        await self._relay_to_node(env, text)

    async def _exec(self, agent: LinkPeer, env: VerifiedEnvelope, text: str) -> None:
        """An exec: relayed only when the node can take it, else dl_undeliverable with emitted as the bridge knows
        it (K-70 Outcomes); every relay is recorded in the call_id map."""
        call_id = env.call_id or ""
        if not (agent.execs.take() and agent.execs_per_min.take()):
            await self._alert(ALERT_RATE, SEVERITY_WARNING, "Desktop link: an agent peer exceeded the exec rate",
                              ip=agent.ip)
            await self._reject(agent.sid, DesktopLinkError("rate", authentic=True), actor=agent.ident,
                               ip=agent.ip, typ="exec")
            await self._undeliverable(agent, call_id)
            return
        node = self._deliverable_node(env)
        if node is None:
            logger.info("Desktop link: exec%s from %s not delivered: the laptop is not connected", _call_note(env),
                        agent.ident)
            await self._undeliverable(agent, call_id)
            return
        now = self._mono()
        known = self._calls.pop(call_id, None)
        self._calls[call_id] = PendingCall(call_id, agent.sid, node.sid, str(env.header.get("tool") or ""),
                                           first=known.first if known else now, last=now,
                                           acked=bool(known and known.acked))
        self._relayed.setdefault(call_id, now)
        while len(self._relayed) > RELAYED_CALLS_MAX:
            self._relayed.popitem(last=False)
        await self._emit(EVENT_MSG, text, node.sid)
        logger.debug("Desktop link: relayed exec%s (%d bytes) from %s", _call_note(env), env.size, agent.ident)
        await self._status_changed()

    def _node_call_activity(self, env: VerifiedEnvelope) -> None:
        call = self._calls.get(env.call_id or "")
        if call is None:
            return
        if env.typ in ("result", "result_resend"):
            del self._calls[call.call_id]
        else:
            call.last = self._mono()
            call.acked = call.acked or env.typ == "ack"

    def _agents(self) -> list[LinkPeer]:
        return [peer for peer in self.peers.values() if peer.role == ROLE_AGENT]

    def _deliverable_node(self, env: VerifiedEnvelope) -> LinkPeer | None:
        """The node socket an agent message may go to: the link is on, a node is connected and 'to' names it."""
        node = self.peers.get(self.node_sid) if self.link_on and self.node_sid else None
        if node is None or env.header["to"] not in ("pc:*", node.ident):
            return None
        return node

    async def _relay_to_node(self, env: VerifiedEnvelope, text: str) -> bool:
        node = self._deliverable_node(env)
        if node is None:
            logger.info("Desktop link: %s%s from %s dropped: no laptop node for %s", env.typ, _call_note(env),
                        env.sender, env.header["to"])
            return False
        await self._emit(EVENT_MSG, text, node.sid)
        logger.debug("Desktop link: relayed %s%s (%d bytes) from %s", env.typ, _call_note(env), env.size,
                     env.sender)
        return True

    async def _relay_from_node(self, env: VerifiedEnvelope, text: str) -> bool:
        """node -> 'agent:*' goes to every agent peer, node -> 'agent:<id>' to that peer's socket (K-70 routing);
        with no such peer the message is dropped at INFO."""
        to = env.header["to"]
        if to == "agent:*":
            targets = [agent.sid for agent in self._agents()]
        else:
            sid = self._agent_sids.get(to)
            targets = [sid] if sid in self.peers else []
        if not targets:
            logger.info("Desktop link: %s%s from the laptop dropped: no agent peer for %s", env.typ,
                        _call_note(env), to)
            return False
        for target in targets:
            await self._emit(EVENT_MSG, text, target)
        logger.debug("Desktop link: relayed %s%s (%d bytes) to %s", env.typ, _call_note(env), env.size, to)
        return True

    async def _undeliverable(self, agent: LinkPeer, call_id: str) -> None:
        """dl_undeliverable {call_id, emitted, acked}: emitted false only when no exec of this call was relayed."""
        if not call_id:
            return
        call = self._calls.get(call_id)
        await self._send_hint(agent, "dl_undeliverable", {"call_id": call_id, "emitted": call_id in self._relayed,
                                                          "acked": bool(call and call.acked)})

    async def _fail_calls(self, node_sid: str) -> None:
        """The node connection dropped: every call still in flight on it (relay or node activity within
        CALL_ACTIVE_S) gets dl_undeliverable {emitted: true, acked}. A refused call (an ack and nothing more, which
        the bridge cannot tell from an accept) or one its agent gave up on gets nothing; the map forgets them all."""
        now = self._mono()
        for call_id, call in list(self._calls.items()):
            if call.node_sid != node_sid:
                continue
            del self._calls[call_id]
            agent = self.peers.get(call.agent_sid)
            if agent is not None and now - call.last < CALL_ACTIVE_S:
                await self._send_hint(agent, "dl_undeliverable",
                                      {"call_id": call_id, "emitted": True, "acked": call.acked})

    async def _message_refused(self, peer: LinkPeer, data: Any, exc: DesktopLinkError) -> None:
        """A refused 'msg': dropped and audited as DL_REJECT (folded); a bad signature raises the critical alert,
        an authentic stale or future node hello or notice the clock-skew warning, any other failed hello (replayed,
        or a body that does not name its sender) the critical alert as at connect, and an authentic exec that the
        bridge never relayed gets dl_undeliverable."""
        typ, ts, call_id = _peek(data) if exc.authentic else (None, None, None)
        if exc.reason == "signature" and not exc.authentic:
            await self._alert(ALERT_SIGNATURE, SEVERITY_CRITICAL,
                              f"Desktop link: a message with an invalid signature was refused on {peer.ident}'s "
                              "connection", ip=peer.ip)
        elif (peer.role == ROLE_PC and exc.authentic and exc.reason in ("stale", "future")
              and typ in ("hello", "notice")):
            await self._clock_skew(ts, peer.ip)
        elif typ in dp.HELLO_TYPES and exc.reason not in ("stale", "future", "seen_store"):
            await self._alert(ALERT_HELLO, SEVERITY_CRITICAL, _hello_title(peer.role, exc.reason), ip=peer.ip)
        elif (peer.role == ROLE_AGENT and exc.authentic and typ == "exec" and call_id
              and exc.reason in ("session", "stale", "future")):
            await self._undeliverable(peer, call_id)
        await self._reject(peer.sid, exc, actor=peer.ident, ip=peer.ip, typ=typ)

    # ------------------------------------------------------------ notices (K-74)

    async def _notice(self, env: VerifiedEnvelope) -> None:
        """A node notice: only the latest values are kept (kv, in place); an alert notice becomes a system_alert and
        an audit row. Notice values are never logged or relayed."""
        body = env.body
        kind = body["kind"]
        now = self._wall()
        if kind == "vitals_pc":
            vitals = {name: body[name] for name in ("cpu", "ram", "disk", "disk_label", "cores", "ram_total_gb")
                      if name in body}
            vitals["host"] = body.get("host") or self.node_id
            vitals["ts"] = round(now, 3)
            self._vitals, self._vitals_mono = vitals, self._mono()
            await self._kv_set(KV_NODE_VITALS, vitals)
            await self._emit_ui("vitals_pc", vitals)
            await self._emit_ui("vitals_update", vitals)
        elif kind == "desktop_state":
            self._apply_state(body)
            state = (self.mode, self.locked, self.panic)
            # compared with what the kv holds, not with memory: the hello right before already set mode and locked
            if state != self._persisted_state and await self._kv_set(
                    KV_NODE_STATE, {"mode": self.mode, "locked": self.locked, "panic": self.panic,
                                    "ts": round(now, 3)}):
                self._persisted_state = state
            if self.stop_pending is not None and self.mode in ("read", "off"):
                await self._clear_stop()
        elif kind == "power":
            self.power_off = body["state"] in OFF_POWER_STATES
            await self._kv_set(KV_NODE_POWER, {"state": body["state"], "ts": round(now, 3)})
        elif kind == "alert":
            code = body["code"]
            if code == "MANIFEST_MISMATCH":
                self.manifest_ok = False
            detail = {name: body[name] for name in ("level", "message", "call_id", "tool", "peer") if name in body}
            await self._alert(code, body["level"], body.get("message") or NODE_ALERT_TITLES.get(code, code),
                              count=int(body.get("count", 1)), source="node", detail=detail, to_node=False)

    def _apply_state(self, values: Mapping[str, Any]) -> None:
        if values.get("mode") in dp.DESKTOP_MODES:
            self.mode = values["mode"]
        if isinstance(values.get("locked"), bool):
            self.locked = values["locked"]
        if isinstance(values.get("panic"), bool):
            self.panic = values["panic"]

    def _counted(self) -> None:
        """A verified node message other than ping or pong: presence (K-74), and the end of a power-off state."""
        self.last_seen, self._seen_mono = self._wall(), self._mono()
        self.power_off = False

    # ------------------------------------------------------------ desktop_status (K-74)

    def _refresh_state(self) -> str:
        if self.node_sid is None or self.power_off:
            state = "offline"
        elif self._seen_mono is None or self._mono() - self._seen_mono > STALE_AFTER_S:
            state = "stale"
        elif self.panic:
            state = "panic"
        elif self.locked:
            state = "locked"
        else:
            state = "online"
        if state != self._state:
            self._state, self._since = state, self._wall()
        return state

    def status(self) -> dict[str, Any]:
        """desktop_status (K-74), built from the link alone, so it stays right with no agent peer connected."""
        state = self._refresh_state()
        status: dict[str, Any] = {
            "online": state in ONLINE_STATES, "state": state, "mode": self.mode, "locked": self.locked,
            "voice_active": self.voice_active(), "node_id": self.node_id, "version": self.version,
            "git_sha": self.git_sha, "manifest_ok": self.manifest_ok, "since": round(self._since, 3),
            "last_seen": None if self.last_seen is None else round(self.last_seen, 3),
            "rtt_ms_p50": self.rtt_ms_p50(), "brain": active_brain(),
        }
        busy = self._busy_tool()
        if busy:
            status["busy_tool"] = busy
        return status

    def voice_active(self) -> bool:
        return self._mono() - self._voice_mono < VOICE_ACTIVE_S

    def rtt_ms_p50(self) -> float | None:
        """The median round trip, timed here, from an agent ping the bridge relayed to the node's pong."""
        return round(statistics.median(self._rtts) * 1000.0, 1) if self._rtts else None

    def _busy_tool(self) -> str | None:
        """The tool of the oldest relayed exec still without its result and with recent relay or node activity (a
        refused call gets an ack and nothing more, so it stops counting)."""
        now = self._mono()
        busy = [call for call in self._calls.values() if now - call.last < BUSY_WINDOW_S]
        return min(busy, key=lambda call: call.first).tool if busy else None

    def _hint_body(self, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The body of welcome and status hints (K-70 Bridge hints)."""
        return {"server_time": round(self._wall(), 3), "brain": active_brain(), "active_job": self.active_job,
                "link": LINK_ON if self.link_on else LINK_OFF, "limits": dict(LIMITS),
                "desktop_status": dict(status) if status is not None else self.status()}

    async def _status_changed(self, *, force: bool = False) -> None:
        """Sends desktop_status to the UI sockets and a status hint to every agent peer when it changed (last_seen
        alone does not count)."""
        status = self.status()
        key = ({name: value for name, value in status.items() if name != "last_seen"}, self.link_on,
               self.active_job)
        if key == self._sent_key and not force:
            return
        self._sent_key = key
        await self._emit_ui("desktop_status", status)
        body = self._hint_body(status)
        for agent in self._agents():
            await self._send_hint(agent, "status", body)

    # ------------------------------------------------------------ controls (C-13 kv)

    async def ui_desktop_stop(self, *, by: str) -> None:
        """ui_command desktop_stop from a UI socket (K-74; tighten-only): persisted until the node reports mode read
        or off, sent to the node as control desktop_stop (again after every reconnect), a stop_all hint to every
        agent peer, and desktop_status to the UI sockets and the agent peers."""
        self.stop_pending = {"ts": round(self._wall(), 3), "by": by}
        await self._kv_set(KV_DESKTOP_STOP, self.stop_pending)
        await self._audit("ui", "desktop_stop", {"by": by})
        logger.warning("Desktop link: Stop desktop from the UI (%s): the laptop goes read-only", by)
        node = self.peers.get(self.node_sid) if self.node_sid else None
        if node is not None:
            await self._send_hint(node, "control", {"kind": "desktop_stop"})
        status = self.status()
        body = {**self._hint_body(status), "stop_all": True}
        for agent in self._agents():
            await self._send_hint(agent, "status", body)
        self._sent_key = ({name: value for name, value in status.items() if name != "last_seen"}, self.link_on,
                          self.active_job)
        await self._emit_ui("desktop_status", status)

    async def _apply_stop(self, node: LinkPeer) -> None:
        """At a node hello: a pending stop is cleared when the hello reports mode read or off, else sent again."""
        if self.stop_pending is None:
            return
        if self.mode in ("read", "off"):
            await self._clear_stop()
        else:
            await self._send_hint(node, "control", {"kind": "desktop_stop"})

    async def _clear_stop(self) -> None:
        self.stop_pending = None
        await self._kv_delete(KV_DESKTOP_STOP)
        logger.info("Desktop link: the laptop reports read-only or off; the UI stop is done")

    async def set_active_job(self, job_id: str | None) -> None:
        """The one agent job allowed to drive the laptop (K-76), persisted and sent to the node as control
        active_job (C-23 decides which job). Clearing it only forgets it: a control may never loosen."""
        self.active_job = _label(job_id, 128)
        if self.active_job is None:
            await self._kv_delete(KV_ACTIVE_JOB)
        else:
            await self._kv_set(KV_ACTIVE_JOB, self.active_job)
            node = self.peers.get(self.node_sid) if self.node_sid else None
            if node is not None:
                await self._send_hint(node, "control", {"kind": "active_job", "job_id": self.active_job})
        await self._status_changed()

    async def set_link(self, on: bool, *, by: str = "bridge") -> None:
        """Persists the desktop_link switch and applies it now. The admin CLI writes the same kv key from another
        process; the loop applies that within 5 s."""
        state = LINK_ON if on else LINK_OFF
        await self._kv_set(KV_DESKTOP_LINK, state)
        await self._audit(by, "desktop_link", {"state": state})
        await self._apply_link(on)

    async def _apply_link(self, on: bool) -> None:
        """The persisted switch takes effect. Off: the node gets control link_disabled and is disconnected, new node
        connects are refused and execs get dl_undeliverable; the agent peers get the status."""
        if on == self.link_on:
            return
        self.link_on = on
        logger.warning("Desktop link switched %s (persisted)", LINK_ON if on else LINK_OFF)
        node = self.peers.get(self.node_sid) if self.node_sid else None
        if not on and node is not None:
            await self._send_hint(node, "control", {"kind": "link_disabled"})
            await self._disconnect(node.sid)
        await self._status_changed()

    async def note_owner_activity(self) -> None:
        """Owner activity (a dashboard command): last_activity_ts, persisted at most once a minute (C-23 reads it)."""
        self.last_activity_ts = self._wall()
        now = self._mono()
        if now - self._activity_written >= OWNER_ACTIVITY_WRITE_S:
            self._activity_written = now
            await self._kv_set(KV_LAST_ACTIVITY_TS, round(self.last_activity_ts, 3))

    def note_voice_activity(self) -> None:
        """A voice turn or speech (bridge notify events): desktop_status.voice_active for a minute."""
        self._voice_mono = self._mono()

    def latest_pc_vitals(self) -> dict[str, Any] | None:
        """The node's last vitals_pc as sent to the UI (init_state.vitals_pc), or None."""
        return dict(self._vitals) if self._vitals is not None else None

    def fresh_pc_vitals(self) -> dict[str, Any] | None:
        """The node's vitals_pc when younger than 30 s: the legacy vitals_update prefers it (K-74 Vitals)."""
        if self._vitals is None or self._mono() - self._vitals_mono >= VITALS_FRESH_S:
            return None
        return dict(self._vitals)

    async def verify_pc_http(self, headers: Iterable[tuple[bytes, bytes]], *, method: str, path: bytes,
                             query: bytes, content_length: int | None, read_body: Callable[[], Awaitable[bytes]],
                             ip: str = "") -> bool:
        """K-70 PC-signed HTTP for a bodiless request (GET /vortex/status, K-43 v4): the headers before any body
        byte, the nonce in the seen store, then the empty body. False when the relay is off or anything fails."""
        if not self.active or self.keys is None or self.seen is None:
            return False
        try:
            auth = await asyncio.to_thread(dp.verify_http_headers, list(headers), method=method, path=path,
                                           query=query, public_key=self.keys.pc, seen=self.seen, max_length=0,
                                           content_length=content_length, now=self._wall())
            dp.verify_http_body(auth, await read_body())
        except DesktopLinkError as exc:
            await self._reject(f"http:{ip}", exc, actor="pc:?", ip=ip, typ="http")
            return False
        return True

    # ------------------------------------------------------------ alerts, refusals, rate limits

    async def _clock_skew(self, ts: float | None, ip: str) -> None:
        if ts is None:
            return
        skew = ts - self._wall()
        await self._alert(ALERT_CLOCK, SEVERITY_WARNING,
                          f"Laptop clock is off by {skew:+.0f} s (message time minus the bridge clock)", ip=ip)

    async def _alert(self, code: str, severity: str, title: str, *, ip: str = "", count: int = 1,
                     source: str = "bridge", detail: Mapping[str, Any] | None = None, to_node: bool = True) -> None:
        """K-74 alert: a system_alert to the UI sockets, an audit row and (bridge alerts) an alert hint to the node.
        Repeats of one code from one source (the bridge, or the node's alert notices) within 60 s fold into the
        same alert id with a count, so each source's first alert writes its row; every node alert notice writes its
        own audit row (capped). A node alert is logged without its message (K-70 Logging: never a notice body)."""
        now_mono, now = self._mono(), self._wall()
        row = {"code": code, "severity": severity, "title": title, "count": count, "source": source,
               **dict(detail or {})}
        key = (source, code)
        fold = self._alerts.get(key)
        if fold is not None and now_mono - fold.first < FOLD_S:
            fold.alert["count"] += count
            fold.alert["ts"] = round(now, 3)
            if source == "node":
                await self._capped_audit(source, "desktop_alert", row, ip, count)
            if now_mono - fold.emitted_at >= ALERT_REEMIT_S:
                fold.emitted_at, fold.emitted_count = now_mono, fold.alert["count"]
                await self._emit_ui("system_alert", dict(fold.alert))
            return
        if fold is not None:
            await self._close_alert(key, fold)
        alert = {"id": uuid.uuid4().hex, "severity": severity, "code": code, "title": title, "count": count,
                 "ts": round(now, 3)}
        self._alerts[key] = _AlertFold(alert, now_mono, now_mono, count, ip, source)
        log = logger.error if severity == SEVERITY_CRITICAL else logger.warning
        log("Desktop link alert %s (%s): %s", code, severity,
            f"a laptop alert notice, count {count}" if source == "node" else title)
        await self._emit_ui("system_alert", dict(alert))
        if source == "node":
            await self._capped_audit(source, "desktop_alert", row, ip, count)
        else:
            await self._audit(source, "desktop_alert", row, ip)
        node = self.peers.get(self.node_sid) if to_node and self.node_sid else None
        if node is not None:
            await self._send_hint(node, "status", {"alert": dict(alert)})

    async def _close_alert(self, key: tuple[str, str], fold: _AlertFold) -> None:
        """The end of an alert's fold window: the final count reaches the UI, and a bridge alert that repeated gets
        one more audit row with its count."""
        self._alerts.pop(key, None)
        if fold.alert["count"] > fold.emitted_count:
            await self._emit_ui("system_alert", dict(fold.alert))
        if fold.source == "bridge" and fold.alert["count"] > 1:
            await self._audit("bridge", "desktop_alert", {"code": fold.alert["code"],
                                                          "severity": fold.alert["severity"],
                                                          "title": fold.alert["title"],
                                                          "count": fold.alert["count"], "folded": True}, fold.ip)

    async def _reject(self, key: str, exc: DesktopLinkError, *, actor: str, ip: str, typ: str | None) -> None:
        """DL_REJECT (K-70): reason and code only, never content; repeats of one reason on one socket (or from one
        address) within 60 s fold into one audit row with a count, written when the window ends."""
        fold = self._rejects.get((key, exc.reason))
        if fold is not None:
            fold.count += 1
            return
        logger.info("Desktop link: refused %s from %s (%s %s)", typ or "a message", actor, exc.code, exc.reason)
        detail = {"reason": exc.reason, "code": exc.code, "authentic": exc.authentic}
        if typ:
            detail["typ"] = typ
        self._rejects[(key, exc.reason)] = _Fold(self._mono(), 1, actor, ip, detail)
        if len(self._rejects) > TRACKED_MAX:
            oldest = next(iter(self._rejects))
            await self._write_reject(self._rejects.pop(oldest))

    async def _write_reject(self, fold: _Fold) -> None:
        await self._capped_audit(fold.actor, "DL_REJECT", {**fold.detail, "count": fold.count}, fold.ip, fold.count)

    async def _capped_audit(self, actor: str, action: str, detail: Mapping[str, Any], ip: str, count: int) -> None:
        """An audit row that a flood could multiply (DL_REJECT, node alert notices): at most AUDIT_ROWS_PER_S, burst
        AUDIT_ROWS_BURST, however many addresses send (X-Real-IP can be forged while the published port is
        reachable). What the cap drops is counted in one row per action and window (_flush_folds)."""
        if self._audit_rows.take():
            await self._audit(actor, action, detail, ip)
            return
        if not self._audit_dropped:
            self._audit_dropped_since = self._mono()
        self._audit_dropped[action] = self._audit_dropped.get(action, 0) + max(1, count)

    async def _flush_folds(self, *, force: bool = False, key: str | None = None) -> None:
        now = self._mono()
        for fold_key in list(self._rejects):
            fold = self._rejects[fold_key]
            if force or fold_key[0] == key or now - fold.first >= FOLD_S:
                del self._rejects[fold_key]
                await self._write_reject(fold)
        for alert_key in list(self._alerts):
            alert = self._alerts[alert_key]
            if force or now - alert.first >= FOLD_S:
                await self._close_alert(alert_key, alert)
        if self._audit_dropped and (force or now - self._audit_dropped_since >= FOLD_S):
            dropped, self._audit_dropped = self._audit_dropped, {}
            logger.warning("Desktop link: %d refusals or laptop alerts over the audit cap were counted, not written "
                           "one by one", sum(dropped.values()))
            for action, count in dropped.items():
                await self._audit("bridge", action, {"capped": True, "count": count})

    def _throttled(self, ip: str) -> bool:
        """True while ip has FAILED_HELLO_LIMIT failed hellos within 60 s. on_connect asks only for a hello that
        failed verification, so the verified node is never throttled."""
        times = self._failures.get(ip)
        if not times:
            return False
        now = self._mono()
        while times and now - times[0] >= FOLD_S:
            times.popleft()
        return len(times) >= FAILED_HELLO_LIMIT

    def _count_failure(self, ip: str) -> None:
        times = self._failures.pop(ip, None) or deque(maxlen=FAILED_HELLO_LIMIT)
        times.append(self._mono())
        self._failures[ip] = times
        while len(self._failures) > TRACKED_MAX:
            self._failures.popitem(last=False)

    # ------------------------------------------------------------ maintenance

    async def _maintain(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.tick()
            except Exception:  # pylint: disable=broad-exception-caught  # the loop must outlive one bad tick
                logger.exception("Desktop link maintenance failed")

    async def tick(self) -> None:
        """Once a second: the persisted controls (every 5 s), expiries, fold windows and the stale transition."""
        now = self._mono()
        if now - self._link_polled >= LINK_POLL_S:
            self._link_polled = now
            await self._poll_controls()
        for call_id in [c for c, call in self._calls.items()
                        if now - call.last >= CALL_ACTIVE_S or now - call.first > CALL_TTL_S]:
            del self._calls[call_id]
        while self._relayed and now - next(iter(self._relayed.values())) > CALL_TTL_S:
            self._relayed.popitem(last=False)
        while self._pings and now - next(iter(self._pings.values())) > dp.PING_MAX_RTT_S:
            self._pings.popitem(last=False)
        await self._flush_folds()
        await self._status_changed()

    async def _poll_controls(self) -> None:
        """The admin CLI's switch takes effect. Until the controls were read once the link stays off; after that a
        read that fails changes nothing, and an off switch or a pending stop that the kv lost (a failed write, a
        corrupt bridge.db renamed aside) is written again from memory. A lost 'on' is not: on is the default, and
        that write could undo a concurrent 'link off' from the CLI."""
        if not self._controls_ok:
            self._controls_ok = await self._load_controls()
            if self._controls_ok:
                logger.warning("Desktop link: the persisted controls could be read again; link %s",
                               LINK_ON if self.link_on else LINK_OFF)
                await self._status_changed()
            return
        values = await self._kv_get_many((KV_DESKTOP_LINK, KV_DESKTOP_STOP))
        if values is None:
            return
        if KV_DESKTOP_LINK in values:
            await self._apply_link(_link_value(values[KV_DESKTOP_LINK]) == LINK_ON)
        elif not self.link_on:
            await self._kv_set(KV_DESKTOP_LINK, LINK_OFF)
        if self.stop_pending is not None and KV_DESKTOP_STOP not in values:
            await self._kv_set(KV_DESKTOP_STOP, self.stop_pending)

    # ------------------------------------------------------------ I/O helpers (never raise)

    async def _send_hint(self, peer: LinkPeer, typ: str, body: Mapping[str, Any]) -> None:
        try:
            hint = dp.build_bridge_hint(typ, body, to=peer.ident, now=self._wall())
        except (DesktopLinkError, dp.CanonicalJsonError):
            logger.error("Desktop link: a %s hint for %s could not be built", typ, peer.ident)
            return
        await self._emit(EVENT_HINT, _json_text(hint), peer.sid)

    async def _emit(self, event: str, data: Any, sid: str) -> None:
        try:
            await self._sio.emit(event, data, to=sid, namespace=NAMESPACE)
        except Exception:  # pylint: disable=broad-exception-caught  # a socket that just went away
            logger.debug("Desktop link: %s to %s not sent", event, sid, exc_info=True)

    async def _disconnect(self, sid: str) -> None:
        try:
            await self._sio.disconnect(sid, namespace=NAMESPACE)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Desktop link: disconnect of %s failed", sid, exc_info=True)

    async def _emit_ui(self, event: str, data: Any) -> None:
        try:
            await self._emit_ui_fn(event, data)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Desktop link: UI event %s not sent", event, exc_info=True)

    async def _kv_get_many(self, keys: Iterable[str]) -> dict[str, Any] | None:
        try:
            return await self._store().kv_get_many(keys)
        except Exception:  # pylint: disable=broad-exception-caught  # a failing store never stops the relay
            logger.warning("Desktop link: kv read failed", exc_info=True)
            return None

    async def _kv_set(self, key: str, value: Any) -> bool:
        try:
            await self._store().kv_set(key, value)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Desktop link: kv %s not written", key, exc_info=True)
            return False
        return True

    async def _kv_delete(self, key: str) -> None:
        try:
            await self._store().kv_delete(key)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Desktop link: kv %s not deleted", key, exc_info=True)

    async def _audit(self, actor: str, action: str, detail: Mapping[str, Any], ip: str = "") -> None:
        try:
            await self._store().audit(actor, action, dict(detail), ip)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Desktop link: audit row %s not written", action, exc_info=True)


# ---------------------------------------------------------------- admin CLI


def _age(ts: Any, now: float) -> str:
    return f"{max(0.0, now - float(ts)):.0f} s ago" if _is_number(ts) else "unknown time"


def status_lines(values: Mapping[str, Any], *, now: float) -> list[str]:
    """The CLI's report of the persisted link state: names and states only, never a secret."""
    stop = values.get(KV_DESKTOP_STOP)
    state = values.get(KV_NODE_STATE)
    power = values.get(KV_NODE_POWER)
    return [
        f"desktop_link: {_link_value(values.get(KV_DESKTOP_LINK))}",
        ("desktop_stop: pending since " + _age(stop.get("ts"), now) + f" (by {stop.get('by', '?')})"
         if isinstance(stop, dict) else "desktop_stop: none"),
        f"active_job: {values.get(KV_ACTIVE_JOB) or 'none'}",
        f"node_id: {values.get(KV_NODE_ID) or 'none'}",
        (f"node_state: mode={state.get('mode')} locked={state.get('locked')} panic={state.get('panic')} "
         f"(changed {_age(state.get('ts'), now)})" if isinstance(state, dict) else "node_state: unknown"),
        (f"node_power: {power.get('state')} ({_age(power.get('ts'), now)})" if isinstance(power, dict)
         else "node_power: unknown"),
        ("last_owner_activity: " + _age(values.get(KV_LAST_ACTIVITY_TS), now)
         if _is_number(values.get(KV_LAST_ACTIVITY_TS)) else "last_owner_activity: never"),
    ]


async def _cli_link(path: str, action: str) -> dict[str, Any]:
    store = BridgeStore(path, repair=False)  # only the bridge itself may rename a damaged database
    try:
        if action in (LINK_ON, LINK_OFF):
            await store.kv_set(KV_DESKTOP_LINK, action)
            await store.audit("admin_cli", "desktop_link", {"state": action})
        return await store.kv_get_many((KV_DESKTOP_LINK, KV_DESKTOP_STOP, KV_ACTIVE_JOB, KV_NODE_ID, KV_NODE_STATE,
                                        KV_NODE_POWER, KV_LAST_ACTIVITY_TS))
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.core.bridge_desktop",
                                     description="Desktop Link admin tools of the UI bridge (plan task C-116).")
    commands = parser.add_subparsers(dest="command", required=True)
    link = commands.add_parser("link", help="the persisted Desktop Link switch (K-76: a stop that survives restarts)")
    link.add_argument("action", choices=(LINK_ON, LINK_OFF, "status"))
    link.add_argument("--db", default=None,
                      help="bridge database (default: BRIDGE_DB_PATH or <project>/data/bridge.db)")
    args = parser.parse_args(argv)

    path = args.db or bridge_db_path()
    if not os.path.isfile(path):
        print(f"no bridge database at {path}", file=sys.stderr)
        return 1
    try:
        values = asyncio.run(_cli_link(path, args.action))
    except Exception as exc:  # pylint: disable=broad-exception-caught  # report and exit, never a traceback
        print(f"cannot use {path}: {type(exc).__name__}", file=sys.stderr)
        return 2
    for line in status_lines(values, now=time.time()):
        print(line)
    if args.action != "status":
        print(f"the running bridge applies it within {LINK_POLL_S:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
