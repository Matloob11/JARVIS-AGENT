"""
# services/utils/jarvis_bridge.py
Agent -> UI bridge notifications (POST /notify, contract C-NOTIFY-1 / K-39).

Agent link v2 (AGENT_TOKEN and BRIDGE_HMAC_SECRET both set): every event is serialized once and exactly
those bytes are sent and signed (X-Agent-Token, -Timestamp, -Nonce, -Signature; signed_headers()).
Retried types (CRITICAL_TYPES) that fail are retried from a bounded per-event-loop queue. Order is kept per key
(K-39 v10, C-64): updates of one message, confirmation or tool call (and each settings or heartbeat snapshot) go out
one at a time in the order they were sent, while different keys go out side by side; and while older events wait
for a retry, or a 429's Retry-After window is open, a new one queues behind them.
Without those keys the legacy X-Vortex headers are sent as before, with one WARNING per process.
UI log lines do not come through here: jarvis_logger's shipper thread posts them as vortex_log_batch with
post_notify_blocking() (C-64).
"""
import asyncio
import contextlib
import functools
import json
import math
import secrets
import threading
import time
import uuid
import weakref
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from httpx import create_ssl_context as _create_ssl_context
from typing_extensions import TypedDict

from services.utils.jarvis_config import config
from services.utils.jarvis_health import health_monitor
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import security_manager, sign_notify

logger = setup_logger("JARVIS-BRIDGE")



@dataclass
class _LoopHTTP:
    """One event loop's HTTP client, lock and telemetry semaphore (C-39 step 2, K-57)."""
    client: httpx.AsyncClient | None = None
    lock: asyncio.Lock | None = None
    semaphore: asyncio.Semaphore | None = None


# C-39 step 2 (K-57): on Windows every LiveKit job runs on its own event loop in a thread. The old module globals
# followed "the last loop", so with two job loops alive every switch dropped the client and built a new one:
# httpx's ssl.create_default_context blocked the loop for 250-570 ms (about 98% of the 3,456 EVENT_LOOP_STALL lines
# of 06-08 Oct) and the dropped clients were never closed. Each loop now keeps its own client, lock and semaphore
# (a new loop never inherits another loop's semaphore permits) and the SSL context is built once per process. A job
# drops its loop's entry and closes the client at its end (aclose_loop_client). livekit-agents can stop a job's loop
# without closing it before that runs, and a client's pooled connection references its loop, so the value would keep
# the weak key alive: every lookup therefore also drops the entries of loops that have ended (closed or no longer
# running, the rule the retry queue uses), which lets the loop and its sockets be collected.
_http_by_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopHTTP]" = weakref.WeakKeyDictionary()
_http_registry_lock = threading.Lock()
_ssl_context: Any = None

NOTIFY_TIMEOUT_S = 2.0
CONFIRM_NOTIFY_TIMEOUT_S = 5.0  # K-14: each ConfirmFlow try takes at most 5 s
# K-39: these types are retried (up to RETRY_MAX times, backoff 1 -> 30 s) from a queue of at most
# RETRY_QUEUE_MAX events; every other type is sent once and a failure is logged at DEBUG.
CRITICAL_TYPES = frozenset({"transcription", "confirm_request", "settings_sync", "tool_sync",
                            "agent_heartbeat"})
RETRY_QUEUE_MAX = 500
RETRY_MAX = 5
RETRY_FIRST_S = 1.0
RETRY_MAX_S = 30.0
NOTIFY_MAX_BYTES = 256 * 1024  # the bridge answers 413 above this, so a larger body is never queued
_WARN_EVERY_S = 60.0
LOG_BATCH_MAX = 50  # K-40 v10: items in one vortex_log_batch


class BridgePayload(TypedDict):
    """Strict payload structure for UI notifications."""
    type: str
    payload: Any


def _loop_http() -> _LoopHTTP:
    """The running event loop's entry (created on first use); entries of ended loops are dropped first."""
    loop = asyncio.get_running_loop()
    with _http_registry_lock:
        for other in [other for other in _http_by_loop if other is not loop]:
            if other.is_closed() or not other.is_running():
                _http_by_loop.pop(other, None)  # its client can no longer be awaited from any loop
        state = _http_by_loop.get(loop)
        if state is None:
            state = _http_by_loop[loop] = _LoopHTTP()
    return state


def _get_lock() -> asyncio.Lock:
    """This event loop's lock, created inside the loop."""
    state = _loop_http()
    if state.lock is None:
        state.lock = asyncio.Lock()
    return state.lock


def _get_semaphore() -> asyncio.Semaphore:
    """This event loop's telemetry semaphore (20 permits), created inside the loop."""
    state = _loop_http()
    if state.semaphore is None:
        state.semaphore = asyncio.Semaphore(20)
    return state.semaphore


def _shared_ssl_context() -> Any:
    """httpx's default verifying SSL context, built once per process (building it costs 250-570 ms)."""
    global _ssl_context  # pylint: disable=global-statement
    with _http_registry_lock:
        if _ssl_context is None:
            _ssl_context = _create_ssl_context()
        return _ssl_context


def _get_headers(signature: str) -> dict[str, str]:
    """Helper to generate signed headers with guaranteed string types."""
    token = str(config.security_token) if config.security_token else "local"
    return {
        "X-Vortex-Token": token,
        "X-Vortex-Signature": signature,
    }


async def get_http_client() -> httpx.AsyncClient:
    """This event loop's shared httpx client (C-39 step 2, K-57): built once per loop with the process-wide SSL
    context, rebuilt only after it was closed. Another loop never replaces it."""
    state = _loop_http()
    if state.client is None or state.client.is_closed:
        state.client = httpx.AsyncClient(timeout=10.0, verify=_shared_ssl_context())
    return state.client


async def aclose_loop_client() -> None:
    """Forgets this event loop's entry (client, lock, semaphore) and closes its client: the job's end. A later call on
    this loop starts a new entry."""
    loop = asyncio.get_running_loop()
    with _http_registry_lock:
        state = _http_by_loop.pop(loop, None)
    client = state.client if state is not None else None
    if client is not None and not client.is_closed:
        await client.aclose()


# ---------------------------------------------------------------- agent link v2 (C-NOTIFY-1)

_link_logged: set[bool] = set()


def agent_link_v2() -> bool:
    """
    True when AGENT_TOKEN and BRIDGE_HMAC_SECRET are both set: notifies are signed (C-NOTIFY-1) and the UI
    socket authenticates as role 'agent' (C-SOCK-1). Otherwise both keep the legacy token, and one WARNING
    per process names the missing variables (never a value).
    """
    missing = [name for name, value in (("AGENT_TOKEN", config.agent_token),
                                        ("BRIDGE_HMAC_SECRET", config.bridge_hmac_secret))
               if not (isinstance(value, str) and value.strip())]
    enabled = not missing
    if enabled not in _link_logged:
        _link_logged.add(enabled)
        if enabled:
            logger.info("Agent link v2: signed notify, UI socket as role 'agent'")
        else:
            logger.warning("Agent link v2 is off (%s not set): bridge notify and the UI socket use the "
                           "legacy token", " and ".join(missing))
    return enabled


def encode_event(event_type: str, payload: Any) -> bytes:
    """The notify body exactly as it is sent and signed: compact JSON of {type, payload}, non-ASCII kept as
    UTF-8 (a lone surrogate becomes '?'). Raises TypeError/ValueError for a payload JSON cannot hold,
    NaN and infinities included: the bridge would store and emit them, and the UI cannot parse them."""
    body: BridgePayload = {"type": event_type, "payload": payload}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8", "replace")


def signed_headers(raw: bytes) -> dict[str, str]:
    """
    C-NOTIFY-1 headers for exactly the body bytes raw: X-Agent-Token, X-Agent-Timestamp (unix seconds, now),
    a fresh 32-hex-char X-Agent-Nonce and X-Agent-Signature = hex HMAC-SHA256(BRIDGE_HMAC_SECRET,
    f'{ts}.{nonce}.' + raw). Build new headers for every request: the bridge refuses a nonce it has seen
    (409) and a timestamp more than 60 s off (401). Also for POST /livekit/ensure (body b'{}').
    Raises RuntimeError when AGENT_TOKEN or BRIDGE_HMAC_SECRET is not set.
    """
    token = (config.agent_token or "").strip()
    if not token or not (config.bridge_hmac_secret or "").strip():
        raise RuntimeError("AGENT_TOKEN and BRIDGE_HMAC_SECRET are needed for signed bridge requests")
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return {
        "Content-Type": "application/json",
        "X-Agent-Token": token,
        "X-Agent-Timestamp": ts,
        "X-Agent-Nonce": nonce,
        "X-Agent-Signature": sign_notify(ts, nonce, bytes(raw)),
    }


# Outcome of one signed POST.
_DELIVERED = "delivered"
_RETRY = "retry"                    # the bridge cannot have handled it: retry any critical type
_RETRY_IF_IDEMPOTENT = "uncertain"  # it may have: retry only what the bridge keeps once (_idempotent)
_FAILED = "failed"                  # a retry would get the same answer

_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError,
                    httpx.WriteError, httpx.WriteTimeout)
_PERMANENT_ERRORS = (httpx.UnsupportedProtocol, httpx.LocalProtocolError, httpx.InvalidURL)


def _error_outcome(exc: Exception) -> str:
    if isinstance(exc, _PERMANENT_ERRORS):
        return _FAILED
    if isinstance(exc, _NOT_SENT_ERRORS):
        return _RETRY
    return _RETRY_IF_IDEMPOTENT  # a read timeout or a dropped connection: the bridge may have it


def _status_outcome(status: int) -> str:
    if 200 <= status < 300 or status == 409:  # 409: this request's nonce was already accepted
        return _DELIVERED
    if status in (429, 502, 503):  # throttled, or the proxy found no bridge behind it
        return _RETRY
    if status >= 500:
        return _RETRY_IF_IDEMPOTENT
    return _FAILED  # 400, 401 (bad token, or a clock more than 60 s off), 403, 413 ...


def _retryable(outcome: str, idempotent: bool) -> bool:
    return outcome == _RETRY or (outcome == _RETRY_IF_IDEMPOTENT and idempotent)


def _idempotent(event_type: str, payload: Any) -> bool:
    """The bridge keeps one copy of these however often they arrive: a message or confirmation by its id,
    a settings or heartbeat snapshot. A tool event would be logged twice, so it is never sent again once
    the bridge may have it."""
    if event_type in ("settings_sync", "agent_heartbeat"):
        return True
    if event_type in ("transcription", "confirm_request"):
        return isinstance(payload, dict) and bool(payload.get("id"))
    return False


def _notify_url() -> str:
    return f"{config.bridge_url.rstrip('/')}/notify"


def _retry_after_s(response: Any) -> float | None:
    """A 429 (or 503) answer's Retry-After in seconds, at most RETRY_MAX_S; None without a usable one."""
    try:
        seconds = float(response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, RETRY_MAX_S)


def _response_outcome(response: Any) -> tuple[str, str, float | None]:
    """(outcome, short reason, Retry-After) of one /notify answer. The reason holds a status and the C-NOTIFY-1 error
    code, never a header or the body."""
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return _DELIVERED, "", None  # a stand-in client that reports no status
    outcome = _status_outcome(status)
    if outcome == _DELIVERED:
        return outcome, "", None
    try:
        error = response.json().get("error") or ""
    except Exception:  # pylint: disable=broad-exception-caught
        error = ""
    retry_after = _retry_after_s(response) if status in (429, 503) else None
    return outcome, f"HTTP {status} {str(error)[:40]}".strip(), retry_after


async def _post_signed(raw: bytes) -> tuple[str, str, float | None]:
    """One signed POST /notify of raw (new headers every time). Returns (outcome, short reason, Retry-After); never
    raises except CancelledError. The reason holds a status, a C-NOTIFY-1 error code or an exception name,
    never a header or the body."""
    try:
        headers = signed_headers(raw)
    except RuntimeError as exc:
        return _FAILED, str(exc), None
    try:
        async with _get_semaphore():  # first, as on the legacy path
            client = await get_http_client()
            response = await client.post(_notify_url(), content=raw, headers=headers,
                                         timeout=NOTIFY_TIMEOUT_S)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error_outcome(exc), f"{type(exc).__name__}: {str(exc)[:160]}", None
    return _response_outcome(response)


def _request_parts(raw: bytes) -> tuple[str, dict[str, str]]:
    """The URL and headers of one POST /notify of exactly raw, with the credential notify_event would use: the
    C-NOTIFY-1 v2 headers (built anew for every attempt) with the agent link v2 keys, else the legacy X-Vortex
    headers (HMAC over the sorted JSON, as the bridge checks it). Raises RuntimeError when v2 headers cannot be
    built."""
    if agent_link_v2():
        return _notify_url(), signed_headers(raw)
    signature = security_manager.generate_signature(json.dumps(json.loads(raw), sort_keys=True))
    return _notify_url(), {"Content-Type": "application/json", **_get_headers(signature)}


def post_notify_blocking(event_type: str, payload: Any, *, client: httpx.Client,
                         timeout: float = NOTIFY_TIMEOUT_S) -> tuple[str, str, float | None]:
    """
    One POST /notify from a plain thread (jarvis_logger's UI log shipper, C-64) with its own httpx.Client: the same
    body (encode_event) and credentials as notify_event, sent once. Returns (outcome, short reason, Retry-After);
    outcome is 'delivered', 'retry' (the bridge cannot have it: connect error, 429, 502, 503), 'uncertain' (it may
    have it: read timeout, 500, 504) or 'failed' (400, 401, 403, 413, a payload JSON cannot hold, missing keys).
    Never raises.
    """
    try:
        raw = encode_event(event_type, payload)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # NaN, RecursionError ...
        return _FAILED, type(exc).__name__, None
    if len(raw) > NOTIFY_MAX_BYTES:
        return _FAILED, "body over the bridge limit", None
    try:
        url, headers = _request_parts(raw)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # missing keys, a signer that refuses
        return _FAILED, f"{type(exc).__name__}: {str(exc)[:160]}", None
    try:
        response = client.post(url, content=raw, headers=headers, timeout=timeout)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _error_outcome(exc), f"{type(exc).__name__}: {str(exc)[:160]}", None
    return _response_outcome(response)


# K-39 v10: the retried types whose order matters per key: updates of one message, confirmation or tool call by id,
# and the settings and heartbeat snapshots by type. Everything else has no key and is never held back by another.
_ORDER_BY_ID = frozenset({"transcription", "confirm_request", "tool_sync"})
_ORDER_BY_TYPE = frozenset({"settings_sync", "agent_heartbeat"})


def _order_key(event_type: str, payload: Any) -> str | None:
    if event_type in _ORDER_BY_TYPE:
        return event_type
    if event_type in _ORDER_BY_ID and isinstance(payload, dict):
        item_id = payload.get("id")
        if isinstance(item_id, (str, int)) and not isinstance(item_id, bool) and item_id != "":
            return f"{event_type}:{item_id}"
    return None


@dataclass(eq=False)
class _Queued:
    """One critical event in the retry queue: its exact body, re-signed on every attempt."""
    event_type: str
    raw: bytes
    idempotent: bool
    attempts: int = 1  # failed sends so far: 1 after a failed direct send, 0 when it queued unsent


@dataclass(eq=False)
class _KeyLock:
    """The lock of one order key and how many sends use it (the entry goes once none does)."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


@dataclass(eq=False)
class _RetryQueue:
    """The critical events of one event loop: the retry queue (oldest first, at most RETRY_QUEUE_MAX events), the
    per-key locks that send the events of one key one at a time (asyncio.Lock is FIFO), and the end of an open
    Retry-After window."""
    items: deque[_Queued] = field(default_factory=deque)
    task: "asyncio.Task[None] | None" = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    wait_s: float = RETRY_FIRST_S  # before the next attempt; 0 right after a delivery
    in_flight: _Queued | None = None
    locks: dict[str, _KeyLock] = field(default_factory=dict)
    hold_until: float = float("-inf")  # time.monotonic() until which new critical events queue unsent (429)
    users: int = 0  # critical sends of this loop in progress


# One queue per event loop (on Windows LiveKit runs each job on a new loop in a thread). The values refer
# to their loop (tasks, locks), so entries are dropped explicitly: once nothing uses them, or once their
# loop has ended. The registry never keeps a loop alive.
_queues: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _RetryQueue]" = (
    weakref.WeakKeyDictionary())
_state_lock = threading.Lock()
_warn_state: dict[str, float] = {"last": float("-inf"), "dropped": 0}


def _forget_ended_loops(current: asyncio.AbstractEventLoop) -> int:
    """Drops the queues of event loops that have ended (closed, or no longer running: a LiveKit job thread
    leaves its loop without closing it); nothing could send their events again. Returns how many events
    went with them. The caller holds _state_lock."""
    lost = 0
    for loop in [loop for loop in _queues if loop is not current]:
        if loop.is_closed() or not loop.is_running():
            lost += len(_queues.pop(loop).items)
    return lost


def _report_lost(count: int, why: str) -> None:
    if count:
        logger.warning("Bridge notify: %d queued critical event(s) dropped: %s", count, why)


def _queue_for(loop: asyncio.AbstractEventLoop) -> _RetryQueue:
    """loop's queue, registered on first use."""
    with _state_lock:
        lost = _forget_ended_loops(loop)
        queue = _queues.get(loop)
        if queue is None:
            queue = _queues[loop] = _RetryQueue()
    _report_lost(lost, "their event loop ended")
    return queue


def _loop_queue(create: bool) -> _RetryQueue | None:
    """The running event loop's retry queue; None outside a loop, or when it has none and not create."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if create:
        return _queue_for(loop)
    with _state_lock:
        lost = _forget_ended_loops(loop)
        queue = _queues.get(loop)
    _report_lost(lost, "their event loop ended")
    return queue


def _release_if_idle(loop: asyncio.AbstractEventLoop, queue: _RetryQueue) -> None:
    """Drops loop's entry once nothing uses it (no event queued, no flusher, no critical send running);
    the next critical event of that loop registers a new one."""
    with _state_lock:
        if (_queues.get(loop) is queue and not queue.users and not queue.items
                and (queue.task is None or queue.task.done())):
            del _queues[loop]


def _flusher_done(queue: _RetryQueue, task: "asyncio.Task[None]") -> None:
    # Only the loop's shutdown cancels the flusher (asyncio.run cancels every task): its events are lost.
    if task.cancelled() and queue.task is task and queue.items:
        lost = len(queue.items)
        queue.items.clear()
        _report_lost(lost, "their event loop is shutting down")
    _release_if_idle(task.get_loop(), queue)


def retry_queue_size() -> int:
    """Critical events of the running event loop that wait for a retry (0 outside a loop)."""
    queue = _loop_queue(create=False)
    return len(queue.items) if queue is not None else 0


def wake_retry_queue() -> None:
    """Retries the running loop's queued events now instead of after the backoff: a notify just went
    through, or the UI socket is connected again. Does nothing outside a loop or with nothing queued."""
    queue = _loop_queue(create=False)
    if queue is not None and queue.items:
        queue.wake.set()


def _report_failure(event_type: str, reason: str, queued: int, dropped: int = 0) -> None:
    """A failed critical event: one WARNING a minute (with the events dropped since the last one), the
    rest at DEBUG."""
    now = time.monotonic()
    with _state_lock:
        _warn_state["dropped"] += dropped
        due = now - _warn_state["last"] >= _WARN_EVERY_S
        if due:
            _warn_state["last"] = now
            dropped_total, _warn_state["dropped"] = int(_warn_state["dropped"]), 0
    if due:
        logger.warning("Bridge notify failing (%s, %s): %d critical event(s) wait for a retry, %d dropped",
                       event_type, reason, queued, dropped_total)
    else:
        logger.debug("Bridge notify failed (%s, %s): %d queued%s", event_type, reason, queued,
                     ", 1 dropped" if dropped else "")


def _discard(queue: _RetryQueue, item: _Queued) -> None:
    if queue.items and queue.items[0] is item:
        queue.items.popleft()
        return
    try:
        queue.items.remove(item)
    except ValueError:  # already evicted to make room
        pass


def _hold(queue: _RetryQueue, retry_after: float | None) -> None:
    """A 429 with Retry-After: new critical events queue behind until it passes, and it is the flusher's next
    wait (at most RETRY_MAX_S)."""
    if retry_after is None:
        return
    wait = min(max(retry_after, 1.0), RETRY_MAX_S)  # the bridge sends whole seconds >= 1
    queue.hold_until = max(queue.hold_until, time.monotonic() + wait)
    queue.wait_s = wait


def _holding(queue: _RetryQueue) -> bool:
    return time.monotonic() < queue.hold_until


@contextlib.asynccontextmanager
async def _in_order(queue: _RetryQueue, key: str | None) -> AsyncIterator[None]:
    """Holds the order lock of key (none for None): events of one key go out one at a time, in the order they came."""
    if key is None:
        yield
        return
    entry = queue.locks.get(key)
    if entry is None:
        entry = queue.locks[key] = _KeyLock()
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if not entry.users and queue.locks.get(key) is entry:
            del queue.locks[key]


def _enqueue(queue: _RetryQueue, item: _Queued, reason: str | None) -> None:
    """Puts a critical event at the end of its loop's queue: after its send failed (reason), or unsent
    behind older waiting events (reason None). When the queue is full the oldest waiting event makes room
    (never the one being sent right now)."""
    dropped = 0
    while len(queue.items) >= RETRY_QUEUE_MAX:
        oldest = next((queued for queued in queue.items if queued is not queue.in_flight), None)
        if oldest is None:  # only the event in flight is left: this one cannot wait
            _report_failure(item.event_type, "retry queue full", len(queue.items), dropped + 1)
            return
        _discard(queue, oldest)
        dropped += 1
    queue.items.append(item)
    if reason is not None or dropped:
        _report_failure(item.event_type, reason or "retry queue full", len(queue.items), dropped)
    else:
        logger.debug("Bridge notify: %s waits behind %d older critical event(s)", item.event_type,
                     len(queue.items) - 1)
    if queue.task is None or queue.task.done():
        # The first wait: an open Retry-After window's rest, else the backoff's first step.
        queue.wait_s = max(RETRY_FIRST_S, min(queue.hold_until - time.monotonic(), RETRY_MAX_S))
        queue.task = asyncio.get_running_loop().create_task(_flush(queue), name="bridge-notify-retry")
        queue.task.add_done_callback(functools.partial(_flusher_done, queue))


async def _wait_before_retry(queue: _RetryQueue) -> None:
    """Waits queue.wait_s seconds, or less when wake_retry_queue() is called meanwhile."""
    try:
        await asyncio.wait_for(queue.wake.wait(), timeout=queue.wait_s)
    except TimeoutError:
        pass


async def _flush(queue: _RetryQueue) -> None:
    """Sends the queued events oldest first, one at a time, until none is left. The wait between failed
    attempts grows 1, 2, 4, 8, 16, 30, 30 ... s and goes back to 0 after a delivery; a 429's Retry-After (at most
    30 s) is the next wait instead. Each event gets its first send and at most RETRY_MAX retries."""
    delivered = 0
    try:
        while queue.items:
            if queue.wait_s > 0:
                await _wait_before_retry(queue)
            if not queue.items:
                break
            item = queue.items[0]
            queue.in_flight = item
            try:
                outcome, reason, retry_after = await _post_signed(item.raw)
            finally:
                queue.in_flight = None
                queue.wake.clear()
            if outcome == _DELIVERED:
                _discard(queue, item)
                delivered += 1
                queue.wait_s = 0.0
                continue
            item.attempts += 1
            if not _retryable(outcome, item.idempotent) or item.attempts > RETRY_MAX:
                _discard(queue, item)
                _report_failure(item.event_type, reason, len(queue.items), 1)
            else:
                _report_failure(item.event_type, reason, len(queue.items))
            if retry_after is not None:
                _hold(queue, retry_after)
            else:
                queue.wait_s = min(max(queue.wait_s * 2, RETRY_FIRST_S), RETRY_MAX_S)
    finally:
        if delivered:
            logger.info("Bridge notify: %d queued critical event(s) delivered after a retry", delivered)


async def _notify_critical(event_type: str, payload: Any, raw: bytes) -> None:
    """Sends a critical event in order (K-39 v10): events of one key (_order_key: a message, confirmation or tool
    call id, or a snapshot type) go out one at a time, while other keys go out side by side; and while older events
    wait for a retry, or a 429's Retry-After window is open, a new one queues behind them instead of overtaking them
    (the bridge and the UI keep the last event per id, so a late retry would roll the state back)."""
    loop = asyncio.get_running_loop()
    queue = _queue_for(loop)
    queue.users += 1
    try:
        idempotent = _idempotent(event_type, payload)
        async with _in_order(queue, _order_key(event_type, payload)):
            if queue.items or _holding(queue):
                if len(raw) > NOTIFY_MAX_BYTES:
                    _report_failure(event_type, "body over the bridge limit", len(queue.items), 1)
                else:
                    _enqueue(queue, _Queued(event_type, raw, idempotent, attempts=0), None)
                return
            outcome, reason, retry_after = await _post_signed(raw)
            if outcome == _DELIVERED:
                return
            _hold(queue, retry_after)
            if len(raw) <= NOTIFY_MAX_BYTES and _retryable(outcome, idempotent):
                _enqueue(queue, _Queued(event_type, raw, idempotent), reason)
            else:
                _report_failure(event_type, reason, len(queue.items), 1)
    finally:
        queue.users -= 1
        _release_if_idle(loop, queue)


async def _notify_signed(event_type: str, payload: Any) -> None:
    try:
        raw = encode_event(event_type, payload)
    except Exception as e:  # pylint: disable=broad-exception-caught  # NaN, RecursionError ...: never raise
        logger.debug("Bridge Notification failed for %s: %s", event_type, e)
        return
    if event_type in CRITICAL_TYPES:
        await _notify_critical(event_type, payload, raw)
        return
    outcome, reason, _ = await _post_signed(raw)
    if outcome == _DELIVERED:
        wake_retry_queue()  # the bridge answers again: no need to wait out the backoff
    else:
        logger.debug("Bridge Notification failed for %s: %s", event_type, reason)


async def _notify_legacy(event_type: str, payload: Any) -> None:
    """The pre-v2 notify, unchanged: X-Vortex headers and an HMAC over the sorted JSON, sent once."""
    async with _get_semaphore():
        try:
            client = await get_http_client()
            url = f"{config.bridge_url}/notify"
            payload_data: BridgePayload = {
                "type": event_type,
                "payload": payload,
            }

            # FAANG standard: Guaranteed key ordering for signature stability
            json_payload = json.dumps(payload_data, sort_keys=True)
            signature = security_manager.generate_signature(json_payload)

            headers = _get_headers(signature)
            await client.post(url, json=payload_data, headers=headers, timeout=2.0)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Bridge Notification failed for %s: %s", event_type, e)


async def _notify_with_answer(event_type: str, payload: Any, timeout: float = CONFIRM_NOTIFY_TIMEOUT_S) -> Any:
    """One POST /notify that returns the bridge's parsed answer and raises on any failure (no retry queue: the
    ConfirmFlow broker does its own tries and reads the answer, K-14). Signed with the agent link v2 keys,
    else with the legacy headers, like notify_event."""
    if agent_link_v2():
        raw = encode_event(event_type, payload)
        headers = signed_headers(raw)
        async with _get_semaphore():
            client = await get_http_client()
            response = await client.post(_notify_url(), content=raw, headers=headers, timeout=timeout)
    else:
        body = {"type": event_type, "payload": payload}
        headers = _get_headers(security_manager.generate_signature(json.dumps(body, sort_keys=True)))
        async with _get_semaphore():
            client = await get_http_client()
            response = await client.post(f"{config.bridge_url}/notify", json=body, headers=headers,
                                         timeout=timeout)
    response.raise_for_status()
    return response.json()


async def notify_confirm_request(payload: dict[str, Any]) -> Any:
    """confirm_request for the dashboard's Allow dialog (K-14, C-29); returns the bridge's answer
    {status, ui_clients}. jarvis_confirm's broker uses it once it exists."""
    return await _notify_with_answer("confirm_request", payload)


async def notify_confirm_resolved(payload: dict[str, Any]) -> Any:
    """confirm_resolved {id, allow, reason, result_status?} (K-14, C-29): closes the dialog on every UI."""
    return await _notify_with_answer("confirm_resolved", payload)


async def notify_event(event_type: str, payload: Any) -> None:
    """
    Sends one event to the STONIX UI Bridge (POST /notify, C-NOTIFY-1); never raises. With the agent link
    v2 keys the body is signed as sent, and a failed critical type (CRITICAL_TYPES) goes to the retry queue
    (one WARNING a minute while that fails); critical types reach the bridge in the order they were sent.
    Any other failure is logged at DEBUG. Without the keys the legacy headers are used, as before.
    """
    if agent_link_v2():
        await _notify_signed(event_type, payload)
    else:
        await _notify_legacy(event_type, payload)


async def notify_ui(status: str, payload: dict[str, Any] | None = None) -> None:
    """Sends a notification to the STONIX UI Bridge with heartbeat and optional payload."""
    health_monitor.record_heartbeat("agent_runner")
    if payload:
        await notify_event(status, payload)
    else:
        await notify_event("status", status)


async def notify_vitals(vitals: dict[str, dict[str, int | float]]) -> None:
    """Pushes real-time system vitals to the UI."""
    payload = {
        "cpu": vitals.get("cpu", {}).get("usage", 0),
        "ram": vitals.get("memory", {}).get("percent", 0),
        "disk": vitals.get("disk", {}).get("percent", 0),
    }
    await notify_event("vitals", payload)


async def notify_pc_vitals(vitals: dict[str, Any]) -> None:
    """Sends this laptop's vitals to the UI as vitals_pc (K-40): the PC brain's agent, every 10 s."""
    await notify_event("vitals_pc", dict(vitals))


async def notify_location(location_data: dict[str, Any]) -> None:
    """Pushes real-time location to the UI."""
    await notify_event("location_sync", location_data)


async def notify_log(message: str, category: str = "SYSTEM") -> None:
    """Sends one system activity line to the UI (vortex_log): for single one-off notices only. The agent's log lines
    reach the UI as vortex_log_batch from jarvis_logger's shipper (C-64)."""
    await notify_event("vortex_log", {
        "text": message,
        "category": category,
        "timestamp": time.time(),
    })


async def notify_log_batch(items: Sequence[Mapping[str, Any]], dropped: int = 0, source: str = "agent") -> None:
    """
    One vortex_log_batch (K-40 v10): at most LOG_BATCH_MAX items {text, category, level, ts}, the lines dropped
    before it (items past the cap count as dropped too) and source ('agent', 'svc' or 'desktop'). Sent once and
    never retried, as every log type. For relays (agent-svc's desktop lines, C-125) and tests; the agent's own lines
    go through jarvis_logger's shipper.
    """
    rows = [dict(item) for item in list(items)[:LOG_BATCH_MAX] if isinstance(item, Mapping)]
    extra = len(items) - len(rows)
    await notify_event("vortex_log_batch", {"items": rows, "dropped": max(0, int(dropped)) + extra,
                                            "source": source})


async def notify_voice_match(confidence: float) -> None:
    """Pushes voice biometric match confidence level."""
    await notify_event("voice_id_sync", {
        "confidence": confidence,
        "timestamp": time.time(),
    })


async def notify_thinking(status: str) -> None:
    """Sends a thinking status notification to the UI."""
    await notify_event("thinking", status)


async def notify_transcription(role: str, text: str,
                               msg_id: str | None = None,
                               is_final: bool = True,
                               source: str | None = "voice",
                               turn_id: str | None = None,
                               extra: dict[str, Any] | None = None) -> None:
    """
    Sends a chat message to the STONIX UI Bridge (K-58 CT-VC-MESSAGE, C-MSG-1), which upserts it by id:
    {id, role ('agent' becomes 'assistant'), text, source, final, ts (epoch s), turn_id?, is_final,
    timestamp, **extra}. is_final/timestamp stay for the UIs that still read them. source is 'voice'
    (default), 'text' or 'system' (None: left out, the bridge decides); turn_id names the user message a
    reply answers; extra adds fields (text_native, score, persona) and never replaces one of these.
    """
    now = time.time()
    payload: dict[str, Any] = {
        "id": msg_id or str(uuid.uuid4()),
        "role": "assistant" if role == "agent" else role,
        "text": text,
        "final": is_final,
        "ts": now,
        "is_final": is_final,
        "timestamp": now,
    }
    if source:
        payload["source"] = source
    if turn_id:
        payload["turn_id"] = turn_id
    for key, value in (extra or {}).items():
        payload.setdefault(key, value)
    await notify_event("transcription", payload)


async def notify_memory(content: str) -> None:
    """Sends a new extracted memory to the STONIX UI Bridge."""
    await notify_event("memory_update", {
        "id": str(uuid.uuid4()),
        "content": content,
        "timestamp": time.time(),
    })


async def notify_tool_action(tool_name: str, action_details: str) -> None:
    """Sends a tool execution sync event to the UI."""
    await notify_event("tool_sync", {
        "tool": tool_name,
        "details": action_details,
        "timestamp": time.time(),
    })


async def notify_sim_data(
    records: list[Any],
    phone_number: str = "",
    status: str = "success",
    message: str = "",
) -> None:
    """Pushes SIM lookup results to the UI SimInfoBox in real-time."""
    await notify_event("sim_data_result", {
        "records": records,
        "phone": phone_number,
        "status": status,
        "message": message,
        "timestamp": time.time(),
    })


async def notify_sim_loading(payload: dict[str, Any] | None = None) -> None:
    """Notifies the UI that a SIM data lookup is in progress."""
    await notify_event("sim_data_loading", payload or {})


async def notify_user_speaking(is_speaking: bool) -> None:
    """Notifies the UI that the user has started/stopped speaking."""
    await notify_event("user_speaking_sync", is_speaking)
