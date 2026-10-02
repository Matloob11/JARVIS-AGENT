"""Desktop Link client of the Desktop Node (plan task C-120 step 4; contracts K-70 v5, K-74 v5).

The node's end of the '/desktop' Socket.IO link to the bridge, built on services/utils/desktop_protocol.py
(C-115):
- Transport: wss to JARVIS_LINK_URL (https only, TLS verified), socketio_path 'vortex/socket.io', websocket
  only, reconnection=False with its own loop: backoff RECONNECT_FIRST_S -> RECONNECT_MAX_S plus up to
  RECONNECT_JITTER_S, forever, and a fresh signed hello (new session nonce, new X25519 key) on every connect.
  Socket.IO mapping (the C-116 bridge's wire format, src/core/bridge_desktop.py): the connect auth is
  {"hello": <the signed hello as JSON text>}, so a socket exists only once its hello verified; then every
  envelope travels as the event LINK_EVENT ('msg') on LINK_NAMESPACE, and the bridge's unsigned hints arrive as
  'hint'. Received events of any name are accepted (catch-all): a hint is told apart by its content.
- One Receiver and one ClockEstimator for the life of the process, across reconnects. The node pings the agent
  peers every PING_S and right after each handshake and registers each ping with ClockEstimator.ping_sent();
  that is all: the Receiver hands every pong it verifies (also outside the window) to the estimator, and an
  agent_hello that passed only on the raw clock makes it re-measure (accept_agent_hello).
- A desktop_state notice to 'bridge' after every hello, on every change and every HEARTBEAT_S as the presence
  heartbeat, in every run mode and desktop mode (K-74 v5). Alert notices fold repeats of a code within
  ALERT_FOLD_S into one with count.
- Power (WM_POWERBROADCAST, WM_ENDSESSION; PowerListener): suspend sends power:suspend and then nothing until
  the wake; resume sends power:resume if the socket survived (a short sleep), else reconnects at once with a
  new hello, and pings at once to refresh the offset; shutdown and logoff are the last message.
- Events (sealed to one agent peer): location and log_batch to the svc peer, window_context and
  clipboard_suggestion to the job peer of the owner room; none fits: dropped. The current location is sent
  again after each svc agent_hello, the current window_context after each owner-room agent_hello.
- A link refusal (an exec that failed a receiver check) is only offered to the handler when
  DesktopLinkError.refusal_code allows one; the node checks its WAL before it signs it.
The node (node.py) implements LinkHandler: calls, controls, hints, peers and its hello fields.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from services.utils import desktop_protocol as dp
from services.utils.desktop_protocol import DesktopLinkError, VerifiedEnvelope

logger = logging.getLogger("JARVIS-DESKTOP.link")

LINK_NAMESPACE = "/desktop"
LINK_EVENT = "msg"
AUTH_HELLO = "hello"
RECONNECT_FIRST_S = 1.0
RECONNECT_MAX_S = 30.0
RECONNECT_JITTER_S = 1.0
STABLE_CONNECTION_S = 60.0
CONNECT_TIMEOUT_S = 10.0
SEND_TIMEOUT_S = 10.0
HEARTBEAT_S = 10.0
PING_S = 30.0
ALERT_FOLD_S = 60.0
REJECT_LOG_FOLD_S = 60.0
INBOX_MAX = 1000
POWER_SEND_TIMEOUT_S = 1.5
SHORT_SUSPEND_S = 30.0  # longer, and the bridge's Engine.IO has dropped the socket: reconnect at once
EVENTS_TO_SVC = frozenset({"location", "log_batch"})
EVENTS_TO_OWNER = frozenset({"window_context", "clipboard_suggestion"})
RESENT_EVENTS = {"location": EVENTS_TO_SVC, "window_context": EVENTS_TO_OWNER}
ISOLATED_ROOM_RE = re.compile(r"^jarvis-(?:probe|canary)-\d+$")  # K-57: never the owner room
_IDENT_RE = re.compile(r"(?:agent|pc):[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
POWER_STATES = dp.POWER_STATES
STATE_CONNECTED, STATE_DISCONNECTED = "connected", "disconnected"
STATE_SUSPENDED, STATE_RESUMED, STATE_ENDED = "suspended", "resumed", "ended"


# --- transport -----------------------------------------------------------------------------------------------

Receive = Callable[[Any], Awaitable[None]]


class LinkTransport(Protocol):
    """One connection to the bridge's '/desktop' namespace."""

    @property
    def connected(self) -> bool:
        """True while the connection is up."""

    def set_receiver(self, receive: Receive) -> None:
        """receive(payload) gets every event payload from the bridge."""

    async def open(self, auth: Mapping[str, Any]) -> None:
        """Connects (raises on failure)."""

    async def send(self, payload: str) -> None:
        """Sends one envelope (canonical JSON text)."""

    async def closed(self) -> None:
        """Returns once the connection has ended."""

    async def close(self) -> None:
        """Ends the connection; never raises."""


class SocketIOTransport:
    """python-socketio AsyncClient on LINK_NAMESPACE (websocket only, reconnection off, TLS verified)."""

    def __init__(self, url: str, socketio_path: str, *, namespace: str = LINK_NAMESPACE, event: str = LINK_EVENT,
                 connect_timeout_s: float = CONNECT_TIMEOUT_S) -> None:
        self._url, self._path, self._ns, self._event = url, socketio_path, namespace, event
        self._timeout = connect_timeout_s
        self._receive: Receive | None = None
        self._sio: Any = None
        self._ended = asyncio.Event()

    @property
    def connected(self) -> bool:
        sio = self._sio
        return sio is not None and bool(sio.connected) and not self._ended.is_set()

    def set_receiver(self, receive: Receive) -> None:
        self._receive = receive

    async def _on_event(self, _event: str, *args: Any) -> None:
        if self._receive is not None and args:
            await self._receive(args[0])

    async def _on_disconnect(self, *_args: Any) -> None:
        self._ended.set()

    async def open(self, auth: Mapping[str, Any]) -> None:
        import socketio  # pylint: disable=import-outside-toplevel  # loaded by the node only

        self._ended.clear()
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False, ssl_verify=True)
        sio.on("*", self._on_event, namespace=self._ns)
        sio.on("disconnect", self._on_disconnect, namespace=self._ns)
        self._sio = sio
        await sio.connect(self._url, auth=dict(auth), transports=["websocket"], namespaces=[self._ns],
                          socketio_path=self._path, wait_timeout=self._timeout)

    async def send(self, payload: str) -> None:
        if self._sio is None:
            raise ConnectionError("not connected")
        await self._sio.emit(self._event, payload, namespace=self._ns)

    async def closed(self) -> None:
        sio = self._sio
        if sio is None:
            return
        waiter = asyncio.ensure_future(sio.wait())
        ended = asyncio.ensure_future(self._ended.wait())
        try:
            await asyncio.wait({waiter, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiter, ended):
                task.cancel()

    async def close(self) -> None:
        sio, self._sio = self._sio, None
        self._ended.set()
        if sio is None:
            return
        try:
            await asyncio.wait_for(sio.disconnect(), timeout=5.0)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # closing never fails the caller
            logger.debug("Desktop Link close: %s", type(exc).__name__)


# --- peers and the handler -----------------------------------------------------------------------------------

@dataclass
class AgentPeer:
    """An agent peer with a session in the node's current session (from its verified agent_hello)."""

    ident: str
    job_id: str
    room: str
    session: dp.PeerSession = field(repr=False)
    since: float = 0.0

    @property
    def peer_id(self) -> str:
        return self.ident.partition(":")[2]

    @property
    def svc(self) -> bool:
        return self.job_id == "svc"

    @property
    def key(self) -> dp.SessionKey:
        return self.session.key


@dataclass(frozen=True)
class CallRef:
    """The header fields a call's ack, progress and result repeat (call_id, tool, room, origin)."""

    call_id: str
    tool: str
    room: str
    origin: str

    @classmethod
    def of(cls, header: Mapping[str, Any]) -> CallRef:
        return cls(str(header.get("call_id", "")), str(header.get("tool", "")), str(header.get("room", "")),
                   str(header.get("origin", "internal")))


class LinkHandler(Protocol):
    """What the node supplies to the link."""

    async def hello_fields(self, report_limit: int) -> dict[str, Any]:
        """The node's hello fields besides the handshake (K-70 hello)."""

    def desktop_state(self) -> dict[str, Any]:
        """{mode, locked, panic} for the desktop_state notice."""

    async def handle_exec(self, env: VerifiedEnvelope, body: dict[str, Any], peer: AgentPeer) -> None:
        """Must register the call at once and return: the judgment and the run go on their own task."""

    async def handle_cancel(self, env: VerifiedEnvelope, body: dict[str, Any], peer: AgentPeer) -> None: ...

    async def handle_result_ack(self, call_id: str, peer_ident: str) -> None: ...

    async def handle_control(self, kind: str, body: Mapping[str, Any], source: str) -> None: ...

    async def handle_sync_request(self, peer: AgentPeer) -> None: ...

    async def handle_peer(self, peer: AgentPeer) -> None: ...

    async def handle_hint(self, hint: dp.BridgeHint) -> None: ...

    async def handle_link_refusal(self, header: Mapping[str, Any], error: DesktopLinkError) -> None: ...

    def link_changed(self, state: str) -> None: ...


@dataclass
class _AlertSlot:
    window_start: float
    pending: int = 0
    fields: dict[str, Any] = field(default_factory=dict)
    flush: asyncio.TimerHandle | None = None


class DesktopLink:
    """The node's link (see the module docstring). transport_factory() builds a LinkTransport per connect."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods  # one link, one place

    def __init__(self, *, transport_factory: Callable[[], LinkTransport], signer: dp.LinkSigner,
                 receiver: dp.Receiver, clock: dp.ClockEstimator, handler: LinkHandler, owner_room: str,
                 heartbeat_s: float = HEARTBEAT_S, ping_s: float = PING_S, alert_fold_s: float = ALERT_FOLD_S,
                 reconnect_first_s: float = RECONNECT_FIRST_S, reconnect_max_s: float = RECONNECT_MAX_S,
                 jitter_s: float = RECONNECT_JITTER_S, short_suspend_s: float = SHORT_SUSPEND_S,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 rng: Callable[[], float] = random.random) -> None:
        if receiver.clock is not clock:
            raise ValueError("the Receiver must use the link's ClockEstimator (one per process)")
        self._factory, self._signer, self._receiver, self.clock = transport_factory, signer, receiver, clock
        self._handler, self.owner_room = handler, owner_room
        self.node_id = signer.ident.partition(":")[2]
        self._heartbeat_s, self._ping_s, self._alert_fold_s = heartbeat_s, ping_s, alert_fold_s
        self._first, self._max, self._jitter, self._short_suspend = (reconnect_first_s, reconnect_max_s, jitter_s,
                                                                      short_suspend_s)
        self._wall, self._mono, self._rng = wall, mono, rng
        self.loop: asyncio.AbstractEventLoop | None = None  # the link loop (run(), or the node before it)
        self.sess: str | None = None
        self._eph: Any = None
        self._transport: LinkTransport | None = None
        self.peers: dict[str, AgentPeer] = {}
        self._timers: list[asyncio.Task[None]] = []
        self._inbox: asyncio.Queue[tuple[Any, float]] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._wake = asyncio.Event()
        self._reconnect_now = False
        self.quiet: str | None = None  # STATE_SUSPENDED or STATE_ENDED: nothing is sent
        self._suspended_at: tuple[float, float] | None = None
        self.connected_since: float | None = None
        self.connects = 0
        self._current: dict[str, dict[str, Any]] = {}
        self._alerts: dict[str, _AlertSlot] = {}
        self._rejects: dict[tuple[str, str], tuple[float, int]] = {}
        self.sent: dict[str, int] = {}
        self.last_error = ""

    # --- state -----------------------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        transport = self._transport
        return transport is not None and transport.connected and self.sess is not None

    def status(self) -> str:
        if self.quiet:
            return self.quiet
        return STATE_CONNECTED if self.connected else ("connecting" if not self._stopping else "stopped")

    # --- the connection loop ---------------------------------------------------------------------------------

    async def run(self) -> None:
        """Connects and reconnects until stop(); never returns otherwise."""
        self.loop = asyncio.get_running_loop()
        delay = self._first
        failures = 0
        while not self._stopping:
            if self.quiet:  # suspended: nothing until the wake; ended (shutdown, logoff): nothing at all
                self._wake.clear()
                await self._wake.wait()
                continue
            connected_at: float | None = None
            try:
                await self._connect_once()
                connected_at = self._mono()
                failures = 0
                transport = self._transport
                if transport is not None:
                    await transport.closed()
                if not self._stopping:
                    logger.warning("Desktop Link lost; reconnecting")
            except asyncio.CancelledError:
                await self._teardown()
                raise
            except Exception as exc:  # pylint: disable=broad-exception-caught  # retried forever
                failures += 1
                self.last_error = type(exc).__name__
                if failures == 1 or failures % 10 == 0:
                    logger.warning("Desktop Link connect failed (attempt %d, %s); retrying", failures,
                                   type(exc).__name__)
            await self._teardown()
            if self._stopping:
                break
            if connected_at is not None and self._mono() - connected_at >= STABLE_CONNECTION_S:
                delay = self._first
            wait = 0.0 if self._reconnect_now else delay + self._rng() * self._jitter
            self._reconnect_now = False
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except TimeoutError:
                pass
            delay = min(delay * 2, self._max)

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        transport = self._transport
        if transport is not None:
            await transport.close()

    def reconnect_now(self) -> None:
        """Drops the connection (if any) and connects again without the backoff wait."""
        self._reconnect_now = True
        self._wake.set()
        transport = self._transport
        if transport is not None:
            self._spawn(transport.close())

    async def _connect_once(self) -> None:
        self.sess, self._eph = dp.new_session_nonce(), dp.new_ephemeral_key()
        self._receiver.bind(self.sess)
        self.peers.clear()
        # One consumer per connection keeps the bridge's order (a cancel right behind its exec, pings and
        # pongs), whatever task the transport hands each message on.
        inbox: asyncio.Queue[tuple[Any, float]] = asyncio.Queue(maxsize=INBOX_MAX)
        self._inbox = inbox
        self._timers = [asyncio.create_task(self._consume(inbox), name="dl-inbox")]
        hello = await self._build_hello("agent:*")
        transport = self._factory()
        transport.set_receiver(self._on_payload)
        self._transport = transport
        # The hello travels in the connect auth: the bridge verifies it before the socket exists and relays it.
        await asyncio.wait_for(transport.open({AUTH_HELLO: dp.canonical_json(hello).decode("utf-8")}),
                               timeout=CONNECT_TIMEOUT_S + 5.0)
        self.sent["hello"] = self.sent.get("hello", 0) + 1
        self.connected_since = self._mono()
        self.connects += 1
        logger.info("Desktop Link connected (connect #%d)", self.connects)
        self._handler.link_changed(STATE_CONNECTED)
        await self.send_state()
        await self._flush_alerts()
        self._timers += [asyncio.create_task(self._every(self._heartbeat_s, self.send_state), name="dl-heartbeat"),
                         asyncio.create_task(self._every(self._ping_s, self.ping), name="dl-ping")]

    async def _teardown(self) -> None:
        timers, self._timers = self._timers, []
        self._inbox = None
        for timer in timers:
            timer.cancel()
        transport, self._transport = self._transport, None
        was_connected = self.connected_since is not None
        self.connected_since = None
        self.peers.clear()
        if transport is not None:
            await transport.close()
        if was_connected:
            self._handler.link_changed(STATE_DISCONNECTED)

    async def _every(self, interval: float, action: Callable[[], Awaitable[Any]]) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await action()
            except Exception:  # pylint: disable=broad-exception-caught  # a timer never dies
                logger.exception("Desktop Link timer %s failed", getattr(action, "__name__", "?"))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any] | None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # no loop in this thread (before run()): nothing to run it on
            coro.close()
            return None
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _on_loop(self, callback: Callable[..., Any], *args: Any) -> None:
        """Runs callback(*args) on the link loop: at once when called there, else handed over thread-safely
        (C-121's tray, panic hotkey and session handlers run on their own threads). Before the loop is known it
        runs at once, and what needs the loop waits for the next connect."""
        loop = self.loop
        if loop is not None and not loop.is_closed():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                try:
                    loop.call_soon_threadsafe(callback, *args)
                except RuntimeError:  # the loop closed meanwhile: the node is stopping
                    pass
                return
        callback(*args)

    # --- sending ---------------------------------------------------------------------------------------------

    async def _emit(self, env: Mapping[str, Any], *, force: bool = False) -> bool:
        transport = self._transport
        if transport is None or not transport.connected or (self.quiet and not force):
            return False
        try:
            await asyncio.wait_for(transport.send(dp.canonical_json(dict(env)).decode("utf-8")),
                                   timeout=SEND_TIMEOUT_S)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the reconnect loop takes over
            logger.warning("Desktop Link send of %s failed (%s)", env.get("typ"), type(exc).__name__)
            return False
        self.sent[str(env.get("typ"))] = self.sent.get(str(env.get("typ")), 0) + 1
        return True

    async def _build_hello(self, to: str) -> dict[str, Any]:
        """A signed hello with this connection's session nonce and X25519 key; the wal_report is shortened
        until the hello fits its size cap."""
        if self.sess is None:
            raise RuntimeError("no Desktop Link session")
        for limit in (300, 100, 25):
            body = dp.node_hello_body(node_id=self.node_id, eph=self._eph, sess=self.sess,
                                      fields=await self._handler.hello_fields(limit))
            try:
                return self._signer.build("hello", body, to=to, sess=self.sess)
            except DesktopLinkError as exc:
                if exc.reason != "size":
                    raise
        body = dp.node_hello_body(node_id=self.node_id, eph=self._eph, sess=self.sess,
                                  fields=await self._handler.hello_fields(0))
        return self._signer.build("hello", body, to=to, sess=self.sess)

    async def send_hello(self, to: str) -> bool:
        """The current session's hello again, to one agent peer that has not seen it (a later peer); like every
        hello, it is followed by a desktop_state notice (K-74)."""
        if self.sess is None or not self.connected or self.quiet:
            return False
        sent = await self._emit(await self._build_hello(to))
        if sent:
            await self.send_state()
        return sent

    async def send_notice(self, kind: str, *, force: bool = False, **fields: Any) -> bool:
        """A notice to 'bridge' (K-74 field lists; a bad field is logged and nothing is sent)."""
        if self.sess is None:
            return False
        try:
            env = self._signer.build("notice", {"kind": kind, **fields}, to=dp.BRIDGE_IDENT, sess=self.sess)
        except DesktopLinkError as exc:
            logger.error("Notice %s not sent: %s", kind, exc.reason)
            return False
        return await self._emit(env, force=force)

    async def send_state(self) -> bool:
        """The desktop_state notice (after every hello, on every change, every HEARTBEAT_S)."""
        return await self.send_notice("desktop_state", **self._handler.desktop_state())

    def state_changed(self) -> None:
        """Mode, lock or panic changed: tell the bridge now. Thread-safe."""
        self._on_loop(self._send_state_soon)

    def _send_state_soon(self) -> None:
        if self.connected and not self.quiet:
            self._spawn(self.send_state())

    async def ping(self, to: str = "agent:*") -> bool:
        if self.sess is None or not self.connected or self.quiet:
            return False
        env = self._signer.build("ping", {}, to=to, sess=self.sess)
        self.clock.ping_sent(env)
        return await self._emit(env)

    def peer(self, ident: str) -> AgentPeer | None:
        return self.peers.get(ident)

    async def send_to_peer(self, typ: str, body: Mapping[str, Any], *, peer_ident: str, ref: CallRef) -> bool:
        """ack, progress or result for a call; False when that peer has no session now (the outbox keeps a
        result for result_resend). A body that cannot be built raises DesktopLinkError (BAD_ARGS)."""
        peer = self.peers.get(peer_ident)
        if peer is None or self.sess is None or not self.connected or self.quiet:
            return False
        env = self._signer.build(typ, dict(body), to=peer.ident, sess=self.sess, room=ref.room, origin=ref.origin,
                                 session_key=peer.key, call_id=ref.call_id, tool=ref.tool)
        return await self._emit(env)

    async def send_result_resend(self, record: dp.ResultRecord, peer: AgentPeer) -> bool:
        if self.sess is None or not self.connected or self.quiet:
            return False
        env = dp.build_result_resend(self._signer, record, to=peer.ident, session_key=peer.key)
        return await self._emit(env)

    def event_peer(self, kind: str) -> AgentPeer | None:
        """The peer an event kind goes to: the svc peer, or the newest job peer of the owner room."""
        if kind in EVENTS_TO_SVC:
            candidates = [peer for peer in self.peers.values() if peer.svc]
        elif kind in EVENTS_TO_OWNER:
            candidates = [peer for peer in self.peers.values() if not peer.svc and peer.room == self.owner_room
                          and not ISOLATED_ROOM_RE.match(peer.room)]
        else:
            return None
        return max(candidates, key=lambda peer: peer.since, default=None)

    async def send_event(self, kind: str, body: Mapping[str, Any]) -> bool:
        """An encrypted event to the one peer that fits; dropped (False) when none does."""
        peer = self.event_peer(kind)
        if peer is None or self.sess is None or not self.connected or self.quiet:
            return False
        try:
            env = self._signer.build("event", {**dict(body), "kind": kind}, to=peer.ident, sess=self.sess,
                                     room=peer.room, session_key=peer.key)
        except DesktopLinkError as exc:
            logger.error("Event %s not sent: %s", kind, exc.reason)
            return False
        return await self._emit(env)

    async def set_current(self, kind: str, body: Mapping[str, Any]) -> bool:
        """Location or window_context: kept, sent now, and sent again after the next fitting agent_hello."""
        if kind not in RESENT_EVENTS:
            raise ValueError(f"{kind} is not re-sent after a hello")
        self._current[kind] = dict(body)
        return await self.send_event(kind, body)

    # --- alerts ----------------------------------------------------------------------------------------------

    def raise_alert(self, code: str, **fields: Any) -> None:
        """A K-74 alert notice (level = ALERT_CODES[code]). The first of a code goes out at once; its repeats
        within ALERT_FOLD_S fold into one notice with count when that window ends. Unsent ones (no link) wait
        for the next connect. Never put arguments, paths, titles or clipboard text in message (K-74).
        Thread-safe."""
        if code not in dp.ALERT_CODES:
            raise ValueError(f"unknown alert code {code}")
        clean = {name: value for name, value in fields.items() if value is not None}
        self._on_loop(self._raise_alert_now, code, clean)

    def _raise_alert_now(self, code: str, clean: dict[str, Any]) -> None:
        now = self._mono()
        slot = self._alerts.get(code)
        if slot is not None and now - slot.window_start < self._alert_fold_s:
            slot.pending += 1
            slot.fields = clean
            return
        if slot is not None and slot.flush is not None:
            slot.flush.cancel()
        self._alerts[code] = _AlertSlot(window_start=now, pending=1, fields=clean)
        self._spawn(self._send_pending_alert(code))
        self._schedule_window_end(code)

    def _schedule_window_end(self, code: str) -> None:
        slot = self._alerts.get(code)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if slot is not None:
            slot.flush = loop.call_later(self._alert_fold_s, self._window_end, code)

    def _window_end(self, code: str) -> None:
        slot = self._alerts.get(code)
        if slot is None:
            return
        if not slot.pending:
            del self._alerts[code]
            return
        slot.window_start = self._mono()
        self._spawn(self._send_pending_alert(code))
        self._schedule_window_end(code)

    async def _send_pending_alert(self, code: str) -> bool:
        slot = self._alerts.get(code)
        if slot is None or not slot.pending:
            return False
        count, body = slot.pending, dict(slot.fields)
        if count > 1:
            body["count"] = min(count, 10 ** 6)
        sent = await self.send_notice("alert", code=code, level=dp.ALERT_CODES[code], **body)
        if sent:
            slot.pending = max(0, slot.pending - count)
        return sent

    async def _flush_alerts(self) -> None:
        """After a hello: alerts that could not be sent while the link was down."""
        for code in list(self._alerts):
            await self._send_pending_alert(code)

    # --- receiving -------------------------------------------------------------------------------------------

    async def _on_payload(self, payload: Any) -> None:
        """The transport's callback: queues the message with its receive time (a pong's t1)."""
        inbox = self._inbox
        if inbox is None:
            return
        try:
            inbox.put_nowait((payload, self._wall()))
        except asyncio.QueueFull:
            self._log_reject("inbox_full", "DL_REJECT")

    async def _consume(self, inbox: asyncio.Queue[tuple[Any, float]]) -> None:
        while True:
            payload, received_at = await inbox.get()
            try:
                await self._receive(payload, received_at)
            except Exception:  # pylint: disable=broad-exception-caught  # one bad message never ends the link
                logger.exception("Desktop Link message handling failed")

    async def _receive(self, payload: Any, received_at: float) -> None:
        if self.quiet == STATE_ENDED:
            return
        if isinstance(payload, (bytes, bytearray)):
            payload = bytes(payload).decode("utf-8", errors="replace")
        if isinstance(payload, str) and len(payload) > dp.MAX_ENVELOPE_BYTES:
            self._log_reject("size", "DL_REJECT")
            return
        try:
            parsed = dp.loads_strict(payload) if isinstance(payload, str) else payload
        except dp.CanonicalJsonError:
            self._log_reject("format", "DL_REJECT")
            return
        if not isinstance(parsed, Mapping):
            self._log_reject("format", "DL_REJECT")
            return
        if parsed.get("from") == dp.BRIDGE_IDENT and "sig" not in parsed:
            await self._on_hint(payload)
            return
        try:
            env = await asyncio.to_thread(self._receiver.verify, payload)
        except DesktopLinkError as err:
            await self._rejected(parsed, err)
            return
        await self._dispatch(env, received_at)

    async def _on_hint(self, payload: Any) -> None:
        try:
            hint = dp.parse_bridge_hint(payload)
        except DesktopLinkError as err:
            self._log_reject(err.reason, err.code)
            return
        if hint.typ == "control":
            await self._handler.handle_control(str(hint.body.get("kind")), hint.body, dp.BRIDGE_IDENT)
        elif hint.typ in ("welcome", "status"):
            await self._handler.handle_hint(hint)

    def _log_reject(self, reason: str, code: str) -> None:
        """DL_REJECT with reason and code only; repeats of one reason within REJECT_LOG_FOLD_S are counted."""
        now = self._mono()
        first, count = self._rejects.get((reason, code), (now, 0))
        if count == 0 or now - first >= REJECT_LOG_FOLD_S:
            logger.warning("DL_REJECT reason=%s code=%s%s", reason, code,
                           f" (+{count - 1} more in {REJECT_LOG_FOLD_S:.0f} s)" if count > 1 else "")
            self._rejects[(reason, code)] = (now, 1)
        else:
            self._rejects[(reason, code)] = (first, count + 1)

    async def _rejected(self, header: Mapping[str, Any], err: DesktopLinkError) -> None:
        self._log_reject(err.reason, err.code)
        sender = header.get("from")
        if err.reason == "signature":  # K-74 critical: the relay passed a message the agent never signed
            self.raise_alert("SIGNATURE_INVALID",
                             peer=sender if isinstance(sender, str) and _IDENT_RE.fullmatch(sender) else None)
        if err.refusal_code is not None and header.get("typ") == "exec":
            await self._handler.handle_link_refusal(header, err)

    async def _dispatch(self, env: VerifiedEnvelope, received_at: float) -> None:
        typ, sender = env.typ, env.sender
        if typ == "agent_hello":
            await self._on_agent_hello(env)
            return
        if typ == "ping":
            pong = self._signer.build("pong", dp.pong_body(env, received_at=received_at), to=sender, sess=self.sess)
            await self._emit(pong)
            return
        if typ == "pong":
            return  # the Receiver gave it to the ClockEstimator
        if typ == "control":
            await self._handler.handle_control(str(env.body.get("kind")), env.body, sender)
            return
        peer = self.peers.get(sender)
        if peer is None:
            logger.info("Desktop Link: %s from a peer without a session in this one; dropped", typ)
            return
        if typ == "result_ack":
            await self._handler.handle_result_ack(str(env.call_id), sender)
        elif typ == "sync_request":
            await self._handler.handle_sync_request(peer)
        elif typ in ("exec", "cancel"):
            try:
                body = dp.open_body(env, peer.key)
            except DesktopLinkError as err:
                await self._rejected(dict(env.header), err)
                return
            if typ == "exec":  # handle_exec registers the call and returns; its judgment runs on its own task
                await self._handler.handle_exec(env, body, peer)
            else:
                await self._handler.handle_cancel(env, body, peer)

    async def _on_agent_hello(self, env: VerifiedEnvelope) -> None:
        sender = env.sender
        named_session = "sess" in env.body or "sess" in env.header
        try:
            session = dp.accept_agent_hello(env, own_eph=self._eph, sess=str(self.sess), node_id=self.node_id)
        except DesktopLinkError as err:
            self._log_reject(err.reason, err.code)
            if err.reason == "session":  # it knows an older session of ours: tell it the current one
                await self.send_hello(to=sender)
            return
        job_id = env.body.get("job_id")
        peer = AgentPeer(ident=sender, job_id=job_id if isinstance(job_id, str) else "", room=str(env.header["room"]),
                         session=session, since=self._mono())
        self.peers[sender] = peer
        logger.info("Desktop Link peer %s (%s) in room %s", sender, "svc" if peer.svc else "job", peer.room or "-")
        if not named_session:  # it has not seen our hello: send it, so it can derive the same key
            await self.send_hello(to=sender)
        await self.ping(sender)
        for kind, kinds in RESENT_EVENTS.items():
            if kind in self._current and self.event_peer(next(iter(kinds))) is peer:
                await self.send_event(kind, self._current[kind])
        await self._handler.handle_peer(peer)

    # --- power -----------------------------------------------------------------------------------------------

    async def on_power(self, state: str) -> None:
        """WM_POWERBROADCAST / WM_ENDSESSION (PowerListener): see the module docstring."""
        if state not in POWER_STATES or self.quiet == STATE_ENDED:
            return
        if state == "suspend":
            if self.quiet == STATE_SUSPENDED:
                return
            await self._power_notice("suspend")
            self.quiet = STATE_SUSPENDED
            self._suspended_at = (self._mono(), self._wall())
            self._handler.link_changed(STATE_SUSPENDED)
        elif state == "resume":
            if self.quiet != STATE_SUSPENDED:
                return
            mono0, wall0 = self._suspended_at or (self._mono(), self._wall())
            slept = max(self._mono() - mono0, self._wall() - wall0)
            self.quiet, self._suspended_at = None, None
            self._handler.link_changed(STATE_RESUMED)
            if self.connected and slept < self._short_suspend:
                await self.send_notice("power", state="resume")
                await self.send_state()
                await self.ping()
            else:
                logger.info("Desktop Link: resumed after %.0f s; reconnecting at once", slept)
                self.reconnect_now()  # also wakes a loop that waited for the resume
        else:  # shutdown or logoff: the last message of this connection
            await self._power_notice(state)
            self.quiet = STATE_ENDED
            self._handler.link_changed(STATE_ENDED)

    async def _power_notice(self, state: str) -> None:
        if not self.connected or self.quiet:
            return
        try:
            await asyncio.wait_for(self.send_notice("power", state=state, force=True), timeout=POWER_SEND_TIMEOUT_S)
        except TimeoutError:
            logger.warning("Desktop Link: the power %s notice did not leave in time", state)


# --- Windows power and session notifications -------------------------------------------------------------------

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
ENDSESSION_LOGOFF = 0x80000000
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002


class PowerListener:
    """A hidden (never shown) top-level window on its own thread that receives the broadcasts
    WM_POWERBROADCAST and WM_QUERYENDSESSION/WM_ENDSESSION, which a message-only window would miss.
    on_event(state) gets 'suspend', 'resume', 'shutdown' or 'logoff' on that thread; it may block briefly so a
    notice leaves before Windows sleeps. add_handler(msg, fn) lets other node parts (C-121 session
    notifications through hwnd) use the same window. Windows only; start() is a no-op elsewhere."""

    def __init__(self, on_event: Callable[[str], None]) -> None:
        self._on_event = on_event
        self._handlers: dict[int, Callable[[int, int], int | None]] = {}
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.hwnd: int | None = None
        self._wndproc: Any = None

    def add_handler(self, msg: int, handler: Callable[[int, int], int | None]) -> None:
        self._handlers[msg] = handler

    def handle_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        """The window procedure's own part; None hands the message to DefWindowProc."""
        if msg == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                self._event("suspend")
                return 1
            if wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                self._event("resume")
                return 1
            return None
        if msg == WM_QUERYENDSESSION:
            return 1  # never blocks the end of the session
        if msg == WM_ENDSESSION:
            if wparam:
                self._event("logoff" if (lparam & ENDSESSION_LOGOFF) else "shutdown")
            return 0
        handler = self._handlers.get(msg)
        return handler(wparam, lparam) if handler is not None else None

    def _event(self, state: str) -> None:
        try:
            self._on_event(state)
        except Exception:  # pylint: disable=broad-exception-caught  # never raise into the window procedure
            logger.exception("Power event %s handling failed", state)

    def start(self) -> bool:
        if sys.platform != "win32" or self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._run, name="desktop-power", daemon=True)
        self._thread.start()
        self._ready.wait(10.0)
        return self.hwnd is not None

    def stop(self) -> None:
        if self.hwnd is not None and sys.platform == "win32":
            import ctypes  # pylint: disable=import-outside-toplevel

            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)  # type: ignore[attr-defined]
        if self._thread is not None:
            self._thread.join(5.0)

    def _run(self) -> None:  # pragma: no cover - needs a Windows desktop session
        # pylint: disable=import-outside-toplevel,too-many-locals
        import ctypes
        from ctypes import wintypes

        lresult = ctypes.c_ssize_t
        wndproc_type = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):  # pylint: disable=invalid-name,too-few-public-methods
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", wndproc_type), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = lresult
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = lresult
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def wndproc(hwnd: Any, msg: int, wparam: int, lparam: int) -> int:
            try:
                result = self.handle_message(int(msg), int(wparam or 0), int(lparam or 0))
            except Exception:  # pylint: disable=broad-exception-caught
                result = None
            if result is not None:
                return result
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc_type(wndproc)  # kept referenced for the window's life
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"JarvisDesktopNodePower{threading.get_ident()}"
        wndclass = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinstance, lpszClassName=class_name)
        if not user32.RegisterClassW(ctypes.byref(wndclass)):
            logger.error("Power listener: RegisterClassW failed (%d)", ctypes.get_last_error())
            self._ready.set()
            return
        hwnd = user32.CreateWindowExW(0, class_name, "JARVIS Desktop Node", 0, 0, 0, 0, 0, None, None, hinstance,
                                      None)
        if not hwnd:
            logger.error("Power listener: CreateWindowExW failed (%d)", ctypes.get_last_error())
            user32.UnregisterClassW(class_name, hinstance)
            self._ready.set()
            return
        self.hwnd = hwnd
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None
        user32.UnregisterClassW(class_name, hinstance)
