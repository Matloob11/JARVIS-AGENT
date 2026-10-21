"""Plan task C-13: the bridge SQLite store (src/core/bridge_store.py) and its use in ui_bridge.

Every test gets its own temporary database, and retention, TTLs and expiry run on a fake clock.
The bridge tests swap ui_bridge.store for a temporary store, give it fresh volatile state and
record sio.emit, so nothing reaches a socket, the live bridge or the project's data/ folder.
A "restart" is a brand-new BridgeStore (and BridgeStateManager) on the same file.
"""
# pylint: disable=protected-access,redefined-outer-name
import asyncio
import importlib
import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from src.core import bridge_store
from src.core.bridge_store import (
    EVENT_LOG,
    EVENT_MEMORY,
    EVENT_TOOL,
    KV_ACTIVE_JOB,
    KV_DESKTOP_LINK,
    KV_LAST_ACTIVITY_TS,
    KV_PAUSED,
    SEEN_DESKTOP,
    SEEN_NOTIFY,
    BridgeStore,
    message_cursor,
    normalize_message,
    parse_message_cursor,
    ui_message,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_790_000_000.0  # 2026-09-21, epoch seconds
TOKEN = "c13-test-token"
DAY = 86400.0


class FakeClock:
    def __init__(self, start: float = T0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Records(logging.Handler):
    """Collects the records of the bridge's 'UI-BRIDGE' logger while a test runs."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "bridge.db"


@pytest.fixture
async def store(db_path: Path, clock: FakeClock):
    s = BridgeStore(db_path, clock=clock)
    yield s
    await s.close()


@pytest.fixture
def bridge_log():
    handler = Records()
    log = logging.getLogger("UI-BRIDGE")
    log.addHandler(handler)
    yield handler
    log.removeHandler(handler)


def _query(path: Path, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _renamed_dbs(db_path: Path) -> list[Path]:
    """The renamed-aside database files (not their -wal/-shm siblings)."""
    pattern = re.compile(r"bridge\.db\.corrupt-\d{8}-\d{6}(-\d+)?")
    return sorted(p for p in db_path.parent.iterdir() if pattern.fullmatch(p.name))


# ---------------------------------------------------------------- C-MSG-1 normalization


def test_normalize_maps_legacy_agent_fields():
    msg = normalize_message({"id": "m1", "role": "agent", "text": "Hi", "is_final": False,
                             "timestamp": T0 + 0.5})

    assert msg == {"id": "m1", "role": "assistant", "text": "Hi", "source": "voice",
                   "final": False, "ts": T0 + 0.5}


def test_normalize_keeps_the_typed_no_reply_marker_for_the_ui():
    """K-59 v50: the router's 'jawab nahi ban saka' note carries kind 'typed_no_reply' as an extra field; the bridge
    keeps it next to source 'system' and turn_id, so the dashboard styles only this note as a system line."""
    msg = normalize_message({"id": "n1", "role": "assistant", "text": "Sir, maazrat", "source": "system",
                             "turn_id": "u-1", "kind": "typed_no_reply", "ts": T0})

    assert msg["kind"] == "typed_no_reply" and msg["source"] == "system" and msg["role"] == "assistant"
    assert msg["turn_id"] == "u-1"


def test_normalize_defaults():
    msg = normalize_message({}, now=T0)

    assert str(uuid.UUID(msg.pop("id"))) != ""
    assert msg == {"role": "assistant", "text": "", "source": "voice", "final": True, "ts": T0}
    assert normalize_message({}, "text")["source"] == "text"
    assert normalize_message({}, "carrier-pigeon")["source"] == "voice"


@pytest.mark.parametrize("payload, field, expected", [
    ({"ts": (T0 + 1) * 1000}, "ts", T0 + 1),                  # milliseconds -> seconds
    ({"timestamp": str(T0 + 2)}, "ts", T0 + 2),               # numeric string
    ({"ts": "soon", "timestamp": T0 + 3}, "ts", T0 + 3),      # unusable ts falls back to timestamp
    ({"ts": "soon"}, "ts", 123.0),                            # nothing usable: now
    ({"text": 42}, "text", "42"),
    ({"text": None}, "text", ""),
    ({"final": "false"}, "final", False),
    ({"final": True, "is_final": False}, "final", True),      # the canonical name wins
    ({"role": " USER "}, "role", "user"),
    ({"role": "model"}, "role", "assistant"),
    ({"role": "system"}, "source", "system"),
    ({"role": "system", "source": "text"}, "source", "text"),
    ({"source": "radio"}, "source", "voice"),
])
def test_normalize_units_and_types(payload, field, expected):
    assert normalize_message(payload, now=123.0)[field] == expected


def test_normalize_keeps_extra_fields_and_drops_legacy_names():
    msg = normalize_message({"id": "a1", "role": "assistant", "text": "x", "turn_id": "u1",
                             "score": 0.9, "timestamp": T0, "is_final": True})

    assert msg["turn_id"] == "u1" and msg["score"] == 0.9
    assert "timestamp" not in msg and "is_final" not in msg


def test_ui_message_adds_the_legacy_aliases():
    out = ui_message(normalize_message({"id": "m1", "role": "agent", "text": "x", "ts": T0,
                                        "final": False}))

    assert out["timestamp"] == out["ts"] == T0
    assert out["is_final"] is out["final"] is False


# ---------------------------------------------------------------- store basics


async def test_open_creates_wal_db_with_every_table(store, db_path):
    await store.open()

    assert db_path.is_file()
    tables = {name for (name,) in _query(db_path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"messages", "events", "settings", "sessions", "audit", "kv", "confirmations",
            "pending_commands", "schema_version", "seen_ids", "notifications"} <= tables
    assert _query(db_path, "PRAGMA journal_mode") == [("wal",)]
    assert _query(db_path, "SELECT version FROM schema_version") == [(bridge_store.SCHEMA_VERSION,)]
    assert await store._run(lambda conn: conn.execute("PRAGMA synchronous").fetchone()[0]) == 1


async def test_sqlite_work_runs_off_the_event_loop_thread(store):
    loop_thread = threading.get_ident()
    threads: list[int] = []

    def probe(_conn: sqlite3.Connection) -> int:
        threads.append(threading.get_ident())
        return 1

    assert await store._run(probe) == 1
    assert threads and threads[0] != loop_thread


def test_db_path_comes_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "x.db"))
    assert bridge_store.bridge_db_path() == str(tmp_path / "x.db")
    monkeypatch.delenv("BRIDGE_DB_PATH")
    assert bridge_store.bridge_db_path() == str(ROOT / "data" / "bridge.db")
    assert BridgeStore().path == str(ROOT / "data" / "bridge.db")


# ---------------------------------------------------------------- messages


async def test_upsert_new_then_update_merges_by_id(store, db_path):
    status, msg = await store.upsert_message(
        {"id": "m1", "role": "agent", "text": "Hel", "is_final": False, "timestamp": T0})
    assert status == "new"
    assert msg["role"] == "assistant" and msg["final"] is False and msg["is_final"] is False
    assert msg["ts"] == msg["timestamp"] == T0

    status, msg = await store.upsert_message({"id": "m1", "text": "Hello", "is_final": True,
                                              "turn_id": "u1"})
    assert status == "updated"
    assert msg == {"id": "m1", "role": "assistant", "text": "Hello", "source": "voice",
                   "final": True, "ts": T0, "turn_id": "u1", "timestamp": T0, "is_final": True}

    assert _query(db_path, "SELECT id, role, text FROM messages") == [("m1", "assistant", "Hello")]
    assert await store.get_message("m1") == msg
    assert await store.get_message("nope") is None


async def test_upsert_without_id_gets_a_new_id_each_time(store, clock):
    _, first = await store.upsert_message({"role": "user", "text": "a"}, "text")
    _, second = await store.upsert_message({"role": "user", "text": "a"}, "text")

    assert first["id"] != second["id"]
    assert first["source"] == "text" and first["ts"] == clock.now


async def test_list_messages_is_ascending_and_pages_by_ts(store):
    for offset in (3, 1, 5, 2, 4):
        await store.upsert_message({"id": f"m{offset}", "role": "user", "text": str(offset),
                                    "ts": T0 + offset})

    page, more = await store.list_messages(limit=2)
    assert [m["id"] for m in page] == ["m4", "m5"] and more is True
    page, more = await store.list_messages(limit=2, before=T0 + 4)
    assert [m["id"] for m in page] == ["m2", "m3"] and more is True
    page, more = await store.list_messages(limit=2, before=T0 + 2)
    assert [m["id"] for m in page] == ["m1"] and more is False
    page, _ = await store.list_messages(limit=2, before=(T0 + 4) * 1000)  # a cursor in ms
    assert [m["id"] for m in page] == ["m2", "m3"]
    assert len((await store.list_messages(limit=0))[0]) == 1
    assert len((await store.list_messages(limit=5000))[0]) == 5
    assert all("timestamp" in m and "is_final" in m for m in page)


async def test_list_messages_caps_a_page_at_100(store):
    for n in range(105):
        await store.upsert_message({"id": f"m{n:03d}", "role": "user", "text": "x", "ts": T0 + n})

    page, more = await store.list_messages(limit=500)
    assert len(page) == 100 and more is True
    assert page[0]["id"] == "m005" and page[-1]["id"] == "m104"


@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_cursor_pages_never_skip_messages_that_share_a_ts(store, limit):
    # time.time() on Windows ticks every ~15.6 ms, so two transcriptions can share a ts.
    stamps = {"a": T0, "b": T0, "c": T0, "d": T0 + 1, "e": T0 + 1, "f": T0 + 2, "g": T0 + 2}
    for msg_id, ts in stamps.items():
        await store.upsert_message({"id": msg_id, "role": "user", "text": msg_id, "ts": ts})

    pages = [await store.message_page(limit)]
    while pages[-1]["next_cursor"] is not None and len(pages) < 20:
        pages.append(await store.message_page(limit, *parse_message_cursor(pages[-1]["next_cursor"])))
    assert [m["id"] for page in reversed(pages) for m in page["messages"]] == list(stamps)
    assert [p["has_more"] for p in pages] == [True] * (len(pages) - 1) + [False]

    # A bare epoch (the K-41 `before`) still means strictly older.
    page = await store.message_page(10, *parse_message_cursor(repr(T0 + 1)))
    assert [m["id"] for m in page["messages"]] == ["a", "b", "c"] and page["next_cursor"] is None


def test_message_cursor_round_trips_exactly():
    ts = T0 + 0.123456789
    assert parse_message_cursor(message_cursor(ts, 42)) == (ts, 42)
    assert parse_message_cursor(" 1790000003.5 ") == (T0 + 3.5, None)
    for bad in ("", "abc", "nan", "inf:1", f"{T0!r}:x", f"{T0!r}:"):
        with pytest.raises(ValueError):
            parse_message_cursor(bad)


async def test_messages_survive_a_restart_with_the_same_ids(db_path, clock):
    first = BridgeStore(db_path, clock=clock)
    await first.upsert_message({"id": "typed-1", "role": "user", "text": "hello", "source": "text",
                                "ts": T0})
    await first.upsert_message({"id": "reply-1", "role": "agent", "text": "Hi Sir", "ts": T0 + 1})
    await first.close()

    second = BridgeStore(db_path, clock=clock)
    try:
        page, more = await second.list_messages()
    finally:
        await second.close()
    assert [(m["id"], m["role"], m["text"]) for m in page] == [
        ("typed-1", "user", "hello"), ("reply-1", "assistant", "Hi Sir")]
    assert more is False


# ---------------------------------------------------------------- events and retention


async def test_events_are_stored_as_received_and_read_oldest_first(store):
    for n in range(35):
        await store.append_event(EVENT_LOG, {"text": f"log {n}", "category": "SYSTEM"})
    await store.append_event(EVENT_MEMORY, {"id": "mem", "content": "likes tea"})
    await store.append_event(EVENT_TOOL, {"tool": "open_app", "details": "notepad"})

    logs = await store.recent_events(EVENT_LOG, 30)
    assert len(logs) == 30 and logs[0]["text"] == "log 5" and logs[-1]["text"] == "log 34"
    assert await store.recent_events(EVENT_MEMORY, 10) == [{"id": "mem", "content": "likes tea"}]
    assert await store.recent_events(EVENT_TOOL, 20) == [{"tool": "open_app", "details": "notepad"}]
    assert await store.delete_events(EVENT_MEMORY) == 1
    assert await store.recent_events(EVENT_MEMORY) == []


async def test_retention_deletes_only_old_events_never_messages(store, clock, db_path, monkeypatch):
    monkeypatch.setattr(bridge_store, "_PRUNE_BATCH", 2)  # forces several batches
    for n in range(5):
        await store.append_event(EVENT_LOG, {"text": f"old {n}"})
    await store.upsert_message({"id": "m-old", "role": "user", "text": "kept", "ts": T0})
    clock.advance(3 * DAY)
    await store.append_event(EVENT_LOG, {"text": "new"})

    assert await store.prune_events(0) == 0          # 0 = keep forever
    assert await store.prune_events(float("nan")) == 0
    assert await store.prune_events(2) == 5
    assert await store.recent_events(EVENT_LOG) == [{"text": "new"}]
    assert _query(db_path, "SELECT id FROM messages") == [("m-old",)]


@pytest.mark.parametrize("raw, days", [
    (None, 0.0), ("", 0.0), ("0", 0.0), ("7", 7.0), ("1.5", 1.5), ("-3", 0.0), ("abc", 0.0),
    ("inf", 0.0),
])
def test_retention_days_from_env(monkeypatch, raw, days):
    if raw is None:
        monkeypatch.delenv("BRIDGE_EVENT_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("BRIDGE_EVENT_RETENTION_DAYS", raw)
    assert bridge_store.event_retention_days() == days


# ---------------------------------------------------------------- settings and kv


async def test_merge_settings_merges_nested_objects_and_records_who(store, db_path):
    await store.merge_settings({"voice": "Charon", "automation": {"desktop_control": True}}, by="ui")
    merged = await store.merge_settings({"automation": {"clipboard_helper": False},
                                         "persona": "anna"}, by="agent")

    expected = {"voice": "Charon", "persona": "anna",
                "automation": {"desktop_control": True, "clipboard_helper": False}}
    assert merged == expected
    assert await store.get_settings() == expected
    assert dict(_query(db_path, "SELECT key, updated_by FROM settings")) == {
        "voice": "ui", "automation": "agent", "persona": "agent"}


async def test_kv_round_trip_including_the_vps_brain_keys(db_path, clock):
    first = BridgeStore(db_path, clock=clock)
    assert await first.kv_get(KV_PAUSED, False) is False
    await first.kv_set_many({KV_DESKTOP_LINK: "off", KV_PAUSED: True,
                             KV_ACTIVE_JOB: {"job_id": "AJ_1", "room": "jarvis-hybrid"},
                             KV_LAST_ACTIVITY_TS: T0})
    await first.kv_set("muted", True)
    assert await first.kv_delete("muted") is True
    assert await first.kv_delete("muted") is False
    await first.close()

    second = BridgeStore(db_path, clock=clock)
    try:
        assert await second.kv_get(KV_DESKTOP_LINK) == "off"
        assert await second.kv_get_many([KV_PAUSED, KV_ACTIVE_JOB, KV_LAST_ACTIVITY_TS, "nope"]) == {
            KV_PAUSED: True, KV_ACTIVE_JOB: {"job_id": "AJ_1", "room": "jarvis-hybrid"},
            KV_LAST_ACTIVITY_TS: T0}
        assert await second.kv_get_many([]) == {}
        assert await second.kv_get("muted", "unset") == "unset"
    finally:
        await second.close()


# ---------------------------------------------------------------- corrupt database


async def test_garbage_file_is_renamed_never_deleted(db_path, clock, bridge_log):
    db_path.parent.mkdir(parents=True)
    garbage = b"this is not a database " * 200
    db_path.write_bytes(garbage)

    store = BridgeStore(db_path, clock=clock)
    try:
        assert await store.kv_get("x", "fresh") == "fresh"
        await store.kv_set("x", 1)
        assert await store.kv_get("x") == 1
    finally:
        await store.close()

    kept = _renamed_dbs(db_path)
    assert [p.name for p in kept] == [f"bridge.db.corrupt-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(T0))}"]
    assert kept[0].read_bytes() == garbage
    critical = [r for r in bridge_log.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1 and "corrupt" in critical[0].getMessage()


async def test_damaged_pages_found_by_the_integrity_check_are_renamed(db_path, clock, bridge_log):
    first = BridgeStore(db_path, clock=clock)
    for n in range(400):
        await first.append_event(EVENT_LOG, {"text": "x" * 200, "n": n})
    await first.close()
    data = bytearray(db_path.read_bytes())
    data[4096 * 2:4096 * 6] = b"\xa5" * (4096 * 4)  # page 1 (header + schema) stays intact
    db_path.write_bytes(bytes(data))

    second = BridgeStore(db_path, clock=clock)
    try:
        await second.open()
        assert await second.recent_events(EVENT_LOG) == []
    finally:
        await second.close()

    kept = _renamed_dbs(db_path)
    assert len(kept) == 1 and kept[0].read_bytes() == bytes(data)
    assert any(r.levelno == logging.CRITICAL for r in bridge_log.records)


async def test_corruption_found_while_running_is_renamed_and_the_call_retried(store, db_path):
    await store.upsert_message({"id": "m1", "role": "user", "text": "x"})
    calls: list[sqlite3.Connection] = []

    def flaky(conn: sqlite3.Connection) -> int:
        calls.append(conn)
        if len(calls) == 1:
            exc = sqlite3.DatabaseError("database disk image is malformed")
            exc.sqlite_errorcode = 11  # SQLITE_CORRUPT
            raise exc
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    assert await store._run(flaky) == 0  # the retry ran on a fresh database
    assert len(calls) == 2 and calls[0] is not calls[1]
    assert len(_renamed_dbs(db_path)) == 1


async def test_without_repair_a_corrupt_file_raises_and_stays(db_path, clock):
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not sqlite" * 100)
    store = BridgeStore(db_path, clock=clock, repair=False)

    with pytest.raises(sqlite3.DatabaseError):
        await store.open()
    assert db_path.read_bytes() == b"not sqlite" * 100
    assert not _renamed_dbs(db_path)


def _db_error(code: int | None, text: str = "boom") -> sqlite3.DatabaseError:
    exc = sqlite3.DatabaseError(text)
    if code is not None:
        exc.sqlite_errorcode = code
    return exc


@pytest.mark.parametrize("exc, corrupt", [
    (_db_error(11), True),                                   # SQLITE_CORRUPT
    (_db_error(11 | (1 << 8)), True),                        # an extended SQLITE_CORRUPT_* code
    (_db_error(26), True),                                   # SQLITE_NOTADB
    (_db_error(5, "database is locked"), False),             # SQLITE_BUSY
    (_db_error(13, "database or disk is full"), False),      # SQLITE_FULL
    (sqlite3.OperationalError("unable to open database file"), False),
    (_db_error(None, "database disk image is malformed"), True),
    (ValueError("nope"), False),
])
def test_only_real_damage_counts_as_corruption(exc, corrupt):
    assert bridge_store._is_corruption(exc) is corrupt


# ---------------------------------------------------------------- sessions, audit, confirmations, queue


async def test_sessions_create_touch_revoke_and_expire(store, clock):
    one = await store.create_session(3600, label="phone", ip="10.0.0.2", user_agent="UA")
    assert re.fullmatch(r"[0-9a-f]{32}", one["sid"]) and one["active"] is True
    two = await store.create_session(60, sid="fixed-sid")

    assert (await store.get_session(one["sid"]))["label"] == "phone"
    clock.advance(10)
    assert await store.touch_session(one["sid"]) is True
    assert (await store.get_session(one["sid"]))["last_seen_ts"] == T0 + 10
    assert await store.revoke_session(one["sid"]) is True
    assert await store.revoke_session(one["sid"]) is False
    assert await store.touch_session(one["sid"]) is False
    assert (await store.get_session(one["sid"]))["active"] is False

    assert [s["sid"] for s in await store.list_sessions()] == [two["sid"]]
    clock.advance(61)
    assert (await store.get_session("fixed-sid"))["active"] is False
    assert await store.list_sessions() == []
    assert len(await store.list_sessions(include_inactive=True)) == 2
    assert await store.revoke_all_sessions() == 1
    assert await store.get_session("unknown") is None


async def test_audit_rows_newest_first_with_prefix(store):
    await store.audit("owner", "auth.login_failed", {"reason": "bad_totp"}, "10.0.0.2")
    await store.audit("owner", "auth.login", "ok", "10.0.0.2")
    await store.audit("ui", "confirm.allow", "tool=send_email")

    rows = await store.recent_audit(action_prefix="auth.")
    assert [r["action"] for r in rows] == ["auth.login", "auth.login_failed"]
    assert json.loads(rows[1]["detail"]) == {"reason": "bad_totp"} and rows[1]["ip"] == "10.0.0.2"
    assert len(await store.recent_audit()) == 3
    assert await store.recent_audit(action_prefix="auth_") == []  # '_' is not a wildcard


async def test_confirmations_lifecycle(store, clock):
    rec = await store.add_confirmation({"id": "c1", "tool": "send_email", "summary": "mail Ali",
                                        "risk": "high", "expires_ts": T0 + 60})
    assert rec["status"] == "pending" and rec["expires_ts"] == T0 + 60
    assert rec["payload"]["summary"] == "mail Ali"
    again = await store.add_confirmation({"id": "c1", "tool": "other"})
    assert again["tool"] == "send_email"  # a repeated id keeps the first record
    await store.add_confirmation({"id": "c2", "tool": "run_cmd_command"}, ttl_s=30)

    assert [c["id"] for c in await store.list_confirmations()] == ["c1", "c2"]
    decided = await store.resolve_confirmation("c1", True, by="owner")
    assert decided["status"] == "allowed" and decided["decided_by"] == "owner"
    assert await store.resolve_confirmation("c1", False, by="owner") is None

    clock.advance(31)
    assert await store.list_confirmations() == []
    assert await store.resolve_confirmation("c2", True, by="owner") is None
    assert await store.expire_confirmations() == 1
    assert (await store.get_confirmation("c2"))["status"] == "expired"
    assert len(await store.list_confirmations(pending_only=False)) == 2


async def test_pending_commands_are_fifo_and_expire(store, clock):
    await store.push_pending("chat", {"id": "u1", "text": "one"})
    await store.push_pending("settings_update", {"voice": "Charon"}, ttl_s=1)
    await store.push_pending("chat", "legacy string")
    clock.advance(2)

    assert await store.count_pending() == 2
    popped = await store.pop_pending()
    assert [(p["type"], p["payload"]) for p in popped] == [
        ("chat", {"id": "u1", "text": "one"}), ("chat", "legacy string")]
    assert await store.pop_pending() == [] and await store.count_pending() == 0


# ---------------------------------------------------------------- seen ids and notifications (v4)


async def test_seen_ids_catch_replays_within_their_ttl(store, clock):
    assert await store.mark_seen(SEEN_DESKTOP, "e1") is True
    assert await store.mark_seen(SEEN_DESKTOP, "e1") is False
    assert await store.mark_seen(SEEN_NOTIFY, "e1") is True  # scopes are separate
    clock.advance(301)
    assert await store.mark_seen(SEEN_NOTIFY, "e1") is True  # nonces: 300 s
    assert await store.mark_seen(SEEN_DESKTOP, "e1") is False
    clock.advance(DAY)
    assert await store.mark_seen(SEEN_DESKTOP, "e1") is True  # envelope ids: 24 h
    assert await store.mark_seen("custom", "x", ttl_s=5) is True
    clock.advance(6)
    assert await store.prune_seen() == 2  # the nonce and the custom id; e1 is fresh again


async def test_notifications_stay_until_acknowledged(store, clock):
    status, note = await store.add_notification({"id": "r1", "description": "Call Ali",
                                                 "source": "reminder", "ts": T0})
    assert status == "new" and note["id"] == "r1"
    status, _ = await store.add_notification({"id": "r1", "description": "changed"})
    assert status == "exists"  # never delivered twice
    clock.advance(1)
    _, second = await store.add_notification({"description": "Meeting"})

    assert [n["id"] for n in await store.list_notifications()] == [second["id"], "r1"]
    assert await store.ack_notification("r1") is True
    assert await store.ack_notification("r1") is False
    assert [n["id"] for n in await store.list_notifications()] == [second["id"]]
    assert len(await store.list_notifications(include_acked=True)) == 2
    record = await store.get_notification("r1")
    assert record["acked_by"] == "ui" and record["kind"] == "reminder_due"
    assert record["payload"]["description"] == "Call Ali"


async def test_init_snapshot_uses_the_contract_sizes(store):
    for n in range(40):
        await store.append_event(EVENT_LOG, {"n": n})
        await store.append_event(EVENT_MEMORY, {"n": n})
        await store.append_event(EVENT_TOOL, {"n": n})
    for n in range(110):
        await store.upsert_message({"id": f"m{n}", "role": "user", "text": "x", "ts": T0 + n})
    await store.kv_set("muted", True)

    snap = await store.init_snapshot(["muted", "location"])
    assert len(snap["messages"]) == 100 and snap["messages"][-1]["id"] == "m109"
    assert [len(snap[k]) for k in ("logs", "memories", "tool_logs")] == [30, 10, 20]
    assert snap["logs"][-1] == {"n": 39}
    assert snap["kv"] == {"muted": True}
    assert snap["settings"] == {} and snap["notifications"] == []


# ---------------------------------------------------------------- stats CLI and backup


async def test_stats_cli_prints_counts_never_content(db_path, clock, capsys):
    store = BridgeStore(db_path, clock=clock)
    await store.upsert_message({"id": "m1", "role": "agent", "text": "PRIVATE-TEXT-123"})
    await store.upsert_message({"id": "m2", "role": "user", "text": "PRIVATE-TEXT-456"})
    await store.kv_set("location", {"city": "PRIVATE-CITY"})

    assert bridge_store.main(["stats", "--db", str(db_path)]) == 0  # next to the open store
    await store.close()
    out = capsys.readouterr().out
    assert "table messages: 2" in out and "table kv: 1" in out and "schema_version: 1" in out
    assert "messages role assistant: 1" in out and "messages role user: 1" in out
    assert "PRIVATE" not in out and "role agent" not in out

    assert bridge_store.main(["stats", "--db", str(db_path.with_name("missing.db"))]) == 1


async def test_stats_cli_runs_as_a_module(db_path, clock):
    store = BridgeStore(db_path, clock=clock)
    await store.upsert_message({"id": "m1", "role": "user", "text": "x"})
    await store.close()

    proc = subprocess.run([sys.executable, "-m", "src.core.bridge_store", "stats", "--db", str(db_path)],
                          cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "messages role user: 1" in proc.stdout


async def test_backup_vps_online_copy_works_on_the_live_db(store, db_path, tmp_path):
    """Runs backup_vps.sh's own SQLITE_PY snippet (online backup + integrity_check) while the
    store holds the database open in WAL mode."""
    await store.upsert_message({"id": "m1", "role": "user", "text": "x"})
    await store.append_event(EVENT_LOG, {"text": "y"})
    script = (ROOT / "deploy" / "hostinger" / "backup_vps.sh").read_text(encoding="utf-8")
    snippet = re.search(r"SQLITE_PY='(.*?)'\n", script, re.S).group(1)
    copy_path = tmp_path / "copy.db"

    proc = subprocess.run([sys.executable, "-c", snippet, str(db_path), str(copy_path)],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0 and proc.stdout.strip() == "integrity_check=ok", proc.stderr
    assert _query(copy_path, "SELECT id FROM messages") == [("m1",)]


def test_deploy_files_put_bridge_db_on_its_volume():
    compose = yaml.safe_load((ROOT / "deploy" / "hostinger" / "docker-compose.yml")
                             .read_text(encoding="utf-8"))
    bridge = compose["services"]["ui-bridge"]
    assert bridge["environment"]["BRIDGE_DB_PATH"] == "/app/data/bridge.db"
    assert "jarvis_bridge_data:/app/data" in bridge["volumes"]
    assert compose["volumes"]["jarvis_bridge_data"] == {"name": "jarvis_bridge_data"}
    dockerfile = (ROOT / "deploy" / "hostinger" / "Dockerfile.bridge").read_text(encoding="utf-8")
    assert re.search(r"mkdir -p [^\n]*\\\n[^\n]* /app/data\b", dockerfile)
    backup = (ROOT / "deploy" / "hostinger" / "backup_vps.sh").read_text(encoding="utf-8")
    assert "/app/data/bridge.db" in backup


# ---------------------------------------------------------------- ui_bridge on the store


@pytest.fixture
async def bridge(monkeypatch, tmp_path, clock):
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("room") or kwargs.get("to")))

    ns = SimpleNamespace(module=module, emitted=emitted, path=tmp_path / "bridge" / "bridge.db",
                         clock=clock, store=None)

    def restart() -> None:
        """A new store object on the same file and fresh volatile state, as after a restart."""
        ns.store = BridgeStore(ns.path, clock=clock)
        monkeypatch.setattr(module, "store", ns.store)
        monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())

    ns.restart = restart
    restart()
    monkeypatch.setattr(module, "VORTEX_SECURITY_TOKEN", TOKEN)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    yield ns
    await ns.store.close()


async def _notify(module: Any, event_type: str, payload: Any) -> httpx.Response:
    from services.utils.jarvis_security import security_manager  # pylint: disable=import-outside-toplevel

    body = {"type": event_type, "payload": payload}
    headers = {"X-Vortex-Token": TOKEN,
               "X-Vortex-Signature": security_manager.generate_signature(json.dumps(body, sort_keys=True))}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url="http://bridge.test") as client:
        return await client.post("/notify", json=body, headers=headers)


async def _connect_ui(ns: Any, sid: str) -> dict[str, Any]:
    ns.emitted.clear()
    assert await ns.module.handle_connect(sid, {"QUERY_STRING": f"token={TOKEN}"}, None) is True
    [(event, state, room)] = ns.emitted
    assert (event, room) == ("init_state", sid)
    return state


def _events(ns: Any, name: str) -> list[Any]:
    return [data for event, data, _room in ns.emitted if event == name]


async def test_agent_transcription_is_stored_and_emitted_as_assistant(bridge):
    resp = await _notify(bridge.module, "transcription", {
        "id": "v1", "role": "agent", "text": "Salam Sir", "is_final": True, "timestamp": T0})

    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    [msg] = _events(bridge, "new_message")
    assert msg == {"id": "v1", "role": "assistant", "text": "Salam Sir", "source": "voice",
                   "final": True, "ts": T0, "timestamp": T0, "is_final": True}
    assert _events(bridge, "transcription_update") == [{"text": "Salam Sir"}]
    assert _query(bridge.path, "SELECT role FROM messages") == [("assistant",)]

    await _notify(bridge.module, "transcription", {"id": "v1", "role": "agent", "text": "Salam!"})
    [update] = _events(bridge, "update_message")
    assert update["id"] == "v1" and update["text"] == "Salam!" and update["role"] == "assistant"


async def test_typed_exchange_survives_restart_and_reload_with_the_same_ids(bridge):
    await _connect_ui(bridge, "sid-ui")  # since C-14 only an authenticated UI socket may send ui_command
    ack = await bridge.module.handle_ui_command("sid-ui", {"type": "chat",
                                                           "payload": {"id": "u-123", "text": "hello"}})
    assert ack == {"status": "sent"}  # C-SOCK-1 ack (was 'success' before C-14)
    assert _events(bridge, "agent_command") == [{"type": "chat", "payload": {"id": "u-123", "text": "hello"}}]
    await _notify(bridge.module, "transcription", {"id": "u-123", "role": "user", "text": "hello",
                                                   "timestamp": T0})
    await _notify(bridge.module, "transcription", {"id": "a-456", "role": "assistant", "text": "Hi!",
                                                   "source": "text", "timestamp": T0 + 1})
    assert _events(bridge, "new_message")[0]["source"] == "text"  # the id of a typed chat command

    await bridge.store.close()
    bridge.restart()
    state = await _connect_ui(bridge, "sid-reload")

    assert [(m["id"], m["role"], m["text"], m["source"]) for m in state["messages"]] == [
        ("u-123", "user", "hello", "text"), ("a-456", "assistant", "Hi!", "text")]
    assert all(m["timestamp"] == m["ts"] and m["is_final"] is True for m in state["messages"])


async def test_init_state_defaults_match_the_old_in_memory_state(bridge):
    state = await _connect_ui(bridge, "sid-1")

    assert state["persona"] == state["active_persona"] == "jarvis"
    assert state["muted"] is False and state["wake_word_active"] is True
    assert state["location"] == {"city": "Detecting...", "lat": 0, "lng": 0}
    assert state["voice_match"] == 0.0
    assert (state["sim_records"], state["sim_loading"], state["sim_query_masked"],
            state["sim_status"], state["sim_message"], state["sim_panel_open"]) == (
        [], False, "", "idle", "", False)
    assert state["messages"] == state["vortex_logs"] == state["logs"] == []
    assert state["memories"] == state["tool_logs"] == state["notifications"] == []
    for key in ("speaking", "thinking", "user_speaking", "vitals", "telemetry", "runner_status",
                "last_runner_contact", "last_user_speak_pulse", "last_transcription", "server_time"):
        assert key in state
    assert "settings" not in state  # nothing stored yet: the settings panel keeps its fallbacks


async def test_every_legacy_event_still_emits_and_its_state_persists(bridge):
    sent = [
        ("persona_change", "anna", "persona_update", {"persona": "anna"}),
        ("mute_sync", True, "mute_update", True),
        ("wake_word_sync", False, "wake_word_update", False),
        ("location_sync", {"city": "Lahore", "lat": 31.5, "lng": 74.3}, "location_update", None),
        ("voice_id_sync", {"confidence": 0.87, "timestamp": T0}, "voice_match_update", None),
        ("vortex_log", {"text": "first", "category": "SYSTEM"}, "new_log", None),
        ("vortex_log", {"text": "second", "category": "SYSTEM"}, "new_log", None),
        ("memory_update", {"id": "mem1", "content": "likes tea"}, "memory_sync", None),
        ("tool_sync", {"tool": "open_app", "details": "notepad"}, "tool_update", None),
        ("sim_data_loading", {"query_masked": "0300***"}, "sim_data_loading", None),
        ("sim_data_result", {"records": [{"name": "A"}], "phone": "0300***", "status": "success",
                             "message": "1 record"}, "sim_data_result", None),
        ("sim_panel_open", {"message": "open"}, "sim_panel_open", None),
    ]
    for event_type, payload, ui_event, ui_payload in sent:
        bridge.emitted.clear()
        resp = await _notify(bridge.module, event_type, payload)
        assert resp.status_code == 200, event_type
        assert _events(bridge, ui_event) == [payload if ui_payload is None else ui_payload]

    state = await _connect_ui(bridge, "sid-a")
    expected = {
        "persona": "anna", "active_persona": "anna", "muted": True, "wake_word_active": False,
        "location": {"city": "Lahore", "lat": 31.5, "lng": 74.3}, "voice_match": 0.87,
        "sim_records": [{"name": "A"}], "sim_loading": False, "sim_query_masked": "0300***",
        "sim_status": "success", "sim_message": "1 record", "sim_panel_open": True,
        "memories": [{"id": "mem1", "content": "likes tea"}],
        "tool_logs": [{"tool": "open_app", "details": "notepad"}],
    }
    assert {k: state[k] for k in expected} == expected
    assert [log["text"] for log in state["vortex_logs"]] == ["first", "second"]
    assert state["logs"] == state["vortex_logs"]

    await bridge.store.close()
    bridge.restart()
    after = await _connect_ui(bridge, "sid-b")
    expected["sim_panel_open"] = False  # volatile, as before C-13
    assert {k: after[k] for k in expected} == expected


async def test_stored_settings_appear_in_init_state(bridge):
    await bridge.store.merge_settings({"voice": "Charon"}, by="ui")

    assert (await _connect_ui(bridge, "sid-1"))["settings"] == {"voice": "Charon"}


async def test_the_agents_settings_sync_reaches_the_ui_and_is_stored(bridge):
    """C-54 (K-40/K-46/K-61): the agent's settings_sync goes to room 'ui' as sent (it was an UNKNOWN EVENT before), and
    its settings object is stored (updated_by 'agent'): a reloaded UI gets it in init_state.settings and the next agent
    gets it as settings_bootstrap."""
    payload = {"settings": {"voice": "Puck", "wake_word": False, "muted": False, "updated_ts": T0},
               "applied": {"voice": "applied"}, "errors": {}, "pending_restart": [], "restart_required": [],
               "enforced": {"wake_word": False, "voice_id": False}, "available_voices": ["Puck"],
               "available_languages": ["auto"], "ts": T0}
    resp = await _notify(bridge.module, "settings_sync", payload)

    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    assert _events(bridge, "settings_sync") == [payload]
    assert await bridge.store.get_settings() == payload["settings"]
    assert _query(bridge.path, "SELECT DISTINCT updated_by FROM settings") == [("agent",)]
    assert (await _connect_ui(bridge, "sid-ui"))["settings"] == payload["settings"]
    # a flat settings object (K-02) is stored without the keys that describe the update
    await _notify(bridge.module, "settings_sync", {"persona": "anna", "applied": True, "errors": {}, "ts": T0})
    stored_now = await bridge.store.get_settings()
    assert stored_now["persona"] == "anna" and stored_now["voice"] == "Puck" and "applied" not in stored_now
    # settings.saved (the fields the agent set; its other values are defaults) is replaced by each settings_sync, never
    # merged: the next settings_bootstrap names only what the agent saved last
    await _notify(bridge.module, "settings_sync", {"settings": {"voice": "Puck", "saved": ["voice", "wake_word"],
                                                                "updated_ts": T0 + 1}})
    await _notify(bridge.module, "settings_sync", {"settings": {"voice": "Aoede", "saved": ["persona"],
                                                                "updated_ts": T0 + 2}})
    stored_now = await bridge.store.get_settings()
    assert (stored_now["saved"], stored_now["voice"], stored_now["updated_ts"]) == (["persona"], "Aoede", T0 + 2)


async def test_unknown_socket_token_is_refused_without_init_state(bridge):
    assert await bridge.module.handle_connect("sid-x", {"QUERY_STRING": "token=wrong"}, None) is False
    assert await bridge.module.handle_connect("sid-y", {}, {"token": "wrong"}) is False
    assert not bridge.emitted
    assert await bridge.module.handle_connect("sid-z", {}, {"token": TOKEN}) is True


async def test_messages_endpoint_pages_history(bridge):
    for n in range(5):
        await bridge.store.upsert_message({"id": f"m{n}", "role": "user", "text": str(n), "ts": T0 + n})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.module.app),
                                 base_url="http://bridge.test") as client:
        assert (await client.get("/messages")).status_code == 401
        auth = {"X-Vortex-Token": TOKEN}
        first = (await client.get("/messages", params={"limit": 2}, headers=auth)).json()
        assert [m["id"] for m in first["messages"]] == ["m3", "m4"] and first["has_more"] is True
        assert parse_message_cursor(first["next_cursor"])[0] == T0 + 3
        assert all("timestamp" in m and "is_final" in m for m in first["messages"])

        by_before = (await client.get("/messages", params={"limit": 2, "before": T0 + 3},
                                      headers=auth)).json()
        by_cursor = (await client.get("/messages", params={"limit": 2, "cursor": first["next_cursor"]},
                                      headers=auth)).json()
        assert by_before == by_cursor
        assert [m["id"] for m in by_before["messages"]] == ["m1", "m2"]

        last = (await client.get("/messages", params={"limit": 2, "before": T0 + 1}, headers=auth)).json()
        assert last == {"messages": [last["messages"][0]], "has_more": False, "next_cursor": None}
        assert last["messages"][0]["id"] == "m0"

        everything = (await client.get("/messages", params={"limit": 1000}, headers=auth)).json()
        assert len(everything["messages"]) == 5
        for bad in ("abc", f"{T0!r}:x", "nan:1"):
            assert (await client.get("/messages", params={"cursor": bad}, headers=auth)).status_code == 400
        assert (await client.get("/messages", params={"before": "nan"}, headers=auth)).status_code == 400


async def test_messages_endpoint_cursor_keeps_messages_that_share_a_ts(bridge):
    for msg_id, ts in (("m1", T0), ("m2", T0), ("m3", T0 + 1)):
        await bridge.store.upsert_message({"id": msg_id, "role": "user", "text": msg_id, "ts": ts})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.module.app),
                                 base_url="http://bridge.test") as client:
        auth = {"X-Vortex-Token": TOKEN}
        first = (await client.get("/messages", params={"limit": 2}, headers=auth)).json()
        older = (await client.get("/messages", params={"limit": 2, "cursor": first["next_cursor"]},
                                  headers=auth)).json()

    assert [m["id"] for m in first["messages"]] == ["m2", "m3"] and first["has_more"] is True
    assert [m["id"] for m in older["messages"]] == ["m1"]
    assert older["has_more"] is False and older["next_cursor"] is None


async def test_a_sim_lookup_whose_result_was_lost_is_reported_idle(bridge):
    await _notify(bridge.module, "sim_data_loading", {"query_masked": "0300***"})
    state = await _connect_ui(bridge, "sid-1")
    assert (state["sim_loading"], state["sim_status"]) == (True, "loading")  # still running

    # No sim_data_result within SIM_LOADING_MAX_S: the agent restarted or the notify was dropped.
    bridge.module.bridge_manager.sim_loading_since -= bridge.module.SIM_LOADING_MAX_S + 1
    state = await _connect_ui(bridge, "sid-2")
    assert (state["sim_loading"], state["sim_status"], state["sim_message"],
            state["sim_query_masked"], state["sim_records"]) == (False, "idle", "", "0300***", [])

    # The bridge restarted mid-lookup, so a result posted meanwhile was lost.
    await _notify(bridge.module, "sim_data_loading", {"query_masked": "0311***"})
    await bridge.store.close()
    bridge.restart()
    state = await _connect_ui(bridge, "sid-3")
    assert (state["sim_loading"], state["sim_status"], state["sim_query_masked"]) == (
        False, "idle", "0311***")


async def test_a_failing_store_never_blocks_live_events(bridge, monkeypatch):
    async def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise sqlite3.OperationalError("disk I/O error")

    for name in ("upsert_message", "kv_set", "append_event", "init_snapshot"):
        monkeypatch.setattr(bridge.store, name, broken)

    await _notify(bridge.module, "transcription", {"id": "v9", "role": "agent", "text": "still here"})
    await _notify(bridge.module, "mute_sync", True)
    await _notify(bridge.module, "vortex_log", {"text": "log"})

    [msg] = _events(bridge, "new_message")
    assert msg["id"] == "v9" and msg["role"] == "assistant" and msg["timestamp"] == msg["ts"]
    assert _events(bridge, "mute_update") == [True] and _events(bridge, "new_log") == [{"text": "log"}]
    state = await _connect_ui(bridge, "sid-1")
    # K-42 v50: an unreadable store leaves the stored lists out (a UI keeps what it shows) instead of sending them empty
    assert state["persona"] == "jarvis" and "messages" not in state and state["degraded"] == ["store"]


async def test_retention_task_prunes_with_the_env_value(bridge, monkeypatch):
    for n in range(3):
        await bridge.store.append_event(EVENT_LOG, {"n": n})
    bridge.clock.advance(2 * DAY)
    await bridge.store.append_event(EVENT_LOG, {"n": "fresh"})
    monkeypatch.setenv("BRIDGE_EVENT_RETENTION_DAYS", "1")

    task = asyncio.create_task(bridge.module.store_retention_loop())
    try:
        for _ in range(200):
            if await bridge.store.recent_events(EVENT_LOG) == [{"n": "fresh"}]:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await bridge.store.recent_events(EVENT_LOG) == [{"n": "fresh"}]


async def test_the_retention_loop_keeps_the_newest_200_memory_events(bridge, monkeypatch):
    """K-07 (C-86): with C-52 every saved message adds a memory event, so the hourly loop caps them at the newest
    MEMORY_MAX_ROWS (even with BRIDGE_EVENT_RETENTION_DAYS=0); init_state.memories still shows the last 10, other
    kinds are untouched, and memory_cleared still empties them."""
    assert (bridge_store.MEMORY_MAX_ROWS, bridge_store.INIT_MEMORIES) == (200, 10)
    monkeypatch.delenv("BRIDGE_EVENT_RETENTION_DAYS", raising=False)
    await bridge.store.append_events(EVENT_MEMORY, [{"id": f"m{n}", "content": "x"} for n in range(230)])
    await bridge.store.append_events(EVENT_LOG, [{"text": f"l{n}"} for n in range(5)])
    await bridge.store.append_events(EVENT_TOOL, [{"tool": f"t{n}"} for n in range(250)])

    task = asyncio.create_task(bridge.module.store_retention_loop())
    try:
        for _ in range(200):
            if len(await bridge.store.recent_events(EVENT_MEMORY, 1000)) == 200:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    kept = await bridge.store.recent_events(EVENT_MEMORY, 1000)
    assert [row["id"] for row in kept] == [f"m{n}" for n in range(30, 230)]  # the newest 200, oldest first
    assert len(await bridge.store.recent_events(EVENT_LOG, 100)) == 5
    assert len(await bridge.store.recent_events(EVENT_TOOL, 1000)) == 250
    state = await _connect_ui(bridge, "sid-mem")
    assert [row["id"] for row in state["memories"]] == [f"m{n}" for n in range(220, 230)]
    await _notify(bridge.module, "memory_cleared", {"scope": "short_term", "ts": T0})
    assert await bridge.store.recent_events(EVENT_MEMORY, 1000) == []
    source = (ROOT / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8")
    loop = source.partition("async def store_retention_loop")[2].partition("async def _expire_ui_sessions")[0]
    assert "cap_events(EVENT_MEMORY, MEMORY_MAX_ROWS)" in loop


async def test_lifespan_opens_and_closes_the_store(bridge, monkeypatch):
    async def idle() -> None:
        await asyncio.Event().wait()

    for name in ("vitals_heartbeat", "telemetry_heartbeat", "user_speaking_monitor",
                 "store_retention_loop"):
        monkeypatch.setattr(bridge.module, name, idle)

    async with bridge.module.lifespan(bridge.module.app):
        for _ in range(200):
            if bridge.store._conn is not None:
                break
            await asyncio.sleep(0.01)
        assert bridge.store._conn is not None and bridge.path.is_file()
    assert bridge.store._conn is None
