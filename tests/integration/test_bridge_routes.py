"""C-86 route audit on the bridge (K-02, K-08, K-29, K-40).

Every notify type the agent and the services post (a literal type in services/ or src/, plus plan_progress) has a
bridge route, so none ends in the 'UNKNOWN EVENT' warning. task_sync (what the task manager sends) reaches the UI and
init_state.tasks with every due_at in epoch seconds (the task manager sends a naive local ISO string, which the
dashboard would show as due now), and a task_alert drops that task from both (C-86 review); a map focus (location_sync
with kind 'map', or notify 'map_focus') becomes map_focus_update and init_state.map_focus and never replaces the device
location; a due task is logged by id and length only; the healing loop's status 'HEARTBEAT' never clears the speaking
state; an unknown type is logged printable and short. The notifies run on the internal listener against a temporary
store with a recorded sio.emit.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

import pytest

from services.utils import task_items
from services.utils.plan_progress import PLAN_PROGRESS_EVENT
from src.core import bridge_security, bridge_store
from tests.integration.test_job_events import (  # noqa: F401  # pylint: disable=unused-import  # the shared fixture
    _events,
    _notify,
    bridge,
)

ROOT = Path(__file__).resolve().parents[2]
# notify_event("x", ...), notify_answered("x", ...), jarvis_bridge._notify_with_answer("x", ...), the UI log shipper's
# offer_event("x", ...) and the settings service's notify("x", ...)
_TYPE_CALL = re.compile(
    r"(?:notify_event|notify_answered|_notify_with_answer|offer_event|\bnotify)\(\s*[\"']([A-Za-z_]+)[\"']")
SECRET = "PRIVATE-TASK-TEXT pay the card"
KARACHI = timezone(timedelta(hours=5))  # Asia/Karachi: the task manager's default TZ (K-33), no daylight saving time
MAP_TOOL_PAYLOAD = {"city": "Badshahi mosque", "label": "Badshahi Mosque, Lahore, Punjab, Pakistan", "lat": 31.588,
                    "lng": 74.31, "kind": "map", "source": "geocode", "zoom": 15, "ts": 1791460000.0}


def _pkt(*args: int) -> float:
    return datetime(*args, tzinfo=KARACHI).timestamp()


@pytest.fixture(autouse=True)
def _task_zone(monkeypatch):
    """TZ unset, as on the VPS bridge: naive task times are Asia/Karachi wall-clock time."""
    monkeypatch.delenv("TZ", raising=False)
    task_items.zone_named.cache_clear()
    yield
    task_items.zone_named.cache_clear()


@pytest.fixture
def bridge_log(bridge):  # noqa: F811
    """The UI-BRIDGE logger's records, DEBUG included (setup_logger loggers do not propagate)."""
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler, logger = _Keep(level=logging.DEBUG), bridge.module.logger
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def _text(records: list[logging.LogRecord]) -> str:
    return "\n".join(record.getMessage() for record in records)


def posted_types() -> set[str]:
    """The notify types the agent and the services post today."""
    found: set[str] = set()
    for folder in ("services", "src"):
        for path in (ROOT / folder).rglob("*.py"):
            found.update(_TYPE_CALL.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found | {PLAN_PROGRESS_EVENT}


async def test_every_type_the_agent_posts_has_a_route(bridge, bridge_log, monkeypatch):  # noqa: F811
    types = posted_types()
    assert {"transcription", "tool_sync", "task_sync", "task_alert", "location_sync", "memory_update",
            "settings_sync", "sim_panel_open", "job_end", "audit_event", "plan_progress"} <= types  # the scan works
    unlimited = {name: (1e6, 1e6) for name in bridge_security.NOTIFY_CLASS_RATES}  # this test is no rate test
    monkeypatch.setattr(bridge.module.app.state, "notify_limiter", bridge_security.NotifyRateLimiter(rates=unlimited))
    for event_type in sorted(types):
        await _notify(bridge, event_type, {})
    assert "UNKNOWN EVENT" not in _text(bridge_log)


async def test_task_sync_reaches_the_ui_and_init_state(bridge):  # noqa: F811
    tasks = [{"id": 7, "description": "Call Ali", "due_at": "2026-10-21T10:00:00", "source": "task",
              "kind": "task", "status": "pending"}]
    shown = [{**tasks[0], "due_at": _pkt(2026, 10, 21, 10, 0)}]
    assert await _notify(bridge, "task_sync", {"tasks": tasks}) == {"status": "ok"}
    assert _events(bridge, "task_sync") == [{"tasks": shown}]
    assert (await bridge.module.build_init_state())["tasks"] == shown
    await _notify(bridge, "task_update", {"pending": []})  # the K-40 name and the {pending} shape
    assert _events(bridge, "task_sync")[-1] == {"pending": []}
    assert (await bridge.module.build_init_state())["tasks"] == []
    await _notify(bridge, "task_sync", {"note": "no list"})  # passed through; the stored list stays
    assert _events(bridge, "task_sync")[-1] == {"note": "no list"}
    assert (await bridge.module.build_init_state())["tasks"] == []
    many = [{"id": n} for n in range(bridge.module.TASKS_KEPT + 5)]
    await _notify(bridge, "task_sync", {"tasks": many})
    assert len(_events(bridge, "task_sync")[-1]["tasks"]) == len(many)  # emitted whole
    assert (await bridge.module.build_init_state())["tasks"] == many[:bridge.module.TASKS_KEPT]


async def test_kal_0900_reaches_the_panel_as_tomorrow_not_now(bridge):  # noqa: F811
    """The review's scenario: schedule_task('kal 09:00', 'Ali ko call') makes the task manager send due_at
    '2026-10-22T09:00:00' (naive, Asia/Karachi). The dashboard reads due_at only as an epoch number, so every task
    showed 'Due now'; the bridge now emits and keeps the epoch seconds of 09:00 PKT that day."""
    sent = {"tasks": [{"id": 12, "description": "Ali ko call", "due_at": "2026-10-22T09:00:00", "source": "task",
                       "kind": "task", "status": "pending"},
                      {"id": 13, "description": "x", "due_at": "2026-10-22T04:00:00Z"},  # an offset is kept
                      {"id": 14, "description": "y", "due_at": 1792641600},  # epoch seconds pass through
                      {"id": 15, "description": "z", "scheduled_time": "2026-10-23T18:30:00"},  # an alias
                      {"id": 16, "description": "w", "due_at": "jab waqt mile"},  # unreadable: null, never a string
                      "junk"]}
    await _notify(bridge, "task_sync", sent)
    [emitted] = _events(bridge, "task_sync")
    assert [(item["id"], item["due_at"]) for item in emitted["tasks"]] == [
        (12, _pkt(2026, 10, 22, 9, 0)), (13, _pkt(2026, 10, 22, 9, 0)), (14, 1792641600.0),
        (15, _pkt(2026, 10, 23, 18, 30)), (16, None)]
    assert all(item["due_at"] is None or isinstance(item["due_at"], float) for item in emitted["tasks"])
    assert "scheduled_time" not in emitted["tasks"][3]
    assert (await bridge.module.build_init_state())["tasks"] == emitted["tasks"]


async def test_without_a_tz_database_the_bridge_still_reads_pkt(bridge, monkeypatch):  # noqa: F811
    """The slim bridge image (python:3.12-slim, no tzdata) cannot load Asia/Karachi: UTC+05:00 is used instead."""
    def missing(name: str) -> Any:
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(task_items, "ZoneInfo", missing)
    task_items.zone_named.cache_clear()
    await _notify(bridge, "task_sync", {"tasks": [{"id": 1, "due_at": "2026-10-22T09:00:00"}]})
    assert _events(bridge, "task_sync") == [{"tasks": [{"id": 1, "due_at": _pkt(2026, 10, 22, 9, 0)}]}]


async def test_a_due_task_leaves_init_state_tasks_and_the_panel(bridge, bridge_log):  # noqa: F811
    """A task_alert means the task left the pending list (K-33: get_due_tasks marked it triggered). The agent's task
    monitor marks it in a worker thread, where the task manager cannot publish, so the bridge drops it itself; before,
    the panel kept listing it as 'Overdue', after every reload and bridge restart."""
    tasks = [{"id": 7, "description": SECRET, "due_at": "2026-10-21T10:00:00", "source": "task", "kind": "task",
              "status": "pending"},
             {"id": 8, "description": "Call Ali", "due_at": "2026-10-22T09:00:00", "source": "task", "kind": "task",
              "status": "pending"}]
    await _notify(bridge, "task_sync", {"tasks": tasks})
    alert = {"id": 7, "description": SECRET, "action": None}
    assert await _notify(bridge, "task_alert", alert) == {"status": "ok"}
    assert [event for event, _data in bridge.emitted][-2:] == ["task_notification", "task_sync"]
    assert _events(bridge, "task_notification") == [alert]
    remaining = [{**tasks[1], "due_at": _pkt(2026, 10, 22, 9, 0)}]
    assert _events(bridge, "task_sync")[-1] == {"tasks": remaining}
    assert (await bridge.module.build_init_state())["tasks"] == remaining
    assert await bridge.store.kv_get("tasks") == remaining  # what the next bridge process reads
    syncs = len(_events(bridge, "task_sync"))
    for unknown in ({"id": 99, "description": "x"}, {"description": "no id"}, {"id": None}, {"id": True}, "odd"):
        await _notify(bridge, "task_alert", unknown)
    assert len(_events(bridge, "task_sync")) == syncs  # an id the list does not hold emits nothing
    await _notify(bridge, "task_alert", {"id": "8"})  # the agent's int id and a string id are the same task
    assert _events(bridge, "task_sync")[-1] == {"tasks": []}
    assert (await bridge.module.build_init_state())["tasks"] == []
    assert SECRET not in _text(bridge_log)


async def test_a_drop_and_a_new_list_never_interleave(bridge, monkeypatch):  # noqa: F811
    """task_sync and a task_alert's drop take turns on init_state.tasks: a drop that read the old list never writes
    its result over a newer list (task 9 would be lost)."""
    module = bridge.module
    await module._route_task_list({"tasks": [{"id": 7}, {"id": 8}]})
    real_get, gate = bridge.store.kv_get, asyncio.Event()

    async def slow_get(key: str, default: Any = None) -> Any:
        value = await real_get(key, default)
        await gate.wait()  # the drop holds its read until the newer list has been sent
        return value

    monkeypatch.setattr(bridge.store, "kv_get", slow_get)
    drop = asyncio.create_task(module._drop_due_task(7))
    await asyncio.sleep(0.05)
    newer = asyncio.create_task(module._route_task_list({"tasks": [{"id": 8}, {"id": 9}]}))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(drop, newer)
    assert [task["id"] for task in await real_get("tasks")] == [8, 9]
    assert [[task["id"] for task in data["tasks"]] for data in _events(bridge, "task_sync")][-2:] == [[8], [8, 9]]


async def test_a_map_focus_never_replaces_the_device_location(bridge, bridge_log):  # noqa: F811
    device = {"city": "Lahore", "lat": 31.52, "lng": 74.35, "kind": "device", "source": "ipinfo", "ts": 1.0}
    await _notify(bridge, "location_sync", device)
    assert await _notify(bridge, "location_sync", MAP_TOOL_PAYLOAD) == {"status": "ok"}  # the map tool today
    focus = {"query": "Badshahi mosque", "display_name": "Badshahi Mosque, Lahore, Punjab, Pakistan",
             "lat": 31.588, "lng": 74.31, "zoom": 15, "ts": 1791460000.0}
    assert _events(bridge, "map_focus_update") == [focus]
    assert _events(bridge, "location_update") == [device]  # only the device reading
    state = await bridge.module.build_init_state()
    assert state["location"] == device and state["map_focus"] == focus
    await _notify(bridge, "map_focus", {"query": "Minar", "display_name": "Minar-e-Pakistan", "lat": "31.59",
                                        "lng": 74.31, "zoom": 99})  # K-08's own name, a string lat, zoom clamped
    latest = _events(bridge, "map_focus_update")[-1]
    assert (latest["query"], latest["lat"], latest["zoom"]) == ("Minar", 31.59, 20) and latest["ts"] > 0
    assert "Badshahi" not in _text(bridge_log) and "Minar" not in _text(bridge_log)  # a place is private (K-29)


@pytest.mark.parametrize("payload", [{"kind": "map", "lat": 95.0, "lng": 74.3}, {"kind": "MAP", "lat": None},
                                     {"kind": "map", "lat": 31.5, "lng": "east"}])
async def test_an_unusable_map_focus_is_ignored_and_stores_nothing(bridge, payload):  # noqa: F811
    assert await _notify(bridge, "location_sync", payload) == {"status": "ignored", "reason": "bad_payload"}
    assert not _events(bridge, "map_focus_update") and not _events(bridge, "location_update")
    state = await bridge.module.build_init_state()
    assert state["map_focus"] is None and state["location"] == {"city": "Detecting...", "lat": 0, "lng": 0}


async def test_a_location_without_kind_is_still_the_device(bridge):  # noqa: F811
    await _notify(bridge, "location_sync", {"city": "Lahore", "lat": 31.5, "lng": 74.3})
    assert _events(bridge, "location_update") == [{"city": "Lahore", "lat": 31.5, "lng": 74.3}]
    assert not _events(bridge, "map_focus_update")


async def test_a_due_task_is_logged_by_id_and_length_only(bridge, bridge_log):  # noqa: F811
    payload = {"id": 42, "kind": "task", "source": "task", "description": SECRET, "action": "", "ts": 1.0}
    assert await _notify(bridge, "task_alert", payload) == {"status": "ok"}
    assert _events(bridge, "task_notification") == [payload]
    info = [r.getMessage() for r in bridge_log if r.levelno == logging.INFO]
    assert any(f"task_alert id=42 ({len(SECRET)} chars)" in line for line in info)
    assert SECRET not in _text(bridge_log)
    for odd in ("not an object", None, ["x"]):  # never a 500 for an odd payload
        assert await _notify(bridge, "task_alert", odd) == {"status": "ok"}


async def test_the_healing_heartbeat_never_clears_the_speaking_state(bridge):  # noqa: F811
    await _notify(bridge, "status", "START")
    await _notify(bridge, "status", "HEARTBEAT")  # agent_runner's healing recovery pulse (notify_ui)
    assert _events(bridge, "status_change") == [{"speaking": True, "thinking": False}]
    assert bridge.module.bridge_manager.state["speaking"] is True
    await _notify(bridge, "status", "STOP")
    assert _events(bridge, "status_change")[-1] == {"speaking": False, "thinking": False}


async def test_an_unknown_type_is_logged_printable_and_short(bridge, bridge_log):  # noqa: F811
    assert await _notify(bridge, "made_up\nFAKE LOG LINE" + "x" * 200, {}) == {"status": "ok"}
    [line] = [r.getMessage() for r in bridge_log if "UNKNOWN EVENT" in r.getMessage()]
    assert "\n" not in line and len(line) < 120


def test_the_memory_and_task_helpers_are_bounded(bridge):  # noqa: F811
    module = bridge.module
    assert (bridge_store.MEMORY_MAX_ROWS, bridge_store.INIT_MEMORIES) == (200, 10)  # K-07 / K-42
    assert module.task_sync_event({"tasks": "nope"}) == ({"tasks": "nope"}, None)
    assert module.task_sync_event([{"id": 1}, "a"]) == ([{"id": 1}], [{"id": 1}])
    assert module.is_map_focus({"kind": " Map "}) and not module.is_map_focus({"kind": "device"})
    assert not module.is_map_focus({"kind": 7}) and not module.is_map_focus({}) and not module.is_map_focus("map")


def _unused(_: Any) -> None:  # keeps the fixture import visible to linters
    return None
