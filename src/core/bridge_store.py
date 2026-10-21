"""
# bridge_store.py
SQLite store of the UI bridge (plan task C-13; contracts C-STATE-1 and C-MSG-1).

One file, BRIDGE_DB_PATH (default <project>/data/bridge.db, which is /app/data/bridge.db on the
jarvis_bridge_data volume in the container), keeps everything the bridge must not lose on a
restart: chat messages, UI log/memory/tool events, settings, owner sessions, the audit trail, a
key-value table, confirmations, the offline command queue, seen message ids and stored UI
notifications. Volatile flags (speaking, thinking, ...) stay in the bridge's memory.

One connection guarded by a threading.Lock serves every call, and the async methods run the
SQLite work through asyncio.to_thread, so the bridge's event loop never waits on the disk. WAL
mode lets deploy/hostinger/backup_vps.sh copy the file online while the bridge writes. A corrupt
file is renamed to bridge.db.corrupt-<UTC time> (never deleted) and a fresh one is started with a
CRITICAL log. Stdlib only, so the CLI runs in the slim bridge image:

    python -m src.core.bridge_store stats [--db PATH]   # row counts per table and role, no content
"""
# pylint: disable=too-many-lines  # one module per database: the schema, every table's calls, the CLI

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

# The bridge's own logger name: jarvis_logger never forwards "UI-BRIDGE" records to /notify, so a
# store log line can never come back into the store as a vortex_log event.
logger = logging.getLogger("UI-BRIDGE")

P = ParamSpec("P")
T = TypeVar("T")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bridge.db"
SCHEMA_VERSION = 1
MESSAGE_PAGE_MAX = 100

# init_state sizes (C-STATE-1)
INIT_LOGS = 30
INIT_MEMORIES = 10
INIT_TOOL_LOGS = 20
INIT_NOTIFICATIONS = 50

EVENT_LOG = "log"        # notify vortex_log / vortex_log_batch items -> init_state vortex_logs / logs
EVENT_MEMORY = "memory"  # notify memory_update -> init_state memories
EVENT_TOOL = "tool"      # notify tool_sync     -> init_state tool_logs

# kv keys of the VPS brain (C-STATE-1 v4). Their writers arrive with C-23 and C-116; the names
# live here so every writer and reader agrees on them.
KV_DESKTOP_LINK = "desktop_link"          # 'on' | 'off': the persisted Desktop Link switch (K-70)
KV_ACTIVE_JOB = "active_job"              # the one agent job allowed to drive the laptop
KV_PAUSED = "paused"                      # bool: the owner paused the agent (C-ROOM-1)
KV_LAST_ACTIVITY_TS = "last_activity_ts"  # epoch s of the last owner activity
# C-23 (K-42 v9): the room owner's state (src/core/bridge_rooms.py). The admin CLI writes the mode override.
KV_ROOM_MODE = "room_dispatch_mode"       # 'always' | 'on_demand' | 'observe': overrides AGENT_DISPATCH_MODE
KV_PC_AGENT = "pc_agent"                  # {name, worker_id, source 'report'|'observed', ts}: the PC worker
KV_ROOM_DISPATCH = "room_dispatch"        # {id, name, reason, ts}: the newest bridge dispatch (written first)
# C-116 (K-74 v5): a UI desktop_stop the node has not confirmed yet, and the latest node notice
# values, overwritten in place (never a history), plus the node_id of the last verified hello.
KV_DESKTOP_STOP = "desktop_stop"          # {ts, by} until a hello or desktop_state reports read/off
KV_NODE_VITALS = "desktop_vitals_pc"      # the last vitals_pc notice, as emitted to the UI
KV_NODE_STATE = "desktop_state"           # {mode, locked, panic, ts}
KV_NODE_POWER = "desktop_power"           # {state, ts}
KV_NODE_ID = "desktop_node_id"            # str: a different node_id raises the new-node alert
# C-125 (K-77): the live job's announce results for agent-svc ({attempt id: {delivered, reason?, ts}}, at most 1 h
# old and 500 entries), kept across a bridge restart so a delivered announce is never spoken on the laptop as well.
KV_SVC_ANNOUNCE = "svc_announce_results"
# C-60 (K-46 v51): the settings copy's revision, owned by the bridge and never an agent clock. {epoch: a random id of
# this database, made once when the kv has none and never changed after (a bridge restart and a second process keep
# it, and so does a restored backup), revision: the newest revision (int, only ever grows: each settings change takes
# the next one, and an agent echo above it raises it first, the Lamport catch-up of bridge_settings.agent_merge),
# delivered: the newest revision sent to an agent socket, keys: {key: {rev, by: 'ui'|'agent', src: the agent instance
# or ''}}}.
KV_SETTINGS_REV = "settings_revision"
SETTINGS_EPOCH_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

SEEN_DESKTOP = "desktop"  # a 24 h scope; K-70 envelope ids live in the relay's own desktop_seen.db (C-116)
SEEN_NOTIFY = "notify"    # C-NOTIFY-1 nonces
SEEN_TTL_S: dict[str, float] = {SEEN_DESKTOP: 24 * 3600.0, SEEN_NOTIFY: 300.0}

MESSAGE_SOURCES = ("voice", "text", "system")
_ROLE_ALIASES = {
    "user": "user",
    "assistant": "assistant",
    "agent": "assistant",
    "model": "assistant",
    "system": "assistant",
}
_MESSAGE_KEYS = ("id", "role", "text", "source", "final", "ts")
_LEGACY_KEYS = ("timestamp", "is_final")
_MS_EPOCH_MIN = 1e11  # larger epoch values are milliseconds (1e11 s would be the year 5138)
_BUSY_TIMEOUT_S = 10.0
_PRUNE_BATCH = 5000
_SQLITE_CORRUPT = 11
_SQLITE_NOTADB = 26
_DB_SUFFIXES = ("", "-wal", "-shm", "-journal")

_MESSAGE_SELECT = "SELECT seq, id, role, text, source, final, ts, extra FROM messages"
_SESSION_SELECT = (
    "SELECT sid, user, created_ts, expires_ts, last_seen_ts, revoked_ts, label, ip, user_agent"
    " FROM sessions"
)
_CONFIRM_SELECT = (
    "SELECT id, tool, risk, data, status, created_ts, expires_ts, decided_by, decided_ts"
    " FROM confirmations"
)

_SCHEMA = """
BEGIN;
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    role       TEXT NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    final      INTEGER NOT NULL,
    ts         REAL NOT NULL,
    extra      TEXT NOT NULL DEFAULT '{}',
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_ts ON messages (ts, seq);
CREATE TABLE IF NOT EXISTS events (
    seq  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ts   REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_kind ON events (kind, seq);
CREATE INDEX IF NOT EXISTS events_by_ts ON events (ts);
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    sid          TEXT PRIMARY KEY,
    user         TEXT NOT NULL,
    created_ts   REAL NOT NULL,
    expires_ts   REAL NOT NULL,
    last_seen_ts REAL,
    revoked_ts   REAL,
    label        TEXT NOT NULL DEFAULT '',
    ip           TEXT NOT NULL DEFAULT '',
    user_agent   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_by_action ON audit (action, seq);
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmations (
    id         TEXT PRIMARY KEY,
    tool       TEXT NOT NULL DEFAULT '',
    risk       TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_ts REAL NOT NULL,
    expires_ts REAL NOT NULL,
    decided_by TEXT,
    decided_ts REAL
);
CREATE INDEX IF NOT EXISTS confirmations_by_status ON confirmations (status, expires_ts);
CREATE TABLE IF NOT EXISTS pending_commands (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_ts REAL NOT NULL,
    expires_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_ids (
    scope      TEXT NOT NULL,
    id         TEXT NOT NULL,
    expires_ts REAL NOT NULL,
    PRIMARY KEY (scope, id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS seen_ids_by_expiry ON seen_ids (expires_ts);
CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_ts REAL NOT NULL,
    acked_ts   REAL,
    acked_by   TEXT
);
CREATE INDEX IF NOT EXISTS notifications_by_ack ON notifications (acked_ts, created_ts);
COMMIT;
"""


def bridge_db_path() -> str:
    """BRIDGE_DB_PATH, or <project>/data/bridge.db (/app/data/bridge.db in the bridge image)."""
    return (os.environ.get("BRIDGE_DB_PATH") or "").strip() or str(DEFAULT_DB_PATH)


def event_retention_days() -> float:
    """BRIDGE_EVENT_RETENTION_DAYS: days of event/log rows to keep. 0 (the default), empty or
    invalid keeps them forever, until the user approves a value (C-STATE-1)."""
    raw = (os.environ.get("BRIDGE_EVENT_RETENTION_DAYS") or "").strip()
    try:
        days = float(raw) if raw else 0.0
    except ValueError:
        logger.warning("BRIDGE_EVENT_RETENTION_DAYS=%r is not a number; events are kept forever", raw)
        return 0.0
    return days if math.isfinite(days) and days > 0 else 0.0


# C-64 review LS-2: even with BRIDGE_EVENT_RETENTION_DAYS=0 the log feed must not grow bridge.db without
# bound (init_state only ever shows the last 30 lines). EVENT_LOG rows are capped at BRIDGE_LOG_MAX_ROWS and
# EVENT_MEMORY rows at MEMORY_MAX_ROWS; tools and chat messages keep the user's retention decision.
DEFAULT_LOG_MAX_ROWS = 20_000
# K-07: the bridge keeps the newest 200 memory events (with C-52 every saved message adds one); the hourly
# retention loop deletes older ones. init_state.memories shows the last INIT_MEMORIES of them.
MEMORY_MAX_ROWS = 200


def log_max_rows() -> int:
    """BRIDGE_LOG_MAX_ROWS: the most EVENT_LOG rows kept (the hourly retention loop deletes the oldest beyond
    it). Default DEFAULT_LOG_MAX_ROWS; 0 removes the cap; an invalid value keeps the default."""
    raw = (os.environ.get("BRIDGE_LOG_MAX_ROWS") or "").strip()
    if not raw:
        return DEFAULT_LOG_MAX_ROWS
    try:
        rows = int(float(raw))
    except ValueError:
        logger.warning("BRIDGE_LOG_MAX_ROWS=%r is not a number; the default %d is used", raw,
                       DEFAULT_LOG_MAX_ROWS)
        return DEFAULT_LOG_MAX_ROWS
    return rows if rows > 0 else 0


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    return default


def _epoch_seconds(value: Any) -> float | None:
    """Epoch seconds from seconds or milliseconds (number or numeric string); None if unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
        except OverflowError:  # an int no float can hold (JSON numbers have no limit)
            return None
    elif isinstance(value, str):
        try:
            seconds = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds / 1000.0 if seconds > _MS_EPOCH_MIN else seconds


def epoch_seconds(value: Any) -> float | None:
    """A client-sent time as epoch seconds (seconds or milliseconds, a number or numeric string); None if unusable."""
    return _epoch_seconds(value)


def _message_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The C-MSG-1 fields the payload actually carries, normalized; other keys go to 'extra'."""
    fields: dict[str, Any] = {}
    msg_id = payload.get("id")
    if msg_id not in (None, ""):
        fields["id"] = str(msg_id)
    role = payload.get("role")
    if role:
        fields["role"] = _ROLE_ALIASES.get(str(role).strip().lower(), "assistant")
    if "text" in payload:
        fields["text"] = "" if payload["text"] is None else str(payload["text"])
    source = str(payload.get("source") or "").strip().lower()
    if source in MESSAGE_SOURCES:
        fields["source"] = source
    for key in ("final", "is_final"):
        if key in payload:
            fields["final"] = _as_bool(payload[key], True)
            break
    for key in ("ts", "timestamp"):
        seconds = _epoch_seconds(payload.get(key))
        if seconds is not None:
            fields["ts"] = seconds
            break
    extra = {k: v for k, v in payload.items() if k not in _MESSAGE_KEYS and k not in _LEGACY_KEYS}
    if extra:
        fields["extra"] = extra
    return fields


def normalize_message(payload: Mapping[str, Any], default_source: str = "voice", *,
                      now: float | None = None) -> dict[str, Any]:
    """A C-MSG-1 message from an agent transcription payload, legacy fields included.

    role 'agent' becomes 'assistant'; final comes from final or is_final (default True); ts from
    ts or timestamp, in seconds or milliseconds (default now); text is a str; source is the
    payload's ('voice'|'text'|'system'), else 'system' for the legacy role 'system', else
    default_source. The id is the payload's, or a new uuid4. Other fields (turn_id, text_native,
    score, persona, ...) are kept as they are.
    """
    fields = _message_fields(payload)
    extra = fields.pop("extra", {})
    if "source" not in fields and str(payload.get("role") or "").strip().lower() == "system":
        fields["source"] = "system"
    default = str(default_source or "").strip().lower()
    msg: dict[str, Any] = {
        "id": fields.get("id") or str(uuid.uuid4()),
        "role": fields.get("role", "assistant"),
        "text": fields.get("text", ""),
        "source": fields.get("source") or (default if default in MESSAGE_SOURCES else "voice"),
        "final": fields.get("final", True),
        "ts": fields.get("ts") or (time.time() if now is None else now),
    }
    msg.update(extra)
    return msg


def ui_message(msg: Mapping[str, Any]) -> dict[str, Any]:
    """The message as UIs receive it: C-MSG-1 plus the legacy aliases timestamp (= ts, seconds)
    and is_final (= final) that the current dashboard reads."""
    out = dict(msg)
    out["timestamp"] = out.get("ts")
    out["is_final"] = out.get("final")
    return out


def message_cursor(ts: float, seq: int) -> str:
    """next_cursor of a history page: '<ts>:<seq>' of its oldest message. It names that message
    exactly, so the next page never skips older messages that share its ts."""
    return f"{ts!r}:{seq}"


def parse_message_cursor(cursor: str) -> tuple[float, int | None]:
    """(ts, seq) from a next_cursor; a bare epoch in s or ms (like `before`) gives seq None.
    Raises ValueError for anything else."""
    ts_text, sep, seq_text = str(cursor).strip().partition(":")
    ts = float(ts_text)
    if not math.isfinite(ts):
        raise ValueError(f"not a message cursor: {cursor!r}")
    return ts, int(seq_text) if sep else None


# vortex_log_batch (K-40 v10, C-64)
LOG_BATCH_MAX = 50
LOG_TEXT_MAX = 2000
LOG_CATEGORY_MAX = 40
LOG_DROPPED_MAX = 1_000_000_000
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_SOURCES = ("agent", "svc", "desktop")


# C-64 review SP-2: control characters (newlines, ANSI escapes) collapse to one space before storage, the way
# jarvis_audit._CONTROL_RUN and ui_bridge._printable treat their text, so a crafted multi-line log message can
# never render as extra, fake entries in the UI log panel or pollute bridge.db.
_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")


def _log_item(raw: Mapping[str, Any], source: str, now: float) -> dict[str, Any]:
    text = raw.get("text")
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    text = _CONTROL_RUN.sub(" ", text)
    category = raw.get("category")
    category = _CONTROL_RUN.sub(" ", category).strip()[:LOG_CATEGORY_MAX] if isinstance(category, str) else ""
    level = raw.get("level")
    level = level.strip().upper() if isinstance(level, str) else ""
    ts = _epoch_seconds(raw.get("ts"))
    if ts is None:
        ts = now
    item: dict[str, Any] = {"text": text[:LOG_TEXT_MAX], "category": category or "SYSTEM"}
    if level in LOG_LEVELS:
        item["level"] = level
    item.update(ts=ts, timestamp=ts, source=source)
    return item


def normalize_log_batch(payload: Mapping[str, Any], now: float) -> tuple[list[dict[str, Any]], int, str]:
    """
    A vortex_log_batch payload as the bridge keeps and emits it (K-40 v10): (items, dropped, source). source is
    'agent', 'svc' or 'desktop' (default 'agent'). Each of the first LOG_BATCH_MAX object items becomes {text (cut at
    LOG_TEXT_MAX), category (at most LOG_CATEGORY_MAX chars, default 'SYSTEM'), level (DEBUG..CRITICAL, else left
    out), ts (epoch s; ms accepted; else now), timestamp (= ts), source}; other fields are dropped. dropped is the
    payload's count (a whole number >= 0) plus the items that were not objects or came after the cap.
    """
    source = payload.get("source")
    source = source if isinstance(source, str) and source in LOG_SOURCES else "agent"
    raw_dropped = payload.get("dropped")
    dropped = 0
    if isinstance(raw_dropped, int) and not isinstance(raw_dropped, bool):
        dropped = min(max(raw_dropped, 0), LOG_DROPPED_MAX)  # an int of any size (JSON has no limit)
    elif isinstance(raw_dropped, float) and math.isfinite(raw_dropped):
        dropped = min(max(int(raw_dropped), 0), LOG_DROPPED_MAX)
    raw_items = payload.get("items")
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
        if index >= LOG_BATCH_MAX or not isinstance(raw, Mapping):
            dropped += 1
            continue
        items.append(_log_item(raw, source, now))
    return items, min(dropped, LOG_DROPPED_MAX), source


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(text: str | None, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _deep_merge(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        old = merged.get(key)
        if isinstance(old, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(old, value)
        else:
            merged[key] = value
    return merged


def _new_settings_meta() -> dict[str, Any]:
    """A database's revision record (C-60), made once: a fresh epoch, so an agent never compares its revisions with
    another database's (a corrupt bridge.db renamed aside, the PC's local bridge)."""
    return {"epoch": secrets.token_hex(8), "revision": 0, "delivered": 0, "keys": {}}


def _count(value: Any) -> int | None:
    """A non-negative int (never a bool), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _clean_settings_meta(raw: Any) -> dict[str, Any] | None:
    """The stored revision meta (KV_SETTINGS_REV) as the code reads it, or None when it is not usable (missing,
    hand-edited, no epoch or revision): then a new record with a new epoch is made, so a revision is never reused
    within one epoch."""
    if not isinstance(raw, Mapping):
        return None
    epoch, revision = raw.get("epoch"), _count(raw.get("revision"))
    if not (isinstance(epoch, str) and SETTINGS_EPOCH_RE.fullmatch(epoch)) or revision is None:
        return None
    keys: dict[str, dict[str, Any]] = {}
    raw_keys = raw.get("keys")
    for key, entry in (raw_keys.items() if isinstance(raw_keys, Mapping) else ()):
        if not isinstance(key, str) or not isinstance(entry, Mapping):
            continue
        rev, by, src = _count(entry.get("rev")), entry.get("by"), entry.get("src", "")
        if rev is None or by not in ("ui", "agent"):
            continue
        keys[key] = {"rev": min(rev, revision), "by": by, "src": src[:64] if isinstance(src, str) else ""}
    delivered = _count(raw.get("delivered")) or 0
    return {"epoch": epoch, "revision": revision, "delivered": min(delivered, revision), "keys": keys}


def _extra_of(msg: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in msg.items() if k not in _MESSAGE_KEYS}


def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": row["id"],
        "role": row["role"],
        "text": row["text"],
        "source": row["source"],
        "final": bool(row["final"]),
        "ts": row["ts"],
    }
    extra = _loads(row["extra"], {})
    if isinstance(extra, dict):
        for key, value in extra.items():
            msg.setdefault(key, value)
    return msg


def _confirmation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tool": row["tool"],
        "risk": row["risk"],
        "status": row["status"],
        "created_ts": row["created_ts"],
        "expires_ts": row["expires_ts"],
        "decided_by": row["decided_by"],
        "decided_ts": row["decided_ts"],
        "payload": _loads(row["data"], {}),
    }


def _session_from_row(row: sqlite3.Row, now: float) -> dict[str, Any]:
    record = dict(row)
    record["active"] = record["revoked_ts"] is None and record["expires_ts"] > now
    return record


class _CorruptDatabase(Exception):
    """PRAGMA quick_check found damage."""


def _is_corruption(exc: BaseException) -> bool:
    """True only for real damage (SQLITE_CORRUPT, SQLITE_NOTADB), never for 'database is locked',
    a full disk or a permission problem, which must not get a good file renamed aside."""
    if isinstance(exc, _CorruptDatabase):
        return True
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in (_SQLITE_CORRUPT, _SQLITE_NOTADB)
    text = str(exc).lower()
    return "malformed" in text or "not a database" in text


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    conn.execute("COMMIT")


def _collect_stats(conn: sqlite3.Connection, path: str) -> dict[str, Any]:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name")]
    counts = {name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
              for name in tables}
    roles: dict[str, int] = {}
    if "messages" in tables:
        roles = {str(role): int(count) for role, count in conn.execute(
            "SELECT role, COUNT(*) FROM messages GROUP BY role ORDER BY role")}
    version = None
    if "schema_version" in tables:
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    # The WAL holds the newest pages until a checkpoint, so it counts towards the size.
    size = sum(os.path.getsize(path + suffix) for suffix in ("", "-wal")
               if os.path.exists(path + suffix))
    return {
        "path": path,
        "size_bytes": size,
        "schema_version": version,
        "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
        "tables": counts,
        "message_roles": roles,
    }


class BridgeStore:  # pylint: disable=too-many-public-methods  # one flat facade over all tables
    """The bridge's SQLite database (C-STATE-1).

    The async methods are for the bridge's event loop: each one runs its SQLite work in a worker
    thread under the store lock. The file is opened on first use, or earlier with open(). With
    repair=False (a second process, such as an admin CLI) a corrupt file raises instead of being
    renamed aside, because only the bridge itself may replace its database.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None, *,
                 clock: Callable[[], float] = time.time, repair: bool = True) -> None:
        self.path = os.fspath(path) if path is not None else bridge_db_path()
        self._clock = clock
        self._repair = repair
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        # Corrupt databases this object renamed aside: the Desktop Link relay then takes a lost switch as off (C-116).
        self.corrupt_renamed = 0

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        """Opens (creates, migrates, checks) the database now instead of on first use."""
        await asyncio.to_thread(self._open_now)

    async def close(self) -> None:
        await asyncio.to_thread(self._close_now)

    def _open_now(self) -> None:
        with self._lock:
            self._connection()

    def _close_now(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    async def _run(self, fn: Callable[Concatenate[sqlite3.Connection, P], T],
                   *args: P.args, **kwargs: P.kwargs) -> T:
        return await asyncio.to_thread(self._call, fn, *args, **kwargs)

    def _call(self, fn: Callable[Concatenate[sqlite3.Connection, P], T],
              *args: P.args, **kwargs: P.kwargs) -> T:
        with self._lock:
            conn = self._connection()
            try:
                return fn(conn, *args, **kwargs)
            except sqlite3.DatabaseError as exc:
                if not (self._repair and _is_corruption(exc)):
                    raise
                self._discard_corrupt(f"{exc} (during {getattr(fn, '__name__', 'a query')})")
                return fn(self._connection(), *args, **kwargs)

    def _connection(self) -> sqlite3.Connection:
        """The open connection; the caller holds self._lock."""
        if self._conn is None:
            try:
                self._conn = self._connect()
            except (sqlite3.DatabaseError, _CorruptDatabase) as exc:
                if not (self._repair and _is_corruption(exc)):
                    raise
                self._discard_corrupt(str(exc))
                self._conn = self._connect()
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        created = not os.path.exists(self.path)
        conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_S, isolation_level=None,
                               check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            if created:
                # Chat history and session ids: owner-only. SQLite gives -wal/-shm the same mode.
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            if mode != "wal":
                logger.warning("Bridge DB %s runs in journal mode %s, not WAL", self.path, mode)
            conn.execute("PRAGMA synchronous=NORMAL")
            if not created:
                problems = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
                if problems != ["ok"]:
                    raise _CorruptDatabase("quick_check: " + "; ".join(problems[:3]))
            self._migrate(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)
        current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        if current < SCHEMA_VERSION:
            conn.execute("INSERT INTO schema_version (version, applied_ts) VALUES (?, ?)",
                         (SCHEMA_VERSION, self._clock()))
        elif current > SCHEMA_VERSION:
            logger.warning("Bridge DB %s has schema v%s, newer than this code (v%s); continuing "
                           "with the tables this code knows", self.path, current, SCHEMA_VERSION)

    def _discard_corrupt(self, reason: str) -> None:
        """Closes the connection and renames the damaged files aside; they are never deleted."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(self._clock()))
        base = f"{self.path}.corrupt-{stamp}"
        target, n = base, 1
        while any(os.path.exists(target + suffix) for suffix in _DB_SUFFIXES):
            target, n = f"{base}-{n}", n + 1
        moved = []
        for suffix in _DB_SUFFIXES:
            if os.path.exists(self.path + suffix):
                os.rename(self.path + suffix, target + suffix)
                moved.append(os.path.basename(target + suffix))
        self.corrupt_renamed += 1
        logger.critical("Bridge DB %s is corrupt (%s): renamed to %s (kept, never deleted); "
                        "started a fresh database", self.path, reason, ", ".join(moved) or "-")

    # ------------------------------------------------------------------ messages (C-MSG-1)

    async def upsert_message(self, payload: Mapping[str, Any],
                             default_source: str = "voice") -> tuple[str, dict[str, Any]]:
        """Stores a transcription by id: ('new', msg) the first time, then ('updated', msg) with
        every field the payload carries merged in (K-58). msg is the emitted form (ui_message)."""
        return await self._run(self._upsert_message, payload, default_source)

    async def get_message(self, msg_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_message, msg_id)

    async def list_messages(self, limit: int = MESSAGE_PAGE_MAX,
                            before: float | None = None) -> tuple[list[dict[str, Any]], bool]:
        """Up to limit (1..100) messages with ts < before (all when None), ascending by ts, and
        whether older ones exist. Messages are never pruned automatically."""
        return await self._run(self._list_messages, limit, before)

    async def message_page(self, limit: int = MESSAGE_PAGE_MAX, before: float | None = None,
                           before_seq: int | None = None) -> dict[str, Any]:
        """One history page: {messages (ascending), has_more, next_cursor (None on the last
        page)}. Without before_seq it holds messages with ts < before, as list_messages; with
        it, the messages before (before, before_seq) in (ts, seq) order, the position a
        next_cursor names, so messages that share a ts are never skipped."""
        return await self._run(self._message_page, limit, before, before_seq)

    def _upsert_message(self, conn: sqlite3.Connection, payload: Mapping[str, Any],
                        default_source: str) -> tuple[str, dict[str, Any]]:
        now = self._clock()
        fields = _message_fields(payload)
        msg_id = fields.pop("id", None) or str(uuid.uuid4())
        with _transaction(conn):
            row = conn.execute(f"{_MESSAGE_SELECT} WHERE id = ?", (msg_id,)).fetchone()
            if row is None:
                msg = normalize_message({**payload, "id": msg_id}, default_source, now=now)
                conn.execute(
                    "INSERT INTO messages (id, role, text, source, final, ts, extra, created_ts,"
                    " updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (msg_id, msg["role"], msg["text"], msg["source"], int(msg["final"]),
                     msg["ts"], _dumps(_extra_of(msg)), now, now))
                status = "new"
            else:
                msg = _message_from_row(row)
                msg.update(fields.pop("extra", {}))
                msg.update(fields)
                conn.execute(
                    "UPDATE messages SET role = ?, text = ?, source = ?, final = ?, ts = ?,"
                    " extra = ?, updated_ts = ? WHERE id = ?",
                    (msg["role"], msg["text"], msg["source"], int(msg["final"]), msg["ts"],
                     _dumps(_extra_of(msg)), now, msg_id))
                status = "updated"
        return status, ui_message(msg)

    def _get_message(self, conn: sqlite3.Connection, msg_id: str) -> dict[str, Any] | None:
        row = conn.execute(f"{_MESSAGE_SELECT} WHERE id = ?", (str(msg_id),)).fetchone()
        return None if row is None else ui_message(_message_from_row(row))

    def _list_messages(self, conn: sqlite3.Connection, limit: int,
                       before: float | None) -> tuple[list[dict[str, Any]], bool]:
        page = self._message_page(conn, limit, before, None)
        return page["messages"], page["has_more"]

    def _message_page(self, conn: sqlite3.Connection, limit: int, before: float | None,
                      before_seq: int | None) -> dict[str, Any]:
        limit = max(1, min(int(limit), MESSAGE_PAGE_MAX))
        where = ""
        params: list[Any] = []
        if before is not None:
            cutoff = float(before)
            if cutoff > _MS_EPOCH_MIN:
                cutoff /= 1000.0
            if before_seq is None:
                where, params = " WHERE ts < ?", [cutoff]
            else:
                where, params = " WHERE (ts, seq) < (?, ?)", [cutoff, int(before_seq)]
        rows = conn.execute(f"{_MESSAGE_SELECT}{where} ORDER BY ts DESC, seq DESC LIMIT ?",
                            (*params, limit + 1)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "messages": [ui_message(_message_from_row(row)) for row in reversed(rows)],
            "has_more": has_more,
            "next_cursor": message_cursor(rows[-1]["ts"], rows[-1]["seq"]) if has_more else None,
        }

    # ------------------------------------------------------------------ events (logs, memories, tools)

    async def append_event(self, kind: str, data: Any) -> int:
        """Stores one UI event (EVENT_LOG, EVENT_MEMORY, EVENT_TOOL) exactly as received."""
        return await self._run(self._append_event, kind, data)

    async def append_events(self, kind: str, items: Sequence[Any]) -> int:
        """Stores several UI events of one kind in one transaction, in order (a vortex_log_batch's items, C-64).
        Returns how many were stored."""
        if not items:
            return 0
        return await self._run(self._append_events, kind, list(items))

    async def recent_events(self, kind: str, limit: int = INIT_LOGS) -> list[Any]:
        """The newest `limit` events of a kind, oldest first."""
        return await self._run(self._recent_events, kind, limit)

    async def delete_events(self, kind: str) -> int:
        return await self._run(self._delete_events, kind)

    async def prune_events(self, retention_days: float) -> int:
        """Deletes event rows older than retention_days (<= 0 keeps everything), in batches so
        other calls are never held up for long. Returns the number of rows deleted."""
        if not math.isfinite(retention_days) or retention_days <= 0:
            return 0
        cutoff = self._clock() - retention_days * 86400.0
        total = 0
        while True:
            deleted = await self._run(self._prune_events_batch, cutoff)
            total += deleted
            if deleted < _PRUNE_BATCH:
                return total

    async def cap_events(self, kind: str, keep: int) -> int:
        """Deletes the oldest rows of one kind beyond the newest keep (<= 0 keeps everything), in batches
        (C-64 review LS-2: EVENT_LOG stays bounded even with retention 0). Returns the rows deleted."""
        if keep <= 0:
            return 0
        total = 0
        while True:
            deleted = await self._run(self._cap_events_batch, kind, int(keep))
            total += deleted
            if deleted < _PRUNE_BATCH:
                return total

    def _append_event(self, conn: sqlite3.Connection, kind: str, data: Any) -> int:
        cur = conn.execute("INSERT INTO events (kind, ts, data) VALUES (?, ?, ?)",
                           (str(kind), self._clock(), _dumps(data)))
        return int(cur.lastrowid or 0)

    def _append_events(self, conn: sqlite3.Connection, kind: str, items: list[Any]) -> int:
        now = self._clock()
        with _transaction(conn):
            conn.executemany("INSERT INTO events (kind, ts, data) VALUES (?, ?, ?)",
                             [(str(kind), now, _dumps(item)) for item in items])
        return len(items)

    def _recent_events(self, conn: sqlite3.Connection, kind: str, limit: int) -> list[Any]:
        rows = conn.execute("SELECT data FROM events WHERE kind = ? ORDER BY seq DESC LIMIT ?",
                            (str(kind), max(0, int(limit)))).fetchall()
        return [_loads(row["data"]) for row in reversed(rows)]

    def _delete_events(self, conn: sqlite3.Connection, kind: str) -> int:
        return int(conn.execute("DELETE FROM events WHERE kind = ?", (str(kind),)).rowcount)

    def _prune_events_batch(self, conn: sqlite3.Connection, cutoff: float) -> int:
        return int(conn.execute(
            "DELETE FROM events WHERE seq IN (SELECT seq FROM events WHERE ts < ? LIMIT ?)",
            (cutoff, _PRUNE_BATCH)).rowcount)

    def _cap_events_batch(self, conn: sqlite3.Connection, kind: str, keep: int) -> int:
        # The cutoff is the oldest seq among the newest `keep` rows of the kind; everything of that kind below
        # it goes, _PRUNE_BATCH rows at a time. Fewer rows than keep: the subselect is empty and nothing goes.
        return int(conn.execute(
            "DELETE FROM events WHERE seq IN (SELECT seq FROM events WHERE kind = ? AND seq < ("
            " SELECT MIN(seq) FROM (SELECT seq FROM events WHERE kind = ? ORDER BY seq DESC LIMIT ?)"
            ") LIMIT ?)", (str(kind), str(kind), keep, _PRUNE_BATCH)).rowcount)

    # ------------------------------------------------------------------ settings (C-SETTINGS-1)

    async def get_settings(self) -> dict[str, Any]:
        """The stored settings object ({} until something is stored)."""
        return await self._run(self._get_settings)

    async def merge_settings(self, partial: Mapping[str, Any], by: str) -> dict[str, Any]:
        """Merges a partial settings object (nested objects merge key by key), records who
        changed each key ('ui' | 'agent') and when, and returns the full stored object."""
        return await self._run(self._merge_settings, partial, by)

    def _get_settings(self, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        return {row["key"]: _loads(row["value"]) for row in rows}

    def _merge_settings(self, conn: sqlite3.Connection, partial: Mapping[str, Any],
                        by: str) -> dict[str, Any]:
        now = self._clock()
        with _transaction(conn):
            current = self._get_settings(conn)
            for raw_key, value in partial.items():
                key = str(raw_key)
                old = current.get(key)
                if isinstance(old, Mapping) and isinstance(value, Mapping):
                    value = _deep_merge(old, value)
                conn.execute(
                    "INSERT INTO settings (key, value, updated_by, updated_ts) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                    " updated_by = excluded.updated_by, updated_ts = excluded.updated_ts",
                    (key, _dumps(value), str(by), now))
                current[key] = value
        return current

    async def settings_with_meta(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """(the stored settings object, its revision meta) in one read (C-60). The meta is made the first time (a new
        epoch, revision 0) and its epoch never changes after: a bridge restart, a second process's store and a backup
        restored over the file keep it. Only a database without a usable record (a fresh or replaced file, a
        hand-edited record) gets a new epoch."""
        return await self._run(self._settings_with_meta)

    async def transact_settings(
            self, fn: Callable[[dict[str, Any], dict[str, Any]], tuple[Mapping[str, Any], Mapping[str, Any], str, T]],
    ) -> tuple[dict[str, Any], dict[str, Any], T]:
        """C-60: one settings change as a single transaction. fn(current settings, current meta) returns (writes
        {key: full new value}, the new meta, updated_by, result); the writes are stored as given (fn merges), the
        meta replaces the stored one. The new meta keeps the epoch and never lowers the revision (ValueError, nothing
        stored); it may raise the revision past every revision it hands out, which is how an agent echo above the
        bridge's newest revision catches the counter up in the same transaction (bridge_settings.agent_merge).
        Returns (the stored settings after it, the meta after it, result)."""
        return await self._run(self._transact_settings, fn)

    async def note_settings_delivered(self, revision: int) -> None:
        """C-60: the newest settings revision sent to an agent socket (meta 'delivered' only grows)."""
        await self._run(self._note_settings_delivered, int(revision))

    def _settings_meta(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """The revision meta; a missing or unusable one is replaced by a new record (a new epoch), written at once."""
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (KV_SETTINGS_REV,)).fetchone()
        meta = _clean_settings_meta(_loads(row["value"]) if row is not None else None)
        if meta is None:
            if row is not None:
                logger.warning("Bridge DB: the settings revision record is not usable; a new one starts (a new epoch)")
            meta = _new_settings_meta()
            conn.execute("INSERT INTO kv (key, value, updated_ts) VALUES (?, ?, ?) ON CONFLICT(key)"
                         " DO UPDATE SET value = excluded.value, updated_ts = excluded.updated_ts",
                         (KV_SETTINGS_REV, _dumps(meta), self._clock()))
        return meta

    def _settings_with_meta(self, conn: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any]]:
        with _transaction(conn):
            return self._get_settings(conn), self._settings_meta(conn)

    def _transact_settings(self, conn: sqlite3.Connection, fn: Callable[..., Any]) -> tuple[Any, Any, Any]:
        now = self._clock()
        with _transaction(conn):
            current = self._get_settings(conn)
            meta = self._settings_meta(conn)
            writes, new_meta, by, result = fn(dict(current), meta)
            clean = _clean_settings_meta(new_meta)
            if clean is None or clean["epoch"] != meta["epoch"] or clean["revision"] < meta["revision"]:
                raise ValueError("a settings change keeps the epoch and never lowers the revision")
            for key, value in writes.items():
                conn.execute(
                    "INSERT INTO settings (key, value, updated_by, updated_ts) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                    " updated_by = excluded.updated_by, updated_ts = excluded.updated_ts",
                    (str(key), _dumps(value), str(by), now))
                current[str(key)] = value
            conn.execute("UPDATE kv SET value = ?, updated_ts = ? WHERE key = ?",
                         (_dumps(clean), now, KV_SETTINGS_REV))
        return current, clean, result

    def _note_settings_delivered(self, conn: sqlite3.Connection, revision: int) -> None:
        with _transaction(conn):
            meta = self._settings_meta(conn)
            delivered = min(max(meta["delivered"], revision), meta["revision"])
            if delivered != meta["delivered"]:
                meta["delivered"] = delivered
                conn.execute("UPDATE kv SET value = ?, updated_ts = ? WHERE key = ?",
                             (_dumps(meta), self._clock(), KV_SETTINGS_REV))

    # ------------------------------------------------------------------ kv

    async def kv_get(self, key: str, default: Any = None) -> Any:
        return await self._run(self._kv_get, key, default)

    async def kv_get_many(self, keys: Iterable[str]) -> dict[str, Any]:
        """The stored values of the given keys; keys never stored are left out."""
        return await self._run(self._kv_get_many, list(keys))

    async def kv_set(self, key: str, value: Any) -> None:
        await self._run(self._kv_set_many, {key: value})

    async def kv_set_many(self, items: Mapping[str, Any]) -> None:
        """Stores several keys in one transaction."""
        await self._run(self._kv_set_many, dict(items))

    async def kv_delete(self, key: str) -> bool:
        return await self._run(self._kv_delete, key)

    def _kv_get(self, conn: sqlite3.Connection, key: str, default: Any) -> Any:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (str(key),)).fetchone()
        return default if row is None else _loads(row["value"], default)

    def _kv_get_many(self, conn: sqlite3.Connection, keys: list[str]) -> dict[str, Any]:
        wanted = list(dict.fromkeys(str(key) for key in keys))
        if not wanted:
            return {}
        marks = ", ".join("?" * len(wanted))
        rows = conn.execute(f"SELECT key, value FROM kv WHERE key IN ({marks})", wanted).fetchall()
        return {row["key"]: _loads(row["value"]) for row in rows}

    def _kv_set_many(self, conn: sqlite3.Connection, items: dict[str, Any]) -> None:
        now = self._clock()
        rows = [(str(key), _dumps(value), now) for key, value in items.items()]
        if not rows:
            return
        with _transaction(conn):
            conn.executemany(
                "INSERT INTO kv (key, value, updated_ts) VALUES (?, ?, ?) ON CONFLICT(key)"
                " DO UPDATE SET value = excluded.value, updated_ts = excluded.updated_ts", rows)

    def _kv_delete(self, conn: sqlite3.Connection, key: str) -> bool:
        return conn.execute("DELETE FROM kv WHERE key = ?", (str(key),)).rowcount > 0

    # ------------------------------------------------------------------ audit

    async def audit(self, actor: str, action: str, detail: Any = "", ip: str = "") -> int:
        """Appends one audit row; a non-string detail is stored as JSON. Audit rows are not
        touched by the event retention."""
        return await self._run(self._audit, actor, action, detail, ip)

    async def recent_audit(self, limit: int = 50, action_prefix: str = "") -> list[dict[str, Any]]:
        """The newest audit rows first, optionally only actions starting with action_prefix."""
        return await self._run(self._recent_audit, limit, action_prefix)

    def _audit(self, conn: sqlite3.Connection, actor: str, action: str, detail: Any,
               ip: str) -> int:
        text = detail if isinstance(detail, str) else _dumps(detail)
        cur = conn.execute("INSERT INTO audit (ts, actor, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
                           (self._clock(), str(actor), str(action), text, str(ip or "")))
        return int(cur.lastrowid or 0)

    def _recent_audit(self, conn: sqlite3.Connection, limit: int,
                      action_prefix: str) -> list[dict[str, Any]]:
        sql = "SELECT seq, ts, actor, action, detail, ip FROM audit"
        params: list[Any] = []
        if action_prefix:
            escaped = action_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " WHERE action LIKE ? ESCAPE '\\'"
            params.append(escaped + "%")
        params.append(max(0, int(limit)))
        rows = conn.execute(sql + " ORDER BY seq DESC LIMIT ?", params).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ sessions (C-AUTH-1)

    async def create_session(self, ttl_s: float, *, sid: str | None = None, user: str = "owner",
                             label: str = "", ip: str = "", user_agent: str = "") -> dict[str, Any]:
        """A new owner session (sid: 32 random hex chars unless given) valid for ttl_s."""
        return await self._run(self._create_session, ttl_s, sid=sid, user=user, label=label, ip=ip,
                               user_agent=user_agent)

    async def get_session(self, sid: str) -> dict[str, Any] | None:
        """The session row with 'active' (not revoked, not expired), or None."""
        return await self._run(self._get_session, sid)

    async def touch_session(self, sid: str) -> bool:
        """Records activity on an active session; False when it is unknown, revoked or expired."""
        return await self._run(self._touch_session, sid)

    async def revoke_session(self, sid: str) -> bool:
        return await self._run(self._revoke_session, sid)

    async def revoke_all_sessions(self) -> int:
        return await self._run(self._revoke_all_sessions)

    async def list_sessions(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        return await self._run(self._list_sessions, include_inactive)

    def _create_session(self, conn: sqlite3.Connection, ttl_s: float, *, sid: str | None,
                        user: str, label: str, ip: str, user_agent: str) -> dict[str, Any]:
        now = self._clock()
        record: dict[str, Any] = {
            "sid": sid or secrets.token_hex(16),
            "user": str(user),
            "created_ts": now,
            "expires_ts": now + float(ttl_s),
            "last_seen_ts": now,
            "revoked_ts": None,
            "label": str(label),
            "ip": str(ip),
            "user_agent": str(user_agent),
        }
        conn.execute(
            "INSERT INTO sessions (sid, user, created_ts, expires_ts, last_seen_ts, revoked_ts,"
            " label, ip, user_agent) VALUES (:sid, :user, :created_ts, :expires_ts,"
            " :last_seen_ts, :revoked_ts, :label, :ip, :user_agent)", record)
        record["active"] = float(ttl_s) > 0
        return record

    def _get_session(self, conn: sqlite3.Connection, sid: str) -> dict[str, Any] | None:
        row = conn.execute(f"{_SESSION_SELECT} WHERE sid = ?", (str(sid),)).fetchone()
        return None if row is None else _session_from_row(row, self._clock())

    def _touch_session(self, conn: sqlite3.Connection, sid: str) -> bool:
        now = self._clock()
        return conn.execute(
            "UPDATE sessions SET last_seen_ts = ? WHERE sid = ? AND revoked_ts IS NULL"
            " AND expires_ts > ?", (now, str(sid), now)).rowcount > 0

    def _revoke_session(self, conn: sqlite3.Connection, sid: str) -> bool:
        return conn.execute("UPDATE sessions SET revoked_ts = ? WHERE sid = ? AND revoked_ts IS NULL",
                            (self._clock(), str(sid))).rowcount > 0

    def _revoke_all_sessions(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("UPDATE sessions SET revoked_ts = ? WHERE revoked_ts IS NULL",
                                (self._clock(),)).rowcount)

    def _list_sessions(self, conn: sqlite3.Connection,
                       include_inactive: bool) -> list[dict[str, Any]]:
        now = self._clock()
        if include_inactive:
            rows = conn.execute(f"{_SESSION_SELECT} ORDER BY created_ts, rowid").fetchall()
        else:
            rows = conn.execute(f"{_SESSION_SELECT} WHERE revoked_ts IS NULL AND expires_ts > ?"
                                " ORDER BY created_ts, rowid", (now,)).fetchall()
        return [_session_from_row(row, now) for row in rows]

    # ------------------------------------------------------------------ confirmations (C-CONFIRM-1)

    async def add_confirmation(self, payload: Mapping[str, Any],
                               ttl_s: float = 60.0) -> dict[str, Any]:
        """Stores a confirm_request as pending until its expires_ts (or expires_at; default
        now + ttl_s). A repeated id keeps the first record. Returns the record, whose 'payload'
        is the request as received."""
        return await self._run(self._add_confirmation, payload, ttl_s)

    async def get_confirmation(self, confirm_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_confirmation, confirm_id)

    async def list_confirmations(self, pending_only: bool = True) -> list[dict[str, Any]]:
        """Records oldest first; by default only the pending ones that have not expired."""
        return await self._run(self._list_confirmations, pending_only)

    async def resolve_confirmation(self, confirm_id: str, allow: bool,
                                   by: str) -> dict[str, Any] | None:
        """Decides a pending, unexpired confirmation ('allowed' | 'denied'); None otherwise."""
        return await self._run(self._resolve_confirmation, confirm_id, allow, by)

    async def expire_confirmations(self) -> int:
        """Marks pending confirmations past their expiry as 'expired'; returns how many."""
        return await self._run(self._expire_confirmations)

    def _add_confirmation(self, conn: sqlite3.Connection, payload: Mapping[str, Any],
                          ttl_s: float) -> dict[str, Any]:
        now = self._clock()
        data = dict(payload)
        data["id"] = str(data.get("id") or uuid.uuid4())
        expires = _epoch_seconds(data.get("expires_ts")) or _epoch_seconds(data.get("expires_at"))
        conn.execute(
            "INSERT OR IGNORE INTO confirmations (id, tool, risk, data, status, created_ts,"
            " expires_ts) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (data["id"], str(data.get("tool") or ""), str(data.get("risk") or ""), _dumps(data),
             now, expires or now + float(ttl_s)))
        row = conn.execute(f"{_CONFIRM_SELECT} WHERE id = ?", (data["id"],)).fetchone()
        return _confirmation_from_row(row)

    def _get_confirmation(self, conn: sqlite3.Connection,
                          confirm_id: str) -> dict[str, Any] | None:
        row = conn.execute(f"{_CONFIRM_SELECT} WHERE id = ?", (str(confirm_id),)).fetchone()
        return None if row is None else _confirmation_from_row(row)

    def _list_confirmations(self, conn: sqlite3.Connection,
                            pending_only: bool) -> list[dict[str, Any]]:
        if pending_only:
            rows = conn.execute(f"{_CONFIRM_SELECT} WHERE status = 'pending' AND expires_ts > ?"
                                " ORDER BY created_ts, rowid", (self._clock(),)).fetchall()
        else:
            rows = conn.execute(f"{_CONFIRM_SELECT} ORDER BY created_ts, rowid").fetchall()
        return [_confirmation_from_row(row) for row in rows]

    def _resolve_confirmation(self, conn: sqlite3.Connection, confirm_id: str, allow: bool,
                              by: str) -> dict[str, Any] | None:
        now = self._clock()
        updated = conn.execute(
            "UPDATE confirmations SET status = ?, decided_by = ?, decided_ts = ?"
            " WHERE id = ? AND status = 'pending' AND expires_ts > ?",
            ("allowed" if allow else "denied", str(by), now, str(confirm_id), now)).rowcount
        return self._get_confirmation(conn, confirm_id) if updated else None

    def _expire_confirmations(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute(
            "UPDATE confirmations SET status = 'expired' WHERE status = 'pending'"
            " AND expires_ts <= ?", (self._clock(),)).rowcount)

    # ------------------------------------------------------------------ offline command queue

    async def push_pending(self, cmd_type: str, payload: Any, ttl_s: float = 600.0) -> int:
        """Queues a UI command for an offline agent (kept ttl_s, default 10 min)."""
        return await self._run(self._push_pending, cmd_type, payload, ttl_s)

    async def pop_pending(self) -> list[dict[str, Any]]:
        """Takes every queued command (expired ones are dropped), oldest first."""
        return await self._run(self._pop_pending)

    async def count_pending(self) -> int:
        return await self._run(self._count_pending)

    def _push_pending(self, conn: sqlite3.Connection, cmd_type: str, payload: Any,
                      ttl_s: float) -> int:
        now = self._clock()
        cur = conn.execute(
            "INSERT INTO pending_commands (type, payload, created_ts, expires_ts)"
            " VALUES (?, ?, ?, ?)", (str(cmd_type), _dumps(payload), now, now + float(ttl_s)))
        return int(cur.lastrowid or 0)

    def _pop_pending(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        now = self._clock()
        with _transaction(conn):
            rows = conn.execute("SELECT seq, type, payload, created_ts, expires_ts"
                                " FROM pending_commands ORDER BY seq").fetchall()
            if rows:
                conn.execute("DELETE FROM pending_commands WHERE seq <= ?", (rows[-1]["seq"],))
        return [{"type": row["type"], "payload": _loads(row["payload"]),
                 "created_ts": row["created_ts"]} for row in rows if row["expires_ts"] > now]

    def _count_pending(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT COUNT(*) FROM pending_commands WHERE expires_ts > ?",
                                (self._clock(),)).fetchone()[0])

    async def queue_command(self, cmd_type: str, payload: Any, *, ttl_s: float, cap: int,
                            dedupe_id: str | None = None) -> dict[str, Any]:
        """C-60 (K-03 v51): keeps a UI command for an offline agent, in one transaction. Expired commands go first;
        a command of the same type whose payload id equals dedupe_id (a chat retried while queued) is not queued
        twice; at cap the oldest are dropped to make room. Returns {queued, duplicate, dropped, expired, waiting}."""
        return await self._run(self._queue_command, str(cmd_type), payload, ttl_s=float(ttl_s), cap=int(cap),
                               dedupe_id=dedupe_id)

    async def take_pending(self) -> tuple[list[dict[str, Any]], int]:
        """C-60: takes every queued command at once: (the unexpired ones, oldest first, as {type, payload,
        created_ts, expires_ts, seq}; the number of expired ones that were dropped)."""
        return await self._run(self._take_pending)

    async def restore_pending(self, entries: Sequence[Mapping[str, Any]]) -> int:
        """C-60 review: puts commands take_pending gave out back where they were, at the head of the queue in their
        order, with their own expiry (the agent socket left before the flush sent them). Each keeps its seq: the
        queue never reuses one (AUTOINCREMENT) and take_pending took everything older. Returns how many."""
        return await self._run(self._restore_pending, [dict(entry) for entry in entries])

    def _queue_command(self, conn: sqlite3.Connection, cmd_type: str, payload: Any, *, ttl_s: float, cap: int,
                       dedupe_id: str | None) -> dict[str, Any]:
        now = self._clock()
        with _transaction(conn):
            expired = conn.execute("DELETE FROM pending_commands WHERE expires_ts <= ?", (now,)).rowcount
            if dedupe_id is not None:
                for row in conn.execute("SELECT payload FROM pending_commands WHERE type = ?", (cmd_type,)):
                    queued = _loads(row["payload"])
                    if isinstance(queued, Mapping) and queued.get("id") == dedupe_id:
                        waiting = int(conn.execute("SELECT COUNT(*) FROM pending_commands").fetchone()[0])
                        return {"queued": False, "duplicate": True, "dropped": 0, "expired": expired,
                                "waiting": waiting}
            waiting = int(conn.execute("SELECT COUNT(*) FROM pending_commands").fetchone()[0])
            dropped = max(0, waiting - cap + 1) if cap > 0 else 0
            if dropped:
                conn.execute("DELETE FROM pending_commands WHERE seq IN"
                             " (SELECT seq FROM pending_commands ORDER BY seq LIMIT ?)", (dropped,))
            conn.execute("INSERT INTO pending_commands (type, payload, created_ts, expires_ts) VALUES (?, ?, ?, ?)",
                         (cmd_type, _dumps(payload), now, now + ttl_s))
        return {"queued": True, "duplicate": False, "dropped": dropped, "expired": expired,
                "waiting": waiting - dropped + 1}

    def _take_pending(self, conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], int]:
        now = self._clock()
        with _transaction(conn):
            rows = conn.execute("SELECT seq, type, payload, created_ts, expires_ts"
                                " FROM pending_commands ORDER BY seq").fetchall()
            if rows:
                conn.execute("DELETE FROM pending_commands WHERE seq <= ?", (rows[-1]["seq"],))
        live = [{"type": row["type"], "payload": _loads(row["payload"]), "created_ts": row["created_ts"],
                 "expires_ts": row["expires_ts"], "seq": row["seq"]}
                for row in rows if row["expires_ts"] > now]
        return live, len(rows) - len(live)

    def _restore_pending(self, conn: sqlite3.Connection, entries: list[dict[str, Any]]) -> int:
        rows = []
        for entry in entries:
            seq = _count(entry.get("seq"))
            created, expires = entry.get("created_ts"), entry.get("expires_ts")
            if seq is None or not isinstance(created, (int, float)) or not isinstance(expires, (int, float)):
                raise ValueError("restore_pending takes the entries take_pending gave out")
            rows.append((seq, str(entry.get("type")), _dumps(entry.get("payload")), float(created), float(expires)))
        with _transaction(conn):
            conn.executemany("INSERT INTO pending_commands (seq, type, payload, created_ts, expires_ts)"
                             " VALUES (?, ?, ?, ?, ?)", rows)
        return len(rows)

    # ------------------------------------------------------------------ seen ids (K-70, C-NOTIFY-1)

    async def mark_seen(self, scope: str, item_id: str, ttl_s: float | None = None) -> bool:
        """True the first time an id is seen in its scope within ttl_s (default by scope: K-70
        envelope ids 24 h, notify nonces 300 s); False for a replay."""
        return await self._run(self._mark_seen, scope, item_id, ttl_s)

    async def prune_seen(self) -> int:
        """Deletes expired seen ids; returns how many."""
        return await self._run(self._prune_seen)

    def _mark_seen(self, conn: sqlite3.Connection, scope: str, item_id: str,
                   ttl_s: float | None) -> bool:
        now = self._clock()
        ttl = SEEN_TTL_S.get(scope, SEEN_TTL_S[SEEN_DESKTOP]) if ttl_s is None else float(ttl_s)
        with _transaction(conn):
            row = conn.execute("SELECT expires_ts FROM seen_ids WHERE scope = ? AND id = ?",
                               (str(scope), str(item_id))).fetchone()
            if row is not None and row["expires_ts"] > now:
                return False
            conn.execute(
                "INSERT INTO seen_ids (scope, id, expires_ts) VALUES (?, ?, ?) ON CONFLICT(scope, id)"
                " DO UPDATE SET expires_ts = excluded.expires_ts",
                (str(scope), str(item_id), now + ttl))
        return True

    def _prune_seen(self, conn: sqlite3.Connection) -> int:
        return int(conn.execute("DELETE FROM seen_ids WHERE expires_ts <= ?",
                                (self._clock(),)).rowcount)

    # ------------------------------------------------------------------ UI notifications (K-77)

    async def add_notification(self, payload: Mapping[str, Any],
                               kind: str = "reminder_due") -> tuple[str, dict[str, Any]]:
        """Stores a UI notification until it is acknowledged: ('new', payload), or ('exists',
        stored payload) for an id stored before, so a reminder is never delivered twice."""
        return await self._run(self._add_notification, payload, kind)

    async def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        """{id, kind, created_ts, acked_ts, acked_by, payload}, or None."""
        return await self._run(self._get_notification, notification_id)

    async def list_notifications(self, include_acked: bool = False,
                                 limit: int = INIT_NOTIFICATIONS) -> list[dict[str, Any]]:
        """Stored payloads, newest first; by default only the unacknowledged ones."""
        return await self._run(self._list_notifications, include_acked, limit)

    async def ack_notification(self, notification_id: str, by: str = "ui") -> bool:
        """True when this call acknowledged it (False: unknown or acknowledged before)."""
        return await self._run(self._ack_notification, notification_id, by)

    def _add_notification(self, conn: sqlite3.Connection, payload: Mapping[str, Any],
                          kind: str) -> tuple[str, dict[str, Any]]:
        data = dict(payload)
        data["id"] = str(data.get("id") or uuid.uuid4())
        inserted = conn.execute(
            "INSERT OR IGNORE INTO notifications (id, kind, data, created_ts) VALUES (?, ?, ?, ?)",
            (data["id"], str(kind), _dumps(data), self._clock())).rowcount
        if inserted:
            return "new", data
        row = conn.execute("SELECT data FROM notifications WHERE id = ?", (data["id"],)).fetchone()
        return "exists", _loads(row["data"], data)

    def _get_notification(self, conn: sqlite3.Connection,
                          notification_id: str) -> dict[str, Any] | None:
        row = conn.execute("SELECT id, kind, data, created_ts, acked_ts, acked_by FROM notifications"
                           " WHERE id = ?", (str(notification_id),)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "kind": row["kind"], "created_ts": row["created_ts"],
                "acked_ts": row["acked_ts"], "acked_by": row["acked_by"],
                "payload": _loads(row["data"], {})}

    def _list_notifications(self, conn: sqlite3.Connection, include_acked: bool,
                            limit: int) -> list[dict[str, Any]]:
        where = "" if include_acked else " WHERE acked_ts IS NULL"
        rows = conn.execute(f"SELECT data FROM notifications{where}"
                            " ORDER BY created_ts DESC, rowid DESC LIMIT ?",
                            (max(0, int(limit)),)).fetchall()
        return [_loads(row["data"], {}) for row in rows]

    def _ack_notification(self, conn: sqlite3.Connection, notification_id: str, by: str) -> bool:
        return conn.execute(
            "UPDATE notifications SET acked_ts = ?, acked_by = ? WHERE id = ? AND acked_ts IS NULL",
            (self._clock(), str(by), str(notification_id))).rowcount > 0

    # ------------------------------------------------------------------ init_state + stats

    async def init_snapshot(self, kv_keys: Iterable[str] = ()) -> dict[str, Any]:
        """Everything init_state (C-STATE-1) reads from the DB, in one locked read: messages
        (last 100, ascending, with legacy aliases), logs (30), memories (10), tool_logs (20),
        settings with their revision meta ('settings_meta', C-60), unacknowledged notifications and the
        stored values of kv_keys ('kv')."""
        return await self._run(self._init_snapshot, list(kv_keys))

    async def stats(self) -> dict[str, Any]:
        """Row counts per table and per message role, schema version, size; never content."""
        return await self._run(self._stats)

    def _init_snapshot(self, conn: sqlite3.Connection, kv_keys: list[str]) -> dict[str, Any]:
        messages, _ = self._list_messages(conn, MESSAGE_PAGE_MAX, None)
        return {
            "messages": messages,
            "logs": self._recent_events(conn, EVENT_LOG, INIT_LOGS),
            "memories": self._recent_events(conn, EVENT_MEMORY, INIT_MEMORIES),
            "tool_logs": self._recent_events(conn, EVENT_TOOL, INIT_TOOL_LOGS),
            "settings": self._get_settings(conn),
            "settings_meta": self._settings_meta(conn),
            "notifications": self._list_notifications(conn, False, INIT_NOTIFICATIONS),
            "kv": self._kv_get_many(conn, kv_keys),
        }

    def _stats(self, conn: sqlite3.Connection) -> dict[str, Any]:
        return _collect_stats(conn, self.path)


def read_stats(path: str) -> dict[str, Any]:
    """Stats of a database file through a read-only connection: safe next to the running bridge,
    and it never migrates, repairs or renames anything."""
    conn = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True,
                           timeout=_BUSY_TIMEOUT_S)
    try:
        return _collect_stats(conn, path)
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.core.bridge_store",
                                     description="UI bridge SQLite store tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    stats_cmd = commands.add_parser(
        "stats", help="row counts per table and per message role (never any content)")
    stats_cmd.add_argument("--db", default=None,
                           help="database file (default: BRIDGE_DB_PATH or <project>/data/bridge.db)")
    args = parser.parse_args(argv)

    path = args.db or bridge_db_path()
    if not os.path.isfile(path):
        print(f"no database at {path}", file=sys.stderr)
        return 1
    try:
        info = read_stats(path)
    except sqlite3.Error as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2
    print(f"db: {info['path']}")
    print(f"size_bytes: {info['size_bytes']}")
    print(f"schema_version: {info['schema_version']}")
    print(f"journal_mode: {info['journal_mode']}")
    for name, count in info["tables"].items():
        print(f"table {name}: {count}")
    for role, count in info["message_roles"].items():
        print(f"messages role {role}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
