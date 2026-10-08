import json
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import jarvis_reminders
from jarvis_reminders import (
    check_due_reminders,
    list_due_reminders,
    list_reminders,
    load_reminders,
    mark_reminder_delivered,
    save_reminders,
    set_reminder,
)
from services.automation.jarvis_task_manager import parse_when, scheduling_now

ROOT = Path(__file__).resolve().parents[1]

REMINDER_WORKER = textwrap.dedent("""
    import json, os, sys, uuid
    sys.path.insert(0, sys.argv[3])
    os.environ["TZ"] = "Asia/Karachi"
    from services.utils.jarvis_filelock import locked
    path = sys.argv[1]
    rounds = int(sys.argv[2])
    tag = sys.argv[4]
    for i in range(rounds):
        with locked(path, timeout=30):
            data = json.loads(open(path, encoding="utf-8").read())
            data.append({"id": f"{tag}_{i}", "time": "2099-01-01T00:00:00", "message": tag,
                         "status": "pending", "created_at": "2099-01-01T00:00:00"})
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
""")


@pytest.fixture
def mock_reminders_file(tmp_path, monkeypatch):
    path = tmp_path / "reminders.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(jarvis_reminders, "REMINDERS_FILE", str(path))
    return str(path)


def test_load_reminders_empty(mock_reminders_file):
    assert load_reminders() == []


def test_load_reminders_content(mock_reminders_file):
    data = [{"id": "1", "message": "test"}]
    Path(mock_reminders_file).write_text(json.dumps(data), encoding="utf-8")
    assert load_reminders() == data


def test_save_reminders(mock_reminders_file):
    data = [{"id": "1", "message": "test"}]
    save_reminders(data)
    assert json.loads(Path(mock_reminders_file).read_text(encoding="utf-8")) == data


@pytest.mark.asyncio
async def test_set_reminder_relative(mock_reminders_file):
    with patch("jarvis_reminders._now_local", return_value=datetime(2026, 1, 1, 12, 0, 0)):
        res = await set_reminder("10 minutes", "Take a break")
    assert "Successfully set reminder" in res
    saved = load_reminders()
    assert len(saved) == 1
    assert saved[0]["message"] == "Take a break"
    assert saved[0]["time"] == "2026-01-01T12:10:00"


@pytest.mark.asyncio
async def test_set_reminder_absolute(mock_reminders_file):
    with patch("jarvis_reminders._now_local", return_value=datetime(2026, 1, 1, 12, 0, 0)):
        res = await set_reminder("14:30", "Meeting")
    assert "Successfully set reminder" in res


@pytest.mark.asyncio
async def test_list_reminders(mock_reminders_file):
    data = [
        {"time": datetime.now().isoformat(), "message": "Rem1", "status": "pending"},
        {"time": datetime.now().isoformat(), "message": "Rem2", "status": "triggered"},
    ]
    save_reminders(data)
    res = await list_reminders()
    assert "Rem1" in res
    assert "Rem2" not in res


def test_check_due_reminders(mock_reminders_file):
    past_time = (datetime(2026, 1, 1, 10, 0, 0)).isoformat()
    future_time = (datetime(2099, 1, 1, 10, 0, 0)).isoformat()
    data = [
        {"id": "a", "time": past_time, "message": "Due", "status": "pending"},
        {"id": "b", "time": future_time, "message": "Wait", "status": "pending"},
    ]
    save_reminders(data)
    with patch("jarvis_reminders._now_local", return_value=datetime(2026, 1, 1, 12, 0, 0)):
        due = check_due_reminders()
    assert len(due) == 1
    assert due[0]["message"] == "Due"
    stored = load_reminders()
    assert stored[0]["status"] == "triggered"
    assert stored[1]["status"] == "pending"


def test_list_due_reminders_does_not_mutate(mock_reminders_file):
    past_time = datetime(2026, 1, 1, 9, 0, 0).isoformat()
    save_reminders([{"id": "x", "time": past_time, "message": "Due", "status": "pending"}])
    with patch("jarvis_reminders._now_local", return_value=datetime(2026, 1, 1, 12, 0, 0)):
        due = list_due_reminders()
    assert len(due) == 1
    assert load_reminders()[0]["status"] == "pending"


def test_mark_reminder_delivered(mock_reminders_file):
    save_reminders([{"id": "rem_1", "time": "2099-01-01T00:00:00", "message": "m", "status": "pending"}])
    assert mark_reminder_delivered("rem_1") is True
    row = load_reminders()[0]
    assert row["status"] == "triggered"
    assert row.get("delivered_at")
    assert mark_reminder_delivered("missing") is False


def test_two_processes_lose_no_reminder_updates(tmp_path, monkeypatch):
    data = tmp_path / "reminders.json"
    data.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(jarvis_reminders, "REMINDERS_FILE", str(data))
    script = tmp_path / "rem_worker.py"
    script.write_text(REMINDER_WORKER, encoding="utf-8")
    rounds = 25
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(data), str(rounds), str(ROOT), tag],
        )
        for tag in ("proc_a", "proc_b")
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    items = json.loads(data.read_text(encoding="utf-8"))
    assert len(items) == 2 * rounds


def test_pkt_due_times_on_utc_host(monkeypatch):
    """When TZ is unset (typical UTC VPS before env), wall clock defaults to Asia/Karachi."""
    monkeypatch.delenv("TZ", raising=False)
    base = datetime(2026, 6, 15, 10, 0, 0)
    target = parse_when("14:30", now=base)
    assert target == datetime(2026, 6, 15, 14, 30, 0)

    expected = datetime.now(ZoneInfo("Asia/Karachi")).replace(tzinfo=None)
    got = scheduling_now()
    assert abs((got - expected).total_seconds()) < 2
