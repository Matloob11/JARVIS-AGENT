"""Desktop Node write-ahead log and outbox (plan task C-120 step 6; K-70 Refusals and Timers, K-76).

Every database lives in tmp_path. The crash test runs a child Python that writes the WAL and then ends with
os._exit (no cleanup, like kill -9); the child does nothing else.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from services.utils.desktop_protocol import ResultRecord
from src.core.desktop import wal as wal_mod
from src.core.desktop.wal import WriteAheadLog

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_759_400_000.0


class Wall:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Wall:
    return Wall()


@pytest.fixture
def wal(tmp_path: Path, clock: Wall):
    log = WriteAheadLog(tmp_path / "run" / wal_mod.WAL_FILE, wall=clock)
    yield log
    log.close()


FIELDS = {"tool": "type_text_tool", "room": "jarvis-hybrid", "origin": "llm", "peer": "agent:job-1"}


def _record(call_id: str, clock: Wall) -> ResultRecord:
    return ResultRecord(call_id=call_id, tool="type_text_tool", room="jarvis-hybrid", origin="llm",
                        original_ts=clock.now, finished_at=clock.now,
                        result={"status": "completed", "result": {"status": "success", "tool": "type_text_tool",
                                                                  "message": "ok"}, "effect": "done",
                                "duration_ms": 5, "blobs": []})


def test_a_call_goes_accepted_running_completed_and_its_result_waits_for_the_ack(wal, clock):
    assert wal.accept("call-0001", exec_id="e" * 32, boot_id="b1", **FIELDS)
    entry = wal.get("call-0001")
    assert entry.state == wal_mod.STATE_ACCEPTED and entry.started and entry.in_flight
    wal.mark_running("call-0001")
    assert wal.get("call-0001").state == wal_mod.STATE_RUNNING
    clock.now += 2
    wal.finish("call-0001", status="completed", effect="done", record=_record("call-0001", clock),
               peer="agent:job-1")
    entry = wal.get("call-0001")
    assert (entry.state, entry.status, entry.effect, entry.finished_at) == ("completed", "completed", "done", T0 + 2)
    [kept] = wal.outbox()
    assert kept.record.call_id == "call-0001" and kept.peer == "agent:job-1"
    assert kept.record.result["result"]["message"] == "ok"
    assert wal.ack_result("call-0001") and wal.outbox() == []
    assert not wal.ack_result("call-0001")
    assert wal.report() == [{"call_id": "call-0001", "state": "completed", "finished_at": T0 + 2}]


def test_a_started_call_is_never_refused_or_accepted_again(wal):
    assert wal.accept("call-0002", **FIELDS)
    assert not wal.accept("call-0002", **FIELDS)
    assert not wal.refuse("call-0002", code="DESKTOP_READ_ONLY", **FIELDS)
    assert wal.get("call-0002").state == wal_mod.STATE_ACCEPTED


def test_a_refusal_does_not_block_the_same_call_id_later(wal):
    assert wal.refuse("call-0003", code="APPROVAL_REQUIRED", **FIELDS)
    entry = wal.get("call-0003")
    assert entry.state == wal_mod.STATE_REFUSED and not entry.started and entry.code == "APPROVAL_REQUIRED"
    assert wal.report()[0]["state"] == "never_started"
    assert wal.accept("call-0003", **FIELDS)  # re-sent with its approval
    assert wal.get("call-0003").state == wal_mod.STATE_ACCEPTED and wal.get("call-0003").code is None


def test_start_window_missed_is_reported_as_such(wal):
    assert wal.refuse("call-0004", code="START_WINDOW_MISSED", state=wal_mod.STATE_START_WINDOW_MISSED, **FIELDS)
    assert wal.report()[0]["state"] == "start_window_missed"
    with pytest.raises(ValueError):
        wal.refuse("call-0005", code="X", state=wal_mod.STATE_COMPLETED, **FIELDS)
    with pytest.raises(ValueError):
        wal.finish("call-0004", state=wal_mod.STATE_RUNNING)


def test_kill_during_a_call_shows_interrupted_after_the_restart(tmp_path):
    db = tmp_path / "run" / wal_mod.WAL_FILE
    child = textwrap.dedent(f"""
        import os
        from src.core.desktop.wal import WriteAheadLog
        wal = WriteAheadLog(r"{db}")
        fields = dict(tool="type_text_tool", room="jarvis-hybrid", origin="llm", peer="agent:job-1")
        wal.accept("call-running", **fields)
        wal.mark_running("call-running")
        wal.accept("call-waiting", **fields)  # accepted, still waiting for the input lock
        wal.refuse("call-refused", code="DESKTOP_LOCKED", **fields)
        os._exit(9)  # no cleanup at all, as kill -9
    """)
    result = subprocess.run([sys.executable, "-c", child], cwd=ROOT, capture_output=True, text=True, timeout=60,
                            check=False)
    assert result.returncode == 9, result.stderr

    with WriteAheadLog(db) as wal:
        assert wal.get("call-running").state == wal_mod.STATE_RUNNING  # as the crash left it
        assert wal.recover() == {"interrupted": 1, "never_started": 1}
        states = {item["call_id"]: item["state"] for item in wal.report()}
        assert states == {"call-running": "interrupted", "call-waiting": "never_started",
                          "call-refused": "never_started"}
        assert wal.get("call-running").started  # interrupted: it may have acted, so it never runs again
        assert not wal.get("call-waiting").started
        assert wal.recover() == {"interrupted": 0, "never_started": 0}


def test_the_outbox_keeps_10_minutes_and_200_entries(wal, clock):
    for index in range(205):
        call_id = f"call-{index:04d}"
        wal.accept(call_id, **FIELDS)
        wal.finish(call_id, status="completed", effect="done", record=_record(call_id, clock), peer="agent:job-1")
        clock.now += 0.001
    kept = wal.outbox()
    assert len(kept) == wal_mod.OUTBOX_MAX == 200
    assert kept[0].record.call_id == "call-0005" and kept[-1].record.call_id == "call-0204"
    clock.now += wal_mod.OUTBOX_TTL_S + 1
    assert wal.outbox() == [] and wal.outbox_size() == 0
    assert wal.get("call-0204").state == wal_mod.STATE_COMPLETED  # the WAL row stays


def test_the_report_covers_the_last_hour_and_rows_stay_24_hours(wal, clock):
    wal.refuse("call-old", code="DESKTOP_LOCKED", **FIELDS)
    clock.now += wal_mod.REPORT_WINDOW_S + 1
    wal.refuse("call-new", code="DESKTOP_LOCKED", **FIELDS)
    assert [item["call_id"] for item in wal.report()] == ["call-new"]
    assert wal.report(limit=0) == []
    clock.now = T0 + 24 * 3600
    assert wal.purge() == 0 and wal.get("call-old") is not None  # call_ids are kept at least 24 h
    wal.accept("call-flight", **FIELDS)
    clock.now = T0 + wal_mod.WAL_RETENTION_S + 7200
    assert wal.purge() == 2  # call-old and call-new; never a call in flight
    assert wal.get("call-old") is None and wal.get("call-flight") is not None
    assert wal_mod.WAL_RETENTION_S >= 24 * 3600


async def test_async_variants(wal, clock):
    assert await wal.aaccept("call-async", **FIELDS)
    assert (await wal.aget("call-async")).state == wal_mod.STATE_ACCEPTED
    await wal.afinish("call-async", status="cancelled", effect="possible", code="STOPPED_BY_USER",
                      record=_record("call-async", clock), peer="agent:job-1")
    assert (await wal.aoutbox_entry("call-async")).record.call_id == "call-async"
    assert await wal.aoutbox_size() == 1
    assert await wal.aack_result("call-async")
    assert await wal.areport() == [{"call_id": "call-async", "state": "completed", "finished_at": T0}]
    assert not await wal.arefuse("call-async", code="X", **FIELDS)
    assert wal.counts() == {"completed": 1}


def test_closed_wal_refuses_work(tmp_path):
    log = WriteAheadLog(tmp_path / "w.db")
    log.close()
    log.close()
    with pytest.raises(RuntimeError):
        log.get("call-0001")
