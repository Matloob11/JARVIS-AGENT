"""K-40: the PC brain's agent sends this laptop's vitals to the bridge as vitals_pc every 10 s.

Without them the dashboard's Quick Stats has no laptop figures in the PC brain (the Desktop Node, which sends
them in vps mode, does not run next to it). The reader and the notify call are faked: no psutil reading and no
request leaves the test.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import sys
import time
from unittest.mock import AsyncMock

from src.core import agent_runner
from src.core.desktop import sensors


def test_the_payload_has_the_reader_fields_a_disk_label_host_and_ts(monkeypatch):
    reading = {"cpu": 12.5, "ram": 61.0, "disk": 70.2, "cores": 8, "ram_total_gb": 15.7}
    monkeypatch.setattr(sensors, "collect_vitals", lambda: dict(reading))
    monkeypatch.setenv("SystemDrive", "D:")

    payload = agent_runner._pc_vitals_payload()

    assert {name: payload[name] for name in reading} == reading
    assert payload["disk_label"] == ("D:" if sys.platform == "win32" else "/")
    assert isinstance(payload["host"], str) and payload["host"]
    assert abs(payload["ts"] - time.time()) < 5


def test_an_odd_system_drive_falls_back_to_c(monkeypatch):
    monkeypatch.setattr(sensors, "collect_vitals", lambda: {"cpu": 1, "ram": 2, "disk": 3})
    monkeypatch.setenv("SystemDrive", "%%")
    if sys.platform == "win32":
        assert agent_runner._pc_vitals_payload()["disk_label"] == "C:"


async def test_the_loop_sends_a_reading_every_interval_and_survives_a_failed_one(monkeypatch):
    readings: list[object] = [RuntimeError("psutil failed"), {"cpu": 1}, {"cpu": 2}]

    def fake_payload() -> dict:
        reading = readings.pop(0) if readings else {"cpu": 3}
        if isinstance(reading, Exception):
            raise reading
        return reading  # type: ignore[return-value]

    monkeypatch.setattr(agent_runner, "_pc_vitals_payload", fake_payload)
    notify = AsyncMock()
    task = asyncio.create_task(agent_runner._pc_vitals_loop(notify, interval=0.01))
    try:
        for _ in range(300):
            if notify.await_count >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert [call.args[0] for call in notify.await_args_list][:2] == [{"cpu": 1}, {"cpu": 2}]


async def test_a_failed_notify_does_not_stop_the_loop(monkeypatch):
    monkeypatch.setattr(agent_runner, "_pc_vitals_payload", lambda: {"cpu": 1})
    notify = AsyncMock(side_effect=[RuntimeError("bridge down"), None, None])
    task = asyncio.create_task(agent_runner._pc_vitals_loop(notify, interval=0.01))
    try:
        for _ in range(300):
            if notify.await_count >= 3:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert notify.await_count >= 3


def test_notify_pc_vitals_sends_a_vitals_pc_event(monkeypatch):
    from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel

    sent = AsyncMock()
    monkeypatch.setattr(jarvis_bridge, "notify_event", sent)
    reading = {"cpu": 1.0, "ram": 2.0, "disk": 3.0}
    asyncio.run(jarvis_bridge.notify_pc_vitals(reading))
    sent.assert_awaited_once_with("vitals_pc", reading)
