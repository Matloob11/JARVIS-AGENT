"""Desktop Node file mirror and its K-73 HTTP client (plan task C-122 step 3; contracts K-70, K-73, K-76).

VPS-made files (QR codes, generated images, downloads, leads, zips) land in the same laptop folders as on the PC
brain: config.shared_dir and config.outputs_dir. The mirror is additive only and never deletes.

- NodeHttp: K-70 PC-signed HTTP (X-Desk-* headers over method|path|query|ts|nonce|length|sha256) to the bridge's
  public /vortex mount, TLS verified (https only, from JARVIS_LINK_URL). It logs status codes and reasons only,
  never a path, a query value or a body.
- Name validation (validate_component, validate_relpath): every relpath component is checked before it can reach
  the filesystem: no backslash, colon or '..', no control or other non-printable character, no Windows device
  name (CON, PRN, AUX, NUL, COM0-9, LPT0-9, also with an extension), no trailing dot or space, no character
  Windows refuses in a name, no dotfile, at most 255 bytes, at most 16 directory levels, and only the K-73
  subtrees of the area. A refused name is never retried; it is audited once per relpath + size + mtime (in
  memory) and never logged.
- MediaMirror: polls GET /media/index per area with cursor = the listing time - 5 s that the bridge returns, and
  dedupes by relpath + size + mtime_ns; every RECONCILE_S (15 min) it lists the whole area once. A file that could
  not be copied (an error, the byte cap, or a deferred large file) holds the cursor below its mtime, so the cursor
  never skips a file. A file that can never be written (name or path too long for the disk, a sensitive target,
  larger than FILE_MAX_BYTES or than the whole deliveries cap) is refused once, audited, and does not hold the
  cursor (it is never charged either); a repeated failure of one file logs at DEBUG and backs
  off (BACKOFF_FIRST_S doubling to BACKOFF_MAX_S per relpath + size + mtime, in memory) while it holds the cursor.
  The deliveries byte cap is charged only after every folder, name and sensitive check, right before the
  download. A cap trip holds that file and every file at least as large for the rest of the pass, smaller files
  are still tried (a second trip in the same pass stops it), and the mirror then pauses (RATE_PAUSE_FIRST_S
  doubling to RATE_PAUSE_MAX_S on each pass with a trip; a pass that copies without a trip resets it). Names
  Explorer parses while browsing (.url, .lnk, .library-ms, .searchConnector-ms, .scf, desktop.ini and similar)
  are refused. Write order per file: '<name>.<random>.part' (the name shortened to fit 255 bytes) in the target folder -> size check
  and sha256 check (when known) -> Mark-of-the-Web (Zone.Identifier ZoneId=3) -> rename without overwriting and
  without following a reparse point; a clash gets ' (n)'. Files over 20 MB are copied only while
  desktop_status.voice_active is false, or on an explicit open (pc.fetch). The bridge pushes desktop_status to the
  node in a status hint right after its welcome and again whenever voice_active changes, and the node keeps that
  last pushed value for the connected link session until a newer hint arrives; a welcome alone (a bridge that does
  not push) counts for at most STATUS_FRESH_S. With no value from the connected session the state is unknown and
  counts as active (voice_state): large files wait unless opened, pc.fetch answers 'deferred_voice_unknown', and
  the mirror logs that once per unknown spell (at INFO, without names).
- install(node): the mirror loop as an active-only node task (never in --canary).

Nothing here runs at import time; httpx is imported on first use.
"""
# pylint: disable=too-many-lines
from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import stat as stat_mod
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.utils import desktop_protocol as dp

logger = logging.getLogger("JARVIS-DESKTOP.mirror")

PUBLIC_PREFIX = dp.HTTP_PUBLIC_PREFIX  # '/vortex': nginx strips it, the signature covers the path without it
STATE_FILE = "desktop_mirror.json"
STATE_VERSION = 1
POLL_S = 20.0
RECONCILE_S = 15 * 60.0                     # K-73: a full reconcile every 15 min
LARGE_FILE_BYTES = 20 * 1024 * 1024         # K-73: larger files only while voice is not active, or on open
STATUS_FRESH_S = POLL_S                      # a welcome's voice_active counts for one poll at most
INDEX_MAX_BYTES = 16 * 1024 * 1024          # a media index response larger than this is refused
FILE_MAX_BYTES = 8 * 1024 * 1024 * 1024     # a single mirrored file larger than this is refused
# The K-76 deliveries cap (policy.RateLimiter.BYTE_LIMITS['deliveries']): a file larger than the whole cap can never
# be charged, so it is refused for good instead of tripping the cap on every pass. Read from the policy at build time.
DELIVERIES_CAP_BYTES = 5 * 1024 * 1024 * 1024
CAP_TRIPS_PER_PASS = 2                      # byte-cap trips (toast, alert, audit) one pass may cause at most
HTTP_TIMEOUT_S = 30.0
DONE_MAX = 20_000                           # remembered copies per area (oldest dropped first)
MAX_DEPTH = 16                              # directory levels below the area root (as the bridge)
MAX_COMPONENT_BYTES = 255
CLASH_MAX = 999
ZONE_IDENTIFIER = "[ZoneTransfer]\r\nZoneId=3\r\n"  # Mark-of-the-Web: the Internet zone
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
WINDOWS_MAX_PATH = 259                      # characters in a full path without long-path support (MAX_PATH - 1)
ZONE_STREAM_SUFFIX = ":Zone.Identifier"
CLASH_SUFFIX_MAX = len(f" ({CLASH_MAX})")
REFUSED_MAX = 4096                          # remembered permanent refusals (in memory, oldest dropped first)
# A file the mirror can never write: refused once (audited), and it never holds the cursor.
PERMANENT_REASONS = frozenset({"name_too_long", "path_too_long", "sensitive_target", "over_cap", "size"})
# A failure of one file that is not permanent (a blocked folder, a sha256 mismatch, a quarantined copy) holds the
# cursor, but the file waits BACKOFF_FIRST_S before it is tried again, doubling up to BACKOFF_MAX_S (in memory).
BACKOFF_FIRST_S = 60.0
BACKOFF_MAX_S = 3600.0
# Failures that are not about one file: the pass stops (network) or the mirror pauses (byte cap) instead.
NO_BACKOFF_REASONS = frozenset({"no_link", "no_root", "root_unusable", "deferred_voice_active",
                                "deferred_voice_unknown"})
RATE_PAUSE_FIRST_S = 600.0                  # after a byte-cap trip; doubles on each trip in a row
RATE_PAUSE_MAX_S = 6 * 3600.0

AREA_SHARED, AREA_OUTPUTS = "shared", "outputs"
AREAS = (AREA_SHARED, AREA_OUTPUTS)
SHARED_SUBDIRS = frozenset({"Generated_Images", "QR_Codes", "Downloads"})  # K-73 allowed paths
OUTPUTS_SUBDIRS = frozenset({"Downloads", "leads"})
OUTPUTS_TOP_SUFFIX = ".zip"
EXCLUDED_NAMES = frozenset({"sim_lookup_history.jsonl"})
EXCLUDED_SUFFIXES = (".part", ".ytdl", ".tmp")
# Files Explorer parses when the user only browses the folder (icons, shortcuts, library and search locations):
# a UNC path in one of them leaks the user's NTLM hash (the CVE-2025-24054 class). None is legitimate VPS output.
SHELL_PARSED_NAMES = frozenset({"desktop.ini", "autorun.inf", "folder.htt"})
SHELL_PARSED_SUFFIXES = (".url", ".lnk", ".library-ms", ".searchconnector-ms", ".search-ms", ".scf", ".website",
                         ".theme", ".themepack", ".deskthemepack", ".appref-ms", ".pif")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    | {f"COM{n}" for n in "0123456789¹²³"}
    | {f"LPT{n}" for n in "0123456789¹²³"})
_WINDOWS_INVALID = frozenset('<>"|?*')

# Mirror error codes (K-12 style; DELIVERY_FAILED and DESKTOP_RATE_LIMITED are K-76 codes).
DELIVERY_FAILED = "DELIVERY_FAILED"
DESKTOP_RATE_LIMITED = "DESKTOP_RATE_LIMITED"
BAD_NAME = "BAD_NAME"


# --- errors --------------------------------------------------------------------------------------------------

class NodeHttpError(Exception):
    """A failed K-73 request: reason is a short code, status the HTTP status when there was one. str() never
    holds a path, a query value or a body."""

    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason if status is None else f"{reason} ({status})")
        self.reason = reason
        self.status = status


class MirrorNameError(ValueError):
    """A relpath the mirror refuses; reason names the rule (never the name)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MirrorError(Exception):
    """A file that was not mirrored: code DELIVERY_FAILED, DESKTOP_RATE_LIMITED or BAD_NAME; reason a short rule
    name (never a path)."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}:{reason}")
        self.code = code
        self.reason = reason


# --- the signed HTTP client ----------------------------------------------------------------------------------

_STATUS_REASONS = {400: "bad_request", 401: "auth", 403: "auth", 404: "not_found", 409: "replay", 413: "too_large",
                   416: "range", 429: "busy", 503: "unavailable", 507: "storage"}


class NodeHttp:
    """K-70 PC-signed HTTP to the bridge (K-73). base_url is the https origin of JARVIS_LINK_URL; paths are given
    without the public '/vortex' mount, which is added to the URL and the signed path alike (the canonical signed
    path drops it again, as nginx strips it). clock_offset() is the node's measured offset (agent - own clock), so
    X-Desk-Ts follows the VPS clock the bridge checks against; None means the own clock. transport: an httpx
    transport for tests."""

    def __init__(self, base_url: str, signing_key: Any, *, clock_offset: Callable[[], float | None] | None = None,
                 wall: Callable[[], float] = time.time, timeout_s: float = HTTP_TIMEOUT_S,
                 transport: Any = None) -> None:
        if not isinstance(base_url, str) or not base_url.lower().startswith("https://"):
            if transport is None:  # K-70: TLS verified, an http:// link URL is refused
                raise ValueError("the bridge URL must be https://")
        self.base_url = base_url.rstrip("/")
        # K-69: httpx logs every request line (with its query: area and relpath) at INFO; keep it at WARNING.
        for name in ("httpx", "httpcore"):
            if logging.getLogger(name).getEffectiveLevel() < logging.WARNING:
                logging.getLogger(name).setLevel(logging.WARNING)
        self._key = signing_key
        self._offset = clock_offset or (lambda: None)
        self._wall = wall
        self._timeout = timeout_s
        self._transport = transport

    def _now(self) -> float:
        try:
            offset = self._offset()
        except Exception:  # pylint: disable=broad-exception-caught  # the own clock then
            offset = None
        return self._wall() + (offset if isinstance(offset, (int, float)) else 0.0)

    def _client(self) -> Any:
        import httpx  # pylint: disable=import-outside-toplevel  # only the node loads it

        options: dict[str, Any] = {"timeout": self._timeout, "follow_redirects": False}
        if self._transport is not None:
            options["transport"] = self._transport
        return httpx.Client(**options)

    def _signed(self, method: str, path: str, query: Mapping[str, Any] | None, body: bytes) -> dict[str, str]:
        return dp.sign_http_request(self._key, method=method, path=PUBLIC_PREFIX + path, query=dict(query or {}),
                                    body=body, now=self._now())

    def _url(self, path: str) -> str:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("path must be an absolute path without a query")
        return self.base_url + PUBLIC_PREFIX + path

    @staticmethod
    def _check(response: Any) -> None:
        status = int(response.status_code)
        if status != 200:
            raise NodeHttpError(_STATUS_REASONS.get(status, f"http_{status}"), status)

    def get_json(self, path: str, query: Mapping[str, Any] | None = None, *,
                 max_bytes: int = INDEX_MAX_BYTES) -> Any:
        """GET path?query -> its JSON body (at most max_bytes)."""
        chunks: list[bytes] = []
        self.stream_get(path, query, chunks.append, max_bytes=max_bytes)
        try:
            return dp.loads_strict(b"".join(chunks))
        except (ValueError, dp.CanonicalJsonError, RecursionError):
            raise NodeHttpError("bad_json") from None

    def stream_get(self, path: str, query: Mapping[str, Any] | None, sink: Callable[[bytes], Any], *,
                   max_bytes: int) -> dict[str, str]:
        """GET path?query, streaming the body into sink() (at most max_bytes, else NodeHttpError('too_large')).
        Returns the response headers (lower-cased names)."""
        import httpx  # pylint: disable=import-outside-toplevel

        headers = self._signed("GET", path, query, b"")
        received = 0
        try:
            with self._client() as client:
                with client.stream("GET", self._url(path), params=dict(query or {}), headers=headers) as response:
                    self._check(response)
                    declared = response.headers.get("content-length")
                    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                        raise NodeHttpError("too_large")
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > max_bytes:
                            raise NodeHttpError("too_large")
                        sink(chunk)
                    return {str(name).lower(): str(value) for name, value in response.headers.items()}
        except httpx.HTTPError as exc:
            raise NodeHttpError(f"network_{type(exc).__name__}") from None

    def post(self, path: str, body: bytes, query: Mapping[str, Any] | None = None, *,
             content_type: str = "application/octet-stream") -> Any:
        """POST path with a signed body -> its JSON answer."""
        import httpx  # pylint: disable=import-outside-toplevel

        headers = self._signed("POST", path, query, body)
        headers["Content-Type"] = content_type
        try:
            with self._client() as client:
                response = client.post(self._url(path), params=dict(query or {}), headers=headers, content=body)
                self._check(response)
                payload = response.content
        except httpx.HTTPError as exc:
            raise NodeHttpError(f"network_{type(exc).__name__}") from None
        try:
            return dp.loads_strict(payload)
        except (ValueError, dp.CanonicalJsonError, RecursionError):
            raise NodeHttpError("bad_json") from None


def node_http(node: Any, *, transport: Any = None) -> NodeHttp:
    """The NodeHttp of a running node: its JARVIS_LINK_URL origin, its signing key, its measured clock offset."""
    from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

    base = node_settings.link_target(node.settings.link_url)[0]
    clock = getattr(node, "clock", None)
    return NodeHttp(base, node.signer.key, clock_offset=lambda: getattr(clock, "offset_s", None),
                    transport=transport)


# --- name validation -----------------------------------------------------------------------------------------

def validate_component(part: Any) -> str | None:
    """The rule one relpath component breaks, or None when it is safe as a Windows (and POSIX) file name.

    The order matters only for the reason given: every rule is checked."""
    # pylint: disable=too-many-return-statements
    if not isinstance(part, str) or part == "":
        return "empty"
    if part in (".", ".."):
        return "dot"
    if "\\" in part:
        return "backslash"
    if "/" in part:
        return "separator"
    if ":" in part:
        return "colon"
    if ".." in part:
        return "dotdot"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in part) or not part.isprintable():
        return "control"
    if part[-1] in (".", " "):
        return "trailing"
    if part[0] == " ":
        return "leading_space"
    if part.startswith("."):
        return "hidden"
    if any(ch in _WINDOWS_INVALID for ch in part):
        return "invalid_char"
    if part.split(".", 1)[0].rstrip(" ").upper() in _DEVICE_NAMES:
        return "device"
    if len(part.encode("utf-8", "surrogatepass")) > MAX_COMPONENT_BYTES:
        return "too_long"
    return None


def is_excluded_name(name: str) -> bool:
    """True for a file name the mirror never writes: a partial or temporary download (EXCLUDED_NAMES,
    EXCLUDED_SUFFIXES) or a name Explorer parses while browsing (SHELL_PARSED_NAMES, SHELL_PARSED_SUFFIXES). The
    backup pull applies the same rule to the snapshot name."""
    lowered = name.lower()
    return (name in EXCLUDED_NAMES or lowered.endswith(EXCLUDED_SUFFIXES)
            or lowered in SHELL_PARSED_NAMES or lowered.endswith(SHELL_PARSED_SUFFIXES))


def validate_relpath(area: str, relpath: Any) -> list[str]:
    """The components of a K-73 relpath ('/'-separated) inside the area's allowed subtrees, or MirrorNameError."""
    if area not in AREAS:
        raise MirrorNameError("area")
    if not isinstance(relpath, str) or not relpath:
        raise MirrorNameError("empty")
    if relpath.startswith("/"):
        raise MirrorNameError("absolute")
    components = relpath.split("/")
    for part in components:
        problem = validate_component(part)
        if problem is not None:
            raise MirrorNameError(problem)
    if len(components) - 1 > MAX_DEPTH:
        raise MirrorNameError("too_deep")
    name = components[-1]
    if is_excluded_name(name):
        raise MirrorNameError("excluded")
    if area == AREA_SHARED:
        allowed = len(components) >= 2 and components[0] in SHARED_SUBDIRS
    else:
        allowed = ((len(components) >= 2 and components[0] in OUTPUTS_SUBDIRS)
                   or (len(components) == 1 and name.lower().endswith(OUTPUTS_TOP_SUFFIX)))
    if not allowed:
        raise MirrorNameError("subtree")
    return components


def clash_name(name: str, n: int) -> str:
    """'report.pdf' -> 'report (n).pdf'; a name without an extension gets ' (n)' at its end."""
    stem, dot, ext = name.rpartition(".")
    if dot and stem:
        return f"{stem} ({n}).{ext}"
    return f"{name} ({n})"


def cursor_below(mtime_ns: int) -> float:
    """A cursor (epoch seconds, ms precision) strictly below mtime_ns: the bridge lists mtime_ns > cursor, so a
    file whose copy failed is listed again."""
    return (int(mtime_ns) // 1_000_000 - 1) / 1000.0


def _cursor_text(cursor: float) -> str:
    return f"{cursor:.3f}"


# --- filesystem helpers --------------------------------------------------------------------------------------

def _is_link_or_reparse(path: Path) -> bool:
    """True for a symlink, a junction or any other reparse point (never followed). A missing path is False."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat_mod.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(os.path.realpath(path)).relative_to(root)
    except ValueError:
        return False
    return True


def write_mark_of_the_web(path: Path) -> None:
    """Zone.Identifier ZoneId=3 on an NTFS file (Windows only; elsewhere there is no such stream)."""
    if os.name != "nt":
        return
    with open(f"{path}:Zone.Identifier", "w", encoding="ascii", newline="") as handle:
        handle.write(ZONE_IDENTIFIER)


def _place(part: Path, final: Path) -> None:
    """Moves part to final without ever replacing an existing file (FileExistsError then): os.rename on Windows,
    a hard link plus unlink elsewhere (POSIX rename would overwrite)."""
    if os.name == "nt":
        os.rename(part, final)
        return
    os.link(part, final)
    try:
        os.unlink(part)
    except OSError:
        pass


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


# --- state ---------------------------------------------------------------------------------------------------

@dataclass
class _AreaState:
    cursor: float | None = None
    done: dict[str, dict[str, Any]] = field(default_factory=dict)  # relpath -> {size, mtime_ns, sha256, local}


@dataclass
class SyncReport:
    """What one pass over an area did (counts only)."""

    area: str
    full: bool = False
    listed: int = 0
    copied: int = 0
    skipped: int = 0
    deferred: int = 0
    backoff: int = 0
    failed: int = 0
    refused: int = 0
    cursor: float | None = None


@dataclass
class _CapPass:
    """The byte-cap state of one pass (shared by both areas of sync_once): after a trip, files at least as large
    as the smallest one that tripped wait; CAP_TRIPS_PER_PASS trips stop the pass; the mirror pauses once."""

    trips: int = 0
    ceiling: int | None = None
    paused: bool = False

    def holds(self, size: int) -> bool:
        return self.trips >= CAP_TRIPS_PER_PASS or (self.ceiling is not None and size >= self.ceiling)


class MediaMirror:
    """The node's mirror of the VPS media areas (see the module docstring). Every method that touches the network
    or the disk is synchronous and runs in a worker thread; run() is the node task."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments

    def __init__(self, *, http: Callable[[], NodeHttp | None], roots: Callable[[], Mapping[str, Any]],
                 state_path: Path, voice_active: Callable[[], bool | None] = lambda: False,  # pylint: disable=redefined-outer-name
                 note_bytes: Callable[[int], str | None] | None = None, audit: Callable[..., None] | None = None,
                 sensitive: Callable[[Path], bool] | None = None, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic, reconcile_s: float = RECONCILE_S,
                 large_bytes: int = LARGE_FILE_BYTES, poll_s: float = POLL_S,
                 ready: Callable[[], bool] = lambda: True, delivery_cap: int = DELIVERIES_CAP_BYTES) -> None:
        self._http = http
        self._ready = ready                    # the loop polls only while this holds (the node's link is up)
        self._skip_reason: dict[str, str] = {}  # the last skip reason per area, logged at INFO once
        self._roots = roots
        self.state_path = Path(state_path)
        self._voice_active = voice_active
        self._note_bytes = note_bytes
        self._audit = audit
        self._sensitive = sensitive
        self._wall, self._mono = wall, mono
        self.reconcile_s, self.large_bytes, self.poll_s = reconcile_s, large_bytes, poll_s
        self.delivery_cap = int(delivery_cap)  # a file larger than the whole deliveries cap is refused for good
        self._lock = threading.RLock()         # state and name allocation
        self._areas: dict[str, _AreaState] = {area: _AreaState() for area in AREAS}
        self._last_full: float | None = None   # monotonic time of the last full reconcile
        self._paused_until = 0.0               # monotonic: after a byte-cap trip
        self._refused: dict[tuple[str, str, int, int], str] = {}  # permanent refusals: (area, relpath, size, mtime)
        # refused names, audited once: (area, relpath, size, mtime) -> reason (in memory, bounded by REFUSED_MAX)
        self._name_refused: dict[tuple[str, str, int, int], str] = {}
        self._failed: dict[tuple[str, str], str] = {}  # the last logged copy failure reason per (area, relpath)
        # (area, relpath) -> (size, mtime_ns, next try on the monotonic clock, current delay): a failed file waits
        self._backoff: dict[tuple[str, str], tuple[int, int, float, float]] = {}
        self._rate_pause_s = 0.0               # the last byte-cap pause; 0 after a copy succeeds
        self._voice_unknown_logged = False     # the unknown voice state was logged in the current unknown spell
        self.last_report: dict[str, SyncReport] = {}
        self._load()

    def voice_state(self) -> bool | None:
        """voice_active() as True (active), False (not active) or None (unknown, which counts as active)."""
        try:
            value = self._voice_active()
        except Exception:  # pylint: disable=broad-exception-caught  # unknown then
            return None
        return value if isinstance(value, bool) else None

    def large_voice_state(self) -> bool | None:
        """voice_state() for a file over the large-file limit: anything but False defers it (unless opened).
        Unknown (no desktop_status from the bridge on the connected link session) is logged once per unknown spell
        at INFO; a known value ends the spell."""
        voice = self.voice_state()
        with self._lock:
            first = voice is None and not self._voice_unknown_logged
            self._voice_unknown_logged = voice is None
        if first:
            logger.info("Mirror: the voice state is unknown (no status from the bridge on this link session); files "
                        "over %d MB wait until it is known, unless opened", self.large_bytes // (1024 * 1024))
        return voice

    # --- state file ------------------------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, RecursionError) as exc:
            logger.warning("Mirror state unreadable (%s); starting over with a full listing", type(exc).__name__)
            return
        if not isinstance(raw, dict) or raw.get("v") != STATE_VERSION:
            return
        for area in AREAS:
            item = raw.get("areas", {}).get(area) if isinstance(raw.get("areas"), dict) else None
            if not isinstance(item, dict):
                continue
            cursor = item.get("cursor")
            done = item.get("done")
            state = self._areas[area]
            state.cursor = float(cursor) if isinstance(cursor, (int, float)) and not isinstance(cursor, bool) \
                else None
            if isinstance(done, dict):
                state.done = {key: value for key, value in done.items()
                              if isinstance(key, str) and isinstance(value, dict)}

    def _save(self) -> None:
        doc = {"v": STATE_VERSION, "areas": {area: {"cursor": state.cursor, "done": state.done}
                                              for area, state in self._areas.items()}}
        tmp = self.state_path.with_name(f"{self.state_path.name}.{secrets.token_hex(4)}.tmp")
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as exc:
            logger.warning("Mirror state not saved (%s)", type(exc).__name__)
            try:
                tmp.unlink()
            except OSError:
                pass

    def cursor(self, area: str) -> float | None:
        with self._lock:
            return self._areas[area].cursor

    def is_done(self, area: str, relpath: str, size: int, mtime_ns: int | None) -> bool:
        """Dedupe: this relpath was copied with this size and mtime (and its copy is still there)."""
        with self._lock:
            entry = self._areas[area].done.get(relpath)
        if not entry or entry.get("size") != size or (mtime_ns is not None and entry.get("mtime_ns") != mtime_ns):
            return False
        return True

    def _remember(self, area: str, relpath: str, *, size: int, mtime_ns: int | None, sha256: str | None,
                  local: str) -> None:
        with self._lock:
            done = self._areas[area].done
            done.pop(relpath, None)
            done[relpath] = {"size": size, "mtime_ns": mtime_ns, "sha256": sha256, "local": local}
            while len(done) > DONE_MAX:
                done.pop(next(iter(done)))

    def _audit_line(self, event: str, **fields: Any) -> None:
        if self._audit is None:
            return
        try:
            self._audit(event, **fields)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Mirror audit callback failed")

    # --- roots and paths -------------------------------------------------------------------------------------

    def root(self, area: str) -> Path:
        """The real (resolved) local root of an area; created when missing."""
        roots = self._roots()
        value = roots.get(area) if isinstance(roots, Mapping) else None
        if not value or not str(value).strip():
            raise MirrorError(DELIVERY_FAILED, "no_root")
        root = Path(str(value)).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise MirrorError(DELIVERY_FAILED, "root_unusable") from None
        return Path(os.path.realpath(root))

    def _target_dir(self, root: Path, components: list[str]) -> Path:
        """The folder for components[:-1] under root, created component by component; a symlink, junction or
        other reparse point on the way is refused, as is anything that resolves outside root (K-73 safe join)."""
        current = root
        for part in components[:-1]:
            current = current / part
            if _is_link_or_reparse(current):
                raise MirrorError(DELIVERY_FAILED, "reparse_point")
            try:
                os.mkdir(current)
            except FileExistsError:
                pass
            except OSError:
                raise MirrorError(DELIVERY_FAILED, "mkdir") from None
            if _is_link_or_reparse(current) or not current.is_dir():
                raise MirrorError(DELIVERY_FAILED, "not_a_folder")
        if not _inside(current, root):
            raise MirrorError(DELIVERY_FAILED, "outside_root")
        return current

    def _check_chain(self, root: Path, folder: Path) -> None:
        """Right before the rename: no component between root and folder became a reparse point."""
        try:
            relative = folder.relative_to(root)
        except ValueError:
            raise MirrorError(DELIVERY_FAILED, "outside_root") from None
        current = root
        for part in relative.parts:
            current = current / part
            if _is_link_or_reparse(current):
                raise MirrorError(DELIVERY_FAILED, "reparse_point")
        if not _inside(folder, root):
            raise MirrorError(DELIVERY_FAILED, "outside_root")

    def local_path(self, area: str, relpath: str) -> Path | None:
        """The local copy of a relpath the mirror wrote (and that still exists), else None."""
        with self._lock:
            entry = self._areas[area].done.get(relpath)
        if not entry or not isinstance(entry.get("local"), str):
            return None
        try:
            root = self.root(area)
            components = validate_relpath(area, relpath)
        except (MirrorError, MirrorNameError):
            return None
        local_parts = entry["local"].split("/")
        if len(local_parts) != len(components) or any(validate_component(p) for p in local_parts):
            return None
        path = root.joinpath(*local_parts)
        if not path.is_file() or _is_link_or_reparse(path) or not _inside(path, root):
            return None
        return path

    # --- one file --------------------------------------------------------------------------------------------

    def fetch_one(self, area: str, relpath: str, *, size: int, sha256: str | None = None,
                  mtime_ns: int | None = None, explicit: bool = False) -> Path:
        """Mirrors one relay file (K-73 write order) and returns its local path. An identical copy the mirror
        already wrote (same relpath and size, and the same sha256 when one is given) is returned as it is.
        explicit (pc.fetch): a file over the large-file limit is copied even while voice is active. The
        deliveries cap is charged only after every folder, name and sensitive check, right before the download."""
        # pylint: disable=too-many-arguments
        try:
            components = validate_relpath(area, relpath)
        except MirrorNameError as exc:
            raise MirrorError(BAD_NAME, exc.reason) from None
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= FILE_MAX_BYTES:
            raise MirrorError(DELIVERY_FAILED, "size")
        if sha256 is not None and not _HEX64.fullmatch(str(sha256)):
            raise MirrorError(DELIVERY_FAILED, "sha256")
        existing = self._existing_copy(area, relpath, size=size, sha256=sha256, mtime_ns=mtime_ns)
        if existing is not None:
            return existing
        if size > self.delivery_cap:  # it could never be charged: refused for good, never tripping the cap
            raise MirrorError(DELIVERY_FAILED, "over_cap")
        if size > self.large_bytes and not explicit:
            voice = self.large_voice_state()
            if voice is not False:
                raise MirrorError(DELIVERY_FAILED, "deferred_voice_active" if voice else "deferred_voice_unknown")
        root = self.root(area)
        folder = self._target_dir(root, components)
        final_name = components[-1]
        part = folder / part_name(final_name)
        if _path_too_long(folder, final_name, part.name):
            raise MirrorError(DELIVERY_FAILED, "path_too_long")
        sensitive = self._sensitive
        if sensitive is not None and (sensitive(folder / final_name) or sensitive(part)):
            raise MirrorError(DELIVERY_FAILED, "sensitive_target")
        if self._note_bytes is not None:  # charged only for a file that is really downloaded next
            code = self._note_bytes(size)
            if code:
                raise MirrorError(DESKTOP_RATE_LIMITED, "deliveries_cap")
        digest = self._download(area, relpath, part, size=size)
        try:
            if sha256 is not None and not dp.digest_equal(digest, sha256):
                raise MirrorError(DELIVERY_FAILED, "sha256_mismatch")
            try:
                write_mark_of_the_web(part)
            except OSError:
                raise MirrorError(DELIVERY_FAILED, "motw") from None
            if mtime_ns is not None:
                try:
                    os.utime(part, ns=(time.time_ns(), int(mtime_ns)))
                except (OSError, ValueError, OverflowError):
                    pass
            final = self._rename(root, folder, part, final_name, size=size, sha256=digest)
        finally:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning("Mirror could not remove a partial file")
        local = "/".join(final.relative_to(root).parts)
        self._remember(area, relpath, size=size, mtime_ns=mtime_ns, sha256=digest, local=local)
        with self._lock:
            self._backoff.pop((area, relpath), None)
            self._failed.pop((area, relpath), None)
        self._audit_line("mirror_copied", kind=area, count=size)
        return final

    def _existing_copy(self, area: str, relpath: str, *, size: int, sha256: str | None,
                       mtime_ns: int | None) -> Path | None:
        with self._lock:
            entry = self._areas[area].done.get(relpath)
        if not entry or entry.get("size") != size:
            return None
        if mtime_ns is not None and entry.get("mtime_ns") not in (None, mtime_ns):
            return None
        if sha256 is not None and not dp.digest_equal(entry.get("sha256"), sha256):
            return None
        path = self.local_path(area, relpath)
        if path is None:
            return None
        try:
            if path.stat().st_size != size:
                return None
        except OSError:
            return None
        return path

    def _download(self, area: str, relpath: str, part: Path, *, size: int) -> str:
        """Streams the relay file into part (created new, never an existing file); returns its sha256. The size
        must match exactly."""
        http = self._http()
        if http is None:
            raise MirrorError(DELIVERY_FAILED, "no_link")
        digest = hashlib.sha256()
        written = 0
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except OSError as exc:
            reason = "name_too_long" if _is_name_too_long(exc) else "part_create"
            raise MirrorError(DELIVERY_FAILED, reason) from None
        try:
            with os.fdopen(fd, "wb") as handle:
                def sink(chunk: bytes) -> None:
                    nonlocal written
                    written += len(chunk)
                    if written > size:
                        raise MirrorError(DELIVERY_FAILED, "size_mismatch")
                    digest.update(chunk)
                    handle.write(chunk)

                http.stream_get("/media/file", {"area": area, "path": relpath}, sink, max_bytes=size)
                handle.flush()
                os.fsync(handle.fileno())
        except NodeHttpError as exc:
            self._drop(part)
            raise MirrorError(DELIVERY_FAILED, f"http_{exc.reason}") from None
        except MirrorError:
            self._drop(part)
            raise
        except OSError:
            self._drop(part)
            raise MirrorError(DELIVERY_FAILED, "write") from None
        if written != size:
            self._drop(part)
            raise MirrorError(DELIVERY_FAILED, "size_mismatch")
        return digest.hexdigest()

    @staticmethod
    def _drop(part: Path) -> None:
        try:
            part.unlink()
        except OSError:
            pass

    def _rename(self, root: Path, folder: Path, part: Path, name: str, *, size: int, sha256: str) -> Path:
        """part -> name, or 'name (n)' on a clash; an identical file already there is kept and part dropped."""
        with self._lock:
            self._check_chain(root, folder)
            for n in range(0, CLASH_MAX + 1):
                candidate = folder / (name if n == 0 else clash_name(name, n))
                if validate_component(candidate.name) is not None:
                    raise MirrorError(DELIVERY_FAILED, "name_too_long")
                if _is_link_or_reparse(candidate):
                    continue
                if candidate.exists():
                    if self._same_file(candidate, size, sha256):
                        return candidate
                    continue
                try:
                    _place(part, candidate)
                except FileExistsError:
                    continue
                except OSError:
                    raise MirrorError(DELIVERY_FAILED, "rename") from None
                if not _inside(candidate, root):  # cannot happen after the chain check; fail closed anyway
                    raise MirrorError(DELIVERY_FAILED, "outside_root")
                return candidate
        raise MirrorError(DELIVERY_FAILED, "too_many_clashes")

    @staticmethod
    def _same_file(path: Path, size: int, sha256: str) -> bool:
        try:
            if not path.is_file() or path.stat().st_size != size:
                return False
        except OSError:
            return False
        return dp.digest_equal(_sha256_file(path), sha256)

    # --- one pass --------------------------------------------------------------------------------------------

    def sync_area(self, area: str, *, full: bool = False, cap: _CapPass | None = None) -> SyncReport:
        """One listing of an area and the copies it calls for (see the module docstring). cap is the byte-cap
        state of the pass (sync_once shares one between the areas); a call without it is a pass of its own."""
        # pylint: disable=too-many-branches,too-many-statements
        own_pass = cap is None
        cap = cap if cap is not None else _CapPass()
        report = SyncReport(area=area, full=full)
        http = self._http()
        if http is None:
            raise MirrorError(DELIVERY_FAILED, "no_link")
        query: dict[str, Any] = {"area": area}
        cursor = self.cursor(area)
        if not full and cursor is not None:
            query["cursor"] = _cursor_text(cursor)
        try:
            index = http.get_json("/media/index", query)
        except NodeHttpError as exc:
            raise MirrorError(DELIVERY_FAILED, f"index_{exc.reason}") from None
        entries = index.get("entries") if isinstance(index, dict) else None
        server_cursor = index.get("cursor") if isinstance(index, dict) else None
        if not isinstance(entries, list) or isinstance(server_cursor, bool) or \
                not isinstance(server_cursor, (int, float)):
            raise MirrorError(DELIVERY_FAILED, "index_format")
        hold_ns: int | None = None
        stopped = False
        for entry in entries:
            row = _index_row(entry)
            if row is None:
                report.refused += 1
                continue
            relpath, size, mtime_ns, sha256 = row
            report.listed += 1
            key = (area, relpath, size, mtime_ns)
            with self._lock:
                known_refusal = key in self._refused or key in self._name_refused
            if known_refusal:  # refused before: never retried, never audited again, never holds the cursor
                report.refused += 1
                continue
            if stopped:
                hold_ns = mtime_ns if hold_ns is None else min(hold_ns, mtime_ns)
                report.deferred += 1
                continue
            try:
                validate_relpath(area, relpath)
            except MirrorNameError as exc:
                report.refused += 1
                self._refuse_name(key, exc.reason)
                continue
            if self.is_done(area, relpath, size, mtime_ns):
                report.skipped += 1
                continue
            if size > self.delivery_cap or size > FILE_MAX_BYTES:  # can never be charged or written: refused
                report.refused += 1
                self._refuse(area, relpath, size, mtime_ns, "over_cap" if size <= FILE_MAX_BYTES else "size")
                continue
            if cap.holds(size):  # after a cap trip in this pass: as large as a file that tripped, it waits
                report.deferred += 1
                hold_ns = mtime_ns if hold_ns is None else min(hold_ns, mtime_ns)
                continue
            if size > self.large_bytes and self.large_voice_state() is not False:
                report.deferred += 1
                hold_ns = mtime_ns if hold_ns is None else min(hold_ns, mtime_ns)
                continue
            if self._backing_off(area, relpath, size, mtime_ns):
                report.backoff += 1  # it failed before and waits: it still holds the cursor
                hold_ns = mtime_ns if hold_ns is None else min(hold_ns, mtime_ns)
                continue
            try:
                self.fetch_one(area, relpath, size=size, sha256=sha256, mtime_ns=mtime_ns)
                report.copied += 1
            except MirrorError as exc:
                if exc.code == BAD_NAME:
                    report.refused += 1
                    continue
                if exc.reason in PERMANENT_REASONS:
                    report.refused += 1
                    self._refuse(area, relpath, size, mtime_ns, exc.reason)
                    continue
                report.failed += 1
                hold_ns = mtime_ns if hold_ns is None else min(hold_ns, mtime_ns)
                self._log_failure(area, relpath, exc.reason)
                if exc.code == DESKTOP_RATE_LIMITED:
                    # this file did not fit the room left in the cap: it and every file at least as large wait
                    # (holding the cursor); smaller files are still tried, and a second trip stops the pass
                    cap.trips += 1
                    cap.ceiling = size if cap.ceiling is None else min(cap.ceiling, size)
                    if not cap.paused:
                        cap.paused = True
                        self._pause_for_cap()
                elif exc.reason.startswith("http_network"):
                    stopped = True  # the rest waits for the next pass (and holds the cursor)
                elif exc.reason not in NO_BACKOFF_REASONS:
                    self._back_off(area, relpath, size, mtime_ns)
        new_cursor = float(server_cursor)
        if hold_ns is not None:
            new_cursor = min(new_cursor, cursor_below(hold_ns))
        with self._lock:
            self._areas[area].cursor = new_cursor
            if own_pass and report.copied and not cap.trips:
                self._rate_pause_s = 0.0  # copies without a trip: the next trip pauses RATE_PAUSE_FIRST_S again
        self._save()
        report.cursor = new_cursor
        self.last_report[area] = report
        return report

    def _backing_off(self, area: str, relpath: str, size: int, mtime_ns: int) -> bool:
        """True while this version (size, mtime_ns) of a file that failed waits for its next try. A changed
        version is tried at once."""
        with self._lock:
            entry = self._backoff.get((area, relpath))
        if entry is None or (entry[0], entry[1]) != (size, mtime_ns):
            return False
        return self._mono() < entry[2]

    def _back_off(self, area: str, relpath: str, size: int, mtime_ns: int) -> None:
        """A failure of one file that is not permanent: its next try waits BACKOFF_FIRST_S, doubling on every
        failure in a row of the same version up to BACKOFF_MAX_S, so a blocked folder or a body that never
        matches its sha256 is neither charged nor downloaded on every pass."""
        key = (area, relpath)
        with self._lock:
            entry = self._backoff.pop(key, None)
            if entry is not None and (entry[0], entry[1]) == (size, mtime_ns):
                delay = min(entry[3] * 2.0, BACKOFF_MAX_S)
            else:
                delay = BACKOFF_FIRST_S
            self._backoff[key] = (size, mtime_ns, self._mono() + delay, delay)
            while len(self._backoff) > REFUSED_MAX:
                self._backoff.pop(next(iter(self._backoff)))

    def _pause_for_cap(self) -> None:
        """A pass with a byte-cap trip pauses the whole mirror: RATE_PAUSE_FIRST_S, doubling on each such pass in
        a row up to RATE_PAUSE_MAX_S (a pass that copies without a trip resets it; copies of smaller files in the
        pass that tripped do not), so a full cap does not raise its toast, alert and audit line every few
        minutes."""
        with self._lock:
            pause = RATE_PAUSE_FIRST_S if self._rate_pause_s <= 0 else min(self._rate_pause_s * 2.0, RATE_PAUSE_MAX_S)
            self._rate_pause_s = pause
            self._paused_until = self._mono() + max(self.poll_s, pause)

    def _refuse_name(self, key: tuple[str, str, int, int], reason: str) -> None:
        """A relpath the name rules refuse: audited the first time (rule name and area only, never the name),
        then only counted in report.refused while the bridge keeps listing this version of it."""
        with self._lock:
            first = key not in self._name_refused
            self._name_refused[key] = reason
            while len(self._name_refused) > REFUSED_MAX:
                self._name_refused.pop(next(iter(self._name_refused)))
        if first:
            self._audit_line("mirror_name_refused", kind=key[0], reason=reason)

    def _refuse(self, area: str, relpath: str, size: int, mtime_ns: int, reason: str) -> None:
        """A file the mirror can never write (PERMANENT_REASONS): audited and logged once, then skipped; it
        never holds the cursor. The memory is per process, so a restart tries it once more."""
        with self._lock:
            self._refused[(area, relpath, size, mtime_ns)] = reason
            while len(self._refused) > REFUSED_MAX:
                self._refused.pop(next(iter(self._refused)))
            self._failed.pop((area, relpath), None)
        logger.warning("Mirror refused a file it cannot write (%s, %s)", area, reason)
        self._audit_line("mirror_refused", kind=area, reason=reason)

    def _log_failure(self, area: str, relpath: str, reason: str) -> None:
        """A copy failure that holds the cursor: WARNING the first time for a file and reason, DEBUG on repeats
        (the line never holds the relpath)."""
        key = (area, relpath)
        with self._lock:
            repeated = self._failed.get(key) == reason
            self._failed.pop(key, None)
            self._failed[key] = reason
            while len(self._failed) > REFUSED_MAX:
                self._failed.pop(next(iter(self._failed)))
        logger.log(logging.DEBUG if repeated else logging.WARNING, "Mirror copy failed (%s, %s)", area, reason)

    def sync_once(self) -> dict[str, SyncReport]:
        """Both areas: a full listing when RECONCILE_S has passed since the last one (or never ran)."""
        now = self._mono()
        if now < self._paused_until:
            return {}
        full = self._last_full is None or now - self._last_full >= self.reconcile_s
        reports: dict[str, SyncReport] = {}
        failures = 0
        cap = _CapPass()  # one byte-cap state for both areas: at most CAP_TRIPS_PER_PASS trips and one pause
        for area in AREAS:
            try:
                reports[area] = self.sync_area(area, full=full, cap=cap)
                self._skip_reason.pop(area, None)
            except MirrorError as exc:
                failures += 1
                repeated = self._skip_reason.get(area) == exc.reason
                self._skip_reason[area] = exc.reason
                logger.log(logging.DEBUG if repeated else logging.INFO, "Mirror pass for %s skipped (%s)", area,
                           exc.reason)
        if full and failures == 0:
            self._last_full = now
        if not cap.trips and any(report.copied for report in reports.values()):
            with self._lock:
                self._rate_pause_s = 0.0  # copies without a trip: the next trip pauses RATE_PAUSE_FIRST_S again
        return reports

    async def run(self) -> None:
        """The node task: a pass every poll_s, in a worker thread, while ready() holds (the link is up)."""
        while True:
            try:
                ready = bool(self._ready())
            except Exception:  # pylint: disable=broad-exception-caught
                ready = False
            if not ready:
                await asyncio.sleep(self.poll_s)
                continue
            try:
                reports = await asyncio.to_thread(self.sync_once)
                copied = sum(report.copied for report in reports.values())
                if copied:
                    logger.info("Mirror: %d new file(s) copied", copied)
            except Exception:  # pylint: disable=broad-exception-caught  # the loop never ends on one bad pass
                logger.exception("Mirror pass failed")
            await asyncio.sleep(self.poll_s)


def _index_row(entry: Any) -> tuple[str, int, int, str | None] | None:
    """(relpath, size, mtime_ns, sha256 or None) of a well-formed index entry, else None."""
    if not isinstance(entry, dict):
        return None
    relpath, size, mtime_ns, sha256 = entry.get("relpath"), entry.get("size"), entry.get("mtime_ns"), \
        entry.get("sha256")
    if not isinstance(relpath, str) or not _is_count(size) or not _is_count(mtime_ns):
        return None
    if sha256 is not None and not (isinstance(sha256, str) and _HEX64.fullmatch(sha256)):
        return None
    return relpath, size, mtime_ns, sha256


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def part_name(name: str) -> str:
    """'<name>.<8 hex>.part', with name shortened (by characters, from its end) so the whole stays within
    MAX_COMPONENT_BYTES as UTF-8, which is never fewer than its UTF-16 units (the NTFS limit)."""
    suffix = f".{secrets.token_hex(4)}.part"
    stem = name
    while stem and len((stem + suffix).encode("utf-8", "surrogatepass")) > MAX_COMPONENT_BYTES:
        stem = stem[:-1]
    return stem + suffix


_LONG_PATHS: list[bool] = []


def _long_paths_enabled() -> bool:
    """Windows long-path support (LongPathsEnabled; python.exe is long-path aware); read once."""
    if not _LONG_PATHS:
        enabled = False
        try:
            import winreg  # pylint: disable=import-outside-toplevel

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
                enabled = winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
        except (ImportError, OSError):
            enabled = False
        _LONG_PATHS.append(enabled)
    return _LONG_PATHS[0]


def _path_too_long(folder: Path, final_name: str, part: str) -> bool:
    """On Windows without long-path support: the .part file, the final name with a clash suffix or their
    Mark-of-the-Web stream would pass MAX_PATH, so the copy can never succeed."""
    if os.name != "nt" or _long_paths_enabled():
        return False
    base = len(str(folder)) + 1 + len(ZONE_STREAM_SUFFIX)
    return base + max(len(part), len(final_name) + CLASH_SUFFIX_MAX) > WINDOWS_MAX_PATH


def _is_name_too_long(exc: OSError) -> bool:
    """ENAMETOOLONG, or Windows ERROR_FILENAME_EXCED_RANGE (206)."""
    return exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) == 206


# --- node wiring ---------------------------------------------------------------------------------------------

def node_roots(node: Any) -> dict[str, str]:
    """{shared: config.shared_dir, outputs: config.outputs_dir} of the node (the PC brain's folders)."""
    config = getattr(node, "config", None)
    shared = getattr(config, "shared_dir", None) or os.environ.get("JARVIS_SHARED_DIR") or \
        str(Path.home() / "Jarvis_Shared")
    outputs = getattr(config, "outputs_dir", None) or os.environ.get("JARVIS_OUTPUTS_DIR") or \
        str(Path(node.data_root) / "Jarvis_Outputs")
    return {AREA_SHARED: str(shared), AREA_OUTPUTS: str(outputs)}


def voice_state(node: Any) -> bool | None:
    """desktop_status.voice_active from the bridge (K-74): True, False, or None when it is unknown. On a
    NodeRuntime the value counts only while trusted_desktop_status says so: from the connected link session, and
    pushed by the bridge in status hints (then the last pushed value holds until a newer hint replaces it) or at
    most STATUS_FRESH_S old (a welcome alone is a snapshot); anything else is unknown."""
    trusted = getattr(node, "trusted_desktop_status", None)
    if callable(trusted):
        status = trusted(STATUS_FRESH_S)
    else:
        status = (getattr(node, "bridge_hint", None) or {}).get("desktop_status")
    if not isinstance(status, Mapping):
        return None
    value = status.get("voice_active")
    return value if isinstance(value, bool) else None


def voice_active(node: Any) -> bool:
    """voice_state as a bool: unknown counts as active, so a large file never competes with a voice session the
    node cannot see."""
    return voice_state(node) is not False


def _note_deliveries(node: Any) -> Callable[[int], str | None]:
    def note(size: int) -> str | None:
        note_bytes = getattr(getattr(node, "policy", None), "note_bytes", None)
        if note_bytes is None:
            return None
        return note_bytes("deliveries", size)
    return note


def deliveries_cap(node: Any = None) -> int:
    """The deliveries byte cap (K-76: 5 GB a day) the node's policy enforces: its RateLimiter's
    BYTE_LIMITS['deliveries'], else the policy class value, else DELIVERIES_CAP_BYTES."""
    limits = getattr(getattr(getattr(node, "policy", None), "_caps", None), "BYTE_LIMITS", None)
    if not isinstance(limits, Mapping):
        try:
            from src.core.desktop import policy as policy_mod  # pylint: disable=import-outside-toplevel

            limits = policy_mod.RateLimiter.BYTE_LIMITS
        except Exception:  # pylint: disable=broad-exception-caught  # the documented K-76 value then
            limits = None
    item = limits.get("deliveries") if isinstance(limits, Mapping) else None
    value = item[0] if isinstance(item, tuple) and item else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return DELIVERIES_CAP_BYTES


def _sensitive_check(node: Any) -> Callable[[Path], bool]:
    """The K-76 extended sensitive set of the node (policy.is_sensitive_path), with the outputs folder allowed."""
    def sensitive(path: Path) -> bool:
        from src.core.desktop import policy as policy_mod  # pylint: disable=import-outside-toplevel

        roots = node_roots(node)
        appdata: Path | None
        try:
            from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

            appdata = node_settings.node_home().parent
        except Exception:  # pylint: disable=broad-exception-caught
            appdata = None
        return policy_mod.is_sensitive_path(path, data_root=Path(node.data_root),
                                            outputs_dir=Path(roots[AREA_OUTPUTS]), jarvis_appdata=appdata)
    return sensitive


def build_mirror(node: Any, *, http: Callable[[], NodeHttp | None] | None = None) -> MediaMirror:
    """The node's MediaMirror (shared by the mirror task and pc.fetch)."""
    def default_http() -> NodeHttp | None:
        try:
            return node_http(node)
        except Exception:  # pylint: disable=broad-exception-caught  # no usable link URL: nothing to mirror
            logger.warning("Mirror has no usable bridge URL")
            return None

    audit = getattr(getattr(node, "audit", None), "record", None)
    return MediaMirror(http=http or default_http, roots=lambda: node_roots(node),
                       state_path=Path(node.run_dir) / STATE_FILE, voice_active=lambda: voice_state(node),
                       note_bytes=_note_deliveries(node), audit=audit, sensitive=_sensitive_check(node),
                       ready=lambda: bool(getattr(getattr(node, "link", None), "connected", False)),
                       delivery_cap=deliveries_cap(node))


def node_mirror(node: Any) -> MediaMirror:
    """The node's one MediaMirror (built on first use)."""
    mirror = getattr(node, "media_mirror", None)
    if mirror is None:
        mirror = build_mirror(node)
        node.media_mirror = mirror
    return mirror


def install(node: Any) -> None:
    """NodeRuntime plugin hook: the mirror loop as an active-only task (never in --canary)."""
    mirror = node_mirror(node)
    node.add_task(mirror.run, active_only=True, name="mirror")
    logger.info("Desktop mirror installed (poll %.0f s, reconcile %.0f min)", mirror.poll_s, mirror.reconcile_s / 60)


__all__ = ["AREAS", "MediaMirror", "MirrorError", "MirrorNameError", "NodeHttp", "NodeHttpError", "SyncReport",
           "build_mirror", "clash_name", "cursor_below", "install", "is_excluded_name", "node_http", "node_mirror",
           "node_roots", "part_name", "validate_component", "validate_relpath", "voice_active", "voice_state",
           "write_mark_of_the_web"]
