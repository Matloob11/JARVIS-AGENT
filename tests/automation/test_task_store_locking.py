"""X-66: tasks.db WAL + busy_timeout; concurrent writers lose no rows."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from services.automation import jarvis_task_manager as tm_mod


@pytest.fixture
def tasks_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tasks.db")
    monkeypatch.setattr(tm_mod, "DB_PATH", db_path)
    manager = tm_mod.TaskManager()
    monkeypatch.setattr(tm_mod, "task_manager", manager)
    return db_path


def test_sqlite_wal_and_busy_timeout(tasks_db):
    with sqlite3.connect(tasks_db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"

    conn = tm_mod._connect_db()
    try:
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert busy == 5000


def test_concurrent_add_tasks_lose_no_rows(tasks_db):
    errors: list[BaseException] = []
    base = datetime(2099, 1, 1, 0, 0, 0)
    rounds = 25

    def worker(tag: str) -> None:
        try:
            mgr = tm_mod.TaskManager()
            for i in range(rounds):
                mgr.add_task(f"{tag}-{i}", base + timedelta(minutes=i))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("worker_a", "worker_b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors

    with sqlite3.connect(tasks_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 2 * rounds


def test_get_due_tasks_uses_scheduling_now(tasks_db, monkeypatch):
    manager = tm_mod.TaskManager()
    past = datetime(2026, 1, 1, 8, 0, 0)
    future = datetime(2099, 1, 1, 8, 0, 0)
    manager.add_task("due soon", past)
    manager.add_task("later", future)

    monkeypatch.setattr(tm_mod, "scheduling_now", lambda: datetime(2026, 1, 1, 12, 0, 0))
    due = manager.get_due_tasks()
    assert len(due) == 1
    assert due[0]["description"] == "due soon"
