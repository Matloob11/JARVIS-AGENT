"""C-86 review: the dashboard's task items (services/utils/task_items.py; K-02 task_sync, K-33 task_store_v2).

The task manager keeps scheduled_time as a naive wall-clock ISO string in TZ (default Asia/Karachi) and its task_sync
sends that string as due_at, while the dashboard reads due_at only as an epoch number (anything else becomes 'due
now'). These tests pin the reading the bridge and the agent share: numbers as they are, an offset as written, a naive
time in TZ, and the slim bridge image (no tz database) still on UTC+05:00. Pure functions only: no store, no network.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import pytest

from services.utils import task_items
from services.utils.task_items import (
    PKT,
    task_due_epoch,
    task_item,
    task_item_from_row,
    task_items as items_of,
    task_sync_payload,
    task_zone,
)

KARACHI = timezone(timedelta(hours=5))


def _at(*args: int, tz: Any = KARACHI) -> float:
    return datetime(*args, tzinfo=tz).timestamp()


@pytest.fixture(autouse=True)
def zones(monkeypatch):
    """TZ unset (the task manager's default zone) and a fresh zone cache before and after every test."""
    monkeypatch.delenv("TZ", raising=False)
    task_items.zone_named.cache_clear()
    yield
    task_items.zone_named.cache_clear()


@pytest.fixture
def no_tz_database(monkeypatch):
    """The slim bridge image: python:3.12-slim without tzdata, so ZoneInfo cannot load any zone."""
    def missing(name: str) -> Any:
        raise ZoneInfoNotFoundError(f"No time zone found with key {name}")

    monkeypatch.setattr(task_items, "ZoneInfo", missing)


@pytest.mark.parametrize("due, expected", [
    ("2026-10-22T09:00:00", _at(2026, 10, 22, 9, 0)),       # the task manager's own string (scheduled_time.isoformat)
    ("2026-10-22 09:00", _at(2026, 10, 22, 9, 0)),
    ("2026-10-22T09:00:30.250000", _at(2026, 10, 22, 9, 0, 30) + 0.25),
    ("2026-10-22T04:00:00Z", _at(2026, 10, 22, 9, 0)),      # an offset is kept as written
    ("2026-10-22T09:00:00+05:00", _at(2026, 10, 22, 9, 0)),
    ("2026-10-22T09:00:00+00:00", _at(2026, 10, 22, 9, 0, tz=timezone.utc)),
    ("2026-10-22", _at(2026, 10, 22, 0, 0)),                 # a bare date is its midnight, as parse_when reads it
    (1792641600, 1792641600.0), (1792641600.5, 1792641600.5),
    (1792641600123, 1792641600.123),                         # milliseconds
    ("1792641600", 1792641600.0), (" 1792641600123 ", 1792641600.123),
])
def test_a_due_time_becomes_epoch_seconds(due: Any, expected: float):
    assert task_due_epoch(due) == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize("due", [None, "", "   ", "kal subah", "09:00", True, False, 0, -5, float("nan"),
                                 float("inf"), "nan", "1e400", 10 ** 400, [1], {"at": 1}, "0001-01-01T00:00:00"])
def test_an_unreadable_due_time_is_none(due: Any):
    assert task_due_epoch(due) is None


def test_the_finding_scenario_kal_0900_is_tomorrow_not_now():
    """schedule_task('kal 09:00', ...) stores '2026-10-22T09:00:00' (TZ unset: Asia/Karachi); the dashboard must get
    the epoch of 09:00 PKT that day, never a string it reads as the moment the list arrived."""
    item = task_item({"id": 7, "description": "Ali ko call", "due_at": "2026-10-22T09:00:00", "source": "task",
                      "kind": "task", "status": "pending"})
    assert item == {"id": 7, "description": "Ali ko call", "due_at": _at(2026, 10, 22, 9, 0), "source": "task",
                    "kind": "task", "status": "pending"}
    assert isinstance(item["due_at"], float) and item["due_at"] < 1e12  # the UI's toEpochMs reads it as seconds


def test_tz_is_the_task_managers_zone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    assert task_due_epoch("2026-10-22T09:00:00") == _at(2026, 10, 22, 9, 0, tz=timezone.utc)
    monkeypatch.setenv("TZ", ":Asia/Karachi")  # a POSIX leading colon
    assert task_due_epoch("2026-10-22T09:00:00") == _at(2026, 10, 22, 9, 0)
    monkeypatch.setenv("TZ", "Not/AZone")  # unknown: the default zone, like scheduling_now()
    assert task_due_epoch("2026-10-22T09:00:00") == _at(2026, 10, 22, 9, 0)
    assert task_due_epoch("2026-10-22T09:00:00", timezone.utc) == _at(2026, 10, 22, 9, 0, tz=timezone.utc)


def test_without_a_tz_database_the_default_zone_is_still_utc_plus_5(no_tz_database, monkeypatch):
    assert task_zone() is PKT
    assert task_due_epoch("2026-10-22T09:00:00") == _at(2026, 10, 22, 9, 0)
    assert task_due_epoch("2026-01-15T23:30:00") == _at(2026, 1, 15, 23, 30)  # no daylight saving time either way
    monkeypatch.setenv("TZ", "UTC")
    assert task_zone() is timezone.utc
    assert task_due_epoch("2026-10-22T09:00:00") == _at(2026, 10, 22, 9, 0, tz=timezone.utc)
    monkeypatch.setenv("TZ", "Europe/London")  # cannot be loaded here: the default zone
    assert task_zone() is PKT


def test_an_item_folds_the_aliases_into_due_at_and_keeps_every_other_field():
    assert task_item({"id": 1, "scheduled_time": "2026-10-22T09:00:00", "extra": [1]}) == {
        "id": 1, "extra": [1], "due_at": _at(2026, 10, 22, 9, 0)}
    assert task_item({"id": 2, "dueAt": 1792641600, "scheduled_time": "2026-10-22T09:00:00"}) == {
        "id": 2, "due_at": 1792641600.0}
    assert task_item({"id": 3, "due_at": None, "scheduled_time": "2026-10-22T09:00:00"})["due_at"] == (
        _at(2026, 10, 22, 9, 0))  # the first one that is set
    assert task_item({"id": 4, "due_at": "soon"}) == {"id": 4, "due_at": None}
    assert task_item({"id": 5, "description": "no time"}) == {"id": 5, "description": "no time"}
    for not_an_object in (None, "task", 7, ["id", 1]):
        assert task_item(not_an_object) is None


def test_a_payload_keeps_its_shape():
    raw = [{"id": 1, "due_at": "2026-10-22T09:00:00"}, "junk", {"id": 2}]
    converted = [{"id": 1, "due_at": _at(2026, 10, 22, 9, 0)}, {"id": 2}]
    assert items_of(raw) == converted and items_of("not a list") == []
    assert task_sync_payload({"tasks": raw, "ts": 5}) == ({"tasks": converted, "ts": 5}, converted)
    assert task_sync_payload({"pending": raw}) == ({"pending": converted}, converted)
    assert task_sync_payload(raw) == (converted, converted)
    both, kept = task_sync_payload({"tasks": raw[:1], "pending": raw})
    assert both == {"tasks": converted[:1], "pending": converted} and kept == converted[:1]
    for no_list in ({"tasks": "nope", "pending": raw}, {"note": "x"}, None, "tasks", 7):
        assert task_sync_payload(no_list) == (no_list, None)


def test_a_tasks_db_row_becomes_the_item_the_task_manager_sends_with_an_epoch_due_at():
    row = {"id": 9, "description": "Bill pay karna", "scheduled_time": "2026-10-23T18:30:00", "status": "pending",
           "kind": None, "action": "get_weather", "action_args": {"city": "Lahore"}, "attempts": 0}
    assert task_item_from_row(row) == {"id": 9, "description": "Bill pay karna",
                                       "due_at": _at(2026, 10, 23, 18, 30), "source": "task", "kind": "task",
                                       "status": "pending"}
