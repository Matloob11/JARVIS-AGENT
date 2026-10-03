"""
# src/core/bridge_auth.py
Owner session auth of the UI bridge (plan task C-19; contracts C-AUTH-1 / K-36 and C-LK-1 / K-37).

- Login: POST /auth/login {password, totp} or {password, recovery_code}. The Origin (or Referer) must be in
  ALLOWED_ORIGINS. The password is checked against OWNER_PASSWORD_HASH (bcrypt, cost 12); the TOTP code (RFC 6238,
  30 s, 6 digits) is compared in constant time with the codes of the steps -1..+1 around now, and the matched step
  must be newer than kv 'totp_last', so a code is never accepted twice; a recovery code (OWNER_RECOVERY_HASHES,
  bcrypt) works once, its use is recorded in kv 'recovery_used'. With a recovery code every configured hash is
  checked whether the password was right or not, so the time never tells the password apart. 5 failures per 15 min
  per IP or 20 per hour in total give 429 rate_limited with retry_after; every attempt is counted before it is
  checked (so concurrent attempts cannot overrun the limits), and at most 2 checks run at once (more get 429 with a
  short retry_after). Nothing enrolled gives 503 owner_not_enrolled.
- Session: a row in the store's sessions table (12 h) and the cookie jarvis_session=<JWT HS256 with
  SESSION_JWT_SECRET, claims {sub:'owner', sid, iat, exp, v:1}>; Path=/vortex (COOKIE_PATH), HttpOnly, Secure
  (COOKIE_SECURE 1|0|auto), SameSite=Strict, Max-Age 43200. A JWT counts only while its sid is in the table, not
  revoked and not expired. A new login with a valid cookie revokes the old session (rotation); POST /auth/logout
  revokes it and clears the cookie; 'revoke-all' ends every session. GET /auth/me reports the session.
- Dependencies for the bridge's endpoints: require_session, require_agent, require_session_or_agent and
  require_session_or_legacy (the X-Vortex-Token header while LEGACY_TOKEN_AUTH=1, until the cut-over C-22).
  A refusal is 401 {error:'auth_required'}.
- Audit: every login attempt, logout, session mint and CLI command writes an audit row. The cheap refusals (bad
  origin, not enrolled, rate limited, busy) have a hard cap of 5000 rows per UTC day; past it one 'auth_audit_capped'
  row is written and the next day's first row carries the number left out (skipped_before). Rows hold reason codes and the first 8 characters of a
  session id, never a password, code, hash, secret or token.

Root-only CLI inside the bridge container (all audited; values are never printed except where noted):

    python -m src.core.bridge_auth hash-password                  # asks twice, prints the base64url bcrypt hash
    python -m src.core.bridge_auth mint-session --ttl N --label X # prints jarvis_session=<JWT> (for the probe)
    python -m src.core.bridge_auth revoke-all
    python -m src.core.bridge_auth enroll-check                   # which owner settings are set and usable
    python -m src.core.bridge_auth audit-summary --prefix P       # audit actions with counts, no details
    python -m src.core.bridge_auth enroll --env-file PATH         # owner enrolment in the owner's own terminal

'enroll' needs an interactive terminal: it asks for the password twice, shows the authenticator QR / otpauth URI and
the 10 recovery codes once on that terminal, checks one current code, and writes OWNER_PASSWORD_HASH,
OWNER_TOTP_SECRET, OWNER_RECOVERY_HASHES (and SESSION_JWT_SECRET when the file has none) into PATH (mode 600).
"""
# pylint: disable=too-many-lines  # one module: the primitives, the login service, its router and the CLI

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import time
import unicodedata
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import bcrypt
import jwt
import pyotp
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import cookie_parser

from services.utils.jarvis_config import config
from services.utils.jarvis_security import verify_notify
from src.core.bridge_security import NonceCache, check_origin, retry_after_header, same_secret
from src.core.bridge_store import BridgeStore, bridge_db_path

# The bridge's logger name (see bridge_store): never forwarded to /notify, so nothing loops back into the store.
logger = logging.getLogger("UI-BRIDGE")

USER = "owner"
COOKIE_NAME = "jarvis_session"
SESSION_TTL_S = 43200            # 12 h: the JWT exp and the cookie Max-Age (C-AUTH-1)
JWT_ALGORITHM = "HS256"
JWT_VERSION = 1
JWT_SECRET_MIN_CHARS = 32        # gen_secrets.py makes token_urlsafe(64) (86 chars)
BCRYPT_COST = 12
PASSWORD_MIN_CHARS = 12
PASSWORD_MAX_BYTES = 72          # bcrypt's limit; bcrypt 5 refuses longer input instead of truncating it
TOTP_INTERVAL_S = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1                  # steps accepted on each side of now (drift)
RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
RECOVERY_GROUPS, RECOVERY_GROUP_LEN = 3, 4                  # xxxx-xxxx-xxxx
KV_TOTP_LAST = "totp_last"       # the newest accepted TOTP timecode
KV_RECOVERY_USED = "recovery_used"  # fingerprints of the used recovery hashes
LIMIT_PER_IP, LIMIT_PER_IP_WINDOW_S = 5, 900.0
LIMIT_GLOBAL, LIMIT_GLOBAL_WINDOW_S = 20, 3600.0
LOGIN_BODY_MAX = 4096
MAX_VERIFYING = 2                # login credential checks (bcrypt) running at once; more get 429 for BUSY_RETRY_S
BUSY_RETRY_S = 2
REFUSAL_AUDIT_DAILY_CAP = 5000   # audit rows per UTC day for cheap login refusals (bad origin, not enrolled, 429)
MINT_TTL_MAX_S = SESSION_TTL_S
TOUCH_INTERVAL_S = 60.0          # last_seen_ts is written at most once a minute per session
AUTH_KIND_SESSION, AUTH_KIND_LEGACY, AUTH_KIND_AGENT = "session", "legacy", "agent"
AGENT_MAX_SKEW_S = 60
ENV_KEYS = ("SESSION_JWT_SECRET", "OWNER_PASSWORD_HASH", "OWNER_TOTP_SECRET", "OWNER_RECOVERY_HASHES")

# ASCII digits only: \d also matches other scripts' digits, which the code check could not compare. A code typed
# with Urdu/Arabic (or any other script's) digits is turned into ASCII digits first (ascii_digits).
_DIGITS = re.compile(r"[0-9]{%d}" % TOTP_DIGITS)
_TIMESTAMP = re.compile(r"\d{1,12}")
_NONCE = re.compile(r"[0-9a-fA-F]{16,128}")
_BCRYPT_HASH = re.compile(rb"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}")
_ENV_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
# A short list of passwords nobody should pick; the length rule already rules out most weak ones.
COMMON_PASSWORDS = frozenset({
    "password1234", "password12345", "password123456", "123456789012", "1234567890123", "qwertyuiop12",
    "qwerty123456", "iloveyou1234", "admin1234567", "letmein12345", "welcome12345", "jarvis123456",
    "jarvisjarvis", "passwordpassword", "abcdefghijkl", "111111111111", "000000000000", "aaaaaaaaaaaa",
    "qwertyuiopas", "asdfghjklzxc", "pakistan1234", "pakistan12345",
})


class AuthRequired(Exception):
    """A protected endpoint was called without an accepted credential: 401 {error: 'auth_required'}."""


@dataclass(frozen=True)
class Principal:
    """Who a request authenticated as: an owner session, the legacy token or an agent."""

    kind: str
    session_id: str = ""
    expires_at: int = 0

    @property
    def is_session(self) -> bool:
        return self.kind == AUTH_KIND_SESSION


# ---------------------------------------------------------------- encodings and primitives


def b64url_encode(data: bytes) -> str:
    """base64url without padding: no '$', '+', '/' or '=' for compose interpolation or a shell to touch."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """The bytes of a base64url string, padded or not; ValueError for anything else."""
    value = text.strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def decode_bcrypt_hash(stored: str | None) -> bytes | None:
    """A stored bcrypt hash as bytes: base64url-encoded (the env format) or, for a hand-edited local .env, the raw
    '$2b$...' form. None when it is missing or not a bcrypt hash."""
    if not stored:
        return None
    value = stored.strip()
    try:
        raw = value.encode("ascii") if value.startswith("$2") else b64url_decode(value)
    except (ValueError, UnicodeEncodeError):
        return None
    return raw if _BCRYPT_HASH.fullmatch(raw) else None


def password_problem(password: str) -> str | None:
    """Why a new owner password is refused, or None: at least 12 characters, at most 72 bytes (bcrypt), not on
    the common list."""
    if len(password) < PASSWORD_MIN_CHARS:
        return f"at least {PASSWORD_MIN_CHARS} characters"
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        return f"at most {PASSWORD_MAX_BYTES} bytes"
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        return "too common"
    return None


def hash_password(password: str, cost: int = BCRYPT_COST) -> str:
    """OWNER_PASSWORD_HASH for password: base64url of its bcrypt hash."""
    problem = password_problem(password)
    if problem:
        raise ValueError(f"password refused: {problem}")
    return b64url_encode(bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=cost)))


def verify_password(password: Any, stored_hash: bytes | None) -> bool:
    """bcrypt check of a presented password; False for a missing hash, a non-string or a password bcrypt cannot
    take (over 72 bytes, so it was never enrolled)."""
    if stored_hash is None or not isinstance(password, str) or not password:
        return False
    encoded = password.encode("utf-8", "replace")
    if len(encoded) > PASSWORD_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, stored_hash)
    except ValueError:
        return False


def ascii_digits(text: str) -> str:
    """text with every Unicode decimal digit replaced by its ASCII digit: Arabic-Indic '١٢٣' and Extended
    Arabic-Indic '۱۲۳' (what an Urdu or Arabic keyboard or phone types), full-width, Devanagari and so on. Every
    other character is kept as it is, so a code that still holds something else is refused by its own check."""
    if text.isascii():
        return text
    return "".join(str(unicodedata.decimal(ch)) if not ch.isascii() and unicodedata.decimal(ch, None) is not None
                   else ch for ch in text)


def _code_text(text: str, *, drop_dashes: bool) -> str:
    """A typed one-time code without the separators people and keyboards add: whitespace of any kind, the invisible
    direction marks an RTL keyboard or a pasted text carries (Unicode category Cf) and, with drop_dashes, any dash
    (category Pd, so '-' and the dashes a phone may substitute for it); its digits in ASCII."""
    kept = "".join(ch for ch in text if not ch.isspace() and unicodedata.category(ch) != "Cf"
                   and not (drop_dashes and unicodedata.category(ch) == "Pd"))
    return ascii_digits(kept)


def normalize_recovery_code(code: str) -> str:
    """The canonical form a recovery code is hashed in: lower case without spaces or '-', digits in ASCII (a code
    typed with Urdu/Arabic digits is the same code)."""
    return _code_text(code, drop_dashes=True).lower()


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """count fresh codes 'xxxx-xxxx-xxxx' (about 70 bits each)."""
    return ["-".join("".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP_LEN))
                     for _ in range(RECOVERY_GROUPS)) for _ in range(count)]


def hash_recovery_code(code: str, cost: int = BCRYPT_COST) -> str:
    """One OWNER_RECOVERY_HASHES entry: base64url of the bcrypt hash of the normalized code."""
    return b64url_encode(bcrypt.hashpw(normalize_recovery_code(code).encode("ascii"), bcrypt.gensalt(rounds=cost)))


def verify_recovery_code(code: str, stored: str) -> bool:
    raw = decode_bcrypt_hash(stored)
    normalized = normalize_recovery_code(code)
    if raw is None or not normalized or len(normalized) > PASSWORD_MAX_BYTES or not normalized.isascii():
        return False
    try:
        return bcrypt.checkpw(normalized.encode("ascii"), raw)
    except ValueError:
        return False


def recovery_match(code: Any, hashes: Sequence[str]) -> int | None:
    """The index of the stored hash that code matches, else None. Every hash is checked, used or not, and the loop
    never stops early, so the time depends only on how many hashes are configured and on the shape of code, never
    on which (if any) matched."""
    matched: int | None = None
    if not isinstance(code, str):
        return None
    for index, stored in enumerate(hashes):
        if verify_recovery_code(code, stored) and matched is None:
            matched = index
    return matched


def recovery_fingerprint(stored: str) -> str:
    """What kv 'recovery_used' records for a used code: a short digest of its stored hash (not the code)."""
    return hashlib.sha256(stored.strip().encode("utf-8")).hexdigest()[:24]


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_usable(secret: str | None) -> bool:
    if not secret:
        return False
    try:
        pyotp.TOTP(secret).generate_otp(0)
    except (ValueError, TypeError):  # binascii.Error is a ValueError
        return False
    return len(secret.strip()) >= 16


def provisioning_uri(secret: str) -> str:
    """The otpauth:// URI the authenticator app scans (name 'owner', issuer 'JARVIS')."""
    return pyotp.TOTP(secret).provisioning_uri(name=USER, issuer_name="JARVIS")


def totp_timecode(now: float) -> int:
    return int(now) // TOTP_INTERVAL_S


def totp_match(secret: str, code: Any, now: float, window: int = TOTP_WINDOW) -> int | None:
    """The timecode whose code equals code within +-window steps of now, else None. Every step is compared in
    constant time, and all of them are always compared, so the timing says nothing about which one matched.
    Digits of any script count as their ASCII digits ('١٢٣٤٥٦' is '123456'); whatever is then not exactly six ASCII
    digits is no match (never an error)."""
    if not isinstance(code, str):
        return None
    given = _code_text(code, drop_dashes=False)
    if not _DIGITS.fullmatch(given) or not given.isascii():
        return None
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_S)
    base = totp_timecode(now)
    matched: int | None = None
    for offset in range(-window, window + 1):
        expected = totp.generate_otp(base + offset)
        if hmac.compare_digest(expected.encode("ascii"), given.encode("ascii")) and matched is None:
            matched = base + offset
    return matched


@dataclass(frozen=True)
class OwnerCredentials:
    """The owner's enrolled credentials as configured (config attributes from the bridge env)."""

    jwt_secret: str | None
    password_hash: bytes | None
    totp_secret: str | None
    recovery_hashes: tuple[str, ...]

    @classmethod
    def from_config(cls) -> OwnerCredentials:
        recovery = tuple(h.strip() for h in (config.owner_recovery_hashes or []) if decode_bcrypt_hash(h))
        secret = (config.owner_totp_secret or "").strip().replace(" ", "").upper() or None
        return cls(jwt_secret=(config.session_jwt_secret or "").strip() or None,
                   password_hash=decode_bcrypt_hash(config.owner_password_hash),
                   totp_secret=secret if totp_usable(secret) else None,
                   recovery_hashes=recovery)

    @property
    def jwt_ready(self) -> bool:
        return bool(self.jwt_secret) and len(self.jwt_secret or "") >= JWT_SECRET_MIN_CHARS

    @property
    def enrolled(self) -> bool:
        return self.jwt_ready and self.password_hash is not None and self.totp_secret is not None

    def problems(self) -> list[str]:
        """Names of the settings that are missing or unusable (never their values)."""
        missing = []
        if not self.jwt_ready:
            missing.append("SESSION_JWT_SECRET")
        if self.password_hash is None:
            missing.append("OWNER_PASSWORD_HASH")
        if self.totp_secret is None:
            missing.append("OWNER_TOTP_SECRET")
        return missing


# ---------------------------------------------------------------- brute-force limits


class LoginLimiter:
    """Failed logins: at most per_ip in per_ip_window_s per client IP and global_max in global_window_s in total
    (C-AUTH-1: 5 per 15 min, 20 per hour). retry_after() > 0 while a limit is reached.

    The login reserves every attempt before it checks anything (reserve(), counted as a failure from that moment),
    so attempts that run at the same time are all counted before any of them is decided; release() takes the
    reservation back for an attempt that turned out not to be a failed guess (a success, a busy refusal, a store
    outage)."""

    def __init__(self, *, per_ip: int = LIMIT_PER_IP, per_ip_window_s: float = LIMIT_PER_IP_WINDOW_S,
                 global_max: int = LIMIT_GLOBAL, global_window_s: float = LIMIT_GLOBAL_WINDOW_S,
                 clock: Callable[[], float] = time.time, max_ips: int = 10_000) -> None:
        self.per_ip, self.per_ip_window_s = per_ip, per_ip_window_s
        self.global_max, self.global_window_s = global_max, global_window_s
        self._clock = clock
        self._max_ips = max_ips
        self._by_ip: OrderedDict[str, deque[float]] = OrderedDict()
        self._all: deque[float] = deque()

    @staticmethod
    def _expire(times: deque[float], now: float, window: float) -> None:
        while times and now - times[0] >= window:
            times.popleft()

    def retry_after(self, ip: str) -> float:
        """Seconds until ip may try again; 0.0 when it may now."""
        now = self._clock()
        self._expire(self._all, now, self.global_window_s)
        waits = [0.0]
        if len(self._all) >= self.global_max:
            waits.append(self._all[0] + self.global_window_s - now)
        times = self._by_ip.get(ip)
        if times is not None:
            self._expire(times, now, self.per_ip_window_s)
            if not times:
                del self._by_ip[ip]
            elif len(times) >= self.per_ip:
                waits.append(times[0] + self.per_ip_window_s - now)
        return max(waits)

    def fail(self, ip: str) -> float:
        """Records one failure for ip now; returns its time stamp (what release() takes)."""
        now = self._clock()
        self._all.append(now)
        times = self._by_ip.setdefault(ip, deque())
        times.append(now)
        self._by_ip.move_to_end(ip)
        while len(self._by_ip) > self._max_ips:
            self._by_ip.popitem(last=False)
        return now

    def reserve(self, ip: str) -> float | None:
        """Admits one attempt from ip and records it as a failure at once; returns its stamp for release(), or None
        (nothing recorded) while a limit is reached. Synchronous, so under asyncio no other attempt can slip in
        between the check and the record."""
        if self.retry_after(ip) > 0:
            return None
        return self.fail(ip)

    def release(self, ip: str, stamp: float) -> None:
        """Takes back one reservation made by reserve(ip) (an attempt that was not a failed guess)."""
        for times in (self._all, self._by_ip.get(ip)):
            if times is not None:
                try:
                    times.remove(stamp)
                except ValueError:  # already expired out of the window
                    pass
        if not self._by_ip.get(ip, True):
            self._by_ip.pop(ip, None)

    def succeed(self, ip: str) -> None:
        """A successful login clears that IP's own failures (the global count keeps them)."""
        self._by_ip.pop(ip, None)


# ---------------------------------------------------------------- agent signed requests (C-NOTIFY-1 headers)


async def verify_agent_headers(  # pylint: disable=too-many-return-statements
        headers: Mapping[str, str], raw: bytes, *, agent_token: str | None, nonces: NonceCache,
        mark_seen: Callable[[str], Awaitable[bool]] | None = None, now: float | None = None) -> bool:
    """True for a request carrying valid C-NOTIFY-1 agent headers over exactly raw (b'' for a GET): X-Agent-Token,
    a timestamp within 60 s, a fresh nonce and X-Agent-Signature = HMAC-SHA256(BRIDGE_HMAC_SECRET,
    f'{ts}.{nonce}.' + raw). Used for the agent's GET /settings and GET /status; POST /notify keeps its own checks."""
    if not same_secret(headers.get("x-agent-token"), agent_token):
        return False
    ts = headers.get("x-agent-timestamp", "")
    current = time.time() if now is None else now
    if not _TIMESTAMP.fullmatch(ts) or abs(current - int(ts)) > AGENT_MAX_SKEW_S:
        return False
    nonce = headers.get("x-agent-nonce", "")
    if not _NONCE.fullmatch(nonce):
        return False
    try:
        if not verify_notify(ts, nonce, raw, headers.get("x-agent-signature")):
            return False
    except RuntimeError:  # production without BRIDGE_HMAC_SECRET
        return False
    if not nonces.add(nonce):
        return False
    return mark_seen is None or await mark_seen(nonce)


# ---------------------------------------------------------------- the login service


def _json(body: dict[str, Any], status: int = 200, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=dict(headers or {}))


class _LoginRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _StoreDown(Exception):
    """The store could not be read or written, so the login cannot be decided."""


# pylint: disable-next=too-many-instance-attributes
class OwnerAuth:
    """The owner login, the session checks and the FastAPI router of C-AUTH-1. Everything it needs from the bridge
    is passed in as a callable, so a test or the bridge can swap the store, the clock or the credentials."""

    def __init__(self, store: Callable[[], BridgeStore], *,  # pylint: disable=too-many-arguments
                 allowed_origins: Callable[[], Iterable[str]],
                 client_ip: Callable[[Request], str],
                 legacy_ok: Callable[[Request], bool],
                 agent_ok: Callable[[Request], Awaitable[bool]] | None = None,
                 is_internal: Callable[[Any], bool] = lambda _scope: False,
                 on_revoke: Callable[[str, str], Awaitable[None]] | None = None,
                 credentials: Callable[[], OwnerCredentials] = OwnerCredentials.from_config,
                 clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._allowed_origins = allowed_origins
        self._client_ip = client_ip
        self._legacy_ok = legacy_ok
        self._agent_ok = agent_ok
        self._is_internal = is_internal
        self._on_revoke = on_revoke
        self._credentials = credentials
        self.clock = clock
        self.limiter = LoginLimiter(clock=clock)
        # One second factor at a time: the TOTP replay check and the recovery bookkeeping read and then write kv.
        self._factor_lock = asyncio.Lock()
        # Credential checks running now (bcrypt in worker threads); at most max_verifying, the rest get 429 at once.
        self.max_verifying = MAX_VERIFYING
        self._verifying = 0
        # Cheap refusals (bad origin, not enrolled, rate limited, busy) get one audit row each, up to a hard cap per
        # UTC day so a flood cannot fill the disk; past the cap one 'auth_audit_capped' row is written and the number
        # left out goes into the first row of the next day.
        self.refusal_audit_daily_cap = REFUSAL_AUDIT_DAILY_CAP
        self._refusal_day = -1
        self._refusal_rows = 0
        self._refusals_skipped = 0
        self._touched: OrderedDict[str, float] = OrderedDict()
        self.router = self._build_router()

    # ------------------------------------------------------------ plumbing

    def install(self, app: FastAPI) -> None:
        """Adds the /auth routes and the 401 {error: 'auth_required'} answer to app."""
        app.add_exception_handler(AuthRequired, _auth_required_response)
        app.include_router(self.router)

    async def audit(self, actor: str, action: str, detail: Mapping[str, Any], ip: str = "") -> None:
        try:
            await self._store().audit(actor, action, dict(detail), ip)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Auth audit row '%s' could not be written", action, exc_info=True)

    async def _audit_refusal(self, action: str, detail: dict[str, Any], ip: str) -> None:
        day = int(self.clock() // 86400)
        if day != self._refusal_day:
            self._refusal_day, self._refusal_rows = day, 0
        if self._refusal_rows > self.refusal_audit_daily_cap:
            self._refusals_skipped += 1
            return
        self._refusal_rows += 1
        if self._refusal_rows > self.refusal_audit_daily_cap:
            self._refusals_skipped += 1
            await self.audit("anonymous", "auth_audit_capped", {"cap": self.refusal_audit_daily_cap}, ip)
            return
        if self._refusals_skipped:
            detail = {**detail, "skipped_before": self._refusals_skipped}
            self._refusals_skipped = 0
        await self.audit("anonymous", action, detail, ip)

    def _secure(self, request: Request) -> bool:
        mode = (getattr(config, "cookie_secure", "1") or "1").strip().lower()
        if mode == "auto":
            return request.url.scheme in ("https", "wss")
        return mode != "0"

    def _cookie_path(self) -> str:
        return (getattr(config, "cookie_path", "") or "/vortex").strip() or "/vortex"

    def set_cookie(self, response: Response, request: Request, token: str, max_age: int) -> None:
        response.set_cookie(COOKIE_NAME, token, max_age=max_age, path=self._cookie_path(),
                            secure=self._secure(request), httponly=True, samesite="strict")

    def clear_cookie(self, response: Response, request: Request) -> None:
        response.set_cookie(COOKIE_NAME, "", max_age=0, expires=0, path=self._cookie_path(),
                            secure=self._secure(request), httponly=True, samesite="strict")

    # ------------------------------------------------------------ sessions

    async def mint_session(self, *, ttl_s: float = SESSION_TTL_S, label: str = "", ip: str = "",
                           user_agent: str = "") -> tuple[str, dict[str, Any]]:
        """A new session row and its JWT. RuntimeError when SESSION_JWT_SECRET is not usable."""
        creds = self._credentials()
        if not creds.jwt_ready:
            raise RuntimeError("SESSION_JWT_SECRET is not set")
        record = await self._store().create_session(ttl_s, user=USER, label=label[:64], ip=ip[:64],
                                                    user_agent=user_agent[:256])
        # The JWT carries the row's own times, so the cookie and the sessions table always agree.
        claims = {"sub": USER, "sid": record["sid"], "iat": int(record["created_ts"]),
                  "exp": int(record["expires_ts"]), "v": JWT_VERSION}
        return jwt.encode(claims, creds.jwt_secret, algorithm=JWT_ALGORITHM), record

    def _claims(self, token: str, secret: str) -> dict[str, Any] | None:
        try:
            claims = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM],
                                options={"require": ["sub", "sid", "iat", "exp"], "verify_exp": False,
                                         "verify_iat": False})
        except jwt.PyJWTError:
            return None
        if claims.get("sub") != USER or claims.get("v") != JWT_VERSION or not isinstance(claims.get("sid"), str):
            return None
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp <= self.clock():
            return None
        return claims

    async def session_from_token(self, token: str | None) -> Principal | None:
        """The owner session a jarvis_session JWT stands for, or None: a bad signature, an expired token, or a sid
        that is unknown, revoked or expired in the sessions table. A failing store counts as no session."""
        if not token or len(token) > 4096:
            return None
        creds = self._credentials()
        if not creds.jwt_ready:
            return None
        claims = self._claims(token, creds.jwt_secret or "")
        if claims is None:
            return None
        sid = claims["sid"]
        try:
            row = await self._store().get_session(sid)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Session check failed: the store is unavailable", exc_info=True)
            return None
        now = self.clock()
        if row is None or row.get("revoked_ts") is not None or float(row.get("expires_ts") or 0) <= now:
            return None
        await self._touch(sid, now)
        return Principal(AUTH_KIND_SESSION, sid, int(min(float(claims["exp"]), float(row["expires_ts"]))))

    async def session_active(self, sid: str) -> bool:
        """True while sid is in the table, not revoked and not expired (used to end UI sockets)."""
        try:
            row = await self._store().get_session(sid)
        except Exception:  # pylint: disable=broad-exception-caught
            return True  # a store hiccup never drops a live socket
        return (row is not None and row.get("revoked_ts") is None
                and float(row.get("expires_ts") or 0) > self.clock())

    async def _touch(self, sid: str, now: float) -> None:
        last = self._touched.get(sid)
        if last is not None and now - last < TOUCH_INTERVAL_S:
            return
        self._touched[sid] = now
        self._touched.move_to_end(sid)
        while len(self._touched) > 1000:
            self._touched.popitem(last=False)
        try:
            await self._store().touch_session(sid)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Session touch failed", exc_info=True)

    def _public(self, scope: Any) -> bool:
        """Owner sessions exist only on the public listener; the internal one is agent-only (K-38 v4)."""
        return not self._is_internal(scope)

    async def session_from_request(self, request: Request) -> Principal | None:
        if not self._public(request.scope):
            return None
        return await self.session_from_token(request.cookies.get(COOKIE_NAME))

    async def session_from_environ(self, environ: Mapping[str, Any]) -> Principal | None:
        """The session of a Socket.IO handshake: the jarvis_session cookie of its HTTP request."""
        if not self._public(environ.get("asgi.scope")):
            return None
        header = environ.get("HTTP_COOKIE")
        if not isinstance(header, str) or COOKIE_NAME not in header:
            return None
        return await self.session_from_token(cookie_parser(header).get(COOKIE_NAME))

    @staticmethod
    def has_session_cookie(environ: Mapping[str, Any]) -> bool:
        header = environ.get("HTTP_COOKIE")
        return isinstance(header, str) and bool(cookie_parser(header).get(COOKIE_NAME))

    async def revoke(self, sid: str, reason: str) -> bool:
        try:
            revoked = await self._store().revoke_session(sid)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Session revoke failed: the store is unavailable", exc_info=True)
            return False
        self._touched.pop(sid, None)
        if self._on_revoke is not None:
            try:
                await self._on_revoke(sid, reason)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Ending the sockets of a revoked session failed", exc_info=True)
        return revoked

    # ------------------------------------------------------------ dependencies

    async def require_session(self, request: Request) -> Principal:
        principal = await self.session_from_request(request)
        if principal is None:
            raise AuthRequired()
        return principal

    async def _agent(self, request: Request) -> Principal | None:
        if self._agent_ok is not None and await self._agent_ok(request):
            return Principal(AUTH_KIND_AGENT)
        return None

    async def require_agent(self, request: Request) -> Principal:
        principal = await self._agent(request)
        if principal is None:
            raise AuthRequired()
        return principal

    async def require_session_or_agent(self, request: Request) -> Principal:
        principal = await self.session_from_request(request) or await self._agent(request)
        if principal is None:
            raise AuthRequired()
        return principal

    async def require_session_or_legacy(self, request: Request) -> Principal:
        """The owner session, or the legacy X-Vortex-Token header while LEGACY_TOKEN_AUTH=1 (until C-22)."""
        principal = await self.session_from_request(request)
        if principal is not None:
            return principal
        if self._legacy_ok(request):
            return Principal(AUTH_KIND_LEGACY)
        raise AuthRequired()

    # ------------------------------------------------------------ login, logout, me

    async def _login_body(self, request: Request) -> dict[str, Any] | None:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > LOGIN_BODY_MAX:
            return None
        try:
            raw = await request.body()
        except Exception:  # pylint: disable=broad-exception-caught  # a client gone mid-body
            return None
        if len(raw) > LOGIN_BODY_MAX:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def _check_totp(self, secret: str, code: Any) -> None:
        matched = totp_match(secret, code, self.clock())
        if matched is None:
            raise _LoginRefused("bad_totp")
        async with self._factor_lock:
            try:
                last = await self._store().kv_get(KV_TOTP_LAST, None)
            except Exception as exc:
                raise _StoreDown() from exc
            if isinstance(last, (int, float)) and not isinstance(last, bool) and matched <= int(last):
                raise _LoginRefused("totp_replay")
            try:
                await self._store().kv_set(KV_TOTP_LAST, matched)
            except Exception as exc:
                raise _StoreDown() from exc

    async def _spend_recovery(self, hashes: Sequence[str], matched: int | None) -> int:
        """Spends the recovery code that matched hashes[matched]; returns how many unused codes are left. A code
        that matched nothing, or one already used, is refused."""
        if matched is None:
            raise _LoginRefused("bad_recovery")
        fingerprint = recovery_fingerprint(hashes[matched])
        async with self._factor_lock:
            try:
                used = set(await self._store().kv_get(KV_RECOVERY_USED, []) or [])
            except Exception as exc:
                raise _StoreDown() from exc
            if fingerprint in used:
                raise _LoginRefused("bad_recovery")
            used.add(fingerprint)
            try:
                await self._store().kv_set(KV_RECOVERY_USED, sorted(used))
            except Exception as exc:
                raise _StoreDown() from exc
        return sum(1 for h in hashes if recovery_fingerprint(h) not in used)

    async def _verify_login(self, creds: OwnerCredentials, body: dict[str, Any] | None) -> str:
        """The second factor used ('totp' or 'recovery'); raises _LoginRefused or _StoreDown.

        The time a refusal takes never tells whether the password was right: with a TOTP code the only slow step is
        the one bcrypt check of the password (the code check is a few HMACs); with a recovery code every configured
        recovery hash is bcrypt-checked as well, whether the password was right or not, in the same worker call.
        A wrong password never touches the TOTP or recovery state."""
        if body is None or not isinstance(body.get("password"), str):
            raise _LoginRefused("bad_body")
        totp, recovery = body.get("totp"), body.get("recovery_code")
        if totp is None and recovery is None:
            raise _LoginRefused("bad_body")
        password = body["password"]
        if totp is not None:
            if not await asyncio.to_thread(verify_password, password, creds.password_hash):
                raise _LoginRefused("bad_password")
            await self._check_totp(creds.totp_secret or "", totp)
            return "totp"
        hashes = creds.recovery_hashes

        def check() -> tuple[bool, int | None]:
            return verify_password(password, creds.password_hash), recovery_match(recovery, hashes)

        password_ok, matched = await asyncio.to_thread(check)
        if not password_ok:
            raise _LoginRefused("bad_password")
        left = await self._spend_recovery(hashes, matched)
        return f"recovery:{left}"

    async def login(self, request: Request) -> Response:  # pylint: disable=too-many-return-statements
        ip = self._client_ip(request)
        if not self._public(request.scope):
            return _json({"detail": "Not Found"}, 404)
        if not check_origin(request.headers, self._allowed_origins()):
            await self._audit_refusal("auth_login_refused", {"reason": "bad_origin"}, ip)
            return _json({"ok": False, "error": "bad_origin"}, 403)
        creds = self._credentials()
        if not creds.enrolled:
            await self._audit_refusal("auth_login_refused", {"reason": "owner_not_enrolled"}, ip)
            return _json({"ok": False, "error": "owner_not_enrolled"}, 503)
        # The attempt is counted as a failure before anything is awaited, so concurrent attempts cannot all pass
        # the limit check while the first ones are still being verified.
        stamp = self.limiter.reserve(ip)
        if stamp is None:
            return await self._rate_limited(ip, self.limiter.retry_after(ip), "limit")
        body = await self._login_body(request)
        # At most max_verifying bcrypt checks at once (they run in worker threads the bridge needs for other work).
        # A refusal here checked nothing, so its reservation is given back.
        if self._verifying >= self.max_verifying:
            self.limiter.release(ip, stamp)
            return await self._rate_limited(ip, BUSY_RETRY_S, "busy")
        self._verifying += 1
        try:
            method = await self._verify_login(creds, body)
        except _LoginRefused as refused:
            # the reservation stays: it is this failure
            await self.audit("anonymous", "auth_login_failed", {"reason": refused.reason}, ip)
            logger.warning("Owner login refused (%s) from %s", refused.reason, ip)
            return _json({"ok": False, "error": "invalid_credentials"}, 401)
        except _StoreDown:
            self.limiter.release(ip, stamp)
            logger.error("Owner login could not be decided: the bridge store is unavailable")
            return _json({"ok": False, "error": "store_unavailable"}, 503)
        finally:
            self._verifying -= 1
        return await self._login_ok(request, ip, method, stamp)

    async def _rate_limited(self, ip: str, wait: float, reason: str) -> Response:
        headers = retry_after_header(wait)
        retry_after = int(headers["Retry-After"])
        await self._audit_refusal("auth_login_rate_limited", {"reason": reason, "retry_after": retry_after}, ip)
        return _json({"ok": False, "error": "rate_limited", "retry_after": retry_after}, 429, headers)

    async def _login_ok(self, request: Request, ip: str, method: str, stamp: float) -> Response:
        previous = await self.session_from_request(request)
        try:
            token, record = await self.mint_session(label="login", ip=ip,
                                                    user_agent=request.headers.get("user-agent", ""))
        except Exception:  # pylint: disable=broad-exception-caught
            self.limiter.release(ip, stamp)
            logger.error("Owner login: the session could not be stored", exc_info=True)
            return _json({"ok": False, "error": "store_unavailable"}, 503)
        self.limiter.release(ip, stamp)
        self.limiter.succeed(ip)
        if previous is not None and previous.session_id != record["sid"]:
            await self.revoke(previous.session_id, "rotated")
        factor, _, left = method.partition(":")
        detail: dict[str, Any] = {"method": factor, "session": record["sid"][:8]}
        if left:
            detail["recovery_left"] = int(left)
            await self.audit(USER, "auth_recovery_used", {"session": record["sid"][:8], "left": int(left)}, ip)
        await self.audit(USER, "auth_login_ok", detail, ip)
        logger.info("Owner login ok (%s) from %s", factor, ip)
        expires_at = int(record["expires_ts"])
        response = _json({"ok": True, "user": USER, "session_id": record["sid"], "expires_at": expires_at})
        self.set_cookie(response, request, token, SESSION_TTL_S)
        return response

    async def logout(self, request: Request) -> Response:
        ip = self._client_ip(request)
        if not self._public(request.scope):
            return _json({"detail": "Not Found"}, 404)
        if not check_origin(request.headers, self._allowed_origins()):
            await self._audit_refusal("auth_logout_refused", {"reason": "bad_origin"}, ip)
            return _json({"ok": False, "error": "bad_origin"}, 403)
        principal = await self.session_from_request(request)
        if principal is not None:
            await self.revoke(principal.session_id, "logout")
            await self.audit(USER, "auth_logout", {"session": principal.session_id[:8]}, ip)
        response = _json({"ok": True})
        self.clear_cookie(response, request)
        return response

    async def me(self, request: Request) -> Response:
        principal = await self.session_from_request(request)
        if principal is None:
            return _json({"ok": False, "authenticated": False}, 401)
        return _json({"ok": True, "authenticated": True, "user": USER, "session_id": principal.session_id,
                      "expires_at": principal.expires_at})

    def _build_router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route("/auth/login", self.login, methods=["POST"], include_in_schema=False)
        router.add_api_route("/auth/logout", self.logout, methods=["POST"], include_in_schema=False)
        router.add_api_route("/auth/me", self.me, methods=["GET"], include_in_schema=False)
        return router


async def _auth_required_response(_request: Request, _exc: Exception) -> Response:
    return JSONResponse({"error": "auth_required"}, status_code=401)


# ---------------------------------------------------------------- env file writing (enrolment)


def write_env_values(path: str, values: Mapping[str, str]) -> None:
    """Sets each name in values in the env file at path: an existing line is replaced in place, a new name is
    appended; every other line stays as it was. Written atomically with mode 600; values are never logged."""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    pending = dict(values)
    out: list[str] = []
    for line in lines:
        match = _ENV_LINE.match(line)
        name = match.group(1) if match else None
        if name in pending:
            out.append(f"{name}={pending.pop(name)}")
        elif name is None or name not in values:
            out.append(line)
    out.extend(f"{name}={value}" for name, value in pending.items())
    folder = os.path.dirname(os.path.abspath(path))
    fd, temp = tempfile.mkstemp(prefix=".env-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(out) + "\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    os.chmod(path, 0o600)


def env_file_has(path: str, name: str) -> bool:
    """True when the env file sets name to a non-empty value (the value itself is not returned)."""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            match = _ENV_LINE.match(line)
            if match and match.group(1) == name and line.split("=", 1)[1].strip().strip("'\""):
                return True
    return False


# ---------------------------------------------------------------- CLI


def _cli_store(db: str | None) -> BridgeStore:
    # repair=False: only the bridge itself may move a damaged database aside.
    return BridgeStore(db or bridge_db_path(), repair=False)


def _cli_actor() -> str:
    try:
        return f"cli:{getpass.getuser()}"[:64]
    except Exception:  # pylint: disable=broad-exception-caught
        return "cli"


async def _cli_audit(store: BridgeStore, action: str, detail: Mapping[str, Any]) -> None:
    try:
        await store.audit(_cli_actor(), action, dict(detail), "local")
    except Exception:  # pylint: disable=broad-exception-caught
        print("warning: the audit row could not be written", file=sys.stderr)


def _ask_new_password(ask: Callable[[str], str]) -> str | None:
    first = ask("New owner password: ")
    problem = password_problem(first)
    if problem:
        print(f"Password refused: {problem}.", file=sys.stderr)
        return None
    if not hmac.compare_digest(first.encode("utf-8"), ask("Repeat the password: ").encode("utf-8")):
        print("The two passwords differ.", file=sys.stderr)
        return None
    return first


async def _cmd_hash_password(store: BridgeStore, ask: Callable[[str], str]) -> int:
    password = _ask_new_password(ask)
    if password is None:
        await _cli_audit(store, "auth_cli_hash_password", {"ok": False})
        return 1
    print(await asyncio.to_thread(hash_password, password))
    await _cli_audit(store, "auth_cli_hash_password", {"ok": True})
    return 0


async def _cmd_mint_session(store: BridgeStore, ttl: int, label: str) -> int:
    if not 1 <= ttl <= MINT_TTL_MAX_S:
        print(f"--ttl must be 1..{MINT_TTL_MAX_S} seconds", file=sys.stderr)
        return 2
    auth = OwnerAuth(lambda: store, allowed_origins=lambda: (), client_ip=lambda _r: "local",
                     legacy_ok=lambda _r: False)
    try:
        token, record = await auth.mint_session(ttl_s=ttl, label=f"cli:{label}"[:64], ip="local")
    except RuntimeError:
        print("SESSION_JWT_SECRET is not set (or shorter than 32 characters): no session minted", file=sys.stderr)
        await _cli_audit(store, "auth_session_minted", {"ok": False, "reason": "no_jwt_secret"})
        return 1
    await _cli_audit(store, "auth_session_minted", {"ok": True, "session": record["sid"][:8], "ttl": ttl,
                                                    "label": label[:32]})
    print(f"{COOKIE_NAME}={token}")
    return 0


async def _cmd_revoke_all(store: BridgeStore) -> int:
    count = await store.revoke_all_sessions()
    await _cli_audit(store, "auth_revoke_all", {"revoked": count})
    print(f"revoked {count} session(s)")
    return 0


async def _cmd_enroll_check(store: BridgeStore) -> int:
    creds = OwnerCredentials.from_config()
    configured = len([h for h in (config.owner_recovery_hashes or []) if h.strip()])
    try:
        used = len(await store.kv_get(KV_RECOVERY_USED, []) or [])
    except Exception:  # pylint: disable=broad-exception-caught
        used = -1
    rows = [
        ("SESSION_JWT_SECRET", creds.jwt_ready),
        ("OWNER_PASSWORD_HASH", creds.password_hash is not None),
        ("OWNER_TOTP_SECRET", creds.totp_secret is not None),
    ]
    for name, ok in rows:
        print(f"{name}: {'ok' if ok else 'missing or unusable'}")
    print(f"OWNER_RECOVERY_HASHES: {len(creds.recovery_hashes)} usable of {configured}"
          + (f", {used} used" if used >= 0 else ", used count unavailable"))
    print("enrolled" if creds.enrolled else "owner_not_enrolled")
    await _cli_audit(store, "auth_enroll_check", {"enrolled": creds.enrolled, "missing": creds.problems()})
    return 0 if creds.enrolled else 1


async def _cmd_audit_summary(store: BridgeStore, prefix: str, limit: int) -> int:
    rows = await store.recent_audit(limit=limit, action_prefix=prefix)
    counts: dict[str, list[float]] = {}
    for row in rows:
        entry = counts.setdefault(str(row["action"]), [0, 0.0])
        entry[0] += 1
        entry[1] = max(entry[1], float(row["ts"]))
    await _cli_audit(store, "auth_audit_summary", {"prefix": prefix[:32], "rows": len(rows)})
    if not counts:
        print(f"no audit rows with prefix '{prefix}' in the newest {limit}")
        return 0
    for action, (count, last) in sorted(counts.items()):
        print(f"{action}: {int(count)} (last {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(last))} UTC)")
    return 0


def _print_qr(uri: str) -> None:
    try:
        import qrcode  # type: ignore[import-untyped]  # pylint: disable=import-outside-toplevel
    except ImportError:
        print("(install 'qrcode' to see a QR code here; the URI below works in every authenticator app)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


# pylint: disable-next=too-many-arguments,too-many-return-statements
async def _cmd_enroll(store: BridgeStore, env_file: str, *, ask: Callable[[str], str],
                      ask_visible: Callable[[str], str], interactive: bool, force: bool,
                      clock: Callable[[], float] = time.time) -> int:
    """Owner enrolment, run by the owner in their own terminal: nothing secret goes anywhere but that terminal and
    the env file."""
    if not interactive:
        print("enroll needs an interactive terminal (the QR and the recovery codes are shown only there)",
              file=sys.stderr)
        return 2
    if env_file_has(env_file, "OWNER_PASSWORD_HASH") and not force:
        print(f"{env_file} already has an owner enrolled; pass --force to replace it", file=sys.stderr)
        return 1
    password = _ask_new_password(ask)
    if password is None:
        await _cli_audit(store, "auth_enroll", {"ok": False, "reason": "password"})
        return 1
    secret = new_totp_secret()
    uri = provisioning_uri(secret)
    print("\nScan this with your authenticator app (or type the otpauth URI into it):\n")
    _print_qr(uri)
    print(uri)
    for _ in range(3):
        if totp_match(secret, ask_visible("\nCurrent 6-digit code from the app: "), clock()) is not None:
            break
        print("That code does not match; check the phone's clock and try again.")
    else:
        await _cli_audit(store, "auth_enroll", {"ok": False, "reason": "totp"})
        return 1
    codes = new_recovery_codes()
    hashes = await asyncio.to_thread(lambda: [hash_recovery_code(code) for code in codes])
    values = {"OWNER_PASSWORD_HASH": await asyncio.to_thread(hash_password, password),
              "OWNER_TOTP_SECRET": secret,
              "OWNER_RECOVERY_HASHES": ",".join(hashes)}
    if not env_file_has(env_file, "SESSION_JWT_SECRET"):
        values["SESSION_JWT_SECRET"] = secrets.token_urlsafe(64)
    write_env_values(env_file, values)
    print("\nRecovery codes (each works once; store them offline, they are not shown again):\n")
    for code in codes:
        print(f"  {code}")
    print(f"\nOwner enrolled in {env_file}. Restart the bridge so it reads the new values.")
    await _cli_audit(store, "auth_enroll", {"ok": True, "recovery_codes": len(codes),
                                            "jwt_secret_created": "SESSION_JWT_SECRET" in values})
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.core.bridge_auth",
                                     description="Owner session auth of the UI bridge (C-AUTH-1)")
    parser.add_argument("--db", help="bridge.db path (default BRIDGE_DB_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-password", help="ask for a password twice and print its OWNER_PASSWORD_HASH")
    mint = sub.add_parser("mint-session", help="create a session and print jarvis_session=<JWT>")
    mint.add_argument("--ttl", type=int, default=SESSION_TTL_S, help="seconds (1..43200)")
    mint.add_argument("--label", default="cli", help="a short label stored with the session")
    sub.add_parser("revoke-all", help="revoke every owner session")
    sub.add_parser("enroll-check", help="report which owner settings are set (exit 1 when not enrolled)")
    summary = sub.add_parser("audit-summary", help="audit actions with their counts")
    summary.add_argument("--prefix", default="auth", help="action prefix (default 'auth')")
    summary.add_argument("--limit", type=int, default=1000)
    enroll = sub.add_parser("enroll", help="enrol the owner into an env file (interactive)")
    enroll.add_argument("--env-file", required=True, help="the env file to write (e.g. a bridge.env)")
    enroll.add_argument("--force", action="store_true", help="replace an existing enrolment")
    return parser


async def _run(args: argparse.Namespace) -> int:
    store = _cli_store(args.db)
    try:
        if args.command == "hash-password":
            return await _cmd_hash_password(store, getpass.getpass)
        if args.command == "mint-session":
            return await _cmd_mint_session(store, args.ttl, args.label)
        if args.command == "revoke-all":
            return await _cmd_revoke_all(store)
        if args.command == "enroll-check":
            return await _cmd_enroll_check(store)
        if args.command == "audit-summary":
            return await _cmd_audit_summary(store, args.prefix, max(1, min(args.limit, 100_000)))
        return await _cmd_enroll(store, args.env_file, ask=getpass.getpass, ask_visible=input,
                                 interactive=sys.stdin.isatty() and sys.stdout.isatty(), force=args.force)
    finally:
        await store.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
