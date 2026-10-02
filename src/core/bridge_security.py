"""
# src/core/bridge_security.py
Agent link v2 checks for the UI bridge (plan task C-14).

- verify_notify_request(): POST /notify in the C-NOTIFY-1 (K-39) order: token 401 bad_token, timestamp
  401 stale, signature 403 bad_signature, nonce 409 replay, size 413 too_large. The legacy X-Vortex
  headers are accepted only while LEGACY_TOKEN_AUTH=1.
- NonceCache: the notify nonces of the last 300 s; the store's seen_ids keep them across a restart.
- TokenBucket: per-socket and per-class rate limits (C-SOCK-1).
- check_origin(): the Origin (or Referer) of a cookie-authenticated POST must be in ALLOWED_ORIGINS
  (C-AUTH-1; used by the session endpoints of C-19).

No function here logs or returns a secret: errors carry only a short code.
"""

from __future__ import annotations

import hmac
import json
import math
import re
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
