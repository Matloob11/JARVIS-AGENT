"""C-09 / K-53: scripts/agent_ctl.py, the safe launchers and the root .env as the single settings source.

No test starts, signals or kills a real process: agent_ctl's subprocess calls (_run, _popen), psutil and
the health probe are replaced, and all files live under tmp_path (JARVIS_RUN_DIR, agent_ctl.LOG_DIR).
The only real subprocess is a lock holder (as in test_agent_lock.py) that releases the lock and exits
when its stdin closes; one test probes a closed local port.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import gc
import importlib.util
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.utils import jarvis_config
from src.core import agent_lock

ROOT = Path(__file__).resolve().parents[2]
WINDOWS_LOCAL = ROOT / "deploy" / "windows-local"


def _load_agent_ctl() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("agent_ctl", ROOT / "scripts" / "agent_ctl.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent_ctl = _load_agent_ctl()
WORKER_CMD = ["C:/Users/x/AppData/Local/Programs/Python/Python312/python.exe", "-m", "src.core.agent", "start"]
LAUNCHER_CMD = ["C:/repo/.venv_312/Scripts/python.exe", "-m", "src.core.agent", "start"]


# --------------------------------------------------------------------------- fakes


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


class FakeProc:
    def __init__(self, pid: int, cmdline: list[str], created: float, *, name: str = "python.exe",
                 parent: FakeProc | None = None, children: tuple[FakeProc, ...] = ()) -> None:
        self.pid = pid
        self._cmdline = list(cmdline)
        self._created = created
        self._name = name
        self._parent = parent
        self._children = list(children)
        self.running = True

    def cmdline(self) -> list[str]:
        return list(self._cmdline)

    def create_time(self) -> float:
        return self._created

    def name(self) -> str:
        return self._name

    def parent(self) -> FakeProc | None:
        return self._parent

    def children(self, recursive: bool = False) -> list[FakeProc]:
        return list(self._children)

    def is_running(self) -> bool:
        return self.running

    def kill(self) -> None:
        self.running = False


class FakePsutil:
    """The part of psutil that agent_ctl uses."""

    class Error(Exception):
        pass

    class NoSuchProcess(Error):
        pass

    class AccessDenied(Error):
        pass

    CONN_LISTEN = "LISTEN"

    def __init__(self, *procs: FakeProc) -> None:
        self.procs = {proc.pid: proc for proc in procs}
        self.connections: list[types.SimpleNamespace] = []

    def Process(self, pid: int) -> FakeProc:  # noqa: N802  (psutil's name)
        proc = self.procs.get(pid)
        if proc is None or not proc.running:
            raise self.NoSuchProcess(pid)
        return proc

    def net_connections(self, kind: str = "tcp") -> list[types.SimpleNamespace]:
        return list(self.connections)


@pytest.fixture
def ctl(monkeypatch, tmp_path):
    """agent_ctl with its files in tmp_path, a fake clock, no .env reads and every process call refused."""
    run_dir = tmp_path / "run"
    logs = tmp_path / "logs"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run_dir))
    for name in ("JARVIS_WORKER_HOST", "JARVIS_WORKER_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agent_ctl, "LOG_DIR", logs)
    monkeypatch.setattr(agent_ctl, "ENV_FILES", ())
    clock = FakeClock()
    monkeypatch.setattr(agent_ctl, "_monotonic", clock.monotonic)
    monkeypatch.setattr(agent_ctl, "_sleep", clock.sleep)

    def refuse(cmd, **_kwargs):
        raise AssertionError(f"unexpected process call: {cmd[:3]}")

    monkeypatch.setattr(agent_ctl, "_run", refuse)
    monkeypatch.setattr(agent_ctl, "_popen", refuse)
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: None)
    monkeypatch.setattr(agent_ctl, "worker_info", lambda: None)
    fake_psutil = FakePsutil()
    monkeypatch.setattr(agent_ctl, "psutil", fake_psutil)
    return types.SimpleNamespace(run=run_dir, logs=logs, clock=clock, psutil=fake_psutil)


def _lock(monkeypatch, state: dict) -> None:
    monkeypatch.setattr(agent_lock, "is_locked", lambda: state["locked"])


def write_pid_json(run_dir: Path, pid: int, *, by: str = "claude", reason: str = "test",
                   mode: str = "start", started: datetime | None = None) -> datetime:
    started = started or datetime.now().astimezone().replace(microsecond=0)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "agent.pid.json").write_text(json.dumps({
        "pid": pid, "started_at": started.isoformat(timespec="seconds"), "by": by, "reason": reason,
        "argv": ["C:/repo/src/core/agent.py", mode], "git_sha": "abc1234def",
    }), encoding="utf-8")
    return started


def audit_lines(logs: Path) -> list[dict]:
    path = logs / "agent_ctl.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def ok(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, 0, "", "")


def agent_tree(created: float) -> tuple[FakeProc, FakeProc, FakeProc, FakeProc]:
    """worker (pid.json pid) with a dev-mode child, its venv launcher and the cmd.exe window above."""
    shell = FakeProc(28604, ["cmd", "/k", "python -m src.core.agent start"], created - 30, name="cmd.exe")
    launcher = FakeProc(14280, LAUNCHER_CMD, created - 2, parent=shell)
    child = FakeProc(31501, ["python.exe", "-c", "from multiprocessing.spawn import spawn_main"], created + 5)
    worker = FakeProc(31500, WORKER_CMD, created, parent=launcher, children=(child,))
    return worker, child, launcher, shell


def user_apps(worker: FakeProc, created: float) -> tuple[FakeProc, FakeProc]:
    """Apps that JARVIS tools open for the user (subprocess.Popen, os.startfile): children of the worker
    with no console, so Ctrl+Break never reaches them."""
    notepad = FakeProc(4444, ["C:\\Windows\\System32\\notepad.exe", "C:\\Users\\x\\notes.txt"], created + 60,
                       name="notepad.exe", parent=worker)
    browser = FakeProc(4555, ["C:/Program Files/Google/Chrome/Application/chrome.exe", "https://www.youtube.com/"],
                       created + 90, name="chrome.exe", parent=worker)
    worker._children += [notepad, browser]
    return notepad, browser


def listener_on_8081(ctl) -> None:
    """Another app (not JARVIS) answering on the worker port, with a secret option on its command line."""
    ctl.psutil.connections.append(types.SimpleNamespace(status="LISTEN", laddr=types.SimpleNamespace(port=8081),
                                                        pid=999))
    ctl.psutil.procs[999] = FakeProc(999, ["node.exe", "C:/work/other-app/server.js", "--port", "8081",
                                           "--api-key", "fake-key-value"], 0.0, name="node.exe")


# --------------------------------------------------------------------------- log rotation


def test_rotation_keeps_five_numbered_files_and_touches_nothing_else(ctl, monkeypatch):
    ctl.logs.mkdir()
    monkeypatch.setattr(agent_ctl, "ROTATE_BYTES", 10)
    current = ctl.logs / "agent_stdout.log"
    current.write_text("current-big", encoding="utf-8")
    for number in range(1, 6):
        (ctl.logs / f"agent_stdout.log.{number}").write_text(f"old{number}", encoding="utf-8")
    others = {"agent_stdout.log.6": "six", "agent_stdout.log.bak": "bak", "bridge.log": "bridge",
              "agent_stderr.log": "small", "jarvis_hybrid_local.log": "keep"}
    for name, text in others.items():
        (ctl.logs / name).write_text(text, encoding="utf-8")

    assert agent_ctl.rotate_agent_logs() == ["agent_stdout.log"]

    assert not current.exists()
    assert (ctl.logs / "agent_stdout.log.1").read_text(encoding="utf-8") == "current-big"
    for number in range(2, 6):
        assert (ctl.logs / f"agent_stdout.log.{number}").read_text(encoding="utf-8") == f"old{number - 1}"
    for name, text in others.items():  # old .5 is the one dropped; nothing else is renamed or deleted
        assert (ctl.logs / name).read_text(encoding="utf-8") == text


def test_rotation_starts_at_5_mb(ctl):
    ctl.logs.mkdir()
    assert (agent_ctl.ROTATE_BYTES, agent_ctl.ROTATE_KEEP) == (5 * 1024 * 1024, 5)
    log = ctl.logs / "agent_stderr.log"
    assert agent_ctl.rotate_log(log) is False  # missing file
    log.write_bytes(b"x" * (agent_ctl.ROTATE_BYTES - 1))
    assert agent_ctl.rotate_log(log) is False
    with log.open("ab") as handle:
        handle.write(b"x")
    assert agent_ctl.rotate_log(log) is True
    assert not log.exists() and (ctl.logs / "agent_stderr.log.1").stat().st_size == agent_ctl.ROTATE_BYTES


# --------------------------------------------------------------------------- restart: cooldown and lock


def _fake_restart_steps(monkeypatch, calls: list) -> None:
    def stop(timeout, force=False):
        calls.append(("stop", timeout, force))
        return 0, {"result": "stopped", "old_pid": 111, "graceful": True, "killed": [], "stop_s": 2.0}

    def start(args, mode, *, foreground, timeout=60):
        calls.append(("start", mode, foreground))
        return 0, {"result": "started", "new_pid": 222, "health": 200, "start_s": 20.0, "ensure_room": 0}

    monkeypatch.setattr(agent_ctl, "stop_worker", stop)
    monkeypatch.setattr(agent_ctl, "start_worker", start)


def test_restart_within_the_cooldown_is_refused_without_force(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    (ctl.run / "last_restart.json").write_text(json.dumps({"ts": time.time() - 30, "by": "cursor"}),
                                               encoding="utf-8")
    calls: list = []
    _fake_restart_steps(monkeypatch, calls)

    code = agent_ctl.main(["restart", "--by", "claude", "--reason", "C-09 cooldown"])

    assert code == agent_ctl.EXIT_REFUSED == 4
    assert not calls  # nothing stopped or started
    err = capsys.readouterr().err
    assert "Restart refused" in err and "by cursor" in err and "--force" in err
    assert not (ctl.run / "restart.lock").exists()  # released again
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["error"], entry["by"]) == ("restart", "refused", "cooldown", "claude")


def test_force_overrides_the_cooldown_logs_it_and_starts_a_new_one(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    (ctl.run / "last_restart.json").write_text(json.dumps({"ts": time.time() - 30, "by": "cursor"}),
                                               encoding="utf-8")
    write_pid_json(ctl.run, 111, mode="dev")
    _lock(monkeypatch, {"locked": True})
    calls: list = []
    _fake_restart_steps(monkeypatch, calls)

    code = agent_ctl.main(["restart", "--by", "claude", "--reason", "C-09 test", "--force"])

    assert code == 0
    assert calls == [("stop", 20, False), ("start", "dev", False)]  # keeps the replaced worker's mode
    out = capsys.readouterr().out
    assert "ignoring the 120 s restart cooldown" in out
    assert "### ✅ 🔁 [claude] C-09 test · Agent worker restart (agent_ctl) · " in out
    assert "purana worker (pid 111) band, naya pid 222" in out
    entry = audit_lines(ctl.logs)[-1]
    assert entry["result"] == "restarted" and entry["force"] is True and entry["cooldown_overridden"] is True
    last = json.loads((ctl.run / "last_restart.json").read_text(encoding="utf-8"))
    assert (last["by"], last["old_pid"], last["new_pid"]) == ("claude", 111, 222)
    assert time.time() - last["ts"] < 60
    assert not (ctl.run / "restart.lock").exists()

    # The restart just recorded starts a new cooldown.
    assert agent_ctl.main(["restart", "--by", "cursor", "--reason", "too soon"]) == 4


def test_restart_lock_of_a_running_restart_is_respected(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    lock = ctl.run / "restart.lock"
    stamp = time.time() - 5
    lock.write_text(json.dumps({"pid": 4242, "ts": stamp, "by": "cursor"}), encoding="utf-8")
    ctl.psutil.procs[4242] = FakeProc(4242, ["python", "scripts/agent_ctl.py", "restart"], stamp - 1)
    calls: list = []
    _fake_restart_steps(monkeypatch, calls)

    code = agent_ctl.main(["restart", "--by", "claude", "--reason", "parallel"])

    assert code == 4 and not calls
    assert "run/restart.lock is held by a restart by cursor (pid 4242)" in capsys.readouterr().err
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == 4242  # left alone
    assert audit_lines(ctl.logs)[-1]["result"] == "refused"


@pytest.mark.parametrize(("age_s", "holder_alive"), [(11 * 60, True), (5, False)])
def test_stale_restart_lock_is_replaced(ctl, monkeypatch, capsys, age_s, holder_alive):
    ctl.run.mkdir(parents=True)
    lock = ctl.run / "restart.lock"
    stamp = time.time() - age_s
    lock.write_text(json.dumps({"pid": 4242, "ts": stamp, "by": "cursor"}), encoding="utf-8")
    if holder_alive:
        ctl.psutil.procs[4242] = FakeProc(4242, ["python", "scripts/agent_ctl.py"], stamp - 1)
    _lock(monkeypatch, {"locked": False})
    calls: list = []
    _fake_restart_steps(monkeypatch, calls)

    code = agent_ctl.main(["restart", "--by", "claude", "--reason", "stale"])

    assert code == 0
    assert calls == [("stop", 20, False), ("start", "start", False)]
    assert "Removing a stale run/restart.lock" in capsys.readouterr().out
    assert not lock.exists()  # our own lock is released after the restart


# --------------------------------------------------------------------------- stop: pid checks and kill path


def test_stop_refuses_a_pid_that_is_not_the_agent(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": True})
    write_pid_json(ctl.run, 5555)
    ctl.psutil.procs[5555] = FakeProc(5555, ["C:/Windows/notepad.exe", "notes.txt"], time.time() - 60,
                                      name="notepad.exe")

    code = agent_ctl.main(["stop", "--by", "claude", "--reason", "t"])  # any process call would fail

    assert code == 4
    err = capsys.readouterr().err
    assert "pid 5555 is not a JARVIS agent worker" in err and "Nothing was killed" in err
    assert ctl.psutil.procs[5555].running
    assert audit_lines(ctl.logs)[-1]["result"] == "refused"


def test_stop_refuses_a_reused_pid(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": True})
    started = write_pid_json(ctl.run, 5555, started=datetime.now().astimezone() - timedelta(hours=1))
    ctl.psutil.procs[5555] = FakeProc(5555, WORKER_CMD, started.timestamp() + 600)  # born later

    assert agent_ctl.main(["stop", "--by", "cursor", "--reason", "t"]) == 4
    assert "the pid was reused" in capsys.readouterr().err


def test_stop_refuses_when_pid_json_is_missing(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": True})
    assert agent_ctl.main(["stop", "--by", "user"]) == 4
    assert "agent.pid.json is missing" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+Break helper and taskkill are Windows paths")
@pytest.mark.parametrize("force", [False, True])
def test_stop_taskkills_only_the_agent_processes_one_pid_at_a_time(ctl, monkeypatch, capsys, force):
    started = write_pid_json(ctl.run, 31500)
    worker, child, launcher, shell = agent_tree(started.timestamp() - 1)
    notepad, browser = user_apps(worker, started.timestamp())
    for proc in (worker, child, launcher, shell, notepad, browser):
        ctl.psutil.procs[proc.pid] = proc
    state = {"locked": True}
    _lock(monkeypatch, state)
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "taskkill":
            ctl.psutil.procs[int(cmd[2])].running = False
            state["locked"] = worker.running
        return ok(cmd)  # the Ctrl+Break is "sent" but this worker ignores it

    monkeypatch.setattr(agent_ctl, "_run", fake_run)

    code = agent_ctl.main(["stop", "--by", "claude", "--reason", "C-09 stop", "--timeout", "5",
                           *(["--force"] if force else [])])

    assert code == 0
    if force:
        assert ctl.clock.slept == 0
    else:
        helper_cmd, helper_kwargs = calls.pop(0)
        assert helper_cmd == [sys.executable, "-c", agent_ctl.CTRL_BREAK_HELPER, "31500"]
        assert helper_kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
        assert ctl.clock.slept >= 5  # waited --timeout after Ctrl+Break
    # The worker, its multiprocessing child and its launcher, each without /T.
    assert [cmd for cmd, _kwargs in calls] == [["taskkill", "/PID", str(pid), "/F"] for pid in (31500, 31501, 14280)]
    assert notepad.running and browser.running  # the user's apps are never killed
    assert shell.running  # nor the cmd.exe window
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["graceful"], entry["killed"]) == ("stopped", False, [31500, 31501, 14280])
    assert "### ✅ 🔁 [claude] C-09 stop · Agent worker stop (agent_ctl)" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["stop", "restart"])
def test_ctrl_break_stop_leaves_the_apps_jarvis_opened_and_does_not_wait_for_them(ctl, monkeypatch, capsys,
                                                                                   command):
    started = write_pid_json(ctl.run, 31500)
    worker, child, launcher, shell = agent_tree(started.timestamp() - 1)
    notepad, browser = user_apps(worker, started.timestamp())
    for proc in (worker, child, launcher, shell, notepad, browser):
        ctl.psutil.procs[proc.pid] = proc
    state = {"locked": True}
    _lock(monkeypatch, state)
    signalled: list = []

    def ctrl_break(pid):  # reaches the processes on the worker's console only
        signalled.append(pid)
        for proc in (worker, child, launcher):
            proc.running = False
        state["locked"] = False
        return True

    def start(*_args, **_kwargs):
        return 0, {"result": "started", "new_pid": 222, "health": 200, "start_s": 1.0, "ensure_room": 0}

    monkeypatch.setattr(agent_ctl, "send_ctrl_break", ctrl_break)
    monkeypatch.setattr(agent_ctl, "start_worker", start)

    code = agent_ctl.main([command, "--by", "user", "--reason", "graceful"])  # any taskkill would fail

    assert code == 0 and signalled == [31500]
    assert notepad.running and browser.running and shell.running
    assert ctl.clock.slept == 0  # no wait for apps that never get the Ctrl+Break
    assert "stopped (Ctrl+Break, 0.0 s)" in capsys.readouterr().out
    entry = audit_lines(ctl.logs)[-1]
    stop = entry if command == "stop" else entry["stop"]
    assert (stop["graceful"], stop["killed"]) == (True, [])


def test_stop_without_a_worker_is_a_no_op(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": False})
    assert agent_ctl.main(["stop", "--by", "cursor", "--reason", "nothing"]) == 0
    out = capsys.readouterr().out
    assert "No agent worker is running" in out and "done_highlight" not in out
    assert audit_lines(ctl.logs)[-1]["result"] == "not_running"


def test_agent_cmdline_and_tree_rules(ctl):
    assert agent_ctl.is_agent_cmdline(WORKER_CMD)
    assert agent_ctl.is_agent_cmdline(["python", "C:\\repo\\src\\core\\agent.py", "dev"])
    assert agent_ctl.is_agent_cmdline(["python", "-m", "src.core.agent_runner"])
    assert not agent_ctl.is_agent_cmdline(["python", "scripts/agent_ctl.py", "stop", "--by", "claude"])
    assert not agent_ctl.is_agent_cmdline(["C:/Windows/notepad.exe"])
    # A worker run straight from the window: the cmd.exe parent mentions src.core.agent but is no launcher.
    shell = FakeProc(1, ["cmd", "/k", "python -m src.core.agent start"], 0.0, name="cmd.exe")
    worker = FakeProc(2, WORKER_CMD, 1.0, parent=shell)
    assert agent_ctl._process_tree(worker) == [worker]


def test_the_agent_tree_follows_agent_processes_only(ctl):
    spawn = ["C:/Python312/python.exe", "-c", "from multiprocessing.spawn import spawn_main; "
             "spawn_main(parent_pid=10, pipe_handle=4)", "--multiprocessing-fork"]
    inference = FakeProc(12, spawn, 3.0)
    notepad = FakeProc(13, ["C:/Windows/System32/notepad.exe", "notes.txt"], 4.0, name="notepad.exe")
    server = FakeProc(11, spawn, 2.0, children=(inference, notepad))  # dev mode's worker child
    pool = FakeProc(15, spawn, 6.0)
    script = FakeProc(14, ["python.exe", "C:/Users/x/Desktop/user_script.py"], 5.0, children=(pool,))
    window = FakeProc(16, ["cmd.exe", "/k", "python -m src.core.agent dev"], 7.0, name="cmd.exe")
    worker = FakeProc(10, WORKER_CMD, 1.0, children=(server, script, window))

    tree = agent_ctl._process_tree(worker)

    assert tree[0] is worker  # killed first: it holds run/agent.lock
    assert sorted(proc.pid for proc in tree) == [10, 11, 12]


# --------------------------------------------------------------------------- start


class Holder:
    """A subprocess holding the worker lock in run_dir until its stdin closes."""

    CODE = ("import os, sys\nfrom src.core import agent_lock\nagent_lock.acquire_or_exit(force=True)\n"
            "print('LOCKED', os.getpid(), flush=True)\nsys.stdin.readline()\n")

    def __init__(self, run_dir: Path) -> None:
        env = os.environ.copy()
        for name in ("PYTEST_CURRENT_TEST", "JARVIS_NO_LOCK"):
            env.pop(name, None)
        env.update(JARVIS_RUN_DIR=str(run_dir), JARVIS_STARTED_BY="pytest-holder", JARVIS_START_REASON="C-09")
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with  # lives until stop()
            [sys.executable, "-c", self.CODE], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.proc.stdout.readline()), daemon=True).start()
        try:
            line = lines.get(timeout=60).strip()
        except queue.Empty:
            line = ""
        if not line.startswith("LOCKED "):
            self.stop()
            raise AssertionError(f"holder did not lock: {line!r}")
        self.pid = int(line.split()[1])

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()  # only this test's own child
                self.proc.wait()


@pytest.mark.parametrize("extra", [[], ["--foreground"]])
def test_start_refuses_with_exit_3_while_a_worker_holds_the_lock(ctl, capsys, extra):
    holder = Holder(ctl.run)
    try:
        code = agent_ctl.main(["start", "--by", "claude", "--reason", "second", *extra])
    finally:
        holder.stop()

    assert code == agent_lock.EXIT_ALREADY_RUNNING == 3
    info = agent_lock.read_pid_info()
    assert info is not None
    assert (f"Agent worker already running (pid {holder.pid}, by pytest-holder, since {info['started_at']}). "
            "Not starting a second one.") in capsys.readouterr().err
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["running_pid"]) == ("already_running", holder.pid)


def test_start_runs_a_hidden_worker_waits_for_health_then_ensures_the_room(ctl, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_NO_LOCK", "1")  # must not reach the worker
    state = {"locked": False, "probes": 0}
    _lock(monkeypatch, state)
    spawned: dict = {}

    class FakePopen:
        pid = 777
        returncode = None

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        spawned.update(kwargs, cmd=cmd)
        state["locked"] = True
        write_pid_json(ctl.run, 31999, reason="C-09 test")
        return FakePopen()

    def fake_probe(timeout=2.0):
        if not state["locked"]:
            return None
        state["probes"] += 1
        return 200 if state["probes"] >= 3 else None

    runs: list = []
    monkeypatch.setattr(agent_ctl, "_popen", fake_popen)
    monkeypatch.setattr(agent_ctl, "probe_health", fake_probe)
    monkeypatch.setattr(agent_ctl, "_run", lambda cmd, **kwargs: runs.append((cmd, kwargs)) or ok(cmd))

    code = agent_ctl.main(["start", "--by", "claude", "--reason", "C-09 test"])

    assert code == 0
    assert spawned["cmd"][1:] == ["-m", "src.core.agent", "start"]
    assert Path(spawned["cmd"][0]).name.lower().startswith("python")
    env = spawned["env"]
    assert env["JARVIS_STARTED_BY"] == "claude" and env["JARVIS_START_REASON"] == "C-09 test"
    assert env["PYTHONPATH"] == str(ROOT) and env["PYTHONIOENCODING"] == "utf-8"
    assert env["JARVIS_HYBRID"] == "1"
    assert "JARVIS_NO_LOCK" not in env and "PYTEST_CURRENT_TEST" not in env
    assert spawned["cwd"] == ROOT and spawned["stdin"] is subprocess.DEVNULL
    assert Path(spawned["stdout"].name) == ctl.logs / "agent_stdout.log"
    assert Path(spawned["stderr"].name) == ctl.logs / "agent_stderr.log"
    if sys.platform == "win32":
        flags = spawned["creationflags"]
        assert flags & subprocess.CREATE_NEW_PROCESS_GROUP and flags & subprocess.CREATE_NO_WINDOW
    for name in ("agent_stdout.log", "agent_stderr.log"):
        assert "===== agent_ctl start " in (ctl.logs / name).read_text(encoding="utf-8")
    assert [cmd[-1] for cmd, _kwargs in runs] == [str(agent_ctl.ENSURE_ROOM)]  # once, after health 200
    assert ctl.clock.slept >= agent_ctl.REGISTER_SETTLE_S
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["new_pid"], entry["health"], entry["ensure_room"]) == \
        ("start", "started", 31999, 200, 0)
    out = capsys.readouterr().out
    assert "Worker pid 31999 healthy (200)" in out
    assert "### ✅ 🔁 [claude] C-09 test · Agent worker start (agent_ctl) · " in out


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are a Windows feature")
def test_start_falls_back_when_the_job_forbids_breakaway(ctl, monkeypatch):
    flags_seen: list = []

    def fake_popen(cmd, **kwargs):
        flags_seen.append(kwargs["creationflags"])
        if kwargs["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")
        return types.SimpleNamespace(pid=1)

    monkeypatch.setattr(agent_ctl, "_popen", fake_popen)
    agent_ctl._spawn_background(["python"], {}, None, None)
    assert len(flags_seen) == 2 and not flags_seen[1] & subprocess.CREATE_BREAKAWAY_FROM_JOB


def test_failed_start_reports_the_worker_exit(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": False})
    monkeypatch.setattr(agent_ctl, "_popen",
                        lambda cmd, **kwargs: types.SimpleNamespace(pid=5, returncode=1, poll=lambda: 1))

    assert agent_ctl.main(["start", "--by", "cursor", "--reason", "broken"]) == 1
    captured = capsys.readouterr()
    assert "the worker exited with code 1" in captured.err
    assert "### 🔴 [cursor] broken · Agent worker start (agent_ctl)" in captured.out
    assert audit_lines(ctl.logs)[-1]["result"] == "failed"


PORT_TAKEN = "port 8081 already answers (pid 999, node.exe) but no worker holds run/agent.lock"


def test_start_refuses_when_the_port_answers_without_a_lock_holder(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": False})
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200)
    listener_on_8081(ctl)

    assert agent_ctl.main(["start", "--by", "cursor"]) == 4
    err = capsys.readouterr().err
    assert PORT_TAKEN in err
    assert "Its command line: node.exe C:/work/other-app/server.js --port 8081 --api-key ***" in err
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["error"]) == ("refused", PORT_TAKEN)
    assert "fake-key-value" not in (ctl.logs / "agent_ctl.log").read_text(encoding="utf-8")


def test_restart_highlight_names_only_the_pid_and_process_name_of_a_port_listener(ctl, monkeypatch, capsys):
    """done_highlight.md is tracked in a public repo: no other app's command line goes into its entry."""
    _lock(monkeypatch, {"locked": False})
    monkeypatch.setattr(agent_ctl, "stop_worker", lambda timeout, force=False: (0, {"result": "not_running"}))
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200)
    listener_on_8081(ctl)

    assert agent_ctl.main(["restart", "--by", "claude", "--reason", "C-16"]) == 4

    captured = capsys.readouterr()
    highlight = captured.out[captured.out.index("done_highlight.md entry"):]
    assert f"`agent_ctl restart` nakam: start: {PORT_TAKEN}." in highlight
    assert "server.js" not in highlight and "--api-key" not in highlight
    assert "Its command line: node.exe C:/work/other-app/server.js" in captured.err  # console only
    entry = audit_lines(ctl.logs)[-1]
    assert entry["start"]["listener_cmdline"].endswith("--api-key ***")
    assert "fake-key-value" not in (ctl.logs / "agent_ctl.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("case", [
    (0, 0, "Worker exited with code 0"),
    (agent_ctl.STATUS_CONTROL_C_EXIT, 0, "Worker stopped by Ctrl+C / Ctrl+Break"),
    (1, 1, "Worker exited with code 1"),
])
def test_foreground_start_runs_in_this_console_and_returns_the_worker_exit_code(ctl, monkeypatch, capsys, case):
    worker_code, expected, text = case
    state = {"locked": False}
    _lock(monkeypatch, state)
    original_sigint = signal.getsignal(signal.SIGINT)
    spawned: dict = {}

    class FakeWorker:
        pid = 888

        def __init__(self) -> None:
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits < 8:
                ctl.clock.sleep(timeout)
                raise subprocess.TimeoutExpired("agent", timeout)
            return worker_code

    def fake_popen(cmd, **kwargs):
        spawned.update(kwargs, cmd=cmd)
        state["locked"] = True
        write_pid_json(ctl.run, 31777, mode="dev")
        return FakeWorker()

    monkeypatch.setattr(agent_ctl, "_popen", fake_popen)
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200 if state["locked"] else None)

    code = agent_ctl.main(["start", "--by", "claude", "--reason", "fg", "--foreground", "--mode", "dev"])

    assert code == expected
    assert spawned["cmd"][1:] == ["-m", "src.core.agent", "dev"]
    assert "creationflags" not in spawned and "stdout" not in spawned  # same console, same output
    assert spawned["env"]["JARVIS_STARTED_BY"] == "claude"
    entries = audit_lines(ctl.logs)
    assert [e["result"] for e in entries] == ["spawned", "healthy", "exited"]
    assert entries[-1]["exit"] == worker_code
    out = capsys.readouterr().out
    assert out.count("Agent worker start (agent_ctl)") == 1 and text in out
    assert signal.getsignal(signal.SIGINT) is original_sigint


# --------------------------------------------------------------------------- wait


def test_wait_returns_0_once_health_is_200(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": True})
    answers = iter([None, None, 200])
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: next(answers))
    write_pid_json(ctl.run, 4321)

    assert agent_ctl.main(["wait", "--timeout", "60"]) == 0
    assert ctl.clock.slept == 2 + agent_ctl.REGISTER_SETTLE_S  # two polls, then the settle time
    assert "Worker pid 4321 is healthy (200 on http://127.0.0.1:8081/)" in capsys.readouterr().out
    assert audit_lines(ctl.logs)[-1]["result"] == "healthy"


def test_wait_on_a_running_healthy_worker_does_not_sleep(ctl, monkeypatch):
    _lock(monkeypatch, {"locked": True})
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200)
    assert agent_ctl.main(["wait"]) == 0
    assert ctl.clock.slept == 0


def test_wait_fails_fast_when_the_worker_exits(ctl, monkeypatch, capsys):
    locks = iter([True, True, False])
    monkeypatch.setattr(agent_lock, "is_locked", lambda: next(locks, False))

    assert agent_ctl.main(["wait", "--timeout", "60"]) == 1
    assert "the worker exited (run/agent.lock was released)" in capsys.readouterr().err
    assert ctl.clock.slept < 5


def test_wait_gives_up_when_no_worker_takes_the_lock(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": False})
    assert agent_ctl.main(["wait", "--timeout", "60"]) == 1
    assert f"no worker took run/agent.lock within {agent_ctl.LOCK_GRACE_S} s" in capsys.readouterr().err
    assert ctl.clock.slept < 30


def test_wait_does_not_trust_a_200_without_the_worker_lock(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": False})
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200)
    write_pid_json(ctl.run, 4321)  # stale pid.json from an earlier worker
    assert agent_ctl.main(["wait", "--timeout", "60"]) == 1
    assert "port 8081 answers but no worker holds run/agent.lock" in capsys.readouterr().err


def test_wait_times_out_while_health_is_not_200(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": True})
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 503)
    assert agent_ctl.main(["wait", "--timeout", "30"]) == 1
    assert "no health 200 within 30 s (last: 503)" in capsys.readouterr().err


# --------------------------------------------------------------------------- status and logs


def test_status_shows_pid_by_reason_uptime_and_health(ctl, monkeypatch, capsys):
    started = write_pid_json(ctl.run, 31500, by="cursor", reason="OP-06",
                             started=datetime.now().astimezone().replace(microsecond=0) - timedelta(seconds=3725))
    worker, _, launcher, _ = agent_tree(started.timestamp() - 1)
    ctl.psutil.procs.update({worker.pid: worker, launcher.pid: launcher})
    _lock(monkeypatch, {"locked": True})
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200)
    monkeypatch.setattr(agent_ctl, "worker_info", lambda: {"active_jobs": 1, "worker_load": 0.25})

    assert agent_ctl.main(["status"]) == 0
    out = capsys.readouterr().out
    for text in ("JARVIS agent worker: RUNNING", "pid      31500 (launcher 14280)", "by       cursor",
                 "reason   OP-06", "mode     start", f"since    {started.isoformat()} (uptime 1:02:",
                 "health   200 (http://127.0.0.1:8081/)", "jobs     1 active (load 0.25)", "git      abc1234"):
        assert text in out


def test_status_of_a_manual_worker_and_of_no_worker(ctl, monkeypatch, capsys):
    write_pid_json(ctl.run, 31500, by="manual", reason="")
    state = {"locked": False}
    _lock(monkeypatch, state)
    assert agent_ctl.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "NOT RUNNING" in out and "last     pid 31500, by manual" in out and "health   no answer" in out

    state["locked"] = True
    assert agent_ctl.main(["status"]) == 0  # its process is unknown here: still running per the lock
    out = capsys.readouterr().out
    assert "by       manual" in out and "reason   -" in out and "WARNING  pid 31500" in out


def test_logs_prints_the_last_lines_of_each_log(ctl, monkeypatch, capsys):
    _lock(monkeypatch, {"locked": False})
    ctl.logs.mkdir()
    (ctl.logs / "agent_stderr.log").write_text("".join(f"line {n}\n" for n in range(1, 101)), encoding="utf-8")
    (ctl.logs / "agent_ctl.log").write_text('{"action": "start"}\n', encoding="utf-8")

    assert agent_ctl.main(["logs", "-n", "5"]) == 0
    out = capsys.readouterr().out
    assert "===== logs/agent_stdout.log (last 5 lines) =====\n(no such file)" in out
    assert "line 96\nline 97\nline 98\nline 99\nline 100" in out and "line 95\n" not in out
    assert '{"action": "start"}' in out
    assert agent_ctl.tail_lines(ctl.logs / "agent_stderr.log", 3, block=7) == ["line 98", "line 99", "line 100"]


def test_failed_probe_of_a_closed_local_port_leaves_no_frame_cycles(monkeypatch):
    """Cycles from httpx errors kept a background start's Popen alive until exit (WinError 6 there)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("JARVIS_WORKER_HOST", "0.0.0.0")  # probed on 127.0.0.1
    monkeypatch.setenv("JARVIS_WORKER_PORT", str(port))
    assert agent_ctl.health_url() == f"http://127.0.0.1:{port}/"
    gc.collect()
    gc.disable()
    try:
        assert agent_ctl.probe_health(timeout=0.5) is None
        gc.set_debug(gc.DEBUG_SAVEALL)
        gc.collect()
        leaked = [type(obj).__name__ for obj in gc.garbage if isinstance(obj, BaseException)]
    finally:
        gc.set_debug(0)
        gc.garbage.clear()
        gc.enable()
    assert not leaked


def test_status_wait_and_logs_need_no_by_but_actions_do():
    with pytest.raises(SystemExit) as missing:
        agent_ctl.parse_args(["restart", "--reason", "x"])
    assert missing.value.code == 2
    with pytest.raises(SystemExit):
        agent_ctl.parse_args(["start", "--by", "someone"])
    assert agent_ctl.parse_args(["stop", "--by", "user"]).timeout == 20
    assert agent_ctl.parse_args(["wait"]).timeout == 60
    assert agent_ctl.parse_args(["logs"]).lines == 50


# --------------------------------------------------------------------------- root .env as the single source


def test_root_env_wins_and_the_hybrid_env_only_fills_missing_keys(monkeypatch, tmp_path):
    root_env = tmp_path / ".env"
    root_env.write_text("C09_A=root\nC09_B=root\n", encoding="utf-8")
    hybrid_env = tmp_path / "hybrid.env"
    hybrid_env.write_text("C09_A=hybrid\nC09_C=hybrid\nC09_D=hybrid\n", encoding="utf-8")
    for key in ("C09_A", "C09_B", "C09_C"):
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)  # recorded, so the values loaded below are removed after the test
    monkeypatch.setenv("C09_D", "process")
    monkeypatch.setattr(jarvis_config, "_ENV_FILES", (str(root_env), str(hybrid_env), str(tmp_path / "none")))

    jarvis_config._load_env_files()

    assert [os.environ[k] for k in ("C09_A", "C09_B", "C09_C", "C09_D")] == ["root", "root", "hybrid", "process"]
    assert root_env.read_text(encoding="utf-8") == "C09_A=root\nC09_B=root\n"  # nothing written back


def test_default_env_files_are_root_then_windows_local():
    expected = [ROOT / ".env", WINDOWS_LOCAL / ".env"]
    assert [Path(p) for p in jarvis_config._ENV_FILES] == expected
    assert [Path(p) for p in agent_ctl.ENV_FILES] == expected


# --------------------------------------------------------------------------- launchers (C-09 steps 6-7, C-10)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_launchers_never_copy_the_hybrid_env_over_the_root_env():
    start_bat = _text(WINDOWS_LOCAL / "start-agent.bat")
    setup_bat = _text(WINDOWS_LOCAL / "setup.bat")
    for text in (start_bat, setup_bat):
        assert 'copy /Y "deploy\\windows-local\\.env" ".env"' not in text
        assert 'if not exist ".env"' in text or 'if exist ".env"' in text  # copied only when missing
    hybrid = _text(WINDOWS_LOCAL / "start_hybrid_agent.py")
    assert "shutil" not in hybrid and "copyfile" not in hybrid
    assert "override=True" not in _text(WINDOWS_LOCAL / "ensure_room.py")


def test_start_agent_bat_goes_through_agent_ctl_and_waits_for_health():
    text = _text(WINDOWS_LOCAL / "start-agent.bat")
    assert ('start "JARVIS-Agent-Worker" cmd /k .venv_312\\Scripts\\python.exe scripts\\agent_ctl.py start '
            "--by user --reason start-agent.bat --foreground") in text
    assert "python scripts\\agent_ctl.py status" in text and "exit /b 3" in text
    wait_at = text.index("agent_ctl.py wait --timeout 60")
    assert wait_at < text.index("python deploy\\windows-local\\ensure_room.py")
    assert "timeout /t" not in text and "-m src.core.agent" not in text


def test_start_agent_bat_takes_livekit_and_shared_dir_settings_from_the_root_env():
    """A value set in the bat would override a root .env edit (K-47); clearing also drops a stale shell value."""
    text = _text(WINDOWS_LOCAL / "start-agent.bat")
    for key in ("LIVEKIT_URL", "LIVEKIT_PUBLIC_URL", "JARVIS_SHARED_DIR"):
        assert re.findall(rf'(?im)^\s*set\s+"?{key}=([^"\r\n]*)', text) == [""], key


def test_other_launchers_use_agent_ctl():
    assert "scripts\\agent_ctl.py start --by user --reason dev.bat --mode dev --foreground" in _text(ROOT / "dev.bat")
    assert "src\\core\\agent.py" not in _text(ROOT / "dev.bat")
    assert "scripts\\agent_ctl.py start --by user --reason prod.bat" in _text(ROOT / "prod.bat")
    assert "agent_ctl.py start --by user" in _text(ROOT / "install_production.bat")
    assert "agent_ctl.py start --by user" in _text(ROOT / "install_production.sh")
    hybrid = _text(WINDOWS_LOCAL / "start_hybrid_agent.py")
    assert '"start", "--by", "user"' in hybrid and '"--foreground"' in hybrid
    setup_bat = _text(WINDOWS_LOCAL / "setup.bat")
    assert "agent_lock.is_locked()" in setup_bat and "Reusing the existing Python 3.12 .venv_312" in setup_bat


def test_no_launcher_prints_the_dead_5100_url():
    for path in (WINDOWS_LOCAL / "start-agent.bat", WINDOWS_LOCAL / "start_hybrid_agent.py",
                 WINDOWS_LOCAL / "setup.bat"):
        text = _text(path)
        assert "5100" not in text
        assert "https://jarvis.srv1802669.hstgr.cloud" in text
