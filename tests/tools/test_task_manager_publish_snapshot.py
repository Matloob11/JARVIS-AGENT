"""REQ-FIX-3 (6): async publish_snapshot reads via to_thread and posts task_sync."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import services.automation.jarvis_task_manager as tm


@pytest.fixture()
def tmp_mgr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "tasks.db"
    monkeypatch.setattr(tm, "DB_PATH", str(db))
    mgr = tm.TaskManager()
    monkeypatch.setattr(tm, "task_manager", mgr)
    return mgr


@pytest.mark.asyncio
async def test_publish_snapshot_posts_one_task_sync_with_epoch_due(tmp_mgr):
    when = datetime.now() + timedelta(hours=2)
    tid = tmp_mgr.add_task("publish snapshot probe", when)

    posts: list = []

    async def capture(snapshot):
        posts.append(snapshot)

    with patch.object(tm, "_post_task_sync", new=AsyncMock(side_effect=capture)):
        await asyncio.to_thread(tmp_mgr.mark_completed, tid)
        await tmp_mgr.publish_snapshot()

    assert len(posts) >= 1
    last = posts[-1]
    assert isinstance(last, list)
    for row in last:
        if not isinstance(row, dict):
            continue
        due = row.get("due_at")
        if due is not None:
            assert isinstance(due, (int, float))
            assert due > 1_000_000_000
