"""services/utils/jarvis_filelock.py (plan task C-125 step 1, for X-66): no lost update between processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from services.utils import jarvis_filelock

ROOT = Path(__file__).resolve().parents[2]

WORKER = textwrap.dedent("""
    import json, os, sys, tempfile
    sys.path.insert(0, sys.argv[3])
    from services.utils.jarvis_filelock import locked
    path, rounds = sys.argv[1], int(sys.argv[2])
    for _ in range(rounds):
        with locked(path, timeout=30):
            data = json.loads(open(path, encoding="utf-8").read())
            data["count"] += 1
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data))
            os.replace(tmp, path)  # the data file is replaced while the sidecar lock is held
""")


def test_two_processes_lose_no_update(tmp_path):
    data = tmp_path / "reminders.json"
    data.write_text(json.dumps({"count": 0}), encoding="utf-8")
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    rounds = 40
    procs = [subprocess.Popen([sys.executable, str(script), str(data), str(rounds), str(ROOT)]) for _ in range(2)]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    assert json.loads(data.read_text(encoding="utf-8"))["count"] == 2 * rounds
    assert jarvis_filelock.lock_path_for(data).exists()


def test_threads_are_serialised_too(tmp_path):
    data = tmp_path / "state.json"
    data.write_text("0", encoding="utf-8")

    def bump():
        for _ in range(25):
            with jarvis_filelock.locked(data):
                value = int(data.read_text(encoding="utf-8"))
                time.sleep(0.001)
                data.write_text(str(value + 1), encoding="utf-8")

    threads = [threading.Thread(target=bump) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert int(data.read_text(encoding="utf-8")) == 75


def test_a_busy_lock_times_out_and_is_free_again_after_release(tmp_path):
    data = tmp_path / "ledger.json"
    held = threading.Event()
    release = threading.Event()

    def holder():
        with jarvis_filelock.locked(data):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(5)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with jarvis_filelock.locked(data, timeout=0.2):
            pass
    assert time.monotonic() - started < 3
    release.set()
    thread.join(10)
    with jarvis_filelock.locked(data, timeout=1) as path:
        assert path == data


def test_a_dead_holder_releases_the_lock(tmp_path):
    data = tmp_path / "location.json"
    code = ("import sys, time; sys.path.insert(0, sys.argv[2]);"
            "from services.utils.jarvis_filelock import locked;"
            "cm = locked(sys.argv[1]); cm.__enter__(); print('held', flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code, str(data), str(ROOT)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(TimeoutError):
            with jarvis_filelock.locked(data, timeout=0.2):
                pass
    finally:
        proc.kill()
        proc.wait(10)
    with jarvis_filelock.locked(data, timeout=5):  # the OS dropped the dead process's lock
        pass


# ---------------------------------------------------------------- C-125 step 1: the shared JSON files


async def test_identity_changes_of_two_processes_are_both_kept_and_seen(tmp_path):
    """Two IdentityManagers on one identity.json stand for a job and agent-svc: each change is a read-modify-write
    under the file lock, and a reader reloads when the file changed (mtime/inode/size)."""
    from services.ai_core.jarvis_identity import IdentityManager

    path = str(tmp_path / "conversations" / "identity.json")
    job, svc = IdentityManager(path), IdentityManager(path)
    assert await job.add_fact("fact from the job") == "Fact stored: fact from the job"
    assert "fact from the job" in svc.get_context()  # reloaded: the file changed under it
    await svc.update_relationship("Bhai", "elder brother")  # svc read the job's write before its own
    await job.set_anna_mood("playful")
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    assert stored["facts"] == ["fact from the job"] and stored["relationships"]["bhai"] == "elder brother"
    assert stored["anna_mood"] == "playful" and svc.get_anna_state()["mood"] == "playful"
    assert await svc.add_fact("fact from the job") == "Fact already exists."
    assert jarvis_filelock.lock_path_for(path).exists()
    assert not Path(path + ".tmp").exists()


async def test_an_unreadable_identity_file_keeps_the_data_last_read(tmp_path):
    from services.ai_core.jarvis_identity import IdentityManager

    path = tmp_path / "identity.json"
    manager = IdentityManager(str(path))
    await manager.add_fact("kept")
    path.write_text("{broken", encoding="utf-8")
    assert "kept" in manager.get_context() and path.read_text(encoding="utf-8") == "{broken"  # never moved aside


async def test_adaptive_state_of_two_processes_is_merged_not_overwritten(tmp_path):
    from services.utils.jarvis_adaptive import JarvisAdaptiveEngine

    path = str(tmp_path / "learning_state.json")
    job, svc = JarvisAdaptiveEngine(path), JarvisAdaptiveEngine(path)
    job.record_feedback("weather", "strategy_a", 1.0)
    assert svc.get_intent_adjustment("weather") == pytest.approx(1.05)  # reloaded
    svc.record_feedback("music", "strategy_b", 2.0)
    await job.log_interaction("s", "u", "reply", True)
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    assert set(state["intent_weights"]) == {"weather", "music"} and state["ab_results"]["music"]["b"] == 2.0
    assert state["successful_patterns"] == [{"has_response": True, "error": None}]
    assert jarvis_filelock.lock_path_for(path).exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["learning_state.json", "learning_state.json.lock"]
