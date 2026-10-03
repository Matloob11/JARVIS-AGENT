"""The one safe way to start, stop and restart the PC agent worker (plan task C-09, contract K-53).

Usage (Git Bash in the repo root, PY=.venv_312/Scripts/python.exe):
    $PY scripts/agent_ctl.py status
    $PY scripts/agent_ctl.py start   --by claude|cursor|user [--reason WHY] [--mode start|dev] [--foreground]
    $PY scripts/agent_ctl.py stop    --by claude|cursor|user [--reason WHY] [--timeout 20] [--force]
    $PY scripts/agent_ctl.py restart --by claude|cursor|user [--reason WHY] [--mode start|dev] [--force]
    $PY scripts/agent_ctl.py wait    [--timeout 60]
    $PY scripts/agent_ctl.py logs    [-n 50] [--file all|stdout|stderr|ctl]
    $PY scripts/agent_ctl.py node    start|stop|status|logs|update [--sha SHA] [--canary]   (C-123, the Desktop Node)
    $PY scripts/agent_ctl.py brain   vps|pc [--when-idle|--now]                            (C-123, the brain switch)
    $PY scripts/agent_ctl.py brain   pc --laptop-only     (only this laptop's JARVIS_BRAIN; no VPS read or command)
    $PY scripts/agent_ctl.py backup  pull [--dest PATH]   (C-122, the newest encrypted VPS snapshot to this PC)

The worker holds run/agent.lock for its whole life (src/core/agent_lock.py): the lock is the source of
truth, run/agent.pid.json only describes the holder and stays behind after it exits. Since C-136 it also
writes run/agent_link.json, the state of its LiveKit link (src/core/livekit_link.py): connecting,
connected, retrying or failed. /health stays 200 while that link retries, so 'ready' means health 200
and a connected link; a worker from before C-136 writes no such file and is ready on health 200 alone.

start    Exit 3 while a worker holds the lock. Rotates logs/agent_stdout.log and logs/agent_stderr.log
         (5 MB, keeps .1-.5) and runs '.venv_312 python -m src.core.agent <mode>' with JARVIS_STARTED_BY,
         JARVIS_START_REASON, PYTHONPATH, PYTHONIOENCODING and JARVIS_HYBRID=1. Default: hidden, output
         appended to those logs, waits until the worker is ready (health 200 on 127.0.0.1:8081 and its
         LiveKit link connected, max 60 s), then runs deploy/windows-local/ensure_room.py once, its output
         streamed here; its '[ROOM] Result: ...' line goes into the summary, NO AGENT / UNKNOWN / an agent of
         an earlier worker left alone / a room ensured by the bridge without an agent check (or no result
         line) as a warning.
         --foreground: in this console until the worker exits. Before a worker starts (also in restart) it
         checks the VPS as the worker reaches it (C-16): GET <UI_BRIDGE_URL>/health must answer 200 and
         GET <LIVEKIT_URL as http(s)>/rtc/validate 401, both on the domain since OP-13. A failed check is
         printed and logged ('vps' in logs/agent_ctl.log), never a reason not to start the local worker.
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
backup pull  Pulls the newest encrypted VPS snapshot now (C-122 step 4, src/core/desktop/backup_pull.cli_main, in
         the .venv_312 interpreter) into --dest (default %LOCALAPPDATA%\\JARVIS\\vps_backups); every snapshot is
         kept. Read-only toward the worker: no runtime lock, nothing started or stopped. stdout: PULLED|KEPT=<path>,
         NEW, SIZE, SHA256, ENCRYPTED, TOTAL_BYTES, COUNT (exit 0); stderr: ERROR=<reason> [HINT=<text>] (exit 1)
         or ERROR=config_<ExceptionName> (exit 2, node config, key or DPAPI). Recorded in
         run/desktop_backup_pull.json (last_success, last_size) and as one line in logs/agent_ctl.log.

start/stop/restart/wait append a JSON line to logs/agent_ctl.log; start/stop/restart by claude or cursor
also print a ready-to-paste done_highlight.md entry (K-53: log every restart there).
Exit codes: 0 ok, 1 failed, 2 usage, 3 a worker already runs (start), 4 refused for safety (restart
cooldown, a restart in progress, a pid that is not the agent, port 8081 answered by another process, a
worker's recovery request that no longer applies), 130 interrupted (Ctrl+C).
JARVIS_BRAIN (environment, else the root .env): only an explicit 'vps' refuses start/restart (exit 4, the laptop
then runs the Desktop Node); unset, empty or 'pc' changes nothing above, and any other value counts as 'pc' with
a warning. 'brain vps|pc' is its only writer: the VPS .env and the laptop's root .env (that one line, in place);
when the VPS already says the target it repairs only a laptop that differs. 'brain pc --laptop-only' rewrites only
this laptop's JARVIS_BRAIN line without reading the VPS at all (for a laptop .env left at vps while the VPS cannot
be reached), journalled like that repair. A running 'brain' holds run/brain.lock: a second 'brain' and a
start/restart (except the worker's own recovery) are refused meanwhile. run/restart.lock and run/brain.lock are
taken with O_EXCL; a stale one is taken over only under an OS lock on run/<name>.takeover, after judging it stale
again there, so two processes can never both take it.
"""
# pylint: disable=too-many-lines  # one self-contained CLI; the six commands share these helpers
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from html import escape as _html_escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
EXIT_USAGE = 2
EXIT_ALREADY_RUNNING = agent_lock.EXIT_ALREADY_RUNNING
EXIT_REFUSED = 4
EXIT_INTERRUPTED = 130   # Ctrl+C (128 + SIGINT)

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
# The VPS as the worker reaches it (C-16, K-48): (label, setting, path, wanted status). LiveKit answers
# /rtc/validate without a token with 401, so 401 means LiveKit itself answered.
VPS_CHECKS = (("bridge", "UI_BRIDGE_URL", "/health", 200), ("LiveKit", "LIVEKIT_URL", "/rtc/validate", 401))
VPS_CHECK_TIMEOUT_S = 4.0
# The worker's own default when a setting is missing (services/utils/jarvis_config.py); LIVEKIT_URL has none.
WORKER_URL_DEFAULTS = {"UI_BRIDGE_URL": "http://127.0.0.1:5001"}
AGENT_MARKERS = ("src.core.agent", "src/core/agent.py")
MP_MARKERS = ("--multiprocessing-fork", "from multiprocessing.")
PKT = timezone(timedelta(hours=5), "PKT")

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
# The worker is the live voice loop. It runs above normal so builds and test runs on the same PC never
# starve it, also when agent_ctl itself runs in a below-normal shell (Windows passes that class to children).
ABOVE_NORMAL_PRIORITY_CLASS = getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)

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


def print_highlight(action: str, args: argparse.Namespace, ok: bool, summary: str, *,
                    warning: bool = False) -> None:
    """A done_highlight.md entry (that file's template) for the agents; the user's launchers skip it. warning:
    the worker runs but ensure_room.py reported NO AGENT / UNKNOWN / an earlier worker's agent / a bridge-ensured
    room whose agent was not checked (🟡 PARTIAL instead of ✅)."""
    if args.by not in ("claude", "cursor"):
        return
    now = datetime.now(PKT)
    status = ("🟡 🔁" if warning else "✅ 🔁") if ok else "🔴"
    task = _one_line(args.reason, 60) or "runtime"
    print()
    print("done_highlight.md entry (paste at the end of the file):")
    print(f"### {status} [{args.by}] {task} · {_TITLES[action]} (agent_ctl) · {now:%Y-%m-%d %H:%M} PKT")
    print(f"- **Kya kiya:** {summary}")
    print("- **Files:** —")
    verify = "`.venv_312/Scripts/python.exe scripts/agent_ctl.py status`"
    outcome = ("health 200, lekin room me agent ki tasdeeq nahi (ensure_room warning)" if warning else
               "health 200") if ok else "dekho upar wala error"
    print(f"- **Verify:** {verify} → {outcome}")
    print(f"- **Commit:** abhi nahi · **Deploy:** PC agent {action} {now:%H:%M} PKT")
    print("- **Doosre agent ke liye:** —")


# --------------------------------------------------------------------------- VPS checks (C-16)


def vps_check_url(setting: str, path: str) -> str:
    """path under the URL in setting as the worker reads it (K-47, its default when missing), ws/wss as
    http/https; '' when there is none. Since OP-13 both lead to the domain: https://<domain>/vortex/health and
    https://<domain>/rtc/validate."""
    base = _env_setting(setting) or WORKER_URL_DEFAULTS.get(setting, "")
    if not base:
        return ""
    parts = urlsplit(base)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme.lower(), parts.scheme)
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + path, "", ""))


def _shown(url: str) -> str:
    """url without user info, query or fragment, for the console and logs/agent_ctl.log."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def _http_status(url: str, timeout: float) -> tuple[int | None, str]:
    """(status, '') for any answer, 4xx and 5xx too; (None, the error's type) when none comes (DNS, connect,
    TLS, timeout)."""
    try:
        return httpx.get(url, timeout=httpx.Timeout(timeout)).status_code, ""
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        _drop_frames(exc)
        return None, type(exc).__name__


def check_vps(timeout: float = VPS_CHECK_TIMEOUT_S) -> list[tuple[bool, str]]:
    """(as wanted, note) for each of VPS_CHECKS, e.g. (True, 'LiveKit https://<domain>/rtc/validate 401'). A
    setting that urlsplit refuses ('wss://[host') is a failed check; the note names only the error's type, since
    its message may hold the URL's user info."""
    results = []
    for label, setting, path, want in VPS_CHECKS:
        try:
            url = vps_check_url(setting, path)
            shown = _shown(url)
        except ValueError as exc:
            results.append((False, f"{label}: {setting} is not a usable URL ({type(exc).__name__})"))
            continue
        if not url:
            results.append((False, f"{label}: {setting} is not set"))
            continue
        code, error = _http_status(url, timeout)
        got = f"no answer ({error})" if code is None else str(code)
        results.append((code == want, f"{label} {shown} {got}" + ("" if code == want else f", want {want}")))
    return results


def report_vps() -> str:
    """Prints the VPS checks before a start; returns 'ok' or the failed checks (for logs/agent_ctl.log). A failed
    check never stops the start: the worker runs on this PC and connects once the VPS answers (C-136). In a
    restart the old worker has already stopped here, so not even an error in the checks may stop it."""
    try:
        results = check_vps()
    except Exception as exc:  # pylint: disable=broad-exception-caught  # see the docstring
        _drop_frames(exc)
        results = [(False, f"the check itself failed ({type(exc).__name__})")]
    notes = "; ".join(note for _good, note in results)
    if all(good for good, _note in results):
        print(f"[agent_ctl] VPS check ok: {notes}.", flush=True)
        return "ok"
    print(f"[agent_ctl] WARNING: VPS check failed: {notes}. Starting the worker anyway; it connects once the VPS "
          "answers ('agent_ctl status' shows its LiveKit link).", file=sys.stderr, flush=True)
    return "; ".join(note for good, note in results if not good)


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
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | ABOVE_NORMAL_PRIORITY_CLASS
    try:
        # Out of a launcher's kill-on-close job (a terminal or IDE), so the worker outlives it.
        return _popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except PermissionError:  # inside a job that forbids breakaway
        return _popen(cmd, creationflags=flags, **kwargs)


def _run_streamed(cmd: list[str], *, timeout: float, **kwargs: Any) -> tuple[int, list[str]]:
    """Run cmd with stdout and stderr merged, echo each line to this console as it comes (as an uncaptured run
    shows it) and return (exit code, its lines). Past timeout the child is killed and TimeoutExpired raised, as
    subprocess.run(timeout=) does; OSError when it cannot start."""
    proc = _popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                  encoding="utf-8", errors="replace", bufsize=1, **kwargs)
    lines: list[str] = []

    def pump() -> None:
        for line in proc.stdout or ():
            text = line.rstrip("\r\n")
            lines.append(text)
            print(text, flush=True)

    reader = threading.Thread(target=pump, name="agent-ctl-output", daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        reader.join(5)
        raise
    reader.join(5)
    return code, list(lines)


ROOM_RESULT_PREFIX = "[ROOM] Result:"
# ensure_room.py results that leave the room without the worker's agent (or unread, or with an agent of an earlier
# worker that was left alone and may be a ghost, or ensured by the bridge without checking for an agent): a start
# reports them as a warning; its exit code stays as it was.
ROOM_WARNING_RESULTS = ("NO AGENT", "UNKNOWN", "AN AGENT OF AN EARLIER WORKER", "ROOM ENSURED BY THE BRIDGE")


def room_result(lines: list[str]) -> str:
    """The text of ensure_room.py's last '[ROOM] Result: ...' line, '' when it printed none."""
    results = [line.strip() for line in lines if line.strip().startswith(ROOM_RESULT_PREFIX)]
    return results[-1][len(ROOM_RESULT_PREFIX):].strip() if results else ""


def room_result_warning(result: str) -> bool:
    """True for a NO AGENT, UNKNOWN, earlier-worker or bridge (agent presence not checked) result, and for a run
    that printed no result line at all."""
    return not result or result.strip().upper().startswith(ROOM_WARNING_RESULTS)


def describe_room_result(result: str, *, mark: bool = True) -> str:
    """ensure_room.py's result for the start/restart summary and the done_highlight.md entry (one line), with
    'WARNING ' before a warning result; mark=False leaves that out (a console line that already says WARNING)."""
    shown = _one_line(result, 160) if result else "no [ROOM] Result line"
    return f"ensure_room.py: {'WARNING ' if mark and room_result_warning(result) else ''}{shown}"


def ensure_room_run(env: dict[str, str]) -> tuple[int, str]:
    """Run deploy/windows-local/ensure_room.py with its output streamed to this console; (exit code, the text of
    its '[ROOM] Result: ...' line or '')."""
    if not ENSURE_ROOM.is_file():
        print(f"[agent_ctl] {ENSURE_ROOM} is missing: the room was not prepared.", file=sys.stderr)
        return EXIT_FAILED, ""
    print("[agent_ctl] Running deploy/windows-local/ensure_room.py (room + agent dispatch)...", flush=True)
    child_env = {**env, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}  # lines arrive as printed
    try:
        code, lines = _run_streamed([str(agent_python()), str(ENSURE_ROOM)], cwd=ROOT, env=child_env,
                                    timeout=ENSURE_ROOM_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[agent_ctl] ensure_room.py failed: {exc}", file=sys.stderr)
        return EXIT_FAILED, ""
    return code, room_result(lines)


def run_ensure_room(env: dict[str, str]) -> int:
    """ensure_room_run's exit code only (the brain switch's ENSURE; its VERIFY checks the agent itself)."""
    return ensure_room_run(env)[0]


def _raise_worker_priority(pids: list[int]) -> None:
    """Windows: lift the worker tree to above normal. The venv python.exe is a launcher that starts the real
    interpreter as a child, and Windows does not pass ABOVE_NORMAL to children, so the creation flag alone
    leaves the worker at normal. Only processes running src.core.agent (and their children) are touched."""
    if sys.platform != "win32":
        return
    for pid in pids:
        try:
            root = psutil.Process(pid)
            if not any(marker in " ".join(root.cmdline()) for marker in AGENT_MARKERS):
                continue
            for proc in (root, *root.children(recursive=True)):
                if proc.nice() in (psutil.IDLE_PRIORITY_CLASS, psutil.BELOW_NORMAL_PRIORITY_CLASS,
                                   psutil.NORMAL_PRIORITY_CLASS):
                    proc.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        except (psutil.Error, OSError):
            continue


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
    _raise_worker_priority([pid for pid in (proc.pid, info.get("pid")) if isinstance(pid, int)])
    room, result = ensure_room_run(env)
    details.update(ensure_room=room, room_result=result, room_warning=room_result_warning(result))
    if room != 0:
        print(f"[agent_ctl] ensure_room.py exited {room}: the room may have no job. The worker keeps "
              "running; run 'python deploy/windows-local/ensure_room.py' again.", file=sys.stderr)
        return EXIT_FAILED, {"result": "failed", "error": f"ensure_room.py exited {room}", **details}
    if details["room_warning"]:
        print(f"[agent_ctl] WARNING: {describe_room_result(result, mark=False)}. The worker runs, but JARVIS may "
              "stay silent until an agent of this worker is in the room; run 'python deploy/windows-local/"
              "ensure_room.py' again or reconnect from the UI.", file=sys.stderr, flush=True)
    else:
        print(f"[agent_ctl] {describe_room_result(result)}", flush=True)
    return EXIT_OK, {"result": "started", **details}


def _run_foreground(cmd: list[str], env: dict[str, str], args: argparse.Namespace, *, vps: str = "") -> int:
    print(f"[agent_ctl] Starting the agent worker in this console ({args.mode} mode, by {args.by}). "
          "Ctrl+C or closing this window stops it.", flush=True)
    try:
        proc = _popen(cmd, cwd=ROOT, env=env)
    except OSError as exc:
        print(f"[agent_ctl] Could not start the worker: {exc}", file=sys.stderr)
        audit("start", args, result="failed", error=str(exc), vps=vps)
        return EXIT_FAILED
    audit("start", args, result="spawned", launcher_pid=proc.pid, vps=vps)
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
    vps = report_vps()
    cmd = [str(agent_python()), "-m", "src.core.agent", mode]
    reason = _one_line(args.reason)
    rotated = rotate_agent_logs()
    if rotated:
        print(f"[agent_ctl] Rotated {', '.join(rotated)}.")
    if foreground:
        return _run_foreground(cmd, agent_env(args.by, reason, background=False), args, vps=vps), {}
    code, details = _start_background(cmd, agent_env(args.by, reason, background=True), timeout, args)
    return code, {**details, "vps": vps}


def cmd_start(args: argparse.Namespace) -> int:
    refused = _vps_mode_worker_refused(args, "start") or _brain_switch_worker_refused(args, "start")
    if refused is not None:
        code, details = refused
        audit("start", args, exit=code, **details)
        return code
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
                   f"({details.get('start_s')} s me), {_room_summary(details)}." if ok
                   else f"`agent_ctl start` nakam: {details.get('error')}.")
        print_highlight("start", args, ok, summary, warning=ok and bool(details.get("room_warning")))
    return code


def _room_summary(start: Mapping[str, Any]) -> str:
    """The ensure_room part of a start/restart summary, from start_worker's details."""
    if "room_result" not in start:
        return "ensure_room.py exit 0" if start.get("ensure_room") == 0 else "ensure_room.py ran"
    return describe_room_result(str(start.get("room_result") or ""))


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


def _run_lock_state(path: Path, what: str = "restart",
                    stale_s: float = RESTART_LOCK_STALE_S) -> tuple[bool, str]:
    """(stale, description) of a run/*.lock written by _take_run_lock: stale after stale_s or once the process
    that wrote it has exited."""
    data = _read_json(path)
    stamp = data.get("ts")
    if not isinstance(stamp, (int, float)):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            stamp = 0.0
    age = time.time() - stamp
    who = f"a {what} by {data.get('by', '?')} (pid {data.get('pid', '?')}) {age:.0f} s ago"
    if age > stale_s:
        return True, who
    pid = data.get("pid")
    if isinstance(pid, int) and not _pid_running_since(pid, stamp):
        return True, f"{who}, whose process has exited"
    return False, who


def _restart_lock_state(path: Path) -> tuple[bool, str]:
    """(stale, description): stale after 10 min or once the restart that wrote it has exited."""
    return _run_lock_state(path, "restart", RESTART_LOCK_STALE_S)


TAKEOVER_SUFFIX = ".takeover"


def _takeover_guard(name: str) -> str:
    """The run/ file whose OS lock (agent_lock.hold) serializes every removal of run/<name>. The OS drops it when
    its holder exits, so the guard itself can never go stale; the file is kept."""
    return name + TAKEOVER_SUFFIX


def _create_run_lock(path: Path, payload: str) -> bool:
    """run/<name> created with O_EXCL and the payload written; False when it already exists."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return True


STALE_UNLINK_TRIES = 20
STALE_UNLINK_PAUSE_S = 0.05


def _unlink_stale_lock(path: Path) -> str:
    """Removes a stale run lock; '' when it is gone, else why not. On Windows another agent_ctl reading the file to
    judge it holds it open for a moment, and the delete then fails with a sharing violation (PermissionError,
    WinError 32): that is retried for about a second, so the takeover does not fail and leave the lock to nobody."""
    for attempt in range(STALE_UNLINK_TRIES):
        try:
            path.unlink()
            return ""
        except FileNotFoundError:
            return ""
        except PermissionError as exc:
            if attempt == STALE_UNLINK_TRIES - 1:
                return str(exc)
            time.sleep(STALE_UNLINK_PAUSE_S)
        except OSError as exc:
            return str(exc)
    return ""


def _take_run_lock(name: str, what: str, stale_s: float, args: argparse.Namespace) -> tuple[Path | None, str]:
    """run/<name> created with O_EXCL; (None, why) when another live <what> holds it. A stale one (older than
    stale_s, or its process has exited) is removed and taken, race-free: the removal happens only under the OS lock
    of run/<name>.takeover and only after the lock was judged stale AGAIN under it. So when two processes both find
    the same stale lock, the second one to get the guard sees the first one's fresh lock and is refused; it can
    never remove that fresh lock and take run/<name> as well."""
    path = agent_lock.run_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "ts": time.time(), "by": getattr(args, "by", None),
                          "reason": _one_line(getattr(args, "reason", "") or "")})
    if _create_run_lock(path, payload):
        return path, ""
    stale, who = _run_lock_state(path, what, stale_s)
    if not stale:
        return None, f"run/{name} is held by {who}"
    guard = _takeover_guard(name)
    if not agent_lock.hold(guard):
        return None, f"another {what} is taking over the stale run/{name} right now"
    try:
        if path.exists():
            stale, who = _run_lock_state(path, what, stale_s)  # judged again: it may be fresh by now
            if not stale:
                return None, f"run/{name} is held by {who}"
            print(f"[agent_ctl] Removing a stale run/{name} ({who}).")
            problem = _unlink_stale_lock(path)
            if problem:
                return None, f"cannot remove the stale run/{name}: {problem}"
        if _create_run_lock(path, payload):
            return path, ""
        stale, who = _run_lock_state(path, what, stale_s)
        return None, f"another {what} took run/{name} first ({who})"
    finally:
        agent_lock.release_held(guard)


def take_restart_lock(args: argparse.Namespace) -> tuple[Path | None, str]:
    """run/restart.lock created with O_EXCL; (None, why) when another restart holds it."""
    return _take_run_lock("restart.lock", "restart", RESTART_LOCK_STALE_S, args)


def worker_recovery_running() -> bool:
    """True while run/restart.lock belongs to a live 'restart --by worker' (C-136 link recovery)."""
    path = agent_lock.run_dir() / "restart.lock"
    return _read_json(path).get("by") == "worker" and not _restart_lock_state(path)[0]


def release_run_lock(path: Path) -> None:
    """Remove a run/*.lock this process took (never one another process holds). The removal takes the same
    takeover guard as _take_run_lock when it can, so it never removes a lock another process has just taken over
    in its place; without the guard (held by another process for over a second) its own lock is removed anyway."""
    guard = _takeover_guard(path.name)
    guarded = agent_lock.hold(guard)
    try:
        if _read_json(path).get("pid") in (os.getpid(), None):
            try:
                path.unlink()
            except OSError:
                pass
    finally:
        if guarded:
            agent_lock.release_held(guard)


release_restart_lock = release_run_lock


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
    refused = _vps_mode_worker_refused(args, "restart") or _brain_switch_worker_refused(args, "restart")
    if refused is not None:
        code, details = refused
        audit("restart", args, exit=code, **details)
        return code
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
    start = details.get("start", {})
    new_pid = start.get("new_pid")
    ok = code == EXIT_OK
    summary = (f"`agent_ctl restart`: purana worker (pid {old_pid}) band, naya pid {new_pid}; health 200 "
               f"({details.get('restart_s')} s me), {_room_summary(start)}." if ok
               else f"`agent_ctl restart` nakam: {details.get('error')}.")
    print_highlight("restart", args, ok, summary, warning=ok and bool(start.get("room_warning")))
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


# =============================================================================================================
# VPS brain: the Desktop Node, the brain switch and the two-runtimes rule (plan task C-123; K-72, K-70).
#
# The laptop runs exactly one brain-side runtime at a time (the two-runtimes rule, docs/CONTRACTS.md K-72):
#   - vps mode: the Desktop Node (a worker start is refused), plus the VPS agent/agent-svc containers which
#     only Claude's deploy touches;
#   - pc mode:  the PC worker named JARVIS_PC_AGENT_NAME ('jarvis-pc'), the commands above.
# 'agent_ctl node ...' drives the node through its logon task and its 127.0.0.1:8082 health/stop server
# (src/core/desktop). 'agent_ctl brain vps|pc' is the only writer of JARVIS_BRAIN: the VPS .env (the source of
# truth) and, at the same FLIP_ENV step, the laptop's root .env, so every laptop reader (current_brain, vortex.py,
# ensure_room.py, the launchers) agrees. It journals a resumable state machine and restores the old brain (both
# .env files) if it fails half-way.
# Every VPS effect goes through ssh and every bridge read through PC-signed HTTP, both injected so the tests
# mock them and no real ssh, container, scheduled task or /stop is ever touched.
# -------------------------------------------------------------------------------------------------------------

BRAINS = ("vps", "pc")
VPS_AGENT_NAME = "jarvis"              # the brain running in the VPS containers
PC_AGENT_NAME_DEFAULT = "jarvis-pc"    # the PC fallback worker (JARVIS_PC_AGENT_NAME in the VPS .env)
BRAIN_ENV = "JARVIS_BRAIN"
NODE_HEALTH_HOST = "127.0.0.1"
NODE_HEALTH_PORT = 8082
NODE_TASK_NAME = "JARVIS Desktop Node"
NODE_STATUS_FILE = "node_current.json"
NODE_TOKEN_FILE = "desktop_node.token"
NODE_START_TIMEOUT_S = 60
NODE_STOP_TIMEOUT_S = 20
# The default public bridge (src/core/desktop/settings.DEFAULT_LINK_URL); JARVIS_LINK_URL overrides it.
DEFAULT_LINK_URL = "https://jarvis.srv1802669.hstgr.cloud"
BRAIN_STATES = ("PREFLIGHT", "BACKUP", "STOP_OLD", "SYNC_DATA", "FLIP_ENV", "START_NEW", "ENSURE", "VERIFY",
                "DONE")
BRAIN_JOURNAL_FILE = "brain_switch.json"
BRAIN_LOCK_FILE = "brain.lock"
# A brain switch runs much longer than a restart (the 60 s notice, ssh steps of up to 600 s each, 60 s of VERIFY),
# so its run/brain.lock only goes stale by age after 2 h; a lock whose process has exited is stale at once.
BRAIN_LOCK_STALE_S = 2 * 3600
# The exact command that makes this laptop pc again (named in the worker-start refusal).
BRAIN_PC_COMMAND = ".venv_312/Scripts/python.exe scripts/agent_ctl.py brain pc"
LAPTOP_ONLY_FLAG = "--laptop-only"
IDLE_GATE_S = 600          # no speech, typed chat or tool call for this long (bridge last_activity_ts)
SPOKEN_NOTICE_S = 60        # --now: the spoken notice before the switch
VERIFY_TRIES = 30
VERIFY_STEP_S = 2.0
# The logon task's program and working directory are plain Windows paths (MAX_PATH 260 with its terminator).
TASK_PATH_MAX = 259
TASK_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
NODE_SUPERVISOR_MODULE = "src.core.desktop.supervisor"

# The VPS side of the switch (K-72). The checkout and compose layout are deploy.sh's: /opt/jarvis-agent with the
# compose file and its .env in deploy/hostinger, compose project 'jarvis'. The helpers below come with later tasks
# (C-126..C-129), so PREFLIGHT checks each of them before anything is backed up or stopped.
VPS_APP_DIR = "/opt/jarvis-agent"
VPS_COMPOSE_DIR = f"{VPS_APP_DIR}/deploy/hostinger"
VPS_COMPOSE_PROJECT = "jarvis"
VPS_BRAIN_PROFILE = "vps-brain"
# (path in the checkout, test flag): the shell scripts run as ./<path>, so they must be executable; set_env.py
# runs through python3, so it must be readable.
VPS_HELPERS = (("deploy/hostinger/backup.sh", "-x"), ("deploy/hostinger/sync_brain_data.sh", "-x"),
               ("deploy/hostinger/set_env.py", "-r"))
# The VPS brain's two containers and the compose service names that may define each (the design names the
# services 'agent' and 'agent-svc' with these container names; the container names are accepted as well).
VPS_BRAIN_CONTAINERS = (("jarvis-agent", ("agent", "jarvis-agent")),
                        ("jarvis-agent-svc", ("agent-svc", "jarvis-agent-svc")))
VPS_DEFAULT_SERVICES = ("agent", "agent-svc")
VPS_PROVISIONED_BY = "C-126..C-129"


_brain_warned: set[str] = set()


def current_brain() -> str:
    """The brain this laptop serves: JARVIS_BRAIN from the environment, else the root .env (then the hybrid
    .env, K-47), default 'pc' (today's PC brain). Only 'agent_ctl brain' writes it (set_laptop_brain, at FLIP_ENV
    and in the restore).

    PC-mode safety: only an explicit 'vps' (any case, surrounding blanks ignored) means vps mode, the one mode
    that refuses a PC worker start. Unset or empty is pc; any other value is pc too, with a warning (once per
    process), so a typo can never block the owner's worker."""
    raw = _env_setting(BRAIN_ENV) or ""
    value = raw.strip().lower()
    if not value or value in BRAINS:
        return value or "pc"
    shown = _one_line(raw, 20).encode("ascii", "replace").decode("ascii")
    if shown not in _brain_warned:
        _brain_warned.add(shown)
        print(f"[agent_ctl] WARNING: {BRAIN_ENV}={shown!r} is neither 'vps' nor 'pc'; treating it as 'pc'.",
              file=sys.stderr, flush=True)
    return "pc"


def laptop_env_file() -> Path | None:
    """The laptop file that 'agent_ctl brain' writes JARVIS_BRAIN into: the root .env, the first of ENV_FILES
    (it wins over the hybrid .env, K-47). None only when no settings file is configured at all."""
    return ENV_FILES[0] if ENV_FILES else None


_BRAIN_LINE_PREFIXES = (f"{BRAIN_ENV}=", f"{BRAIN_ENV} =", f"export {BRAIN_ENV}=", f"export {BRAIN_ENV} =")


def _is_brain_line(line: str) -> bool:
    return line.strip().startswith(_BRAIN_LINE_PREFIXES)


def set_laptop_brain(brain: str, path: Path | None = None) -> None:
    """Write JARVIS_BRAIN=<brain> into the laptop's root .env in place: every other line (and its line ending)
    is kept byte for byte, every JARVIS_BRAIN line is replaced (dotenv reads the last one, so all of them must
    agree), and the line is appended when there was none. The file is replaced atomically. No value of the
    file is ever printed. A JARVIS_BRAIN in this process's environment is updated too, since it wins over the
    .env for current_brain() and is passed on to the workers this process starts.

    Raises BrainSwitchError when the file cannot be read or written (the switch then restores)."""
    if brain not in BRAINS:
        raise ValueError(f"brain must be one of {BRAINS}")
    target = path if path is not None else laptop_env_file()
    if target is None:
        raise BrainSwitchError("no laptop .env is configured, so JARVIS_BRAIN could not be written on this laptop")
    target = Path(target)
    try:
        raw = target.read_bytes() if target.is_file() else b""
    except OSError as exc:
        raise BrainSwitchError(f"could not read the laptop .env ({type(exc).__name__})") from None
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8", errors="surrogateescape") if bom else raw.decode("utf-8",
                                                                                   errors="surrogateescape")
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    wanted = f"{BRAIN_ENV}={brain}"
    found = False
    for index, line in enumerate(lines):
        if _is_brain_line(line):
            ending = line[len(line.rstrip("\r\n")):] or ""
            lines[index] = wanted + ending
            found = True
    if not found:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(wanted + newline)
    data = "".join(lines).encode("utf-8", errors="surrogateescape")
    if bom:
        data = b"\xef\xbb\xbf" + data
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(data)
        os.replace(temp, target)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise BrainSwitchError(f"could not write JARVIS_BRAIN to the laptop .env ({type(exc).__name__})") from None
    if (os.environ.get(BRAIN_ENV) or "").strip():
        os.environ[BRAIN_ENV] = brain


def pc_agent_name() -> str:
    return (_env_setting("JARVIS_PC_AGENT_NAME") or PC_AGENT_NAME_DEFAULT).strip() or PC_AGENT_NAME_DEFAULT


def brain_agent_name(brain: str) -> str:
    """The lk.agent.name a running brain registers: 'jarvis' for vps, JARVIS_PC_AGENT_NAME for pc."""
    return VPS_AGENT_NAME if brain == "vps" else pc_agent_name()


def _vps_mode_worker_refused(args: argparse.Namespace, action: str) -> tuple[int, dict[str, Any]] | None:
    """The two-runtimes rule: while JARVIS_BRAIN=vps the laptop target is the Desktop Node, so a PC worker
    start/restart is refused (K-72). Returns a refusal tuple, or None in pc mode. The message names the exact
    command that makes this laptop pc again: 'brain pc' only rewrites the laptop's JARVIS_BRAIN when the VPS
    already says pc (a laptop .env left at vps), else it switches the brain."""
    if current_brain() != "vps":
        return None
    by = getattr(args, "by", None) or "<you>"
    error = (f"JARVIS_BRAIN=vps: the laptop runs the Desktop Node, not a PC worker. Use 'agent_ctl node {action}'; "
             f"to make this laptop pc again run: {BRAIN_PC_COMMAND} --by {by} (when the VPS already says pc it only "
             "rewrites this laptop's JARVIS_BRAIN; otherwise it switches the brain, add --now to skip the 10 min "
             f"idle wait). If the VPS cannot be read, run: {BRAIN_PC_COMMAND} {LAPTOP_ONLY_FLAG} --by {by} (it "
             "rewrites only this laptop's JARVIS_BRAIN, journalled; no VPS read or command, nothing stopped or "
             "started)")
    if (os.environ.get(BRAIN_ENV) or "").strip().lower() == "vps":
        error += (f". {BRAIN_ENV}=vps is also set in this shell's environment, which wins over the .env: remove it "
                  "there too")
    print(f"[agent_ctl] {action.capitalize()} refused: {error}.", file=sys.stderr)
    return EXIT_REFUSED, {"result": "refused", "error": error, "brain": "vps"}


def take_brain_lock(args: argparse.Namespace) -> tuple[Path | None, str]:
    """run/brain.lock created with O_EXCL, so two 'agent_ctl brain' runs never drive a switch at once; the same
    stale rules as run/restart.lock (its process has exited, or older than BRAIN_LOCK_STALE_S)."""
    return _take_run_lock(BRAIN_LOCK_FILE, "brain switch", BRAIN_LOCK_STALE_S, args)


def brain_switch_running() -> str:
    """Who holds a live run/brain.lock, else ''."""
    path = agent_lock.run_dir() / BRAIN_LOCK_FILE
    if not path.exists():
        return ""
    stale, who = _run_lock_state(path, "brain switch", BRAIN_LOCK_STALE_S)
    return "" if stale else who


def _brain_switch_worker_refused(args: argparse.Namespace, action: str) -> tuple[int, dict[str, Any]] | None:
    """A worker start/restart while 'agent_ctl brain' runs is refused: the switch itself stops and starts the
    laptop side. The worker's own LiveKit link recovery ('restart --by worker', C-136) only replaces itself and
    is let through."""
    if getattr(args, "by", None) == "worker":
        return None
    who = brain_switch_running()
    if not who:
        return None
    error = f"a brain switch is running (run/{BRAIN_LOCK_FILE} is held by {who}); wait for it to finish"
    print(f"[agent_ctl] {action.capitalize()} refused: {error}.", file=sys.stderr)
    return EXIT_REFUSED, {"result": "refused", "error": error}


# --------------------------------------------------------------------------- the node health/stop server


def node_health_url() -> str:
    return f"http://{NODE_HEALTH_HOST}:{NODE_HEALTH_PORT}/health"


def node_stop_url() -> str:
    return f"http://{NODE_HEALTH_HOST}:{NODE_HEALTH_PORT}/stop"


def probe_node_health(timeout: float = 2.0) -> dict[str, Any] | None:
    """The Desktop Node supervisor's /health document, or None when nothing answers on 8082."""
    try:
        response = httpx.get(node_health_url(), timeout=_timeout(timeout), trust_env=False)
        data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        _drop_frames(exc)
        return None
    return data if isinstance(data, dict) else None


def node_running() -> bool:
    return probe_node_health(timeout=1.0) is not None


def _node_token() -> str | None:
    try:
        token = (agent_lock.run_dir() / NODE_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def node_stop_request(timeout: float = NODE_STOP_TIMEOUT_S) -> tuple[bool, str]:
    """Graceful POST /stop with the node token; (True, '') when the supervisor accepted and then went away."""
    token = _node_token()
    if token is None:
        return False, "run/desktop_node.token is missing (the supervisor is not serving 8082)"
    try:
        response = httpx.post(node_stop_url(), headers={"X-Node-Token": token}, timeout=_timeout(5.0),
                              trust_env=False)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        _drop_frames(exc)
        return False, "the supervisor did not answer POST /stop"
    if response.status_code != 200:
        return False, f"POST /stop was refused (HTTP {response.status_code})"
    if _wait_until(lambda: not node_running(), timeout):
        return True, ""
    return False, f"the node still answers /health {timeout:g} s after POST /stop"


# --------------------------------------------------------------------------- the node scheduled task


def node_python_w() -> Path:
    """The .venv_312 pythonw (no console) that the logon task runs, else this interpreter's pythonw."""
    if sys.platform == "win32":
        venv = ROOT / ".venv_312" / "Scripts" / "pythonw.exe"
        if venv.is_file():
            return venv
    return agent_python()


def node_task_action(snapshot: Path, *, canary: bool = False) -> tuple[str, str, str]:
    """The logon task's action as (program, arguments, working directory): the .venv_312 pythonw runs the
    snapshot's supervisor with the snapshot as its working directory, so 'src.core.desktop.supervisor' resolves
    there (the venv is not inside the snapshot). Task Scheduler starts pythonw itself: no cmd.exe or other console
    program runs, so no console window shows at logon. -B: the snapshot is read-only, so the supervisor never
    tries to write __pycache__ there (its node child gets PYTHONDONTWRITEBYTECODE=1 from the supervisor)."""
    arguments = f"-B -m {NODE_SUPERVISOR_MODULE}" + (" --canary" if canary else "")
    return str(node_python_w()), arguments, str(snapshot)


def xml_escape(text: str) -> str:
    """text for an XML element (&, < and > escaped; quotes are fine in element text)."""
    return _html_escape(text, quote=False)


def _task_user() -> str:
    """DOMAIN\\user of this logon (the account icacls grants the snapshot to), '' when unknown."""
    name = (os.environ.get("USERNAME") or "").strip()
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    return f"{domain}\\{name}" if name and domain else name


def node_task_xml(snapshot: Path, *, canary: bool = False, user: str | None = None) -> str:
    """The Task Scheduler definition of the node's logon task (K-76 Process rules): a logon trigger for this user,
    LeastPrivilege (never elevated) with the interactive token, and one Exec action with an explicit
    WorkingDirectory (node_task_action). Unlike schtasks' /SC defaults it has no 72 h run limit and also starts and
    keeps running on battery, since the node runs for the whole logon session."""
    program, arguments, workdir = node_task_action(snapshot, canary=canary)
    user = _task_user() if user is None else user
    user_id = f"<UserId>{xml_escape(user)}</UserId>" if user else ""
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-16"?>',
        f'<Task version="1.2" xmlns="{TASK_XML_NS}">',
        "  <RegistrationInfo><Description>JARVIS Desktop Node supervisor from the pinned read-only snapshot "
        "(agent_ctl node update)</Description></RegistrationInfo>",
        f"  <Triggers><LogonTrigger><Enabled>true</Enabled>{user_id}</LogonTrigger></Triggers>",
        '  <Principals><Principal id="Author">'
        f"{user_id}<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>"
        "</Principal></Principals>",
        "  <Settings>",
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
        "    <AllowHardTerminate>true</AllowHardTerminate>",
        "    <StartWhenAvailable>false</StartWhenAvailable>",
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>",
        "    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>",
        "    <AllowStartOnDemand>true</AllowStartOnDemand>",
        "    <Enabled>true</Enabled>",
        "    <Hidden>false</Hidden>",
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>",
        "    <WakeToRun>false</WakeToRun>",
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "    <Priority>7</Priority>",
        "  </Settings>",
        '  <Actions Context="Author"><Exec>'
        f"<Command>{xml_escape(program)}</Command><Arguments>{xml_escape(arguments)}</Arguments>"
        f"<WorkingDirectory>{xml_escape(workdir)}</WorkingDirectory></Exec></Actions>",
        "</Task>",
        ""])


def _schtasks(*args: str, check: bool = False) -> subprocess.CompletedProcess[Any]:
    cmd = ["schtasks", *args]
    try:
        result = _run(cmd, capture_output=True, text=True, timeout=60,
                      **({"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}))
    except (OSError, subprocess.SubprocessError) as exc:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))
    return result


def node_task_exists() -> bool:
    return _schtasks("/Query", "/TN", NODE_TASK_NAME).returncode == 0


def create_node_task(snapshot: Path, *, canary: bool = False) -> bool:
    """schtasks /Create /XML (node_task_xml: logon trigger, limited rights, interactive token, the snapshot as the
    working directory, no console program) replacing an existing task (/F). True on success. The definition goes
    through a temporary file in the run dir (paths and the user name only, no secret), removed right after."""
    program, _, workdir = node_task_action(snapshot, canary=canary)
    too_long = [f"{label} ({len(value)} characters)" for label, value in (("pythonw path", program),
                                                                          ("snapshot path", workdir))
                if len(value) > TASK_PATH_MAX]
    if too_long:
        print(f"[agent_ctl] The logon task's {' and '.join(too_long)} is longer than {TASK_PATH_MAX} characters, "
              "which Task Scheduler cannot run. Move the repo or %LOCALAPPDATA% to a shorter path.", file=sys.stderr)
        return False
    run_dir = agent_lock.run_dir()
    definition = run_dir / f".node_task.{os.getpid()}.xml"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        definition.write_bytes(node_task_xml(snapshot, canary=canary).encode("utf-16"))  # BOM + UTF-16, as declared
    except OSError as exc:
        print(f"[agent_ctl] Could not write the logon task definition ({type(exc).__name__}).", file=sys.stderr)
        return False
    try:
        result = _schtasks("/Create", "/TN", NODE_TASK_NAME, "/XML", str(definition), "/F")
    finally:
        try:
            definition.unlink(missing_ok=True)
        except OSError:
            pass
    return result.returncode == 0


def set_node_task_enabled(enabled: bool) -> bool:
    return _schtasks("/Change", "/TN", NODE_TASK_NAME, "/Enable" if enabled else "/Disable").returncode == 0


def run_node_task() -> bool:
    return _schtasks("/Run", "/TN", NODE_TASK_NAME).returncode == 0


def node_task_state() -> str | None:
    """'enabled', 'disabled' (or 'enabled, running' ...) from a read-only 'schtasks /Query /V'; None when the
    task does not exist. Only the state lines are read: the rest (the command line, the user) is never shown."""
    result = _schtasks("/Query", "/TN", NODE_TASK_NAME, "/FO", "LIST", "/V")
    if result.returncode != 0:
        return None
    state = status = ""
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "scheduled task state" and not state:
            state = value.lower()
        elif key == "status" and not status:
            status = value.lower()
    if not state:
        return "present"
    return state if not status or status == "disabled" else f"{state}, {status}"


# --------------------------------------------------------------------------- node start/stop/status/logs


def _node_current() -> dict[str, Any] | None:
    try:
        from scripts.desktop import node_snapshot  # noqa: E402  pylint: disable=import-outside-toplevel
    except Exception:  # pylint: disable=broad-exception-caught
        return _read_json(agent_lock.run_dir() / NODE_STATUS_FILE) or None
    return node_snapshot.read_current(agent_lock.run_dir())


def cmd_node(args: argparse.Namespace) -> int:
    handler = {"start": node_start, "stop": node_stop, "status": node_status, "logs": node_logs,
               "update": node_update}[args.node_command]
    return handler(args)


def node_start(args: argparse.Namespace) -> int:
    current = _node_current()
    if not current or not current.get("snapshot"):
        print("[agent_ctl] No pinned node snapshot yet: run 'agent_ctl node update' first "
              "(run/node_current.json is missing).", file=sys.stderr)
        audit("node_start", args, result="no_snapshot")
        return EXIT_FAILED
    if node_running():
        print("[agent_ctl] The Desktop Node already answers on 127.0.0.1:8082. Not starting a second one.")
        audit("node_start", args, result="already_running")
        return EXIT_OK
    if agent_lock.is_locked():
        # The two-runtimes rule (K-72): a PC worker holds run/agent.lock (and run/desktop_actor.lock), so the node
        # would only exit 3. The owner's worker is never stopped implicitly; 'brain vps' stops it first.
        error = ("a PC agent worker runs (run/agent.lock is held); stop it with 'agent_ctl stop' or switch with "
                 "'agent_ctl brain vps'")
        print(f"[agent_ctl] Node start refused: {error}.", file=sys.stderr)
        audit("node_start", args, result="refused", error=error)
        return EXIT_REFUSED
    if not node_task_exists():
        print(f"[agent_ctl] The '{NODE_TASK_NAME}' logon task is missing: run 'agent_ctl node update' to "
              "create it.", file=sys.stderr)
        audit("node_start", args, result="no_task")
        return EXIT_FAILED
    if not set_node_task_enabled(True):   # a previous 'node stop' left it disabled
        print(f"[agent_ctl] Warning: could not enable the '{NODE_TASK_NAME}' task; the node runs now but will "
              "not start at the next logon.", file=sys.stderr)
    if not run_node_task():
        print(f"[agent_ctl] schtasks could not start the '{NODE_TASK_NAME}' task.", file=sys.stderr)
        audit("node_start", args, result="failed", error="schtasks /Run failed")
        return EXIT_FAILED
    healthy = _wait_until(node_running, NODE_START_TIMEOUT_S)
    audit("node_start", args, result="started" if healthy else "failed",
          sha=current.get("short_sha") or current.get("sha"))
    if not healthy:
        print(f"[agent_ctl] The node did not answer /health within {NODE_START_TIMEOUT_S} s; see "
              "logs/desktop_supervisor.log.", file=sys.stderr)
        return EXIT_FAILED
    print("[agent_ctl] Desktop Node up (127.0.0.1:8082/health).")
    return EXIT_OK


def node_stop(args: argparse.Namespace) -> int:
    """'node stop', and the brain switch's vps laptop side (LaptopBrain), whose 'brain' arguments have no
    --timeout: the default NODE_STOP_TIMEOUT_S applies then."""
    if not node_running():
        print("[agent_ctl] The Desktop Node is not running.")
        set_node_task_enabled(False)  # keep it from re-launching at the next logon
        audit("node_stop", args, result="not_running")
        return EXIT_OK
    timeout = getattr(args, "timeout", None) or NODE_STOP_TIMEOUT_S
    ok_stop, why = node_stop_request(timeout)
    # The task stays disabled until the next 'node start' (C-123 step 2), so it does not come back at logon.
    disabled = set_node_task_enabled(False)
    audit("node_stop", args, result="stopped" if ok_stop else "failed", error=None if ok_stop else why,
          task_disabled=disabled)
    if not ok_stop:
        print(f"[agent_ctl] Could not stop the node gracefully: {why}.", file=sys.stderr)
        return EXIT_FAILED
    print("[agent_ctl] Desktop Node stopped; its logon task stays disabled until 'agent_ctl node start'.")
    return EXIT_OK


def node_status(_args: argparse.Namespace) -> int:
    current = _node_current()
    health = probe_node_health()
    if current:
        print(f"snapshot  {current.get('short_sha') or current.get('sha') or '?'} "
              f"(run mode {current.get('run_mode', '?')})")
    else:
        print("snapshot  none pinned (run 'agent_ctl node update')")
    task = node_task_state()
    print(f"task      {NODE_TASK_NAME}: {task or 'not created'}")
    print(f"brain     {current_brain()} (JARVIS_BRAIN on this laptop)")
    if health is None:
        print("node      NOT RUNNING (no answer on 127.0.0.1:8082)")
        return 1
    supervisor = health.get("supervisor") or {}
    print("node      RUNNING")
    print(f"  status  {health.get('status', '?')}")
    for key in ("mode", "locked", "panic", "link", "git_sha", "node_id"):
        if key in health:
            print(f"  {key:<6}{health[key]}")
    if supervisor:
        print(f"  pid     {supervisor.get('child_pid')} (supervisor {supervisor.get('pid')}, "
              f"restarts {supervisor.get('restarts')})")
    return 0


def node_logs(args: argparse.Namespace) -> int:
    for rel in ("logs/desktop_supervisor.log", "logs/desktop_node.stdio.log", "logs/desktop_node.log"):
        path = ROOT / rel
        print(f"===== {rel} (last {args.lines} lines) =====")
        if not path.is_file():
            print("(no such file)")
            continue
        for line in tail_lines(path, args.lines):
            print(line)
    return EXIT_OK


def _node_config_inputs() -> dict[str, Any]:
    """The non-secret node_config.json values from the root .env: the pinned K-70 public keys and the owner
    room. Secrets are never read here; 'update' wraps the tokens separately through DPAPI."""
    return {"agent_public_key": _env_setting("DESKTOP_AGENT_PUBLIC_KEY"),
            "pc_public_key": _env_setting("DESKTOP_PC_PUBLIC_KEY"),
            "node_id": _env_setting("JARVIS_NODE_ID"),
            "owner_room": _env_setting("JARVIS_ROOM_NAME")}


def node_update(args: argparse.Namespace) -> int:
    """Pin the deployed commit into a read-only snapshot, write node_config.json and the DPAPI-wrapped
    secrets, (re)create the logon task and restart the supervisor (C-123 steps 1-3)."""
    try:
        from scripts.desktop import node_snapshot  # noqa: E402  pylint: disable=import-outside-toplevel
        from services.utils import desktop_protocol as dp  # noqa: E402  pylint: disable=import-outside-toplevel
        from src.core.desktop import health as node_health  # noqa: E402  pylint: disable=import-outside-toplevel
    except ImportError as exc:
        print(f"[agent_ctl] node update needs the desktop modules: {exc}", file=sys.stderr)
        audit("node_update", args, result="failed", error="imports")
        return EXIT_FAILED
    run_mode = "canary" if getattr(args, "canary", False) else "active"
    restrict = node_health.restrict_to_current_user if sys.platform == "win32" else (lambda _p: None)
    try:
        plan = node_snapshot.update_snapshot(
            sha=getattr(args, "sha", None), data_root=ROOT, run_dir=agent_lock.run_dir(), run_mode=run_mode,
            protect=dp.dpapi_protect, restrict=restrict, runner=_run, **_node_config_inputs())
    except node_snapshot.SnapshotError as exc:
        print(f"[agent_ctl] node update failed: {exc}", file=sys.stderr)
        audit("node_update", args, result="failed", error=str(exc))
        return EXIT_FAILED
    created = create_node_task(plan.snapshot, canary=run_mode == "canary")
    # Restart the supervisor onto the new snapshot: a node that ran is stopped gracefully and started again; in
    # vps mode a stopped node is started too. In pc mode the laptop target is the PC worker (K-72), so a node that
    # was not running stays stopped and its logon task stays disabled (it would take run/desktop_actor.lock at the
    # next logon and the owner's worker would then exit 3); 'node start' or 'brain vps' enables it.
    was_running = node_running()
    brain = current_brain()
    if not created:
        # Without the task the node could not be started again: a running node is left alone on its old snapshot
        # (stopping it would leave the laptop without a node), and the update reports a failure.
        note = (f"the '{NODE_TASK_NAME}' logon task could not be (re)created, so "
                + ("the running node was left on its old snapshot" if was_running else "the node was not started"))
        print(f"[agent_ctl] node update failed: {note}. The snapshot {plan.sha[:12]} is pinned; fix schtasks and "
              "run 'agent_ctl node update' again.", file=sys.stderr)
        audit("node_update", args, result="failed", sha=plan.sha[:12], run_mode=run_mode, task=False,
              restarted=False, brain=brain, error="schtasks /Create failed", note=note)
        return EXIT_FAILED
    note = ""
    if was_running:
        stopped, why = node_stop_request(NODE_STOP_TIMEOUT_S)
        if not stopped:
            note = f"the running node did not stop ({why}); it still runs the old snapshot"
    want_start = (was_running or brain == "vps") and not note
    if want_start and agent_lock.is_locked():
        want_start, note = False, "a PC agent worker holds run/agent.lock, so the node was not started"
    restarted = False
    if node_task_exists():
        if want_start:
            set_node_task_enabled(True)
            restarted = run_node_task() and _wait_until(node_running, NODE_START_TIMEOUT_S)
        elif brain != "vps":
            set_node_task_enabled(False)
    audit("node_update", args, result="updated", sha=plan.sha[:12], run_mode=run_mode, task=created,
          restarted=restarted, brain=brain, **({"note": note} if note else {}))
    if restarted:
        tail = "Supervisor restarted." if was_running else "Supervisor started."
    elif note:
        tail = f"Not restarted: {note}."
    elif want_start:
        tail = ("The supervisor did not come up on the new snapshot; see logs/desktop_supervisor.log, then "
                "'agent_ctl node start'.")
    elif brain != "vps":
        tail = "pc mode: the node stays stopped and its logon task disabled until 'agent_ctl node start'."
    else:
        tail = "Start it with: agent_ctl node start."
    print(f"[agent_ctl] Pinned snapshot {plan.sha[:12]} ({run_mode}); node_config.json and secrets written. {tail}")
    print_highlight_node(args, plan.sha[:12], run_mode)
    return EXIT_OK


def print_highlight_node(args: argparse.Namespace, sha: str, run_mode: str) -> None:
    if getattr(args, "by", None) not in ("claude", "cursor"):
        return
    now = datetime.now(PKT)
    print()
    print("done_highlight.md entry (paste at the end of the file):")
    print(f"### ✅ [{args.by}] {_one_line(getattr(args, 'reason', '') or 'C-123', 60)} · Desktop Node update "
          f"(agent_ctl) · {now:%Y-%m-%d %H:%M} PKT")
    print(f"- **Kya kiya:** node snapshot {sha} pin kiya ({run_mode}); node_config.json + secrets.dpapi likhe, "
          "logon task set kiya.")
    print("- **Files:** —")
    print("- **Verify:** `.venv_312/Scripts/python.exe scripts/agent_ctl.py node status`")
    print(f"- **Commit:** abhi nahi · **Deploy:** node update {now:%H:%M} PKT")
    print("- **Doosre agent ke liye:** —")


# --------------------------------------------------------------------------- PC-signed bridge reads (K-70)


def link_base_url() -> str:
    """The public bridge base (JARVIS_LINK_URL or the default); trailing slash removed."""
    return (_env_setting("JARVIS_LINK_URL") or DEFAULT_LINK_URL).rstrip("/")


def _pc_signing_key() -> Any:
    """The node's Ed25519 PC key (keys.dpapi, DPAPI-unwrapped). Raises when it is missing or unreadable."""
    from services.utils import desktop_protocol as dp  # noqa: E402  pylint: disable=import-outside-toplevel
    from src.core.desktop import settings as node_settings  # noqa: E402  pylint: disable=import-outside-toplevel
    key_file = node_settings.node_home() / node_settings.KEYS_FILE
    return dp.load_pc_key_file(key_file)


def pc_signed_get(path: str, *, base: str | None = None, timeout: float = 8.0) -> dict[str, Any] | None:
    """GET a bodiless PC-signed (K-70 X-Desk-*) bridge route and return its JSON, or None on any failure.
    Used by the brain switch's idle gate and VERIFY (GET /vortex/status)."""
    from services.utils import desktop_protocol as dp  # noqa: E402  pylint: disable=import-outside-toplevel
    base = (base or link_base_url()).rstrip("/")
    try:
        key = _pc_signing_key()
        headers = dp.sign_http_request(key, method="GET", path=path, body=b"")
        response = httpx.get(f"{base}{path}", headers=headers, timeout=httpx.Timeout(timeout))
        return response.json() if response.status_code == 200 else None
    except (OSError, ValueError, httpx.HTTPError, httpx.InvalidURL) as exc:
        _drop_frames(exc)
        return None


def pc_signed_post(path: str, body: bytes = b"", *, base: str | None = None,
                   timeout: float = 8.0) -> tuple[int | None, dict[str, Any] | None]:
    """POST a PC-signed bridge route (the ensure call runs in pc mode); (status, json|None)."""
    from services.utils import desktop_protocol as dp  # noqa: E402  pylint: disable=import-outside-toplevel
    base = (base or link_base_url()).rstrip("/")
    try:
        key = _pc_signing_key()
        headers = {**dp.sign_http_request(key, method="POST", path=path, body=body),
                   "Content-Type": "application/json"}
        response = httpx.post(f"{base}{path}", headers=headers, content=body, timeout=httpx.Timeout(timeout))
        try:
            data = response.json()
        except ValueError:
            data = None
        return response.status_code, data if isinstance(data, dict) else None
    except (OSError, ValueError, httpx.HTTPError, httpx.InvalidURL) as exc:
        _drop_frames(exc)
        return None, None


def vortex_status(base: str | None = None) -> dict[str, Any] | None:
    """GET /vortex/status (PC-signed). The brain switch reads the brain, the agent participant and the idle
    timestamp from it."""
    return pc_signed_get("/vortex/status", base=base)


def _status_last_activity(status: Mapping[str, Any] | None) -> float | None:
    """last_activity_ts from /vortex/status (top level or the desktop block); None when absent."""
    if not status:
        return None
    for holder in (status, status.get("desktop") if isinstance(status.get("desktop"), Mapping) else None):
        if isinstance(holder, Mapping):
            value = holder.get("last_activity_ts")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def status_shows_brain(status: Mapping[str, Any] | None, target: str) -> bool:
    """True when /vortex/status reports the target brain AND an agent participant whose lk.agent.name is the
    target's and whose jarvis.session is running (K-72 success condition)."""
    if not status:
        return False
    desktop = status.get("desktop") if isinstance(status.get("desktop"), Mapping) else {}
    brain = status.get("brain") or desktop.get("brain")
    if brain != target:
        return False
    want = brain_agent_name(target)
    agent = status.get("agent") if isinstance(status.get("agent"), Mapping) else {}
    livekit = status.get("livekit") if isinstance(status.get("livekit"), Mapping) else {}
    candidates: list[Mapping[str, Any]] = [agent] if agent else []
    for participants in (status.get("participants"), livekit.get("participants")):
        if isinstance(participants, list):
            candidates += [p for p in participants if isinstance(p, Mapping)]
    for part in candidates:
        # A participant's own fields, else its LiveKit attributes. Only an agent-name field counts: a plain
        # display name or identity is never taken for lk.agent.name.
        attrs = part.get("attributes") if isinstance(part.get("attributes"), Mapping) else {}
        name = next((src.get(key) for src in (part, attrs)
                     for key in ("lk.agent.name", "agent_name", "worker_agent_name") if src.get(key)), None)
        session = next((src.get(key) for src in (part, attrs)
                        for key in ("jarvis.session", "session") if src.get(key)), None)
        if name == want and str(session).lower() == "running":
            return True
    return False


# --------------------------------------------------------------------------- the brain switch (K-72)


class BrainSwitchError(RuntimeError):
    """A brain-switch step failed; the switch then restores the previous brain."""


class BrainSwitchRefused(BrainSwitchError):
    """PREFLIGHT refused the switch: nothing was backed up, stopped or changed, so nothing is restored."""

    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = list(missing or [])


class LaptopBrain:
    """The laptop side of each brain, so the switch can start/stop/ensure without caring which it is. The
    tests inject a fake; the real one drives the node (vps) and the PC worker (pc) through this module."""

    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args

    def start(self, brain: str) -> int:
        if brain == "vps":
            return node_start(self._args)
        code, _ = start_worker(self._args, "start", foreground=False)
        return EXIT_OK if code == EXIT_OK else EXIT_FAILED

    def stop(self, brain: str) -> int:
        if brain == "vps":
            # The 'brain' arguments carry no --timeout; node_stop then uses NODE_STOP_TIMEOUT_S. A graceful POST
            # /stop, and the logon task disabled so the node cannot take run/desktop_actor.lock at the next logon.
            return node_stop(self._args)
        code, _ = stop_worker(STOP_TIMEOUT_S)
        return EXIT_OK if code == EXIT_OK else EXIT_FAILED

    def ensure(self, brain: str) -> int:
        if brain != "pc":
            return EXIT_OK  # the VPS dispatches its own job; ensure_room runs in pc mode only (C-123 step 5)
        # ensure_room.py asks the PC-signed /vortex/desktop/ensure first and falls back to the LiveKit API; it
        # never deletes a room. It runs with the env the worker got, so it sees JARVIS_BRAIN as pc.
        env = agent_env(getattr(self._args, "by", "user"), _one_line(getattr(self._args, "reason", "") or ""),
                        background=True)
        env[BRAIN_ENV] = "pc"
        return EXIT_OK if run_ensure_room(env) == 0 else EXIT_FAILED


def _default_ssh(command: list[str]) -> subprocess.CompletedProcess[Any]:
    """Run a command on the VPS over ssh, never interactively (BatchMode: a missing key fails at once instead of
    asking for a password). The target is JARVIS_VPS_SSH ('user@host' or an ssh alias), else JARVIS_VPS_HOST as
    deploy_from_pc.sh reads it; the key is JARVIS_SSH_KEY when set, else ssh's own configuration."""
    target = _env_setting("JARVIS_VPS_SSH") or _env_setting("JARVIS_VPS_HOST") or ""
    if not target:
        raise BrainSwitchError("JARVIS_VPS_SSH is not set: cannot reach the VPS for the brain switch")
    key = _env_setting("JARVIS_SSH_KEY")
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=20"]
    if key:
        options = ["-i", os.path.expanduser(key), *options]
    # ssh joins its arguments into one string for the remote shell, so the argv goes as ONE quoted argument:
    # ['bash', '-lc', 'cd X && Y'] then really runs 'cd X && Y' inside bash -lc.
    return _run(["ssh", *options, target, shlex.join(command)], capture_output=True, text=True, timeout=600)


def _vps_shell(script: str) -> list[str]:
    """The argv of one shell script on the VPS; _default_ssh sends it to ssh as a single quoted argument."""
    return ["bash", "-lc", script]


def _vps_compose(*args: str) -> str:
    """docker compose for the VPS brain services the way deploy.sh runs compose: project 'jarvis', the compose
    file and its .env in deploy/hostinger, plus the vps-brain profile that holds the agent services."""
    return (f"cd {VPS_COMPOSE_DIR} && docker compose -p {VPS_COMPOSE_PROJECT} --profile {VPS_BRAIN_PROFILE} "
            + " ".join(shlex.quote(arg) for arg in args))


def _vps_helper(path: str, *args: str) -> str:
    """A helper of the VPS checkout (VPS_HELPERS): a .py through python3, a shell script as ./<path>."""
    runner = "python3 " if path.endswith(".py") else "./"
    return f"cd {VPS_APP_DIR} && {runner}{path}" + "".join(" " + shlex.quote(arg) for arg in args)


def vps_provision_script() -> str:
    """A read-only check run on the VPS at PREFLIGHT. It prints 'missing:<item>' for the checkout and for each
    helper of VPS_HELPERS that is absent or lacks its permission, 'service:<name>' for each compose service the
    vps-brain profile defines, and 'missing:<compose config>' when compose cannot read its file and .env (it reads
    JARVIS_BRAIN as ${JARVIS_BRAIN:?}). It changes nothing and always exits 0 once it ran."""
    lines = [f'cd {VPS_APP_DIR} 2>/dev/null || {{ echo "missing:{VPS_APP_DIR}"; exit 0; }}']
    lines += [f'{{ [ -f {path} ] && [ {flag} {path} ]; }} || echo "missing:{path}"' for path, flag in VPS_HELPERS]
    lines.append(f"if out=$(cd {VPS_COMPOSE_DIR} && docker compose -p {VPS_COMPOSE_PROJECT} --profile "
                 f"{VPS_BRAIN_PROFILE} config --services 2>/dev/null); then "
                 "printf '%s\\n' \"$out\" | sed -n 's/^\\(..*\\)$/service:\\1/p'; "
                 "else echo \"missing:a docker compose config that compose accepts (deploy/hostinger)\"; fi")
    lines.append("exit 0")
    return "\n".join(lines)


def parse_vps_provision(stdout: str) -> tuple[list[str], dict[str, str]]:
    """(the missing items, {container: its compose service}) from vps_provision_script's output. A brain
    container without a compose service in the vps-brain profile is missing too."""
    missing: list[str] = []
    services: set[str] = set()
    for line in (stdout or "").splitlines():
        kind, _, value = line.strip().partition(":")
        if kind == "missing" and value.strip():
            missing.append(value.strip())
        elif kind == "service" and value.strip():
            services.add(value.strip())
    resolved: dict[str, str] = {}
    for container, names in VPS_BRAIN_CONTAINERS:
        found = next((name for name in names if name in services), None)
        if found is None:
            missing.append(f"compose service {container}")
        else:
            resolved[container] = found
    return missing, resolved


class BrainSwitch:
    """PREFLIGHT -> BACKUP -> STOP_OLD -> SYNC_DATA -> FLIP_ENV -> START_NEW -> ENSURE -> VERIFY -> DONE,
    journalled in run/brain_switch.json and resumable.

    - PREFLIGHT changes nothing. It checks that the VPS is provisioned for the switch (the checkout, every helper of
      VPS_HELPERS, the two brain services in compose), that the VPS brain is readable, the node snapshot (for vps),
      that the PC-signed /vortex/status already shows the current brain and its running agent (the condition VERIFY
      and the restore check need) and last the idle gate (or the --now notice). A PREFLIGHT problem is a refusal
      (exit 4): it is journalled as such, nothing is restored because nothing changed. A journal left at PREFLIGHT
      starts over with a full PREFLIGHT.
    - A failure at BACKUP changes nothing either (exit 1, no restore).
    - From STOP_OLD on the old brain may be down, so a failure restores the previous brain automatically, whatever
      the error (an unexpected exception too; only KeyboardInterrupt/SystemExit leave the journal for a resume).
    - A journal of a switch to the OTHER brain that stopped half-way blocks a new switch (exit 4) until that one is
      resumed with 'agent_ctl brain <its target>', which finishes it or restores the previous brain.

    - FLIP_ENV writes JARVIS_BRAIN on the VPS .env and then in the laptop's root .env (set_laptop_brain), so the
      laptop agrees (a PC worker start is refused once it says vps). The restore writes the previous brain back to
      both, and a resume past FLIP_ENV re-applies the laptop value before it goes on.
    - When the VPS already says the target, both sides are compared: equal is 'already' (nothing to switch); a
      laptop that differs is repaired alone (its JARVIS_BRAIN written, journalled; no VPS command, nothing stopped
      or started), so 'brain pc' frees the owner's worker from a laptop .env left at vps.

    ssh, status, laptop, laptop_env and laptop_brain are injected (the tests pass fakes or a tmp .env), so no real
    VPS command, container, scheduled task, /vortex call or laptop .env write runs under test. Success is declared
    only when VERIFY sees the target brain and its agent participant (status_shows_brain)."""

    def __init__(self, target: str, *, journal_path: Path, ssh: Callable[[list[str]], Any],
                 status: Callable[[], dict[str, Any] | None], laptop: LaptopBrain,
                 notify: Callable[[str], None] | None = None, when_idle: bool = False, now_force: bool = False,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] | None = None,
                 laptop_env: Callable[[str], None] | None = None,
                 laptop_brain: Callable[[], str] | None = None) -> None:
        if target not in BRAINS:
            raise ValueError(f"brain must be one of {BRAINS}")
        self.target = target
        self.journal_path = Path(journal_path)
        self._ssh, self._status, self._laptop = ssh, status, laptop
        self._notify = notify or (lambda _m: None)
        # The laptop's JARVIS_BRAIN writer and reader; looked up at call time so a test's patched function applies.
        self._laptop_env = laptop_env or (lambda brain: set_laptop_brain(brain))  # pylint: disable=unnecessary-lambda
        self._laptop_brain = laptop_brain or (lambda: current_brain())  # pylint: disable=unnecessary-lambda
        self._when_idle, self._now_force = when_idle, now_force
        self._clock = clock
        self._sleep = sleep or _sleep
        self.journal: dict[str, Any] = {}

    # --- journal ---------------------------------------------------------------------------------------------

    def _load_journal(self) -> dict[str, Any]:
        """The journal of an unfinished switch to this target; {} for none, another target or a refusal record."""
        data = _read_json(self.journal_path)
        if data.get("target") != self.target or data.get("result") == "refused":
            return {}
        return data

    def _other_switch_in_progress(self) -> str:
        """Why a switch to the other brain must be finished first, else ''. Only a journal past PREFLIGHT counts
        (from BACKUP on STOP_OLD may have begun), or one that was restoring."""
        data = _read_json(self.journal_path)
        other = data.get("target")
        if other in (None, self.target) or data.get("result") == "refused":
            return ""
        state = data.get("state")
        begun = state in BRAIN_STATES and BRAIN_STATES.index(state) >= BRAIN_STATES.index("BACKUP")
        if not (begun or data.get("restoring")):
            return ""
        how = "while restoring" if data.get("restoring") else f"after {state}"
        return (f"a switch to '{other}' stopped half-way ({how}, run/{BRAIN_JOURNAL_FILE}); run 'agent_ctl brain "
                f"{other}' first: it finishes that switch or restores the previous brain")

    def _save(self, **fields: Any) -> None:
        self.journal.update(fields, updated_at=self._clock())
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.journal_path.write_text(json.dumps(self.journal, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            print(f"[agent_ctl] Warning: could not write run/{BRAIN_JOURNAL_FILE}: {exc}", file=sys.stderr)

    def _clear(self) -> None:
        try:
            self.journal_path.unlink(missing_ok=True)
        except OSError:
            pass

    # --- the run ---------------------------------------------------------------------------------------------

    def run(self) -> tuple[int, dict[str, Any]]:
        other = self._other_switch_in_progress()
        if other:
            print(f"[agent_ctl] Brain switch refused: {other}.", file=sys.stderr)
            return EXIT_REFUSED, {"result": "refused", "state": None, "error": other}
        self.journal = self._load_journal()
        if self.journal.get("restoring"):
            return self._restore("resumed a restore from the journal")
        if self.journal.get("state") in (None, "PREFLIGHT"):
            # Nothing was backed up, stopped or changed yet (a crash at or right after PREFLIGHT), so the switch
            # starts over with a full PREFLIGHT: the provisioning, status and idle checks and the --now notice.
            self.journal = {}
        previous = self.journal.get("previous") or self._read_vps_brain()
        if previous == self.target and not self.journal:
            return self._settle_laptop()
        if not self.journal:
            self.journal = {"target": self.target, "previous": previous, "state": None,
                            "started_at": self._clock()}
            self._save()
        done = self.journal.get("state")
        start_index = BRAIN_STATES.index(done) + 1 if done in BRAIN_STATES else 0
        if start_index > BRAIN_STATES.index("FLIP_ENV"):
            # A resume past FLIP_ENV: make sure the laptop still says the target (the .env may have been edited or
            # restored by hand since), so the laptop readers agree with the VPS before START_NEW goes on.
            try:
                self._laptop_env(self.target)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the old brain is down: restore
                why = str(exc) if isinstance(exc, BrainSwitchError) else f"unexpected {type(exc).__name__}"
                print(f"[agent_ctl] brain switch failed re-applying FLIP_ENV on resume: {why}", file=sys.stderr)
                return self._restore(f"failed at FLIP_ENV (resume): {why}")
        steps: dict[str, Callable[[], None]] = {
            "PREFLIGHT": self._preflight, "BACKUP": self._backup, "STOP_OLD": self._stop_old,
            "SYNC_DATA": self._sync_data, "FLIP_ENV": self._flip_env, "START_NEW": self._start_new,
            "ENSURE": self._ensure, "VERIFY": self._verify, "DONE": lambda: None}
        for state in BRAIN_STATES[start_index:]:
            try:
                print(f"[agent_ctl] brain {self.target}: {state}...", flush=True)
                steps[state]()
            except BrainSwitchRefused as exc:
                return self._refuse(state, exc)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # never skip the restore
                # A BrainSwitchError says what failed. Any other error is a bug or an unexpected condition; only
                # its type is shown (its text may hold a URL or a path). KeyboardInterrupt and SystemExit are not
                # caught: the journal keeps the last finished state and the next run resumes from it.
                why = str(exc) if isinstance(exc, BrainSwitchError) else f"unexpected {type(exc).__name__}"
                print(f"[agent_ctl] brain switch failed at {state}: {why}", file=sys.stderr)
                if BRAIN_STATES.index(state) < BRAIN_STATES.index("STOP_OLD"):
                    return self._fail_unchanged(state, why)
                return self._restore(f"failed at {state}: {why}")
            self._save(state=state)
        self._clear()
        print(f"[agent_ctl] Brain switched to '{self.target}'.")
        return EXIT_OK, {"result": "switched", "brain": self.target}

    def _settle_laptop(self) -> tuple[int, dict[str, Any]]:
        """The VPS .env already says the target. 'Nothing to switch' only when this laptop's JARVIS_BRAIN says it
        too; when only the laptop differs (e.g. a .env left at vps while the VPS says pc, so the owner's worker is
        refused), this repairs the laptop side alone: its JARVIS_BRAIN is written (journalled like a switch), no
        VPS command runs and nothing is stopped or started."""
        laptop = self._laptop_brain()
        if laptop == self.target:
            print(f"[agent_ctl] JARVIS_BRAIN is already '{self.target}' on the VPS and on this laptop. Nothing to "
                  "switch.")
            return EXIT_OK, {"result": "already", "brain": self.target}
        print(f"[agent_ctl] JARVIS_BRAIN on the VPS is already '{self.target}' but this laptop says '{laptop}': "
              "repairing only this laptop's JARVIS_BRAIN (no VPS command; nothing is stopped or started).",
              flush=True)
        return self._repair_laptop(laptop, previous=self.target)

    def repair_laptop_only(self) -> tuple[int, dict[str, Any]]:
        """'brain pc --laptop-only': rewrites only this laptop's JARVIS_BRAIN line, without reading the VPS (for a
        laptop .env left at vps while the VPS cannot be reached, which would refuse the owner's worker forever). It
        is journalled like the repair in _settle_laptop: no ssh, no /vortex call, nothing stopped or started. A
        half-done switch in the journal is never overwritten: it is refused (exit 4) until that switch is finished
        or restored with 'agent_ctl brain <its target>'."""
        other = self._other_switch_in_progress()
        unfinished = self._load_journal()
        state = unfinished.get("state")
        # A journal at PREFLIGHT (or none) changed nothing and starts over anyway; from BACKUP on it must be kept.
        begun = state in BRAIN_STATES and BRAIN_STATES.index(state) >= BRAIN_STATES.index("BACKUP")
        if not other and (unfinished.get("restoring") or begun):
            how = "while restoring" if unfinished.get("restoring") else f"after {state}"
            other = (f"a switch to '{self.target}' stopped half-way ({how}, run/{BRAIN_JOURNAL_FILE}); run "
                     f"'agent_ctl brain {self.target}' to finish it")
        if other:
            error = f"{LAPTOP_ONLY_FLAG} would overwrite the journal of an unfinished switch: {other}"
            print(f"[agent_ctl] Laptop-only repair refused: {error}.", file=sys.stderr)
            return EXIT_REFUSED, {"result": "refused", "state": None, "error": error, "laptop_only": True}
        laptop = self._laptop_brain()
        if laptop == self.target:
            print(f"[agent_ctl] This laptop's JARVIS_BRAIN is already '{self.target}'. Nothing to change (the VPS was "
                  "not read).")
            return EXIT_OK, {"result": "already", "brain": self.target, "laptop_only": True}
        print(f"[agent_ctl] {LAPTOP_ONLY_FLAG}: this laptop says '{laptop}'; rewriting only its JARVIS_BRAIN to "
              f"'{self.target}' (the VPS is not read; no VPS command; nothing is stopped or started).", flush=True)
        code, details = self._repair_laptop(laptop, previous=None, laptop_only=True)
        if code == EXIT_OK:
            print("[agent_ctl] The VPS .env was not checked: once the VPS answers, run "
                  f"'{BRAIN_PC_COMMAND} --by <you>' to bring both sides in line.")
        return code, details

    def _repair_laptop(self, laptop: str, *, previous: str | None,
                       laptop_only: bool = False) -> tuple[int, dict[str, Any]]:
        """Writes the target into this laptop's JARVIS_BRAIN (journalled), then reads it back: the repair counts
        only when this laptop now reads the target (a JARVIS_BRAIN in this shell's environment, or a later .env
        line, can still say otherwise)."""
        extra: dict[str, Any] = {"laptop_only": True} if laptop_only else {}
        in_shell = bool((os.environ.get(BRAIN_ENV) or "").strip())
        self.journal = {"target": self.target, "previous": previous, "state": None, "repair": "laptop",
                        "laptop_was": laptop, "started_at": self._clock(), **extra}
        self._save()
        try:
            self._laptop_env(self.target)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # reported, nothing else was changed
            why = str(exc) if isinstance(exc, BrainSwitchError) else f"unexpected {type(exc).__name__}"
            print(f"[agent_ctl] Could not repair this laptop's JARVIS_BRAIN: {why}. Nothing else was changed.",
                  file=sys.stderr)
            self._clear()
            return EXIT_FAILED, {"result": "failed", "state": "REPAIR_LAPTOP", "error": why, "changed": False,
                                 "brain": self.target, "laptop_was": laptop, **extra}
        self._clear()
        now = self._laptop_brain()
        if now != self.target:
            error = f"this laptop still reads JARVIS_BRAIN as '{now}' after the repair"
            print(f"[agent_ctl] {error}.", file=sys.stderr)
            return EXIT_FAILED, {"result": "failed", "state": "REPAIR_LAPTOP", "error": error, "brain": self.target,
                                 "laptop_was": laptop, **extra}
        if laptop_only:
            print(f"[agent_ctl] This laptop's JARVIS_BRAIN is now '{self.target}' (the VPS was not read, so whether "
                  "it matches the VPS is unknown).")
        else:
            print(f"[agent_ctl] This laptop's JARVIS_BRAIN is now '{self.target}', matching the VPS.")
        if in_shell:
            print(f"[agent_ctl] Note: {BRAIN_ENV} is also set in this shell's environment, which wins over the .env: "
                  f"set it to '{self.target}' or remove it there too.", file=sys.stderr)
        if self.target == "pc":
            print("[agent_ctl] Next: if the Desktop Node still runs, stop it with 'agent_ctl node stop'; then start "
                  "the PC worker with 'agent_ctl start --by <you>'.")
        else:
            print("[agent_ctl] Next: if a PC worker still runs, stop it with 'agent_ctl stop --by <you>'; then start "
                  "the Desktop Node with 'agent_ctl node start'.")
        return EXIT_OK, {"result": "repaired", "repaired": "laptop", "brain": self.target, "laptop_was": laptop,
                         **extra}

    def _refuse(self, state: str, exc: BrainSwitchRefused) -> tuple[int, dict[str, Any]]:
        """PREFLIGHT said no: nothing was backed up, stopped or changed. The journal keeps the refusal as a record
        (never resumed: the next run starts a fresh PREFLIGHT)."""
        message = str(exc)
        print(f"[agent_ctl] Brain switch refused at {state}: {message}. Nothing was backed up, stopped or "
              "changed.", file=sys.stderr)
        previous = self.journal.get("previous")
        self.journal = {"target": self.target, "previous": previous, "state": None, "result": "refused",
                        "refused_at": state, "error": message, "missing": exc.missing,
                        "started_at": self.journal.get("started_at", self._clock())}
        self._save()
        details: dict[str, Any] = {"result": "refused", "state": state, "error": message, "brain": previous}
        if exc.missing:
            details["missing"] = exc.missing
        if self.target == "pc" and self._laptop_brain() == "vps":
            print(f"[agent_ctl] This laptop still says JARVIS_BRAIN=vps, which refuses the PC worker. If the VPS "
                  f"cannot be read, '{BRAIN_PC_COMMAND} {LAPTOP_ONLY_FLAG} --by <you>' rewrites only this laptop's "
                  "JARVIS_BRAIN (no VPS read or command).", file=sys.stderr)
        return EXIT_REFUSED, details

    def _fail_unchanged(self, state: str, why: str) -> tuple[int, dict[str, Any]]:
        """A failure before STOP_OLD: the old brain still runs and JARVIS_BRAIN is unchanged, so no restore."""
        previous = self.journal.get("previous")
        print(f"[agent_ctl] Nothing was stopped or changed; '{previous}' is still the brain.", file=sys.stderr)
        self._clear()
        return EXIT_FAILED, {"result": "failed", "state": state, "error": why, "changed": False, "brain": previous}

    # --- states ----------------------------------------------------------------------------------------------

    def _preflight(self) -> None:
        """Every check that can refuse comes before the --now notice, so a refused switch is never announced."""
        self._check_vps_provisioned()
        previous = self.journal.get("previous")
        if previous not in BRAINS:
            previous = self._read_vps_brain()
            if previous not in BRAINS:
                raise BrainSwitchRefused("JARVIS_BRAIN could not be read from the VPS .env ('set_env.py --get "
                                         "JARVIS_BRAIN' gave neither vps nor pc), so a failure could not be restored")
            self._save(previous=previous)
        if previous == self.target:
            raise BrainSwitchRefused(f"JARVIS_BRAIN on the VPS is already '{self.target}'")
        # The node needs a pinned snapshot and its logon task (a read-only schtasks /Query) before it can be the
        # laptop target; without the task START_NEW could not start it after STOP_OLD stopped the owner's worker.
        if self.target == "vps" and not _node_current():
            raise BrainSwitchRefused("no pinned node snapshot: run 'agent_ctl node update' before 'brain vps'")
        if self.target == "vps" and not node_task_exists():
            raise BrainSwitchRefused(f"the '{NODE_TASK_NAME}' logon task is missing: run 'agent_ctl node update' "
                                     "(it creates the task) before 'brain vps'")
        status = self._status()
        if status is None:
            raise BrainSwitchRefused("the PC-signed /vortex/status does not answer (JARVIS_LINK_URL, the node's PC "
                                     "key), so VERIFY could never confirm the switch")
        # VERIFY and the restore's own check both need the K-72 condition (status_shows_brain), so it must already
        # hold for the current brain. Otherwise the switch would stop the owner's brain, spend VERIFY failing and
        # then restore without being able to confirm it: refuse now, before anything is backed up or stopped.
        if not status_shows_brain(status, previous):
            raise BrainSwitchRefused(self._status_gap(status, previous))
        if self._when_idle or self._now_force:
            self._idle_gate(status)

    @staticmethod
    def _status_gap(status: Mapping[str, Any], previous: str) -> str:
        """Why /vortex/status does not confirm the current brain (PREFLIGHT's refusal message)."""
        desktop = status.get("desktop") if isinstance(status.get("desktop"), Mapping) else {}
        shown = status.get("brain") or desktop.get("brain")
        tail = "so neither VERIFY nor a restore could ever confirm a switch"
        if shown not in BRAINS:
            return f"/vortex/status does not report the brain field yet (contracts v4, K-72), {tail}"
        if shown != previous:
            return (f"/vortex/status reports brain '{shown}' but the VPS .env says '{previous}'; settle that first, "
                    f"{tail}")
        return (f"/vortex/status shows brain '{previous}' but no agent participant whose lk.agent.name is "
                f"'{brain_agent_name(previous)}' with jarvis.session running (the bridge does not report those "
                f"agent fields yet, or the current brain is not running), {tail}")

    def _check_vps_provisioned(self) -> None:
        """The VPS-side prerequisites every later state needs (C-126..C-129); refuses with the full list."""
        try:
            result = self._ssh(_vps_shell(vps_provision_script()))
        except BrainSwitchError as exc:
            raise BrainSwitchRefused(str(exc)) from None
        except Exception as exc:  # pylint: disable=broad-exception-caught  # ssh itself: refused, never a crash
            raise BrainSwitchRefused(f"the VPS could not be reached over ssh ({type(exc).__name__})") from None
        code = getattr(result, "returncode", 1)
        if code != 0:
            raise BrainSwitchRefused(f"the VPS provisioning check exited {code} (is the VPS reachable over ssh?)")
        missing, services = parse_vps_provision(getattr(result, "stdout", "") or "")
        if missing:
            raise BrainSwitchRefused(f"VPS brain not provisioned yet: missing {', '.join(missing)} "
                                     f"({VPS_PROVISIONED_BY})", missing=missing)
        self._save(vps_services=[services[container] for container, _names in VPS_BRAIN_CONTAINERS])

    def _idle_gate(self, status: Mapping[str, Any] | None) -> None:
        if self._now_force:
            self._notify(f"JARVIS brain switch to {self.target} in {SPOKEN_NOTICE_S} seconds.")
            self._sleep(SPOKEN_NOTICE_S)
            return
        last = _status_last_activity(status)
        idle_for = None if last is None else self._clock() - last
        if idle_for is None or idle_for < IDLE_GATE_S:
            waited = "unknown" if idle_for is None else f"{idle_for:.0f} s"
            raise BrainSwitchRefused(f"not idle for {IDLE_GATE_S} s yet (last activity {waited} ago); wait or "
                                     "use --now")

    def _services(self) -> list[str]:
        services = self.journal.get("vps_services")
        return list(services) if isinstance(services, list) and services else list(VPS_DEFAULT_SERVICES)

    def _backup(self) -> None:
        self._ssh_step(_vps_shell(_vps_helper("deploy/hostinger/backup.sh")), "BACKUP")

    def _stop_old(self) -> None:
        previous = self.journal.get("previous")
        if previous == "pc":
            if self._laptop.stop("pc") != EXIT_OK:
                raise BrainSwitchError("the PC worker did not stop")
        elif previous == "vps":
            if self._laptop.stop("vps") != EXIT_OK:
                raise BrainSwitchError("the Desktop Node did not stop")
            self._ssh_step(_vps_shell(_vps_compose("stop", *self._services())), "STOP_OLD")

    def _sync_data(self) -> None:
        self._ssh_step(_vps_shell(_vps_helper("deploy/hostinger/sync_brain_data.sh", self.target)), "SYNC_DATA")

    def _flip_env(self) -> None:
        # Edit JARVIS_BRAIN in place on the VPS .env, keeping every other line (K-72 env writer), then the laptop's
        # root .env the same way, so current_brain(), vortex.py, ensure_room.py and the launchers agree.
        self._ssh_step(_vps_shell(_vps_helper("deploy/hostinger/set_env.py", BRAIN_ENV, self.target)), "FLIP_ENV")
        self._laptop_env(self.target)

    def _start_new(self) -> None:
        if self.target == "vps":
            self._ssh_step(_vps_shell(_vps_compose("up", "-d", *self._services())), "START_NEW")
            if self._laptop.start("vps") != EXIT_OK:
                raise BrainSwitchError("the Desktop Node did not start")
        else:
            self._ssh_step(_vps_shell(_vps_compose("stop", *self._services())), "START_NEW")
            if self._laptop.start("pc") != EXIT_OK:
                raise BrainSwitchError("the PC worker did not start")

    def _ensure(self) -> None:
        if self._laptop.ensure(self.target) != EXIT_OK:
            raise BrainSwitchError("the room could not be ensured")

    def _verify(self) -> None:
        if not self._wait_for_brain(self.target):
            raise BrainSwitchError(f"/vortex/status never showed brain '{self.target}' with a running "
                                   f"'{brain_agent_name(self.target)}' agent")

    def _wait_for_brain(self, brain: str) -> bool:
        for attempt in range(VERIFY_TRIES):
            if status_shows_brain(self._status(), brain):
                return True
            if attempt < VERIFY_TRIES - 1:
                self._sleep(VERIFY_STEP_S)
        return False

    # --- restore ---------------------------------------------------------------------------------------------

    def _restore(self, why: str) -> tuple[int, dict[str, Any]]:
        """Bring the previous brain back: the new laptop side down, JARVIS_BRAIN back (the VPS .env, then the
        laptop's root .env), the old side up. Every
        action runs even when one before it failed; the journal says 'restoring' so a crash resumes the restore."""
        previous = self.journal.get("previous")
        if previous not in BRAINS or previous == self.target:
            self._clear()
            return EXIT_FAILED, {"result": "failed", "error": why, "restored": False}
        self._save(restoring=True)
        print(f"[agent_ctl] Restoring the previous brain '{previous}' ({why})...", file=sys.stderr)
        services = self._services()
        actions: list[tuple[str, Callable[[], Any]]] = [
            (f"stop the {self.target} laptop side", lambda: self._laptop.stop(self.target)),
            ("restore JARVIS_BRAIN", lambda: self._ssh(_vps_shell(
                _vps_helper("deploy/hostinger/set_env.py", BRAIN_ENV, previous)))),
            ("restore JARVIS_BRAIN on this laptop", lambda: self._laptop_env(previous))]
        if previous == "vps":
            actions += [("start the VPS brain", lambda: self._ssh(_vps_shell(_vps_compose("up", "-d", *services)))),
                        ("start the Desktop Node", lambda: self._laptop.start("vps"))]
        else:
            actions += [("stop the VPS brain", lambda: self._ssh(_vps_shell(_vps_compose("stop", *services)))),
                        ("start the PC worker", lambda: self._laptop.start("pc")),
                        ("ensure the room", lambda: self._laptop.ensure("pc"))]
        for label, action in actions:
            try:
                outcome = action()
            except Exception as exc:  # pylint: disable=broad-exception-caught  # restore must always finish
                detail = str(exc) if isinstance(exc, BrainSwitchError) else type(exc).__name__
                print(f"[agent_ctl] WARNING: restore could not {label}: {detail}", file=sys.stderr)
                continue
            # A laptop step returns an exit code, an ssh step a CompletedProcess: a non-zero one is reported too
            # (e.g. a node that would not stop keeps run/desktop_actor.lock, so the PC worker cannot start).
            code = getattr(outcome, "returncode", outcome)
            if isinstance(code, int) and code != EXIT_OK:
                print(f"[agent_ctl] WARNING: restore could not {label}: exit {code}", file=sys.stderr)
        restored = self._wait_for_brain(previous)
        self._clear()
        return EXIT_FAILED, {"result": "failed", "error": why, "restored": restored, "brain": previous}

    # --- helpers ---------------------------------------------------------------------------------------------

    def _ssh_step(self, command: list[str], state: str) -> None:
        try:
            result = self._ssh(command)
        except BrainSwitchError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise BrainSwitchError(f"ssh for {state} failed ({type(exc).__name__})") from None
        code = getattr(result, "returncode", 1)
        if code != 0:
            raise BrainSwitchError(f"the VPS command for {state} exited {code}")

    def _read_vps_brain(self) -> str | None:
        try:
            result = self._ssh(_vps_shell(_vps_helper("deploy/hostinger/set_env.py", "--get", BRAIN_ENV)))
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        if getattr(result, "returncode", 1) != 0:
            return None
        value = (getattr(result, "stdout", "") or "").strip().lower()
        return value if value in BRAINS else None


def cmd_brain(args: argparse.Namespace) -> int:
    """The idle gate applies unless --now (which gives the spoken 60 s notice instead). run/brain.lock keeps a
    second 'agent_ctl brain' from running a switch at the same time (it is refused, exit 4). --laptop-only (pc only)
    repairs this laptop's JARVIS_BRAIN without the VPS (BrainSwitch.repair_laptop_only)."""
    laptop_only = bool(getattr(args, "laptop_only", False))
    if laptop_only and args.target != "pc":
        error = (f"{LAPTOP_ONLY_FLAG} works only with 'brain pc' (it frees the PC worker from a laptop .env left at "
                 "vps); 'brain vps' always needs the VPS")
        print(f"[agent_ctl] {error}.", file=sys.stderr)
        audit("brain", args, exit=EXIT_USAGE, target=args.target, result="refused", error=error, laptop_only=True)
        return EXIT_USAGE
    if laptop_only and (args.now or args.when_idle):
        print(f"[agent_ctl] Note: --now/--when-idle have no effect with {LAPTOP_ONLY_FLAG} (nothing is stopped or "
              "started).")
    lock, why = take_brain_lock(args)
    if lock is None:
        print(f"[agent_ctl] Brain switch refused: {why}.", file=sys.stderr)
        audit("brain", args, exit=EXIT_REFUSED, target=args.target, result="refused", error=why)
        return EXIT_REFUSED
    try:
        laptop = LaptopBrain(args)
        switch = BrainSwitch(args.target, journal_path=agent_lock.run_dir() / BRAIN_JOURNAL_FILE, ssh=_default_ssh,
                             status=vortex_status, laptop=laptop, notify=_speak_notice,
                             when_idle=args.when_idle or not args.now, now_force=args.now)
        try:
            code, details = switch.repair_laptop_only() if laptop_only else switch.run()
        except KeyboardInterrupt:
            _report_interrupted_switch(args, switch.journal_path)
            return EXIT_INTERRUPTED
    finally:
        release_run_lock(lock)
    audit("brain", args, exit=code, target=args.target, **details)
    return code


def _report_interrupted_switch(args: argparse.Namespace, journal_path: Path) -> None:
    """Ctrl+C during 'brain': the run does not catch it (no restore runs in an interrupted process), so the
    journal keeps the last finished state. Audit that and tell the owner how to finish or undo it."""
    journal = _read_json(journal_path)
    state = journal.get("state")
    restoring = bool(journal.get("restoring"))
    begun = restoring or (state in BRAIN_STATES and BRAIN_STATES.index(state) >= BRAIN_STATES.index("BACKUP"))
    audit("brain", args, exit=EXIT_INTERRUPTED, target=args.target, result="interrupted", state=state,
          restoring=restoring, previous=journal.get("previous"), half_done=begun)
    if not begun:
        print(f"[agent_ctl] Brain switch interrupted before anything was stopped or changed; "
              f"'{journal.get('previous') or current_brain()}' is still the brain.", file=sys.stderr)
        return
    how = "while restoring the previous brain" if restoring else f"after {state}"
    print(f"[agent_ctl] Brain switch to '{args.target}' INTERRUPTED {how}: it is half-done and the laptop may have "
          f"no brain running. run/{BRAIN_JOURNAL_FILE} keeps its progress; run 'agent_ctl brain {args.target}' "
          "now: it finishes the switch or restores the previous brain.", file=sys.stderr)


SAY_ROUTE = "/vortex/desktop/say"


def _speak_notice(message: str) -> None:
    """The 60 s notice before a --now switch. It is always printed. It is spoken only once the bridge serves the
    PC-signed SAY_ROUTE: today's bridge has no such route (it answers 404), so the notice is printed only."""
    print(f"[agent_ctl] {message}", flush=True)
    status, _ = pc_signed_post(SAY_ROUTE, json.dumps({"text": message}).encode("utf-8"))
    if status != 200:
        why = ("the bridge does not serve it yet" if status == 404
               else "no answer" if status is None else f"HTTP {status}")
        print(f"[agent_ctl] (notice printed only, not spoken: {SAY_ROUTE} {why})", file=sys.stderr)


# --------------------------------------------------------------------------- command line


# --------------------------------------------------------------------------- backup pull (C-122 step 4)

# 'backup pull' runs src/core/desktop/backup_pull.cli_main (K-73 'Backup pull'). It only reads the node config and
# keys and writes the snapshot folder and run/desktop_backup_pull.json, so it needs no runtime lock. In the .venv_312
# interpreter it runs in this process; under another python it runs as this child of .venv_312's python, its
# KEY=VALUE stdout, ERROR/HINT stderr and exit code passed through unchanged. Both ways an unexpected exception
# (an OSError from a full or locked disk, a failed import) ends as ERROR=unexpected_<type> (plus a HINT for an
# OSError) on stderr and exit 1, never a traceback; argparse's own exits (--help, a bad option) pass through.
BACKUP_UNEXPECTED_HINT = ("HINT=the snapshot could not be read or written on this PC: check the free disk space "
                          "and that the destination folder is writable and not locked by another program")
BACKUP_PULL_CODE = (
    "import sys\n"
    "try:\n"
    "    from src.core.desktop import backup_pull\n"
    "    code = backup_pull.cli_main(sys.argv[1:])\n"
    "except Exception as exc:\n"
    "    print('ERROR=unexpected_' + type(exc).__name__, file=sys.stderr)\n"
    "    if isinstance(exc, OSError):\n"
    f"        print({BACKUP_UNEXPECTED_HINT!r}, file=sys.stderr)\n"
    "    code = 1\n"
    "sys.exit(code)\n"
)
BACKUP_EXIT_RESULTS = {0: "pulled", 1: "failed", 2: "config"}


def backup_pull_argv(args: argparse.Namespace) -> list[str]:
    """The cli_main arguments after 'backup pull'."""
    dest = getattr(args, "dest", None)
    return ["--dest", str(dest)] if dest else []


def _in_agent_venv() -> bool:
    """True when this python is .venv_312's (its sys.prefix is that venv)."""
    try:
        return Path(sys.prefix).resolve() == (ROOT / ".venv_312").resolve()
    except OSError:
        return False


def _backup_pull_in_process(argv: list[str]) -> int:
    try:
        # pylint: disable-next=import-outside-toplevel  # only this command needs the node's modules
        from src.core.desktop import backup_pull as backup_pull_mod

        return int(backup_pull_mod.cli_main(argv))
    except SystemExit as exc:  # argparse inside cli_main (usage error or --help)
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else EXIT_FAILED)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # a KEY=VALUE error, never a traceback
        print(f"ERROR=unexpected_{type(exc).__name__}", file=sys.stderr)
        if isinstance(exc, OSError):
            print(BACKUP_UNEXPECTED_HINT, file=sys.stderr)
        return EXIT_FAILED


def _backup_pull_child(argv: list[str]) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(ROOT), env.get("PYTHONPATH", "")) if p)
    env["PYTHONIOENCODING"] = "utf-8"
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        code = int(_run([str(agent_python()), "-c", BACKUP_PULL_CODE, *argv], cwd=ROOT, env=env).returncode)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR=launch_{type(exc).__name__}", file=sys.stderr)
        return EXIT_FAILED
    if code not in BACKUP_EXIT_RESULTS:  # killed, or crashed past BACKUP_PULL_CODE's own handler
        print(f"ERROR=child_exit_{code}", file=sys.stderr)
        return EXIT_FAILED
    return code


def backup_pull(args: argparse.Namespace) -> int:
    argv = backup_pull_argv(args)
    code = _backup_pull_in_process(argv) if _in_agent_venv() else _backup_pull_child(argv)
    # Only the outcome: no path, size or hash goes into the log.
    audit("backup_pull", args, exit=code, result=BACKUP_EXIT_RESULTS.get(code, "failed"),
          dest="custom" if argv else "default")
    return code


def cmd_backup(args: argparse.Namespace) -> int:
    handler = {"pull": backup_pull}[args.backup_command]
    return handler(args)


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
    _add_node_parser(sub)
    _add_brain_parser(sub)
    _add_backup_parser(sub)
    return parser.parse_args(argv)


def _add_node_parser(sub: Any) -> None:
    """node start|stop|status|logs|update (C-123 steps 1-3): the laptop Desktop Node, its logon task and the
    pinned snapshot."""
    node = sub.add_parser("node", help="the Desktop Node: start|stop|status|logs|update")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    for name, text in (("start", "enable and run the logon task, wait for /health"),
                       ("stop", "graceful POST /stop; the logon task stays disabled until node start"),
                       ("status", "the node's /health, pinned snapshot and logon task"),
                       ("update", "pin the deployed commit, write node_config.json + secrets, restart")):
        action = node_sub.add_parser(name, help=text)
        if name in ("start", "stop", "update"):
            action.add_argument("--by", choices=WHO, default="user", help="who asks (logged)")
            action.add_argument("--reason", default="", help="task ID or why (logged)")
        if name == "stop":
            action.add_argument("--timeout", type=_positive, default=NODE_STOP_TIMEOUT_S)
        if name == "update":
            action.add_argument("--sha", default=None, help="the commit to pin (default: HEAD)")
            action.add_argument("--canary", action="store_true", help="pin and run the node in canary mode")
    node_logs_p = node_sub.add_parser("logs", help="last lines of the supervisor and node logs")
    node_logs_p.add_argument("-n", "--lines", type=_positive_int, default=50)


def _add_brain_parser(sub: Any) -> None:
    """brain vps|pc [--when-idle|--now] (C-123 steps 4, 6): the only writer of JARVIS_BRAIN."""
    brain = sub.add_parser("brain", help="switch the brain: vps|pc [--when-idle|--now]")
    brain.add_argument("target", choices=BRAINS, help="the brain to switch to")
    brain.add_argument("--by", choices=WHO, default="user", help="who asks (logged)")
    brain.add_argument("--reason", default="", help="task ID or why (logged)")
    gate = brain.add_mutually_exclusive_group()
    gate.add_argument("--when-idle", action="store_true",
                      help="only switch once there was no activity for 10 min (the default)")
    gate.add_argument("--now", action="store_true",
                      help=f"switch now, after a 60 s notice (printed; spoken only once the bridge serves "
                           f"{SAY_ROUTE}, which it does not yet)")
    brain.add_argument(LAPTOP_ONLY_FLAG, dest="laptop_only", action="store_true",
                       help="pc only: rewrite only this laptop's JARVIS_BRAIN line (journalled), without reading the "
                            "VPS; for a laptop .env left at vps while the VPS cannot be read")
    brain.epilog = (f"PREFLIGHT refuses (exit 4, nothing changed) until the VPS is provisioned ({VPS_PROVISIONED_BY}): "
                    "the helper scripts in the VPS checkout and the jarvis-agent/jarvis-agent-svc compose services; "
                    "and until the PC-signed /vortex/status shows the current brain with its running agent "
                    "(lk.agent.name, jarvis.session), which VERIFY and the restore check need. FLIP_ENV writes "
                    "JARVIS_BRAIN in the VPS .env and in this laptop's root .env (only that line); a failure "
                    "writes the previous brain back to both. When the VPS already says <target> but this laptop does "
                    "not, only the laptop's JARVIS_BRAIN line is repaired (no VPS command, nothing stopped). "
                    f"'brain pc {LAPTOP_ONLY_FLAG}' does that repair without reading the VPS at all. "
                    "run/brain.lock refuses a second 'brain' run while one is running. Ctrl+C leaves "
                    "run/brain_switch.json: run the same 'brain <target>' again to finish the switch or restore the "
                    "previous brain.")


def _add_backup_parser(sub: Any) -> None:
    """backup pull [--dest PATH] (C-122 step 4): the newest encrypted VPS snapshot to this PC."""
    backup = sub.add_parser("backup", help="the encrypted VPS backup: pull [--dest PATH]")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    pull = backup_sub.add_parser("pull", help="pull the newest encrypted VPS snapshot now (no runtime lock; "
                                              "KEY=VALUE lines, exit 0 pulled or kept, 1 failed, 2 configuration)")
    pull.add_argument("--dest", default=None,
                      help="target folder (default %%LOCALAPPDATA%%\\JARVIS\\vps_backups)")


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "status": cmd_status, "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart,
    "wait": cmd_wait, "logs": cmd_logs, "node": cmd_node, "brain": cmd_brain, "backup": cmd_backup,
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
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
