"""C-08 / K-53: single agent worker lock, worker options and the vortex.py skip.

Real locking runs in subprocesses with force=True (or without the pytest markers) and
JARVIS_RUN_DIR=tmp_path, so these tests never touch the live run/agent.lock. No test starts an
agent worker: the subprocesses that run 'python -m src.core.agent' or 'python -m
src.core.agent_runner' get a fake 'livekit' package that exits 99 on import, so they can never get
as far as registering with LiveKit.
"""

import importlib
import io
import json
import os
import queue
import runpy
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from src.core import agent_lock

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_MESSAGE = (
    "[JARVIS] Agent already running (pid {pid}, by {by}, since {since}). "
    "Use: python scripts/agent_ctl.py restart --by <you> --reason <why>"
)
HOLD = (
    "import os, sys\n"
    "from src.core import agent_lock\n"
    "agent_lock.acquire_or_exit(force=True)\n"
    "print('LOCKED', os.getpid(), flush=True)\n"
    "sys.stdin.readline()\n"
)
ACQUIRE = (
    "import os\n"
    "from src.core import agent_lock\n"
    "agent_lock.acquire_or_exit(force={force})\n"
    "print('ACQUIRED', os.getpid(), flush=True)\n"
)
# Both start paths lock before importing livekit.
ENTRY_MODULES = ("src.core.agent", "src.core.agent_runner")


def _env(run_dir: Path, **extra: str) -> dict[str, str]:
    """Child env without the pytest markers, so the lock is not a no-op there."""
    env = os.environ.copy()
    for name in ("PYTEST_CURRENT_TEST", "JARVIS_NO_LOCK", "JARVIS_STARTED_BY", "JARVIS_START_REASON"):
        env.pop(name, None)
    env["JARVIS_RUN_DIR"] = str(run_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _run(code: str, run_dir: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=_env(run_dir, **extra), capture_output=True, text=True, timeout=60,
        check=False,
    )


def _read_line(proc: subprocess.Popen[str], timeout: float) -> str:
    lines: queue.Queue[str] = queue.Queue()
    assert proc.stdout is not None
    threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout).strip()
    except queue.Empty:
        return ""


class Holder:
    """A subprocess that holds the lock in tmp_path until stop() closes its stdin."""

    def __init__(self, run_dir: Path) -> None:
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with  # lives until stop()
            [sys.executable, "-c", HOLD],
            cwd=ROOT,
            env=_env(run_dir, JARVIS_STARTED_BY="pytest-holder", JARVIS_START_REASON="C-08 test"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        line = _read_line(self.proc, timeout=60)
        if not line.startswith("LOCKED "):
            self.stop()
            raise AssertionError(f"holder did not lock: {line!r} {self.proc.stderr.read()!r}")
        # The interpreter's own pid: .venv_312's python.exe is a launcher with a different pid.
        self.pid = int(line.split()[1])

    def stop(self) -> None:
        if self.proc.poll() is None:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()  # only this test's own child
                self.proc.wait()


@pytest.fixture
def holder(tmp_path):
    held = Holder(tmp_path)
    try:
        yield held
    finally:
        held.stop()


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path))
    return tmp_path


def _fake_livekit_site(tmp_path: Path) -> Path:
    site = tmp_path / "fake_site"
    (site / "livekit").mkdir(parents=True)
    (site / "livekit" / "__init__.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
    return site


# --- agent_lock ---------------------------------------------------------------------------------

def test_second_acquire_exits_3_while_another_process_holds_the_lock(run_dir, holder):
    assert agent_lock.is_locked() is True
    info = agent_lock.read_pid_info()
    assert info is not None
    assert info["pid"] == holder.pid
    assert info["by"] == "pytest-holder"
    assert info["reason"] == "C-08 test"
    assert isinstance(info["argv"], list) and info["argv"]
    assert "git_sha" in info and info["started_at"]

    started = time.monotonic()
    second = _run(ACQUIRE.format(force=True), run_dir)
    elapsed = time.monotonic() - started

    assert second.returncode == agent_lock.EXIT_ALREADY_RUNNING == 3
    assert "ACQUIRED" not in second.stdout
    assert EXPECTED_MESSAGE.format(pid=holder.pid, by="pytest-holder", since=info["started_at"]) in second.stderr
    assert elapsed < 5

    holder.stop()
    assert agent_lock.is_locked() is False


def test_stale_pid_json_with_dead_pid_does_not_block(run_dir):
    dead = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True, text=True, check=True, timeout=60,
    )
    dead_pid = int(dead.stdout.strip())
    (run_dir / "agent.lock").write_bytes(b"")
    (run_dir / "agent.pid.json").write_text(
        json.dumps({"pid": dead_pid, "started_at": "2026-01-01T00:00:00+05:00", "by": "ghost",
                    "reason": "", "argv": [], "git_sha": ""}),
        encoding="utf-8",
    )
    assert agent_lock.is_locked() is False

    result = _run(ACQUIRE.format(force=True), run_dir, JARVIS_STARTED_BY="pytest")

    assert result.returncode == 0, result.stderr
    new_pid = int(result.stdout.split()[1])
    info = agent_lock.read_pid_info()
    assert info is not None
    assert info["pid"] == new_pid != dead_pid
    assert info["by"] == "pytest"
    assert agent_lock.is_locked() is False  # that process exited, so the OS dropped its lock


def test_is_locked_without_lock_file_is_false_and_creates_nothing(tmp_path, monkeypatch):
    missing = tmp_path / "run"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(missing))
    assert agent_lock.is_locked() is False
    assert agent_lock.read_pid_info() is None
    assert not missing.exists()


def test_in_process_acquire_is_idempotent_and_release_unlocks(run_dir):
    try:
        assert agent_lock.acquire_or_exit(force=True) is True
        assert agent_lock.acquire_or_exit(force=True) is True  # already held: no exit
        assert agent_lock.is_locked() is True
        info = agent_lock.read_pid_info()
        assert info is not None and info["pid"] == os.getpid()
        assert _run(ACQUIRE.format(force=True), run_dir).returncode == 3
    finally:
        agent_lock.release()
    assert agent_lock.is_locked() is False


def test_acquire_is_a_noop_under_pytest(tmp_path, monkeypatch):
    missing = tmp_path / "run"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(missing))
    assert agent_lock.lock_disabled() is True
    assert agent_lock.acquire_or_exit() is False
    assert not missing.exists()


def test_default_call_locks_outside_pytest_and_no_lock_env_disables_it(run_dir, holder):
    assert _run(ACQUIRE.format(force=False), run_dir).returncode == 3
    skipped = _run(ACQUIRE.format(force=False), run_dir, JARVIS_NO_LOCK="1")
    assert skipped.returncode == 0, skipped.stderr
    assert "ACQUIRED" in skipped.stdout


def test_run_dir_defaults_to_project_run(monkeypatch):
    monkeypatch.delenv("JARVIS_RUN_DIR", raising=False)
    assert agent_lock.run_dir() == agent_lock.PROJECT_ROOT / "run"
    assert agent_lock.lock_path() == agent_lock.PROJECT_ROOT / "run" / "agent.lock"
    assert agent_lock.pid_path() == agent_lock.PROJECT_ROOT / "run" / "agent.pid.json"


def test_pid_info_argv_masks_secret_options():
    argv = ["agent.py", "start", "--api-secret", "s3", "--api-key=k1", "--url", "wss://x", "--log-level", "info"]
    assert agent_lock.redact_argv(argv) == [
        "agent.py", "start", "--api-secret", "***", "--api-key=***", "--url", "wss://x", "--log-level", "info",
    ]


# --- src/core/agent.py and src/core/agent_runner.py entries -------------------------------------

@pytest.mark.parametrize("module", ENTRY_MODULES)
def test_agent_module_exits_3_before_importing_livekit(tmp_path, holder, module):
    env = _env(tmp_path, PYTHONPATH=str(_fake_livekit_site(tmp_path)))
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    elapsed = time.monotonic() - started

    # 99 would mean the fake livekit was imported before the lock was checked.
    assert result.returncode == 3, (result.returncode, result.stderr)
    assert f"[JARVIS] Agent already running (pid {holder.pid}, by pytest-holder" in result.stderr
    assert elapsed < 5


@pytest.mark.parametrize("module", ENTRY_MODULES)
def test_agent_module_locks_and_records_itself_before_importing_livekit(tmp_path, module):
    env = _env(tmp_path, PYTHONPATH=str(_fake_livekit_site(tmp_path)),
               JARVIS_STARTED_BY="pytest", JARVIS_START_REASON="C-08 entry")
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
    )

    assert result.returncode == 99, result.stderr  # got past the lock, stopped at 'livekit'
    info = json.loads((tmp_path / "agent.pid.json").read_text(encoding="utf-8"))
    assert info["by"] == "pytest" and info["reason"] == "C-08 entry"
    assert info["argv"][-1] == "--help"


def _clear_worker_env(monkeypatch):
    for name in ("JARVIS_WORKER_HOST", "JARVIS_WORKER_PORT", "JARVIS_AGENT_NAME"):
        monkeypatch.delenv(name, raising=False)


def test_build_worker_options_with_installed_livekit(monkeypatch):
    from livekit.agents import WorkerOptions

    agent_module = importlib.import_module("src.core.agent")
    runner_module = importlib.import_module("src.core.agent_runner")
    assert agent_module.build_worker_options is runner_module.build_worker_options  # one builder
    _clear_worker_env(monkeypatch)
    opts = agent_module.build_worker_options()
    assert isinstance(opts, WorkerOptions)
    assert (opts.host, opts.port, opts.agent_name) == ("127.0.0.1", 8081, "")
    assert opts.entrypoint_fnc is agent_module.entrypoint

    monkeypatch.setenv("JARVIS_WORKER_HOST", "127.0.0.2")
    monkeypatch.setenv("JARVIS_WORKER_PORT", "18081")
    monkeypatch.setenv("JARVIS_AGENT_NAME", "jarvis")
    sentinel = agent_module.entrypoint
    opts = agent_module.build_worker_options(sentinel)
    assert (opts.host, opts.port, opts.agent_name) == ("127.0.0.2", 18081, "jarvis")
    assert opts.entrypoint_fnc is sentinel


def test_agent_runner_main_takes_the_lock_then_uses_the_shared_builder(monkeypatch):
    import livekit.agents.cli as lk_cli
    from livekit import rtc
    from livekit.agents import WorkerOptions

    # Running the module again re-wraps Room._on_room_event; monkeypatch restores it afterwards.
    monkeypatch.setattr(rtc.Room, "_on_room_event", rtc.Room._on_room_event)
    calls: list[object] = []
    monkeypatch.setattr(agent_lock, "acquire_or_exit", lambda: calls.append("lock"))
    monkeypatch.setattr(lk_cli, "run_app", calls.append)
    _clear_worker_env(monkeypatch)
    for name in ("src.core.agent", "src.core.agent_runner"):
        monkeypatch.delitem(sys.modules, name, raising=False)  # restored after the test

    runpy.run_module("src.core.agent_runner", run_name="__main__")

    # No import cycle: importing src.core.agent here would import this module a second time and
    # run its body (logging setup, Room patch, logger handlers) twice.
    assert "src.core.agent" not in sys.modules
    assert "src.core.agent_runner" not in sys.modules
    assert calls[0] == "lock"
    opts = calls[1]
    assert isinstance(opts, WorkerOptions)
    assert (opts.host, opts.port, opts.agent_name) == ("127.0.0.1", 8081, "")
    assert opts.entrypoint_fnc.__name__ == "entrypoint"


# --- src/core/vortex.py -------------------------------------------------------------------------

class FakeProc:
    """poll() returns the given results in order and then keeps returning the last one."""

    def __init__(self, *polls: int | None) -> None:
        self._polls = list(polls)
        self.stdout = io.StringIO("")
        self.pid = 4242

    def poll(self) -> int | None:
        return self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]


@pytest.fixture
def vortex(monkeypatch):
    module = importlib.import_module("src.core.vortex")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("JARVIS_STARTED_BY", raising=False)
    monkeypatch.setattr(module, "time", types.SimpleNamespace(sleep=lambda _s: None))

    class _Healthy:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake_request = types.SimpleNamespace(urlopen=lambda *_a, **_k: _Healthy())
    monkeypatch.setattr(module, "urllib", types.SimpleNamespace(request=fake_request))
    return module


def _record_starts(monkeypatch, vortex, procs):
    started = []

    def fake_start(command, name, extra_env=None):
        started.append((command, name, extra_env))
        return procs[name]

    monkeypatch.setattr(vortex, "start_process", fake_start)
    return started


def test_vortex_hybrid_mode_starts_nothing(vortex, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HYBRID", "1")
    started = _record_starts(monkeypatch, vortex, {})

    vortex.main()  # returns, exit code 0, so Electron does not restart it

    assert not started
    assert vortex.SKIP_AGENT_MESSAGE in capsys.readouterr().out


def test_vortex_with_running_worker_serves_only_the_bridge(vortex, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HYBRID", "0")
    monkeypatch.setattr(vortex.agent_lock, "is_locked", lambda: True)
    started = _record_starts(monkeypatch, vortex, {"UI-BRIDGE": FakeProc(None, None, 1)})

    with pytest.raises(SystemExit):
        vortex.main()  # ends when the fake bridge exits

    assert [name for _cmd, name, _env in started] == ["UI-BRIDGE"]
    assert "src.core.ui_bridge" in started[0][0]
    assert vortex.SKIP_AGENT_MESSAGE in capsys.readouterr().out


def test_vortex_treats_agent_exit_3_as_already_running(vortex, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HYBRID", "0")
    monkeypatch.setattr(vortex.agent_lock, "is_locked", lambda: False)
    procs = {"UI-BRIDGE": FakeProc(None, None, 1), "AGENT-RUNNER": FakeProc(3)}
    started = _record_starts(monkeypatch, vortex, procs)

    with pytest.raises(SystemExit):
        vortex.main()

    out = capsys.readouterr().out
    assert [name for _cmd, name, _env in started] == ["UI-BRIDGE", "AGENT-RUNNER"]
    assert "-m src.core.agent dev" in started[1][0]
    assert started[1][2] == {"JARVIS_STARTED_BY": "vortex"}
    assert "AGENT-RUNNER exited 3 (already running)" in out
    assert "AGENT-RUNNER EXITED" not in out
    assert "UI-BRIDGE EXITED with code 1" in out  # the bridge stayed monitored after the agent left


def test_vortex_reads_hybrid_flag_from_root_env_when_unset(vortex, monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_HYBRID", raising=False)
    monkeypatch.setattr(vortex, "project_root", str(tmp_path))
    assert vortex._hybrid_mode() is False  # no .env at all
    (tmp_path / ".env").write_text("JARVIS_HYBRID=1\n", encoding="utf-8")
    assert vortex._hybrid_mode() is True
    monkeypatch.setenv("JARVIS_HYBRID", "0")  # the process environment wins over .env
    assert vortex._hybrid_mode() is False
