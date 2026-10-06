"""
# src/core/bridge_security.py
Agent link v2 checks for the UI bridge (plan task C-14).

- verify_notify_request(): POST /notify in the C-NOTIFY-1 (K-39) order: token 401 bad_token, timestamp
  401 stale, signature 403 bad_signature, nonce 409 replay, size 413 too_large. The legacy X-Vortex
  headers are accepted only while LEGACY_TOKEN_AUTH=1.
- NonceCache: the notify nonces of the last 300 s; the store's seen_ids keep them across a restart.
- TokenBucket: per-socket and per-class rate limits (C-SOCK-1).
- NotifyRateLimiter: POST /notify's three buckets (K-39 v10, C-64): critical 50/s, others 20/s per notify, and
  logs 25 items/s burst 100 charged per vortex_log_batch item (SP-3; K-39 v12 draft in the C-64 NOTE.md), one
  bucket per class for the whole bridge (both listeners, every credential), so a log flood never throttles a
  transcript.
- check_origin(): the Origin (or Referer) of a cookie-authenticated POST must be in ALLOWED_ORIGINS
  (C-AUTH-1; used by the session endpoints of C-19).

No function here logs or returns a secret: errors carry only a short code.
"""

from __future__ import annotations

import hmac
import json
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import ClientDisconnect, Request

from services.utils.jarvis_security import security_manager, verify_notify

NOTIFY_MAX_BYTES = 256 * 1024
# A larger body is never read in full (nginx's default client_max_body_size is 1 MB as well).
NOTIFY_READ_LIMIT = 1024 * 1024
NOTIFY_MAX_SKEW_S = 60
NONCE_TTL_S = 300.0
NONCE_CACHE_MAX = 50_000

AUTH_AGENT = "agent"    # X-Agent-* headers (C-NOTIFY-1 v2)
AUTH_LEGACY = "legacy"  # X-Vortex-Token / X-Vortex-Signature (only while LEGACY_TOKEN_AUTH=1)

_TIMESTAMP = re.compile(r"\d{1,12}")
_NONCE = re.compile(r"[0-9a-fA-F]{16,128}")


class NotifyAuthError(Exception):
    """A refused notify: status is the HTTP code, error the C-NOTIFY-1 reason code (never a value)."""

    def __init__(self, status: int, error: str, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(error)
        self.status = status
        self.error = error
        self.headers = dict(headers or {})


@dataclass(frozen=True)
class NotifyAuth:
    """An accepted notify: which credentials it used and its parsed JSON body."""

    path: str
    data: dict[str, Any]


def same_secret(given: Any, expected: str | None) -> bool:
    """Constant-time equality of a presented credential and a configured one. An unset or empty
    configured value never matches anything."""
    if not expected or not isinstance(given, str) or not given:
        return False
    return hmac.compare_digest(given.encode("utf-8", "replace"), expected.encode("utf-8", "replace"))


class NonceCache:
    """Nonces seen within ttl_s (C-NOTIFY-1 replay check). Bounded: past max_items the oldest go first.
    Only authenticated requests (good token and signature) ever add one."""

    def __init__(self, ttl_s: float = NONCE_TTL_S, max_items: int = NONCE_CACHE_MAX,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = float(ttl_s)
        self.max_items = int(max_items)
        self._clock = clock
        self._expiry: OrderedDict[str, float] = OrderedDict()

    def __len__(self) -> int:
        return len(self._expiry)

    def add(self, nonce: str) -> bool:
        """True the first time nonce is seen within ttl_s; False for a replay."""
        now = self._clock()
        while self._expiry:
            oldest, expires = next(iter(self._expiry.items()))
            if expires > now:
                break
            del self._expiry[oldest]
        if nonce in self._expiry:
            return False
        self._expiry[nonce] = now + self.ttl_s
        while len(self._expiry) > self.max_items:
            self._expiry.popitem(last=False)
        return True


class TokenBucket:
    """rate tokens per second, holding at most capacity (the burst; default one second's worth)."""

    def __init__(self, rate: float, capacity: float | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else max(rate, 1.0))
        self._clock = clock
        self._tokens = self.capacity
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def take(self, tokens: float = 1.0) -> bool:
        """Spends tokens when the bucket holds them; False (nothing spent) when it does not."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def retry_after(self, tokens: float = 1.0) -> float:
        """Seconds until take(tokens) can succeed."""
        self._refill()
        return max(0.0, tokens - self._tokens) / self.rate


# POST /notify rate classes (K-39 v10): the voice path and the agent's control events; the log lines; everything
# else (a missing or unknown type included). user_speaking_sync and job_end are low-volume voice/lifecycle events.
NOTIFY_CLASS_CRITICAL, NOTIFY_CLASS_LOGS, NOTIFY_CLASS_OTHERS = "critical", "logs", "others"
NOTIFY_CRITICAL_TYPES = frozenset({"transcription", "status", "thinking", "confirm_request", "settings_sync",
                                   "agent_heartbeat", "user_speaking_sync", "job_end"})
NOTIFY_LOG_TYPES = frozenset({"vortex_log", "vortex_log_batch"})
# class: (tokens a second, burst). critical and others charge one token per notify. The logs class charges one
# token per item a vortex_log_batch will store (C-64 review SP-3: 10 posts/s x 50 items was a 500 stored-rows/s
# amplifier), so its numbers are item budgets: 25 items/s, burst 100 (two full batches; the agent shipper's
# steady rate is 5 lines/s and svc and desktop share the class). Draft contract wording: K-39 v12 in the C-64
# NOTE.md handoff.
NOTIFY_CLASS_RATES: Mapping[str, tuple[float, float]] = {
    NOTIFY_CLASS_CRITICAL: (50.0, 50.0),
    NOTIFY_CLASS_LOGS: (25.0, 100.0),
    NOTIFY_CLASS_OTHERS: (20.0, 20.0),
}
NOTIFY_REFUSAL_REPORT_S = 60.0
# One vortex_log_batch is charged min(items, NOTIFY_LOG_COST_MAX) tokens, at least 1 (the stored-item cap in
# bridge_store.LOG_BATCH_MAX; items past it are dropped by normalization and cost nothing more).
NOTIFY_LOG_COST_MAX = 50


def notify_class(event_type: Any) -> str:
    """The rate class of a notify type (K-39 v10); a missing or non-string type is 'others'."""
    if not isinstance(event_type, str):
        return NOTIFY_CLASS_OTHERS
    if event_type in NOTIFY_CRITICAL_TYPES:
        return NOTIFY_CLASS_CRITICAL
    if event_type in NOTIFY_LOG_TYPES:
        return NOTIFY_CLASS_LOGS
    return NOTIFY_CLASS_OTHERS


def notify_cost(event_type: Any, payload: Any) -> float:
    """The tokens one notify spends (C-64 review SP-3): a vortex_log_batch costs one per item it carries (at
    least 1, at most NOTIFY_LOG_COST_MAX, the stored-item cap), so the logs bucket counts items, not posts;
    every other notify costs 1."""
    if event_type != "vortex_log_batch" or not isinstance(payload, Mapping):
        return 1.0
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return 1.0
    return float(min(len(items), NOTIFY_LOG_COST_MAX))


class NotifyRateLimiter:
    """
    POST /notify's rate buckets (K-39 v10, C-64): one global bucket per class (NOTIFY_CLASS_RATES), checked after
    authentication, so the nonce of a refused notify is spent and a retry is signed anew. check() counts refusals;
    refusal_report() hands each class's count to the caller at most once a minute.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 rates: Mapping[str, tuple[float, float]] | None = None) -> None:
        self._clock = clock
        self.buckets = {name: TokenBucket(rate, burst, clock=clock)
                        for name, (rate, burst) in (rates or NOTIFY_CLASS_RATES).items()}
        self._refused = dict.fromkeys(self.buckets, 0)
        self._reported = dict.fromkeys(self.buckets, float("-inf"))
        # C-64 review LS-5: today both bridge listeners share one asyncio loop, so the counters and buckets
        # see one thread; the lock keeps them right if a listener ever gets a thread of its own.
        self._lock = threading.Lock()

    def check(self, event_type: Any, cost: float = 1.0) -> tuple[str, float | None]:
        """(class, None) when the notify may pass, else (class, seconds until its bucket holds cost tokens).
        cost is notify_cost(): one per stored item for a vortex_log_batch, 1 for everything else (SP-3)."""
        name = notify_class(event_type)
        cost = max(1.0, float(cost))
        with self._lock:
            bucket = self.buckets[name]
            if bucket.take(cost):
                return name, None
            self._refused[name] += 1
            return name, bucket.retry_after(cost)

    def refusal_report(self) -> list[tuple[str, int]]:
        """[(class, refusals not reported yet)] for each class with refusals whose last report is at least
        NOTIFY_REFUSAL_REPORT_S old; the caller logs one WARNING for each. Called on every refused notify and
        from the bridge's 12 s vitals loop, so the last window of a flood that stops abruptly is still
        reported (COR-2/LS-5)."""
        now = self._clock()
        due: list[tuple[str, int]] = []
        with self._lock:
            for name, count in self._refused.items():
                if count and now - self._reported[name] >= NOTIFY_REFUSAL_REPORT_S:
                    due.append((name, count))
                    self._refused[name] = 0
                    self._reported[name] = now
        return due


async def read_body(request: Request, limit: int = NOTIFY_READ_LIMIT) -> bytes | None:
    """The raw request body; None when it is larger than limit (it is then not read in full). A client that
    disconnects before its body is complete gets NotifyAuthError 400 instead of an unhandled ClientDisconnect
    (a 500 and a logged traceback), like FastAPI's body parsing answered it before."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                return None
            chunks.append(chunk)
    except ClientDisconnect as exc:
        raise NotifyAuthError(400, "client_disconnect") from exc
    return b"".join(chunks)


def _parse_body(raw: bytes) -> dict[str, Any]:
    """The JSON object in raw, read the way Starlette's request.json() reads it."""
    try:
        data = json.loads(raw)
    except ValueError as exc:  # also UnicodeDecodeError
        raise NotifyAuthError(400, "bad_body") from exc
    if not isinstance(data, dict):
        raise NotifyAuthError(400, "bad_body")
    return data


async def _verify_agent(request: Request, raw: bytes | None, *, agent_token: str | None,
                        nonces: NonceCache, mark_seen: Callable[[str], Awaitable[bool]] | None,
                        now: float) -> NotifyAuth:
    headers = request.headers
    if not same_secret(headers.get("x-agent-token"), agent_token):
        raise NotifyAuthError(401, "bad_token")
    ts = headers.get("x-agent-timestamp", "")
    if not _TIMESTAMP.fullmatch(ts) or abs(now - int(ts)) > NOTIFY_MAX_SKEW_S:
        raise NotifyAuthError(401, "stale")
    nonce = headers.get("x-agent-nonce", "")
    if not _NONCE.fullmatch(nonce):
        raise NotifyAuthError(401, "bad_nonce")
    if raw is None:  # not read in full, so it cannot be verified
        raise NotifyAuthError(413, "too_large")
    try:
        signed = verify_notify(ts, nonce, raw, headers.get("x-agent-signature"))
    except RuntimeError as exc:  # production without BRIDGE_HMAC_SECRET
        raise NotifyAuthError(503, "agent_link_unconfigured") from exc
    if not signed:
        raise NotifyAuthError(403, "bad_signature")
    if not nonces.add(nonce):
        raise NotifyAuthError(409, "replay")
    if mark_seen is not None and not await mark_seen(nonce):  # seen before a bridge restart
        raise NotifyAuthError(409, "replay")
    if len(raw) > NOTIFY_MAX_BYTES:
        raise NotifyAuthError(413, "too_large")
    return NotifyAuth(AUTH_AGENT, _parse_body(raw))


def _verify_legacy(request: Request, raw: bytes | None, legacy_token: str | None) -> NotifyAuth:
    """Today's checks, unchanged: token 401, missing signature 401, HMAC over the sorted JSON 403."""
    headers = request.headers
    if not same_secret(headers.get("x-vortex-token"), legacy_token):
        raise NotifyAuthError(401, "bad_token")
    signature = headers.get("x-vortex-signature")
    if not signature:
        raise NotifyAuthError(401, "missing_signature")
    if raw is None:
        raise NotifyAuthError(413, "too_large")
    data = _parse_body(raw)
    if not security_manager.verify_signature(json.dumps(data, sort_keys=True), signature):
        raise NotifyAuthError(403, "bad_signature")
    return NotifyAuth(AUTH_LEGACY, data)


async def verify_notify_request(request: Request, raw: bytes | None, *, agent_token: str | None,
                                legacy_token: str | None, legacy_enabled: bool, nonces: NonceCache,
                                mark_seen: Callable[[str], Awaitable[bool]] | None = None,
                                now: float | None = None) -> NotifyAuth:
    """
    Authenticates one POST /notify whose body is raw (read_body(): None for a body over its limit).
    Agent headers (X-Agent-Token, -Timestamp, -Nonce, -Signature) win when present; the legacy X-Vortex
    headers count only while legacy_enabled. mark_seen(nonce) is the persistent replay check (False =
    seen before). Returns NotifyAuth; raises NotifyAuthError with the C-NOTIFY-1 status and reason code.
    """
    if "x-agent-token" in request.headers:
        return await _verify_agent(request, raw, agent_token=agent_token, nonces=nonces,
                                   mark_seen=mark_seen, now=time.time() if now is None else now)
    if legacy_enabled and "x-vortex-token" in request.headers:
        return _verify_legacy(request, raw, legacy_token)
    raise NotifyAuthError(401, "bad_token")


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def check_origin(headers: Mapping[str, str], allowed_origins: Iterable[str]) -> bool:
    """
    C-AUTH-1 CSRF check for cookie-authenticated POSTs: the Origin header, or the origin of the Referer
    when a browser sent no Origin, must be one of allowed_origins. No header at all fails, and so does
    Origin 'null' (sandboxed frames, file://).
    """
    allowed = {origin.strip().rstrip("/") for origin in allowed_origins if origin and origin.strip()}
    origin = headers.get("origin")
    if origin is not None:
        return origin.strip().rstrip("/") in allowed
    referer = headers.get("referer")
    return bool(referer) and _origin_of(referer) in allowed


def retry_after_header(seconds: float) -> dict[str, str]:
    """Retry-After in whole seconds (at least 1)."""
    return {"Retry-After": str(max(1, math.ceil(seconds)))}
