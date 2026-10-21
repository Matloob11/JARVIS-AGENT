"""
# services/utils/task_items.py
The dashboard's task list (C-86 review fix; K-02 task_sync, K-40 task_update, K-42 init_state.tasks):

    TaskItem = {id, description, due_at: epoch seconds | None, source: 'task'|'reminder', kind, status}

The task manager (services/automation/jarvis_task_manager.py, K-33) keeps scheduled_time as a naive wall-clock ISO
string in TZ (default Asia/Karachi), and its own task_sync sends that string as due_at. The dashboard reads due_at only
as an epoch number and shows anything else as due at the moment it arrived. task_due_epoch() reads both: a number or a
numeric string (seconds or milliseconds) as it is, an ISO 8601 date-time with an offset as written, and a naive one as
wall-clock time in TZ, the task manager's own rule (scheduling_now). The bridge passes every task_sync and task_update
list through task_sync_payload() before it stores and emits it. The agent's own publisher (ui_command task_cancel and
tasks_refresh) builds its items with task_item_from_row().

Stdlib only: the slim bridge image imports it too. That image has no tz database, so a zone ZoneInfo cannot load falls
back to the default zone, and the default zone falls back to a fixed UTC+05:00 (Asia/Karachi's offset; Pakistan keeps
no daylight saving time). The UTC names are always UTC.
"""
from __future__ import annotations

import functools
import math
import os
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

TASK_TZ_DEFAULT = "Asia/Karachi"  # K-33: the task manager's zone when TZ is not set
PKT = timezone(timedelta(hours=5), "PKT")  # Asia/Karachi where no tz database is installed
DUE_KEYS = ("due_at", "dueAt", "scheduled_time")  # the due time, then the aliases the dashboard also reads
_UTC_NAMES = frozenset({"utc", "etc/utc", "gmt", "etc/gmt", "universal", "etc/universal", "zulu", "etc/zulu"})
_MS_EPOCH_MIN = 1e11  # larger epoch values are milliseconds (the bridge store's rule)


@functools.lru_cache(maxsize=16)
def zone_named(name: str) -> tzinfo:
    """The zone of a TZ name. A name ZoneInfo cannot load (unknown, a path, or no tz database at all) is UTC for the
    UTC names and the default zone otherwise; the default zone itself falls back to PKT."""
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, OSError):  # ZoneInfoNotFoundError is a KeyError; a path or '..' a ValueError
        if name.lower() in _UTC_NAMES:
            return timezone.utc
        return PKT if name == TASK_TZ_DEFAULT else zone_named(TASK_TZ_DEFAULT)


def task_zone() -> tzinfo:
    """The zone of the task manager's naive times: TZ (a leading ':' ignored), else Asia/Karachi (K-33)."""
    return zone_named((os.getenv("TZ") or "").strip().lstrip(":").strip() or TASK_TZ_DEFAULT)


def _epoch_number(value: float) -> float | None:
    """Epoch seconds from a number in seconds or milliseconds; None for zero, a negative or a non-finite one."""
    if not math.isfinite(value) or value <= 0:
        return None
    return round(value / 1000.0 if value > _MS_EPOCH_MIN else value, 3)


def _as_float(value: int | float | str) -> float | None:
    """float(value); None for text that is no number and for an int no float can hold (JSON numbers have no limit)."""
    try:
        return float(value)
    except (ValueError, OverflowError):
        return None


def _iso_epoch(text: str, zone: tzinfo | None) -> float | None:
    """Epoch seconds of an ISO 8601 date or date-time string with its own offset; a naive one is wall-clock time in
    zone (default task_zone()). None when it cannot be read or lies at or before 1970."""
    try:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone or task_zone())
        return _epoch_number(moment.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def task_due_epoch(value: Any, zone: tzinfo | None = None) -> float | None:
    """Epoch seconds (rounded to the millisecond) of a task's due time. An int, a float or a numeric string counts as
    seconds or milliseconds. An ISO 8601 date or date-time string (what datetime.fromisoformat reads, for example
    '2026-10-22T09:00:00', '2026-10-22 09:00', with a fraction, an offset or 'Z') keeps its offset; a naive one is
    wall-clock time in zone (default task_zone()). None for anything else: a bool, empty or other text, a time at or
    before 1970."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    text = value.strip() if isinstance(value, str) else ""
    number = _as_float(text if isinstance(value, str) else value)
    if number is not None:
        return _epoch_number(number)
    return _iso_epoch(text, zone) if text else None


def task_item(raw: Any, zone: tzinfo | None = None) -> dict[str, Any] | None:
    """One TaskItem as the bridge stores and emits it: a copy of the object whose due time (due_at, else dueAt, else
    scheduled_time: the first one that is set) becomes due_at in epoch seconds (None when it cannot be read), with the
    two aliases dropped; every other field stays as it came. None for a value that is not an object (the dashboard
    skips those anyway)."""
    if not isinstance(raw, Mapping):
        return None
    item = dict(raw)
    if any(key in item for key in DUE_KEYS):
        due = next((item[key] for key in DUE_KEYS if item.get(key) is not None), None)
        for alias in DUE_KEYS[1:]:
            item.pop(alias, None)
        item["due_at"] = None if due is None else task_due_epoch(due, zone)
    return item


def task_items(raw: Any, zone: tzinfo | None = None) -> list[dict[str, Any]]:
    """task_item() of every object in a list, in order; an entry that is not an object is left out."""
    if not isinstance(raw, list):
        return []
    zone = zone or task_zone()
    return [item for item in (task_item(entry, zone) for entry in raw) if item is not None]


def task_sync_payload(payload: Any, zone: tzinfo | None = None) -> tuple[Any, list[dict[str, Any]] | None]:
    """(the payload the bridge emits as task_sync, the list init_state.tasks keeps) of a task_sync or task_update
    payload: {tasks: [...]}, else {pending: [...]}, else a bare list (K-02). Every item goes through task_item() and
    the shape stays as it came (a payload with both lists gets both converted, and 'tasks' is the list kept).
    (payload, None) when it carries no list."""
    zone = zone or task_zone()
    if isinstance(payload, list):
        items = task_items(payload, zone)
        return items, items
    if not isinstance(payload, Mapping):
        return payload, None
    key = "tasks" if "tasks" in payload else "pending"
    if not isinstance(payload.get(key), list):
        return payload, None
    emitted = dict(payload)
    for name in ("tasks", "pending"):
        if isinstance(payload.get(name), list):
            emitted[name] = task_items(payload[name], zone)
    return emitted, emitted[key]


def task_item_from_row(row: Mapping[str, Any], zone: tzinfo | None = None) -> dict[str, Any]:
    """The TaskItem of one tasks.db row as TaskManager.list_pending_tasks() returns it (K-33): the fields the task
    manager's own task_sync sends, with scheduled_time read as due_at in epoch seconds."""
    return {
        "id": row.get("id"),
        "description": row.get("description"),
        "due_at": task_due_epoch(row.get("scheduled_time"), zone),
        "source": "task",
        "kind": row.get("kind") or "task",
        "status": row.get("status"),
    }
