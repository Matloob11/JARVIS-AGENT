"""Desktop Node write-ahead log and result outbox (plan task C-120 step 6; K-70 Refusals, Messages and Timers;
K-76 Process rules).

run/desktop_link.db under JARVIS_RUN_DIR (the C-115 SeenStore keeps its own table desktop_seen in the same
file), owned by one thread: every public method is thread-safe and waits for that thread (asubmit variants for
asyncio). WAL mode and BEGIN IMMEDIATE, like the SeenStore, so both connections share the file.

desktop_wal: one row per call_id, written BEFORE the node tells the agent anything (write-ahead):
- refused: refused before it was accepted (the ack {accepted:false, code}); a later exec with the same
  call_id is judged again (an APPROVAL_REQUIRED exec comes back with its approval);
- accepted: written before the accept ack; the tool has not started yet (it waits for the input lock);
- running: the tool started;
- completed: it ended with a result (any status), which also went to the outbox in the same transaction;
- never_started / start_window_missed: accepted but never started (the lock wait, a cancel, the start window),
  or recover() found it accepted after a crash;
- interrupted: recover() found it running after a crash (kill -9, power loss): its effect is unknown.
A call in accepted, running, completed or interrupted 'started' for the agent (it got the accept ack), so it
never gets a refusal and never runs again: a retry gets its real outcome (K-70: the WAL is checked before a
refusal is signed). Rows are kept WAL_RETENTION_S (> 24 h, K-76) after their last change.

desktop_outbox: the result body of every finished call until the agent acknowledges it (result_ack), at most
OUTBOX_TTL_S (10 min) and OUTBOX_MAX (200) entries (K-70 Timers); the node re-sends what is left as
result_resend after a hello or sync_request of the peer that serves the call's room.

The hello's wal_report (K-70) lists the calls of the last REPORT_WINDOW_S with the state completed,
interrupted, never_started (also for a refusal) or start_window_missed, and finished_at. Nothing here stores
arguments: only call_id, tool, room, origin, peer, codes and the result body the agent gets anyway.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.utils.desktop_protocol import ResultRecord

WAL_FILE = "desktop_link.db"
WAL_RETENTION_S = 25 * 3600.0   # K-70/K-76: call_ids and their state at least 24 h
OUTBOX_TTL_S = 600.0            # K-70: outbox 10 min / 200 entries
OUTBOX_MAX = 200
REPORT_WINDOW_S = 3600.0        # an agent never sends an exec for a call_id more than 1 h after its first send
REPORT_MAX = 300

STATE_REFUSED = "refused"
STATE_ACCEPTED = "accepted"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_INTERRUPTED = "interrupted"
STATE_NEVER_STARTED = "never_started"
STATE_START_WINDOW_MISSED = "start_window_missed"
STATES = frozenset({STATE_REFUSED, STATE_ACCEPTED, STATE_RUNNING, STATE_COMPLETED, STATE_INTERRUPTED,
                    STATE_NEVER_STARTED, STATE_START_WINDOW_MISSED})
STARTED_STATES = frozenset({STATE_ACCEPTED, STATE_RUNNING, STATE_COMPLETED, STATE_INTERRUPTED})
FINAL_STATES = frozenset({STATE_COMPLETED, STATE_NEVER_STARTED, STATE_START_WINDOW_MISSED})
# wal_report states (K-70 hello): a refusal never started either.
REPORT_STATES = {STATE_COMPLETED: STATE_COMPLETED, STATE_INTERRUPTED: STATE_INTERRUPTED,
                 STATE_NEVER_STARTED: STATE_NEVER_STARTED, STATE_REFUSED: STATE_NEVER_STARTED,
                 STATE_START_WINDOW_MISSED: STATE_START_WINDOW_MISSED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_wal (
    call_id TEXT PRIMARY KEY, tool TEXT NOT NULL, room TEXT NOT NULL, origin TEXT NOT NULL, peer TEXT NOT NULL,
    state TEXT NOT NULL, code TEXT, status TEXT, effect TEXT, exec_id TEXT, boot_id TEXT,
    accepted_at REAL, run_at REAL, finished_at REAL, updated_at REAL NOT NULL) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS desktop_wal_updated ON desktop_wal (updated_at);
CREATE INDEX IF NOT EXISTS desktop_wal_state ON desktop_wal (state);
CREATE TABLE IF NOT EXISTS desktop_outbox (
    call_id TEXT PRIMARY KEY, tool TEXT NOT NULL, room TEXT NOT NULL, origin TEXT NOT NULL, peer TEXT NOT NULL,
    original_ts REAL NOT NULL, finished_at REAL NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL)
    WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS desktop_outbox_created ON desktop_outbox (created_at);
"""
_WAL_COLUMNS = ("call_id", "tool", "room", "origin", "peer", "state", "code", "status", "effect", "exec_id",
                "boot_id", "accepted_at", "run_at", "finished_at", "updated_at")


@dataclass(frozen=True)
class WalEntry:
    """One desktop_wal row."""

    call_id: str
    tool: str
    room: str
    origin: str
    peer: str
    state: str
    code: str | None = None
    status: str | None = None
    effect: str | None = None
    exec_id: str | None = None
    boot_id: str | None = None
    accepted_at: float | None = None
    run_at: float | None = None
    finished_at: float | None = None
    updated_at: float = 0.0

    @property
    def started(self) -> bool:
        """The agent was told this call started (accept ack): no refusal, no second run (K-70)."""
        return self.state in STARTED_STATES

    @property
    def in_flight(self) -> bool:
        return self.state in (STATE_ACCEPTED, STATE_RUNNING)

    @property
    def report_state(self) -> str | None:
        return REPORT_STATES.get(self.state)


@dataclass(frozen=True)
class OutboxEntry:
    """A finished call's result body waiting for result_ack, with the peer it was for."""

    record: ResultRecord
    peer: str
    created_at: float


class WriteAheadLog:  # pylint: disable=too-many-public-methods  # each operation has a sync and an async form
    """The node's WAL and outbox in run/desktop_link.db (see the module docstring)."""

    def __init__(self, path: str | os.PathLike[str], *, wall: Callable[[], float] = time.time,
                 retention_s: float = WAL_RETENTION_S, outbox_ttl_s: float = OUTBOX_TTL_S,
                 outbox_max: int = OUTBOX_MAX) -> None:
        self.path = Path(path)
        self._wall, self._retention, self._ttl, self._max = wall, retention_s, outbox_ttl_s, outbox_max
        self._closed = False
        self._lock = threading.Lock()
        self._jobs: queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...], Future[Any]] | None]
        self._jobs = queue.SimpleQueue()
        ready: Future[None] = Future()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True, name="desktop-wal")
        self._thread.start()
        ready.result()

    # --- the owner thread ------------------------------------------------------------------------------

    def _run(self, ready: Future[None]) -> None:
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(os.fspath(self.path), timeout=10.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")  # a finished call survives a power cut
            conn.executescript(_SCHEMA)
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

    def submit(self, func: Callable[..., Any], *args: Any) -> Future[Any]:
        """Runs func(conn, *args) on the owner thread."""
        future: Future[Any] = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("the write-ahead log is closed")
            self._jobs.put((func, args, future))
        return future

    def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        return self.submit(func, *args).result(timeout=30.0)

    async def asubmit(self, func: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.wrap_future(self.submit(func, *args))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._jobs.put(None)
        self._thread.join(timeout=10.0)

    def __enter__(self) -> WriteAheadLog:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _write(conn: sqlite3.Connection, statements: Iterable[tuple[str, tuple[Any, ...]]]) -> None:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for sql, params in statements:
                conn.execute(sql, params)
            conn.execute("COMMIT")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _entry(row: tuple[Any, ...] | None) -> WalEntry | None:
        return None if row is None else WalEntry(*row)

    # --- calls ---------------------------------------------------------------------------------------------

    def _get(self, conn: sqlite3.Connection, call_id: str) -> WalEntry | None:
        row = conn.execute(f"SELECT {', '.join(_WAL_COLUMNS)} FROM desktop_wal WHERE call_id=?",
                           (call_id,)).fetchone()
        return self._entry(row)

    def get(self, call_id: str) -> WalEntry | None:
        return self._call(self._get, call_id)

    async def aget(self, call_id: str) -> WalEntry | None:
        return await self.asubmit(self._get, call_id)

    def _put_unstarted(self, conn: sqlite3.Connection, call_id: str, state: str, fields: dict[str, Any]) -> bool:
        """Inserts or replaces the row unless the call already started (never overwrite a started call)."""
        now = self._wall()
        values = {"call_id": call_id, "tool": fields.get("tool") or "", "room": fields.get("room") or "",
                  "origin": fields.get("origin") or "", "peer": fields.get("peer") or "", "state": state,
                  "code": fields.get("code"), "status": None, "effect": None, "exec_id": fields.get("exec_id"),
                  "boot_id": fields.get("boot_id"), "accepted_at": now if state == STATE_ACCEPTED else None,
                  "run_at": None, "finished_at": None if state == STATE_ACCEPTED else now, "updated_at": now}
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT state FROM desktop_wal WHERE call_id=?", (call_id,)).fetchone()
            if existing is not None and existing[0] in STARTED_STATES:
                conn.execute("ROLLBACK")
                return False
            conn.execute(f"INSERT OR REPLACE INTO desktop_wal ({', '.join(_WAL_COLUMNS)}) VALUES "
                         f"({', '.join('?' for _ in _WAL_COLUMNS)})", tuple(values[name] for name in _WAL_COLUMNS))
            conn.execute("COMMIT")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return True

    @staticmethod
    def _refusal_state(state: str) -> str:
        if state not in (STATE_REFUSED, STATE_START_WINDOW_MISSED):
            raise ValueError(f"{state!r} is not a refusal state")
        return state

    def refuse(self, call_id: str, *, code: str, state: str = STATE_REFUSED, **fields: Any) -> bool:
        """Records a refusal before acceptance (state refused or start_window_missed); False (nothing
        written) when the call already started."""
        return self._call(self._put_unstarted, call_id, self._refusal_state(state), {**fields, "code": code})

    async def arefuse(self, call_id: str, *, code: str, state: str = STATE_REFUSED, **fields: Any) -> bool:
        return await self.asubmit(self._put_unstarted, call_id, self._refusal_state(state),
                                  {**fields, "code": code})

    def accept(self, call_id: str, **fields: Any) -> bool:
        """Write-ahead record of an accepted call (before its accept ack): tool, room, origin, peer, exec_id,
        boot_id. False when the call_id already started (the caller answers with its real outcome)."""
        return self._call(self._put_unstarted, call_id, STATE_ACCEPTED, fields)

    async def aaccept(self, call_id: str, **fields: Any) -> bool:
        return await self.asubmit(self._put_unstarted, call_id, STATE_ACCEPTED, fields)

    def _mark_running(self, conn: sqlite3.Connection, call_id: str) -> None:
        now = self._wall()
        self._write(conn, [("UPDATE desktop_wal SET state=?, run_at=?, updated_at=? WHERE call_id=? AND state=?",
                            (STATE_RUNNING, now, now, call_id, STATE_ACCEPTED))])

    def mark_running(self, call_id: str) -> None:
        self._call(self._mark_running, call_id)

    def _finish(self, conn: sqlite3.Connection, call_id: str, fields: dict[str, Any]) -> None:
        state, record, peer = fields["state"], fields.get("record"), fields.get("peer") or ""
        if state not in FINAL_STATES:
            raise ValueError(f"{state!r} is not a final state")
        now = self._wall()
        statements = [("UPDATE desktop_wal SET state=?, status=?, effect=?, code=?, finished_at=?, updated_at=? "
                       "WHERE call_id=?", (state, fields.get("status"), fields.get("effect"), fields.get("code"), now,
                                           now, call_id))]
        if record is not None:
            statements.append((
                "INSERT OR REPLACE INTO desktop_outbox (call_id, tool, room, origin, peer, original_ts, finished_at, "
                "result, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.call_id, record.tool, record.room, record.origin, peer, float(record.original_ts),
                 float(record.finished_at), json.dumps(dict(record.result), ensure_ascii=False, sort_keys=True),
                 now)))
        self._write(conn, statements)

    def finish(self, call_id: str, *, state: str = STATE_COMPLETED, status: str | None = None,
               effect: str | None = None, code: str | None = None, record: ResultRecord | None = None,
               peer: str = "") -> None:
        """Ends a call; with a record, its result body goes to the outbox in the same transaction."""
        self._call(self._finish, call_id, {"state": state, "status": status, "effect": effect, "code": code,
                                           "record": record, "peer": peer})

    async def afinish(self, call_id: str, *, state: str = STATE_COMPLETED, status: str | None = None,
                      effect: str | None = None, code: str | None = None, record: ResultRecord | None = None,
                      peer: str = "") -> None:
        await self.asubmit(self._finish, call_id, {"state": state, "status": status, "effect": effect, "code": code,
                                                   "record": record, "peer": peer})

    def _recover(self, conn: sqlite3.Connection) -> dict[str, int]:
        now = self._wall()
        counts: dict[str, int] = {}
        try:
            conn.execute("BEGIN IMMEDIATE")
            for old, new in ((STATE_RUNNING, STATE_INTERRUPTED), (STATE_ACCEPTED, STATE_NEVER_STARTED)):
                counts[new] = conn.execute("UPDATE desktop_wal SET state=?, finished_at=?, updated_at=? WHERE state=?",
                                           (new, now, now, old)).rowcount
            conn.execute("COMMIT")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        self._purge(conn)
        return counts

    def recover(self) -> dict[str, int]:
        """At node start: calls left running are interrupted, calls left accepted never started. Returns the
        counts per new state. Only one node runs at a time (run/desktop_node.lock), so nothing is in flight."""
        return self._call(self._recover)

    # --- outbox ---------------------------------------------------------------------------------------------

    def _outbox(self, conn: sqlite3.Connection) -> list[OutboxEntry]:
        self._expire_outbox(conn)
        rows = conn.execute("SELECT call_id, tool, room, origin, peer, original_ts, finished_at, result, created_at "
                            "FROM desktop_outbox ORDER BY created_at, call_id").fetchall()
        entries = []
        for call_id, tool, room, origin, peer, original_ts, finished_at, result, created_at in rows:
            try:
                body = json.loads(result)
            except ValueError:
                continue
            entries.append(OutboxEntry(ResultRecord(call_id, tool, room, origin, original_ts, finished_at, body),
                                       peer, created_at))
        return entries

    def outbox(self) -> list[OutboxEntry]:
        """Results not acknowledged yet, oldest first (expired ones are dropped first)."""
        return self._call(self._outbox)

    async def aoutbox(self) -> list[OutboxEntry]:
        return await self.asubmit(self._outbox)

    def _outbox_entry(self, conn: sqlite3.Connection, call_id: str) -> OutboxEntry | None:
        return next((entry for entry in self._outbox(conn) if entry.record.call_id == call_id), None)

    async def aoutbox_entry(self, call_id: str) -> OutboxEntry | None:
        return await self.asubmit(self._outbox_entry, call_id)

    def _ack(self, conn: sqlite3.Connection, call_id: str) -> bool:
        before = conn.total_changes
        self._write(conn, [("DELETE FROM desktop_outbox WHERE call_id=?", (call_id,))])
        return conn.total_changes > before

    def ack_result(self, call_id: str) -> bool:
        """result_ack: the agent has the result; True when an entry was removed."""
        return self._call(self._ack, call_id)

    async def aack_result(self, call_id: str) -> bool:
        return await self.asubmit(self._ack, call_id)

    def _expire_outbox(self, conn: sqlite3.Connection) -> int:
        now = self._wall()
        before = conn.total_changes
        self._write(conn, [
            ("DELETE FROM desktop_outbox WHERE created_at < ?", (now - self._ttl,)),
            ("DELETE FROM desktop_outbox WHERE call_id NOT IN (SELECT call_id FROM desktop_outbox "
             "ORDER BY created_at DESC, call_id DESC LIMIT ?)", (self._max,))])
        return conn.total_changes - before

    def _outbox_size(self, conn: sqlite3.Connection) -> int:
        self._expire_outbox(conn)
        return int(conn.execute("SELECT COUNT(*) FROM desktop_outbox").fetchone()[0])

    def outbox_size(self) -> int:
        return self._call(self._outbox_size)

    async def aoutbox_size(self) -> int:
        return await self.asubmit(self._outbox_size)

    # --- report and purge -------------------------------------------------------------------------------------

    def _report(self, conn: sqlite3.Connection, window_s: float, limit: int) -> list[dict[str, Any]]:
        since = self._wall() - window_s
        states = tuple(REPORT_STATES)
        rows = conn.execute(
            f"SELECT call_id, state, finished_at FROM desktop_wal WHERE updated_at >= ? AND state IN "
            f"({', '.join('?' for _ in states)}) ORDER BY updated_at DESC, call_id LIMIT ?",
            (since, *states, max(0, int(limit)))).fetchall()
        return [{"call_id": call_id, "state": REPORT_STATES[state],
                 "finished_at": round(float(finished_at if finished_at is not None else since), 3)}
                for call_id, state, finished_at in rows]

    def report(self, *, window_s: float = REPORT_WINDOW_S, limit: int = REPORT_MAX) -> list[dict[str, Any]]:
        """The hello's wal_report: [{call_id, state, finished_at}], newest first."""
        return self._call(self._report, window_s, limit)

    async def areport(self, *, window_s: float = REPORT_WINDOW_S, limit: int = REPORT_MAX) -> list[dict[str, Any]]:
        return await self.asubmit(self._report, window_s, limit)

    def _purge(self, conn: sqlite3.Connection) -> int:
        before = conn.total_changes
        self._write(conn, [("DELETE FROM desktop_wal WHERE updated_at < ? AND state NOT IN (?, ?)",
                            (self._wall() - self._retention, STATE_ACCEPTED, STATE_RUNNING))])
        removed = conn.total_changes - before
        self._expire_outbox(conn)
        return removed

    def purge(self) -> int:
        """Drops rows older than the retention (never a call in flight) and expired outbox entries."""
        return self._call(self._purge)

    async def apurge(self) -> int:
        return await self.asubmit(self._purge)

    def _counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        return dict(conn.execute("SELECT state, COUNT(*) FROM desktop_wal GROUP BY state").fetchall())

    def counts(self) -> dict[str, int]:
        return self._call(self._counts)
