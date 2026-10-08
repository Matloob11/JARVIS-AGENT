"""X-32: TaskStore v2 — migration, parse_when, schedule/cancel tools."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import services.automation.jarvis_task_manager as tm


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "tasks.db"
    monkeypatch.setattr(tm, "DB_PATH", str(db))
    # Rebuild manager against tmp DB
    mgr = tm.TaskManager()
    monkeypatch.setattr(tm, "task_manager", mgr)
    return db, mgr


def test_migrate_6_column_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                action TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO tasks (description, scheduled_time, action, created_at) VALUES (?,?,?,?)",
            ("keep me", datetime.now().isoformat(), None, datetime.now().isoformat()),
        )
        conn.commit()

    monkeypatch.setattr(tm, "DB_PATH", str(db))
    mgr = tm.TaskManager()
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(tasks)")}
    for name in ("kind", "action_args", "result", "attempts", "updated_at", "confirm_id"):
        assert name in cols
    rows = mgr.list_pending_tasks()
    assert len(rows) == 1
    assert rows[0]["description"] == "keep me"


@pytest.mark.parametrize(
    "text,delta_min",
    [
        ("in 10 minutes", 10),
        ("das minute baad", 10),
        ("in 2 hours", 120),
    ],
)
def test_parse_when_relative(text: str, delta_min: int) -> None:
    now = datetime(2026, 10, 8, 12, 0, 0)
    got = tm.parse_when(text, now=now)
    assert got is not None
    assert abs((got - now).total_seconds() - delta_min * 60) < 2


def test_parse_when_unit_without_number_returns_none() -> None:
    assert tm.parse_when("minutes baad") is None


def test_parse_when_hhmm_and_kal() -> None:
    now = datetime(2026, 10, 8, 12, 0, 0)
    t = tm.parse_when("14:30", now=now)
    assert t is not None and t.hour == 14 and t.minute == 30
    kal = tm.parse_when("kal 09:00", now=now)
    assert kal is not None and kal.date() == (now.date() + timedelta(days=1))


@pytest.mark.asyncio
async def test_schedule_json_error_and_forbidden(tmp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, mgr = tmp_db

    import inspect

    inner = inspect.unwrap(tm.schedule_task)
    bad = await inner("in 1 minute", "x", action="get_weather", action_args_json="[1,2]")
    assert bad["status"] == "error"
    assert "object" in bad["message"].lower() or "JSON" in bad["message"]

    monkeypatch.setattr(tm, "_schedule_risk", lambda _a: "forbidden")
    # Re-bind: schedule_task body calls _schedule_risk from module
    forbidden = await inner("in 1 minute", "x", action="confirm_action", action_args_json="{}")
    assert forbidden["status"] == "error"
    assert forbidden.get("error_code") == "FORBIDDEN_ACTION"


@pytest.mark.asyncio
async def test_schedule_and_cancel(tmp_db) -> None:
    import inspect

    inner = inspect.unwrap(tm.schedule_task)
    cancel = inspect.unwrap(tm.cancel_scheduled_task)

    res = await inner(
        "in 1 minute",
        "weather",
        action="get_weather",
        action_args_json=json.dumps({"city": "Lahore"}),
    )
    assert res["status"] == "success"
    tid = res["task_id"]
    task = tm.task_manager.get_task(tid)
    assert task is not None
    assert task["action"] == "get_weather"
    assert task["action_args"] == {"city": "Lahore"}

    c = await cancel(tid)
    assert c["status"] == "success"
    assert tid not in [t["id"] for t in tm.task_manager.list_pending_tasks()]
