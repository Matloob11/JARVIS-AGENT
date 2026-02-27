import pytest
import json
import os
import asyncio
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta
import jarvis_reminders
from jarvis_reminders import (
    load_reminders,
    save_reminders,
    set_reminder,
    list_reminders,
    check_due_reminders
)


@pytest.fixture
def mock_reminders_file():
    with patch("jarvis_reminders.REMINDERS_FILE", "fake_reminders.json"):
        yield "fake_reminders.json"


def test_load_reminders_empty(mock_reminders_file):
    with patch("os.path.exists", return_value=False):
        assert load_reminders() == []


def test_load_reminders_content(mock_reminders_file):
    data = [{"id": "1", "message": "test"}]
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            assert load_reminders() == data


def test_save_reminders(mock_reminders_file):
    data = [{"id": "1", "message": "test"}]
    with patch("os.makedirs"):
        with patch("builtins.open", mock_open()) as m_open:
            with patch("os.replace") as mock_replace:
                save_reminders(data)
                m_open.assert_called()
                mock_replace.assert_called()


@pytest.mark.asyncio
async def test_set_reminder_relative(mock_reminders_file):
    with patch("jarvis_reminders.load_reminders", return_value=[]):
        with patch("jarvis_reminders.save_reminders") as mock_save:
            res = await set_reminder("10 minutes", "Take a break")
            assert "Successfully set reminder" in res
            mock_save.assert_called()
            # Verify data structure
            saved_list = mock_save.call_args[0][0]
            assert len(saved_list) == 1
            assert saved_list[0]["message"] == "Take a break"


@pytest.mark.asyncio
async def test_set_reminder_absolute(mock_reminders_file):
    with patch("jarvis_reminders.load_reminders", return_value=[]):
        with patch("jarvis_reminders.save_reminders"):
            # HH:MM format
            res = await set_reminder("14:30", "Meeting")
            assert "Successfully set reminder" in res


@pytest.mark.asyncio
async def test_list_reminders(mock_reminders_file):
    data = [
        {"time": datetime.now().isoformat(), "message": "Rem1", "status": "pending"},
        {"time": datetime.now().isoformat(), "message": "Rem2",
         "status": "triggered"}
    ]
    with patch("jarvis_reminders.load_reminders", return_value=data):
        res = await list_reminders()
        assert "Rem1" in res
        assert "Rem2" not in res


def test_check_due_reminders(mock_reminders_file):
    past_time = (datetime.now() - timedelta(minutes=5)).isoformat()
    future_time = (datetime.now() + timedelta(minutes=5)).isoformat()
    data = [
        {"time": past_time, "message": "Due", "status": "pending"},
        {"time": future_time, "message": "Wait", "status": "pending"}
    ]
    with patch("jarvis_reminders.load_reminders", return_value=data):
        with patch("jarvis_reminders.save_reminders") as mock_save:
            due = check_due_reminders()
            assert len(due) == 1
            assert due[0]["message"] == "Due"
            assert data[0]["status"] == "triggered"
            mock_save.assert_called()
