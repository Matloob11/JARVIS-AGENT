"""
# services/utils/jarvis_jobs.py
BackgroundJobsAPI (contract K-20; events K-31; plan task C-57): one module for long tool work (maps scraping, YouTube,
research and email) so a tool can hand the work off and the conversation goes on. services/ai_core/background_jobs.py
re-exports it and registers the tools list_background_jobs and cancel_background_job.

    job_id = start_job("download_youtube_media", "YouTube download", factory, timeout=1800, announce=True)
        factory(ctx) -> awaitable dict (the ToolResult; may carry artifact {kind, path?, url?, name})
    ctx.progress(percent | None, message)   throttled: one event per 1.5 s, or a 5-point move
    ctx.cancelled                           True once cancel_job asked for it
    list_jobs(), get_job(job_id), cancel_job(job_id)

At most MAX_CONCURRENT (2) jobs run per event loop; the rest wait their turn (status 'queued'). Every job emits K-31
tool_sync events {id, tool, action: title, phase: 'start'|'progress'|'end', status, details, progress?, duration_ms?,
job: true, artifact?, timestamp}; the bridge turns them into tool_progress for the Jobs panel (C-58). The end is
announced (K-18): the full message in the UI chat, spoken as a fixed line ("Sir, <title> mukammal ho gaya"), because a
result can carry external text. Jobs live in this process only: an agent restart loses them, and the tool says so.
Nothing here logs a job's message or result.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("JARVIS-JOBS")

MAX_CONCURRENT = 2
DEFAULT_TIMEOUT_S = 1800.0
PROGRESS_MIN_INTERVAL_S = 1.5
PROGRESS_MIN_STEP = 5.0
KEEP_FINISHED = 50
STARTED_AT = time.time()  # this process's start: jobs are memory-only (K-20)

QUEUED, RUNNING, DONE, ERROR, CANCELLED = "queued", "running", "done", "error", "cancelled"
ACTIVE = (QUEUED, RUNNING)
_SYNC_END = {DONE: "success", ERROR: "error", CANCELLED: "cancelled"}  # tool_sync end status (K-13/K-31)


@dataclass
class _Job:
    job_id: str
    tool: str
    title: str
    status: str = QUEUED
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    percent: float | None = None
    message: str = ""
    artifact: dict[str, Any] | None = None
    cancel_requested: bool = False
    task: asyncio.Task[Any] | None = None
    loop: asyncio.AbstractEventLoop | None = None
    last_emit: float = float("-inf")
    last_emit_percent: float | None = None

    def public(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "tool": self.tool, "title": self.title, "status": self.status,
                "percent": self.percent, "message": self.message, "created": self.created,
                "started": self.started, "finished": self.finished, "artifact": self.artifact}


class JobContext:
    """What a job's factory gets: its identity, cooperative cancel and progress reporting."""

    def __init__(self, job: _Job) -> None:
        self._job = job

    @property
    def job_id(self) -> str:
        return self._job.job_id

    @property
    def tool(self) -> str:
        return self._job.tool

    @property
    def title(self) -> str:
        return self._job.title

    @property
    def cancelled(self) -> bool:
        return self._job.cancel_requested

    async def progress(self, percent: float | None, message: str) -> None:
        """Records the job's progress; emits a tool_sync 'progress' event at most once per 1.5 s unless the percentage
        moved 5 points or more. Never raises."""
        job = self._job
        try:
            clean = None if percent is None else max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            clean = None
        job.percent = clean if clean is not None else job.percent
        job.message = " ".join(str(message or "").split())[:300]
        now = time.monotonic()
        moved = (clean is not None and job.last_emit_percent is not None
                 and abs(clean - job.last_emit_percent) >= PROGRESS_MIN_STEP)
        if now - job.last_emit < PROGRESS_MIN_INTERVAL_S and not moved:
            return
        job.last_emit, job.last_emit_percent = now, clean if clean is not None else job.last_emit_percent
        await _emit(job, "progress", "running", job.message, progress=job.percent)


_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_tasks: set[asyncio.Task[Any]] = set()


def _semaphore(loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
    with _jobs_lock:
        sem = _semaphores.get(loop)
        if sem is None:
            sem = _semaphores[loop] = asyncio.Semaphore(MAX_CONCURRENT)
        return sem


async def _emit(job: _Job, phase: str, status: str, details: str, *, progress: float | None = None,
                duration_ms: int | None = None) -> None:
    """One K-31 tool_sync event for the job (never raises)."""
    payload: dict[str, Any] = {"id": job.job_id, "tool": job.tool, "action": job.title, "phase": phase,
                               "status": status, "details": details, "job": True, "timestamp": time.time()}
    if progress is not None:
        payload["progress"] = round(progress, 1)
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if job.artifact:
        payload["artifact"] = job.artifact
    try:
        from services.utils.jarvis_bridge import notify_event  # pylint: disable=import-outside-toplevel
        await notify_event("tool_sync", payload)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("job event not sent: %s", type(e).__name__)


def _ok(result: Any) -> bool:
    status = result.get("status") if isinstance(result, dict) else None
    return status in (None, "success", "ok", "done", "started")


def _artifact(result: Any) -> dict[str, Any] | None:
    art = result.get("artifact") if isinstance(result, dict) else None
    if not isinstance(art, dict):
        return None
    keep = {k: art[k] for k in ("kind", "path", "url", "name", "area", "relpath", "sha256") if art.get(k) is not None}
    return keep or None


def _forget_old() -> None:
    finished = [j for j in _jobs.values() if j.status not in ACTIVE]
    for job in sorted(finished, key=lambda j: j.finished or 0)[:-KEEP_FINISHED or None]:
        _jobs.pop(job.job_id, None)


async def _run(job: _Job, factory: Callable[[JobContext], Any], timeout: float, announce: bool) -> None:
    result: Any = None
    status = ERROR
    started_mono = time.monotonic()
    try:
        async with _semaphore(asyncio.get_running_loop()):
            if job.cancel_requested:
                raise asyncio.CancelledError
            job.status, job.started = RUNNING, time.time()
            started_mono = time.monotonic()
            await _emit(job, "progress", "running", job.message or "shuru", progress=job.percent)
            outcome = factory(JobContext(job))
            if inspect.isawaitable(outcome):
                outcome = await asyncio.wait_for(outcome, timeout)
            result = outcome
        status = DONE if _ok(result) else ERROR
        job.artifact = _artifact(result)
        raw_message = result.get("message") if isinstance(result, dict) else result
        job.message = " ".join(str(raw_message).split())[:500] if raw_message else ""
    except asyncio.CancelledError:
        status, job.message = CANCELLED, "Cancel kar diya gaya."
    except asyncio.TimeoutError:
        status, job.message = ERROR, f"Waqt khatam ({int(timeout)} s)."
    except Exception as e:  # pylint: disable=broad-exception-caught
        status, job.message = ERROR, f"Masla: {type(e).__name__}"
        logger.warning("job %s (%s) failed: %s", job.job_id, job.tool, type(e).__name__)
    finally:
        job.status, job.finished = status, time.time()
        if status == DONE:
            job.percent = 100.0
        await _emit(job, "end", _SYNC_END[status], job.message, progress=job.percent,
                    duration_ms=int((time.monotonic() - started_mono) * 1000))
        logger.info("JOB %s %s tool=%s", job.job_id, status, job.tool)
        if announce and status != CANCELLED:
            await _announce_end(job, status)
        with _jobs_lock:
            _forget_old()


async def _announce_end(job: _Job, status: str) -> None:
    try:
        from services.utils import jarvis_announcer  # pylint: disable=import-outside-toplevel
        shown = job.message or (f"{job.title} mukammal." if status == DONE else f"{job.title} nahi ho saka.")
        spoken = (f"Sir, {job.title} mukammal ho gaya, natija chat mein hai." if status == DONE
                  else f"Sir, {job.title} nahi ho saka, wajah chat mein hai.")
        await jarvis_announcer.announce(shown, priority="normal", kind="job", spoken_text=spoken)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("job end not announced: %s", type(e).__name__)


def start_job(tool: str, title: str, factory: Callable[[JobContext], Awaitable[dict[str, Any]]], *,
              timeout: float = DEFAULT_TIMEOUT_S, announce: bool = True) -> str:
    """Starts (or queues) a background job on the running event loop and returns its id at once (K-20)."""
    loop = asyncio.get_running_loop()
    job = _Job(job_id=uuid.uuid4().hex[:12], tool=str(tool), title=" ".join(str(title or tool).split())[:120])
    job.loop = loop
    with _jobs_lock:
        _jobs[job.job_id] = job
    start_event = loop.create_task(_emit(job, "start", QUEUED, job.title))
    _tasks.add(start_event)
    start_event.add_done_callback(_tasks.discard)
    job.task = loop.create_task(_run(job, factory, max(1.0, float(timeout)), announce), name=f"job_{job.job_id}")
    _tasks.add(job.task)
    job.task.add_done_callback(_tasks.discard)
    logger.info("JOB %s queued tool=%s", job.job_id, job.tool)
    return job.job_id


def list_jobs() -> list[dict[str, Any]]:
    """Every known job, active first, newest first."""
    with _jobs_lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda j: (j.status not in ACTIVE, -(j.created or 0)))
    return [j.public() for j in jobs]


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(str(job_id or ""))
    return job.public() if job is not None else None


def cancel_job(job_id: str) -> bool:
    """Asks a queued or running job to stop (its task is cancelled on its own loop). False for an unknown or finished
    job."""
    with _jobs_lock:
        job = _jobs.get(str(job_id or ""))
    if job is None or job.status not in ACTIVE:
        return False
    job.cancel_requested = True
    task, loop = job.task, job.loop
    if task is not None and not task.done() and loop is not None and not loop.is_closed():
        try:
            if asyncio.get_running_loop() is loop:
                task.cancel()
            else:
                loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # no running loop here (a thread): hand it over
            loop.call_soon_threadsafe(task.cancel)
    return True


def session_note() -> str:
    """The honest K-20 line for list_background_jobs: jobs are memory-only."""
    since = time.strftime("%H:%M", time.localtime(STARTED_AT))
    return (f"Sirf is session (agent start {since}) ke jobs; agent restart se pehle ke jobs khatam ho chuke hain, "
            "unka natija yaad nahi.")
