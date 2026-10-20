"""
# services/ai_core/desktop_link.py
Desktop Link client of the VPS agent peers (plan task C-119 step 1; contracts K-70 v8, K-73, K-74 v8, K-76).

One DesktopLink per process (a LiveKit job process; jarvis-agent-svc builds its own, C-125), built on the C-115
library services/utils/desktop_protocol.py, so no part of the protocol is re-implemented here:
- Transport: Socket.IO namespace '/desktop' on the bridge's internal listener (UI_BRIDGE_URL, the unpublished
  http://jarvis-bridge:5002, K-75), websocket only, reconnection=False with its own reconnect loop (1 -> 30 s plus
  up to 1 s jitter, forever) and a fresh signed agent_hello in the connect auth on every connect: the C-116 wire
  format of src/core/bridge_desktop.py ('msg' envelopes, 'hint' bridge hints), through the node's own
  SocketIOTransport. The link runs on its own event loop in a daemon thread, so a busy job loop never delays an
  ack, a ping or a reconnect; every public method is thread-safe and never blocks.
- Handshake: the connect agent_hello names no session; the node answers with a hello to this peer, and the session
  key comes from that hello and the agent_hello's ephemeral key. A hello of a new node session (the node
  reconnected) gets an in-band agent_hello that names the session, with a new ephemeral key. Node sessions are never
  ordered by the node's wall clock (after a clock fix a new session's hello is signed 'before' the old one's): only a
  hello of a session this link replaced less than the K-70 freshness window ago is ignored (a relay can reorder
  hellos, and none older than the window verifies).
- One Receiver (its SeenStore conversations/desktop_seen.db, namespace = the peer id, kept across restarts) and one
  ClockEstimator for the life of the process, across reconnects; a ping right after each handshake and every 30 s,
  each registered with ClockEstimator.ping_sent(): the Receiver hands every pong it verifies to the estimator.
- Calls: a pending map call_id -> call, never sio.call. run_call() sends one exec (start_by = send time + 2.5 s on
  the agent clock) and waits for the K-70 outcome: CLOCK_SKEW at once while ClockEstimator.skew_exceeded() (before
  the offline fast path), STOPPED_BY_USER for 3 s after a stop, DESKTOP_OFFLINE without a node session or while the
  bridge's desktop_status says offline (well under 0.1 s); after the send, no ack within 3 s sends a cancel and
  waits a 2 s grace; an explicit refusal coded DESKTOP_REJECTED or START_WINDOW_MISSED, a never_started or
  start_window_missed WAL report, a not_started answer to that cancel, or dl_undeliverable {emitted: false} is
  DESKTOP_NOT_STARTED; every other refusal code (CLOCK_SKEW and the K-76 codes, APPROVAL_REQUIRED included) is
  returned as it is; 30 s without progress (or the K-70 proxy cap) sends a cancel and ends DESKTOP_TIMEOUT; every
  other loss is DESKTOP_RESULT_UNKNOWN. So is a node result of a call that started and ended early (any status but
  completed, with effect 'possible': the node's own timeout, a tool that raised, typing stopped after a chunk went
  out, the node's answer to this side's cancel after a late ack): part of it may have happened, so neither the model
  nor Sir is ever told it failed (Sir would ask again, and a second message would go out); the node's own status,
  code and message stay in the K-12 result's data (partial_k12). A code from the laptop (a refusal's or a result's)
  counts only in the K-12 form [A-Z0-9_]{1,64} (clean_code; a refusal with any other code is DESKTOP_REJECTED), so
  nothing the laptop sends reaches a log line or the audit unchecked. An event that lands as a wait runs out (or
  that waits in the queue when no time is left) is still handled, never dropped for 'unknown', and a result queued
  behind a stop or a link loss (it landed in the same loop step) is the call's outcome. The caller's budget_s
  (what is left of the proxy's K-11 timeout) ends the wait earlier, so the cancel and its grace finish before that
  timeout and the proxy always gets its K-12 outcome;
  a call left less than the ack window plus the grace is not sent (DESKTOP_NOT_STARTED). An unknown, timed-out or
  stopped call stays for reconciliation: a later result or result_resend, or the WAL report of the node's next
  hello, is handed to the 'late' listeners (announced, and the call's tool_sync end corrected by desktop_proxy). A
  WAL 'completed' is any end (done, failed, cancelled or timed out): without its result_resend within 15 s the call
  is handed on as 'completed', an end whose outcome is not known, never as done. No exec for a call_id is sent more
  than 1 h after its first send (first-send times are kept 24 h).
- Order (K-71): calls are sent one at a time, in the order they were submitted (run_call takes a FIFO lock first and
  keeps it until its exec went out). A call that names its LiveKit speech (CallRequest.speech_id) gets seq
  {speech_id, n} right before its exec is built: n counts the execs of that speech this link sent, and a call
  answered without a send (a fast refusal, the 1 h call_id rule, too little budget, BAD_ARGS, a failed send) gives
  its number back, so the node's SeqGate never holds the speech's next call for a number no exec carries.
- Stops: stop_all() refuses new calls for 3 s, ends every in-flight call with STOPPED_BY_USER (one whose result is
  already queued gets that result: it ended before the stop) and sends the
  agent-signed control stop_all whenever a laptop call of this peer may still run: one in flight, or one answered
  unknown, timed out or stopped that no result or WAL report has ended yet (after a link loss the node keeps a
  reconnected peer's running calls, and stop_all cancels by peer). Without a session it waits for the next
  handshake and goes before any exec of the new session (so it never ends a call made after it), and it is dropped
  when that hello's WAL report shows every such call ended. 'desktop band karo' sends desktop_stop instead, which
  stays pending until the laptop reports mode read or off (a node hello, or a status hint of an online laptop): it
  is sent while the laptop is online, else at its next hello (first in that session, and always before an exec),
  and again at every new hello until then. One said while the current session's status already shows the laptop
  online in mode read or off is done once it is sent: that mode does not change, so nothing would report the stop,
  and left pending it would go out again at the next hello and undo a later tray Full. A LiveKit job process is
  single-use, so a pending stop is also kept in conversations/desktop_stop.json (stop_path; shared by the agent
  processes like the seen store): when this process ends first, the next agent process's link loads it and sends it
  at the laptop's next hello, and a stop said while the laptop is offline (a stale session included) is never lost
  with the job. The file goes once the stop is done (it keeps the stop's wall time exactly, so a stop done in the
  millisecond it was kept clears it too). desktop_stop_persisted says whether the file holds the stop: one that
  could not be written holds only while this process runs (and Jarvis says so).
  A status hint with stop_all (after a UI desktop_stop, K-74) ends in-flight calls the same way; stop_all only
  tightens.
- sync_request after the first handshake of the process; a result_ack for every result; results are deduped by
  call_id in the SeenStore (24 h), so a result_resend after an agent restart is handed on once. A result that proves
  its call never ran (status not_started, effect none) is not recorded there, because the same call_id may still run
  after an approval (K-76: APPROVAL_REQUIRED is re-sent under it) and its real result must never look like a
  duplicate; such a result of a call this process does not know is acknowledged only (nothing ran).
- jarvis_delivery's DesktopSender (C-113): online() and submit(op, args) for pc.fetch and pc.open_url. A delivery
  kept open (unknown, timed out, or stopped while in flight: it may have opened) is reported only by its late
  outcome, never at once as a failure.
- End of a job: retire(link) stops the link for good (close(): any thread, no wait) and forgets it as the process's
  link and delivery sender. A job process drains for seconds after its job, and every job of a room is the same peer
  job-<room>, whose newest socket pushes the older one off the bridge: a retired link never reconnects, so it never
  pushes the next job's link off or acknowledges that job's results.

Laptop state comes only from the bridge's 'status' hints (agents get no notices). A hint is never authority: it only
makes a call refuse sooner (offline) or stop. Logs carry call ids, tools, codes and timings, never arguments, results
or keys.
"""
# pylint: disable=too-many-lines  # one link: handshake, calls and their outcomes, reconciliation, stops, sender

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import math
import os
import random
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from services.ai_core import tool_placement
from services.utils import desktop_protocol as dp
from services.utils.desktop_protocol import DesktopLinkError, VerifiedEnvelope
from services.utils.jarvis_filelock import locked
from services.utils.jarvis_logger import setup_logger
from src.core.desktop.link import AUTH_HELLO, LinkTransport, SocketIOTransport

logger = setup_logger("DESKTOP-LINK")

SOCKETIO_PATH = "socket.io"  # the bridge's internal listener serves Socket.IO at its default path
ENV_SIGNING_KEY = "DESKTOP_AGENT_SIGNING_KEY"  # .env.agent: the agent's Ed25519 seed as hex (K-70 Keys)
ENV_PC_PUBLIC_KEY = "DESKTOP_PC_PUBLIC_KEY"  # the node's pinned public key
SEEN_FILE = "desktop_seen.db"  # K-70 check 7: conversations/desktop_seen.db, a namespace per agent peer id
SVC_PEER_ID = "svc"
CALL_TTL_S = 3600.0  # K-70: no exec for a call_id more than 1 h after its first send
FIRST_SENT_KEEP_S = 24 * 3600.0  # first-send times outlive CALL_TTL_S (as the node's WAL call_ids), so the rule holds
UNRESOLVED_TTL_S = 3600.0  # an unknown call is reconciled within the node's 1 h WAL report window, or dropped
PROXY_MARGIN_S = 1.0  # a call ends this long before the K-70 proxy cap, so the K-11 wrapper never times it out
STOPPED_MODES = frozenset({"read", "off"})  # a laptop in one of these modes has taken desktop_stop (K-76)
STOP_FILE = "desktop_stop.json"  # conversations/desktop_stop.json: a desktop_stop the laptop has not reported yet
STOP_FILE_LOCK_S = 2.0
REPLACED_HOLD_S = dp.FRESHNESS_WINDOW_S  # a hello of a node session replaced this recently may be a reordered one
REPLACED_MAX = 16
SEQ_SPEECHES_MAX = 256  # speeches whose exec numbers (seq n) the link remembers
SPEECH_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")  # the K-70 seq speech_id format
INBOX_MAX = 1000
REJECT_LOG_FOLD_S = 60.0
MAINTENANCE_S = 1.0

# Outcome kinds of run_call().
RESULT, REFUSED, OFFLINE, NOT_STARTED, UNKNOWN, TIMEOUT, STOPPED = (
    "result", "refused", "offline", "not_started", "unknown", "timeout", "stopped")
# K-12 codes of the link (K-76 'K-12 codes added').
DESKTOP_OFFLINE, DESKTOP_NOT_STARTED, DESKTOP_RESULT_UNKNOWN = (
    "DESKTOP_OFFLINE", "DESKTOP_NOT_STARTED", "DESKTOP_RESULT_UNKNOWN")
DESKTOP_TIMEOUT, STOPPED_BY_USER, CLOCK_SKEW, BAD_ARGS = "DESKTOP_TIMEOUT", "STOPPED_BY_USER", "CLOCK_SKEW", "BAD_ARGS"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
# K-70 Refusals: the only refusal codes the agent reports as 'not executed'.
NOT_STARTED_CODES = frozenset({"DESKTOP_REJECTED", "START_WINDOW_MISSED"})
# What Jarvis says for a code (K-12: one short Roman-Urdu sentence); the node's own results carry their own.
CODE_MESSAGES: Mapping[str, str] = {
    DESKTOP_OFFLINE: "Sir, laptop abhi online nahi hai, is liye yeh kaam nahi ho saka.",
    DESKTOP_NOT_STARTED: "Sir, yeh kaam laptop par shuru hi nahi hua.",
    DESKTOP_RESULT_UNKNOWN: ("Sir, laptop se is kaam ka nateeja nahi aaya; ho sakta hai yeh ho chuka ho, is liye "
                             "main ise dobara nahi kar raha."),
    DESKTOP_TIMEOUT: "Sir, laptop par yeh kaam waqt par mukammal nahi hua.",
    STOPPED_BY_USER: "Sir, laptop ka kaam rok diya gaya.",
    CLOCK_SKEW: "Sir, laptop ki ghari ka waqt theek nahi, is liye laptop ka kaam abhi roka gaya hai.",
    BAD_ARGS: "Sir, is kaam ki maloomat laptop ko bheji nahi ja saki.",
    APPROVAL_REQUIRED: "Sir, laptop ke is kaam ke liye pehle ijazat chahiye.",
    "APPROVAL_INVALID": "Sir, ijazat is kaam se match nahi hui, is liye yeh kaam nahi kiya.",
    "MANIFEST_MISMATCH": "Sir, laptop ke tools VPS se match nahi karte, is liye yeh kaam nahi kiya.",
    "PROTO_MISMATCH": "Sir, laptop ka link version match nahi karta.",
    "DESKTOP_REJECTED": "Sir, laptop ne yeh kaam qabool nahi kiya.",
    "START_WINDOW_MISSED": "Sir, yeh kaam waqt par laptop tak nahi pohancha.",
    "DESKTOP_LOCKED": "Sir, laptop lock hai; unlock hote hi typing aur clicks khud chal jayenge.",
    "DESKTOP_READ_ONLY": "Sir, laptop abhi read-only hai; laptop ke tray se Full karne par yeh kaam ho sakega.",
    "DESKTOP_PANIC": "Sir, laptop par panic stop laga hai; tray se Full karne tak koi kaam nahi hoga.",
    "DESKTOP_CONTROL_DISABLED": "Sir, laptop par desktop control band hai.",
    "DESKTOP_RATE_LIMITED": "Sir, laptop ki hadd poori ho gayi, thori der baad dobara kahein.",
    "PRESENCE_REQUIRED": "Sir, is kaam ke liye laptop par aap ki maujoodgi zaroori hai.",
    "PROTECTED_WINDOW": "Sir, saamne wali window mehfooz hai, is liye laptop ne yeh kaam nahi kiya.",
    "SENSITIVE_SCREEN": "Sir, screen par hassas cheez khuli hai, is liye laptop ne yeh nahi kiya.",
    "DESKTOP_BUSY": "Sir, laptop par abhi doosra kaam chal raha hai, thori der baad dobara kahein.",
    "DESKTOP_DEGRADED": "Sir, laptop par pichla kaam abhi ruka nahi, is liye typing aur clicks abhi band hain.",
    "DESKTOP_LINK_DISABLED": "Sir, laptop ka link band kiya gaya hai.",
}
DEFAULT_MESSAGE = "Sir, laptop par yeh kaam nahi ho saka."
# A node result of a call that started and ended early (may_have_run): its outcome is not known, never a failure.
PARTIAL_MESSAGE = ("Sir, laptop par yeh kaam poora nahi hua{reason}; ho sakta hai is ka kuch hissa ho chuka ho, is "
                   "liye main ise dobara nahi kar raha.")
_CODE_RE = re.compile(r"[A-Z0-9_]{1,64}")  # the K-12 error_code form: the only node code this side uses
_STATUS_RE = re.compile(r"[a-z_]{1,32}")
_ID_CLEAN = re.compile(r"[^A-Za-z0-9._-]+")
_ROOM_RE = re.compile(r"[A-Za-z0-9._:-]{0,64}")  # the K-70 room format
_DELIVERY_OPS = ("pc.fetch", "pc.open_url")  # what jarvis_delivery submits (C-113)
_PC_OPS = {op.name: op for op in tool_placement.PC_OPERATIONS}
_ISOLATED_ROOM = re.compile(r"^jarvis-(?:probe|canary)-\d+$")  # K-57 rooms: origin 'canary' (K-76)


def link_cap(timeout_s: float, lock_wait_s: float, ack_s: float = 3.0) -> float:
    """K-70 proxy cap: 3 + lock_wait + timeout + 10 s."""
    return ack_s + float(lock_wait_s) + float(timeout_s) + 10.0


def code_message(code: str | None) -> str:
    return CODE_MESSAGES.get(code or "", DEFAULT_MESSAGE)


def k12_error(tool: str, code: str, message: str | None = None, *, status: str = "error") -> dict[str, Any]:
    """A K-12 ToolResult for an outcome the link or the proxy produces itself."""
    return {"status": status, "tool": tool, "error_code": code, "message": message or code_message(code)}


def clean_code(code: Any) -> str | None:
    """A code the laptop sent (a refusal's, or a result's error_code) when it has the K-12 form [A-Z0-9_]{1,64}, else
    None: a buggy or compromised laptop never gets text into a log line, the audit or an announcement."""
    return code if isinstance(code, str) and _CODE_RE.fullmatch(code) else None


def may_have_run(status: Any, effect: Any) -> bool:
    """A node result of a call that started and ended early: any status but 'completed' with effect 'possible' (the
    node's own TIMEOUT, a tool that raised, typing stopped after a chunk was sent, the node's answer to a cancel). Part
    of it may have happened, so its outcome is not known: never reported as a failure (partial_k12)."""
    return status != "completed" and effect == "possible"


def partial_k12(tool: str, result: Any, status: Any) -> dict[str, Any]:
    """The K-12 ToolResult of a node result that may have run in part (may_have_run): DESKTOP_RESULT_UNKNOWN, whose
    message says it did not finish and may have partly happened (so the model never retries it and on_result never
    calls it a failure); the node's own status, code and message stay in data."""
    node = result if isinstance(result, Mapping) else {}
    code = clean_code(node.get("error_code"))
    data: dict[str, Any] = {"effect": "possible"}
    if isinstance(status, str) and _STATUS_RE.fullmatch(status):
        data["laptop_status"] = status
    if code is not None:
        data["laptop_error_code"] = code
    message = node.get("message")
    if isinstance(message, str) and message.strip():
        data["laptop_message"] = message
    reason = f" ({code})" if code is not None and code != DESKTOP_RESULT_UNKNOWN else ""
    return {"status": "error", "tool": tool, "error_code": DESKTOP_RESULT_UNKNOWN,
            "message": PARTIAL_MESSAGE.format(reason=reason), "data": data}


def new_call_id() -> str:
    """A K-70 call_id ([A-Za-z0-9_-]{8,64}): the idempotency key of one laptop call."""
    return "c" + uuid.uuid4().hex


def peer_id_for(room: str, *, svc: bool = False) -> str:
    """The agent peer id: 'svc' for jarvis-agent-svc, else 'job-<room>'. It stays the same across restarts, so the
    SeenStore namespace and the node's outbox routing survive an agent restart (K-70 check 7)."""
    if svc:
        return SVC_PEER_ID
    clean = _ID_CLEAN.sub("-", room or "").strip("-._")[:60]
    return f"job-{clean or 'room'}"


def origin_for(room: str, default: str) -> str:
    """'canary' in a K-57 probe or canary room (the node refuses actuation from there), else default."""
    return "canary" if isinstance(room, str) and _ISOLATED_ROOM.match(room) else default


def _label_text(value: Any, limit: int = 64) -> str:
    return value if isinstance(value, str) and 0 < len(value) <= limit and value.isprintable() else ""


# ---------------------------------------------------------------- keys


@dataclass(frozen=True)
class LinkKeys:
    """The agent's Ed25519 signing key and the node's pinned public key; repr() never shows key material."""

    signing: Any = field(repr=False)
    pc_public: Any = field(repr=False)


def load_keys(environ: Mapping[str, str] | None = None) -> tuple[LinkKeys | None, str]:
    """(keys, '') from DESKTOP_AGENT_SIGNING_KEY and DESKTOP_PC_PUBLIC_KEY, or (None, why not). The reason names
    variables, never values."""
    env = os.environ if environ is None else environ
    seed, pc = ((env.get(name) or "").strip() for name in (ENV_SIGNING_KEY, ENV_PC_PUBLIC_KEY))
    missing = [name for name, value in ((ENV_SIGNING_KEY, seed), (ENV_PC_PUBLIC_KEY, pc)) if not value]
    if missing:
        return None, " and ".join(missing) + (" is" if len(missing) == 1 else " are") + " not set"
    try:
        signing = dp.signing_key_from_hex(seed)
    except ValueError:
        return None, f"{ENV_SIGNING_KEY} is not a usable Ed25519 seed"
    try:
        pc_public = dp.public_key_from_hex(pc)
    except ValueError:
        return None, f"{ENV_PC_PUBLIC_KEY} is not a usable Ed25519 public key"
    return LinkKeys(signing, pc_public), ""


# ---------------------------------------------------------------- calls and outcomes


@dataclass(frozen=True)
class CallRequest:
    """One laptop call: a manifest tool or a pc.* operation with its arguments (args_sha256 is computed when the
    exec is built). label is opaque to the link and comes back with a late outcome (desktop_proxy keeps the K-13 id,
    the risk and the args preview there). budget_s is the most the whole call may take from when the link starts it,
    its cancel and grace included: what is left of the caller's K-11 timeout (None: only the K-70 proxy cap).
    speech_id names the LiveKit speech whose laptop calls keep their order: right before the exec is built, the link
    stamps seq {speech_id, n}, n counting the execs of that speech it sent (DesktopLink._stamp_seq). A request with its
    own seq keeps it, and one with neither carries none."""

    tool: str
    args: Mapping[str, Any]
    param_schema_sha256: str
    timeout_s: float
    lock_wait_s: float = 0.0
    origin: str = "llm"
    seq: Mapping[str, Any] | None = None
    approval: Mapping[str, Any] | None = None
    call_id: str = field(default_factory=new_call_id)
    label: Mapping[str, Any] = field(default_factory=dict)
    budget_s: float | None = None
    speech_id: str | None = None

    @property
    def cap_s(self) -> float:
        return link_cap(self.timeout_s, self.lock_wait_s)


@dataclass(frozen=True)
class CallOutcome:
    """How one call ended for its caller. kind: result (the node's K-12 result), refused (a node refusal or a fast
    refusal with its code), offline, not_started, unknown, timeout or stopped. sent: an exec left for the node.
    kept_open: an unknown, timed-out or stopped call the link reconciles later (_keep_open), whose real outcome then
    reaches the 'late' listeners."""

    call_id: str
    kind: str
    code: str | None = None
    result: Mapping[str, Any] | None = None
    status: str | None = None
    effect: str | None = None
    sent: bool = False
    kept_open: bool = False

    @property
    def never_ran(self) -> bool:
        """The call provably did not run (a refusal, not started, offline before the send)."""
        return self.kind in (REFUSED, OFFLINE, NOT_STARTED) or (
            self.kind == RESULT and self.status == "not_started" and self.effect == "none")

    def k12(self, tool: str) -> dict[str, Any]:
        """The K-12 ToolResult the model gets (and on_result, and a lost-output announcement): the node's own result,
        except for one that may have run in part, which is DESKTOP_RESULT_UNKNOWN (partial_k12), never a failure."""
        if self.kind == RESULT:
            result = self.result
            if may_have_run(self.status, self.effect):
                return partial_k12(tool, result, self.status)
            if isinstance(result, Mapping):
                return dict(result)
            return {"status": "success", "tool": tool, "message": "" if result is None else str(result)}
        if self.kind == STOPPED:
            return k12_error(tool, STOPPED_BY_USER, status="cancelled")
        code = {OFFLINE: DESKTOP_OFFLINE, NOT_STARTED: DESKTOP_NOT_STARTED, UNKNOWN: DESKTOP_RESULT_UNKNOWN,
                TIMEOUT: DESKTOP_TIMEOUT}.get(self.kind, self.code or DESKTOP_RESULT_UNKNOWN)
        return k12_error(tool, code)


@dataclass(frozen=True)
class LateOutcome:
    """A call's outcome that came after its caller had its answer (or after an agent restart): reason is why it was
    still open (unknown, timeout, stopped, restart or delivery) and state what the node says now (result,
    not_started, interrupted or completed). 'completed' is the WAL's word for any end (done, failed, cancelled or
    timed out) whose result never came: the call no longer runs, and its outcome is not known."""

    call_id: str
    tool: str
    reason: str
    state: str
    result: Mapping[str, Any] | None = None
    status: str | None = None
    effect: str | None = None
    label: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class _Call:
    """An exec in flight: its events arrive on the queue (ack, progress, result, undeliverable, wal, link_lost, stop).
    ends_by (link clock) is when its outcome must be in, cancel and grace included (the caller's budget_s)."""

    request: CallRequest
    events: asyncio.Queue[tuple[str, Any]]
    sent_at: float
    acked: bool = False
    last_activity: float = 0.0
    ends_by: float | None = None

    def push(self, kind: str, body: Any = None) -> None:
        self.events.put_nowait((kind, body))


@dataclass
class _Unresolved:
    """A call answered unknown, timed out or stopped, until its outcome is known. ended: a hello's WAL report says it
    ended (completed) and its result_resend is awaited, so nothing of it is left to stop."""

    request: CallRequest
    reason: str
    since: float
    ended: bool = False


@dataclass(frozen=True)
class LinkTimers:
    """K-70 timers (tests shorten them)."""

    ack_s: float = 3.0
    grace_s: float = 2.0
    stall_s: float = 30.0
    ping_s: float = 30.0
    stop_refuse_s: float = 3.0
    completed_grace_s: float = 15.0
    reconnect_first_s: float = 1.0
    reconnect_max_s: float = 30.0
    jitter_s: float = 1.0
    stable_connection_s: float = 60.0
    connect_timeout_s: float = 10.0
    send_timeout_s: float = 10.0


# ---------------------------------------------------------------- a pending desktop_stop across agent processes


def _read_stop_file(path: Path) -> float | None:
    """The wall time of the desktop_stop kept in path, or None when none is kept. A file that cannot be read or
    parsed counts as a pending stop with ts 0 (fail closed; a stop only tightens) until the laptop reports it."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Desktop stop file unreadable (%s); a pending desktop_stop is assumed", type(exc).__name__)
        return 0.0
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    ts = data.get("ts") if isinstance(data, dict) else None
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts):
        return float(ts)
    logger.warning("Desktop stop file malformed; a pending desktop_stop is assumed")
    return 0.0


def _write_stop_file(path: Path, ts: float) -> None:
    """Keeps a pending desktop_stop (its wall time) in path, atomically under the file's lock. The wall time is kept
    exactly as read (JSON keeps a float exactly), never rounded: a stop said while the laptop already reports read or
    off is done right after it was kept, so _clear_stop_file compares two wall times of one millisecond; a rounded-up
    ts left the file behind (the next job's link sent desktop_stop again and undid a later tray Full), and a
    rounded-down one could make a stop kept after a report look older than that report. Raises OSError or
    TimeoutError when it cannot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path, timeout=STOP_FILE_LOCK_S):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"ts": float(ts)}), encoding="utf-8")
        os.replace(tmp, path)


def _clear_stop_file(path: Path, upto: float) -> None:
    """Removes the kept desktop_stop once the laptop reported read or off (at wall time upto): a stop kept before that
    report goes (its exact ts is no later than upto, read on the same clock), and one another process kept after it
    stays. Never raises: a file left behind only sends desktop_stop once more."""
    try:
        with locked(path, timeout=STOP_FILE_LOCK_S):
            kept = _read_stop_file(path)
            if kept is not None and kept <= upto:
                path.unlink(missing_ok=True)
    except (OSError, TimeoutError) as exc:
        logger.warning("Desktop stop file not cleared (%s); the next hello sends desktop_stop again",
                       type(exc).__name__)


# ---------------------------------------------------------------- the link


class DesktopLink:  # pylint: disable=too-many-instance-attributes,too-many-public-methods  # one link, one place
    """The agent end of the Desktop Link (see the module docstring)."""

    def __init__(self, *, keys: LinkKeys, peer_id: str, job_id: str, room: str, seen: dp.SeenStore,
                 transport_factory: Callable[[], LinkTransport], timers: LinkTimers | None = None,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.perf_counter,
                 rng: Callable[[], float] = random.random, stop_path: Path | None = None) -> None:
        # mono is perf_counter (monotonic everywhere): on Windows with Python 3.12 time.monotonic ticks every ~16 ms,
        # and a ClockEstimator then drops every sample whose round trip is shorter than one tick.
        # stop_path: where a pending desktop_stop outlives this process (ensure_link: conversations/desktop_stop.json);
        # None keeps it in memory only.
        self.peer_id, self.ident = peer_id, f"agent:{peer_id}"
        self.job_id = _label_text(job_id) or ("svc" if peer_id == SVC_PEER_ID else "job")
        self.room = room if isinstance(room, str) and _ROOM_RE.fullmatch(room) else ""
        self.timers = timers or LinkTimers()
        self._wall, self._mono, self._rng = wall, mono, rng
        self.clock = dp.ClockEstimator(wall=wall, mono=mono)
        self.signer = dp.LinkSigner(self.ident, keys.signing)
        self.seen = seen
        self.receiver = dp.Receiver(self.ident, {"pc": keys.pc_public}, seen, clock=self.clock, wall=wall)
        self._factory = transport_factory
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._transport: LinkTransport | None = None
        self._connected = False
        self.session: dp.PeerSession | None = None
        self.node_ident: str | None = None
        self._node_sess: str | None = None  # the node session of the newest hello taken, kept across reconnects
        self._replaced: OrderedDict[str, float] = OrderedDict()  # node sessions this link replaced -> when (mono)
        self._eph: Any = None
        self._eph_sess: str | None = None
        self._awaiting_reply = False
        self.desktop_status: dict[str, Any] | None = None
        self._status_at = float("-inf")  # when the last desktop_status hint came (mono)
        self._session_at = float("-inf")  # when the current node session was taken (mono)
        self.brain: str | None = None
        self._calls: dict[str, _Call] = {}
        self._unresolved: dict[str, _Unresolved] = {}
        self._first_sent: dict[str, float] = {}
        self._seq: OrderedDict[str, int] = OrderedDict()  # speech_id -> the n its next sent exec takes (K-71)
        self._exec_lock: asyncio.Lock | None = None  # run_call's sends, in submission order (made on the link loop)
        self._stop_until = float("-inf")
        self._pending_controls: list[str] = []
        self._controls_lock: asyncio.Lock | None = None  # _flush_controls (made on the link loop at first use)
        self._desktop_stop_pending = False  # 'desktop band karo' until the laptop reports mode read or off
        self._desktop_stop_sent_at: float | None = None  # when it last went out (mono)
        self._desktop_stop_persisted = False  # the pending stop is in the stop file (it outlives this process)
        self.stop_path = Path(stop_path) if stop_path is not None else None
        if self.stop_path is not None and _read_stop_file(self.stop_path) is not None:
            # an earlier agent process ended before the laptop reported its stop: it goes at the laptop's next hello
            self._desktop_stop_pending = self._desktop_stop_persisted = True
            logger.warning("Desktop control desktop_stop of an earlier agent process is pending; it goes to the "
                           "laptop at its next hello")
        self._sync_sent = False
        self._listeners: dict[str, list[tuple[Callable[[Any], Any], asyncio.AbstractEventLoop | None]]] = {}
        self._listener_lock = threading.Lock()
        self._inbox: asyncio.Queue[tuple[Any, float]] | None = None
        self._timer_tasks: list[asyncio.Task[Any]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._retired = False  # close(): stopped for good at its job's end
        self._wake: asyncio.Event | None = None
        self._rejects: dict[tuple[str, str], tuple[float, int]] = {}
        self.sent: dict[str, int] = {}
        self.connects = 0

    # --- state ------------------------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """The '/desktop' socket to the bridge is up."""
        return self._connected

    def online(self) -> bool:
        """The laptop can take a call now: a node session exists and the bridge's last desktop_status (K-74), when it
        came after that session's hello, does not say offline (the bridge sends its status right after relaying a
        hello). A stopped or retired link is never online. Thread-safe; part of jarvis_delivery's DesktopSender."""
        if self._stopping or not self._connected or self.session is None:
            return False
        status = self.desktop_status
        offline = isinstance(status, Mapping) and status.get("online") is False
        return not (offline and self._status_at >= self._session_at)

    def stopped(self) -> bool:
        """Within the 3 s after an explicit stop: new calls are refused with STOPPED_BY_USER."""
        return self._mono() < self._stop_until

    @property
    def desktop_stop_pending(self) -> bool:
        """A desktop_stop the laptop has not yet reported (mode read or off in a hello or a status hint)."""
        return self._desktop_stop_pending

    @property
    def desktop_stop_persisted(self) -> bool:
        """The pending desktop_stop is kept in the stop file, so it reaches the laptop also when this process ends
        first (the next agent process's link sends it); False: it holds only while this process runs."""
        return self._desktop_stop_pending and self._desktop_stop_persisted

    def fast_refusal(self) -> str | None:
        """The code a new call gets at once, else None: CLOCK_SKEW first (K-70 v5: before the offline fast path),
        then STOPPED_BY_USER, then DESKTOP_OFFLINE. Thread-safe."""
        if self.clock.skew_exceeded():
            return CLOCK_SKEW
        if self.stopped():
            return STOPPED_BY_USER
        if not self.online():
            return DESKTOP_OFFLINE
        return None

    def in_flight(self) -> int:
        return len(self._calls)

    def unresolved(self) -> list[str]:
        return sorted(self._unresolved)

    # --- listeners --------------------------------------------------------------------------------------------

    def subscribe(self, kind: str, callback: Callable[[Any], Any],
                  loop: asyncio.AbstractEventLoop | None = None) -> Callable[[], None]:
        """callback(payload) for 'late' (LateOutcome), 'event' (a node event body with its kind), 'stop_all' (the
        reason), 'session' (the node ident) or 'status' (desktop_status). With loop it runs there
        (call_soon_threadsafe; a coroutine result becomes a task), else on the link loop. Returns the unsubscribe."""
        entry = (callback, loop)
        with self._listener_lock:
            self._listeners.setdefault(kind, []).append(entry)

        def unsubscribe() -> None:
            with self._listener_lock:
                entries = self._listeners.get(kind, [])
                if entry in entries:
                    entries.remove(entry)
        return unsubscribe

    def _notify(self, kind: str, payload: Any) -> None:
        with self._listener_lock:
            entries = list(self._listeners.get(kind, ()))
        for callback, loop in entries:
            if loop is not None:
                if loop.is_closed():
                    continue
                try:
                    loop.call_soon_threadsafe(_run_listener, callback, payload)
                except RuntimeError:  # the loop closed meanwhile
                    continue
            else:
                _run_listener(callback, payload)

    # --- lifecycle --------------------------------------------------------------------------------------------

    def start_in_thread(self) -> None:
        """Runs the link on its own event loop in a daemon thread (the production mode)."""
        if self._thread is not None:
            return
        ready = threading.Event()

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            ready.set()
            try:
                loop.run_until_complete(self.run())
            except Exception:  # pylint: disable=broad-exception-caught  # logged; the proxies answer offline
                logger.exception("Desktop Link loop ended with an error")
            finally:
                loop.close()
                if self._retired:  # nothing uses a retired link's seen store any more (its worker thread ends)
                    self.seen.close()

        self._thread = threading.Thread(target=runner, name="desktop-link", daemon=True)
        self._thread.start()
        ready.wait(5.0)

    def stop_threadsafe(self, timeout: float = 5.0) -> None:
        """Ends run() from any thread and waits for the link thread (when there is one)."""
        loop = self.loop
        if loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.stop(), loop).result(timeout)
            except Exception:  # pylint: disable=broad-exception-caught  # stopping never fails the caller
                logger.debug("Desktop Link stop did not finish cleanly", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout)

    async def stop(self) -> None:
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        transport = self._transport
        if transport is not None:
            await transport.close()

    def close(self) -> None:
        """Stops the link for good, from any thread and without waiting (retire, at the end of its job): run() ends
        without another connect, a call in flight ends DESKTOP_RESULT_UNKNOWN (the node keeps its result for the
        room's next link) and a new one answers DESKTOP_OFFLINE. Also before run() started: it then never connects.
        Run in its own thread (start_in_thread), the link closes its seen store once that thread's loop ended."""
        self._stopping = self._retired = True  # read by run() and submit_call() at once, whatever the loop does
        self._call_soon(self._stop_soon)

    def _stop_soon(self) -> None:
        self._spawn(self.stop())  # on the link loop: wakes the reconnect wait and closes the socket

    async def run(self) -> None:
        """Connects and reconnects until stop()."""
        self.loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        maintenance = asyncio.create_task(self._maintain(), name="dl-agent-maintenance")
        delay, failures = self.timers.reconnect_first_s, 0
        try:
            while not self._stopping:
                connected_at: float | None = None
                try:
                    await self._connect_once()
                    connected_at, failures = self._mono(), 0
                    transport = self._transport
                    if transport is not None:
                        await transport.closed()
                    if not self._stopping:
                        logger.warning("Desktop Link to the bridge lost; reconnecting")
                except Exception as exc:  # pylint: disable=broad-exception-caught  # retried forever (never a cancel)
                    failures += 1
                    if failures == 1 or failures % 10 == 0:
                        logger.warning("Desktop Link connect failed (attempt %d, %s); retrying", failures,
                                       type(exc).__name__)
                await self._teardown()
                if self._stopping:
                    break
                if connected_at is not None and self._mono() - connected_at >= self.timers.stable_connection_s:
                    delay = self.timers.reconnect_first_s
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay + self._rng() * self.timers.jitter_s)
                except TimeoutError:
                    pass
                delay = min(delay * 2, self.timers.reconnect_max_s)
        finally:
            await self._teardown()
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)

    async def _connect_once(self) -> None:
        # A new socket needs a new handshake: the node replaces our key when it takes the new agent_hello.
        self.session, self._eph, self._eph_sess, self._awaiting_reply = None, dp.new_ephemeral_key(), None, True
        hello = self.signer.build("agent_hello", dp.agent_hello_body(peer_id=self.peer_id, job_id=self.job_id,
                                                                     room=self.room, eph=self._eph),
                                  to="pc:*", room=self.room, now=self._wall())
        inbox: asyncio.Queue[tuple[Any, float]] = asyncio.Queue(maxsize=INBOX_MAX)
        self._inbox = inbox
        self._timer_tasks = [asyncio.create_task(self._consume(inbox), name="dl-agent-inbox")]
        transport = self._factory()
        transport.set_receiver(self._on_payload)
        self._transport = transport
        await asyncio.wait_for(transport.open({AUTH_HELLO: dp.canonical_json(hello).decode("utf-8")}),
                               timeout=self.timers.connect_timeout_s + 5.0)
        self._connected = True
        self.connects += 1
        self._count("agent_hello")
        logger.info("Desktop Link connected as %s (connect #%d)", self.ident, self.connects)
        self._timer_tasks.append(asyncio.create_task(self._ping_loop(), name="dl-agent-ping"))

    async def _teardown(self) -> None:
        timers, self._timer_tasks = self._timer_tasks, []
        for task in timers:
            task.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        self._inbox = None
        was_connected, self._connected = self._connected, False
        self.session = None
        transport, self._transport = self._transport, None
        if transport is not None:
            await transport.close()
        for call in list(self._calls.values()):
            call.push("link_lost")
        if was_connected:
            self._notify("session", None)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.timers.ping_s)
            try:
                await self.ping()
            except Exception:  # pylint: disable=broad-exception-caught  # a timer never dies
                logger.exception("Desktop Link ping failed")

    async def _maintain(self) -> None:
        """Drops reconciliation entries older than their window, and first-send times after FIRST_SENT_KEEP_S (they
        must outlive CALL_TTL_S, or run_call could no longer tell that a call_id is older than 1 h)."""
        while True:
            await asyncio.sleep(MAINTENANCE_S)
            now = self._mono()
            for call_id in [c for c, item in self._unresolved.items() if now - item.since > UNRESOLVED_TTL_S]:
                del self._unresolved[call_id]
                logger.info("Desktop call %s left unreconciled after %.0f s", call_id, UNRESOLVED_TTL_S)
            for call_id in [c for c, first in self._first_sent.items()
                            if now - first > FIRST_SENT_KEEP_S and c not in self._calls]:
                del self._first_sent[call_id]

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _call_soon(self, callback: Callable[..., Any], *args: Any) -> bool:
        """callback(*args) on the link loop (at once when called there); False when the loop is not running."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            callback(*args)
            return True
        try:
            loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            return False
        return True

    # --- sending ----------------------------------------------------------------------------------------------

    def _count(self, typ: str) -> None:
        self.sent[typ] = self.sent.get(typ, 0) + 1

    async def _emit(self, env: Mapping[str, Any]) -> bool:
        transport = self._transport
        if transport is None or not self._connected or not transport.connected:
            return False
        try:
            await asyncio.wait_for(transport.send(dp.canonical_json(dict(env)).decode("utf-8")),
                                   timeout=self.timers.send_timeout_s)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the reconnect loop takes over
            logger.warning("Desktop Link send of %s failed (%s)", env.get("typ"), type(exc).__name__)
            return False
        self._count(str(env.get("typ")))
        return True

    async def ping(self) -> bool:
        """A ping to the node, registered with the ClockEstimator (K-70)."""
        session, node = self.session, self.node_ident
        if session is None or node is None or not self._connected:
            return False
        env = self.signer.build("ping", {}, to=node, sess=session.sess, now=self._wall())
        self.clock.ping_sent(env)
        return await self._emit(env)

    async def _send_control(self, kind: str) -> bool:
        """An agent-signed tighten-only control (stop_all); it waits for the next handshake while there is no
        session. desktop_stop goes through _send_desktop_stop."""
        session, node = self.session, self.node_ident
        if session is None or node is None:
            if kind not in self._pending_controls:
                self._pending_controls.append(kind)
            logger.info("Desktop control %s waits for the next laptop handshake", kind)
            return False
        env = self.signer.build("control", {"kind": kind}, to=node, sess=session.sess, now=self._wall())
        sent = await self._emit(env)
        if sent:
            logger.warning("Desktop control %s sent to %s", kind, node)
        elif kind not in self._pending_controls:
            self._pending_controls.append(kind)
        return sent

    async def _send_desktop_stop(self) -> bool:
        """The agent-signed desktop_stop while it is pending (see the module docstring). It goes out only while the
        laptop is online: on a stale session (the node left the bridge, this socket stayed up) the bridge would drop
        it, so it waits for the node's next hello instead."""
        if not self._desktop_stop_pending:
            return False
        session, node = self.session, self.node_ident
        if session is None or node is None or not self.online():
            logger.info("Desktop control desktop_stop waits for the laptop's next hello")
            return False
        env = self.signer.build("control", {"kind": "desktop_stop"}, to=node, sess=session.sess, now=self._wall())
        sent = await self._emit(env)
        if sent:
            self._desktop_stop_sent_at = self._mono()
            logger.warning("Desktop control desktop_stop sent to %s", node)
        return sent

    def _desktop_stop_on_status(self, status: Mapping[str, Any]) -> None:
        """A status hint while desktop_stop is pending: an online laptop in mode read or off has taken it; an online
        laptop that was never sent it in this session (it was stale or offline when Sir asked) gets it now."""
        if not self._desktop_stop_pending or status.get("online") is not True:
            return
        if status.get("mode") in STOPPED_MODES:
            self._desktop_stop_done("status")
        elif self.online() and not self._desktop_stop_sent_since_hello():
            self._spawn(self._send_desktop_stop())

    def _desktop_stop_sent_since_hello(self) -> bool:
        """The pending desktop_stop went out after the newest node hello was taken (so in its session)."""
        sent = self._desktop_stop_sent_at
        return sent is not None and sent >= self._session_at

    def _persist_desktop_stop(self) -> bool:
        """Keeps the pending desktop_stop in the stop file (see the module docstring); False without a file or when
        it cannot be written: the stop then holds only while this process runs."""
        path = self.stop_path
        if path is None:
            return False
        try:
            _write_stop_file(path, self._wall())
        except (OSError, TimeoutError, ValueError) as exc:
            logger.error("Desktop control desktop_stop not kept on disk (%s); it holds only while this agent process "
                         "runs", type(exc).__name__)
            return False
        return True

    def _desktop_stop_done(self, source: str) -> None:
        self._desktop_stop_pending, self._desktop_stop_sent_at = False, None
        self._desktop_stop_persisted = False
        logger.info("Desktop control desktop_stop done: the laptop reports read-only or off (%s)", source)
        if self.stop_path is not None:  # on a worker thread: the link loop never waits for the file lock
            self._spawn(asyncio.to_thread(_clear_stop_file, self.stop_path, self._wall()))

    async def _send_cancel(self, call: _Call) -> bool:
        session, node = self.session, self.node_ident
        if session is None or node is None:
            return False
        req = call.request
        try:
            env = self.signer.build("cancel", {"call_id": req.call_id}, to=node, sess=session.sess,
                                    room=self.room, origin=req.origin, session_key=session.key,
                                    call_id=req.call_id, tool=req.tool, now=self._wall())
        except DesktopLinkError as exc:
            logger.error("Desktop cancel for %s not built (%s)", req.tool, exc.reason)
            return False
        return await self._emit(env)

    async def _send_result_ack(self, env: VerifiedEnvelope) -> None:
        session = self.session
        if session is None:
            return
        ack = self.signer.build("result_ack", {}, to=env.sender, sess=session.sess, call_id=str(env.call_id),
                                now=self._wall())
        await self._emit(ack)

    # --- calls ------------------------------------------------------------------------------------------------

    def submit_call(self, request: CallRequest) -> concurrent.futures.Future[CallOutcome]:
        """Thread-safe: runs one call on the link loop. The future never raises (cancelling it cancels the call's
        wait, never the laptop call itself)."""
        loop = self.loop
        if loop is None or loop.is_closed() or self._stopping:
            done: concurrent.futures.Future[CallOutcome] = concurrent.futures.Future()
            done.set_result(CallOutcome(request.call_id, OFFLINE, DESKTOP_OFFLINE))
            return done
        return asyncio.run_coroutine_threadsafe(self.run_call(request), loop)

    async def run_call(self, request: CallRequest) -> CallOutcome:
        """One exec and its K-70 outcome (see the module docstring); on the link loop. Every call goes through
        _send_exec under _exec_lock, which is FIFO and taken first, so calls are sent one at a time in the order they
        were submitted, and the seq numbers of one speech follow that order."""
        if self._exec_lock is None:
            self._exec_lock = asyncio.Lock()
        started = self._mono()  # before the lock: a wait for it counts against the caller's budget
        async with self._exec_lock:
            sent = await self._send_exec(request, started)
        if isinstance(sent, CallOutcome):
            return sent
        try:
            outcome = await self._await_outcome(sent)
        finally:
            self._calls.pop(request.call_id, None)
        logger.info("Desktop call %s (%s): %s%s", request.tool, request.call_id, outcome.kind,
                    f" {outcome.code}" if outcome.code else "")
        return outcome

    async def _send_exec(self, request: CallRequest, started: float) -> CallOutcome | _Call:
        """run_call's part under _exec_lock: the checks that answer at once, the controls that must reach the laptop
        before this exec, then the seq stamp (_stamp_seq), the exec and its send. Returns the call in flight, or the
        outcome of a call that was not sent; such a call gives its seq number back (_unstamp_seq), so the node's
        SeqGate never holds the speech's next call for it (K-71: only a sent exec takes a number)."""
        # pylint: disable=too-many-return-statements
        call_id = request.call_id
        code = self.fast_refusal()
        if code is not None:
            kind = {DESKTOP_OFFLINE: OFFLINE, STOPPED_BY_USER: STOPPED}.get(code, REFUSED)
            return CallOutcome(call_id, kind, code)
        if self._desktop_stop_pending and not self._desktop_stop_sent_since_hello():
            await self._send_desktop_stop()  # a stop not yet sent in this session reaches the laptop before an exec
        await self._flush_controls()  # so does a stop_all that waited for this session: it was said before this call
        # The two awaits above can let 'ruko' or the dashboard Stop run, and this call is not in _calls yet, so
        # _stop_all_now cannot reach it: check again before anything is built or sent (C-119 verify, 20 Oct).
        code = self.fast_refusal()
        if code is not None:
            kind = {DESKTOP_OFFLINE: OFFLINE, STOPPED_BY_USER: STOPPED}.get(code, REFUSED)
            return CallOutcome(call_id, kind, code)
        first = self._first_sent.get(call_id)
        if call_id in self._calls or (first is not None and self._mono() - first > CALL_TTL_S):
            logger.warning("Desktop exec %s for %s not sent: in flight or older than %.0f s", call_id, request.tool,
                           CALL_TTL_S)
            return CallOutcome(call_id, NOT_STARTED, DESKTOP_NOT_STARTED)
        session, node = self.session, self.node_ident
        if session is None or node is None:
            return CallOutcome(call_id, OFFLINE, DESKTOP_OFFLINE)
        ends_by = self._ends_by(request, started)
        if ends_by is not None and ends_by - self.timers.grace_s - self._mono() < self.timers.ack_s:
            logger.warning("Desktop exec %s for %s not sent: too little of its K-11 timeout is left", call_id,
                           request.tool)
            return CallOutcome(call_id, NOT_STARTED, DESKTOP_NOT_STARTED)
        request, taken = self._stamp_seq(request)
        try:
            env = self._build_exec(request, session, node)
        except (DesktopLinkError, dp.CanonicalJsonError, TypeError, ValueError) as exc:
            self._unstamp_seq(taken)
            logger.error("Desktop exec for %s not built (%s)", request.tool, getattr(exc, "reason", type(exc).__name__))
            return CallOutcome(call_id, REFUSED, BAD_ARGS)
        call = _Call(request, asyncio.Queue(), sent_at=self._mono(), ends_by=ends_by)
        self._calls[call_id] = call
        try:
            sent = await self._emit(env)
        except BaseException:  # a cancelled wait: this call ends here, and the speech's next one takes its number
            self._calls.pop(call_id, None)
            self._unstamp_seq(taken)
            raise
        if not sent:
            self._calls.pop(call_id, None)
            self._unstamp_seq(taken)
            return CallOutcome(call_id, OFFLINE, DESKTOP_OFFLINE)
        call.sent_at = call.last_activity = self._mono()
        self._first_sent.setdefault(call_id, call.sent_at)
        logger.info("Desktop exec %s sent (%s)", request.tool, call_id)
        return call

    def _stamp_seq(self, request: CallRequest) -> tuple[CallRequest, tuple[str, int] | None]:
        """K-71 ordering: a request that names its speech (speech_id) and has no seq of its own gets seq {speech_id,
        n}, n counting the execs of that speech this link sent, from 0. It runs right before the exec is built, under
        _exec_lock, so the numbers follow the order the calls were submitted in, and _unstamp_seq gives back the
        number of an exec that is not sent. Returns the request to build and the (speech_id, n) it took, else None."""
        speech_id = request.speech_id
        if request.seq is not None or not isinstance(speech_id, str) or not SPEECH_ID_RE.fullmatch(speech_id):
            return request, None
        number = self._seq.pop(speech_id, 0)
        self._seq[speech_id] = number + 1
        while len(self._seq) > SEQ_SPEECHES_MAX:
            self._seq.popitem(last=False)
        return replace(request, seq={"speech_id": speech_id, "n": number}), (speech_id, number)

    def _unstamp_seq(self, taken: tuple[str, int] | None) -> None:
        """Gives back the seq number of an exec that was not sent; the speech's next exec takes it. Only the newest
        number of a speech goes back (under _exec_lock, the number of the call being sent always is)."""
        if taken is None:
            return
        speech_id, number = taken
        if self._seq.get(speech_id) == number + 1:
            self._seq[speech_id] = number

    @staticmethod
    def _ends_by(request: CallRequest, started: float) -> float | None:
        """When the call's outcome must be in (link clock), PROXY_MARGIN_S before its caller's K-11 timeout runs out;
        None without a budget."""
        budget = request.budget_s
        if not isinstance(budget, (int, float)) or isinstance(budget, bool) or not math.isfinite(budget):
            return None
        return started + float(budget) - PROXY_MARGIN_S

    def _build_exec(self, request: CallRequest, session: dp.PeerSession, node: str) -> dict[str, Any]:
        args = dict(request.args)
        start_by = dp.make_start_by(self._wall())
        body: dict[str, Any] = {
            "call_id": request.call_id, "tool": request.tool, "args": args, "args_sha256": dp.args_sha256(args),
            "param_schema_sha256": request.param_schema_sha256, "timeout_s": float(request.timeout_s),
            "lock_wait_s": float(request.lock_wait_s), "start_by": start_by, "room": self.room,
            "origin": request.origin}
        if request.seq is not None:
            body["seq"] = dict(request.seq)
        if request.approval is not None:
            body["approval"] = dict(request.approval)
        return self.signer.build("exec", body, to=node, sess=session.sess, room=self.room, origin=request.origin,
                                 session_key=session.key, call_id=request.call_id, tool=request.tool,
                                 seq=request.seq, start_by=start_by, now=self._wall())

    async def _await_outcome(self, call: _Call) -> CallOutcome:  # pylint: disable=too-many-return-statements,too-many-branches
        """The phases after the send: ack (3 s), running (30 s stall, the proxy cap), and the 2 s grace after a
        cancel this side sent. The cancel goes out early enough that its grace ends PROXY_MARGIN_S before the proxy
        cap and before the caller's budget (call.ends_by), so the K-11 wrapper never times the call out. An event
        already queued when a wait times out is handled like one that came in time (_queued)."""
        timers, call_id = self.timers, call.request.call_id
        request = call.request
        hard_end = call.sent_at + link_cap(request.timeout_s, request.lock_wait_s, timers.ack_s) - PROXY_MARGIN_S
        if call.ends_by is not None:
            hard_end = min(hard_end, call.ends_by)
        cap_at = hard_end - timers.grace_s  # the last moment for a cancel whose grace still ends by hard_end
        phase, deadline, on_expiry, cancelled_for = "ack", min(call.sent_at + timers.ack_s, cap_at), UNKNOWN, ""
        while True:
            if phase == "running":
                deadline = min(call.last_activity + timers.stall_s, cap_at)
            try:
                kind, body = await asyncio.wait_for(call.events.get(), timeout=max(0.0, deadline - self._mono()))
            except TimeoutError:
                queued = self._queued(call)
                if queued is not None:
                    kind, body = queued  # it came as the wait ran out: handled, never left behind as 'unknown'
                elif phase == "grace":
                    return self._keep_open(call, on_expiry, cancelled_for)
                else:
                    cancelled_for = "ack_timeout" if phase == "ack" else "stall"
                    on_expiry = UNKNOWN if phase == "ack" else TIMEOUT
                    logger.warning("Desktop call %s (%s): %s; cancel sent", call.request.tool, call_id,
                                   "no ack in time" if phase == "ack" else "no progress or result in time")
                    await self._send_cancel(call)
                    phase, deadline = "grace", min(self._mono() + timers.grace_s, hard_end)
                    continue
            if kind == "ack":
                if body.get("accepted") is True:
                    call.acked, call.last_activity = True, self._mono()
                    if phase == "ack":
                        phase = "running"
                    continue
                return self._refusal(call, body.get("code"))
            if kind == "progress":
                call.last_activity = self._mono()
                continue
            if kind == "result":
                return self._result(call, body, cancelled_for)
            if kind == "undeliverable":
                if body.get("emitted") is False:
                    return CallOutcome(call_id, NOT_STARTED, DESKTOP_NOT_STARTED, sent=True)
                return self._keep_open(call, UNKNOWN, cancelled_for)
            if kind == "wal":
                if body in ("never_started", "start_window_missed"):
                    return CallOutcome(call_id, NOT_STARTED, DESKTOP_NOT_STARTED, sent=True)
                if body == "interrupted":  # it started and its end was lost in a node crash: nothing more comes
                    return CallOutcome(call_id, UNKNOWN, DESKTOP_RESULT_UNKNOWN, effect="possible", sent=True)
                continue  # completed: its result_resend follows
            if kind == "link_lost":
                return self._keep_open(call, UNKNOWN, cancelled_for)
            if kind == "stop":
                return self._keep_open(call, STOPPED, cancelled_for)

    @staticmethod
    def _queued(call: _Call) -> tuple[str, Any] | None:
        """The call's next event when one is already queued, else None. asyncio.wait_for cancels its get when the
        timer and the event land in the same loop step (or when no time is left at all), and the event stays in the
        queue: _on_result has acknowledged that result and recorded it as seen, so dropping it here would leave the
        call 'unknown' with nothing more ever coming for it."""
        try:
            return call.events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def _refusal(self, call: _Call, code: Any) -> CallOutcome:
        """K-70 Refusals: DESKTOP_REJECTED and START_WINDOW_MISSED are 'not executed'; every other code as it is, when
        it has the K-12 form (clean_code); a refusal with any other code counts as DESKTOP_REJECTED."""
        code = clean_code(code) or "DESKTOP_REJECTED"
        if code in NOT_STARTED_CODES:
            return CallOutcome(call.request.call_id, NOT_STARTED, DESKTOP_NOT_STARTED, sent=True)
        return CallOutcome(call.request.call_id, REFUSED, code, sent=True)

    @staticmethod
    def _result(call: _Call, body: Mapping[str, Any], cancelled_for: str) -> CallOutcome:
        result = body.get("result")
        status, effect = body.get("status"), body.get("effect")
        status, effect = (status if isinstance(status, str) else None), (effect if isinstance(effect, str) else None)
        if cancelled_for == "ack_timeout" and status == "not_started" and effect == "none":
            # our cancel stopped it before it started: the node proves it never ran
            return CallOutcome(call.request.call_id, NOT_STARTED, DESKTOP_NOT_STARTED, sent=True)
        if cancelled_for == "stall" and status in ("cancelled", "not_started"):
            # K-70: a stall is cancelled and reported DESKTOP_TIMEOUT (the node's answer says what it did)
            return CallOutcome(call.request.call_id, TIMEOUT, DESKTOP_TIMEOUT, status=status, effect=effect, sent=True)
        code = clean_code(result.get("error_code")) if isinstance(result, Mapping) else None
        if may_have_run(status, effect):
            # it started and ended early (the node's own TIMEOUT, a tool that raised, typing stopped after a chunk,
            # the answer to the cancel this side sent after the ack window, ...): part of it may have happened
            code = DESKTOP_RESULT_UNKNOWN
        return CallOutcome(call.request.call_id, RESULT, code,
                           result=dict(result) if isinstance(result, Mapping) else None,
                           status=status, effect=effect, sent=True)

    def _keep_open(self, call: _Call, kind: str, cancelled_for: str = "") -> CallOutcome:
        """An unknown, timed-out or stopped call: answered now, reconciled later (late listeners). A result queued
        behind the event that decided this (it landed in the same loop step as Sir's stop or a link loss) is the
        call's outcome instead: the call had ended before that event reached it, and _on_result has acknowledged that
        result and recorded it as seen, so it would never come again and the call would stay 'unknown'."""
        while (queued := self._queued(call)) is not None:
            if queued[0] == "result":
                return self._result(call, queued[1], cancelled_for)
        req = call.request
        self._unresolved[req.call_id] = _Unresolved(req, kind, self._mono())
        code = {UNKNOWN: DESKTOP_RESULT_UNKNOWN, TIMEOUT: DESKTOP_TIMEOUT, STOPPED: STOPPED_BY_USER}[kind]
        return CallOutcome(req.call_id, kind, code, effect="possible", sent=True, kept_open=True)

    def _late(self, item: _Unresolved | None, *, call_id: str, tool: str, reason: str, state: str,
              body: Mapping[str, Any] | None = None) -> None:
        result = body.get("result") if isinstance(body, Mapping) else None
        status = body.get("status") if isinstance(body, Mapping) else None
        effect = body.get("effect") if isinstance(body, Mapping) else None
        late = LateOutcome(call_id=call_id, tool=item.request.tool if item is not None else tool, reason=reason,
                           state=state, result=dict(result) if isinstance(result, Mapping) else None,
                           status=status if isinstance(status, str) else None,
                           effect=effect if isinstance(effect, str) else None,
                           label=dict(item.request.label) if item is not None else {})
        logger.info("Desktop call %s (%s): late outcome %s after %s", late.tool, call_id, state, reason)
        self._notify("late", late)

    # --- stops ------------------------------------------------------------------------------------------------

    def stop_all(self, reason: str, *, desktop_stop: bool = False) -> None:
        """Thread-safe: new calls are refused with STOPPED_BY_USER for 3 s (at once, from any thread), every call in
        flight ends with STOPPED_BY_USER (one whose result is already queued gets that result, _keep_open), and the
        node gets the agent-signed control: desktop_stop (the laptop goes read-only and cancels every call; pending
        until the laptop reports it, and kept in the stop file so that it outlives this process, see the module
        docstring; done once sent when the laptop already reports read or off, _send_said_desktop_stop) or stop_all
        (this peer's calls), whenever one of them may still run on the laptop: also a call answered
        DESKTOP_RESULT_UNKNOWN after a link loss (_stop_all_needed)."""
        self._stop_until = self._mono() + self.timers.stop_refuse_s
        if desktop_stop:
            self._desktop_stop_pending = True  # kept even when the link loop is not running yet
            self._desktop_stop_persisted = self._persist_desktop_stop()
        logger.warning("Desktop stop (%s)%s", reason, ": the laptop goes read-only" if desktop_stop else "")
        self._call_soon(self._stop_all_now, reason, desktop_stop)

    def _stop_all_now(self, reason: str, desktop_stop: bool) -> None:
        stop_all = reason == "bridge" or self._stop_all_needed()  # also for a call that may run after a link loss
        for call in list(self._calls.values()):
            call.push("stop", reason)
        if desktop_stop:
            self._desktop_stop_pending = True
            self._spawn(self._send_said_desktop_stop())
        elif stop_all:
            self._spawn(self._send_control("stop_all"))
        self._notify("stop_all", reason)

    async def _send_said_desktop_stop(self) -> None:
        """The desktop_stop Sir just said (stop_all with desktop_stop). It stays pending until the laptop reports mode
        read or off, except when the current session's desktop_status already shows the laptop online in one of them:
        its mode then does not change, so no status hint would ever report the stop, and a stop left pending (and in
        the stop file) would go out again at the next hello and undo a later tray Full. Such a stop is done once sent:
        the laptop is read-only already, and the send cancels its in-flight calls."""
        sent = await self._send_desktop_stop()
        if sent and self._desktop_stop_pending and self._laptop_reports_stopped():  # (a hint may have ended it already)
            self._desktop_stop_done("sent while it already did")

    def _laptop_reports_stopped(self) -> bool:
        """The link is online and the bridge's desktop_status of the current node session (a hint that came after
        that session's hello) shows the laptop online in mode read or off: it has taken any desktop_stop."""
        status = self.desktop_status
        return (self.online() and isinstance(status, Mapping) and status.get("online") is True
                and status.get("mode") in STOPPED_MODES and self._status_at >= self._session_at)

    def _stop_all_needed(self) -> bool:
        """A laptop call of this peer may still run, so a stop must reach the node: one in flight, or one answered
        unknown, timed out or stopped (a link loss, a cancel the node never answered) that no result or WAL report has
        ended yet. The node keeps a reconnected peer's running calls, so after a link loss only stop_all ends them."""
        return bool(self._calls) or any(not item.ended for item in self._unresolved.values())

    # --- receiving --------------------------------------------------------------------------------------------

    async def _on_payload(self, payload: Any) -> None:
        inbox = self._inbox
        if inbox is None:
            return
        try:
            inbox.put_nowait((payload, self._wall()))
        except asyncio.QueueFull:
            self._log_reject("inbox_full", "DL_REJECT")

    async def _consume(self, inbox: asyncio.Queue[tuple[Any, float]]) -> None:
        """One consumer per connection keeps the bridge's order (a hello before the node's ping, an ack before its
        result)."""
        while True:
            payload, received_at = await inbox.get()
            try:
                await self._receive(payload, received_at)
            except Exception:  # pylint: disable=broad-exception-caught  # one bad message never ends the link
                logger.exception("Desktop Link message handling failed")

    async def _receive(self, payload: Any, received_at: float) -> None:
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
            self._on_hint(payload)
            return
        try:
            env = await asyncio.to_thread(self.receiver.verify, payload)
        except DesktopLinkError as err:
            self._log_reject(err.reason, err.code)
            return
        await self._dispatch(env, received_at)

    def _log_reject(self, reason: str, code: str) -> None:
        """DL_REJECT with reason and code only; repeats of one reason within 60 s are counted."""
        now = self._mono()
        first, count = self._rejects.get((reason, code), (now, 0))
        if count == 0 or now - first >= REJECT_LOG_FOLD_S:
            logger.warning("DL_REJECT reason=%s code=%s%s", reason, code,
                           f" (+{count - 1} more in {REJECT_LOG_FOLD_S:.0f} s)" if count > 1 else "")
            self._rejects[(reason, code)] = (now, 1)
        else:
            self._rejects[(reason, code)] = (first, count + 1)

    def _on_hint(self, payload: Any) -> None:
        try:
            hint = dp.parse_bridge_hint(payload)
        except DesktopLinkError as err:
            self._log_reject(err.reason, err.code)
            return
        if hint.to not in (self.ident, "agent:*"):
            return
        body = hint.body
        if hint.typ in ("welcome", "status"):
            status = body.get("desktop_status")
            if isinstance(status, Mapping):
                self.desktop_status, self._status_at = dict(status), self._mono()
                self._notify("status", dict(status))
                self._desktop_stop_on_status(status)
            brain = body.get("brain")
            if isinstance(brain, str):
                self.brain = brain
            if body.get("stop_all") is True:  # K-74: after a UI desktop_stop; tighten-only
                self.stop_all("bridge")
        elif hint.typ == "dl_undeliverable":
            call = self._calls.get(str(body.get("call_id")))
            if call is not None:
                call.push("undeliverable", dict(body))

    async def _dispatch(self, env: VerifiedEnvelope, received_at: float) -> None:
        typ = env.typ
        if typ == "hello":
            await self._on_hello(env)
            return
        if typ == "ping":
            pong = self.signer.build("pong", dp.pong_body(env, received_at=received_at), to=env.sender,
                                     sess=str(env.sess), now=self._wall())
            await self._emit(pong)
            return
        if typ == "pong":
            return  # the Receiver gave it to the ClockEstimator
        session = self.session
        if session is None or env.sess != session.sess:
            logger.info("Desktop Link: %s outside the current session; dropped", typ)
            return
        try:
            body = dp.open_body(env, session.key)
        except DesktopLinkError as err:
            self._log_reject(err.reason, err.code)
            return
        if typ == "ack":
            self._on_ack(env, body)
        elif typ == "progress":
            call = self._calls.get(str(env.call_id))
            if call is not None:
                call.push("progress", body)
        elif typ in ("result", "result_resend"):
            await self._on_result(env, body)
        elif typ == "event":
            self._notify("event", body)

    async def _on_hello(self, env: VerifiedEnvelope) -> None:
        """A node hello: our session key comes from the newest agent_hello we sent and this hello (see the module
        docstring); then the WAL report (before any await), a pending desktop_stop (first, unless the hello reports
        mode read or off), a ping, the first sync_request and the waiting controls (a stop_all that waited is dropped
        when the report shows that nothing it could stop still runs).

        Node sessions are told apart by arrival, never by the node's wall clock (its ts): after a clock fix a new
        session's hello is signed 'before' the old one's, and ordering by ts would leave this link on a dead session
        (CLOCK_SKEW from the old sample, then DESKTOP_RESULT_UNKNOWN or DESKTOP_OFFLINE) until a later hello. Only a
        hello of a session this link replaced within REPLACED_HOLD_S (the K-70 freshness window: no hello signed
        before the replacement verifies later than that) is a relay's reordered one and ignored."""
        sess, node = str(env.sess), env.sender
        replaced_at = self._replaced.get(sess)
        if sess != self._node_sess and replaced_at is not None and self._mono() - replaced_at < REPLACED_HOLD_S:
            logger.info("Desktop Link: a hello of a replaced node session ignored")
            return
        if env.header["to"] == self.ident and self._awaiting_reply:
            eph, send_hello = self._eph, False  # the answer to our connect agent_hello
        elif self._eph is not None and self._eph_sess == sess:
            eph, send_hello = self._eph, False  # the session we already joined
        else:
            eph, send_hello = dp.new_ephemeral_key(), True  # a new node session: join it
        try:
            session = dp.accept_node_hello(env, own_eph=eph, peer_id=self.peer_id)
        except DesktopLinkError as err:
            self._log_reject(err.reason, err.code)
            return
        self._eph, self._eph_sess, self._awaiting_reply = eph, sess, False
        self.receiver.bind(sess, node)
        if self._node_sess is not None and self._node_sess != sess:
            self._replaced[self._node_sess] = self._mono()
            while len(self._replaced) > REPLACED_MAX:
                self._replaced.popitem(last=False)
        self._replaced.pop(sess, None)
        self._node_sess, self.session, self.node_ident = sess, session, node
        self._session_at = self._mono()
        # the WAL report before the first await: a run_call that starts meanwhile, and the waiting stop_all it may
        # flush before its exec, already know which calls of this peer ended
        self._reconcile(env.body.get("wal_report"))
        if send_hello:
            joined = self.signer.build("agent_hello", dp.agent_hello_body(peer_id=self.peer_id, job_id=self.job_id,
                                                                          room=self.room, eph=eph, sess=sess),
                                       to=node, sess=sess, room=self.room, now=self._wall())
            await self._emit(joined)
        logger.info("Desktop Link session with %s", node)
        self._notify("session", node)
        if self._desktop_stop_pending:  # first in the session: the laptop goes read-only before anything else
            if env.body.get("mode") in STOPPED_MODES:
                self._desktop_stop_done("hello")
            elif not self._desktop_stop_sent_since_hello():  # (a run_call may have sent it meanwhile)
                await self._send_desktop_stop()
        await self.ping()  # K-70: once right after each handshake, so a new session is measured at once
        if not self._sync_sent:
            sync = self.signer.build("sync_request", {}, to=node, sess=sess, now=self._wall())
            self._sync_sent = await self._emit(sync)
        await self._flush_controls()

    async def _flush_controls(self) -> None:
        """The controls that waited for a session (stop_all), once and in order: at a handshake, and before an exec
        (run_call), so a stop said before a call always reaches the laptop before that call and never ends it. A
        stop_all is dropped when nothing it could stop still runs (_stop_all_needed, after the hello's WAL report).
        On the link loop; a flush in progress is waited for, so an exec never overtakes its stop_all."""
        if self._controls_lock is None:
            self._controls_lock = asyncio.Lock()
        lock = self._controls_lock
        if not self._pending_controls and not lock.locked():
            return
        async with lock:
            controls, self._pending_controls = self._pending_controls, []
            for kind in dict.fromkeys(controls):
                if kind == "stop_all" and not self._stop_all_needed():
                    logger.info("Desktop control stop_all dropped: the laptop reports that nothing it could stop "
                                "runs")
                    continue
                await self._send_control(kind)

    def _reconcile(self, report: Any) -> None:
        """The hello's WAL report for the calls this side still waits for or reconciles. The report lists ended calls
        only (completed, interrupted, never_started, start_window_missed): a call it leaves out may still run."""
        if not isinstance(report, list):
            return
        for entry in report:
            if not isinstance(entry, Mapping):
                continue
            call_id, state = str(entry.get("call_id") or ""), entry.get("state")
            call = self._calls.get(call_id)
            if call is not None:
                call.push("wal", state)
                continue
            item = self._unresolved.get(call_id)
            if item is None:
                continue
            if state in ("never_started", "start_window_missed"):
                del self._unresolved[call_id]
                self._late(item, call_id=call_id, tool=item.request.tool, reason=item.reason, state=NOT_STARTED)
            elif state == "interrupted":
                del self._unresolved[call_id]
                self._late(item, call_id=call_id, tool=item.request.tool, reason=item.reason, state="interrupted")
            elif state == "completed" and not item.ended:
                # it ended (any status: done, failed, cancelled or timed out), so nothing is left to stop; its
                # result_resend normally follows. Without one (the outbox entry expired, or another process
                # acknowledged it) its outcome is not known, and it is handed on as such, never as done.
                item.ended = True
                if self.loop is not None:
                    self.loop.call_later(self.timers.completed_grace_s, self._completed_without_result, call_id)

    def _completed_without_result(self, call_id: str) -> None:
        """No result_resend followed the WAL's 'completed': an end whose outcome is not known (state 'completed')."""
        item = self._unresolved.pop(call_id, None)
        if item is not None:
            self._late(item, call_id=call_id, tool=item.request.tool, reason=item.reason, state="completed")

    def _on_ack(self, env: VerifiedEnvelope, body: Mapping[str, Any]) -> None:
        call_id = str(env.call_id)
        call = self._calls.get(call_id)
        if call is not None:
            call.push("ack", body)
            return
        item = self._unresolved.get(call_id)
        if item is not None and body.get("accepted") is False:  # e.g. our late cancel refused it: it never ran
            del self._unresolved[call_id]
            self._late(item, call_id=call_id, tool=item.request.tool, reason=item.reason, state=NOT_STARTED)

    async def _on_result(self, env: VerifiedEnvelope, body: Mapping[str, Any]) -> None:
        """result / result_resend: acknowledged, deduped by call_id (SeenStore, 24 h), then handed to the waiting
        call, to reconciliation, or (a call this process never waited for: an agent restart) to the late
        listeners once. A result proving its call never ran (not_started, effect none) is not recorded: the same
        call_id runs after an approval (the APPROVAL_REQUIRED re-send), and that run's result must still count as
        new, also in the next process after an agent restart."""
        call_id = str(env.call_id)
        inner = body.get("result") if env.typ == "result_resend" else body
        inner = inner if isinstance(inner, Mapping) else {}
        await self._send_result_ack(env)
        never_ran = inner.get("status") == "not_started" and inner.get("effect") == "none"
        new = False
        if not never_ran:
            try:
                new = await self.seen.acheck_and_add(dp.KIND_CALL_ID, call_id)
            except Exception:  # pylint: disable=broad-exception-caught  # a store that cannot answer: no dedupe
                logger.warning("Desktop result %s: the seen store did not answer", call_id)
                new = True
        call = self._calls.get(call_id)
        if call is not None:
            call.push("result", inner)
            return
        item = self._unresolved.pop(call_id, None)
        if item is not None:
            self._late(item, call_id=call_id, tool=item.request.tool, reason=item.reason, state=RESULT, body=inner)
            return
        if new:
            self._late(None, call_id=call_id, tool=str(env.header.get("tool") or ""), reason="restart",
                       state=RESULT, body=inner)
        else:
            logger.info("Desktop result %s %s; acknowledged only", call_id,
                        "of a call that never ran" if never_ran else "seen before")

    # --- jarvis_delivery's DesktopSender (C-113) -------------------------------------------------------------

    def submit(self, op: str, args: dict[str, Any]) -> None:
        """Thread-safe and non-blocking: one agent-signed pc.fetch or pc.open_url with exactly its PC_OPERATIONS
        fields. Raises jarvis_delivery.DeliveryError (an OSError) when it cannot be sent; an end other than a success
        reaches the 'late' listeners (reason 'delivery'), and so does the real outcome of one kept open (unknown,
        timed out or stopped) once it is known (_delivery_done)."""
        from services.utils import jarvis_delivery as delivery  # pylint: disable=import-outside-toplevel

        spec = _PC_OPS.get(op)
        if op not in _DELIVERY_OPS or spec is None or not isinstance(args, Mapping) or \
                set(args) != {name for name, _annotation, _default in spec.params}:
            raise delivery.DeliveryError(delivery.BAD_ARGS)
        code = self.fast_refusal()
        if code == DESKTOP_OFFLINE:
            raise delivery.DeliveryError(delivery.DESKTOP_OFFLINE)
        if code is not None:
            raise delivery.DeliveryError(delivery.DELIVERY_FAILED)
        request = CallRequest(tool=op, args=dict(args), param_schema_sha256=spec.manifest_entry()["param_schema_sha256"],
                              timeout_s=spec.timeout, origin=origin_for(self.room, "internal"),
                              label={"delivery": op})
        future = self.submit_call(request)
        future.add_done_callback(lambda done, req=request: self._delivery_done(req, done))

    def _delivery_done(self, request: CallRequest, future: concurrent.futures.Future[CallOutcome]) -> None:
        """A delivery's outcome for the 'late' listeners (desktop_proxy announces it): nothing for a success, and
        nothing yet for a call kept open (unknown, timed out, or stopped by Sir while it was in flight: it may have
        opened), whose late outcome tells. Every other end goes on now with its state and effect: a failure, or (one
        that may have run, effect 'possible') an open whose outcome is not known."""
        try:
            outcome = future.result()
        except BaseException as exc:  # pylint: disable=broad-exception-caught  # a cancelled or broken wait
            logger.warning("Delivery %s: no outcome (%s)", request.tool, type(exc).__name__)
            return
        ok = outcome.kind == RESULT and isinstance(outcome.result, Mapping) and \
            outcome.result.get("status") in ("success", "ok")
        if ok:
            logger.info("Delivery %s done on the laptop", request.tool)
            return
        if outcome.kept_open:
            return  # reconciled later: the late listeners get its real outcome (C-119 review: a stop is no failure)
        logger.warning("Delivery %s not done: %s", request.tool, outcome.code or outcome.kind)
        self._notify("late", LateOutcome(call_id=request.call_id, tool=request.tool, reason="delivery",
                                         state=outcome.kind, result=outcome.k12(request.tool),
                                         status=outcome.status, effect=outcome.effect, label=dict(request.label)))


def _run_listener(callback: Callable[[Any], Any], payload: Any) -> None:
    try:
        result = callback(payload)
        if inspect.isawaitable(result):
            asyncio.ensure_future(result)
    except Exception:  # pylint: disable=broad-exception-caught  # a listener never breaks the link
        logger.exception("Desktop Link listener failed")


# ---------------------------------------------------------------- the process's link


_current: DesktopLink | None = None
_current_lock = threading.Lock()
_key_problem_logged = False


def current() -> DesktopLink | None:
    """This process's link, or None before ensure_link(), after retire() or without usable keys."""
    return _current


def set_current(link: DesktopLink | None) -> DesktopLink | None:
    """Replaces this process's link (tests); returns the previous one."""
    global _current  # pylint: disable=global-statement
    with _current_lock:
        previous, _current = _current, link
    return previous


def seen_path() -> Path:
    """conversations/desktop_seen.db under the project root (the jarvis_agent_conversations volume, K-75)."""
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

    return Path(config.project_root) / "conversations" / SEEN_FILE


def ensure_link(*, room: str, job_id: str, svc: bool = False, environ: Mapping[str, str] | None = None,
                transport_factory: Callable[[], LinkTransport] | None = None) -> DesktopLink | None:
    """The process's link: built and started on its own loop on the first call, registered as jarvis_delivery's
    sender; later calls return it until retire() (the next call then builds a new one). None (logged once, names
    only) without usable keys: laptop calls then answer DESKTOP_OFFLINE. A pending desktop_stop is kept in
    conversations/desktop_stop.json next to the seen store, so it outlives the job process (see the module
    docstring). transport_factory replaces the Socket.IO transport (tests)."""
    global _current, _key_problem_logged  # pylint: disable=global-statement
    from services.utils import jarvis_delivery  # pylint: disable=import-outside-toplevel
    from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

    with _current_lock:
        if _current is not None:
            return _current
        keys, why = load_keys(environ)
        if keys is None:
            if not _key_problem_logged:
                _key_problem_logged = True
                logger.error("Desktop Link not started: %s; laptop tools answer DESKTOP_OFFLINE", why)
            return None
        peer_id = peer_id_for(room, svc=svc)
        path = seen_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            seen = dp.SeenStore(path, namespace=peer_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # no store, no link (fail closed)
            logger.error("Desktop Link not started: its seen store cannot be opened (%s)", type(exc).__name__)
            return None
        url = str(config.bridge_url).rstrip("/")
        link = DesktopLink(keys=keys, peer_id=peer_id, job_id=job_id, room=room, seen=seen,
                           transport_factory=transport_factory or (lambda: SocketIOTransport(url, SOCKETIO_PATH)),
                           stop_path=path.parent / STOP_FILE)
        _current = link
    link.start_in_thread()
    with _current_lock:  # under the lock retire() takes: a link retired meanwhile never becomes the sender
        if _current is link:
            jarvis_delivery.set_desktop_sender(link)
    logger.info("Desktop Link started as %s for room %s", link.ident, link.room or "-")
    return link


def retire(link: DesktopLink | None) -> bool:
    """Stops link for good as its job ends (C-119 review; desktop_proxy.end_job, called by agent_runner before the job
    process drains): close() from any thread without waiting, so it never reconnects as the room's peer, which would
    push the next job's link off the bridge (one socket per peer) and could acknowledge that job's results. When it is
    this process's link it is forgotten as that and as jarvis_delivery's sender, so a later job of this process
    builds its own. Idempotent; False without a link."""
    global _current  # pylint: disable=global-statement
    if link is None:
        return False
    from services.utils import jarvis_delivery  # pylint: disable=import-outside-toplevel

    with _current_lock:
        if _current is link:
            _current = None
        if jarvis_delivery.desktop_sender() is link:
            jarvis_delivery.set_desktop_sender(None)
    link.close()
    logger.info("Desktop Link %s retired: it never reconnects", getattr(link, "ident", "-"))
    return True
