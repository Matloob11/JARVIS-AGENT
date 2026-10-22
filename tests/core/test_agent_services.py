"""src/core/agent_services.py, jarvis-agent-svc (plan task C-125; contract K-77).

Every test runs on temp folders with fake ports (bridge, laptop, reminders, tasks, e-mail): no real reminder, task,
location or backup is read, nothing reaches a bridge, the laptop or a real SMTP server (the e-mail tests talk to a
fake one on 127.0.0.1 with throwaway credentials), and no service process is started.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import dataclasses
import errno
import json
import logging
import signal
import smtplib
import socket
import socketserver
import sys
import threading
import time
import types
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core import agent_services as svc_mod
from src.core.agent_services import (
    AgentServices,
    DeliveryLedger,
    DueItem,
    NotifyResult,
    PollAnswer,
    ServiceTimers,
)

FAST = ServiceTimers(reminder_s=0.05, task_s=0.05, heartbeat_s=0.05, watch_s=0.05, announce_wait_s=0.3, poll_s=0.01,
                     retry_throttle_s=0.0, late_wait_s=0.3, ui_ack_grace_s=0.2, restart_hold_s=5.0,
                     scheduler_offline_wait_s=0.3, scheduler_poll_s=0.02, snapshot_catchup_s=600.0)
SLOW_ANNOUNCE = dataclasses.replace(FAST, announce_wait_s=30.0)  # a wait a test cuts on purpose, never a timeout
MARKER = "PRIVATE-REMINDER-TEXT-7731"
CITY_MARKER = "Markerabad"
HEALTH_KEYS = {"status", "role", "brain", "state", "pid", "uptime_s", "git_sha", "due", "due_pending", "undelivered",
               "last_snapshot_ts", "snapshot", "loops", "link", "ts"}


# ---------------------------------------------------------------- fakes


class FakeBridge:
    """The bridge as agent-svc sees it: owner-room agents, UI sockets (a connected UI acknowledges a reminder_due at
    once, as the dashboard's useTasks does), stored notifications and announce results."""

    def __init__(self) -> None:
        self.boot = "boot-1"  # the id of this bridge start (svc_poll boot)
        self.agents = 0
        self.ui_clients = 0
        self.ui_connects = 0
        self.ui_auto_ack = True
        self.job_answer: dict[str, Any] | None = None  # what the live job reports for an announce
        self.announce_status: str | None = None  # what announce answers once it went out (None: 'sent')
        self.down = False
        self.down_after_announce = False  # the bridge goes down right after it emitted an announce
        self.fail_acks = 0  # this many polls that carry 'svc' acks get no answer
        self.fail_polls = 0  # this many polls get no answer at all (a 429 of the shared bucket, a 5 s timeout)
        self.lost_notifies = 0  # this many step 3s are stored (and emitted) but their answer is lost (a 5 s timeout)
        self.announce_results: dict[str, dict[str, Any]] = {}
        self.acked: dict[str, str] = {}
        self.stored: dict[str, dict[str, Any]] = {}
        self.notify_calls: list[dict[str, Any]] = []
        self.emitted: list[str] = []
        self.announces: list[tuple[str, str, str]] = []
        self.svc_acked: list[str] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.locations: list[dict[str, Any]] = []
        self.logs: list[tuple[list[dict[str, Any]], int]] = []

    def connect_ui(self) -> None:
        self.ui_clients += 1
        self.ui_connects += 1

    def restart(self) -> None:
        """A bridge restart (a deploy): sockets and the connect counter start again; bridge.db (notifications, acks
        and the kept announce results) stays."""
        self.boot = f"boot-{int(self.boot.rsplit('-', 1)[1]) + 1}"
        self.ui_clients = self.ui_connects = self.agents = 0

    def _answer(self, ids: list[str]) -> PollAnswer:
        acks: dict[str, dict[str, Any]] = {}
        for item_id in ids:
            if item_id in self.announce_results:
                acks[item_id] = {"by": "agent", **self.announce_results[item_id]}
            elif item_id in self.acked:
                acks[item_id] = {"by": self.acked[item_id], "delivered": True}
        return PollAnswer(ui_clients=self.ui_clients, ui_connects=self.ui_connects, agents=self.agents, acks=acks,
                          boot=self.boot)

    async def poll(self, ids: list[str], ack_ids: list[str]) -> PollAnswer | None:
        if self.down:
            return None
        if self.fail_polls:
            self.fail_polls -= 1
            return None
        if ack_ids and self.fail_acks:
            self.fail_acks -= 1
            return None
        for item_id in ack_ids:
            self.svc_acked.append(item_id)
            self.acked.setdefault(item_id, "svc")
        return self._answer(list(ids))

    async def heartbeat(self, payload: dict[str, Any]) -> PollAnswer | None:
        self.heartbeats.append(payload)
        return None if self.down else self._answer([])

    async def announce(self, attempt_id: str, text: str, kind: str) -> str:
        if self.down:
            return "refused"  # never sent (a connect error): surely not emitted
        self.announces.append((attempt_id, text, kind))
        if self.agents <= 0:
            return "no_agent"
        if self.job_answer is not None:
            self.announce_results[attempt_id] = dict(self.job_answer)
        if self.down_after_announce:
            self.down = True
        return self.announce_status or "sent"

    async def notify(self, payload: dict[str, Any]) -> NotifyResult | None:
        if self.down:
            return None
        self.notify_calls.append(dict(payload))
        item_id = payload["id"]
        outcome = "exists" if item_id in self.stored else "new"
        self.stored[item_id] = dict(payload)
        acked = item_id in self.acked
        if not acked:
            self.emitted.append(item_id)
            if self.ui_clients and self.ui_auto_ack:
                self.acked[item_id] = "ui"  # the dashboard sends notification_ack right after the event
        if self.lost_notifies:
            self.lost_notifies -= 1
            return NotifyResult(stored=svc_mod.NOTIFY_UNKNOWN)
        return NotifyResult(stored=outcome, acked=acked, ui_clients=self.ui_clients)

    async def location_sync(self, payload: dict[str, Any]) -> None:
        self.locations.append(payload)

    async def log_batch(self, items: list[dict[str, Any]], dropped: int) -> None:
        self.logs.append((items, dropped))


class FakeLaptop:
    def __init__(self, online: bool = False, result: str = "acked", results: list[str] | None = None) -> None:
        self.is_online = online
        self.result = result
        self.results = list(results or [])  # outcomes of the next speaks, then result
        self.spoken: list[tuple[str, str]] = []
        self.call_ids: list[str | None] = []
        self.actions: list[dict[str, Any]] = []

    def online(self) -> bool:
        return self.is_online

    async def speak(self, text: str, item_id: str, call_id: str | None = None) -> str:
        self.spoken.append((text, item_id))
        self.call_ids.append(call_id)
        return self.results.pop(0) if self.results else self.result

    async def run_action(self, **kwargs: Any) -> dict[str, Any]:
        self.actions.append(kwargs)
        return {"status": "success", "tool": kwargs["tool"], "message": "done"}


class FakeReminders:
    """Cursor's X-66 API as the real functions behave: list_due_reminders (read-only, the pending rows sorted by their
    'time' string), load_reminders' file order (pending_rows) and mark_reminder_delivered (the first pending row with
    the id in file order -> triggered). rows is the file, in file order."""

    def __init__(self, *reminders: dict[str, Any]) -> None:
        self.rows = [dict(row) for row in reminders]
        self.marks: list[str] = []
        self.fail_marks = 0
        self.lost_marks = 0  # this many marks answer True but change nothing (save_reminders logged an OSError)
        self.clock_offset = timedelta(0)  # the wall clock list_due_reminders compares with (a step back: negative)

    def list_due(self) -> list[dict[str, Any]]:
        now = datetime.now(svc_mod.local_zone()).replace(tzinfo=None) + self.clock_offset
        due = [dict(row) for row in self.rows
               if row.get("status") == "pending" and datetime.fromisoformat(row["time"]) <= now]
        due.sort(key=lambda row: row["time"])  # as list_due_reminders: stable, so file order only within one string
        return due

    def pending_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows if row.get("status") == "pending"]

    def mark_delivered(self, reminder_id: str) -> bool:
        if self.fail_marks:
            self.fail_marks -= 1
            raise OSError("disk busy")
        self.marks.append(reminder_id)
        for row in self.rows:
            if row["id"] == reminder_id and row.get("status") == "pending":
                if self.lost_marks:
                    self.lost_marks -= 1
                else:
                    row["status"] = "triggered"
                return True
        return False


class FakeTasks:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self.rows = {row["id"]: dict(row) for row in rows}
        self.completed: list[int] = []

    def claim_due(self) -> list[dict[str, Any]]:
        claimed = []
        for row in self.rows.values():
            if row["status"] == "pending":
                row["status"] = "triggered"
                claimed.append(dict(row))
        return claimed

    def triggered(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values() if row["status"] == "triggered"]

    def mark_completed(self, task_id: int) -> None:
        self.completed.append(task_id)
        self.rows[task_id]["status"] = "completed"


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def configured(self) -> bool:
        return True

    def send(self, subject: str, body: str) -> bool:
        self.sent.append((subject, body))
        return True


def reminder(rid: str = "rem_1760000000", message: str = MARKER, minutes_ago: float = 1.0) -> dict[str, Any]:
    """A pending reminder in the X-66 on-disk format: naive ISO wall-clock time in TZ (Asia/Karachi by default)."""
    moment = datetime.now(svc_mod.local_zone()).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    when = moment.isoformat(timespec="seconds")
    return {"id": rid, "time": when, "message": message, "status": "pending", "created_at": when}


def task(task_id: int = 7, description: str = MARKER, action: str | None = None) -> dict[str, Any]:
    return {"id": task_id, "description": description, "scheduled_time": "2026-10-20T09:00:00", "action": action,
            "status": "pending", "created_at": "2026-10-20T08:00:00"}


@pytest.fixture
def records():
    """Every record of the svc logger (it does not propagate to pytest's capture)."""
    captured: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Keep(level=logging.DEBUG)
    log = logging.getLogger("JARVIS-SVC")
    old_level = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    yield captured
    log.removeHandler(handler)
    log.setLevel(old_level)


def make_svc(tmp_path: Path, *, bridge: FakeBridge | None = None, laptop: FakeLaptop | None = None,
             reminders: FakeReminders | None = None, tasks: FakeTasks | None = None, email: FakeEmail | None = None,
             brain: str = "vps", timers: ServiceTimers = FAST, **kwargs: Any) -> AgentServices:
    saved: list[tuple[Any, ...]] = []

    def saver(city: str, lat: float, lng: float, *, ts: float) -> dict[str, Any]:
        from services.utils import jarvis_location
        saved.append((city, lat, lng, ts))
        return jarvis_location.save_device_location(city, lat, lng, ts=ts, path=str(tmp_path / "location.json"))

    service = AgentServices(brain=brain, root=tmp_path, bridge=bridge or FakeBridge(),
                            laptop=laptop or FakeLaptop(), reminders=reminders or FakeReminders(),
                            tasks=tasks or FakeTasks(), email=email or FakeEmail(), location_saver=saver,
                            timers=timers, health_port=0, **kwargs)
    service.saved_locations = saved  # type: ignore[attr-defined]
    return service


def ledger(tmp_path: Path) -> dict[str, dict[str, Any]]:
    return DeliveryLedger(tmp_path / "conversations" / "delivery_ledger.json").read()


async def drain(service: AgentServices) -> None:
    for _ in range(50):
        if not service._spawned:
            return
        await asyncio.gather(*list(service._spawned), return_exceptions=True)


async def http_get(port: int, path: str = "/health") -> tuple[int, dict[str, Any]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


async def wait_until(check: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------- acceptance: delivered on the next connect, once


async def test_a_reminder_due_with_no_ui_is_delivered_on_the_next_ui_connect_and_never_twice(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, reminders=source)

    await service.reminder_tick()  # no job, laptop offline, no UI: stored at the bridge, not delivered
    [entry] = ledger(tmp_path).values()
    assert entry["notified"] is True and not entry.get("delivered_at") and source.marks == []
    assert [row["step"] for row in entry["attempts"]] == ["announce", "laptop", "notify"]
    await service.watch_tick()
    assert source.marks == [] and len(bridge.notify_calls) == 1

    bridge.connect_ui()  # the owner opens the dashboard
    await service.watch_tick()  # the connect is seen: reminder_due again, the dashboard acknowledges it
    await service.watch_tick()  # the ack is seen
    [entry] = ledger(tmp_path).values()
    assert entry["delivered_at"] and entry["channel"] == "ui" and entry["source_marked"] is True
    assert source.marks == ["rem_1760000000"] and source.rows[0]["status"] == "triggered"

    for _ in range(3):  # later ticks and connects never deliver it again
        await service.reminder_tick()
        bridge.connect_ui()
        await service.watch_tick()
    assert len(bridge.notify_calls) == 2 and source.marks == ["rem_1760000000"]


async def test_a_reminder_due_with_no_ui_is_delivered_on_the_next_laptop_connect(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    assert laptop.spoken == [] and source.marks == []

    laptop.is_online = True
    service.on_node_session("pc:laptop-1")  # the node's hello (DesktopLink 'session')
    await drain(service)
    assert len(laptop.spoken) == 1 and MARKER in laptop.spoken[0][0]
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and source.marks == ["rem_1760000000"]
    # the stored notification is acknowledged by 'svc' at once: a dashboard that connects now does not show it
    assert bridge.svc_acked == [entry["id"]] and bridge.acked[entry["id"]] == "svc" and "bridge_ack" not in entry

    await service.watch_tick()  # nothing is left for the watch tick
    assert bridge.svc_acked == [entry["id"]] and "bridge_ack" not in ledger(tmp_path)[entry["id"]]
    service.on_node_session("pc:laptop-1")  # a second connect speaks nothing
    await drain(service)
    assert len(laptop.spoken) == 1


async def test_the_ledger_is_the_truth_across_a_restart(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True), FakeReminders(reminder())
    source.fail_marks = 1  # the process dies between the ack and reminders.json (simulated: the mark fails)
    first = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await first.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and not entry.get("source_marked")
    assert source.rows[0]["status"] == "pending"  # reminders.json still lists it as due

    second = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)  # the restarted svc
    await second.reminder_tick()
    assert len(laptop.spoken) == 1 and bridge.notify_calls == [] and bridge.announces == []
    assert source.marks == ["rem_1760000000"] and ledger(tmp_path)[entry["id"]]["source_marked"] is True


async def test_an_item_waiting_for_its_ack_is_not_sent_again_after_a_restart(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    await make_svc(tmp_path, bridge=bridge, reminders=source).reminder_tick()
    assert len(bridge.notify_calls) == 1
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source)
    for _ in range(3):
        await restarted.reminder_tick()
    assert len(bridge.notify_calls) == 1 and bridge.announces == []
    bridge.connect_ui()
    await restarted.watch_tick()
    await restarted.watch_tick()
    assert source.marks == ["rem_1760000000"] and len(bridge.notify_calls) == 2


# ---------------------------------------------------------------- each ladder step and its ack


async def test_step_1_the_live_job_announces_it_and_its_result_is_the_ack(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True), FakeReminders(reminder())
    bridge.agents, bridge.job_answer = 1, {"delivered": True}
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "announce" and source.marks == ["rem_1760000000"]
    [(attempt_id, text, kind)] = bridge.announces
    assert attempt_id == f"{entry['id']}#a1" and text == MARKER and kind == "reminder"
    assert laptop.spoken == [] and bridge.notify_calls == []


async def test_step_1_without_a_participant_goes_on_to_the_laptop(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=True)
    bridge.agents, bridge.job_answer = 1, {"delivered": False, "reason": "no_participant"}
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()))
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and len(laptop.spoken) == 1 and bridge.notify_calls == []
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["sent", "no_participant"]


async def test_step_1_a_job_that_never_answers_times_out_then_the_ladder_goes_on(tmp_path):
    bridge = FakeBridge()
    bridge.agents = 1  # a job without the 'announce' handler: no result ever comes
    service = make_svc(tmp_path, bridge=bridge, reminders=FakeReminders(reminder()))
    started = time.monotonic()
    await service.reminder_tick()
    assert time.monotonic() - started >= FAST.announce_wait_s
    [entry] = ledger(tmp_path).values()
    assert [row["outcome"] for row in entry["attempts"]] == ["sent", "timeout", "offline", "new"]
    assert entry["notified"] is True


async def test_an_announce_whose_answer_was_lost_waits_for_the_jobs_result_and_is_never_spoken_too(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True), FakeReminders(reminder())
    bridge.agents, bridge.job_answer = 1, {"delivered": True}
    bridge.announce_status = "unknown"  # the bridge emitted it, but its answer was lost (a 5 s read timeout)
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "announce" and laptop.spoken == [] and bridge.notify_calls == []
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["unknown", "acked"]
    assert "announce_call" not in entry and source.marks == ["rem_1760000000"]


async def test_an_announce_of_unknown_outcome_without_a_result_times_out_then_the_laptop_speaks(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=True)
    bridge.agents, bridge.announce_status = 1, "unknown"  # and no result ever comes
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()))
    started = time.monotonic()
    await service.reminder_tick()
    assert time.monotonic() - started >= FAST.announce_wait_s
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and len(laptop.spoken) == 1 and "announce_call" not in entry
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["unknown", "timeout"]


async def test_a_refused_announce_goes_on_to_the_laptop_at_once(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=True)
    bridge.agents, bridge.announce_status = 1, "refused"  # a 429: the bridge never ran its handler
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()),
                       timers=SLOW_ANNOUNCE)
    started = time.monotonic()
    await service.reminder_tick()
    assert time.monotonic() - started < 5
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and "announce_call" not in entry
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["refused"]


async def test_step_2_a_failed_or_unknown_speak_is_no_ack(tmp_path):
    for result in ("failed", "unknown", "offline"):
        root = tmp_path / result
        bridge, laptop = FakeBridge(), FakeLaptop(online=True, result=result)
        service = make_svc(root, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()))
        await service.reminder_tick()
        [entry] = ledger(root).values()
        assert not entry.get("delivered_at")
        if result == "unknown":  # it may have spoken: held on every channel, so not stored at the bridge either
            assert entry["laptop_call"]["id"] == laptop.call_ids[0] and not entry.get("notified")
            assert bridge.notify_calls == []
        else:
            assert entry["notified"] is True and len(bridge.notify_calls) == 1 and "laptop_call" not in entry


async def test_a_late_speak_result_is_the_ack(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=True, result="unknown")
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()))
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    late = types.SimpleNamespace(label={"svc_item": entry["id"]}, state="result",
                                 result={"status": "success", "tool": "pc.speak_local"})
    service.on_late_outcome(late)
    await drain(service)
    entry = ledger(tmp_path)[entry["id"]]
    assert entry["channel"] == "laptop" and "laptop_call" not in entry
    assert bridge.notify_calls == [] and bridge.svc_acked == []  # never stored at the bridge: nothing to ack there


async def test_step_3_with_an_open_dashboard_is_acknowledged_by_the_ui(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    bridge.connect_ui()
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()
    assert bridge.emitted == [next(iter(ledger(tmp_path)))]
    await service.watch_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "ui" and source.marks == ["rem_1760000000"]
    assert bridge.notify_calls[0]["description"] == MARKER and bridge.notify_calls[0]["kind"] == "reminder"
    assert isinstance(bridge.notify_calls[0]["due_at"], float)


async def test_a_bridge_that_is_down_leaves_the_item_open_for_the_next_tick(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    bridge.down = True
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert not entry.get("notified") and [row["outcome"] for row in entry["attempts"]] == [
        "no_bridge", "offline", "failed"]
    bridge.down = False
    await service.reminder_tick()
    assert ledger(tmp_path)[entry["id"]]["notified"] is True


async def test_two_reminders_with_one_id_are_two_items(tmp_path):
    first, second = reminder(), reminder()
    second["created_at"] = "2026-10-20T08:00:01"
    bridge, source = FakeBridge(), FakeReminders(first, second)
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()
    assert len(ledger(tmp_path)) == 2 and len(bridge.notify_calls) == 2


async def test_a_reminder_gone_from_its_source_is_closed_and_its_notification_acknowledged(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()
    source.rows[0]["status"] = "triggered"  # the PC brain's loop took it after a brain switch
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["closed_at"] and entry["close_reason"] == "gone" and not entry.get("delivered_at")
    await service.watch_tick()
    assert bridge.svc_acked == [entry["id"]]


# ---------------------------------------------------------------- never twice: connects, restarts, shared ids


async def test_a_ui_ack_just_before_a_laptop_hello_is_delivered_on_the_ui_and_not_spoken(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()  # stored at the bridge while the laptop sleeps
    [key] = ledger(tmp_path)
    bridge.connect_ui()  # on wake the dashboard reconnects first
    await service.watch_tick()  # reminder_due again: the dashboard shows it and sends notification_ack
    assert bridge.acked == {key: "ui"} and not ledger(tmp_path)[key].get("delivered_at")  # not polled yet
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")  # the node's hello comes before the next watch poll
    await drain(service)
    entry = ledger(tmp_path)[key]
    assert laptop.spoken == [] and entry["channel"] == "ui" and source.marks == ["rem_1760000000"]
    assert bridge.svc_acked == []


async def test_a_svc_ack_the_bridge_missed_goes_out_on_the_next_watch_tick(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=False)
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()))
    await service.reminder_tick()
    laptop.is_online = True
    bridge.fail_acks = 1  # the immediate 'svc' ack gets no answer
    service.on_node_session("pc:laptop-1")
    await drain(service)
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and entry["bridge_ack"] is True and bridge.svc_acked == []
    await service.watch_tick()
    assert bridge.svc_acked == [entry["id"]] and "bridge_ack" not in ledger(tmp_path)[entry["id"]]


async def test_two_reminders_with_one_id_each_mark_their_own_row_when_delivered_out_of_order(tmp_path):
    first, second = reminder(), reminder()
    second["time"], second["created_at"] = first["time"], "2026-10-20T08:00:01"  # both set for one minute
    bridge, source = FakeBridge(), FakeReminders(first, second)
    laptop = FakeLaptop(online=True, results=["failed", "acked"])  # the first speak fails, the second is heard
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    key_a, key_b = svc_mod.reminder_item(first).id, svc_mod.reminder_item(second).id
    await service.reminder_tick()
    book = ledger(tmp_path)
    assert book[key_b]["channel"] == "laptop" and not book[key_b].get("source_marked")
    assert book[key_a]["notified"] is True and not book[key_a].get("delivered_at")
    assert [row["status"] for row in source.rows] == ["pending", "pending"]  # A's row is not marked for B

    await service.reminder_tick()
    assert not ledger(tmp_path)[key_a].get("closed_at")  # A is still due: never closed as gone

    bridge.connect_ui()
    await service.watch_tick()
    await service.watch_tick()  # A is delivered on the dashboard and marks its own (the first) row
    assert ledger(tmp_path)[key_a]["channel"] == "ui"
    assert [row["status"] for row in source.rows] == ["triggered", "pending"]
    await service.reminder_tick()  # now B is the first pending reminder with the id: its own mark
    assert [row["status"] for row in source.rows] == ["triggered", "triggered"]
    assert ledger(tmp_path)[key_b]["source_marked"] is True and source.marks == ["rem_1760000000"] * 2


async def test_reminders_sharing_an_id_mark_their_own_rows_when_their_times_differ_below_a_second(tmp_path):
    """Set for the same second, two reminders share rem_<epoch> while their 'time' strings differ (a relative
    reminder keeps its microseconds): list_due_reminders sorts them A, R while reminders.json holds R, A, and
    mark_reminder_delivered(id) takes the first pending row in file order."""
    a = reminder()
    r = dict(a, time=a["time"] + ".500000", created_at=a["created_at"] + ".250000")
    bridge, source = FakeBridge(), FakeReminders(r, a)  # the file: R, then A
    assert [row["time"] for row in source.list_due()] == [a["time"], r["time"]]  # due order: A, then R
    laptop = FakeLaptop(online=True, results=["acked", "failed"])  # A is heard, R's speak fails
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    key_a, key_r = svc_mod.reminder_item(a).id, svc_mod.reminder_item(r).id
    await service.reminder_tick()
    book = ledger(tmp_path)
    assert book[key_a]["channel"] == "laptop" and not book[key_a].get("source_marked")
    assert book[key_r]["notified"] is True and not book[key_r].get("delivered_at")
    assert [row["status"] for row in source.rows] == ["pending", "pending"]  # R's row is never marked for A

    await service.reminder_tick()
    assert not ledger(tmp_path)[key_r].get("closed_at") and bridge.svc_acked == []  # R is still due: kept open

    bridge.connect_ui()
    await service.watch_tick()
    await service.watch_tick()  # R is delivered on the dashboard and marks its own row, the first in the file
    assert ledger(tmp_path)[key_r]["channel"] == "ui"
    assert [row["status"] for row in source.rows] == ["triggered", "pending"]
    await service.reminder_tick()  # now A is the first pending reminder with the id: its own row
    assert [row["status"] for row in source.rows] == ["triggered", "triggered"]
    assert ledger(tmp_path)[key_a]["source_marked"] is True and laptop.spoken[0][1] == key_a


@pytest.fixture
def x66_reminders(monkeypatch):
    """A private copy of Cursor's real services/automation/jarvis_reminders.py (X-66), executed with stub
    jarvis_task_manager (parse_when only) and jarvis_plugin_manager (a no-op jarvis_tool) modules. Importing the real
    module would import jarvis_task_manager, whose module-level TaskManager() opens the checkout's real
    conversations/tasks.db (PRAGMA journal_mode=WAL, CREATE/ALTER migrations) while the PC worker may use it, and would
    register its tools. ReminderSource's own imports get the copy (sys.modules and the package attribute, both put
    back afterwards); nothing opens a tasks.db meanwhile (checked)."""
    import importlib.util
    import sqlite3

    import services.automation as automation

    opened: list[str] = []
    real_connect = sqlite3.connect

    def watched_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    def parse_when(_text: str, now: Any = None) -> None:  # pylint: disable=unused-argument
        return None

    def jarvis_tool(*args: Any, **_kwargs: Any) -> Any:
        return args[0] if args and callable(args[0]) else (lambda real: real)

    monkeypatch.setattr(sqlite3, "connect", watched_connect)
    stubs = {"services.automation.jarvis_task_manager": {"parse_when": parse_when},
             "services.ai_core.jarvis_plugin_manager": {"jarvis_tool": jarvis_tool}}
    for name, members in stubs.items():
        stub = types.ModuleType(name)
        stub.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.find_spec("services.automation.jarvis_reminders")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert vars(module)["parse_when"] is parse_when  # the stub, never the real task manager
    monkeypatch.setitem(sys.modules, "services.automation.jarvis_reminders", module)
    monkeypatch.setattr(automation, "jarvis_reminders", module, raising=False)
    yield module
    assert not [path for path in opened if Path(path).name.startswith("tasks.db")], opened


async def test_with_the_real_x66_functions_each_reminder_sharing_an_id_marks_its_own_row(tmp_path, monkeypatch,
                                                                                         x66_reminders):
    jarvis_reminders = x66_reminders
    path = tmp_path / "reminders.json"
    monkeypatch.setattr(jarvis_reminders, "REMINDERS_FILE", str(path))
    base = (datetime.now(svc_mod.local_zone()).replace(tzinfo=None) - timedelta(minutes=1)).replace(microsecond=0)
    rid = f"rem_{int(base.timestamp())}"
    r = {"id": rid, "time": (base + timedelta(seconds=0.5)).isoformat(), "message": MARKER, "status": "pending",
         "created_at": (base - timedelta(minutes=10)).isoformat()}
    a = {"id": rid, "time": base.isoformat(), "message": MARKER, "status": "pending",
         "created_at": (base - timedelta(minutes=5)).isoformat()}
    path.write_text(json.dumps([r, a]), encoding="utf-8")  # the file: R, then A
    source = svc_mod.ReminderSource()
    assert [row["time"] for row in source.list_due()] == [a["time"], r["time"]]  # due order: A, then R
    assert [row["time"] for row in source.pending_rows()] == [r["time"], a["time"]]
    bridge, laptop = FakeBridge(), FakeLaptop(online=True, results=["acked", "failed"])
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)

    def statuses() -> list[str]:
        return [row["status"] for row in json.loads(path.read_text(encoding="utf-8"))]

    await service.reminder_tick()  # A is heard; R's speak fails, so R is stored at the bridge
    assert statuses() == ["pending", "pending"] and (tmp_path / "reminders.json.lock").exists()
    bridge.connect_ui()
    await service.watch_tick()
    await service.watch_tick()  # R is acknowledged on the dashboard: its own row
    assert statuses() == ["triggered", "pending"]
    await service.reminder_tick()  # then A's own row
    assert statuses() == ["triggered", "triggered"]
    book = ledger(tmp_path)
    assert book[svc_mod.reminder_item(a).id]["channel"] == "laptop" and book[svc_mod.reminder_item(r).id][
        "channel"] == "ui"


async def test_a_mark_that_did_not_take_is_made_again_so_the_reminder_is_never_delivered_twice(tmp_path):
    now = [time.time()]
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True), FakeReminders(reminder())
    source.lost_marks = 1  # mark_reminder_delivered answers True, but its save failed: the row stays pending
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "laptop" and entry["source_marked"] is True and source.rows[0]["status"] == "pending"

    await service.reminder_tick()  # still due after its mark: it is marked again
    assert source.rows[0]["status"] == "triggered" and source.marks == ["rem_1760000000"] * 2
    now[0] += 31 * 86400  # past LEDGER_KEEP_S
    for _ in range(2):
        await service.reminder_tick()
    assert len(laptop.spoken) == 1 and bridge.announces == [] and bridge.notify_calls == []


async def test_an_entry_whose_item_is_still_due_is_never_dropped(tmp_path):
    now = [time.time()]
    laptop, source = FakeLaptop(online=True), FakeReminders(reminder())
    source.lost_marks = 99  # every mark is lost: reminders.json keeps listing it as due
    service = make_svc(tmp_path, laptop=laptop, reminders=source, clock=lambda: now[0])
    await service.reminder_tick()
    now[0] += 31 * 86400  # the finished entry is past LEDGER_KEEP_S, but its item is still due
    for _ in range(3):
        await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop" and len(source.marks) == 4


async def _cut_announce(tmp_path: Path, bridge: FakeBridge, **kwargs: Any) -> str:
    """A first svc that is stopped (SIGTERM) while it waits for the live job's announce_result; the attempt id."""
    first = make_svc(tmp_path, bridge=bridge, timers=SLOW_ANNOUNCE, **kwargs)
    tick = asyncio.create_task(first.reminder_tick())
    await wait_until(lambda: bridge.announces)
    first.request_stop()
    await asyncio.wait_for(tick, 5)
    [(attempt_id, _text, _kind)] = bridge.announces
    return attempt_id


async def test_a_restart_during_the_announce_wait_settles_that_attempt_and_never_announces_twice(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    bridge.agents = 1  # the live job is connected; its announce_result lands after the svc stopped
    attempt_id = await _cut_announce(tmp_path, bridge, reminders=source)
    [entry] = ledger(tmp_path).values()
    assert [row["outcome"] for row in entry["attempts"]] == ["sent"]  # no 'timeout': the wait was only cut
    assert entry["announce_call"]["id"] == attempt_id

    bridge.announce_results[attempt_id] = {"delivered": True}  # the job did announce it
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source, timers=SLOW_ANNOUNCE)
    await restarted.reminder_tick()
    entry = ledger(tmp_path)[entry["id"]]
    assert len(bridge.announces) == 1 and entry["channel"] == "announce" and "announce_call" not in entry
    assert source.marks == ["rem_1760000000"] and bridge.notify_calls == []


async def test_an_announce_left_open_by_a_restart_is_waited_for_and_its_answer_is_step_1(tmp_path):
    bridge, source, laptop = FakeBridge(), FakeReminders(reminder()), FakeLaptop(online=True)
    bridge.agents = 1
    attempt_id = await _cut_announce(tmp_path, bridge, reminders=source, laptop=laptop)
    assert laptop.spoken == []  # the stopping svc went no further

    async def the_job_answers_later() -> None:
        await asyncio.sleep(0.1)
        bridge.announce_results[attempt_id] = {"delivered": False, "reason": "no_participant"}

    answer = asyncio.create_task(the_job_answers_later())
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source, laptop=laptop, timers=SLOW_ANNOUNCE)
    await restarted.reminder_tick()
    await answer
    [entry] = ledger(tmp_path).values()
    assert len(bridge.announces) == 1 and entry["channel"] == "laptop" and len(laptop.spoken) == 1
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["sent", "no_participant"]


async def test_an_announce_left_open_past_its_window_is_a_timeout_and_no_new_announce(tmp_path):
    bridge, source, laptop = FakeBridge(), FakeReminders(reminder()), FakeLaptop(online=True)
    bridge.agents = 1
    await _cut_announce(tmp_path, bridge, reminders=source, laptop=laptop)
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source, laptop=laptop, timers=SLOW_ANNOUNCE,
                         clock=lambda: time.time() + 60)  # back long after the 30 s window; no result came
    await restarted.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert len(bridge.announces) == 1 and entry["channel"] == "laptop"
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["sent", "timeout"]


async def test_an_open_announce_whose_first_poll_gets_no_answer_is_still_settled_before_the_laptop(tmp_path):
    bridge, source, laptop = FakeBridge(), FakeReminders(reminder()), FakeLaptop(online=True)
    bridge.agents = 1
    await _cut_announce(tmp_path, bridge, reminders=source, laptop=laptop)
    bridge.fail_polls = 1  # the restarted svc's first poll gets no answer (a 429 of the shared bucket, a timeout)
    late = SLOW_ANNOUNCE.announce_wait_s - 0.3  # back with 0.3 s of the attempt's window left; no result comes
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source, laptop=laptop, timers=SLOW_ANNOUNCE,
                         clock=lambda: time.time() + late)
    await restarted.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert len(bridge.announces) == 1 and entry["channel"] == "laptop" and len(laptop.spoken) == 1
    assert "announce_call" not in entry and bridge.notify_calls == []
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["sent", "timeout"]


def _leave_announce_open(service: AgentServices, key: str, attempt_id: str, ts: float) -> None:
    """The ledger state of an announce whose result was never read on an item already stored at the bridge."""
    service.ledger.update(lambda items: items[key].update(announce_call={"id": attempt_id, "ts": ts}))


async def test_a_laptop_hello_settles_an_open_announce_of_a_notified_item_once_its_window_ends(tmp_path, records):
    now = [time.time()]
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    await service.reminder_tick()  # stored at the bridge: no job, the laptop asleep
    [key] = ledger(tmp_path)
    _leave_announce_open(service, key, f"{key}#a7", now[0])
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")  # inside the attempt's window the job may still announce it: held
    await wait_until(lambda: any("held" in record.getMessage() for record in records))
    assert laptop.spoken == []
    now[0] += FAST.announce_wait_s + 1  # its window ends without a result
    await drain(service)  # the held item's step 2 after late_wait_s
    entry = ledger(tmp_path)[key]
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop" and "announce_call" not in entry
    assert [row.get("ref") for row in entry["attempts"] if row["outcome"] == "timeout"] == [f"{key}#a7"]


async def test_a_laptop_hello_reads_the_stored_result_of_an_open_announce_and_speaks_nothing(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    [key] = ledger(tmp_path)
    bridge.announce_results[f"{key}#a7"] = {"delivered": True}  # the job did announce it
    _leave_announce_open(service, key, f"{key}#a7", time.time())
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")
    await drain(service)
    entry = ledger(tmp_path)[key]
    assert laptop.spoken == [] and entry["channel"] == "announce" and "announce_call" not in entry
    assert bridge.svc_acked == [key] and source.marks == ["rem_1760000000"]


async def _cut_speak(tmp_path: Path, bridge: FakeBridge, source: FakeReminders) -> dict[str, Any]:
    """A first svc stopped while the laptop speaks the item (no job, no UI then): its entry, laptop_call left."""

    class StuckLaptop(FakeLaptop):
        async def speak(self, text: str, item_id: str, call_id: str | None = None) -> str:
            await super().speak(text, item_id, call_id)
            await asyncio.Event().wait()
            return "acked"

    stuck = StuckLaptop(online=True)
    first = make_svc(tmp_path, bridge=bridge, laptop=stuck, reminders=source)
    tick = asyncio.create_task(first.reminder_tick())
    await wait_until(lambda: stuck.spoken)
    tick.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tick
    [entry] = ledger(tmp_path).values()
    assert entry["laptop_call"]["id"] == stuck.call_ids[0]
    return entry


async def test_a_speak_cut_by_a_restart_waits_for_the_first_hello_even_after_late_wait(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    entry = await _cut_speak(tmp_path, bridge, source)
    bridge.agents, bridge.job_answer = 1, {"delivered": True}  # now a live job would announce it ...
    bridge.connect_ui()  # ... and an open dashboard would show it
    laptop = FakeLaptop(online=True)
    frozen = entry["laptop_call"]["ts"] + FAST.late_wait_s + 1.0  # the restart took longer than late_wait_s
    restarted = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: frozen)
    await restarted.reminder_tick()  # the new link has seen no node hello yet: the speak's outcome may still come
    assert bridge.announces == [] and bridge.notify_calls == [] and laptop.spoken == []
    restarted.on_node_session("pc:laptop-1")  # the first hello, then its result_resend (reason 'restart')
    restarted.on_late_outcome(types.SimpleNamespace(
        call_id=entry["laptop_call"]["id"], tool="pc.speak_local", reason="restart", state="result",
        status="completed", effect="done", result={"status": "success", "tool": "pc.speak_local"}, label={}))
    await drain(restarted)
    done = ledger(tmp_path)[entry["id"]]
    assert done["channel"] == "laptop" and "laptop_call" not in done and laptop.spoken == []
    assert bridge.announces == [] and bridge.notify_calls == [] and source.marks == ["rem_1760000000"]


async def test_a_speak_cut_by_a_restart_is_held_at_most_restart_hold_s(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    entry = await _cut_speak(tmp_path, bridge, source)
    bridge.agents, bridge.job_answer = 1, {"delivered": True}
    later = entry["laptop_call"]["ts"] + FAST.restart_hold_s + 1  # no hello for longer than a result is kept
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source, clock=lambda: later)
    await restarted.reminder_tick()
    done = ledger(tmp_path)[entry["id"]]
    assert done["channel"] == "announce" and len(bridge.announces) == 1


async def test_a_speak_cut_by_a_restart_is_acked_by_its_restart_result_and_not_spoken_again(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())

    class StuckLaptop(FakeLaptop):
        async def speak(self, text: str, item_id: str, call_id: str | None = None) -> str:
            await super().speak(text, item_id, call_id)
            await asyncio.Event().wait()  # the svc is stopped while the laptop speaks
            return "acked"

    stuck = StuckLaptop(online=True)
    first = make_svc(tmp_path, bridge=bridge, laptop=stuck, reminders=source)
    tick = asyncio.create_task(first.reminder_tick())
    await wait_until(lambda: stuck.spoken)
    tick.cancel()  # _shutdown cancels the step in flight: no outcome is written
    with contextlib.suppress(asyncio.CancelledError):
        await tick
    [entry] = ledger(tmp_path).values()
    call_id = entry["laptop_call"]["id"]  # in the ledger before the call was submitted
    assert stuck.call_ids == [call_id] and [row["step"] for row in entry["attempts"]] == ["announce"]

    laptop = FakeLaptop(online=True)
    frozen = entry["laptop_call"]["ts"] + 0.1  # the restarted svc, inside late_wait_s of that call
    restarted = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: frozen)
    await restarted.reminder_tick()  # its late result may still ack it: no ladder run now
    restarted.on_node_session("pc:laptop-1")  # the new link's hello comes first ...
    restarted.on_late_outcome(types.SimpleNamespace(  # ... then the node's result_resend: reason 'restart', no label
        call_id=call_id, tool="pc.speak_local", reason="restart", state="result", status="completed", effect="done",
        result={"status": "success", "tool": "pc.speak_local"}, label={}))
    await drain(restarted)
    entry = ledger(tmp_path)[entry["id"]]
    assert laptop.spoken == [] and entry["channel"] == "laptop" and "laptop_call" not in entry
    assert bridge.announces == [] and bridge.notify_calls == [] and source.marks == ["rem_1760000000"]


async def test_an_unknown_speak_waits_for_its_late_result_at_the_next_hello_and_is_spoken_once(tmp_path, records):
    now = [time.time()]
    bridge, laptop = FakeBridge(), FakeLaptop(online=True, results=["unknown"])
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()),
                       clock=lambda: now[0])
    await service.reminder_tick()  # spoken, then the lid was closed: outcome unknown (held: not stored at the bridge)
    [entry] = ledger(tmp_path).values()
    call_id = entry["laptop_call"]["id"]
    now[0] += 3600  # the laptop is back an hour later
    service.on_node_session("pc:laptop-1")  # its hello comes before the hello's result_resend
    await wait_until(lambda: any("held" in record.getMessage() for record in records))
    service.on_late_outcome(types.SimpleNamespace(call_id=call_id, label={"svc_item": entry["id"]}, reason="unknown",
                                                  state="result", result={"status": "success"}))
    await drain(service)
    entry = ledger(tmp_path)[entry["id"]]
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop" and "laptop_call" not in entry


async def test_an_unknown_speak_without_a_late_result_is_spoken_again_after_the_wait(tmp_path, records):
    now = [time.time()]
    bridge, laptop = FakeBridge(), FakeLaptop(online=True, results=["unknown"])
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()),
                       clock=lambda: now[0])
    await service.reminder_tick()
    now[0] += 3600
    service.on_node_session("pc:laptop-1")
    await wait_until(lambda: any("held" in record.getMessage() for record in records))
    assert len(laptop.spoken) == 1
    now[0] += FAST.late_wait_s + 1  # no late outcome within the wait
    await drain(service)
    [entry] = ledger(tmp_path).values()
    assert len(laptop.spoken) == 2 and entry["channel"] == "laptop" and "laptop_call" not in entry


async def test_a_late_end_that_is_no_success_releases_the_item_for_the_laptop(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=True, results=["unknown"])
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=FakeReminders(reminder()))
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    service.on_late_outcome(types.SimpleNamespace(call_id=entry["laptop_call"]["id"], label={"svc_item": entry["id"]},
                                                  reason="unknown", state="not_started", result=None))
    service.on_late_outcome(types.SimpleNamespace(call_id="c" + "9" * 32, tool="open_app", reason="restart",
                                                  state="result", result={"status": "success"}, label={}))
    await drain(service)
    entry = ledger(tmp_path)[entry["id"]]
    assert "laptop_call" not in entry and entry["attempts"][-1]["outcome"] == "late_not_started"
    assert not entry.get("delivered_at")  # another call's 'restart' result acks nothing here
    service.on_node_session("pc:laptop-1")  # it never ran: spoken at once on the next hello
    await drain(service)
    assert len(laptop.spoken) == 2 and ledger(tmp_path)[entry["id"]]["channel"] == "laptop"


def _late_success(key: str, call_id: str | None) -> types.SimpleNamespace:
    """The DesktopLink 'late' outcome of a pc.speak_local that was answered unknown and did speak."""
    return types.SimpleNamespace(label={"svc_item": key}, call_id=call_id, reason="unknown", state="result",
                                 result={"status": "success", "tool": "pc.speak_local"})


async def test_an_unknown_speak_sends_no_reminder_due_to_a_dashboard_already_open(tmp_path):
    """The laptop wakes on flaky Wi-Fi: its node speaks the reminder but acknowledges the call too late (outcome
    unknown) while the Electron dashboard on that laptop is already connected. No step 3 goes out before the speak's
    late outcome (the dashboard would toast reminder_due and acknowledge it: the item twice)."""
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True, result="unknown"), FakeReminders(reminder())
    bridge.connect_ui()
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert len(laptop.spoken) == 1 and entry["laptop_call"]["id"] == laptop.call_ids[0]
    assert bridge.notify_calls == [] and bridge.emitted == [] and not entry.get("notified")
    for _ in range(2):  # later ticks and watch polls while the speak may still be acknowledged
        await service.reminder_tick()
        await service.watch_tick()
    assert bridge.notify_calls == [] and bridge.announces == [] and len(laptop.spoken) == 1
    service.on_late_outcome(_late_success(entry["id"], laptop.call_ids[0]))
    await drain(service)
    entry = ledger(tmp_path)[entry["id"]]
    assert entry["channel"] == "laptop" and bridge.emitted == [] and bridge.acked == {}
    assert source.marks == ["rem_1760000000"]


async def test_after_the_hold_of_an_unknown_speak_the_ladder_goes_on_to_step_3(tmp_path):
    now = [time.time()]
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True, result="unknown"), FakeReminders(reminder())
    bridge.connect_ui()
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    await service.reminder_tick()
    assert bridge.emitted == [] and len(laptop.spoken) == 1
    laptop.is_online = False  # the lid closes and no late outcome ever comes
    now[0] += FAST.restart_hold_s + 1  # this process saw no node hello: the longest hold, then it may go again
    await service.reminder_tick()  # the ladder runs again, step 3 included: the open dashboard acknowledges it
    await service.watch_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["channel"] == "ui" and len(bridge.emitted) == 1 and len(laptop.spoken) == 1


async def _speak_unknown_at_a_hello(tmp_path: Path, now: list[float]) -> tuple[AgentServices, FakeBridge, FakeLaptop,
                                                                                FakeReminders, str]:
    """An item stored at the bridge while the laptop slept; then the node's hello: step 2 speaks it, outcome unknown."""
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False, result="unknown"), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    await service.reminder_tick()
    [key] = ledger(tmp_path)
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")
    await drain(service)
    assert len(laptop.spoken) == 1 and ledger(tmp_path)[key]["laptop_call"]["id"] == laptop.call_ids[0]
    return service, bridge, laptop, source, key


async def test_a_ui_connect_while_an_unknown_speak_is_pending_sends_no_reminder_due(tmp_path):
    now = [time.time()]
    service, bridge, laptop, source, key = await _speak_unknown_at_a_hello(tmp_path, now)
    sent = len(bridge.notify_calls)
    bridge.connect_ui()  # the dashboard reconnects a moment later
    await service.watch_tick()  # the connect is seen while the speak may still be acknowledged
    assert len(bridge.notify_calls) == sent and bridge.acked == {}  # no reminder_due: no toast, no ack
    service.on_late_outcome(_late_success(key, laptop.call_ids[0]))  # the node did speak it
    await drain(service)
    entry = ledger(tmp_path)[key]
    assert entry["channel"] == "laptop" and len(bridge.notify_calls) == sent and bridge.acked == {key: "svc"}
    assert source.marks == ["rem_1760000000"]


async def test_a_ui_connect_held_by_an_unknown_speak_gets_reminder_due_once_its_hold_ends(tmp_path):
    now = [time.time()]
    service, bridge, laptop, _source, key = await _speak_unknown_at_a_hello(tmp_path, now)
    sent = len(bridge.notify_calls)
    bridge.connect_ui()
    await service.watch_tick()
    assert len(bridge.notify_calls) == sent
    now[0] += FAST.late_wait_s + 1  # no late outcome within the hold
    await drain(service)  # the held UI retry: step 3 again, and the open dashboard acknowledges it
    await service.watch_tick()
    entry = ledger(tmp_path)[key]
    assert entry["channel"] == "ui" and len(bridge.notify_calls) == sent + 1 and len(laptop.spoken) == 1


# ---------------------------------------------------------------- lost answers, kills, clock steps, bridge restarts


async def test_a_step_3_whose_answer_was_lost_is_delivered_by_the_dashboards_ack_and_never_announced(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    bridge.connect_ui()  # the open dashboard shows reminder_due and acknowledges it at once ...
    bridge.lost_notifies = 1  # ... but the bridge's answer to step 3 is lost (a 5 s read timeout)
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    [key] = ledger(tmp_path)
    assert ledger(tmp_path)[key]["notified"] is True and ledger(tmp_path)[key]["notify_unknown"] is True
    bridge.agents, bridge.job_answer = 1, {"delivered": True}  # now a live job would announce it ...
    laptop.is_online = True  # ... and the laptop is awake
    await service.reminder_tick()  # step 3 again (idempotent at the bridge): acknowledged before
    service.on_node_session("pc:laptop-1")
    await drain(service)
    await service.watch_tick()
    entry = ledger(tmp_path)[key]
    assert entry["channel"] == "ui" and bridge.announces == [] and laptop.spoken == []
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "notify"] == ["unknown", "exists"]
    assert source.marks == ["rem_1760000000"] and "notify_unknown" not in entry


async def test_a_step_3_cut_by_a_stop_counts_as_stored_after_the_restart(tmp_path):
    class StuckBridge(FakeBridge):
        stuck = True

        async def notify(self, payload: dict[str, Any]) -> NotifyResult | None:
            result = await super().notify(payload)  # stored, emitted, acknowledged by the open dashboard ...
            if self.stuck:
                await asyncio.Event().wait()  # ... and the svc is stopped before the bridge's answer arrives
            return result

    bridge, source = StuckBridge(), FakeReminders(reminder())
    bridge.connect_ui()
    first = make_svc(tmp_path, bridge=bridge, reminders=source)
    tick = asyncio.create_task(first.reminder_tick())
    await wait_until(lambda: bridge.notify_calls)
    tick.cancel()  # _shutdown cancels the step in flight: no answer is recorded
    with contextlib.suppress(asyncio.CancelledError):
        await tick
    [entry] = ledger(tmp_path).values()
    assert entry["notified"] is True and entry["notify_unknown"] is True  # in the ledger before it was sent
    bridge.stuck = False
    bridge.agents, bridge.job_answer = 1, {"delivered": True}  # the restarted svc finds a live job
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source)
    await restarted.reminder_tick()
    entry = ledger(tmp_path)[entry["id"]]
    assert entry["channel"] == "ui" and bridge.announces == [] and source.marks == ["rem_1760000000"]


async def test_a_lost_step_3_answer_is_sent_again_until_the_bridge_answers(tmp_path):
    class DroppingBridge(FakeBridge):
        drops = 1

        async def notify(self, payload: dict[str, Any]) -> NotifyResult | None:
            if self.drops:  # a 504 of the proxy: the bridge never had it, but that cannot be told from a lost answer
                self.drops -= 1
                return NotifyResult(stored=svc_mod.NOTIFY_UNKNOWN)
            return await super().notify(payload)

    bridge, source = DroppingBridge(), FakeReminders(reminder())
    bridge.connect_ui()  # the dashboard stays open all along
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()
    [key] = ledger(tmp_path)
    assert bridge.notify_calls == [] and ledger(tmp_path)[key]["notify_unknown"] is True
    await service.reminder_tick()  # sent again: stored and emitted, and the open dashboard acknowledges it
    assert len(bridge.notify_calls) == 1 and "notify_unknown" not in ledger(tmp_path)[key]
    await service.watch_tick()
    assert ledger(tmp_path)[key]["channel"] == "ui" and source.marks == ["rem_1760000000"]


class Killed(BaseException):
    """The process dies (SIGKILL, a power cut) right after a ledger write."""


@pytest.mark.parametrize("channel", ["announce", "laptop"])
async def test_a_kill_right_after_the_ack_row_never_delivers_the_item_twice(tmp_path, monkeypatch, channel):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True), FakeReminders(reminder())
    if channel == "announce":
        bridge.agents, bridge.job_answer = 1, {"delivered": True}
    real_update, real_read = DeliveryLedger.update, DeliveryLedger.read

    def update_then_die(self: DeliveryLedger, change: Any) -> Any:
        result = real_update(self, change)
        if any(row.get("outcome") == "acked" for entry in real_read(self).values() for row in entry["attempts"]):
            raise Killed()  # right after the write that holds the ack row
        return result

    monkeypatch.setattr(DeliveryLedger, "update", update_then_die)
    with pytest.raises(Killed):
        await make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source).reminder_tick()
    monkeypatch.setattr(DeliveryLedger, "update", real_update)
    [entry] = ledger(tmp_path).values()
    assert entry.get("delivered_at") and entry["channel"] == channel  # the ack row and delivered_at: one write
    assert "announce_call" not in entry and "laptop_call" not in entry
    await make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source).reminder_tick()  # the restarted svc
    assert len(bridge.announces) + len(laptop.spoken) == 1 and source.marks == ["rem_1760000000"]


async def test_a_laptop_hello_right_after_a_ui_connect_waits_for_the_dashboards_ack_and_speaks_nothing(tmp_path,
                                                                                                     records):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()  # stored at the bridge while the laptop sleeps and no dashboard is open
    [key] = ledger(tmp_path)
    bridge.ui_auto_ack = False  # the dashboard's notification_ack is still on its way ...
    bridge.connect_ui()  # ... after it reconnected on wake
    await service.watch_tick()  # reminder_due again: the dashboard toasts it
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")  # the node's hello takes the ladder lock right after
    await wait_until(lambda: any("held" in record.getMessage() for record in records))
    assert laptop.spoken == []
    bridge.acked[key] = "ui"  # the dashboard's ack lands
    await drain(service)
    entry = ledger(tmp_path)[key]
    assert laptop.spoken == [] and entry["channel"] == "ui" and source.marks == ["rem_1760000000"]
    assert entry["ui_emit_clients"] == 1


async def test_an_item_sent_again_to_a_dashboard_that_never_acknowledges_it_is_spoken_after_the_grace(tmp_path,
                                                                                                    records):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()
    bridge.ui_auto_ack = False  # a UI socket that never acknowledges reminder_due
    bridge.connect_ui()
    await service.watch_tick()
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")
    await wait_until(lambda: any("held" in record.getMessage() for record in records))
    await drain(service)  # the held item's step 2 after the grace: no ack came, so the laptop speaks it
    [entry] = ledger(tmp_path).values()
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop" and bridge.svc_acked == [entry["id"]]


async def test_an_item_held_again_at_its_retry_is_retried_until_the_laptop_speaks_it(tmp_path, records):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()  # stored at the bridge while the laptop sleeps
    [key] = ledger(tmp_path)
    laptop.is_online = True
    bridge.fail_polls = 2  # the hello's poll and the first retry's poll get no answer (a 429 burst, a bridge restart)
    service.on_node_session("pc:laptop-1")
    await wait_until(lambda: any("held" in record.getMessage() for record in records))
    await drain(service)
    entry = ledger(tmp_path)[key]
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop" and not service._spawned


def _ui_emit_without_ack(service: AgentServices, bridge: FakeBridge) -> Any:
    """reminder_due goes again to a connected UI socket that never acknowledges it (a legacy socket, or a dashboard
    whose ack was lost)."""
    bridge.ui_auto_ack = False
    bridge.connect_ui()
    return service.watch_tick()


async def test_a_clock_stepped_back_never_stretches_the_ui_grace(tmp_path):
    now = [time.time()]
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    await service.reminder_tick()
    [key] = ledger(tmp_path)
    await _ui_emit_without_ack(service, bridge)
    assert ledger(tmp_path)[key]["ui_emit_clients"] == 1
    now[0] -= 600  # NTP steps the VPS clock back 10 minutes
    laptop.is_online = True
    service.on_node_session("pc:laptop-1")
    await drain(service)  # held for the grace at most (it runs on the monotonic clock), then spoken
    entry = ledger(tmp_path)[key]
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop"


async def test_a_restarted_svc_counts_a_ui_emit_stamped_ahead_of_its_clock_as_elapsed(tmp_path):
    now = [time.time()]
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=False), FakeReminders(reminder())
    first = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    await first.reminder_tick()
    await _ui_emit_without_ack(first, bridge)
    now[0] -= 600  # the clock steps back and the svc restarts: only the ledger's stamp is left, 10 min ahead
    laptop.is_online = True
    restarted = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, clock=lambda: now[0])
    restarted.on_node_session("pc:laptop-1")
    await drain(restarted)
    [entry] = ledger(tmp_path).values()
    assert len(laptop.spoken) == 1 and entry["channel"] == "laptop"


async def test_a_clock_stepped_back_never_throttles_an_item(tmp_path):
    now = [time.time()]
    bridge, source = FakeBridge(), FakeReminders(reminder())
    bridge.down = True
    service = make_svc(tmp_path, bridge=bridge, reminders=source, clock=lambda: now[0],
                       timers=dataclasses.replace(FAST, retry_throttle_s=25.0))
    await service.reminder_tick()  # the bridge is down: nothing stored, tried again after retry_throttle_s
    [key] = ledger(tmp_path)
    bridge.down = False
    now[0] += 10
    await service.reminder_tick()  # inside retry_throttle_s: not yet
    assert not ledger(tmp_path)[key].get("notified")
    now[0] -= 600  # the clock steps back 10 minutes: the last try's stamp lies ahead, which counts as elapsed
    await service.reminder_tick()
    assert ledger(tmp_path)[key]["notified"] is True and len(bridge.notify_calls) == 1


async def test_an_open_announce_is_settled_as_a_timeout_only_after_a_poll_the_bridge_answered(tmp_path):
    bridge, source, laptop = FakeBridge(), FakeReminders(reminder()), FakeLaptop(online=True)
    bridge.agents = 1
    attempt_id = await _cut_announce(tmp_path, bridge, reminders=source, laptop=laptop)
    bridge.restart()
    bridge.down = True  # a deploy restarts the bridge and the svc: the restarted svc's polls get no answer yet
    restarted = make_svc(tmp_path, bridge=bridge, reminders=source, laptop=laptop, timers=SLOW_ANNOUNCE,
                         clock=lambda: time.time() + 60)  # back after the attempt's 30 s window
    await restarted.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert entry["announce_call"]["id"] == attempt_id and laptop.spoken == [] and bridge.notify_calls == []
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "announce"] == ["sent"]
    bridge.down = False
    bridge.announce_results[attempt_id] = {"delivered": True}  # the job's result, kept across the bridge restart
    await restarted.reminder_tick()
    entry = ledger(tmp_path)[entry["id"]]
    assert entry["channel"] == "announce" and laptop.spoken == [] and len(bridge.announces) == 1


async def test_an_announce_wait_that_ends_while_the_bridge_is_down_neither_speaks_nor_stores_it(tmp_path):
    bridge, laptop, source = FakeBridge(), FakeLaptop(online=True), FakeReminders(reminder())
    bridge.agents, bridge.job_answer = 1, {"delivered": True}  # the job announces it, ...
    bridge.down_after_announce = True  # ... but the bridge goes down before the svc could read that result
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source)
    await service.reminder_tick()  # the wait ends without an answered poll
    [entry] = ledger(tmp_path).values()
    assert laptop.spoken == [] and bridge.notify_calls == [] and "announce_call" in entry
    assert [row["outcome"] for row in entry["attempts"]] == ["sent"]
    bridge.down = bridge.down_after_announce = False  # the bridge is back
    await service.reminder_tick()
    entry = ledger(tmp_path)[entry["id"]]
    assert entry["channel"] == "announce" and laptop.spoken == [] and len(bridge.announces) == 1


async def test_a_reminder_not_due_after_the_clock_stepped_back_is_never_closed_as_gone(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()  # stored at the bridge: no job, no laptop, no dashboard
    [key] = ledger(tmp_path)
    source.clock_offset = timedelta(minutes=-5)  # the clock steps back: its time lies ahead again for a while
    await service.reminder_tick()
    await service.watch_tick()
    entry = ledger(tmp_path)[key]
    assert not entry.get("closed_at") and bridge.svc_acked == [] and service._due_counts["reminder"] == 0
    source.clock_offset = timedelta(0)  # due again
    bridge.connect_ui()
    await service.reminder_tick()
    await service.watch_tick()
    await service.watch_tick()
    entry = ledger(tmp_path)[key]
    assert entry["channel"] == "ui" and source.marks == ["rem_1760000000"]


async def test_a_dashboard_that_reconnects_after_a_bridge_restart_gets_the_item_again(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    bridge.connect_ui()
    bridge.ui_auto_ack = False  # the open dashboard shows reminder_due but sends no ack (a background tab)
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    await service.reminder_tick()
    await service.watch_tick()  # the baseline: boot-1, 1 connect, 1 UI socket
    [key] = ledger(tmp_path)
    emitted = len(bridge.emitted)
    bridge.restart()  # a deploy restarts the bridge: its connect counter starts again from 0 ...
    bridge.connect_ui()  # ... and the dashboard reconnects before the next poll: 1 connect, 1 UI socket again
    bridge.ui_auto_ack = True
    await service.watch_tick()  # a new boot id: every UI connected now came after the restart
    assert len(bridge.emitted) == emitted + 1
    await service.watch_tick()
    assert ledger(tmp_path)[key]["channel"] == "ui" and source.marks == ["rem_1760000000"]


# ---------------------------------------------------------------- e-mail (step 4)


async def test_the_email_step_is_off_at_0(tmp_path):
    email = FakeEmail()
    service = make_svc(tmp_path, email=email, reminders=FakeReminders(reminder(minutes_ago=600)), email_after_min=0)
    await service.reminder_tick()
    await service.email_tick()
    assert email.sent == []


async def test_the_email_goes_once_after_n_minutes_and_is_no_ack(tmp_path):
    email, source = FakeEmail(), FakeReminders(reminder(minutes_ago=3))
    service = make_svc(tmp_path, email=email, reminders=source, email_after_min=2)
    await service.reminder_tick()  # notified, then the e-mail of the same tick
    await service.reminder_tick()
    await service.email_tick()
    assert len(email.sent) == 1 and MARKER in email.sent[0][1]
    [entry] = ledger(tmp_path).values()
    assert entry["emailed_at"] and not entry.get("delivered_at") and source.marks == []


async def test_no_email_before_n_minutes(tmp_path):
    email = FakeEmail()
    service = make_svc(tmp_path, email=email, reminders=FakeReminders(reminder(minutes_ago=1)), email_after_min=30)
    await service.reminder_tick()
    assert email.sent == []


async def test_the_email_also_goes_while_the_bridge_is_down(tmp_path):
    email, bridge = FakeEmail(), FakeBridge()
    bridge.down = True  # step 3 keeps failing: just when the e-mail helps most
    service = make_svc(tmp_path, email=email, bridge=bridge, reminders=FakeReminders(reminder(minutes_ago=120)),
                       email_after_min=5)
    await service.reminder_tick()
    await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert len(email.sent) == 1 and not entry.get("notified") and entry["emailed_at"]


def _full_disk(_self: Any, _items: Any) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


async def test_no_email_goes_while_the_ledger_cannot_record_it(tmp_path, monkeypatch):
    email = FakeEmail()
    service = make_svc(tmp_path, email=email, reminders=FakeReminders(reminder(minutes_ago=10)), email_after_min=0)
    await service.reminder_tick()  # stored at the bridge while the e-mail step is still off
    service.email_after_min = 5
    monkeypatch.setattr(DeliveryLedger, "_write_unlocked", _full_disk)
    for _ in range(4):
        with contextlib.suppress(OSError):
            await service.reminder_tick()
    assert email.sent == []  # never one e-mail per tick
    monkeypatch.undo()
    for _ in range(3):
        await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert len(email.sent) == 1 and entry["emailed_at"]
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "email"] == ["sent"]


async def test_an_email_whose_result_cannot_be_written_is_not_sent_again(tmp_path, monkeypatch):
    email = FakeEmail()
    service = make_svc(tmp_path, email=email, reminders=FakeReminders(reminder(minutes_ago=10)), email_after_min=0)
    await service.reminder_tick()
    service.email_after_min = 5
    real_write = DeliveryLedger._write_unlocked
    writes: list[int] = []

    def claim_then_full_disk(self: DeliveryLedger, items: Any) -> None:
        writes.append(1)
        if len(writes) > 1:
            _full_disk(self, items)
        real_write(self, items)

    monkeypatch.setattr(DeliveryLedger, "_write_unlocked", claim_then_full_disk)
    for _ in range(4):
        with contextlib.suppress(OSError):
            await service.reminder_tick()
    assert len(email.sent) == 1 and len(writes) == 2  # the claim, then the result that failed: nothing more


class FakeSmtp(socketserver.ThreadingTCPServer):
    """A minimal SMTP server on 127.0.0.1 that counts the messages it took. quit_code answers QUIT, rcpt_code answers
    RCPT, final_code answers the end of DATA; with drop_after_data it takes the message and drops the connection before
    its reply (a lost 250)."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *, quit_code: int = 221, rcpt_code: int = 250, final_code: int = 250,
                 drop_after_data: bool = False) -> None:
        self.quit_code, self.rcpt_code, self.final_code = quit_code, rcpt_code, final_code
        self.drop_after_data = drop_after_data
        self.accepted = 0
        super().__init__(("127.0.0.1", 0), _SmtpHandler)


class _SmtpHandler(socketserver.StreamRequestHandler):
    server: FakeSmtp

    def _say(self, line: str) -> None:
        self.wfile.write(f"{line}\r\n".encode("ascii"))

    def handle(self) -> None:
        fake = self.server
        self._say("220 fake ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            verb = line.split(b" ", 1)[0].strip().upper()
            if verb in (b"EHLO", b"HELO"):
                self._say("250 fake")
            elif verb in (b"MAIL", b"RSET"):
                self._say("250 ok")
            elif verb == b"RCPT":
                self._say(f"{fake.rcpt_code} rcpt")
            elif verb == b"DATA":
                self._say("354 go ahead")
                while self.rfile.readline() not in (b".\r\n", b""):
                    pass
                if fake.drop_after_data:
                    fake.accepted += 1
                    return  # taken, but the 250 never reaches the client
                if fake.final_code == 250:
                    fake.accepted += 1
                self._say(f"{fake.final_code} done")
            elif verb == b"QUIT":
                self._say(f"{fake.quit_code} bye")
                return
            else:
                self._say("502 not here")


@contextlib.contextmanager
def fake_smtp(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Iterator[FakeSmtp]:
    """OwnerEmail talks to a FakeSmtp: throwaway credentials, STARTTLS and login stubbed on the client (the fake has
    no TLS), and a fixed local host name (no DNS lookup)."""
    server = FakeSmtp(**kwargs)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    monkeypatch.setenv("EMAIL_USER", "owner@example.invalid")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "test-only-not-a-password")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", str(server.server_address[1]))
    monkeypatch.setattr(smtplib.SMTP, "starttls", lambda self, *args, **kw: (220, b"ready"))
    monkeypatch.setattr(smtplib.SMTP, "login", lambda self, user, password, **kw: (235, b"ok"))
    monkeypatch.setattr(socket, "getfqdn", lambda *args: "svc.test")
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize(("server", "expected", "accepted"), [
    ({}, "sent", 1),
    ({"quit_code": 421}, "sent", 1),  # taken; only QUIT failed afterwards
    ({"drop_after_data": True}, "unknown", 1),  # taken, but the 250 reply was lost: never sent again
    ({"final_code": 451}, "failed", 0),  # refused at the end of DATA: not taken, so it may be tried again
    ({"rcpt_code": 550}, "failed", 0),  # refused before DATA
])
def test_owner_email_tells_sent_failed_and_unknown(monkeypatch, server, expected, accepted):
    with fake_smtp(monkeypatch, **server) as smtp:
        assert svc_mod.OwnerEmail().send("JARVIS reminder", "Reminder: x") == expected
    assert smtp.accepted == accepted


@pytest.mark.parametrize(("server", "outcomes"), [
    ({"quit_code": 421}, ["sent"]),
    ({"drop_after_data": True}, ["unknown"]),
])
async def test_an_email_the_server_may_have_taken_is_never_sent_again(tmp_path, monkeypatch, server, outcomes):
    with fake_smtp(monkeypatch, **server) as smtp:
        service = make_svc(tmp_path, email=svc_mod.OwnerEmail(), reminders=FakeReminders(reminder(minutes_ago=10)),
                           email_after_min=5)
        for _ in range(4):
            await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert smtp.accepted == 1 and entry["emailed_at"]
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "email"] == outcomes


async def test_an_email_refused_before_data_is_tried_again_at_most_three_times(tmp_path, monkeypatch):
    with fake_smtp(monkeypatch, rcpt_code=550) as smtp:
        service = make_svc(tmp_path, email=svc_mod.OwnerEmail(), reminders=FakeReminders(reminder(minutes_ago=10)),
                           email_after_min=5)
        for _ in range(5):
            await service.reminder_tick()
    [entry] = ledger(tmp_path).values()
    assert smtp.accepted == 0 and "emailed_at" not in entry
    assert [row["outcome"] for row in entry["attempts"] if row["step"] == "email"] == ["failed"] * 3


# ---------------------------------------------------------------- tasks (C-56 rule) and the C-80 hook


async def test_a_due_task_is_completed_only_after_its_delivery(tmp_path):
    bridge, tasks = FakeBridge(), FakeTasks(task())
    service = make_svc(tmp_path, bridge=bridge, tasks=tasks)
    await service.task_tick()
    assert tasks.rows[7]["status"] == "triggered" and tasks.completed == []
    bridge.connect_ui()
    await service.watch_tick()
    await service.watch_tick()
    assert tasks.completed == [7] and tasks.rows[7]["status"] == "completed"
    await service.task_tick()
    assert tasks.completed == [7] and len(bridge.notify_calls) == 2
    assert bridge.notify_calls[0]["kind"] == "task"


async def test_a_task_left_triggered_by_a_crash_is_picked_up(tmp_path):
    left = task(9)
    left["status"] = "triggered"
    tasks, laptop = FakeTasks(left), FakeLaptop(online=True)
    service = make_svc(tmp_path, tasks=tasks, laptop=laptop)
    await service.task_tick()
    assert tasks.completed == [9] and "scheduled task" in laptop.spoken[0][0]


async def test_a_task_action_is_not_run_without_the_c80_runner(tmp_path):
    tasks, laptop = FakeTasks(task(3, action="get_weather")), FakeLaptop(online=True)
    service = make_svc(tmp_path, tasks=tasks, laptop=laptop)
    await service.task_tick()
    assert tasks.completed == [3] and laptop.actions == []


async def test_with_the_c80_runner_the_action_is_handed_over_once(tmp_path):
    tasks, laptop, bridge = FakeTasks(task(3, action="get_weather")), FakeLaptop(online=True), FakeBridge()
    rows: list[dict[str, Any]] = []

    async def runner(row: dict[str, Any]) -> None:
        rows.append(row)

    service = make_svc(tmp_path, tasks=tasks, laptop=laptop, bridge=bridge)
    service.action_runner = runner
    await service.task_tick()
    await service.task_tick()
    assert [row["id"] for row in rows] == [3] and tasks.completed == [] and laptop.spoken == []
    assert bridge.notify_calls == []


async def test_the_scheduler_waits_for_an_offline_laptop_then_announces_the_failure(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=False)
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop)
    started = time.monotonic()
    result = await service.scheduler.run_desktop_action(task_id=12, tool="send_whatsapp_message", args={"x": 1},
                                                        param_schema_sha256="0" * 64, timeout_s=30)
    assert time.monotonic() - started >= FAST.scheduler_offline_wait_s
    assert result["error_code"] == "DESKTOP_OFFLINE" and laptop.actions == []
    [note] = bridge.notify_calls
    assert note["id"] == "s:12:offline" and "laptop offline" in note["description"]
    assert ledger(tmp_path)["s:12:offline"]["synthetic"] is True


async def test_the_scheduler_runs_the_action_once_when_the_laptop_comes_back(tmp_path):
    bridge, laptop = FakeBridge(), FakeLaptop(online=False)
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop)

    async def comes_back() -> None:
        await asyncio.sleep(0.08)
        laptop.is_online = True

    back = asyncio.create_task(comes_back())
    result = await service.scheduler.run_desktop_action(task_id=12, tool="open_app", args={"name": "x"},
                                                        param_schema_sha256="0" * 64, timeout_s=30,
                                                        approval={"confirm_id": "c1"})
    await back
    assert result["status"] == "success" and len(laptop.actions) == 1
    assert laptop.actions[0]["label"] == {"scheduler_task": "12"} and laptop.actions[0]["approval"] == {
        "confirm_id": "c1"}
    assert bridge.notify_calls == []


async def test_the_link_laptop_sends_scheduler_and_internal_origins():
    from services.ai_core import desktop_link as dl

    requests: list[Any] = []

    class FakeLink:
        room = "jarvis-hybrid"

        def online(self) -> bool:
            return True

        def submit_call(self, request: Any) -> concurrent.futures.Future:
            requests.append(request)
            done: concurrent.futures.Future = concurrent.futures.Future()
            done.set_result(dl.CallOutcome(request.call_id, dl.RESULT, result={"status": "success"},
                                           status="completed", effect="done", sent=True))
            return done

    laptop = svc_mod.LinkLaptop(FakeLink())
    assert await laptop.speak("Sir, reminder: x", "r:1:ab", call_id="c" + "1" * 32) == "acked"
    await laptop.run_action(tool="open_app", args={"name": "x"}, param_schema_sha256="0" * 64, timeout_s=20)
    assert await laptop.speak("Sir, reminder: y", "r:2:cd") == "acked"
    speak, action, fresh = requests
    assert speak.tool == "pc.speak_local" and speak.origin == "internal" and speak.label == {"svc_item": "r:1:ab"}
    assert speak.call_id == "c" + "1" * 32  # the id the ledger already holds (laptop_call)
    assert action.origin == "scheduler" and action.tool == "open_app"
    assert fresh.call_id.startswith("c") and fresh.call_id != speak.call_id


@pytest.mark.parametrize(("kind", "result", "status", "effect", "expected"), [
    ("result", {"status": "error"}, "failed", "none", "failed"),
    ("result", {"status": "error"}, "timeout", "possible", "unknown"),
    ("offline", None, None, None, "offline"),
    ("refused", None, None, None, "failed"),
    ("unknown", None, None, None, "unknown"),
    ("timeout", None, None, None, "unknown"),
])
def test_speak_outcomes(kind, result, status, effect, expected):
    outcome = types.SimpleNamespace(kind=kind, result=result, status=status, effect=effect)
    assert svc_mod.speak_outcome(outcome) == expected


# ---------------------------------------------------------------- node events


async def test_a_node_location_is_cached_and_synced(tmp_path):
    bridge = FakeBridge()
    service = make_svc(tmp_path, bridge=bridge)
    now = time.time()
    service.on_node_event({"kind": "location", "city": CITY_MARKER, "lat": 33.68, "lng": 73.04, "ts": now - 30,
                           "source": "device"})
    await drain(service)
    stored = json.loads((tmp_path / "location.json").read_text(encoding="utf-8"))
    assert stored["city"] == CITY_MARKER and abs(stored["ts"] - (now - 30)) < 1
    assert (tmp_path / "location.json.lock").exists()  # written under the K-77 file lock
    [sync] = bridge.locations
    assert sync["kind"] == "device" and sync["source"] == "device" and sync["city"] == CITY_MARKER


async def test_a_bad_location_or_another_event_kind_changes_nothing(tmp_path):
    bridge = FakeBridge()
    service = make_svc(tmp_path, bridge=bridge)
    for body in ({"kind": "location", "city": "X", "lat": 120.0, "lng": 1.0, "ts": time.time()},
                 {"kind": "location", "city": None, "lat": 1.0, "lng": 1.0},
                 {"kind": "vitals_pc", "cpu": 1}, {"kind": "desktop_state", "mode": "full"},
                 {"kind": "power", "state": "suspend"}, {"kind": "alert", "code": "CLOCK_SKEW"}, "junk"):
        service.on_node_event(body)
    await drain(service)
    assert bridge.locations == [] and bridge.logs == [] and not (tmp_path / "location.json").exists()


async def test_a_future_or_stale_location_ts_is_clamped_to_now(tmp_path):
    service = make_svc(tmp_path)
    before = time.time()
    await service.cache_location({"kind": "location", "city": "Lahore", "lat": 31.5, "lng": 74.3,
                                  "ts": before + 9999})
    assert before - 1 <= service.saved_locations[0][3] <= time.time()  # type: ignore[attr-defined]


async def test_node_log_lines_are_redacted_and_relayed(tmp_path, monkeypatch):
    fake_key = "test-only-fake-google-key-1234567890"
    monkeypatch.setenv("GOOGLE_API_KEY", fake_key)
    bridge = FakeBridge()
    service = make_svc(tmp_path, bridge=bridge)
    items = [{"text": f"key {fake_key} at C:\\Users\\SomeUser\\notes.txt from me@example.com", "category": "NODE",
              "level": "warning", "ts": 1760000000.5}] + [{"text": "x"}] * 55 + ["junk"]
    count = await service.relay_log_batch({"kind": "log_batch", "items": items, "dropped": 2})
    [(sent, dropped)] = bridge.logs
    assert count == 50 and len(sent) == 50 and dropped == 2 + 6 + 1
    text = sent[0]["text"]
    assert fake_key not in text and "***" in text and "SomeUser" not in text and "example.com" not in text
    assert sent[0]["level"] == "WARNING" and sent[0]["category"] == "NODE" and sent[0]["ts"] == 1760000000.5


async def test_the_notify_bridge_posts_desktop_log_lines_with_source_desktop(monkeypatch):
    from services.utils import jarvis_bridge
    calls: list[tuple[Any, ...]] = []

    async def fake_log_batch(items, dropped=0, source="agent"):
        calls.append((items, dropped, source))

    monkeypatch.setattr(jarvis_bridge, "notify_log_batch", fake_log_batch)
    await svc_mod.NotifyBridge().log_batch([{"text": "x"}], 1)
    assert calls == [([{"text": "x"}], 1, "desktop")]


async def test_the_notify_bridge_reads_the_answers(monkeypatch):
    from services.utils import jarvis_bridge
    sent: list[tuple[str, Any]] = []
    answers = {"svc_poll": {"status": "ok", "boot": "b7", "ui_clients": 1, "ui_connects": 4, "agents": 2,
                            "acks": {"r:1": {"by": "ui", "delivered": True}}},
               "svc_deliver": {"status": "ok", "stored": "exists", "acked": True, "ui_clients": 1}}

    async def fake_answer(event_type, payload, timeout=5.0):
        sent.append((event_type, payload))
        if event_type == "svc_heartbeat":
            raise OSError("bridge down")
        return answers[event_type]

    monkeypatch.setattr(jarvis_bridge, "notify_with_answer", fake_answer)
    bridge = svc_mod.NotifyBridge()
    answer = await bridge.poll(["r:1"], ["r:2"])
    assert answer == PollAnswer(ui_clients=1, ui_connects=4, agents=2, acks={"r:1": {"by": "ui", "delivered": True}},
                                boot="b7")
    assert PollAnswer.from_json({"status": "ok"}).boot == ""  # a bridge that sends no boot id
    assert PollAnswer.from_json({"status": "unsure", "boot": "b7", "acks": {}}) is None  # counts as no answer
    assert await bridge.notify({"id": "r:1", "description": "x"}) == NotifyResult("exists", True, 1)
    assert await bridge.heartbeat({"svc": "active"}) is None
    assert sent[0] == ("svc_poll", {"ids": ["r:1"], "ack_ids": ["r:2"]})
    assert sent[1][1]["step"] == "notify"


_REQUEST = httpx.Request("POST", "http://bridge.test/notify")


def _status_error(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(f"{code}", request=_REQUEST, response=httpx.Response(code, request=_REQUEST))


@pytest.mark.parametrize(("outcome", "expected"), [
    ({"status": "sent", "agents": 1}, "sent"),
    ({"status": "no_agent", "agents": 0}, "no_agent"),
    ({"status": "rejected", "reason": "bad_payload"}, "refused"),  # the bridge emits only with 'sent'
    ({"status": "suppressed", "ui_clients": 0}, "refused"),
    (httpx.ConnectError("refused", request=_REQUEST), "refused"),  # never sent
    (_status_error(429), "refused"),  # rate limited before any handler ran
    (_status_error(503), "refused"),  # the proxy found no bridge
    (_status_error(401), "refused"),
    (httpx.ReadTimeout("slow", request=_REQUEST), "unknown"),  # the bridge may have emitted it
    (httpx.RemoteProtocolError("dropped", request=_REQUEST), "unknown"),
    (_status_error(500), "unknown"),
    (_status_error(409), "unknown"),  # an earlier copy was accepted
    (ValueError("not JSON"), "unknown"),
])
async def test_the_notify_bridge_tells_an_announce_that_surely_did_not_go_out(monkeypatch, outcome, expected):
    from services.utils import jarvis_bridge

    async def fake_answer(event_type, payload, timeout=5.0):
        assert event_type == "svc_deliver" and payload["step"] == "announce"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(jarvis_bridge, "notify_with_answer", fake_answer)
    assert await svc_mod.NotifyBridge().announce("r:1:ab#a1", "x", "reminder") == expected


_UNKNOWN = NotifyResult(svc_mod.NOTIFY_UNKNOWN)


@pytest.mark.parametrize(("outcome", "expected"), [
    ({"status": "ok", "stored": "new", "acked": False, "ui_clients": 2}, NotifyResult("new", False, 2)),
    ({"status": "ok", "stored": "exists", "acked": True, "ui_clients": 0}, NotifyResult("exists", True, 0)),
    ({"status": "rejected", "reason": "bad_payload"}, None),  # the bridge stores and emits only with 'ok'
    ({"status": "error", "reason": "store"}, None),
    # stored before, but bridge.db could not tell whether it was acknowledged, so it was not emitted again
    ({"status": "unsure", "stored": "exists", "acked": False, "ui_clients": 0}, _UNKNOWN),
    (httpx.ConnectError("refused", request=_REQUEST), None),  # never sent
    (_status_error(429), None),  # rate limited before any handler ran
    (_status_error(503), None),
    (_status_error(401), None),
    (httpx.ReadTimeout("slow", request=_REQUEST), _UNKNOWN),  # the bridge may have stored and emitted it
    (httpx.RemoteProtocolError("dropped", request=_REQUEST), _UNKNOWN),
    (_status_error(500), _UNKNOWN),
    (_status_error(504), _UNKNOWN),
    (_status_error(409), _UNKNOWN),  # an earlier copy was accepted
    (ValueError("not JSON"), _UNKNOWN),
])
async def test_the_notify_bridge_tells_a_step_3_that_surely_was_not_stored(monkeypatch, outcome, expected):
    from services.utils import jarvis_bridge

    async def fake_answer(event_type, payload, timeout=5.0):
        assert event_type == "svc_deliver" and payload["step"] == "notify"
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(jarvis_bridge, "notify_with_answer", fake_answer)
    assert await svc_mod.NotifyBridge().notify({"id": "r:1:ab", "description": "x"}) == expected


async def test_an_unsure_poll_answer_settles_no_announce_and_speaks_nothing(tmp_path, monkeypatch):
    """bridge.db could not be read (status 'unsure'): an announce left open past its window is neither timed out nor
    followed by a speak; the answer the bridge gives once it can read its store again settles it."""
    from services.utils import jarvis_bridge
    rem = reminder()
    item = svc_mod.reminder_item(rem)
    attempt = f"{item.id}#a1"
    store_ok = [False]

    async def fake_answer(event_type, payload, timeout=5.0):
        assert event_type == "svc_poll"
        acks = {attempt: {"by": "agent", "delivered": True}} if store_ok[0] and attempt in payload["ids"] else {}
        return {"status": "ok" if store_ok[0] else "unsure", "boot": "b1", "ui_clients": 0, "ui_connects": 0,
                "agents": 0, "acks": acks}

    monkeypatch.setattr(jarvis_bridge, "notify_with_answer", fake_answer)
    sent_ts = time.time() - 60  # a restart cut its wait; its 30 s window has passed
    laptop, source = FakeLaptop(online=True), FakeReminders(rem)
    service = make_svc(tmp_path, bridge=svc_mod.NotifyBridge(), laptop=laptop, reminders=source,
                       timers=SLOW_ANNOUNCE)
    service.ledger.update(lambda items: items.setdefault(item.id, {
        **svc_mod._new_entry(item, sent_ts), "announce_seq": 1, "announce_call": {"id": attempt, "ts": sent_ts},
        "attempts": [{"step": "announce", "outcome": "sent", "ts": sent_ts, "ref": attempt}]}))
    await service.reminder_tick()
    entry = ledger(tmp_path)[item.id]
    assert entry["announce_call"]["id"] == attempt and laptop.spoken == [] and not entry.get("delivered_at")
    assert [row["outcome"] for row in entry["attempts"]] == ["sent"]
    store_ok[0] = True
    await service.reminder_tick()
    entry = ledger(tmp_path)[item.id]
    assert entry["channel"] == "announce" and laptop.spoken == [] and source.marks == ["rem_1760000000"]


# ---------------------------------------------------------------- standby, health, SIGTERM


class Untouchable:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"standby touched {name}")


async def test_standby_when_the_brain_is_pc_serves_only_health(tmp_path):
    service = AgentServices(brain="pc", root=tmp_path, bridge=Untouchable(), laptop=None, reminders=Untouchable(),
                            tasks=Untouchable(), email=Untouchable(), timers=FAST, health_port=0,
                            link_factory=lambda: (_ for _ in ()).throw(AssertionError("standby built a link")))
    runner = asyncio.create_task(service.run())
    await wait_until(lambda: service.health_port_bound is not None)
    status, body = await http_get(service.health_port_bound)
    assert status == 200 and set(body) == HEALTH_KEYS
    assert body["state"] == "standby" and body["brain"] == "pc" and body["loops"] == {} and body["role"] == "svc"
    assert body["snapshot"]["enabled"] is False and body["link"] == {"started": False, "online": False}
    await asyncio.sleep(0.2)  # several loop intervals: still nothing touched
    service.request_stop()
    assert await asyncio.wait_for(runner, 5) == 0
    assert not (tmp_path / "conversations" / "delivery_ledger.json").exists()


async def test_health_of_an_active_service(tmp_path):
    bridge, source = FakeBridge(), FakeReminders(reminder())
    service = make_svc(tmp_path, bridge=bridge, reminders=source)
    runner = asyncio.create_task(service.run())
    await wait_until(lambda: service.health_port_bound is not None and len(bridge.heartbeats) >= 2)
    await wait_until(lambda: service._undelivered == 1 and bridge.heartbeats[-1]["undelivered"] == 1)
    status, body = await http_get(service.health_port_bound)
    assert status == 200 and set(body) == HEALTH_KEYS and body["state"] == "active" and body["status"] == "ok"
    assert set(body["loops"]) == {"reminders", "tasks", "watch", "heartbeat", "snapshot"}
    assert all(loop["alive"] for loop in body["loops"].values())
    assert body["due"] == {"reminders": 1, "tasks": 0} and body["due_pending"] == 1 and body["undelivered"] == 1
    assert MARKER not in json.dumps(body)
    assert bridge.heartbeats[-1]["undelivered"] == 1 and MARKER not in json.dumps(bridge.heartbeats)
    status, body = await http_get(service.health_port_bound, "/other")
    assert status == 404 and body == {"error": "not_found"}
    service.request_stop()
    assert await asyncio.wait_for(runner, 5) == 0


async def test_an_unusable_configuration_reports_503_and_starts_no_duty(tmp_path):
    def refuse() -> None:
        raise RuntimeError("JARVIS_ENCRYPTION_KEY missing")

    bridge = FakeBridge()
    service = make_svc(tmp_path, bridge=bridge, reminders=FakeReminders(reminder()), validate=refuse)
    runner = asyncio.create_task(service.run())
    await wait_until(lambda: service.health_port_bound is not None)
    status, body = await http_get(service.health_port_bound)
    assert status == 503 and body["state"] == "config_error" and body["loops"] == {}
    assert bridge.notify_calls == [] and bridge.heartbeats == []
    service.request_stop()
    assert await asyncio.wait_for(runner, 5) == 0


async def test_sigterm_ends_the_service_cleanly_with_exit_0(tmp_path):
    bridge = FakeBridge()
    service = make_svc(tmp_path, bridge=bridge, reminders=FakeReminders(reminder()))
    before = signal.getsignal(signal.SIGTERM)
    runner = asyncio.create_task(svc_mod.serve(service))
    await wait_until(lambda: service.health_port_bound is not None and bridge.heartbeats)
    handler = signal.getsignal(signal.SIGTERM)
    assert handler not in (signal.SIG_DFL, signal.SIG_IGN, None)  # never raise it without our handler in place
    port = service.health_port_bound
    signal.raise_signal(signal.SIGTERM)
    assert await asyncio.wait_for(runner, 10) == 0
    assert service.stopping and all(task.done() for task in service._loops.values())
    assert signal.getsignal(signal.SIGTERM) == before
    with pytest.raises(OSError):
        await http_get(port)


def test_main_refuses_another_role(monkeypatch):
    monkeypatch.setenv("JARVIS_ROLE", "agent")
    monkeypatch.setattr(svc_mod.net_guard, "harden_process", lambda: None)
    assert svc_mod.main([]) == 2


def test_main_hardens_the_process_first(monkeypatch):
    order: list[str] = []
    monkeypatch.setenv("JARVIS_ROLE", "svc")
    monkeypatch.setattr(svc_mod.net_guard, "harden_process", lambda: order.append("harden"))

    def fake_from_environment(profile=None):
        order.append("build")
        raise SystemExit(0)

    monkeypatch.setattr(svc_mod.AgentServices, "from_environment", staticmethod(fake_from_environment))
    with pytest.raises(SystemExit):
        svc_mod.main([])
    assert order == ["harden", "build"]


def test_from_environment_reads_the_k47_names(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    monkeypatch.setenv("JARVIS_REMINDER_EMAIL_AFTER_MIN", "15")
    monkeypatch.setenv("JARVIS_SNAPSHOTS_DIR", str(tmp_path / "snaps"))
    monkeypatch.delenv("BACKUP_RECIPIENT_PUBLIC_KEY", raising=False)
    service = AgentServices.from_environment()
    assert service.active and service.email_after_min == 15 and service.snapshots.out_dir == tmp_path / "snaps"
    assert not service.snapshots.enabled and "BACKUP_RECIPIENT_PUBLIC_KEY" in service.snapshots.problem
    monkeypatch.setenv("JARVIS_BRAIN", "pc")
    monkeypatch.setenv("JARVIS_REMINDER_EMAIL_AFTER_MIN", "x")
    service = AgentServices.from_environment()
    assert not service.active and service.state == "standby" and service.email_after_min == 0


# ---------------------------------------------------------------- snapshot schedule and loop


def test_the_snapshot_runs_at_0330_pkt(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Karachi")
    service = AgentServices(brain="vps", root=Path("."), timers=FAST)
    service._snapshot_first = False
    # 2026-10-20 02:00 PKT = 2026-10-19 21:00 UTC -> 90 min to 03:30
    now = 1760907600.0
    assert abs(service.snapshot_delay(now) - 90 * 60) < 1
    # 04:00 PKT -> the next day's 03:30
    assert abs(service.snapshot_delay(now + 2 * 3600) - (23.5 * 3600)) < 1


def test_a_start_without_a_recent_snapshot_catches_up_once(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Karachi")
    service = AgentServices(brain="vps", root=Path("."), timers=FAST)
    now = 1760907600.0 + 6 * 3600  # 08:00 PKT
    service.last_snapshot_ts = None
    assert service.snapshot_delay(now) == FAST.snapshot_catchup_s
    assert service.snapshot_delay(now) > 3600  # only once: then the normal 03:30


async def test_a_missing_recipient_key_means_no_snapshot_and_one_warning(tmp_path, records):
    service = make_svc(tmp_path, reminders=FakeReminders())
    runner = asyncio.create_task(service.run())
    await wait_until(lambda: any("SNAPSHOT off" in r.getMessage() for r in records))
    await asyncio.sleep(0.2)
    service.request_stop()
    await asyncio.wait_for(runner, 5)
    assert sum("SNAPSHOT off" in r.getMessage() for r in records) == 1
    assert not (tmp_path / "snapshots").exists()


async def test_run_snapshot_writes_a_sealed_file(tmp_path):
    from services.utils import backup_crypto as bc
    key = bc.generate_private_key()
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "reminders.json").write_text(json.dumps([{"message": MARKER}]), encoding="utf-8")
    service = make_svc(tmp_path, recipient_key=bc.public_key_hex(key))
    info = await service.run_snapshot()
    assert info is not None and info["files"] == 1 and service.last_snapshot_ts == info["ts"]
    sealed = (tmp_path / "snapshots" / info["name"]).read_bytes()
    assert MARKER.encode() not in sealed and bc.looks_sealed(sealed)
    assert MARKER.encode() in bc.open_bytes(sealed, key)


def test_the_snapshot_leaves_out_the_folders_a_restore_moved_aside(tmp_path):
    """C-128 review (low): restore_agent_data.py moves each volume's live data into .pre-restore-<UTC>/ at the top of
    conversations, chroma_db and data, kept until the owner decides. The daily snapshot walked into them, so every later
    snapshot the laptop pulls carried a second, stale copy (one more per restore). Top-level '.pre-restore-*' folders
    of each root stay out now, the rule of backup_vps.sh's agent step (services/utils/agent_volumes.ASIDE_PREFIX); a
    folder of that name deeper down is ordinary data."""
    import io  # pylint: disable=import-outside-toplevel
    import tarfile  # pylint: disable=import-outside-toplevel

    from services.utils import agent_volumes as av  # pylint: disable=import-outside-toplevel
    from services.utils import backup_crypto as bc  # pylint: disable=import-outside-toplevel

    key = bc.generate_private_key()
    for root in svc_mod.SNAPSHOT_ROOTS:
        aside = tmp_path / root / ".pre-restore-20261022T000000Z"
        (aside / "deeper").mkdir(parents=True)
        (aside / "old.json").write_text(MARKER, encoding="utf-8")
        (aside / "deeper" / "older.json").write_text(MARKER, encoding="utf-8")
    (tmp_path / "conversations" / "live.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "data" / "notes" / ".pre-restore-not-the-top"
    nested.mkdir(parents=True)
    (nested / "kept.json").write_text("{}", encoding="utf-8")
    info = svc_mod.SnapshotWriter(root=tmp_path, out_dir=tmp_path / "snapshots",
                                  recipient_text=bc.public_key_hex(key)).write()
    plain = bc.open_bytes((tmp_path / "snapshots" / info["name"]).read_bytes(), key)
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
        names = sorted(tar.getnames())
    assert names == ["conversations/live.json", "data/notes/.pre-restore-not-the-top/kept.json"], names
    assert info["files"] == 2 and MARKER.encode() not in plain
    assert svc_mod.RESTORE_ASIDE_PREFIX == av.ASIDE_PREFIX == ".pre-restore-"


# ---------------------------------------------------------------- the ledger and log hygiene


def test_an_unreadable_ledger_is_kept_aside_and_the_ledger_starts_empty(tmp_path):
    path = tmp_path / "conversations" / "delivery_ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    book = DeliveryLedger(path)
    assert book.read() == {}
    [aside] = [p for p in path.parent.iterdir() if p.name.startswith("delivery_ledger.json.unreadable-")]
    assert aside.read_text(encoding="utf-8") == "{not json"
    book.update(lambda items: items.setdefault("x", {"id": "x"}))
    assert book.read() == {"x": {"id": "x"}} and (path.parent / "delivery_ledger.json.lock").exists()


async def test_finished_entries_are_dropped_after_30_days_open_ones_never(tmp_path):
    service = make_svc(tmp_path)
    old = time.time() - 31 * 86400
    rows = {"t:1:old": {"id": "t:1:old", "kind": "task", "delivered_at": old, "source_marked": True},
            "t:2:new": {"id": "t:2:new", "kind": "task", "delivered_at": time.time(), "source_marked": True},
            "t:3:unmarked": {"id": "t:3:unmarked", "kind": "task", "delivered_at": old},
            "s:4:offline": {"id": "s:4:offline", "kind": "task", "synthetic": True, "text": "x", "delivered_at": old},
            "s:5:open": {"id": "s:5:open", "kind": "task", "synthetic": True, "text": "x", "first_seen": old}}
    service.ledger.update(lambda items: items.update(rows))
    await service.task_tick()
    assert sorted(ledger(tmp_path)) == ["s:5:open", "t:2:new", "t:3:unmarked"]
    assert not ledger(tmp_path)["s:5:open"].get("closed_at")  # a synthetic item never leaves a source


def test_the_ledger_is_written_only_on_a_change(tmp_path):
    path = tmp_path / "ledger.json"
    book = DeliveryLedger(path)
    book.update(lambda items: items.setdefault("a", {"id": "a"}))
    stamp = path.stat().st_mtime_ns
    time.sleep(0.02)
    book.update(lambda items: None)
    assert path.stat().st_mtime_ns == stamp


async def test_log_lines_carry_ids_and_counts_but_never_text(tmp_path, records):
    from services.utils import backup_crypto as bc
    bridge, laptop = FakeBridge(), FakeLaptop(online=False)
    bridge.agents, bridge.job_answer = 1, {"delivered": False, "reason": "no_participant"}
    source, tasks = FakeReminders(reminder()), FakeTasks(task(5))
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / "notes.json").write_text(MARKER, encoding="utf-8")
    service = make_svc(tmp_path, bridge=bridge, laptop=laptop, reminders=source, tasks=tasks, email_after_min=0,
                       recipient_key=bc.public_key_hex(bc.generate_private_key()))
    await service.reminder_tick()
    await service.task_tick()
    laptop.is_online = True
    service.on_node_session()
    await drain(service)
    service.on_node_event({"kind": "location", "city": CITY_MARKER, "lat": 31.5, "lng": 74.3, "ts": time.time()})
    service.on_node_event({"kind": "log_batch", "items": [{"text": "line"}]})
    service.on_node_event({"kind": "location", "city": CITY_MARKER, "lat": 931.5, "lng": 74.3})
    await drain(service)
    await service.watch_tick()
    await service.run_snapshot()
    await service.scheduler.run_desktop_action(task_id=4, tool="t", args={"secret": MARKER},
                                               param_schema_sha256="0" * 64, timeout_s=5)
    lines = [record.getMessage() for record in records]
    assert any("delivered via laptop" in line for line in lines) and any("SNAPSHOT" in line for line in lines)
    for line in lines:
        assert MARKER not in line and CITY_MARKER not in line and "31.5" not in line


def test_due_item_keys_and_texts():
    item = svc_mod.reminder_item({"id": "rem_1", "message": "  pani\npeena \x07 ", "time": "2026-10-20T09:00:00",
                                  "created_at": "2026-10-20T08:00:00"})
    assert isinstance(item, DueItem) and item.text == "pani peena" and item.id.startswith("r:rem_1:")
    assert svc_mod.reminder_item({"message": "x"}) is None
    assert svc_mod.task_item({"id": True}) is None
    assert svc_mod.task_item({"id": 4, "description": "", "action": "get_weather"}).text == "task 4"
    assert svc_mod.spoken_text(item) == "Sir, reminder: pani peena"


def test_the_service_module_imports_no_livekit_and_no_voice_runtime():
    source = Path(svc_mod.__file__).read_text(encoding="utf-8")
    assert "import livekit" not in source and "from livekit" not in source
    assert "agent_runner" not in source and sys.modules.get("src.core.agent_services") is svc_mod
