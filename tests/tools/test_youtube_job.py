"""Background YouTube download job: progress parsing and cancel."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from services.utils import jarvis_announcer, jarvis_bridge, jarvis_jobs
from tests.automation.test_youtube_downloader import FakeProcess


@pytest.fixture(name="events")
def _events(monkeypatch) -> dict[str, list]:
    seen: dict[str, list] = {"sync": []}

    async def notify_event(event_type, payload):
        if event_type == "tool_sync":
            seen["sync"].append(dict(payload))

    async def announce(text, **kwargs):
        del text, kwargs

    monkeypatch.setattr(jarvis_bridge, "notify_event", notify_event)
    monkeypatch.setattr(jarvis_announcer, "announce", announce)
    monkeypatch.setattr(jarvis_jobs, "PROGRESS_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(jarvis_jobs, "_jobs", {})
    return seen


async def _wait_job(job_id: str, timeout: float = 5.0) -> dict:
    for _ in range(int(timeout / 0.01)):
        job = jarvis_jobs.get_job(job_id)
        if job and job["status"] not in jarvis_jobs.ACTIVE:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


@pytest.mark.asyncio
async def test_job_emits_progress_from_fake_subprocess(events, monkeypatch):
    from services.multimedia import jarvis_youtube_downloader as ytd_mod

    stderr = [
        b"download:JARVIS_PROGRESS  25.0% ETA 00:30\n",
        b"download:JARVIS_PROGRESS  80.0% ETA 00:05\n",
    ]
    proc = FakeProcess(
        stderr_lines=stderr,
        stdout_lines=[b"C:/shared/Downloads/YouTube/track.mp3\n"],
        returncode=0,
    )

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(ytd_mod.asyncio, "create_subprocess_exec", fake_exec)

    with patch("os.path.exists", return_value=True):
        with patch.object(ytd_mod.jarvis_delivery, "open_artifact"):
            async def factory(ctx: jarvis_jobs.JobContext) -> dict:
                return await ytd_mod.yt_downloader.run_download_job(
                    ctx, "test track", "audio", auto_open=False
                )

            job_id = jarvis_jobs.start_job(
                "download_youtube_media", "YouTube audio download", factory
            )
            job = await _wait_job(job_id)

    assert job["status"] == "done"
    progress = [
        e for e in events["sync"] if e["phase"] == "progress" and e.get("progress") is not None
    ]
    percents = [e["progress"] for e in progress]
    assert 25.0 in percents or 80.0 in percents
    assert job["artifact"] == {
        "kind": "file",
        "path": "C:/shared/Downloads/YouTube/track.mp3",
        "name": "track.mp3",
    }


class _BlockingStream:
    async def readline(self) -> bytes:
        await asyncio.sleep(3600)
        return b""


class _HangingProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__(returncode=0)
        self.stdout = _BlockingStream()
        self.stderr = _BlockingStream()


@pytest.mark.asyncio
async def test_job_cancel_kills_subprocess(events, monkeypatch):
    from services.multimedia import jarvis_youtube_downloader as ytd_mod

    started = asyncio.Event()
    proc = _HangingProcess()

    async def slow_exec(*_args, **_kwargs):
        started.set()
        return proc

    monkeypatch.setattr(ytd_mod.asyncio, "create_subprocess_exec", slow_exec)

    async def factory(ctx: jarvis_jobs.JobContext) -> dict:
        return await ytd_mod.yt_downloader.run_download_job(
            ctx, "https://youtube.com/watch?v=abc", "audio", auto_open=False
        )

    job_id = jarvis_jobs.start_job("download_youtube_media", "YouTube cancel test", factory)
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.sleep(0.05)
    assert jarvis_jobs.cancel_job(job_id) is True
    job = await _wait_job(job_id)
    assert job["status"] == "cancelled"
    assert proc.killed is True
