"""The one safe way to start, stop and restart the PC agent worker (plan task C-09, contract K-53).

Usage (Git Bash in the repo root, PY=.venv_312/Scripts/python.exe):
    $PY scripts/agent_ctl.py status
    $PY scripts/agent_ctl.py start   --by claude|cursor|user [--reason WHY] [--mode start|dev] [--foreground]
    $PY scripts/agent_ctl.py stop    --by claude|cursor|user [--reason WHY] [--timeout 20] [--force]
    $PY scripts/agent_ctl.py restart --by claude|cursor|user [--reason WHY] [--mode start|dev] [--force]
    $PY scripts/agent_ctl.py wait    [--timeout 60]
    $PY scripts/agent_ctl.py logs    [-n 50] [--file all|stdout|stderr|ctl]

The worker holds run/agent.lock for its whole life (src/core/agent_lock.py): the lock is the source of
truth, run/agent.pid.json only describes the holder and stays behind after it exits. Since C-136 it also
writes run/agent_link.json, the state of its LiveKit link (src/core/livekit_link.py): connecting,
connected, retrying or failed. /health stays 200 while that link retries, so 'ready' means health 200
and a connected link; a worker from before C-136 writes no such file and is ready on health 200 alone.

start    Exit 3 while a worker holds the lock. Rotates logs/agent_stdout.log and logs/agent_stderr.log
         (5 MB, keeps .1-.5) and runs '.venv_312 python -m src.core.agent <mode>' with JARVIS_STARTED_BY,
         JARVIS_START_REASON, PYTHONPATH, PYTHONIOENCODING and JARVIS_HYBRID=1. Default: hidden, output
         appended to those logs, waits until the worker is ready (health 200 on 127.0.0.1:8081 and its
         LiveKit link connected, max 60 s), then runs deploy/windows-local/ensure_room.py once.
         --foreground: in this console until the worker exits.
stop     Only the pid in agent.pid.json, and only when its command line runs src.core.agent: Ctrl+Break,
         then after --timeout 'taskkill /PID <pid> /F' for the worker, its agent children and its launcher
         (no /T: apps that JARVIS opened for the user are its children too). --force skips the Ctrl+Break.
restart  Takes run/restart.lock (fails fast; stale after 10 min or once its holder has exited), refuses
         within 120 s of the last successful restart unless --force (logged), then stop + start. The worker
         itself runs 'restart --by worker --expect-pid P --expect-started T' when its LiveKit link stays lost
         while LiveKit answers (C-136); that request is refused (exit 4) once worker P no longer holds the
         lock or its link is connected again, and it never starts a worker that someone else stopped.
wait     Waits until the worker is ready, as start does (start-agent.bat runs it before ensure_room.py);
         fails at once when the worker reports that its LiveKit link failed.
status   pid, by, reason, mode, uptime, health code, LiveKit link state and its age, active jobs; exit 0
         when a worker runs, else 1.
logs     Tails of the agent logs and of logs/agent_ctl.log.

start/stop/restart/wait append a JSON line to logs/agent_ctl.log; start/stop/restart by claude or cursor
also print a ready-to-paste done_highlight.md entry (K-53: log every restart there).
Exit codes: 0 ok, 1 failed, 2 usage, 3 a worker already runs (start), 4 refused for safety (restart
cooldown, a restart in progress, a pid that is not the agent, port 8081 answered by another process, a
worker's recovery request that no longer applies).
"""
# pylint: disable=too-many-lines  # one self-contained CLI; the six commands share these helpers
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import agent_lock, livekit_link  # noqa: E402  pylint: disable=wrong-import-position

LOG_DIR = ROOT / "logs"
AGENT_LOG_NAMES = ("agent_stdout.log", "agent_stderr.log")
CTL_LOG_NAME = "agent_ctl.log"
ROTATE_BYTES = 5 * 1024 * 1024
ROTATE_KEEP = 5
ENSURE_ROOM = ROOT / "deploy" / "windows-local" / "ensure_room.py"
# Same order as services/utils/jarvis_config.py (K-47): the root .env wins, the hybrid file fills gaps.
ENV_FILES = (ROOT / ".env", ROOT / "deploy" / "windows-local" / ".env")

# 'worker': the worker's own LiveKit link recovery (C-136, src/core/livekit_link.py) runs 'restart'.
WHO = ("claude", "cursor", "user", "worker")
MODES = ("start", "dev")
LOG_CHOICES = {"all": (*AGENT_LOG_NAMES, CTL_LOG_NAME), "stdout": AGENT_LOG_NAMES[:1],
               "stderr": AGENT_LOG_NAMES[1:], "ctl": (CTL_LOG_NAME,)}
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ALREADY_RUNNING = agent_lock.EXIT_ALREADY_RUNNING
EXIT_REFUSED = 4

START_TIMEOUT_S = 60
STOP_TIMEOUT_S = 20
KILL_WAIT_S = 10
RESTART_COOLDOWN_S = 120
RESTART_LOCK_STALE_S = 600
ENSURE_ROOM_TIMEOUT_S = 120
# A start that has not taken run/agent.lock this long after 'wait' began has failed.
LOCK_GRACE_S = 20
# livekit-agents 1.4.6 answers '/' with 200 as soon as its HTTP server listens, a moment before the
# worker has registered with LiveKit, so a fresh start gets this long before the room is dispatched.
REGISTER_SETTLE_S = 5
# pid.json's started_at has whole seconds and is written just after the process starts.
PID_START_SLACK_S = 5
AGENT_MARKERS = ("src.core.agent", "src/core/agent.py")
MP_MARKERS = ("--multiprocessing-fork", "from multiprocessing.")
PKT = timezone(timedelta(hours=5), "PKT")

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)

# Ctrl+Break only reaches processes on the sender's console, so this helper attaches to the worker's
# console (hidden in background mode) and signals every process there. Its own handler must be set after
# AttachConsole (signal.SIG_IGN does not stop CTRL_BREAK_EVENT). Python's default SIGBREAK action ends
# the worker at once; the venv launcher and cmd.exe ignore it.
CTRL_BREAK_HELPER = (
    "import ctypes, sys, time\n"
    "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "kernel32.FreeConsole()\n"
    "if not kernel32.AttachConsole(int(sys.argv[1])):\n"
    "    sys.exit(2)\n"
    "handled = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(lambda _event: 1)\n"
    "kernel32.SetConsoleCtrlHandler(handled, 1)\n"
    "sent = kernel32.GenerateConsoleCtrlEvent(1, 0)\n"
    "time.sleep(0.5)\n"
    "sys.exit(0 if sent else 3)\n"
)
# Exit status of a process ended by Ctrl+C / Ctrl+Break.
STATUS_CONTROL_C_EXIT = 0xC000013A

# Indirections that the tests replace: no test may start, signal or kill a real process.
_monotonic = time.monotonic
_sleep = time.sleep


def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(cmd, check=False, **kwargs)


def _popen(cmd: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    return subprocess.Popen(cmd, **kwargs)  # pylint: disable=consider-using-with  # outlives this call


# --------------------------------------------------------------------------- settings and probes


def _env_setting(name: str) -> str | None:
    """Process environment first, then the root .env, then deploy/windows-local/.env (K-47)."""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        from dotenv import dotenv_values  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    for path in ENV_FILES:
        if path.is_file():
            value = (dotenv_values(path).get(name) or "").strip()
            if value:
                return value
    return None


def worker_address() -> tuple[str, int]:
    """Host and port of the worker's health server (JARVIS_WORKER_HOST/PORT, default 127.0.0.1:8081)."""
    host = _env_setting("JARVIS_WORKER_HOST") or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    try:
        port = int(_env_setting("JARVIS_WORKER_PORT") or "8081")
    except ValueError:
        port = 8081
    return host, port


def health_url() -> str:
    host, port = worker_address()
    return f"http://{host}:{port}/"


def _timeout(seconds: float) -> httpx.Timeout:
    # Windows takes ~2 s to refuse a loopback connect, while a listening port connects at once.
    return httpx.Timeout(seconds, connect=min(seconds, 0.5))


def _drop_frames(exc: BaseException | None) -> None:
    """A failed httpx request leaves exception/frame cycles that keep the callers' frames, and so a
    background worker's Popen, alive until the garbage collector; it may close the Popen's handle
    first, and Popen.__del__ then fails with WinError 6. Clearing the frames breaks the cycles now."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        traceback.clear_frames(exc.__traceback__)
        exc = exc.__cause__ or exc.__context__


def probe_health(timeout: float = 2.0) -> int | None:
    """HTTP status of the worker's health route; None when nothing answers."""
    try:
        return httpx.get(health_url(), timeout=_timeout(timeout), trust_env=False).status_code
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        _drop_frames(exc)
        return None


def worker_info() -> dict[str, Any] | None:
    """livekit-agents' /worker document (active_jobs, worker_load, ...); None when unavailable."""
    try:
        response = httpx.get(health_url() + "worker", timeout=_timeout(2.0), trust_env=False)
        data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        _drop_frames(exc)
        return None
    return data if isinstance(data, dict) else None


def _listening_pid(port: int) -> int | None:
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                return conn.pid
    except (psutil.Error, OSError):
        return None
    return None


# --------------------------------------------------------------------------- the worker process


def is_agent_cmdline(cmdline: list[str]) -> bool:
    joined = " ".join(cmdline).replace("\\", "/")
    return any(marker in joined for marker in AGENT_MARKERS)


def _started_epoch(info: dict[str, Any] | None) -> float | None:
    try:
        return datetime.fromisoformat(str((info or {}).get("started_at"))).timestamp()
    except (TypeError, ValueError):
        return None


def worker_process(info: dict[str, Any] | None) -> tuple[psutil.Process | None, str]:
    """The live process that agent.pid.json names, or None and the reason it is not trusted."""
    pid = (info or {}).get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None, "run/agent.pid.json is missing or has no pid"
    try:
        proc = psutil.Process(pid)
        cmdline = proc.cmdline()
        created = proc.create_time()
    except psutil.NoSuchProcess:
        return None, f"pid {pid} from run/agent.pid.json is not running"
    except psutil.Error as exc:
        return None, f"pid {pid}: cannot read its command line ({type(exc).__name__})"
    if not is_agent_cmdline(cmdline):
        return None, f"pid {pid} is not a JARVIS agent worker (no src.core.agent in its command line)"
    started = _started_epoch(info)
    if started is not None and created > started + PID_START_SLACK_S:
        return None, f"pid {pid} started after run/agent.pid.json was written (the pid was reused)"
    return proc, ""


def _is_agent_process(proc: psutil.Process) -> bool:
    """A python process that runs the agent, or a multiprocessing child of it (dev mode's worker, job or
    inference processes)."""
    try:
        cmdline = proc.cmdline() if proc.name().lower().startswith("python") else []
    except psutil.Error:
        return False
    return is_agent_cmdline(cmdline) or any(marker in " ".join(cmdline) for marker in MP_MARKERS)


def _launcher(proc: psutil.Process) -> psutil.Process | None:
    """The .venv_312 python.exe launcher that runs the worker interpreter as its child, if any."""
    try:
        parent = proc.parent()
    except psutil.Error:
        return None
    return parent if parent is not None and _is_agent_process(parent) else None


def _process_tree(proc: psutil.Process) -> list[psutil.Process]:
    """The worker, its descendants found through agent processes only, and its launcher. JARVIS tools
    open apps for the user (notepad, a browser) as children of the worker: never part of the agent."""
    tree, pending = [proc], [proc]
    while pending:
        try:
            agents = [child for child in pending.pop().children() if _is_agent_process(child)]
        except psutil.Error:
            continue
        tree += agents
        pending += agents
    launcher = _launcher(proc)
    return tree if launcher is None else [*tree, launcher]


def _alive(proc: psutil.Process) -> bool:
    try:
        return proc.is_running()
    except psutil.Error:
        return False


def _pid_running_since(pid: int, since: float) -> bool:
    try:
        return psutil.Process(pid).create_time() <= since + 1
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True  # cannot tell: treat as running


def _previous_mode(info: dict[str, Any] | None) -> str | None:
    argv = (info or {}).get("argv")
    if isinstance(argv, list) and argv and argv[-1] in MODES:
        return str(argv[-1])
    return None


def agent_python() -> Path:
    """.venv_312's python (a launcher on Windows: the worker runs as its child), else this python."""
    venv = ROOT / ".venv_312" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv.is_file():
        return venv
    print(f"[agent_ctl] {venv} not found; using {sys.executable}", file=sys.stderr)
    return Path(sys.executable)


def _registry_path_values() -> list[str]:
    """Machine and user PATH strings as stored in the registry (Windows only)."""
    if sys.platform != "win32":
        return []
    import winreg  # pylint: disable=import-outside-toplevel
    values = []
    for hive, sub_key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                values.append(str(winreg.QueryValueEx(key, "Path")[0]))
        except OSError:
            continue
    return values


def fresh_path(current: str, registry_values: list[str]) -> str:
    """current PATH plus registry PATH entries it lacks, so a worker started from an old shell still
    finds programs installed after that shell started (winget ffmpeg, npm -g pinchtab)."""
    parts = [p for p in current.split(os.pathsep) if p]
    seen = {os.path.normcase(os.path.normpath(p)) for p in parts}
    for value in registry_values:
        for part in value.split(";"):
            part = os.path.expandvars(part.strip().strip('"'))
            key = os.path.normcase(os.path.normpath(part)) if part else ""
            if part and key not in seen:
                seen.add(key)
                parts.append(part)
    return os.pathsep.join(parts)


def agent_env(by: str, reason: str, *, background: bool) -> dict[str, str]:
    env = dict(os.environ)
    # A worker started here always takes the lock.
    for name in ("JARVIS_NO_LOCK", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    if by != "worker":  # only the worker's own recovery hands its count on (C-136)
        env.pop(livekit_link.RECOVERIES_ENV, None)
    env["PATH"] = fresh_path(env.get("PATH", ""), _registry_path_values())
    env.update(JARVIS_STARTED_BY=by, JARVIS_START_REASON=reason, PYTHONPATH=str(ROOT),
               PYTHONIOENCODING="utf-8", JARVIS_HYBRID="1")
    if background:
        env["PYTHONUNBUFFERED"] = "1"
    return env


def _wait_until(condition: Callable[[], bool], timeout: float, step: float = 0.5) -> bool:
    deadline = _monotonic() + timeout
    while True:
        if condition():
            return True
        if _monotonic() >= deadline:
            return False
        _sleep(step)


def worker_link(info: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The LiveKit link state (C-136) that the worker of run/agent.pid.json reports in run/agent_link.json;
    None when unknown: a worker from before C-136 writes no such file, and an earlier worker's is ignored."""
    return livekit_link.read_link(agent_lock.read_pid_info() if info is None else info)


def describe_link(link: dict[str, Any] | None) -> str:
    """The 'link' line of status: the state and its age."""
    if link is None:
        return "unknown (no run/agent_link.json from this worker: it started before C-136)"
    since = link.get("since")
    age = str(timedelta(seconds=max(0, int(time.time() - since)))) if isinstance(since, (int, float)) else "?"
    state, detail = link["state"], _one_line(str(link.get("detail") or ""), 160)
    if state == livekit_link.CONNECTED:
        return f"connected for {age} (LiveKit worker {link.get('worker_id') or '?'})"
    if state == livekit_link.CONNECTING:
        return f"connecting for {age} (not registered with LiveKit yet)"
    never = "" if link.get("connected_once") else ", never connected"
    return f"{state} for {age} ({detail or 'no detail'}{never})"


def link_hint(link: dict[str, Any]) -> str:
    """What comes next for a worker whose LiveKit link is not connected (the WARNING line of status), by the
    rules of livekit_link.LinkMonitor.recovery_reason."""
    if link["state"] == livekit_link.CONNECTING:
        return "it registers within seconds once LiveKit answers; if this lasts, see logs/agent_stderr.log"
    if link.get("recovery") is False:
        return "JARVIS_LINK_RECOVERY=0, so it never restarts itself"
    if link.get("connected_once"):
        return ("it asks agent_ctl for a fresh worker once LiveKit answers again (after 10 min of retrying, "
                "at once after failed)")
    if livekit_link.self_recovers(link):
        try:
            pause = livekit_link.pause_s(int(link.get("recoveries") or 0)) / 60
        except (TypeError, ValueError):
            pause = livekit_link.REQUEST_EVERY_S / 60
        return ("it has not registered since it started; once LiveKit answers again it asks agent_ctl for a "
                f"fresh worker, at the earliest {pause:.0f} min after its start")
    return ("LiveKit or TLS turned it away and it never restarts itself for that: check LIVEKIT_URL and its "
            "keys, then restart it")


def _link_ready() -> bool:
    """True unless the running worker reports a LiveKit link that is not connected."""
    link = worker_link() if agent_lock.is_locked() else None
    return link is None or link["state"] == livekit_link.CONNECTED


def _gone(proc: subprocess.Popen[Any] | None, link: dict[str, Any] | None) -> str:
    """Why waiting is pointless: the worker started here exited, or its LiveKit link failed; else ''."""
    if proc is not None and proc.poll() is not None:
        if proc.returncode == EXIT_ALREADY_RUNNING:
            return "another worker already holds run/agent.lock"
        return f"the worker exited with code {proc.returncode}"
    if link is not None and link["state"] == livekit_link.FAILED:
        return f"the worker's LiveKit link failed ({_one_line(str(link.get('detail') or ''), 160)})"
    return ""


def wait_healthy(timeout: float, proc: subprocess.Popen[Any] | None = None, *,
                 progress: bool = False) -> tuple[bool, str]:
    """Wait until the worker is ready: health 200 while a worker holds run/agent.lock and, when it reports its
    LiveKit link in run/agent_link.json (C-136), that link connected; a 200 alone no longer counts. A worker
    without that file (started before C-136) is ready on 200 plus the register settle, as before. Gives up
    early when the worker exits, never takes run/agent.lock or reports that its link failed."""
    started = _monotonic()
    was_down = False
    seen_lock = False
    next_note = started + 10
    while True:
        code = probe_health()
        locked = agent_lock.is_locked()
        link = worker_link() if locked else None
        # A 200 without the lock is some other process on the port.
        if code == 200 and locked and (link is None or link["state"] == livekit_link.CONNECTED):
            if link is None and was_down:  # no link file: 200 comes a moment before the registration
                _sleep(REGISTER_SETTLE_S)
            return True, ""
        was_down = True
        why = _gone(proc, link)
        if why:
            return False, why
        now = _monotonic()
        if locked:
            seen_lock = True
        elif seen_lock:
            return False, "the worker exited (run/agent.lock was released)"
        elif now - started >= LOCK_GRACE_S:
            return False, ("port 8081 answers but no worker holds run/agent.lock" if code == 200
                           else f"no worker took run/agent.lock within {LOCK_GRACE_S} s")
        if now - started >= timeout:
            last = code if code is not None else "no answer"
            return False, (f"no health 200 within {timeout:g} s (last: {last})" if link is None else
                           f"no LiveKit link within {timeout:g} s (health {last}; link {describe_link(link)})")
        if progress and now >= next_note:
            state = f", link {link['state']}" if link is not None else ""
            print(f"[agent_ctl] still waiting for the worker ({now - started:.0f} s{state})...", flush=True)
            next_note += 10
        _sleep(1.0)


# --------------------------------------------------------------------------- logs


def rotate_log(path: Path, max_bytes: int | None = None, keep: int | None = None) -> bool:
    """Once path reaches max_bytes (5 MB): path.(keep-1) -> path.keep ... path -> path.1, keep 5.
    Touches no other file."""
    max_bytes = ROTATE_BYTES if max_bytes is None else max_bytes
    keep = ROTATE_KEEP if keep is None else keep
    try:
        if path.stat().st_size < max_bytes:
            return False
        for number in range(keep - 1, 0, -1):
            older = path.with_name(f"{path.name}.{number}")
            if older.exists():
                os.replace(older, path.with_name(f"{path.name}.{number + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except FileNotFoundError:
        return False
    except OSError as exc:
        print(f"[agent_ctl] Warning: could not rotate {path.name}: {exc}", file=sys.stderr)
        return False
    return True


def rotate_agent_logs() -> list[str]:
    return [name for name in AGENT_LOG_NAMES if rotate_log(LOG_DIR / name)]


def tail_lines(path: Path, count: int, block: int = 65536) -> list[str]:
    """The last count lines of a text file, read from the end."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= count:
            step = min(block, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-count:]


def _now() -> datetime:
    return datetime.now().astimezone()


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join((text or "").split())[:limit]


def audit(action: str, args: argparse.Namespace | None, **fields: Any) -> None:
    """One JSON line per action in logs/agent_ctl.log."""
    entry: dict[str, Any] = {"ts": _now().isoformat(timespec="seconds"), "action": action}
    for key in ("by", "reason", "mode", "foreground", "force", "expect_pid"):
        value = getattr(args, key, None)
        if value is not None:
            entry[key] = value
    entry.update(fields)
    entry["ctl_pid"] = os.getpid()
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / CTL_LOG_NAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        print(f"[agent_ctl] Warning: could not write logs/{CTL_LOG_NAME}: {exc}", file=sys.stderr)


_TITLES = {"start": "Agent worker start", "stop": "Agent worker stop", "restart": "Agent worker restart"}


def print_highlight(action: str, args: argparse.Namespace, ok: bool, summary: str) -> None:
    """A done_highlight.md entry (that file's template) for the agents; the user's launchers skip it."""
    if args.by not in ("claude", "cursor"):
        return
    now = datetime.now(PKT)
    status = "✅ 🔁" if ok else "🔴"
    task = _one_line(args.reason, 60) or "runtime"
    print()
    print("done_highlight.md entry (paste at the end of the file):")
    print(f"### {status} [{args.by}] {task} · {_TITLES[action]} (agent_ctl) · {now:%Y-%m-%d %H:%M} PKT")
    print(f"- **Kya kiya:** {summary}")
    print("- **Files:** —")
    verify = "`.venv_312/Scripts/python.exe scripts/agent_ctl.py status`"
    print(f"- **Verify:** {verify} → {'health 200' if ok else 'dekho upar wala error'}")
    print(f"- **Commit:** abhi nahi · **Deploy:** PC agent {action} {now:%H:%M} PKT")
    print("- **Doosre agent ke liye:** —")


# --------------------------------------------------------------------------- start


def _port_problem() -> tuple[str, str]:
    """('', '') when nothing answers on the worker port (the lock is free here). Else the error, naming only
    the listener's pid and process name (restart copies it into a done_highlight.md entry, a tracked
    file), and its command line with secret options masked, for the console and logs/agent_ctl.log."""
    if probe_health(timeout=1.0) is None:
        return "", ""
    port = worker_address()[1]
    owner = _listening_pid(port)
    who, cmdline = (f"pid {owner}" if owner else "an unknown process"), ""
    try:
        if owner:
            listener = psutil.Process(owner)
            who += f", {listener.name()}"
            cmdline = _one_line(" ".join(agent_lock.redact_argv(listener.cmdline())))
    except psutil.Error:
        pass
    return f"port {port} already answers ({who}) but no worker holds run/agent.lock", cmdline


def _spawn_background(cmd: list[str], env: dict[str, str], out: Any, err: Any) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {"cwd": ROOT, "env": env, "stdin": subprocess.DEVNULL,
                              "stdout": out, "stderr": err, "close_fds": True}
    if sys.platform != "win32":
        return _popen(cmd, start_new_session=True, **kwargs)
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        # Out of a launcher's kill-on-close job (a terminal or IDE), so the worker outlives it.
        return _popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except PermissionError:  # inside a job that forbids breakaway
        return _popen(cmd, creationflags=flags, **kwargs)


def run_ensure_room(env: dict[str, str]) -> int:
    if not ENSURE_ROOM.is_file():
        print(f"[agent_ctl] {ENSURE_ROOM} is missing: the room was not prepared.", file=sys.stderr)
        return EXIT_FAILED
    print("[agent_ctl] Running deploy/windows-local/ensure_room.py (room + agent dispatch)...", flush=True)
    try:
        result = _run([str(agent_python()), str(ENSURE_ROOM)], cwd=ROOT, env=env,
                      timeout=ENSURE_ROOM_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[agent_ctl] ensure_room.py failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return result.returncode


def _start_background(cmd: list[str], env: dict[str, str], timeout: float,
                      args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    marker = (f"\n===== agent_ctl start {_now().isoformat(timespec='seconds')} by {args.by} "
              f"({_one_line(args.reason) or '-'}) =====\n").encode("utf-8")
    started = _monotonic()
    try:
        with (LOG_DIR / AGENT_LOG_NAMES[0]).open("ab") as out, (LOG_DIR / AGENT_LOG_NAMES[1]).open("ab") as err:
            for handle in (out, err):
                handle.write(marker)
                handle.flush()
            proc = _spawn_background(cmd, env, out, err)
    except OSError as exc:
        print(f"[agent_ctl] Could not start the worker: {exc}", file=sys.stderr)
        return EXIT_FAILED, {"result": "failed", "error": str(exc)}
    print(f"[agent_ctl] Worker starting in the background (launcher pid {proc.pid}); "
          "output goes to logs/agent_stderr.log. Waiting for health 200...", flush=True)
    healthy, why = wait_healthy(timeout, proc)
    details: dict[str, Any] = {"launcher_pid": proc.pid, "start_s": round(_monotonic() - started, 1)}
    if not healthy:
        details["health"] = probe_health(timeout=1.0)
        print(f"[agent_ctl] The worker did not come up: {why}. See logs/agent_stderr.log", file=sys.stderr)
        code = EXIT_ALREADY_RUNNING if proc.poll() == EXIT_ALREADY_RUNNING else EXIT_FAILED
        return code, {"result": "failed", "error": why, **details}
    info = agent_lock.read_pid_info() or {}
    link = worker_link(info)
    details.update(health=200, new_pid=info.get("pid"), **({"link": link["state"]} if link else {}))
    linked = " and connected to LiveKit" if link else ""
    print(f"[agent_ctl] Worker pid {details['new_pid']} healthy (200){linked} after {details['start_s']} s.",
          flush=True)
    room = run_ensure_room(env)
    details["ensure_room"] = room
    if room != 0:
        print(f"[agent_ctl] ensure_room.py exited {room}: the room may have no job. The worker keeps "
              "running; run 'python deploy/windows-local/ensure_room.py' again.", file=sys.stderr)
        return EXIT_FAILED, {"result": "failed", "error": f"ensure_room.py exited {room}", **details}
    return EXIT_OK, {"result": "started", **details}


def _run_foreground(cmd: list[str], env: dict[str, str], args: argparse.Namespace) -> int:
    print(f"[agent_ctl] Starting the agent worker in this console ({args.mode} mode, by {args.by}). "
          "Ctrl+C or closing this window stops it.", flush=True)
    try:
        proc = _popen(cmd, cwd=ROOT, env=env)
    except OSError as exc:
        print(f"[agent_ctl] Could not start the worker: {exc}", file=sys.stderr)
        audit("start", args, result="failed", error=str(exc))
        return EXIT_FAILED
    audit("start", args, result="spawned", launcher_pid=proc.pid)
    started = _monotonic()
    healthy = False
    next_probe = started + 2
    # The worker shares this console and handles Ctrl+C / Ctrl+Break itself; this process only notes
    # them and keeps waiting, so it can report the worker's exit.
    interrupts: list[int] = []
    previous = {sig: signal.signal(sig, lambda signum, _frame: interrupts.append(signum))
                for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)) if sig is not None}
    try:
        while True:
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
            if interrupts:
                interrupts.clear()
                print("[agent_ctl] Stop requested; waiting for the worker to exit...", flush=True)
            if not healthy and _monotonic() >= next_probe:
                next_probe = _monotonic() + 2
                if probe_health(timeout=1.0) == 200 and _link_ready():
                    healthy = True
                    pid = (agent_lock.read_pid_info() or {}).get("pid")
                    seconds = round(_monotonic() - started, 1)
                    print(f"[agent_ctl] Worker pid {pid} healthy (200) after {seconds} s.", flush=True)
                    audit("start", args, result="healthy", new_pid=pid, health=200, start_s=seconds)
                    print_highlight("start", args, True,
                                    f"`agent_ctl start --foreground` se worker chala: pid {pid}, "
                                    f"health 200 ({seconds} s me).")
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    ran = round(_monotonic() - started, 1)
    replaced = code == STATUS_CONTROL_C_EXIT and worker_recovery_running()
    audit("start", args, result="exited", exit=code, ran_s=ran, **({"replaced_by": "worker"} if replaced else {}))
    if code == STATUS_CONTROL_C_EXIT:
        print(f"[agent_ctl] Worker stopped by Ctrl+C / Ctrl+Break after {ran} s.", flush=True)
        if replaced:
            print("[agent_ctl] Its own LiveKit link recovery (C-136, 'restart --by worker') replaces it: the new "
                  "worker runs hidden in the background, so closing this window no longer stops JARVIS. "
                  "See 'agent_ctl status'; stop it with 'agent_ctl stop --by user'.", flush=True)
        return EXIT_OK
    if code == EXIT_ALREADY_RUNNING:
        print("[agent_ctl] Another worker already holds run/agent.lock (see the message above).", file=sys.stderr)
    print(f"[agent_ctl] Worker exited with code {code} after {ran} s.", flush=True)
    return code if 0 <= code <= 255 else EXIT_FAILED


def start_worker(args: argparse.Namespace, mode: str, *, foreground: bool,
                 timeout: float = START_TIMEOUT_S) -> tuple[int, dict[str, Any]]:
    """Start one worker unless one runs. Foreground returns only after the worker has exited."""
    if agent_lock.is_locked():
        info = agent_lock.read_pid_info() or {}
        print(f"[agent_ctl] Agent worker already running (pid {info.get('pid', '?')}, by {info.get('by', '?')}, "
              f"since {info.get('started_at', '?')}). Not starting a second one. "
              "Use: python scripts/agent_ctl.py restart --by <you> --reason <why>", file=sys.stderr)
        return EXIT_ALREADY_RUNNING, {"result": "already_running", "running_pid": info.get("pid")}
    problem, listener = _port_problem()
    if problem:
        print(f"[agent_ctl] Refusing to start: {problem}. Stop that process first.", file=sys.stderr)
        if listener:
            print(f"[agent_ctl] Its command line: {listener}", file=sys.stderr)
        return EXIT_REFUSED, {"result": "refused", "error": problem, "listener_cmdline": listener}
    cmd = [str(agent_python()), "-m", "src.core.agent", mode]
    reason = _one_line(args.reason)
    rotated = rotate_agent_logs()
    if rotated:
        print(f"[agent_ctl] Rotated {', '.join(rotated)}.")
    if foreground:
        return _run_foreground(cmd, agent_env(args.by, reason, background=False), args), {}
    return _start_background(cmd, agent_env(args.by, reason, background=True), timeout, args)


def cmd_start(args: argparse.Namespace) -> int:
    if args.force:
        print("[agent_ctl] --force has no effect on start: a running worker is never replaced "
              "(use restart).")
    code, details = start_worker(args, args.mode, foreground=args.foreground, timeout=args.timeout)
    if not details:  # a foreground run has audited itself
        return code
    audit("start", args, exit=code, **details)
    if details.get("result") in ("started", "failed"):
        ok = code == EXIT_OK
        summary = (f"`agent_ctl start` se worker chala: pid {details.get('new_pid')}, health 200 "
                   f"({details.get('start_s')} s me), ensure_room.py OK." if ok
                   else f"`agent_ctl start` nakam: {details.get('error')}.")
        print_highlight("start", args, ok, summary)
    return code


# --------------------------------------------------------------------------- stop


def send_ctrl_break(pid: int) -> bool:
    """Ask the worker to exit as if Ctrl+Break was pressed in its console. True when sent."""
    if sys.platform != "win32":
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            return False
        return True
    try:
        result = _run([sys.executable, "-c", CTRL_BREAK_HELPER, str(pid)], capture_output=True,
                      timeout=20, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    # A helper ended by the event itself has sent it too.
    return result.returncode in (0, STATUS_CONTROL_C_EXIT)


def _force_kill(tree: list[psutil.Process]) -> list[int]:
    """Kill each agent process still alive, one pid at a time (/T would also end the user's apps)."""
    killed = []
    for proc in tree:
        if not _alive(proc):
            continue
        if sys.platform == "win32":
            try:
                _run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
        else:
            try:
                proc.kill()
            except psutil.Error:
                continue
        killed.append(proc.pid)
    return killed


def stop_worker(timeout: float, *, force: bool = False) -> tuple[int, dict[str, Any]]:
    """Stop the worker that holds run/agent.lock; never any process that is not the agent."""
    if not agent_lock.is_locked():
        print("[agent_ctl] No agent worker is running (run/agent.lock is free).")
        return EXIT_OK, {"result": "not_running"}
    proc, problem = worker_process(agent_lock.read_pid_info())
    if proc is None:
        print(f"[agent_ctl] Refusing to stop: {problem}. Nothing was killed.", file=sys.stderr)
        return EXIT_REFUSED, {"result": "refused", "error": problem}
    tree = _process_tree(proc)

    def gone() -> bool:
        return not agent_lock.is_locked() and not any(_alive(p) for p in tree)

    started = _monotonic()
    graceful = False
    if not force:
        graceful = send_ctrl_break(proc.pid)
        if graceful:
            print(f"[agent_ctl] Sent Ctrl+Break to worker pid {proc.pid}; waiting up to {timeout:g} s...",
                  flush=True)
            _wait_until(gone, timeout)
        else:
            print(f"[agent_ctl] Ctrl+Break could not reach pid {proc.pid}; killing the agent's processes.")
    if not gone() and _alive(proc):  # dev mode may have started a new worker child during the wait
        known = {p.pid for p in tree}
        tree += [p for p in _process_tree(proc) if p.pid not in known]
    killed = [] if gone() else _force_kill(tree)
    stopped = _wait_until(gone, KILL_WAIT_S)
    details = {"old_pid": proc.pid, "graceful": graceful and not killed, "killed": killed,
               "stop_s": round(_monotonic() - started, 1)}
    if not stopped:
        error = "the worker still holds run/agent.lock after taskkill"
        print(f"[agent_ctl] {error} (pid {proc.pid}).", file=sys.stderr)
        return EXIT_FAILED, {"result": "failed", "error": error, **details}
    how = "Ctrl+Break" if details["graceful"] else "taskkill"
    print(f"[agent_ctl] Worker pid {proc.pid} stopped ({how}, {details['stop_s']} s).")
    return EXIT_OK, {"result": "stopped", **details}


def cmd_stop(args: argparse.Namespace) -> int:
    code, details = stop_worker(args.timeout, force=args.force)
    audit("stop", args, exit=code, **details)
    if details.get("result") in ("stopped", "failed"):
        ok = code == EXIT_OK
        how = "Ctrl+Break" if details.get("graceful") else "taskkill"
        summary = (f"`agent_ctl stop` se worker band kiya: pid {details.get('old_pid')} ({how}, "
                   f"{details.get('stop_s')} s)." if ok else f"`agent_ctl stop` nakam: {details.get('error')}.")
        print_highlight("stop", args, ok, summary)
    return code


# --------------------------------------------------------------------------- restart


def _last_restart_path() -> Path:
    return agent_lock.run_dir() / "last_restart.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _restart_lock_state(path: Path) -> tuple[bool, str]:
    """(stale, description): stale after 10 min or once the restart that wrote it has exited."""
    data = _read_json(path)
    stamp = data.get("ts")
    if not isinstance(stamp, (int, float)):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            stamp = 0.0
    age = time.time() - stamp
    who = f"a restart by {data.get('by', '?')} (pid {data.get('pid', '?')}) {age:.0f} s ago"
    if age > RESTART_LOCK_STALE_S:
        return True, who
    pid = data.get("pid")
    if isinstance(pid, int) and not _pid_running_since(pid, stamp):
        return True, f"{who}, whose process has exited"
    return False, who


def take_restart_lock(args: argparse.Namespace) -> tuple[Path | None, str]:
    """run/restart.lock created with O_EXCL; (None, why) when another restart holds it."""
    path = agent_lock.run_dir() / "restart.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "ts": time.time(), "by": args.by,
                          "reason": _one_line(args.reason)})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            stale, who = _restart_lock_state(path)
            if not stale:
                return None, f"run/restart.lock is held by {who}"
            print(f"[agent_ctl] Removing a stale run/restart.lock ({who}).")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                return None, f"cannot remove the stale run/restart.lock: {exc}"
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return path, ""
    return None, "another restart took run/restart.lock first"


def worker_recovery_running() -> bool:
    """True while run/restart.lock belongs to a live 'restart --by worker' (C-136 link recovery)."""
    path = agent_lock.run_dir() / "restart.lock"
    return _read_json(path).get("by") == "worker" and not _restart_lock_state(path)[0]


def release_restart_lock(path: Path) -> None:
    if _read_json(path).get("pid") in (os.getpid(), None):
        try:
            path.unlink()
        except OSError:
            pass


def _cooldown_left() -> tuple[float, dict[str, Any]]:
    last = _read_json(_last_restart_path())
    stamp = last.get("ts")
    if not isinstance(stamp, (int, float)):
        return 0.0, last
    return max(0.0, RESTART_COOLDOWN_S - (time.time() - stamp)), last


def _problem(details: dict[str, Any]) -> str:
    return str(details.get("error") or details.get("result") or "unknown")


def _expected_worker_problem(args: argparse.Namespace) -> str:
    """For 'restart --expect-pid' (the worker's own LiveKit link recovery, C-136): why that request no longer
    applies, else ''. The worker that asked must still hold run/agent.lock with its link down: a stop,
    another restart or a reconnect in the meantime wins."""
    pid = getattr(args, "expect_pid", None)
    if pid is None:
        return ""
    if not agent_lock.is_locked():
        return f"worker pid {pid} asked for it, but no worker runs any more (stopped in the meantime)"
    info = agent_lock.read_pid_info() or {}
    started = getattr(args, "expect_started", None)
    if info.get("pid") != pid or (started and info.get("started_at") != started):
        return (f"worker pid {pid} asked for it, but another worker holds run/agent.lock now (pid "
                f"{info.get('pid', '?')}, started {info.get('started_at', '?')})")
    link = worker_link(info)
    if link is not None and link["state"] == livekit_link.CONNECTED:
        return f"worker pid {pid} asked for it, but its LiveKit link is connected again"
    return ""


def _restart(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    expected = _expected_worker_problem(args)
    if expected:
        print(f"[agent_ctl] Restart refused: {expected}.", file=sys.stderr)
        return EXIT_REFUSED, {"result": "refused", "error": expected}
    left, last = _cooldown_left()
    if left > 0 and not args.force:
        error = (f"the last restart (by {last.get('by', '?')}) was {RESTART_COOLDOWN_S - left:.0f} s ago; "
                 f"wait {left:.0f} s or add --force")
        print(f"[agent_ctl] Restart refused: {error}.", file=sys.stderr)
        return EXIT_REFUSED, {"result": "refused", "error": "cooldown", "cooldown_left_s": round(left)}
    details: dict[str, Any] = {"cooldown_overridden": left > 0}
    if left > 0:
        print("[agent_ctl] --force: ignoring the 120 s restart cooldown (logged).")
    info = agent_lock.read_pid_info() if agent_lock.is_locked() else None
    mode = args.mode or _previous_mode(info) or "start"
    started = _monotonic()
    code, details["stop"] = stop_worker(args.timeout)
    if code != EXIT_OK:
        return code, {"result": "failed", "error": "stop: " + _problem(details["stop"]), **details}
    if getattr(args, "expect_pid", None) is not None and details["stop"].get("result") == "not_running":
        error = f"worker pid {args.expect_pid} exited before this restart stopped it; no new worker is started"
        print(f"[agent_ctl] Restart refused: {error}.", file=sys.stderr)
        return EXIT_REFUSED, {"result": "refused", "error": error, **details}
    code, details["start"] = start_worker(args, mode, foreground=False)
    details["restart_s"] = round(_monotonic() - started, 1)
    if code != EXIT_OK:
        return code, {"result": "failed", "error": "start: " + _problem(details["start"]), **details}
    stamp = time.time()
    entry = {"ts": stamp, "at": _now().isoformat(timespec="seconds"), "by": args.by,
             "reason": _one_line(args.reason), "old_pid": details["stop"].get("old_pid"),
             "new_pid": details["start"].get("new_pid")}
    try:
        _last_restart_path().write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[agent_ctl] Warning: could not record the restart time: {exc}", file=sys.stderr)
    return EXIT_OK, {"result": "restarted", **details}


def cmd_restart(args: argparse.Namespace) -> int:
    lock, why = take_restart_lock(args)
    if lock is None:
        print(f"[agent_ctl] Restart refused: {why}.", file=sys.stderr)
        audit("restart", args, exit=EXIT_REFUSED, result="refused", error=why)
        return EXIT_REFUSED
    try:
        code, details = _restart(args)
    finally:
        release_restart_lock(lock)
    audit("restart", args, exit=code, **details)
    if details.get("result") == "refused":
        return code
    old_pid = details.get("stop", {}).get("old_pid", "-")
    new_pid = details.get("start", {}).get("new_pid")
    summary = (f"`agent_ctl restart`: purana worker (pid {old_pid}) band, naya pid {new_pid}; health 200 "
               f"({details.get('restart_s')} s me), ensure_room.py OK." if code == EXIT_OK
               else f"`agent_ctl restart` nakam: {details.get('error')}.")
    print_highlight("restart", args, code == EXIT_OK, summary)
    return code


# --------------------------------------------------------------------------- status, wait, logs


def _uptime(proc: psutil.Process | None, info: dict[str, Any] | None) -> str:
    try:
        started = proc.create_time() if proc is not None else _started_epoch(info)
    except psutil.Error:
        started = _started_epoch(info)
    return "?" if started is None else str(timedelta(seconds=max(0, int(time.time() - started))))


def cmd_status(_args: argparse.Namespace) -> int:
    locked = agent_lock.is_locked()
    info = agent_lock.read_pid_info()
    health = probe_health()
    health_text = f"{health if health is not None else 'no answer'} ({health_url()})"
    if not locked:
        print("JARVIS agent worker: NOT RUNNING (run/agent.lock is free)")
        if info:
            print(f"  last     pid {info.get('pid', '?')}, by {info.get('by', '?')}, "
                  f"started {info.get('started_at', '?')} (exited)")
        print(f"  health   {health_text}")
        if health is not None:
            print("  WARNING  something answers on the worker port without holding the lock.")
        return 1
    proc, problem = worker_process(info)
    launcher = _launcher(proc) if proc is not None else None
    info = info or {}
    print("JARVIS agent worker: RUNNING")
    print(f"  pid      {info.get('pid', '?')}" + (f" (launcher {launcher.pid})" if launcher else ""))
    print(f"  by       {info.get('by') or '?'}")
    print(f"  reason   {info.get('reason') or '-'}")
    print(f"  mode     {_previous_mode(info) or '?'}")
    print(f"  since    {info.get('started_at', '?')} (uptime {_uptime(proc, info)})")
    print(f"  health   {health_text}")
    link = worker_link(info)
    print(f"  link     {describe_link(link)}")
    worker = worker_info() if health == 200 else None
    if worker:
        load = worker.get("worker_load")
        load_text = f"{load:.2f}" if isinstance(load, (int, float)) else "?"
        print(f"  jobs     {worker.get('active_jobs', '?')} active (load {load_text})")
    if info.get("git_sha"):
        print(f"  git      {str(info['git_sha'])[:7]}")
    if link is not None and link["state"] != livekit_link.CONNECTED:
        print(f"  WARNING  not connected to LiveKit, so no room gets this worker: {link_hint(link)}")
    if problem:
        print(f"  WARNING  {problem}")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    started = _monotonic()
    healthy, why = wait_healthy(args.timeout, progress=True)
    waited = round(_monotonic() - started, 1)
    if healthy:
        info = agent_lock.read_pid_info() or {}
        link = worker_link(info)
        linked = ", LiveKit link connected," if link else ""
        print(f"[agent_ctl] Worker pid {info.get('pid')} is healthy (200 on {health_url()}){linked} after {waited} s.")
        audit("wait", args, result="healthy", health=200, pid=info.get("pid"), waited_s=waited,
              **({"link": link["state"]} if link else {}))
        return EXIT_OK
    print(f"[agent_ctl] Worker not healthy: {why}.", file=sys.stderr)
    audit("wait", args, result="failed", error=why, waited_s=waited)
    return EXIT_FAILED


def cmd_logs(args: argparse.Namespace) -> int:
    since = _started_epoch(agent_lock.read_pid_info()) if agent_lock.is_locked() else None
    for name in LOG_CHOICES[args.file]:
        path = LOG_DIR / name
        print(f"===== logs/{name} (last {args.lines} lines) =====")
        if not path.is_file():
            print("(no such file)")
            continue
        if since is not None and name in AGENT_LOG_NAMES and path.stat().st_mtime < since:
            print("(not written since the running worker started: it logs to its own console window)")
        for line in tail_lines(path, args.lines):
            print(line)
    return EXIT_OK


# --------------------------------------------------------------------------- command line


def _positive(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent_ctl.py",
        description="Start, stop and watch the single JARVIS agent worker (K-53 COORD-3).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="pid, by, reason, uptime, health and LiveKit link; exit 0 when a worker runs")
    actions = {}
    for name, text in (("start", "start the worker unless one runs (exit 3 if one does)"),
                       ("stop", "stop the worker that holds run/agent.lock"),
                       ("restart", "stop + start, with run/restart.lock and a 120 s cooldown")):
        action = sub.add_parser(name, help=text)
        action.add_argument("--by", required=True, choices=WHO, help="who asks (logged)")
        action.add_argument("--reason", default="", help="task ID or why (logged)")
        actions[name] = action
    actions["start"].add_argument("--mode", choices=MODES, default="start")
    actions["start"].add_argument("--foreground", action="store_true",
                                  help="run in this console until the worker exits")
    actions["start"].add_argument("--timeout", type=_positive, default=START_TIMEOUT_S,
                                  help="seconds to wait for health 200 (background)")
    actions["start"].add_argument("--force", action="store_true", help="no effect (kept for symmetry)")
    actions["stop"].add_argument("--timeout", type=_positive, default=STOP_TIMEOUT_S,
                                 help="seconds between Ctrl+Break and taskkill")
    actions["stop"].add_argument("--force", action="store_true", help="skip Ctrl+Break, kill at once")
    actions["restart"].add_argument("--mode", choices=MODES, default=None,
                                    help="default: the mode of the worker being replaced, else start")
    actions["restart"].add_argument("--timeout", type=_positive, default=STOP_TIMEOUT_S,
                                    help="seconds between Ctrl+Break and taskkill")
    actions["restart"].add_argument("--force", action="store_true", help="ignore the 120 s cooldown (logged)")
    actions["restart"].add_argument("--expect-pid", type=int, default=None,
                                    help="only while this worker pid holds the lock with its LiveKit link down "
                                         "(the worker's own recovery, C-136)")
    actions["restart"].add_argument("--expect-started", default=None,
                                    help="with --expect-pid: that worker's started_at in run/agent.pid.json")
    wait = sub.add_parser("wait", help="wait for health 200 and a connected LiveKit link (exit 1 on timeout, "
                                         "when the worker exits or its link failed)")
    wait.add_argument("--timeout", type=_positive, default=START_TIMEOUT_S)
    logs = sub.add_parser("logs", help="last lines of the agent logs and logs/agent_ctl.log")
    logs.add_argument("-n", "--lines", type=_positive_int, default=50)
    logs.add_argument("--file", choices=tuple(LOG_CHOICES), default="all")
    return parser.parse_args(argv)


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "status": cmd_status, "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart,
    "wait": cmd_wait, "logs": cmd_logs,
}


def _utf8_output() -> None:
    """UTF-8 output also through pipes (Git Bash, Electron); the console shows Unicode either way."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_output()
    args = parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("[agent_ctl] Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
