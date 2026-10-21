"""C-86 review: the Tasks panel's Cancel and Refresh reach the agent (K-03 task_cancel {id, source}, tasks_refresh).

Since the bridge routes task_sync, the panel lists the scheduled tasks, but the agent ignored both buttons (they were
in FORWARDED_UNHANDLED), so a confirmed Cancel did nothing. task_cancel now cancels a task that has not finished
(cancel_scheduled_task's rule) in a worker thread and publishes the pending list again from the event loop, with
due_at in epoch seconds; tasks_refresh publishes it. A fake task manager and a recorded notify_event: nothing touches
tasks.db, a network or the bridge. Logs carry ids, outcomes and counts, never a task's text (K-29).
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.ai_core import agent_loops
from services.utils import jarvis_bridge, task_items
from services.utils.ui_command_registry import UI_COMMANDS, CommandContext, dispatch

KARACHI = timezone(timedelta(hours=5))
SECRET = "PRIVATE-TASK-TEXT bank ko call"


def _at(*args: int) -> float:
    return datetime(*args, tzinfo=KARACHI).timestamp()


class FakeTasks:
    """The TaskManager methods the commands use (K-33), recording the thread each call ran on."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["id"]: dict(row) for row in rows}
        self.cancelled: list[int] = []
        self.threads: list[str] = []
        self.broken: str | None = None  # a method name that raises sqlite3.OperationalError

    def _call(self, name: str) -> None:
        self.threads.append(threading.current_thread().name)
        if self.broken == name:
            raise sqlite3.OperationalError("database is locked")

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        self._call("get_task")
        row = self.rows.get(task_id)
        return dict(row) if row else None

    def cancel_task(self, task_id: int) -> None:
        self._call("cancel_task")
        self.cancelled.append(task_id)
        self.rows[task_id]["status"] = "cancelled"

    def list_pending_tasks(self) -> list[dict[str, Any]]:
        self._call("list_pending_tasks")
        return [dict(row) for row in self.rows.values() if row["status"] == "pending"]


@pytest.fixture
def tasks(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)  # the task manager's default zone, Asia/Karachi
    task_items.zone_named.cache_clear()
    fake = FakeTasks([
        {"id": 7, "description": SECRET, "scheduled_time": "2026-10-22T09:00:00", "status": "pending",
         "kind": "task", "action": None, "action_args": None},
        {"id": 8, "description": "Ali ko call", "scheduled_time": "2026-10-23T18:30:00", "status": "pending",
         "kind": None},
        {"id": 9, "description": "ho gaya", "scheduled_time": "2026-10-20T08:00:00", "status": "completed",
         "kind": "task"},
    ])
    monkeypatch.setattr(agent_loops, "_task_manager", lambda: fake)
    return fake


@pytest.fixture
def sent(monkeypatch):
    """agent_loops.notify_event recorded; it answers 'delivered'."""
    calls: list[tuple[str, Any]] = []

    async def fake_notify(event_type: str, payload: Any) -> str:
        calls.append((event_type, payload))
        return jarvis_bridge.NOTIFY_DELIVERED

    monkeypatch.setattr(agent_loops, "notify_event", fake_notify)
    return calls


@pytest.fixture
def loops_log():
    """The JARVIS-LOOPS logger's records (setup_logger loggers do not propagate)."""
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler, logger = _Keep(level=logging.DEBUG), agent_loops.logger
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def _text(records: list[logging.LogRecord]) -> str:
    return "\n".join(record.getMessage() for record in records)


ITEM_8 = {"id": 8, "description": "Ali ko call", "due_at": _at(2026, 10, 23, 18, 30), "source": "task",
          "kind": "task", "status": "pending"}
ITEM_7 = {"id": 7, "description": SECRET, "due_at": _at(2026, 10, 22, 9, 0), "source": "task", "kind": "task",
          "status": "pending"}


def test_both_buttons_have_handlers_and_are_no_longer_ignored():
    assert {"task_cancel", "tasks_refresh"} <= set(UI_COMMANDS)
    assert not {"task_cancel", "tasks_refresh"} & agent_loops.FORWARDED_UNHANDLED


async def test_the_jobs_listener_hands_a_cancel_to_its_handler(tasks, sent, monkeypatch):
    """What failed before: the listener dropped task_cancel at DEBUG (a bridge-forwarded type without a handler)."""
    monkeypatch.setattr(agent_loops.socketio, "AsyncClient", MagicMock(return_value=MagicMock()))
    listener = agent_loops.UIBridgeListener(MagicMock())
    await listener._handle_command({"type": "task_cancel", "payload": {"id": "7", "source": "task"}})
    assert tasks.cancelled == [7] and sent == [("task_sync", {"tasks": [ITEM_8]})]


async def test_a_cancel_runs_in_a_worker_thread_and_publishes_the_new_list(tasks, sent, loops_log):
    assert await dispatch("task_cancel", {"id": "7", "source": "task"}, CommandContext()) is True
    assert tasks.cancelled == [7]
    loop_thread = threading.current_thread().name
    assert tasks.threads and all(name != loop_thread for name in tasks.threads)  # sqlite never on the event loop
    [(event_type, payload)] = sent
    assert event_type == "task_sync" and payload == {"tasks": [ITEM_8]}
    assert all(isinstance(item["due_at"], float) for item in payload["tasks"])  # the UI reads epoch numbers only
    assert "TASK cancel via UI: id=7 cancelled; 1 pending task(s) published (delivered)" in _text(loops_log)
    assert SECRET not in _text(loops_log)


@pytest.mark.parametrize("task_id, outcome", [("404", "not found"), (9, "already completed")])
async def test_an_unknown_or_finished_task_is_not_cancelled_but_the_list_is_published(tasks, sent, loops_log,
                                                                                      task_id, outcome):
    await dispatch("task_cancel", {"id": task_id, "source": "task"}, CommandContext())
    assert tasks.cancelled == []
    assert sent == [("task_sync", {"tasks": [ITEM_7, ITEM_8]})]  # a stale row leaves the panel
    assert f"cancel via UI: id={int(task_id)} {outcome}; 2 pending task(s) published" in _text(loops_log)
    assert SECRET not in _text(loops_log)


@pytest.mark.parametrize("payload", [
    {"id": "abc", "source": "task"}, {"id": -1, "source": "task"}, {"id": 0}, {"id": True, "source": "task"},
    {"id": "1" * 19, "source": "task"}, {"id": "٣", "source": "task"}, {"source": "task"}, {"id": None},
    "7", None, ["7"], {"id": "7", "source": "reminder"}, {"id": "7", "source": "something"},
])
async def test_bad_ids_and_reminders_change_nothing(tasks, sent, loops_log, payload):
    await dispatch("task_cancel", payload, CommandContext())
    assert tasks.cancelled == [] and tasks.threads == [] and sent == []
    assert "TASK cancel via UI" in _text(loops_log)


async def test_a_missing_source_means_a_task_and_an_int_id_works(tasks, sent):
    await dispatch("task_cancel", {"id": 8}, CommandContext())
    assert tasks.cancelled == [8] and sent == [("task_sync", {"tasks": [ITEM_7]})]


async def test_refresh_publishes_the_pending_list(tasks, sent, loops_log):
    assert await dispatch("tasks_refresh", {}, CommandContext()) is True
    assert sent == [("task_sync", {"tasks": [ITEM_7, ITEM_8]})] and tasks.cancelled == []
    assert "TASKS refresh via UI: 2 pending task(s) published (delivered)" in _text(loops_log)
    await dispatch("tasks_refresh", None, CommandContext())  # id-less: never a 'duplicate'
    assert len(sent) == 2


async def test_a_broken_task_store_is_logged_and_never_raised(tasks, sent, loops_log):
    tasks.broken = "get_task"
    await dispatch("task_cancel", {"id": "7", "source": "task"}, CommandContext())
    assert tasks.cancelled == [] and sent == []
    assert "TASK cancel via UI: id=7 failed (OperationalError)" in _text(loops_log)
    tasks.broken = "list_pending_tasks"
    await dispatch("tasks_refresh", {}, CommandContext())
    await dispatch("task_cancel", {"id": "8", "source": "task"}, CommandContext())
    assert sent == [] and tasks.cancelled == [8]
    text = _text(loops_log)
    assert "TASKS refresh via UI: the list was not published (OperationalError)" in text
    assert "TASK cancel via UI: id=8 cancelled; the list was not published (OperationalError)" in text
