"""Desktop Link protocol library (contract K-70 in docs/CONTRACTS.md v5; plan task C-115).

One audited implementation for the bridge relay (C-116), the agent peers (C-119) and the laptop Desktop Node
(C-120): canonical JSON, Ed25519 envelopes and signed HTTP, X25519 + HKDF-SHA256 + ChaCha20-Poly1305 session
crypto (message bodies and 1 MiB blob chunks), the eight receiver checks in K-70 order, the signed ping/pong
clock estimator, start_by, result_resend, node notices and the persisted SeenStore. The bridge holds no
private key: it can verify and relay, but it can neither forge nor read a command.

Pure library: no network and nothing runs at import time (win32crypt is imported only when DPAPI is used).
Errors never carry message content, keys or plaintext.

Decisions where K-70 is silent:
- Encodings: ids, sess, nonces, keys and signatures are lowercase hex; a sealed body is
  {"enc": "chacha20-poly1305", "data": base64(nonce || ciphertext || tag)}. Keys are raw 32-byte values.
- canonical_json and loads_strict also refuse NaN/Infinity, non-string keys, lone surrogates and nesting
  deeper than 32; for every other value the bytes are exactly K-70's json.dumps(...).encode('utf-8').
  ts and start_by are epoch seconds in (0, 1e11).
- Refusals: K-70 gives a failed exec a signed refusal, which the agent reports as 'not executed'.
  refusal_code allows one only for an authentic message that provably has not run and never will. A replay
  gets none, nor does an id the SeenStore could not check. A check 5/6 failure gets one only if all hold:
  its id was new to the store; |age| < Receiver.refusal_horizon_s = min(retention - 2 x window, 1 h), with
  age = (own clock + offset) - ts, so that had it run (or once its time comes) its id is still in the store;
  and ts - offset >= Receiver.refusal_floor_s = SeenStore.created_at + SeenStore.wall_step_s + 2 x window, so
  that it cannot have run before the store existed (wall_step_s: a forward step of the node clock since the
  store was opened; a step before that open, or of the sender's clock since it signed, only the margin
  absorbs); and the offset is measured: while the Receiver's ClockEstimator has no sample (before the first
  pong, after it expired, while it must re-measure) ts - offset cannot be put on the node clock. At a Receiver
  without a store it never does. Its id is recorded either way, so a later delivery (a 'future' message whose
  time comes, a clock correction) is a replay.
- Pinned Ed25519 public keys must be canonical and not of small order (libsodium's rule): OpenSSL accepts
  such points, and a fixed forged signature then verifies for most or all messages.
- Size caps apply to the canonical envelope including sig (KiB = 1024 bytes): exec 96, result and
  result_resend 256, event 32 (K-70); hello 64 (wal_report), notice 4 and every other type 32.
- sess is required on every signed message except agent_hello (the node's own messages carry it too).
- Freshness uses the receiver's own clock. A Receiver given a ClockEstimator adds the offset measured from
  verified, signed pongs that answer its own pings (RTT <= 5 s), and hands that estimator every pong it
  verifies, so a link only registers its pings (ClockEstimator.ping_sent). Without one, or while it has no
  sample, the check is K-70's exact |own clock - ts| <= 120 s. The bridge's server_time hint is never used.
- CLOCK_SKEW fails closed at the node (ClockEstimator.check_actuation): it holds while the latest sample is
  above 60 s, also once that sample has expired, and while the estimator must re-measure, until a new sample
  shows otherwise. skew_exceeded(), the agent's fast path, counts the re-measure state but not an expired
  sample, so a laptop that went offline with a skewed clock reads as offline. Before the first sample, or
  after one within 60 s expired, there is nothing to refuse. Each endpoint keeps one estimator for the life of
  its process, across reconnects.
- Clock probes, so that a clock step above the window is measured and reported (CLOCK_SKEW) instead of
  silently cutting the link: a ping body is {} and a pong body {echo, t1}. A ping is accepted whatever its ts
  (it only asks for a pong), and the bridge relays a pong whatever its ts. An agent or node refuses a pong
  outside the window, but first offers it, when it is authentic, in session, exactly {echo, t1} and new to the
  SeenStore (if any), to its ClockEstimator, which uses it only if it echoes one of its own pings sent to that
  peer at most 5 s ago (the echo proves it fresh).
- A new session after a clock fix: a sample stays valid for 600 s and a hello is no clock probe, so a hello or
  agent_hello that fails check 5 with the offset but passes it on the raw own clock is accepted. Once the
  handshake with it succeeds (accept_node_hello, accept_agent_hello), the receiver's estimator must re-measure
  (ClockEstimator.reset): no offset for check 5, and CLOCK_SKEW until its next sample. A hello the bridge held
  back, or one sent after a partial clock fix, takes the same path, so it can hold actuation back until the
  next pong but never allow it. A hello whose handshake fails leaves the sample alone; a replayed one is a replay.
- Notices: node state for the bridge itself, signed but not encrypted: typ 'notice', to 'bridge', room '',
  origin 'internal', no call_id, tool, seq or start_by. Only a notice may be addressed to 'bridge' (check 2)
  and no agent or node receiver accepts anything addressed to 'bridge' (check 3), so a relayed one is refused.
  Kinds vitals_pc, desktop_state, power and alert, each with an exact field list (NOTICE_FIELDS): a missing,
  unknown, mistyped or out-of-range field is refused when building (BAD_ARGS) and when verifying (check 8).
  The only free text is short and printable (an alert message of up to 200 characters, host up to 64,
  disk_label up to 32); arguments, results, paths, window titles, clipboard text and locations never go there
  (a node duty) but only sealed to one agent peer, as the event kinds location, window_context,
  clipboard_suggestion and log_batch.
- Signed HTTP: the path without the public '/vortex' mount (nginx strips it), the query as sorted RFC 3986
  pairs, ts in whole seconds. Bridge hints (welcome, status, control, dl_undeliverable) are unsigned and a
  bridge control may only tighten.
- Whatever a peer or client sends, Receiver.verify, parse_bridge_hint and verify_http_headers raise only
  DesktopLinkError, so every refusal can be audited, counted and alerted on.
"""
# pylint: disable=too-many-lines  # one audited K-70 module shared by the bridge, the agent peers and the node
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import queue
import re
import secrets
import sqlite3
import statistics
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, quote, unquote

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROTO_VERSION = 1
SUPPORTED_VERSIONS: tuple[int, ...] = (PROTO_VERSION,)
MSG_CONTEXT = b"jarvis/desktop/msg/v1|"
HTTP_CONTEXT = b"jarvis/desktop/http/v1|"
AEAD_INFO = b"jarvis/desktop/aead/v1"
AEAD_ALG = "chacha20-poly1305"
AEAD_OVERHEAD = 12 + 16  # random nonce + Poly1305 tag
FRESHNESS_WINDOW_S = 120.0
CLOCK_SKEW_LIMIT_S = 60.0
SEEN_RETENTION_S = 24 * 3600.0
REFUSAL_HORIZON_S = 3600.0  # cap on Receiver.refusal_horizon_s: no signed refusal for a message this old
START_BY_DELAY_S = 2.5
START_BY_MAX_AHEAD_S = 30.0
PING_MAX_RTT_S = 5.0
MAX_EPOCH_S = 1e11  # far beyond any real ts or start_by; also bounds a huge JSON integer without float overflow
KIB = 1024
SIZE_CAPS: Mapping[str, int] = MappingProxyType({
    "exec": 96 * KIB, "result": 256 * KIB, "result_resend": 256 * KIB, "event": 32 * KIB, "hello": 64 * KIB,
    "notice": 4 * KIB})
DEFAULT_SIZE_CAP = 32 * KIB
MAX_ENVELOPE_BYTES = max(SIZE_CAPS.values())
MAX_JSON_DEPTH = 32
BLOB_CHUNK_BYTES = 1024 * KIB
HTTP_MAX_BODY_BYTES = 30 * 1024 * KIB
HTTP_PUBLIC_PREFIX = "/vortex"
HTTP_MAX_QUERY_FIELDS = 100
HTTP_HEADER_NAMES = ("X-Desk-Ts", "X-Desk-Nonce", "X-Desk-Len", "X-Desk-Sha256", "X-Desk-Sig")
HTTP_STATUS: Mapping[str, int] = MappingProxyType({
    "size": 413, "length": 400, "format": 400, "digest": 400, "signature": 403, "stale": 401, "future": 401,
    "replay": 409, "seen_store": 503})
ORIGINS = frozenset({"llm", "job", "scheduler", "internal", "canary"})
BRIDGE_IDENT = "bridge"  # the relay: never signs; 'to' of a notice, 'from' of a hint
AGENT_TO_PC = frozenset({"agent_hello", "exec", "cancel", "result_ack", "control", "ping", "pong", "sync_request"})
PC_TO_AGENT = frozenset({"hello", "ack", "progress", "result", "result_resend", "event", "ping", "pong"})
PC_TO_BRIDGE = frozenset({"notice"})
ENCRYPTED_TYPES = frozenset({"exec", "ack", "progress", "result", "result_resend", "cancel", "event"})
HELLO_TYPES = frozenset({"hello", "agent_hello"})
BRIDGE_HINT_TYPES = frozenset({"welcome", "status", "control", "dl_undeliverable"})
TIGHTEN_ONLY_KINDS = frozenset({"desktop_stop", "desktop_off", "active_job", "link_disabled"})
AGENT_CONTROL_KINDS = TIGHTEN_ONLY_KINDS | {"stop_all"}
EVENT_KINDS = frozenset({"location", "window_context", "clipboard_suggestion", "log_batch"})  # sealed, to one agent
NOTICE_ROOM, NOTICE_ORIGIN = "", "internal"
DESKTOP_MODES = frozenset({"off", "read", "full"})
POWER_STATES = frozenset({"suspend", "resume", "shutdown", "logoff"})
ALERT_CODES: Mapping[str, str] = MappingProxyType({  # alert notice code -> its fixed K-74 level
    "SIGNATURE_INVALID": "critical", "APPROVAL_INVALID": "critical", "CLOCK_SKEW": "warning",
    "MANIFEST_MISMATCH": "warning", "DESKTOP_RATE_LIMITED": "warning"})
ALERT_MESSAGE_MAX_CHARS = 200
KIND_ID, KIND_CALL_ID, KIND_CONFIRM_ID, KIND_HTTP_NONCE = "id", "call_id", "confirm_id", "http_nonce"
DPAPI_ENTROPY = b"jarvis/desktop/keys/v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
PC_KEY_KIND = "desktop_pc_signing_key"

_HEX16 = re.compile(r"[0-9a-f]{32}")  # 16 bytes: id, sess, nonces
_HEX32 = re.compile(r"[0-9a-f]{64}")  # 32 bytes: keys, sha256
_HEX64 = re.compile(r"[0-9a-f]{128}")  # 64 bytes: Ed25519 signatures
_ID_PART = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_ID_RE = re.compile(_ID_PART)
_FROM_RE = re.compile(rf"(agent|pc):({_ID_PART})")
_TO_RE = re.compile(rf"(agent|pc):({_ID_PART}|\*)")
_TYP_RE = re.compile(r"[a-z_]{1,32}")
_ROOM_RE = re.compile(r"[A-Za-z0-9._:-]{0,64}")
_CALL_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
_TOOL_RE = re.compile(r"[A-Za-z0-9_.]{1,64}")
_SPEECH_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_B64_RE = re.compile(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_DECIMAL_RE = re.compile(r"0|[1-9][0-9]{0,11}")
_METHOD_RE = re.compile(r"[A-Za-z]{1,16}")
_ED25519_P = 2 ** 255 - 19
# The y coordinates (sign bit cleared) of the eight small-order edwards25519 points: libsodium's blocklist.
# Its other two entries, p and p + 1, are non-canonical forms that _weak_public_key refuses as y >= p.
_SMALL_ORDER_Y = frozenset(bytes.fromhex(value) for value in (
    "0100000000000000000000000000000000000000000000000000000000000000",  # y = 1: the identity (order 1)
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",  # y = p - 1: order 2
    "0000000000000000000000000000000000000000000000000000000000000000",  # y = 0: order 4
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",  # order 8
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a"))  # order 8
_HEADER_FIELDS = ("v", "typ", "id", "ts", "from", "to", "sess", "room", "origin", "call_id", "tool", "seq",
                  "start_by")
_ENVELOPE_FIELDS = frozenset(_HEADER_FIELDS) | {"body", "sig"}
_REQUIRED_BY_TYPE: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "exec": ("call_id", "tool", "start_by"), "ack": ("call_id",), "progress": ("call_id",),
    "result": ("call_id",), "result_resend": ("call_id",), "cancel": ("call_id",), "result_ack": ("call_id",)})
_BODY_MIRRORS = ("call_id", "tool", "room", "origin", "start_by", "seq")
_VERIFIED = object()


class CanonicalJsonError(ValueError):
    """The value has no canonical JSON form (NaN/inf, non-string key, lone surrogate, unsupported type, too deep)."""


class DesktopLinkError(Exception):
    """A refused Desktop Link message or request; the caller drops it and audits DL_REJECT.

    reason: size, version, format, route, signature, stale, future, session, replay, seen_store, direction,
    decrypt, handshake, proto, clock_skew, start_window, length or digest. code: 'DL_REJECT', 'BAD_ARGS'
    (refused while building) or a K-12 code. authentic: the signature had been verified when the check failed.
    refusable=False: no signed refusal may answer it (see refusal_code). str() never contains message content.
    """

    def __init__(self, reason: str, *, code: str = "DL_REJECT", authentic: bool = False,
                 refusable: bool = True) -> None:
        super().__init__(f"{code}:{reason}")
        self.reason = reason
        self.code = code
        self.authentic = authentic
        self.refusable = refusable

    @property
    def refusal_code(self) -> str | None:
        """K-12 code for the signed refusal of an exec, or None when no refusal may be sent.

        The agent reports a refusal as 'not executed' (DESKTOP_NOT_STARTED), so one is allowed only for an
        authentic message that provably has not run and never will. A replay gets none, nor does an id that
        could not be checked (seen_store) or a failure marked refusable=False: a check 5/6 failure with
        |age| >= Receiver.refusal_horizon_s (had it run, or once its time comes, the SeenStore may no longer
        hold its id), with ts - offset < Receiver.refusal_floor_s (it may have run before the store existed),
        while the Receiver's ClockEstimator has no sample (the offset, and so both bounds, are unknown), or at
        a Receiver without a store. Before it signs, the node still looks the call_id up in its WAL: a retry
        (new id, same call_id) of a call that already ran gets that call's real outcome, never a refusal.
        """
        if not (self.authentic and self.refusable) or self.reason in ("replay", "seen_store"):
            return None
        return "DESKTOP_REJECTED" if self.code == "DL_REJECT" else self.code


# --- canonical JSON -----------------------------------------------------------------------------------------

def _check_text(text: str) -> None:
    """A lone surrogate (JSON allows the escape) has no UTF-8 form, so canonical_json could not sign it."""
    if not text.isascii():
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise CanonicalJsonError("lone surrogate in a string") from None


def _check_json(value: Any, depth: int = 0) -> None:
    """Refuses what json.dumps would encode ambiguously or not at all."""
    if depth > MAX_JSON_DEPTH:
        raise CanonicalJsonError("nested too deep")
    if isinstance(value, str):
        _check_text(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError("object keys must be strings")
            _check_text(key)
            _check_json(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_json(item, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise CanonicalJsonError("NaN and infinity are not allowed")
    elif not (value is None or isinstance(value, (int, float))):
        raise CanonicalJsonError(f"unsupported type {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """K-70: json.dumps(sort_keys=True, separators=(',',':'), ensure_ascii=False).encode('utf-8'), strict."""
    _check_json(value)
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return text.encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise CanonicalJsonError("value cannot be encoded") from None


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise CanonicalJsonError("duplicate object key")
        out[key] = value
    return out


def _no_constant(_name: str) -> Any:
    raise CanonicalJsonError("NaN and infinity are not allowed")


def loads_strict(raw: bytes | str) -> Any:
    """Parses wire JSON: UTF-8 only; duplicate keys, NaN/Infinity (also 1e400), lone surrogates and deep
    nesting are refused, so whatever it returns canonical_json can encode."""
    try:
        text = bytes(raw).decode("utf-8") if not isinstance(raw, str) else raw
        value = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise CanonicalJsonError("invalid JSON") from None
    _check_json(value)
    return value


def args_sha256(args: Mapping[str, Any]) -> str:
    """sha256 hex of canonical_json(args): exec.args_sha256 and approval.args_sha256 (K-70, K-76)."""
    return hashlib.sha256(canonical_json(dict(args))).hexdigest()


def digest_equal(left: Any, right: Any) -> bool:
    """Constant-time, case-insensitive comparison of two hex digests; False when either is not a str."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left.lower().encode("utf-8"), right.lower().encode("utf-8"))


# --- keys ---------------------------------------------------------------------------------------------------

def _hex_bytes(text: Any, size: int) -> bytes:
    """Exactly `size` bytes as hex (case-insensitive, outer whitespace ignored); the error never echoes it."""
    if isinstance(text, str):
        clean = text.strip().lower()
        if len(clean) == 2 * size and re.fullmatch(r"[0-9a-f]*", clean):
            return bytes.fromhex(clean)
    raise ValueError(f"expected {size} bytes as hex")


def generate_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def signing_key_from_hex(text: Any) -> Ed25519PrivateKey:
    """Loads DESKTOP_AGENT_SIGNING_KEY / DESKTOP_PC_SIGNING_KEY: the raw 32-byte Ed25519 seed as hex."""
    return Ed25519PrivateKey.from_private_bytes(_hex_bytes(text, 32))


def signing_key_to_hex(key: Ed25519PrivateKey) -> str:
    """The private seed as hex. Only key files may hold it; never print or log it."""
    return key.private_bytes_raw().hex()


def public_key_hex(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public.public_bytes_raw().hex()


def _weak_public_key(raw: bytes) -> bool:
    """A non-canonical encoding (y >= p) or a small-order point, whatever its sign bit (libsodium's rule)."""
    y_bytes = raw[:31] + bytes([raw[31] & 0x7F])
    return int.from_bytes(y_bytes, "little") >= _ED25519_P or y_bytes in _SMALL_ORDER_Y


def public_key_from_hex(text: Any) -> Ed25519PublicKey:
    """Loads DESKTOP_AGENT_PUBLIC_KEY / DESKTOP_PC_PUBLIC_KEY (raw 32 bytes as hex).

    A small-order point or a non-canonical encoding raises ValueError: OpenSSL accepts them, and a fixed
    forged signature then verifies for most or all messages.
    """
    raw = _hex_bytes(text, 32)
    if _weak_public_key(raw):
        raise ValueError("not a usable Ed25519 public key (small order or non-canonical)")
    return Ed25519PublicKey.from_public_bytes(raw)


def _check_pinned_key(key: Any) -> None:
    """A configuration error (ValueError) for a pinned key that public_key_from_hex would have refused."""
    if isinstance(key, Ed25519PublicKey) and _weak_public_key(key.public_bytes_raw()):
        raise ValueError("a pinned Ed25519 public key is a small-order or non-canonical point")


def _on_windows() -> bool:
    return sys.platform == "win32"


def _win32crypt() -> tuple[Any, type[Exception]]:
    if not _on_windows():
        raise OSError("DPAPI is available on Windows only")
    import pywintypes  # pylint: disable=import-outside-toplevel,import-error
    import win32crypt  # pylint: disable=import-outside-toplevel,import-error
    return win32crypt, pywintypes.error  # pylint: disable=no-member


def dpapi_protect(data: bytes, description: str = "JARVIS desktop link key") -> bytes:
    """Wraps bytes with Windows DPAPI for the current user (CRYPTPROTECT_UI_FORBIDDEN). Windows only."""
    crypt, error = _win32crypt()
    try:
        return crypt.CryptProtectData(  # pylint: disable=c-extension-no-member
            data, description, DPAPI_ENTROPY, None, None, CRYPTPROTECT_UI_FORBIDDEN)
    except error:
        raise OSError("DPAPI could not wrap the data") from None


def dpapi_unprotect(blob: bytes) -> bytes:
    """Unwraps dpapi_protect() output; only the same Windows user can do it."""
    crypt, error = _win32crypt()
    try:
        _, data = crypt.CryptUnprotectData(  # pylint: disable=c-extension-no-member
            blob, DPAPI_ENTROPY, None, None, CRYPTPROTECT_UI_FORBIDDEN)
    except error:
        raise OSError("DPAPI could not unwrap the data (another user, or a damaged file)") from None
    return data


def pc_key_document(key: Ed25519PrivateKey, *, created: int | None = None) -> bytes:
    """The plaintext that keys.dpapi wraps (canonical JSON with the seed). Never log or print it."""
    return canonical_json({"alg": "ed25519", "created": int(time.time() if created is None else created),
                           "kind": PC_KEY_KIND, "public": public_key_hex(key), "seed": signing_key_to_hex(key),
                           "v": 1})


def parse_pc_key_document(data: bytes) -> Ed25519PrivateKey:
    try:
        doc = loads_strict(data)
        if not isinstance(doc, dict) or doc.get("kind") != PC_KEY_KIND or doc.get("alg") != "ed25519":
            raise ValueError("wrong kind")
        key = signing_key_from_hex(doc.get("seed"))
    except ValueError:
        raise ValueError("not a Desktop Link PC key file") from None
    if not digest_equal(public_key_hex(key), doc.get("public")):
        raise ValueError("the PC key file is inconsistent")
    return key


def load_pc_key_file(path: str | os.PathLike[str]) -> Ed25519PrivateKey:
    """Node side (C-120): unwraps %LOCALAPPDATA%\\JARVIS\\node\\keys.dpapi written by gen_desktop_keys.py."""
    return parse_pc_key_document(dpapi_unprotect(Path(path).read_bytes()))


def _role(ident: str) -> str:
    return ident.partition(":")[0]


def _answers(target: str, sender: str) -> bool:
    """A pong comes from the peer its ping went to (for '<role>:*', any peer of that role)."""
    return sender == target or (target.endswith(":*") and _role(sender) == _role(target))


def _match(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _member(value: Any, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_time(value: Any) -> bool:
    """Epoch seconds in (0, MAX_EPOCH_S). int/float comparison is exact: a 400-digit int cannot overflow it."""
    return (_is_int(value) or isinstance(value, float)) and 0 < value < MAX_EPOCH_S


# --- session crypto -----------------------------------------------------------------------------------------

class SessionKey:
    """ChaCha20-Poly1305 key of one (node sess, agent peer) pair; repr() never shows key material."""

    def __init__(self, key: bytes, *, sess: str, peer_id: str) -> None:
        if len(key) != 32:
            raise ValueError("a session key has 32 bytes")
        self._aead = ChaCha20Poly1305(key)
        self.sess = sess
        self.peer_id = peer_id

    def __repr__(self) -> str:
        return f"SessionKey(sess={self.sess[:8]}..., peer_id={self.peer_id})"

    def seal(self, plaintext: bytes, aad: bytes) -> bytes:
        """nonce (12 random bytes) || ciphertext || tag."""
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, plaintext, aad)

    def open(self, sealed: bytes, aad: bytes, *, authentic: bool = False) -> bytes:
        """Inverse of seal(); any failure raises DesktopLinkError('decrypt') and yields no plaintext."""
        if len(sealed) < AEAD_OVERHEAD:
            raise DesktopLinkError("decrypt", authentic=authentic)
        try:
            return self._aead.decrypt(sealed[:12], sealed[12:], aad)
        except InvalidTag:
            raise DesktopLinkError("decrypt", authentic=authentic) from None


def new_ephemeral_key() -> X25519PrivateKey:
    """A fresh X25519 key for one hello (never reused for another connection)."""
    return X25519PrivateKey.generate()


def eph_public_hex(key: X25519PrivateKey) -> str:
    return key.public_key().public_bytes_raw().hex()


def derive_session_key(own: X25519PrivateKey, peer_eph_pub: Any, *, sess: str, peer_id: str) -> SessionKey:
    """K-70: HKDF-SHA256(X25519, salt = node sess || agent peer_id, info 'jarvis/desktop/aead/v1')."""
    if not _match(_HEX16, sess) or not _match(_ID_RE, peer_id):
        raise DesktopLinkError("handshake")
    try:
        shared = own.exchange(X25519PublicKey.from_public_bytes(_hex_bytes(peer_eph_pub, 32)))
    except ValueError:  # bad hex, or a low-order point that gives an all-zero secret
        raise DesktopLinkError("handshake") from None
    salt = sess.encode("ascii") + peer_id.encode("utf-8")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=AEAD_INFO).derive(shared)
    return SessionKey(key, sess=sess, peer_id=peer_id)


def blob_chunk_count(size: int) -> int:
    return max(1, -(-size // BLOB_CHUNK_BYTES))


def encrypted_blob_size(size: int) -> int:
    return size + blob_chunk_count(size) * AEAD_OVERHEAD


def _blob_aad(blob_id: str, index: int, count: int, size: int) -> bytes:
    return canonical_json({"blob_id": blob_id, "i": index, "n": count, "size": size, "typ": "blob",
                           "v": PROTO_VERSION})


def encrypt_blob(key: SessionKey, blob_id: str, data: bytes) -> bytes:
    """Encrypts a blob in 1 MiB chunks; each chunk's AAD binds blob_id, its index, the count and the size."""
    if not _match(_HEX16, blob_id):
        raise ValueError("blob_id must be 32 lowercase hex characters")
    size, count = len(data), blob_chunk_count(len(data))
    if encrypted_blob_size(size) > HTTP_MAX_BODY_BYTES:
        raise DesktopLinkError("size", code="BAD_ARGS")
    return b"".join(key.seal(data[i * BLOB_CHUNK_BYTES:(i + 1) * BLOB_CHUNK_BYTES], _blob_aad(blob_id, i, count, size))
                    for i in range(count))


def decrypt_blob(key: SessionKey, blob_id: str, data: bytes, *, size: int, sha256: str | None = None) -> bytes:
    """Decrypts encrypt_blob() output; size and sha256 come from the signed, encrypted result body.

    Reordered, truncated or modified chunks raise DesktopLinkError and no plaintext is returned.
    """
    if not _match(_HEX16, blob_id) or not _is_int(size) or size < 0 or len(data) != encrypted_blob_size(size):
        raise DesktopLinkError("length")
    count, parts, pos = blob_chunk_count(size), [], 0
    for index in range(count):
        end = pos + min(BLOB_CHUNK_BYTES, size - index * BLOB_CHUNK_BYTES) + AEAD_OVERHEAD
        parts.append(key.open(data[pos:end], _blob_aad(blob_id, index, count, size)))
        pos = end
    plain = b"".join(parts)
    if sha256 is not None and not digest_equal(hashlib.sha256(plain).hexdigest(), sha256):
        raise DesktopLinkError("digest")
    return plain


# --- envelopes ----------------------------------------------------------------------------------------------

def new_message_id() -> str:
    return secrets.token_hex(16)


def new_session_nonce() -> str:
    """The node's session nonce: 32 hex, rotated on every connect."""
    return secrets.token_hex(16)


def _now(now: float | None) -> float:
    return round(time.time() if now is None else float(now), 3)


def _size_cap(typ: Any) -> int:
    return SIZE_CAPS.get(typ, DEFAULT_SIZE_CAP) if isinstance(typ, str) else DEFAULT_SIZE_CAP


def _valid_seq(seq: Any) -> bool:
    return (isinstance(seq, dict) and set(seq) == {"speech_id", "n"} and _match(_SPEECH_RE, seq["speech_id"])
            and _is_int(seq["n"]) and 0 <= seq["n"] < 2 ** 31)


_OPTIONAL_CHECKS: Mapping[str, Callable[[Any], bool]] = MappingProxyType({
    "sess": lambda value: _match(_HEX16, value), "call_id": lambda value: _match(_CALL_ID_RE, value),
    "tool": lambda value: _match(_TOOL_RE, value), "seq": _valid_seq, "start_by": _is_time})
_CALL_FIELDS = ("call_id", "tool", "seq", "start_by")


# --- notices ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class NoticeField:
    """One field of a notice body: required or optional (absent; never null), and its rule (what check takes)."""

    required: bool
    rule: str
    check: Callable[[Any], bool] = field(repr=False, compare=False)


def _number(low: float, high: float) -> Callable[[Any], bool]:
    """An int or float (never bool) in [low, high]: also refuses NaN and infinity."""
    return lambda value: (_is_int(value) or isinstance(value, float)) and low <= value <= high


def _integer(low: int, high: int) -> Callable[[Any], bool]:
    return lambda value: _is_int(value) and low <= value <= high


def _text(limit: int) -> Callable[[Any], bool]:
    """1..limit characters, all printable: no control, format (bidi, zero-width), line or paragraph characters."""
    return lambda value: isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


def _one_of(allowed: frozenset[str]) -> Callable[[Any], bool]:
    return lambda value: _member(value, allowed)


def _shaped(pattern: re.Pattern[str]) -> Callable[[Any], bool]:
    return lambda value: _match(pattern, value)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


_PERCENT = "number 0..100"
NOTICE_FIELDS: Mapping[str, Mapping[str, NoticeField]] = MappingProxyType({
    "vitals_pc": MappingProxyType({  # the K-26/K-40 vitals the UI shows
        "cpu": NoticeField(True, _PERCENT, _number(0, 100)),
        "ram": NoticeField(True, _PERCENT, _number(0, 100)),
        "disk": NoticeField(True, _PERCENT, _number(0, 100)),
        "disk_label": NoticeField(False, "text, 1..32 printable characters", _text(32)),
        "host": NoticeField(False, "text, 1..64 printable characters", _text(64)),
        "cores": NoticeField(False, "integer 1..4096", _integer(1, 4096)),
        "ram_total_gb": NoticeField(False, "number 0..65536", _number(0, 65536))}),
    "desktop_state": MappingProxyType({  # K-74/K-76 node state; never presence
        "mode": NoticeField(True, "'off' | 'read' | 'full'", _one_of(DESKTOP_MODES)),
        "locked": NoticeField(True, "boolean", _is_bool),
        "panic": NoticeField(True, "boolean", _is_bool)}),
    "power": MappingProxyType({
        "state": NoticeField(True, "'suspend' | 'resume' | 'shutdown' | 'logoff'", _one_of(POWER_STATES))}),
    "alert": MappingProxyType({  # K-74 alerts the node raises
        "code": NoticeField(True, "a key of ALERT_CODES", _one_of(frozenset(ALERT_CODES))),
        "level": NoticeField(True, "ALERT_CODES[code]", _one_of(frozenset(ALERT_CODES.values()))),
        "count": NoticeField(False, "integer 1..1000000 (repeats folded into one notice)", _integer(1, 10 ** 6)),
        "message": NoticeField(False, f"text, 1..{ALERT_MESSAGE_MAX_CHARS} printable characters",
                               _text(ALERT_MESSAGE_MAX_CHARS)),
        "call_id": NoticeField(False, "a call_id ([A-Za-z0-9_-]{8,64})", _shaped(_CALL_ID_RE)),
        "tool": NoticeField(False, "a tool name ([A-Za-z0-9_.]{1,64})", _shaped(_TOOL_RE)),
        "peer": NoticeField(False, "a link ident ('agent:<id>' or 'pc:<id>')", _shaped(_FROM_RE))}),
})
NOTICE_KINDS = frozenset(NOTICE_FIELDS)


def _check_notice_body(body: Mapping[str, Any]) -> None:
    """Check 8 for a notice: a kind on NOTICE_KINDS ('direction': no other kind may travel in plaintext), then
    exactly that kind's fields, each passing its rule, and an alert level equal to ALERT_CODES[code] ('format')."""
    kind = body.get("kind")
    spec = NOTICE_FIELDS.get(kind) if isinstance(kind, str) else None
    if spec is None:
        raise DesktopLinkError("direction")
    names = set(body) - {"kind"}
    valid = (names <= set(spec) and all(name in names for name, rule in spec.items() if rule.required)
             and all(spec[name].check(body[name]) for name in names)
             and (kind != "alert" or body["level"] == ALERT_CODES[body["code"]]))
    if not valid:
        raise DesktopLinkError("format")


def _validate_header(header: Mapping[str, Any]) -> None:
    """K-70 field formats; required fields per type; 'to' is 'bridge' exactly for a notice, which also has
    room '', origin 'internal' and no call fields. Raises DesktopLinkError('format')."""
    typ = header.get("typ")
    if not _match(_TYP_RE, typ):  # first: a list or dict typ is unhashable in the lookup below
        raise DesktopLinkError("format")
    required = (() if typ == "agent_hello" else ("sess",)) + _REQUIRED_BY_TYPE.get(typ, ())
    notice = typ == "notice"
    valid = (_is_int(header.get("v")) and _match(_HEX16, header.get("id"))
             and _is_time(header.get("ts")) and _match(_FROM_RE, header.get("from"))
             and (header.get("to") == BRIDGE_IDENT if notice else _match(_TO_RE, header.get("to")))
             and _match(_ROOM_RE, header.get("room"))
             and _member(header.get("origin"), ORIGINS) and all(name in header for name in required)
             and all(check(header[name]) for name, check in _OPTIONAL_CHECKS.items() if name in header)
             and (not notice or (header["room"] == NOTICE_ROOM and header["origin"] == NOTICE_ORIGIN
                                 and not any(name in header for name in _CALL_FIELDS))))
    if not valid:
        raise DesktopLinkError("format")


def _sealed_ok(body: Mapping[str, Any]) -> bool:
    data = body.get("data")
    return (set(body) == {"enc", "data"} and body.get("enc") == AEAD_ALG and _match(_B64_RE, data)
            and len(data) >= 40)


def _pong_body_ok(body: Mapping[str, Any]) -> bool:
    return set(body) == {"echo", "t1"} and _match(_HEX16, body["echo"]) and _is_time(body["t1"])


def _check_direction(header: Mapping[str, Any], body: Mapping[str, Any]) -> None:
    """Check 8: typ allowed from the sender's role to its target (agent -> pc, pc -> agent, pc -> bridge);
    sealed types go to one peer; and the plaintext bodies K-70 fixes: a control kind on its list (an agent
    control only tightens, plus stop_all), ping {}, pong {echo, t1} and a notice per NOTICE_FIELDS."""
    typ, to = header["typ"], header["to"]
    if _role(header["from"]) == "agent":
        allowed = AGENT_TO_PC
    else:
        allowed = PC_TO_BRIDGE if to == BRIDGE_IDENT else PC_TO_AGENT
    if (typ not in allowed or (typ in ENCRYPTED_TYPES and to.endswith(":*"))
            or (typ == "control" and not _member(body.get("kind"), AGENT_CONTROL_KINDS))):
        raise DesktopLinkError("direction")
    if typ == "notice":
        _check_notice_body(body)
    elif (typ == "ping" and body) or (typ == "pong" and not _pong_body_ok(body)):
        raise DesktopLinkError("format")


def _agent_party(header: Mapping[str, Any]) -> str:
    """The agent peer id of a message (its sender or its receiver): the peer half of the session key."""
    ident = header["from"] if _role(header["from"]) == "agent" else header["to"]
    return ident.partition(":")[2]


def _key_matches(key: SessionKey, header: Mapping[str, Any]) -> bool:
    return (hmac.compare_digest(key.sess.encode("ascii"), str(header.get("sess", "")).encode("utf-8"))
            and key.peer_id == _agent_party(header))


def _seal_body(header: Mapping[str, Any], body: Mapping[str, Any], key: SessionKey | None) -> dict[str, str]:
    if key is None or not _key_matches(key, header):
        raise DesktopLinkError("session")
    sealed = key.seal(canonical_json(dict(body)), canonical_json(dict(header)))
    return {"enc": AEAD_ALG, "data": base64.b64encode(sealed).decode("ascii")}


@dataclass(frozen=True)
class LinkSigner:
    """Signs envelopes as one link endpoint: ident 'agent:<peer_id>' or 'pc:<node_id>'."""

    ident: str
    key: Ed25519PrivateKey = field(repr=False)

    def __post_init__(self) -> None:
        if not _match(_FROM_RE, self.ident):
            raise ValueError("ident must be 'agent:<peer_id>' or 'pc:<node_id>'")

    def build(self, typ: str, body: Mapping[str, Any], *, to: str, sess: str | None = None, room: str = "",
              origin: str = "internal", session_key: SessionKey | None = None, call_id: str | None = None,
              tool: str | None = None, seq: Mapping[str, Any] | None = None, start_by: float | None = None,
              now: float | None = None) -> dict[str, Any]:
        """One signed envelope; K-70 encrypted types are sealed to session_key (sess + agent peer).

        exec gets start_by = ts + 2.5 s unless given. A notice needs to='bridge' and sess, keeps the default
        room '' and origin 'internal', and its body is {kind, ...} per NOTICE_FIELDS. Bad fields, a wrong
        direction, a body that check 8 refuses or a size cap raise DesktopLinkError with code BAD_ARGS (reason
        as the receiver would give it), so nothing oversized or malformed ever leaves.
        """
        ts = _now(now)
        if typ == "exec" and start_by is None:
            start_by = ts + START_BY_DELAY_S
        header: dict[str, Any] = {"v": PROTO_VERSION, "typ": typ, "id": new_message_id(), "ts": ts,
                                  "from": self.ident, "to": to, "room": room, "origin": origin}
        optional = {"sess": sess, "call_id": call_id, "tool": tool, "seq": None if seq is None else dict(seq),
                    "start_by": None if start_by is None else round(float(start_by), 3)}
        header.update({name: value for name, value in optional.items() if value is not None})
        try:
            if not isinstance(body, Mapping):
                raise DesktopLinkError("format")
            _validate_header(header)
            if _role(to) == _role(self.ident):
                raise DesktopLinkError("route")
            _check_direction(header, body)
            payload = _seal_body(header, body, session_key) if typ in ENCRYPTED_TYPES else dict(body)
            envelope = {**header, "body": payload}
            envelope["sig"] = self.key.sign(MSG_CONTEXT + canonical_json(envelope)).hex()
            size = len(canonical_json(envelope))
        except (DesktopLinkError, CanonicalJsonError) as exc:
            raise DesktopLinkError(getattr(exc, "reason", "format"), code="BAD_ARGS") from None
        if size > _size_cap(typ):
            raise DesktopLinkError("size", code="BAD_ARGS")
        return envelope


@dataclass(frozen=True, eq=False)
class VerifiedEnvelope:
    """An envelope that passed the K-70 receiver checks; only Receiver.verify() can create one.

    remeasure is set only on a hello or agent_hello that passed check 5 on the raw own clock alone: it is the
    receiver's ClockEstimator, which must re-measure once the handshake with this hello succeeds
    (accept_node_hello and accept_agent_hello call its reset()).
    """

    header: Mapping[str, Any]
    body: Mapping[str, Any]
    size: int
    token: object = field(default=None, repr=False)
    remeasure: ClockEstimator | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.token is not _VERIFIED:
            raise TypeError("a VerifiedEnvelope comes from Receiver.verify() only")

    @property
    def typ(self) -> str:
        return self.header["typ"]

    @property
    def sender(self) -> str:
        return self.header["from"]

    @property
    def sess(self) -> str | None:
        return self.header.get("sess")

    @property
    def call_id(self) -> str | None:
        return self.header.get("call_id")

    @property
    def encrypted(self) -> bool:
        return self.typ in ENCRYPTED_TYPES


def _load_envelope(message: Any) -> tuple[dict[str, Any], int]:
    """Check 1: the size cap, on the received bytes or on the canonical size of an already decoded dict."""
    if isinstance(message, (str, bytes, bytearray, memoryview)):
        if len(message) > MAX_ENVELOPE_BYTES:
            raise DesktopLinkError("size")
        try:
            raw = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        except UnicodeError:
            raise DesktopLinkError("format") from None
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise DesktopLinkError("size")
        try:
            env = loads_strict(raw)
        except CanonicalJsonError:
            raise DesktopLinkError("format") from None
        size = len(raw)
    elif isinstance(message, Mapping):
        try:
            env, size = dict(message), len(canonical_json(dict(message)))
        except CanonicalJsonError:
            raise DesktopLinkError("format") from None
    else:
        raise DesktopLinkError("format")
    if not isinstance(env, dict):
        raise DesktopLinkError("format")
    if size > _size_cap(env.get("typ")):
        raise DesktopLinkError("size")
    return env, size


def _split_envelope(env: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    sig, body = env.get("sig"), env.get("body")
    if set(env) - _ENVELOPE_FIELDS or not _match(_HEX64, sig) or not isinstance(body, dict):
        raise DesktopLinkError("format")
    header = {name: env[name] for name in _HEADER_FIELDS if name in env}
    _validate_header(header)
    if header["typ"] in ENCRYPTED_TYPES and not _sealed_ok(body):
        raise DesktopLinkError("format")
    return header, body, bytes.fromhex(sig)


def _record_new(store: SeenStore, kind: str, value: str, sess: str) -> bool:
    """Check 7; a store that cannot answer fails closed (seen_store) instead of letting the value through."""
    try:
        return store.check_and_add(kind, value, sess=sess)
    except (sqlite3.Error, TimeoutError, RuntimeError):
        raise DesktopLinkError("seen_store") from None


@dataclass
class Receiver:
    """The K-70 receiver checks, in order, for one endpoint: 'agent:<peer_id>', 'pc:<node_id>' or 'bridge'.

    public_keys maps a sender role ('agent', 'pc') to its pinned Ed25519 key (a small-order or non-canonical
    one raises ValueError here). sess is the node's current session (the node's own; an agent or the bridge
    adopts it from a verified hello with bind()). peer, once bound, is the only sender accepted besides a new
    hello. seen is this endpoint's own SeenStore: check 7 and the refusal bounds use its check_and_add,
    retention_s, created_at and wall_step_s, so any other type raises TypeError here (the bridge too gets a
    library SeenStore, not an adapter over its C-13 store). seen=None leaves check 7 to the caller, and then a
    failure at check 5 or 6 allows no refusal (refusal_horizon_s is -inf, refusal_floor_s +inf). clock, when
    given, is the ClockEstimator that registers this endpoint's pings: it supplies the offset for check 5 (none
    while it has no sample, and then a check 5 or 6 failure allows no refusal either), gets every pong this
    Receiver verifies and the out-of-window ones it may measure, and must re-measure once a hello that passed
    check 5 only on the raw own clock starts a session (see verify).
    """

    ident: str
    public_keys: Mapping[str, Ed25519PublicKey]
    seen: SeenStore | None
    sess: str | None = None
    peer: str | None = None
    clock: ClockEstimator | None = None
    window_s: float = FRESHNESS_WINDOW_S
    wall: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if self.seen is not None and not isinstance(self.seen, SeenStore):
            raise TypeError("seen must be a desktop_protocol.SeenStore or None")
        for key in self.public_keys.values():
            _check_pinned_key(key)

    def bind(self, sess: str, peer: str | None = None) -> None:
        """Adopts the node session (and for an agent the node's ident) after a verified hello."""
        self.sess, self.peer = sess, peer

    def verify(self, message: Mapping[str, Any] | bytes | str, *, now: float | None = None) -> VerifiedEnvelope:
        """Runs checks 1-8; any failure raises DesktopLinkError (authentic=True after check 4 passed).

        Checks 5 and 6 depend on when and in which session a message arrives. An authentic message that fails
        them may have passed them before (a replay after a reconnect or after the window) or could pass them
        later (a 'future' message, a clock correction). So its id is recorded: one seen before is reported
        as a replay, and one refused now is a replay at every later delivery while the store keeps its id.
        The failure is refusable only when that keeping covers every delivery that could pass or have passed
        (refusal_horizon_s, refusal_floor_s; see refusal_code).

        Clock probes: a ping skips check 5 (it only asks for a pong), and so does a pong at the bridge, which
        relays it. At an agent or node every pong that passes all eight checks goes to self.clock (one ping,
        one sample: a later on_pong call for it returns None). A pong outside the window is refused like any
        message, but when it is authentic, in session, exactly {echo, t1} and new to the SeenStore (if any), it
        is first offered to self.clock. Either way the estimator takes the sample only if the pong echoes one of
        the pings it registered for that peer at most 5 s ago. So a clock step above the window is measured
        (skew_exceeded, CLOCK_SKEW) instead of leaving every later message refused without a reason.

        A new session after a clock fix: a sample stays valid for 600 s and a hello is no clock probe, so with
        the old offset a new session could not start until the sample expired. A hello or agent_hello that
        fails check 5 with the offset but passes it on the raw own clock (K-70's own window, exactly) is
        therefore accepted, marked with remeasure = self.clock. Once the handshake with it succeeds
        (accept_node_hello, accept_agent_hello), the estimator must re-measure (ClockEstimator.reset): no offset
        for check 5, and CLOCK_SKEW until its next sample. A hello that the bridge held back, or one sent after a
        partial clock fix, takes the same path, so it can hold actuation back until the next pong but never
        allow it. A hello whose handshake fails leaves the estimator alone, a replayed one is a replay at check 7,
        and no other type gets this rule.
        """
        env, size = _load_envelope(message)
        version = env.get("v")
        if not (_is_int(version) and version in SUPPORTED_VERSIONS):
            raise DesktopLinkError("version", code="PROTO_MISMATCH")
        header, body, sig = _split_envelope(env)
        self._check_route(header)
        self._check_signature(header, body, sig)
        wall_now = self.wall() if now is None else now
        offset = self.clock.offset_s if self.clock is not None else 0.0  # None: a clock without a sample
        raw_clock_only = self._check_window(header, body, wall_now=wall_now, offset=offset)
        if not self._in_session(header):
            raise self._timing_failure("session", header, wall_now=wall_now, offset=offset)
        try:
            if self.seen is not None and not _record_new(self.seen, KIND_ID, header["id"], header.get("sess", "")):
                raise DesktopLinkError("replay")
            _check_direction(header, body)
        except DesktopLinkError as exc:
            raise DesktopLinkError(exc.reason, code=exc.code, authentic=True) from None
        verified = VerifiedEnvelope(MappingProxyType(header), MappingProxyType(body), size, _VERIFIED,
                                    remeasure=self.clock if raw_clock_only else None)
        if self.clock is not None and verified.typ == "pong":
            self.clock.on_pong(verified)
        return verified

    def _window_failure(self, ts: float, *, wall_now: float, offset: float = 0.0) -> str | None:
        """The check 5 reason ('stale' or 'future') for ts against wall_now + offset, or None when it passes."""
        try:
            check_freshness(ts, now=wall_now, offset=offset, window=self.window_s)
        except DesktopLinkError as exc:
            return exc.reason
        return None

    def _check_window(self, header: Mapping[str, Any], body: Mapping[str, Any], *, wall_now: float,
                      offset: float | None) -> bool:
        """Check 5 (see verify). True for a hello that passes it only on the raw own clock (its estimator must
        re-measure once the handshake succeeds); a failure raises after recording the id and offering a pong."""
        if not self._window_applies(header["typ"]):
            return False
        reason = self._window_failure(header["ts"], wall_now=wall_now, offset=offset or 0.0)
        if reason is None:
            return False
        if header["typ"] in HELLO_TYPES and offset and self._window_failure(header["ts"], wall_now=wall_now) is None:
            return True
        error = self._timing_failure(reason, header, wall_now=wall_now, offset=offset)
        if (error.reason == reason and header["typ"] == "pong" and self.clock is not None
                and self._in_session(header) and _pong_body_ok(body)):
            self.clock._out_of_window_pong(header, body)  # pylint: disable=protected-access  # see verify
        raise error

    @property
    def refusal_horizon_s(self) -> float:
        """|age| (age = (own clock + offset) - ts) from which a message that fails check 5 or 6 gets no refusal.

        min(SeenStore retention - 2 x window, REFUSAL_HORIZON_S). Below it the store keeps the id long enough:
        an exec that ran was accepted no earlier than one window before its ts, so its id is still there; and a
        'future' exec refused now stays a replay until its ts is more than a window behind, so it can never run
        afterwards. The second window absorbs a clock or offset change in between. The 1 h cap also absorbs a
        forward jump of the node clock (rows age on it, the age is on the sender's clock) of up to the
        retention minus 1 h and 2 windows, and a real late exec arrives within minutes. -inf without a store,
        which cannot show that a message never ran.
        """
        if self.seen is None:
            return -math.inf
        return min(self.seen.retention_s - 2 * self.window_s, REFUSAL_HORIZON_S)

    @property
    def refusal_floor_s(self) -> float:
        """The earliest ts, taken to own clock (ts - offset), for which a check 5 or 6 failure may be refused.

        SeenStore.created_at + SeenStore.wall_step_s + 2 x window: an exec with an earlier ts may have run
        before this store existed (a deleted run/ folder, a new data root or namespace), and then no row could
        show that it ran. created_at is the node's wall clock as it read then, while ts - offset uses the
        current offset, so a forward step of the node clock since then raises ts - offset by the step: the
        floor rises with it (wall_step_s, measured since the store was opened). Only the 2 windows absorb what
        it cannot see: a forward step of the node clock before that open (across a restart), and a step of the
        sender's clock between signing and delivery. While the offset is unmeasured no refusal is given at all
        (see _timing_failure). +inf without a store.
        """
        if self.seen is None:
            return math.inf
        return self.seen.created_at + self.seen.wall_step_s + 2 * self.window_s

    def _timing_failure(self, reason: str, header: Mapping[str, Any], *, wall_now: float,
                        offset: float | None) -> DesktopLinkError:
        """The error for an authentic message that failed check 5 or 6 (see verify): its id is recorded, and it
        is refusable only if that id was new, the offset is measured (None: self.clock has no sample, so ts
        cannot be put on the own clock), |age| < refusal_horizon_s and ts - offset >= refusal_floor_s."""
        if self.seen is not None:
            try:
                new = _record_new(self.seen, KIND_ID, header["id"], header.get("sess", ""))
            except DesktopLinkError as exc:
                return DesktopLinkError(exc.reason, authentic=True)
            if not new:
                return DesktopLinkError("replay", authentic=True)
        if offset is None:
            return DesktopLinkError(reason, authentic=True, refusable=False)
        ts = header["ts"]
        refusable = abs((wall_now + offset) - ts) < self.refusal_horizon_s and ts - offset >= self.refusal_floor_s
        return DesktopLinkError(reason, authentic=True, refusable=refusable)

    def _window_applies(self, typ: str) -> bool:
        """Check 5 for every type but the clock probes: a ping, and at the bridge also a pong (see verify)."""
        return not (typ == "ping" or (typ == "pong" and self.ident == BRIDGE_IDENT))

    def _check_route(self, header: Mapping[str, Any]) -> None:
        sender, target, mine = _role(header["from"]), _role(header["to"]), _role(self.ident)
        wrong = sender == target or (mine != BRIDGE_IDENT and (
            sender == mine or header["to"] not in (self.ident, f"{mine}:*")))
        if wrong or (self.peer is not None and header["typ"] not in HELLO_TYPES and header["from"] != self.peer):
            raise DesktopLinkError("route")

    def _check_signature(self, header: Mapping[str, Any], body: Mapping[str, Any], sig: bytes) -> None:
        key = self.public_keys.get(_role(header["from"]))
        if key is None:
            raise DesktopLinkError("signature")
        try:
            signed = MSG_CONTEXT + canonical_json({**header, "body": dict(body)})
        except CanonicalJsonError:  # loads_strict already refuses what cannot be encoded; never let one escape
            raise DesktopLinkError("format") from None
        try:
            key.verify(sig, signed)
        except InvalidSignature:
            raise DesktopLinkError("signature") from None

    def _in_session(self, header: Mapping[str, Any]) -> bool:
        """Check 6: sess is the node's current session; a hello opens (or rotates) it."""
        if header["typ"] in HELLO_TYPES:
            return True
        return self.sess is not None and hmac.compare_digest(str(header.get("sess", "")).encode("utf-8"),
                                                             self.sess.encode("utf-8"))


def _body_consistent(header: Mapping[str, Any], body: Mapping[str, Any]) -> bool:
    """Header fields repeated in a decrypted body agree; exec args match args_sha256; event kinds are known."""
    if any(name in body and body[name] != header.get(name) for name in _BODY_MIRRORS):
        return False
    if header["typ"] == "exec" and "args" in body:
        if not isinstance(body["args"], dict):
            return False
        try:
            return digest_equal(args_sha256(body["args"]), body.get("args_sha256"))
        except CanonicalJsonError:
            return False
    return header["typ"] != "event" or _member(body.get("kind"), EVENT_KINDS)


def open_body(env: VerifiedEnvelope, key: SessionKey | None) -> dict[str, Any]:
    """The plaintext body of a verified envelope; encrypted types need the key of (env sess, agent peer)."""
    header = dict(env.header)
    if not env.encrypted:
        return dict(env.body)
    if key is None or not _key_matches(key, header):
        raise DesktopLinkError("session", authentic=True)
    try:
        sealed = base64.b64decode(env.body["data"], validate=True)
    except (binascii.Error, ValueError):
        raise DesktopLinkError("format", authentic=True) from None
    plain = key.open(sealed, canonical_json(header), authentic=True)
    try:
        body = loads_strict(plain)
    except CanonicalJsonError:
        raise DesktopLinkError("format", authentic=True) from None
    if not isinstance(body, dict) or not _body_consistent(header, body):
        raise DesktopLinkError("format", authentic=True)
    return body


# --- handshake ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PeerSession:
    """One established link session: node, agent peer, node sess, protocol version and the AEAD key."""

    node_id: str
    peer_id: str
    sess: str
    proto: int
    key: SessionKey = field(repr=False)


def negotiate_proto(ours: Iterable[Any], theirs: Iterable[Any]) -> int:
    """The highest version in both lists; none in common raises PROTO_MISMATCH."""
    common = {v for v in ours if _is_int(v)} & {v for v in theirs if _is_int(v)}
    if not common:
        raise DesktopLinkError("proto", code="PROTO_MISMATCH")
    return max(common)


def node_hello_body(*, node_id: str, eph: X25519PrivateKey, sess: str,
                    fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """hello body: the node's other K-70 fields (boot_id, version, mode, wal_report, ...) plus the handshake."""
    body = dict(fields or {})
    body.update(node_id=node_id, eph_pub=eph_public_hex(eph), sess=sess, proto=list(SUPPORTED_VERSIONS))
    return body


def agent_hello_body(*, peer_id: str, job_id: str, room: str, eph: X25519PrivateKey,
                     sess: str | None = None) -> dict[str, Any]:
    """agent_hello body (K-70): peer_id, job_id|'svc', room, eph_pub and, once known, the node sess."""
    body: dict[str, Any] = {"peer_id": peer_id, "job_id": job_id, "room": room, "eph_pub": eph_public_hex(eph)}
    if sess is not None:
        body["sess"] = sess
    return body


def _session_started(hello: VerifiedEnvelope) -> None:
    """The handshake with a verified hello succeeded. If it passed check 5 only on the raw own clock, its
    receiver's ClockEstimator must re-measure now (Receiver.verify); a failed handshake never gets here."""
    if hello.remeasure is not None:
        hello.remeasure.reset()


def accept_node_hello(hello: VerifiedEnvelope, *, own_eph: X25519PrivateKey, peer_id: str) -> PeerSession:
    """Agent side: checks a verified node hello and derives this peer's session key.

    A hello that passed check 5 only on the agent's raw clock leaves the Receiver's ClockEstimator in the
    re-measure state once it is accepted here (see Receiver.verify).
    """
    header, body = hello.header, hello.body
    node_id = header["from"].partition(":")[2]
    try:
        consistent = (body.get("node_id") == node_id and body.get("sess") == header.get("sess")
                      and isinstance(body.get("proto"), list))
        if hello.typ != "hello" or _role(header["from"]) != "pc" or not consistent:
            raise DesktopLinkError("format")
        proto = negotiate_proto(SUPPORTED_VERSIONS, body["proto"])
        key = derive_session_key(own_eph, body.get("eph_pub"), sess=header["sess"], peer_id=peer_id)
    except DesktopLinkError as exc:
        raise DesktopLinkError(exc.reason, code=exc.code, authentic=True) from None
    _session_started(hello)
    return PeerSession(node_id, peer_id, header["sess"], proto, key)


def accept_agent_hello(agent_hello: VerifiedEnvelope, *, own_eph: X25519PrivateKey, sess: str,
                       node_id: str) -> PeerSession:
    """Node side: checks a verified agent_hello and derives the session key for that agent peer.

    An agent_hello that names a session (envelope or body sess) must name the node's current one. One that
    passed check 5 only on the node's raw clock leaves the Receiver's ClockEstimator in the re-measure state once
    it is accepted here (see Receiver.verify).
    """
    header, body = agent_hello.header, agent_hello.body
    peer_id = header["from"].partition(":")[2]
    try:
        if (agent_hello.typ != "agent_hello" or body.get("peer_id") != peer_id
                or body.get("room", header["room"]) != header["room"]):
            raise DesktopLinkError("format")
        if body.get("sess", sess) != sess or header.get("sess", sess) != sess:
            raise DesktopLinkError("session")
        key = derive_session_key(own_eph, body.get("eph_pub"), sess=sess, peer_id=peer_id)
    except DesktopLinkError as exc:
        raise DesktopLinkError(exc.reason, code=exc.code, authentic=True) from None
    _session_started(agent_hello)
    return PeerSession(node_id, peer_id, sess, PROTO_VERSION, key)


# --- clock --------------------------------------------------------------------------------------------------

def check_freshness(ts: float, *, now: float, offset: float = 0.0, window: float = FRESHNESS_WINDOW_S) -> None:
    """K-70 check 5: |(own clock + offset) - ts| <= window, offset = measured sender clock - own clock."""
    delta = (now + offset) - ts
    if delta > window:
        raise DesktopLinkError("stale")
    if delta < -window:
        raise DesktopLinkError("future")


def pong_body(ping: VerifiedEnvelope, *, received_at: float) -> dict[str, Any]:
    """Answer to a verified ping: its id and our receive time t1; the pong's own ts is t2."""
    return {"echo": ping.header["id"], "t1": round(received_at, 3)}


class ClockEstimator:
    """Offset and RTT of the peer's clock from signed ping/pong (NTP formula), updated on every good pong.

    Only an authentic pong that echoes one of our own outstanding pings counts, from the peer that ping was
    sent to. A Receiver that has this estimator as its clock hands it every pong it verifies, and the ones
    outside the window it may measure (see Receiver.verify), so that link only calls ping_sent(); on_pong is
    for a pong verified by a Receiver without a clock. Unsigned data (for example the bridge's server_time
    hint) can never move the offset. Round trips are measured on the monotonic clock and samples above
    max_rtt_s are dropped, which bounds what a delaying relay can bias the offset by (max_rtt_s / 2) and proves
    that the pong is fresh whatever its ts says.

    CLOCK_SKEW fails closed at the node (check_actuation): neither the expiry of a sample above the limit nor
    reset() turns it off; only a new sample does. A relay that holds a hello back or drops pongs can delay
    actuation, never allow it on a clock measured as skewed. Keep one estimator per endpoint for the life of the
    process, across reconnects: a new one has no measured skew to hold.
    """

    def __init__(self, *, max_rtt_s: float = PING_MAX_RTT_S, max_age_s: float = 600.0,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic) -> None:
        self._max_rtt, self._max_age, self._wall, self._mono = max_rtt_s, max_age_s, wall, mono
        self._pending: dict[str, tuple[float, float, str]] = {}  # ping id -> (wall, monotonic, its 'to')
        self._sample: tuple[float, float] | None = None  # (offset, monotonic time), kept after it expires
        self._remeasure = False  # after reset(): skew counts as exceeded until the next sample
        self._rtts: deque[float] = deque(maxlen=15)
        self._lock = threading.Lock()

    def ping_sent(self, ping: Mapping[str, Any]) -> None:
        """Registers a ping envelope we just sent (from LinkSigner.build) so that its pong can be matched."""
        if ping.get("typ") != "ping" or not _match(_HEX16, ping.get("id")) or not _match(_TO_RE, ping.get("to")):
            raise ValueError("not a ping envelope")
        with self._lock:
            mono = self._mono()
            self._pending = {k: v for k, v in self._pending.items() if mono - v[1] <= self._max_rtt}
            if len(self._pending) < 64:
                self._pending[ping["id"]] = (self._wall(), mono, ping["to"])

    def on_pong(self, pong: VerifiedEnvelope) -> float | None:
        """Feeds a verified pong; returns the new offset (peer - own, s) or None for an unusable sample.

        One ping gives one sample: for a pong that a Receiver with this estimator as its clock already handed
        over, this returns None.
        """
        if not isinstance(pong, VerifiedEnvelope):
            raise TypeError("ClockEstimator takes only a VerifiedEnvelope pong")
        return self._take(pong.header, pong.body) if pong.typ == "pong" else None

    def reset(self) -> None:
        """Must re-measure: offset_s is None (check 5 uses the own clock, and a check 5 or 6 failure gets no
        refusal) and skew_exceeded() is True (CLOCK_SKEW) until the next usable pong.

        accept_node_hello and accept_agent_hello call it when a hello that passed check 5 only on the raw own
        clock starts a session (see Receiver.verify): the peer's clock has changed since the sample, or the hello
        was held back, so neither the old offset nor 'no skew' can be trusted. Pending pings and the RTT history
        stay.
        """
        with self._lock:
            self._sample, self._remeasure = None, True

    def _out_of_window_pong(self, header: Mapping[str, Any], body: Mapping[str, Any]) -> float | None:
        """From Receiver.verify only: a pong that passed checks 1-4 and 6 but not 5, whose body is exactly
        {echo, t1} (check 8's rule) and whose id was new to the receiver's SeenStore (if it has one).

        A pong that echoes one of our pings sent at most max_rtt_s ago is fresh whatever its ts, so it can
        measure a clock step above the window; the receiver still refuses it as a message.
        """
        return self._take(header, body)

    def _take(self, header: Mapping[str, Any], body: Mapping[str, Any]) -> float | None:
        echo, t1, t2 = body.get("echo"), body.get("t1"), header["ts"]
        with self._lock:
            entry = self._pending.get(echo) if isinstance(echo, str) else None
            if entry is None or not _answers(entry[2], header["from"]):
                return None
            del self._pending[echo]  # one ping, one sample
            if not _is_time(t1):
                return None
            (t0, m0, _), m3 = entry, self._mono()
            local_rtt, remote_hold = m3 - m0, t2 - t1  # timestamps carry ms precision: allow 2 ms
            if local_rtt > self._max_rtt or not -0.002 <= remote_hold <= local_rtt + 0.002:
                return None
            offset = ((t1 - t0) + (t2 - (t0 + local_rtt))) / 2
            self._sample, self._remeasure = (offset, m3), False
            self._rtts.append(max(0.0, local_rtt - remote_hold))
            return offset

    @property
    def offset_s(self) -> float | None:
        """The latest offset (peer clock - own clock), or None when none is younger than max_age_s (also while
        the estimator must re-measure)."""
        with self._lock:
            if self._sample is None or self._mono() - self._sample[1] > self._max_age:
                return None
            return self._sample[0]

    @property
    def rtt_ms_p50(self) -> float | None:
        with self._lock:
            return round(statistics.median(self._rtts) * 1000, 1) if self._rtts else None

    def _skewed(self, limit_s: float, *, expired_too: bool) -> bool:
        with self._lock:
            if self._remeasure:
                return True
            if self._sample is None:
                return False
            current = self._mono() - self._sample[1] <= self._max_age
            return (current or expired_too) and abs(self._sample[0]) > limit_s

    def skew_exceeded(self, limit_s: float = CLOCK_SKEW_LIMIT_S) -> bool:
        """True while |offset_s| > limit_s (default 60 s, K-70's CLOCK_SKEW limit) or the estimator must
        re-measure (reset): the agent's fast path (C-119). An expired sample does not count here, so a laptop
        that went offline with a skewed clock reads as offline; check_actuation, the node's guard, still counts
        it."""
        return self._skewed(limit_s, expired_too=False)

    def check_actuation(self, limit_s: float = CLOCK_SKEW_LIMIT_S) -> None:
        """Node side, for every exec besides the start window: CLOCK_SKEW while skew_exceeded(), and also while
        the latest sample, above limit_s, has expired. Only a new sample takes a measured skew back, so a relay
        that drops pongs cannot turn it off. In the re-measure and expired states offset_s is None and
        start_deadline() converts start_by on the raw clock, so only this check refuses then. Before the first
        sample, and after one within limit_s expired, there is nothing to refuse."""
        if self._skewed(limit_s, expired_too=True):
            raise DesktopLinkError("clock_skew", code="CLOCK_SKEW", authentic=True)


def make_start_by(now: float | None = None, delay_s: float = START_BY_DELAY_S) -> float:
    """Agent side: exec start_by on the agent clock (send time + 2.5 s)."""
    return round((time.time() if now is None else now) + delay_s, 3)


def start_deadline(start_by: float, *, offset: float | None, wall_now: float, mono_now: float,
                   max_ahead_s: float = START_BY_MAX_AHEAD_S) -> float:
    """Node side: start_by (agent clock) as a monotonic deadline, using offset = agent clock - node clock (None:
    the raw clock). The node also calls ClockEstimator.check_actuation for the exec (see there)."""
    remaining = (start_by - (offset or 0.0)) - wall_now
    return mono_now + min(remaining, max_ahead_s)


def check_start_window(deadline: float, *, mono_now: float) -> None:
    """START_WINDOW_MISSED once the monotonic deadline from start_deadline() has passed."""
    if mono_now > deadline:
        raise DesktopLinkError("start_window", code="START_WINDOW_MISSED", authentic=True)


# --- result_resend and bridge hints -------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultRecord:
    """A finished call kept in the node outbox until result_ack (K-70: 10 min / 200 entries)."""

    call_id: str
    tool: str
    room: str
    origin: str
    original_ts: float
    finished_at: float
    result: Mapping[str, Any]


def build_result_resend(signer: LinkSigner, record: ResultRecord, *, to: str, session_key: SessionKey,
                        now: float | None = None) -> dict[str, Any]:
    """A fresh result_resend: new id, ts, to and sess; the body keeps call_id, original_ts and finished_at."""
    body = {"call_id": record.call_id, "original_ts": record.original_ts, "finished_at": record.finished_at,
            "result": dict(record.result)}
    return signer.build("result_resend", body, to=to, sess=session_key.sess, room=record.room,
                        origin=record.origin, call_id=record.call_id, tool=record.tool,
                        session_key=session_key, now=now)


@dataclass(frozen=True)
class BridgeHint:
    """An unsigned bridge message: a hint, never authority (welcome, status, control, dl_undeliverable)."""

    typ: str
    to: str
    body: Mapping[str, Any]


def parse_bridge_hint(message: Mapping[str, Any] | bytes | str) -> BridgeHint:
    """Accepts only allow-listed hints; a bridge control goes to the node and only tightens (K-70, K-76)."""
    env = _load_envelope(message)[0]
    typ, to, body = env.get("typ"), env.get("to"), env.get("body")
    target = _role(to) if isinstance(to, str) else ""
    valid = (env.get("from") == BRIDGE_IDENT and "sig" not in env and _is_int(env.get("v"))
             and env.get("v") in SUPPORTED_VERSIONS and _member(typ, BRIDGE_HINT_TYPES) and _match(_TO_RE, to)
             and isinstance(body, dict)
             and (typ != "control" or (target == "pc" and _member(body.get("kind"), TIGHTEN_ONLY_KINDS)))
             and (typ != "dl_undeliverable" or target == "agent"))
    if not valid:
        raise DesktopLinkError("format")
    return BridgeHint(typ, to, MappingProxyType(dict(body)))


def build_bridge_hint(typ: str, body: Mapping[str, Any], *, to: str, now: float | None = None) -> dict[str, Any]:
    """Bridge side: an unsigned hint (the bridge holds no link key), checked with parse_bridge_hint's rules."""
    hint = {"v": PROTO_VERSION, "typ": typ, "id": new_message_id(), "ts": _now(now), "from": BRIDGE_IDENT, "to": to,
            "body": dict(body)}
    parse_bridge_hint(hint)
    return hint


# --- signed HTTP --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HttpAuth:
    """Verified X-Desk-* headers: the body must be exactly `length` bytes with this sha256."""

    ts: int
    nonce: str
    length: int
    sha256: str


def _request_text(value: Any) -> str:
    """A request-line part as str: str as given, bytes (ASGI raw_path, query_string) as ASCII; else 'format'."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("ascii")
        except UnicodeDecodeError:
            raise DesktopLinkError("format") from None
    raise DesktopLinkError("format")


def _quote(text: str, safe: str) -> str:
    try:
        return quote(text, safe=safe)
    except UnicodeEncodeError:  # a lone surrogate has no UTF-8 form
        raise DesktopLinkError("format") from None


def canonical_http_path(path: str | bytes, *, public_prefix: str = HTTP_PUBLIC_PREFIX) -> str:
    """The signed path: percent-decoded, without the public mount (nginx strips '/vortex'), re-quoted.

    The verifier passes the path as received (ASGI raw_path, bytes are fine), not one that is already decoded.
    """
    plain = unquote(_request_text(path).split("?", 1)[0])
    if public_prefix and (plain == public_prefix or plain.startswith(public_prefix + "/")):
        plain = plain[len(public_prefix):] or "/"
    if not plain.startswith("/"):
        raise DesktopLinkError("format")
    return _quote(plain, "/:@!$&'()*+,;=-._~")


def _query_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if _is_int(value):
        return str(value)
    raise DesktopLinkError("format")


def _query_pairs(query: Any) -> list[tuple[str, str]]:
    """The pairs of a Mapping or an iterable of (key, value), at most HTTP_MAX_QUERY_FIELDS; str or int only."""
    try:
        items = list(islice(query.items() if isinstance(query, Mapping) else query, HTTP_MAX_QUERY_FIELDS + 1))
        if len(items) > HTTP_MAX_QUERY_FIELDS:
            raise DesktopLinkError("format")
        return [(_query_text(key), _query_text(value)) for key, value in items]
    except (TypeError, ValueError):  # not iterable, or an item that is not a (key, value) pair
        raise DesktopLinkError("format") from None


def canonical_http_query(query: str | bytes | Mapping[str, Any] | Iterable[tuple[str, Any]] | None) -> str:
    """Sorted key=value pairs, RFC 3986 percent-encoded; '' for no query. Values must be str or int.

    A str or bytes query (ASGI query_string) is parsed as sent. Anything malformed raises DesktopLinkError.
    """
    if not query:
        return ""
    if isinstance(query, (str, bytes, bytearray, memoryview)):
        try:
            pairs = parse_qsl(_request_text(query).lstrip("?"), keep_blank_values=True,
                              max_num_fields=HTTP_MAX_QUERY_FIELDS)
        except ValueError:
            raise DesktopLinkError("format") from None
    else:
        pairs = _query_pairs(query)
    return "&".join(f"{_quote(key, '-._~')}={_quote(value, '-._~')}" for key, value in sorted(pairs))


def _http_signed(method: Any, path: Any, query: Any, auth: HttpAuth) -> bytes:
    verb = _request_text(method)
    if not _match(_METHOD_RE, verb):
        raise DesktopLinkError("format")
    fields = (verb.upper(), canonical_http_path(path), canonical_http_query(query), str(auth.ts), auth.nonce,
              str(auth.length), auth.sha256)
    return HTTP_CONTEXT + "|".join(fields).encode("utf-8")


def sign_http_request(key: Ed25519PrivateKey, *, method: str, path: str, query: Any = None, body: bytes = b"",
                      now: float | None = None) -> dict[str, str]:
    """X-Desk-* headers signed over method|path|query|ts|nonce|length|sha256(body) (K-70)."""
    auth = HttpAuth(int(time.time() if now is None else now), secrets.token_hex(16), len(body),
                    hashlib.sha256(body).hexdigest())
    sig = key.sign(_http_signed(method, path, query, auth)).hex()
    return dict(zip(HTTP_HEADER_NAMES, (str(auth.ts), auth.nonce, str(auth.length), auth.sha256, sig)))


def _desk_headers(headers: Mapping[Any, Any] | Iterable[tuple[Any, Any]]) -> dict[str, str]:
    """The five X-Desk-* values; names are case-insensitive, str or bytes (ASGI); a repeated one is refused."""
    wanted = {name.lower() for name in HTTP_HEADER_NAMES}
    found: dict[str, str] = {}
    for name, value in headers.items() if isinstance(headers, Mapping) else headers:
        lname = (name.decode("latin-1") if isinstance(name, bytes) else str(name)).lower()
        if lname in wanted:
            if lname in found:
                raise DesktopLinkError("format")
            found[lname] = value.decode("latin-1") if isinstance(value, bytes) else str(value)
    if len(found) != len(wanted):
        raise DesktopLinkError("format")
    return found


def verify_http_headers(headers: Mapping[Any, Any] | Iterable[tuple[Any, Any]], *, method: str | bytes,
                        path: str | bytes, query: Any, public_key: Ed25519PublicKey, seen: SeenStore | None,
                        max_length: int = HTTP_MAX_BODY_BYTES, content_length: int | None = None,
                        now: float | None = None) -> HttpAuth:
    """Verifies X-Desk-* BEFORE any body byte is read; HTTP_STATUS maps each reason to a status code.

    Order: length cap, formats, Content-Length agreement, signature, +-120 s on the own clock, nonce unseen
    for 24 h (a failing store refuses: seen_store). Then stream the body through HttpBodyVerifier. path and
    query may be the raw ASGI bytes. A weak pinned public_key is a configuration error (ValueError).
    """
    _check_pinned_key(public_key)
    found = _desk_headers(headers)
    if not _match(_DECIMAL_RE, found["x-desk-len"]):
        raise DesktopLinkError("format")
    if int(found["x-desk-len"]) > max_length:
        raise DesktopLinkError("size")
    nonce, digest, sig = found["x-desk-nonce"], found["x-desk-sha256"], found["x-desk-sig"]
    if not (_match(_DECIMAL_RE, found["x-desk-ts"]) and _match(_HEX16, nonce) and _match(_HEX32, digest)
            and _match(_HEX64, sig)):
        raise DesktopLinkError("format")
    auth = HttpAuth(int(found["x-desk-ts"]), nonce, int(found["x-desk-len"]), digest)
    if content_length is not None and content_length != auth.length:
        raise DesktopLinkError("length")
    try:
        public_key.verify(bytes.fromhex(sig), _http_signed(method, path, query, auth))
    except InvalidSignature:
        raise DesktopLinkError("signature") from None
    check_freshness(auth.ts, now=time.time() if now is None else now)
    if seen is not None and not _record_new(seen, KIND_HTTP_NONCE, nonce, ""):
        raise DesktopLinkError("replay")
    return auth


class HttpBodyVerifier:
    """Streams a body after verify_http_headers(): hard cap at the signed length, sha256 checked at the end."""

    def __init__(self, auth: HttpAuth) -> None:
        self._auth, self._count, self._hash = auth, 0, hashlib.sha256()

    def update(self, chunk: bytes) -> None:
        self._count += len(chunk)
        if self._count > self._auth.length:
            raise DesktopLinkError("length")
        self._hash.update(chunk)

    def finish(self) -> None:
        if self._count != self._auth.length:
            raise DesktopLinkError("length")
        if not digest_equal(self._hash.hexdigest(), self._auth.sha256):
            raise DesktopLinkError("digest")


def verify_http_body(auth: HttpAuth, body: bytes) -> None:
    verifier = HttpBodyVerifier(auth)
    verifier.update(body)
    verifier.finish()


# --- seen store ---------------------------------------------------------------------------------------------

_SEEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_seen (
    ns TEXT NOT NULL, sess TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, ts REAL NOT NULL,
    PRIMARY KEY (ns, sess, kind, value)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS desktop_seen_value ON desktop_seen (ns, kind, value);
CREATE INDEX IF NOT EXISTS desktop_seen_ts ON desktop_seen (ts);
CREATE TABLE IF NOT EXISTS desktop_seen_meta (ns TEXT PRIMARY KEY, created_at REAL NOT NULL) WITHOUT ROWID;
"""


def _check_seen_key(kind: Any, value: Any, sess: Any) -> None:
    if not (_match(_TYP_RE, kind) and isinstance(value, str) and 0 < len(value) <= 128
            and (sess == "" or _match(_HEX16, sess))):
        raise ValueError("invalid seen-store key")


class SeenStore:
    """Seen ids, call ids, confirm ids and HTTP nonces, persisted in SQLite for 24 h (K-70 check 7).

    One worker thread owns the sqlite3 connection; every public method is thread-safe and waits for that
    thread (acheck_and_add for asyncio). Rows are keyed by (namespace, sess, kind, value): the namespace
    separates receivers that share one file, sess binds the value to the session nonce it arrived with. A
    value counts as seen when it was recorded under ANY session of the namespace, so a replay into another
    session is refused too. Writes use BEGIN IMMEDIATE, so several processes may share the file. The table
    desktop_seen_meta keeps, per namespace, when it was first opened (created_at); purges never touch it.
    wall and mono are the clocks of this host (rows and created_at are on wall; mono measures wall_step_s).
    """

    _PURGE_EVERY_S = 60.0

    def __init__(self, path: str | os.PathLike[str], *, namespace: str = "",
                 retention_s: float = SEEN_RETENTION_S, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self._ns, self._retention, self._wall, self._mono = namespace, retention_s, wall, mono
        self._opened = (wall(), mono())  # wall_step_s counts from here
        self._created_at = math.inf  # set by the worker thread before the constructor returns
        self._last_purge = float("-inf")
        self._closed = False
        self._lock = threading.Lock()
        self._jobs: queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...], Future[Any]] | None]
        self._jobs = queue.SimpleQueue()
        ready: Future[None] = Future()
        self._thread = threading.Thread(target=self._run, args=(os.fspath(path), ready), daemon=True,
                                        name="desktop-seen-store")
        self._thread.start()
        ready.result()

    @property
    def retention_s(self) -> float:
        """How long (s, on this store's wall clock) a recorded value stays seen; read-only."""
        return self._retention

    @property
    def created_at(self) -> float:
        """When this namespace of the file was first opened (s, on the store's wall clock). Written once and
        kept across opens, so it changes only when the store is recreated (a new file, data root or
        namespace): ids recorded before then are unknown here (Receiver.refusal_floor_s)."""
        return self._created_at

    @property
    def wall_step_s(self) -> float:
        """How far the wall clock has stepped forward against the monotonic clock since this store was opened
        (s, >= 0): a time sync or a manual change, and on a system whose monotonic clock stops in sleep the sleep
        too. Receiver.refusal_floor_s adds it to created_at; a step before this open is not seen."""
        return max(0.0, (self._wall() - self._opened[0]) - (self._mono() - self._opened[1]))

    def _run(self, path: str, ready: Future[None]) -> None:
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SEEN_SCHEMA)
            conn.execute("INSERT OR IGNORE INTO desktop_seen_meta (ns, created_at) VALUES (?, ?)",
                         (self._ns, self._wall()))
            self._created_at = float(conn.execute("SELECT created_at FROM desktop_seen_meta WHERE ns=?",
                                                  (self._ns,)).fetchone()[0])
        except Exception as exc:  # pylint: disable=broad-exception-caught  # raised by the constructor
            if conn is not None:
                conn.close()
            ready.set_exception(exc)
            return
        ready.set_result(None)
        while (job := self._jobs.get()) is not None:
            func, args, future = job
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(func(conn, *args))
                except Exception as exc:  # pylint: disable=broad-exception-caught  # handed to the caller
                    future.set_exception(exc)
        conn.close()

    def _submit(self, func: Callable[..., Any], *args: Any) -> Future[Any]:
        future: Future[Any] = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("the SeenStore is closed")
            self._jobs.put((func, args, future))
        return future

    def check_and_add(self, kind: str, value: str, *, sess: str = "") -> bool:
        """Records (sess, kind, value); True when it is new in every session, False when seen (a replay)."""
        _check_seen_key(kind, value, sess)
        return bool(self._submit(self._check_and_add, kind, value, sess).result(timeout=30.0))

    async def acheck_and_add(self, kind: str, value: str, *, sess: str = "") -> bool:
        _check_seen_key(kind, value, sess)
        return bool(await asyncio.wrap_future(self._submit(self._check_and_add, kind, value, sess)))

    def seen(self, kind: str, value: str, *, sess: str | None = None) -> bool:
        """True when (kind, value) is recorded within the retention (only under `sess` when given)."""
        _check_seen_key(kind, value, sess or "")
        return bool(self._submit(self._lookup, kind, value, sess).result(timeout=30.0))

    def purge(self) -> int:
        """Deletes rows older than the retention; returns how many."""
        return int(self._submit(self._purge).result(timeout=30.0))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._jobs.put(None)
        self._thread.join(timeout=10.0)

    def __enter__(self) -> SeenStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _check_and_add(self, conn: sqlite3.Connection, kind: str, value: str, sess: str) -> bool:
        now = self._wall()
        if now - self._last_purge >= self._PURGE_EVERY_S:
            self._purge(conn)
        key = (self._ns, kind, value)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM desktop_seen WHERE ns=? AND kind=? AND value=? AND ts<?",
                         (*key, now - self._retention))
            known = conn.execute("SELECT 1 FROM desktop_seen WHERE ns=? AND kind=? AND value=? LIMIT 1",
                                 key).fetchone()
            conn.execute("INSERT OR IGNORE INTO desktop_seen (ns, sess, kind, value, ts) VALUES (?, ?, ?, ?, ?)",
                         (self._ns, sess, kind, value, now))
            conn.execute("COMMIT")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return known is None

    def _lookup(self, conn: sqlite3.Connection, kind: str, value: str, sess: str | None) -> bool:
        sql = "SELECT 1 FROM desktop_seen WHERE ns=? AND kind=? AND value=? AND ts>=?"
        params: list[Any] = [self._ns, kind, value, self._wall() - self._retention]
        if sess is not None:
            sql, params = sql + " AND sess=?", [*params, sess]
        return conn.execute(sql + " LIMIT 1", params).fetchone() is not None

    def _purge(self, conn: sqlite3.Connection) -> int:
        self._last_purge = self._wall()
        return conn.execute("DELETE FROM desktop_seen WHERE ts<?", (self._last_purge - self._retention,)).rowcount
