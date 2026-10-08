"""Plan task C-58 (K-31): job progress routing on the bridge.

The agent's jobs post tool_sync {id, tool, action, phase, status, details, progress?, job: true, artifact?} over
/notify (here the internal listener, which needs no signature). The bridge keeps today's tool_update, emits a
Jobs-panel tool_progress {job_id, tool, title, status, percent, message, artifact?, ts} (success maps to done),
remembers the newest 20 jobs for init_state.jobs and also takes a direct tool_progress. The handlers run against a
temporary store with a recorded sio.emit, so nothing reaches the live bridge.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib
import json
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from services.utils.jarvis_config import config
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore

INTERNAL_PORT = 5002


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store, no sockets, an empty job list and a recorded sio.emit."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    emitted: list[tuple[str, Any]] = []

    async def fake_emit(event: str, data: Any = None, **_kwargs: Any) -> None:
        emitted.append((event, data))

    for name, value in (("store", store), ("bridge_manager", module.BridgeStateManager()), ("_clients", {}),
                        ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("_jobs", OrderedDict()), ("_agent_pc_vitals", None), ("_agent_pc_vitals_mono", 0.0),
                        ("_latest_vps_vitals", None)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    monkeypatch.setattr(config, "env", "development")
    yield SimpleNamespace(module=module, store=store, emitted=emitted)
    await store.close()


async def _notify(ns: Any, event_type: str, payload: Any) -> dict[str, Any]:
    transport = httpx.ASGITransport(app=ns.module.app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://bridge.test:{INTERNAL_PORT}") as client:
        resp = await client.post("/notify", content=json.dumps({"type": event_type, "payload": payload}).encode())
    assert resp.status_code == 200
    return resp.json()


def _events(ns: Any, name: str) -> list[Any]:
    return [data for event, data in ns.emitted if event == name]


def _job(phase: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"id": "job-c58", "tool": "google_maps_scraper", "action": "Maps leads", "phase": phase,
            "status": status, "details": extra.pop("details", phase), "job": True, "timestamp": 1791460000.0,
            **extra}


async def test_a_jobs_start_progress_and_end_reach_the_jobs_panel(bridge):
    await _notify(bridge, "tool_sync", _job("start", "queued"))
    await _notify(bridge, "tool_sync", _job("progress", "running", progress=40, details="step 2"))
    await _notify(bridge, "tool_sync", _job("end", "success", details="12 records",
                                            artifact={"kind": "file", "path": "C:/x/leads.csv", "name": "leads.csv",
                                                      "junk": 1}))
    rows = _events(bridge, "tool_progress")
    assert [row["status"] for row in rows] == ["queued", "running", "done"]  # success maps to done
    assert rows[1]["percent"] == 40.0 and rows[1]["message"] == "step 2"
    end = rows[-1]
    assert end["job_id"] == "job-c58" and end["title"] == "Maps leads" and end["tool"] == "google_maps_scraper"
    assert end["percent"] == 40.0  # the end row kept the last percent it never sent itself
    assert end["artifact"] == {"kind": "file", "path": "C:/x/leads.csv", "name": "leads.csv"}
    assert len(_events(bridge, "tool_update")) == 3  # today's tool rows are untouched


async def test_init_state_restores_the_newest_jobs_first_and_keeps_twenty(bridge):
    for n in range(25):  # straight into the router: 25 notifies at once would meet the 'others' rate class
        await bridge.module._route_job_event({**_job("progress", "running", progress=n), "id": f"job-{n}"})
    jobs = (await bridge.module.build_init_state())["jobs"]
    assert len(jobs) == bridge.module.JOBS_MAX == 20
    assert jobs[0]["job_id"] == "job-24" and jobs[-1]["job_id"] == "job-5"


async def test_a_direct_tool_progress_is_accepted_and_a_plain_tool_row_is_not_a_job(bridge):
    result = await _notify(bridge, "tool_progress", {"job_id": "job-direct", "tool": "swarm", "title": "Swarm",
                                                       "status": "error", "percent": 250, "message": "x", "ts": 5})
    assert result == {"status": "ok"}
    (row,) = _events(bridge, "tool_progress")
    assert row["status"] == "error" and row["percent"] == 100.0 and row["ts"] == 5.0
    await _notify(bridge, "tool_sync", {"id": "call-1", "tool": "get_weather", "phase": "end", "status": "success"})
    assert len(_events(bridge, "tool_progress")) == 1  # an ordinary tool's end row is no job
    assert not [job for job in bridge.module.jobs_newest_first() if job["job_id"] == "call-1"]


async def test_bad_job_payloads_are_dropped(bridge):
    for payload in ({"tool": "x", "job": True}, {"id": "", "job": True}, ["not", "a", "dict"]):
        await _notify(bridge, "tool_sync", payload)
    assert _events(bridge, "tool_progress") == []


def test_job_commands_are_allow_listed():
    module = importlib.import_module("src.core.ui_bridge")
    assert {"job_cancel", "open_job_artifact"} <= module.UI_COMMAND_TYPES
