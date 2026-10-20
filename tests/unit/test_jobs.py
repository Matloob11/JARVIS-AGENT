"""services/utils/jarvis_jobs.py: the BackgroundJobsAPI (contract K-20, events K-31, plan task C-57)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.utils import jarvis_announcer, jarvis_bridge, jarvis_jobs


@pytest.fixture(name="events")
def _events(monkeypatch) -> dict[str, list[Any]]:
    seen: dict[str, list[Any]] = {"sync": [], "announced": []}

    async def notify_event(event_type, payload):
        if event_type == "tool_sync":
            seen["sync"].append(dict(payload))

    async def announce(text, **kwargs):
        seen["announced"].append((text, kwargs))

    monkeypatch.setattr(jarvis_bridge, "notify_event", notify_event)
    monkeypatch.setattr(jarvis_announcer, "announce", announce)
    monkeypatch.setattr(jarvis_jobs, "PROGRESS_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(jarvis_jobs, "_jobs", {})
    return seen


async def _wait(job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    for _ in range(int(timeout / 0.01)):
        job = jarvis_jobs.get_job(job_id)
        if job and job["status"] not in jarvis_jobs.ACTIVE:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


async def test_a_three_step_job_emits_three_progress_events_and_one_announced_completion(events):
    async def factory(ctx: jarvis_jobs.JobContext) -> dict:
        for n, pct in enumerate((10, 50, 90), start=1):
            await ctx.progress(pct, f"step {n}")
        return {"status": "success", "message": "Maps leads tayyar: 12 records.",
                "artifact": {"kind": "file", "path": "C:/x/leads.csv", "name": "leads.csv", "junk": 1}}

    job_id = jarvis_jobs.start_job("google_maps_scraper", "Maps leads", factory)
    job = await _wait(job_id)
    assert job["status"] == "done" and job["percent"] == 100.0
    phases = [(e["phase"], e["status"]) for e in events["sync"]]
    assert phases[0] == ("start", "queued") and phases[-1] == ("end", "success")
    progress = [e for e in events["sync"] if e["phase"] == "progress" and e["details"].startswith("step")]
    assert [e["progress"] for e in progress] == [10.0, 50.0, 90.0]
    assert all(e["job"] is True and e["id"] == job_id and e["action"] == "Maps leads" for e in events["sync"])
    assert events["sync"][-1]["artifact"] == {"kind": "file", "path": "C:/x/leads.csv", "name": "leads.csv"}
    assert len(events["announced"]) == 1
    shown, kwargs = events["announced"][0]
    assert shown == "Maps leads tayyar: 12 records." and kwargs["kind"] == "job"
    assert "12 records" not in kwargs["spoken_text"]  # a result is shown, spoken as a fixed line


async def test_progress_is_throttled_unless_it_moves_five_points(events, monkeypatch):
    monkeypatch.setattr(jarvis_jobs, "PROGRESS_MIN_INTERVAL_S", 60.0)

    async def factory(ctx):
        for pct in (1, 2, 3, 10, 11):
            await ctx.progress(pct, "x")
        return {"status": "success", "message": "ok"}

    await _wait(jarvis_jobs.start_job("t", "throttle", factory))
    sent = [e["progress"] for e in events["sync"] if e["phase"] == "progress" and e["details"] == "x"]
    assert sent == [1.0, 10.0]  # the first, then only a 5-point move inside the window


async def test_cancel_stops_a_running_job_and_it_is_not_announced(events):
    started = asyncio.Event()

    async def factory(ctx):
        started.set()
        await asyncio.sleep(30)
        return {"status": "success"}

    job_id = jarvis_jobs.start_job("t", "long", factory)
    await asyncio.wait_for(started.wait(), 5)
    assert jarvis_jobs.cancel_job(job_id) is True
    job = await _wait(job_id)
    assert job["status"] == "cancelled" and events["sync"][-1]["status"] == "cancelled"
    assert events["announced"] == []
    assert jarvis_jobs.cancel_job(job_id) is False  # finished jobs cannot be cancelled again
    assert jarvis_jobs.cancel_job("nope") is False


async def test_at_most_two_jobs_run_at_once(events):
    running = 0
    peak = 0
    gate = asyncio.Event()

    async def factory(ctx):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await gate.wait()
        running -= 1
        return {"status": "success", "message": "done"}

    ids = [jarvis_jobs.start_job("t", f"j{n}", factory) for n in range(4)]
    await asyncio.sleep(0.1)
    states = [jarvis_jobs.get_job(i)["status"] for i in ids]
    assert states.count("running") == 2 and states.count("queued") == 2
    gate.set()
    for job_id in ids:
        await _wait(job_id)
    assert peak == 2


async def test_errors_and_timeouts_end_as_error_and_are_announced(events):
    async def boom(ctx):
        raise ValueError("secret detail")

    async def slow(ctx):
        await asyncio.sleep(10)

    bad = await _wait(jarvis_jobs.start_job("t", "bad", boom))
    late_id = jarvis_jobs.start_job("t", "late", slow, timeout=1)
    late = await _wait(late_id, timeout=5)
    assert bad["status"] == "error" and "secret detail" not in bad["message"]
    assert late["status"] == "error" and "Waqt khatam" in late["message"]
    assert len(events["announced"]) == 2


async def test_list_jobs_shows_active_first_and_the_tool_says_restarts_lose_jobs(events):
    gate = asyncio.Event()

    async def factory(ctx):
        await gate.wait()
        return {"status": "success", "message": "m"}

    finished = await _wait(jarvis_jobs.start_job("t", "old", lambda ctx: {"status": "success", "message": "x"}))
    active_id = jarvis_jobs.start_job("t", "active", factory)
    await asyncio.sleep(0.05)
    listed = jarvis_jobs.list_jobs()
    assert listed[0]["job_id"] == active_id and listed[-1]["job_id"] == finished["job_id"]
    assert "restart" in jarvis_jobs.session_note()
    gate.set()
    await _wait(active_id)


async def test_count_active_counts_only_the_queued_and_running_jobs_of_one_loop(events):
    """C-114: the LiveKit job's idle close waits while its own loop runs a job. Another loop's job (another LiveKit
    job's thread on Windows), even one its stopped loop left 'running' for ever, never counts."""
    gate = asyncio.Event()

    async def factory(ctx):
        await gate.wait()
        return {"status": "success", "message": "m"}

    assert jarvis_jobs.count_active() == 0
    ids = [jarvis_jobs.start_job("t", f"j{n}", factory) for n in range(3)]  # two run, one waits its turn
    await asyncio.sleep(0.05)
    assert jarvis_jobs.count_active() == 3 == jarvis_jobs.count_active(asyncio.get_running_loop())
    gone = asyncio.new_event_loop()
    gone.close()
    stale = jarvis_jobs._Job(job_id="stale", tool="t", title="left behind",  # pylint: disable=protected-access
                             status=jarvis_jobs.RUNNING, loop=gone)
    jarvis_jobs._jobs["stale"] = stale  # pylint: disable=protected-access
    assert jarvis_jobs.count_active() == 3 and jarvis_jobs.count_active(gone) == 1
    assert await asyncio.to_thread(jarvis_jobs.count_active) == 0  # no running loop there
    gate.set()
    for job_id in ids:
        await _wait(job_id)
    assert jarvis_jobs.count_active() == 0


# ---------------------------------------------------------------- ui_command job_cancel / open_job_artifact (K-31)


async def test_open_job_artifact_opens_only_a_finished_jobs_file_inside_the_output_folders(events, monkeypatch,
                                                                                         tmp_path):
    from services.ai_core import agent_loops
    from services.utils import jarvis_delivery
    from services.utils.jarvis_config import config

    monkeypatch.setattr(config, "project_root", str(tmp_path))
    monkeypatch.setattr(config, "shared_dir", str(tmp_path / "Jarvis_Shared"))
    outputs = tmp_path / "Jarvis_Outputs"
    outputs.mkdir()
    inside = outputs / "leads.csv"
    inside.write_text("a,b\n", encoding="utf-8")
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("x", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(jarvis_delivery, "open_artifact", lambda path, *a, **k: opened.append(path))

    async def finished_with(path):
        return await _wait(jarvis_jobs.start_job(
            "t", "artifact", lambda ctx: {"status": "success", "message": "ok",
                                          "artifact": {"kind": "file", "path": str(path), "name": "f"}}))

    assert await agent_loops.open_job_artifact({"job_id": "unknown"}) is False
    good = await finished_with(inside)
    assert await agent_loops.open_job_artifact({"job_id": good["job_id"]}) is True
    bad = await finished_with(outside)
    assert await agent_loops.open_job_artifact({"job_id": bad["job_id"]}) is False
    inside.unlink()
    assert await agent_loops.open_job_artifact({"job_id": good["job_id"]}) is False  # the file is gone now
    assert opened == [str(inside.resolve())] or opened == [__import__("os").path.realpath(inside)]


async def test_the_job_cancel_ui_command_cancels_through_the_listener(events):
    from unittest.mock import MagicMock

    from services.ai_core import agent_loops

    started = asyncio.Event()

    async def factory(ctx):
        started.set()
        await asyncio.sleep(30)

    job_id = jarvis_jobs.start_job("t", "ui cancel", factory)
    await asyncio.wait_for(started.wait(), 5)
    listener = agent_loops.UIBridgeListener(MagicMock())
    await listener._handle_command({"type": "job_cancel", "payload": {"job_id": job_id}})  # pylint: disable=protected-access
    assert (await _wait(job_id))["status"] == "cancelled"
