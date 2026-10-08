"""
# services/ai_core/background_jobs.py
BackgroundJobsAPI re-export (contract K-20, tools-info import path) plus its two LLM tools (plan task C-57).
The implementation lives in services/utils/jarvis_jobs.py.
"""
from __future__ import annotations

from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_jobs import (  # noqa: F401
    JobContext,
    cancel_job,
    get_job,
    list_jobs,
    session_note,
    start_job,
)

LIST_LIMIT = 10


@jarvis_tool(timeout=10)
async def list_background_jobs() -> dict[str, Any]:
    """
    Lists JARVIS's background jobs (running, queued and the most recent finished ones) with their progress.
    Use it when Sir asks which tasks are running ("kaunse kaam chal rahe hain?"). Jobs started before the last
    agent restart are gone and are not listed.
    """
    jobs = list_jobs()[:LIST_LIMIT]
    active = [j for j in jobs if j["status"] in ("queued", "running")]
    if not jobs:
        message = "Sir, abhi koi background kaam nahi chal raha."
    elif not active:
        message = f"Sir, abhi koi kaam nahi chal raha; aakhri {len(jobs)} mukammal ya band ho chuke."
    else:
        message = "Sir, chal rahe kaam: " + "; ".join(
            f"{j['title']} ({j['status']}{'' if j['percent'] is None else f', {int(j['percent'])}%'})" for j in active)
    return {"status": "success", "jobs": jobs, "message": message, "note": session_note()}


@jarvis_tool(timeout=10)
async def cancel_background_job(job_id: str) -> dict[str, Any]:
    """
    Cancels one queued or running background job by its job_id (from list_background_jobs).

    Args:
        job_id: The job_id of the job to stop.
    """
    if cancel_job(job_id):
        return {"status": "success", "job_id": job_id, "message": "Sir, yeh kaam band kar raha hoon."}
    return {"status": "error", "job_id": job_id, "error_code": "JOB_NOT_ACTIVE",
            "message": "Sir, is ID ka koi chalta hua kaam nahi mila."}
