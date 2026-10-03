"""
# livekit_link.py
The PC worker's LiveKit link: visible state and recovery after laptop sleep or network loss (C-136).

livekit-agents keeps one websocket from the worker to the LiveKit server (/agent) and retries it by itself.
Its /health answers 200 while it retries, so the state comes from the worker's connection lines on the
livekit.agents logger: connecting (start), connected, retrying, failed (livekit-agents stopped retrying;
/health then answers 503). The worker writes every change to run/agent_link.json, which
scripts/agent_ctl.py reads for status and wait, and it asks agent_ctl for a fresh worker when the link stays
lost while LiveKit answers again (probe_livekit).

A job that livekit-agents still counts postpones that request only while its session can still be live: the
JARVIS room has an AGENT participant (room_presence, a read-only list_rooms + list_participants). After the
network loss of 2026-10-03 the counter kept the job of a room session that had long died, and the worker
never asked for its replacement.

Stdlib only, like agent_lock.py: agent_ctl imports it without the worker's dependencies or its .env loading;
room_presence imports livekit.api (and python-dotenv) only when the worker's monitor calls it.
src/core/agent_runner.py wires the monitor into the process that runs the LiveKit worker.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.core import agent_lock

LINK_FILE_NAME = "agent_link.json"
CONNECTING, CONNECTED, RETRYING, FAILED = "connecting", "connected", "retrying", "failed"
STATES = (CONNECTING, CONNECTED, RETRYING, FAILED)
NETWORK, REJECTED = "network", "rejected"  # cause of a failed connection attempt (failure_cause)
CHECK_S = 15.0  # monitor tick
RECOVER_AFTER_S = 600.0  # retrying this long (while LiveKit answers) means livekit-agents will not recover
REQUEST_EVERY_S = 300.0  # between two recovery requests of one worker
MAX_PAUSE_S = 3600.0  # longest pause_s() of a worker that never registered
PROBE_TIMEOUT_S = 5.0
# A running job postpones the recovery only while its session can still be live (room_presence). When the
# room cannot be read, it postpones at most this long after the link went down.
JOB_SESSION_GRACE_S = 300.0
PRESENCE_TIMEOUT_S = 8.0  # room_presence: both API calls together
DEFAULT_ROOM = "jarvis-hybrid"  # deploy/windows-local/ensure_room.py's default for JARVIS_ROOM_NAME
AGENT_KIND = 4  # livekit.protocol.models.ParticipantInfo.Kind.AGENT
GONE_STATE = 3  # ParticipantInfo.State.DISCONNECTED: a dropped agent that LiveKit still lists
ROOM_PRESENT, ROOM_MISSING, ROOM_NO_AGENT = "present", "room missing", "no agent in the room"
# Set by the recovery for the worker it starts: how many automatic restarts in a row did not register.
RECOVERIES_ENV = "JARVIS_LINK_RECOVERIES"
LK_LOGGER = "livekit.agents"
# Connection lines of livekit-agents 1.4.6 (worker.py: _handle_register, and _connection_task, which
# utils.log_exceptions wraps); tests/unit/test_agent_link_state.py checks them in the installed source.
LK_REGISTERED = "registered worker"
LK_RETRYING = "failed to connect to livekit, retrying in "
LK_GAVE_UP = "failed to connect to livekit after "
AGENT_CTL = agent_lock.PROJECT_ROOT / "scripts" / "agent_ctl.py"
# The settings files ensure_room.py loads (the root .env wins, K-47); read only for a key that the
# environment lacks, and never written.
ENV_FILES = (agent_lock.PROJECT_ROOT / ".env", agent_lock.PROJECT_ROOT / "deploy" / "windows-local" / ".env")
RECOVERY_LOG = agent_lock.PROJECT_ROOT / "logs" / "agent_recovery.log"

LOG = logging.getLogger("AGENT-RUNNER")  # agent_runner's logger (stderr, secrets masked)
_popen = subprocess.Popen  # replaced in tests: no test starts a real process


def link_path() -> Path:
    return agent_lock.run_dir() / LINK_FILE_NAME


def read_link(info: dict[str, Any] | None, *, attempts: int = 3) -> dict[str, Any] | None:
    """run/agent_link.json when it describes the worker of run/agent.pid.json (info: same lock pid and
    start time), else None: absent (a worker from before C-136), written by an earlier worker, or unreadable.
    A read that meets the writer's move on Windows (sharing violation) is tried again."""
    doc: Any = None
    for attempt in range(attempts):
        try:
            doc = json.loads(link_path().read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if attempt == attempts - 1:
                return None
            time.sleep(0.02)
    if not isinstance(doc, dict) or doc.get("state") not in STATES or not info:
        return None
    if doc.get("lock_pid") != info.get("pid") or doc.get("lock_started_at") != info.get("started_at"):
        return None
    return doc


def lock_owner() -> dict[str, Any] | None:
    """run/agent.pid.json when this process holds run/agent.lock (start mode) or its parent does (dev mode
    runs the LiveKit worker in a child); None under pytest, with JARVIS_NO_LOCK=1 or for another worker."""
    if agent_lock.lock_disabled() or not agent_lock.is_locked():
        return None
    info = agent_lock.read_pid_info() or {}
    return info if info.get("pid") in (os.getpid(), os.getppid()) else None


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join((text or "").split())[:limit]


def failure_cause(error: BaseException) -> str:
    """NETWORK when a connection attempt failed on the way to LiveKit (unreachable, refused, DNS, timeout, a
    dropped connection, a 5xx from a proxy in front of it): a fresh worker gets through once LiveKit
    answers. REJECTED when LiveKit or TLS turned the worker away (401, 403, 404, a certificate) or the cause
    is unknown: a fresh worker would meet it again."""
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & {"SSLError", "ClientSSLError"}:
        return REJECTED
    status = getattr(error, "status", None)  # aiohttp's WSServerHandshakeError
    if isinstance(status, int):
        return NETWORK if status >= 500 else REJECTED
    return NETWORK if isinstance(error, OSError) or "ClientConnectionError" in names else REJECTED


def classify_record(record: logging.LogRecord) -> tuple[str, str, str, str] | None:
    """(state, detail, worker id, cause) for a worker connection line of livekit.agents, else None. The
    cause (failure_cause) is that of a failed attempt; '' when the line has none."""
    error = record.exc_info[1] if record.exc_info else None
    message = record.getMessage()
    if message == LK_REGISTERED:
        return CONNECTED, "", str(getattr(record, "id", "") or ""), ""
    if message.startswith(LK_RETRYING):
        retry = message[len("failed to connect to livekit, "):]
        if error is None:
            return RETRYING, retry, "", ""
        return RETRYING, f"{retry} ({type(error).__name__}: {error})", "", failure_cause(error)
    if error is not None and str(error).startswith(LK_GAVE_UP):
        return FAILED, f"{error} (livekit-agents stopped retrying)", "", ""
    return None


def hide_livekit_address(text: str, url: str | None = None) -> str:
    """text with LiveKit's address from LIVEKIT_URL replaced by '<livekit>': a URL to that host (its path
    and query too), host:port or the host. aiohttp names it in its errors ('Cannot connect to host
    <host>:<port>', "url='ws://<host>:<port>/agent'")."""
    address = livekit_address(url)
    if not text or address is None:
        return text
    host = re.escape(address[0])
    pattern = rf"(?:[a-z][a-z0-9+.-]*://)?(?<![\w.-])\[?{host}(?![\w-])\]?(?::\d+)?(?:/[^\s'\"]*)?"
    return re.sub(pattern, "<livekit>", text, flags=re.IGNORECASE)


def self_recovers(doc: dict[str, Any]) -> bool:
    """Whether the worker of this run/agent_link.json document asks agent_ctl for a fresh worker once LiveKit
    answers again: never with JARVIS_LINK_RECOVERY=0; once it has registered, or when a recovery started
    it; a worker that never registered only after failures on the way to LiveKit (NETWORK), never when
    LiveKit or TLS turned it away: a wrong URL, key or certificate would just restart in a loop."""
    if doc.get("recovery") is False:
        return False
    return bool(doc.get("connected_once") or doc.get("recoveries") or doc.get("cause") == NETWORK)


def pause_s(recoveries: int) -> float:
    """How long a worker that never registered waits from its start before it asks for a fresh worker:
    REQUEST_EVERY_S, doubled for each automatic restart before it that did not register, at most an hour."""
    return min(REQUEST_EVERY_S * 2 ** min(max(recoveries, 0), 8), MAX_PAUSE_S)


def inherited_recoveries() -> int:
    """JARVIS_LINK_RECOVERIES of a worker that a recovery started (JARVIS_STARTED_BY=worker): how many
    automatic restarts in a row came before it without one of them registering; 0 for any other start."""
    if os.getenv("JARVIS_STARTED_BY") != "worker":
        return 0
    try:
        return max(0, int(os.getenv(RECOVERIES_ENV) or "0"))
    except ValueError:
        return 0


def write_json_atomic(path: Path, doc: dict[str, Any], *, attempts: int = 20) -> None:
    """A temporary file moved over path: a reader sees the old or the new file, never half of one. Windows
    refuses the move while a reader has path open, so it is retried for about a second."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        for attempt in range(attempts):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def livekit_address(url: str | None = None) -> tuple[str, int] | None:
    """(host, port) of LIVEKIT_URL: ws and http default to port 80, wss and https to 443; None without a host
    or when urlsplit refuses it ('wss://[host')."""
    try:
        parts = urlsplit((os.getenv("LIVEKIT_URL", "") if url is None else url).strip())
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname, port or (443 if parts.scheme in ("wss", "https") else 80)


def livekit_validate_url(url: str | None = None) -> str | None:
    """What probe_livekit asks: /rtc/validate under LIVEKIT_URL over http(s), ws as http and wss as https, below
    its own path as livekit-agents puts /agent there; None without a usable ws(s) or http(s) URL."""
    raw = (os.getenv("LIVEKIT_URL", "") if url is None else url).strip()
    if livekit_address(raw) is None:
        return None
    parts = urlsplit(raw)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    if scheme not in ("http", "https"):
        return None
    return f"{scheme}://{parts.netloc.rpartition('@')[2]}{parts.path.rstrip('/')}/rtc/validate"


def probe_livekit(timeout: float = PROBE_TIMEOUT_S) -> bool:
    """True when LiveKit itself answers GET livekit_validate_url() with a status below 500 (401: no token).
    A 5xx is no answer, like a refused, failed or timed-out connection: nginx on the domain sends 502 while
    LiveKit behind it is down, and accepts a TCP connection all the same (C-16). Never through a proxy."""
    url = livekit_validate_url()
    if url is None:
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:  # every status from 400 up
        status = exc.code
        exc.close()
    except (OSError, ValueError, http.client.HTTPException):  # DNS, refused, TLS, timeout, no HTTP answer
        return False
    return status < 500


def worker_active_jobs() -> int:
    """Jobs this worker runs, from its own livekit-agents /worker route; 0 when it does not answer."""
    host = os.getenv("JARVIS_WORKER_HOST") or "127.0.0.1"
    host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{host}:{os.getenv('JARVIS_WORKER_PORT') or '8081'}/worker"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy
    try:
        with opener.open(url, timeout=2.0) as response:
            return int(json.loads(response.read()).get("active_jobs") or 0)
    except (OSError, ValueError, AttributeError):
        return 0


def env_value(name: str) -> str:
    """name from the environment, else from ENV_FILES (the first file that has it); '' when none has it or
    python-dotenv is missing (agent_ctl's interpreter)."""
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    try:
        from dotenv import dotenv_values  # pylint: disable=import-outside-toplevel
    except ImportError:
        return ""
    for path in ENV_FILES:
        try:
            found = dotenv_values(path).get(name) if path.is_file() else None
        except (OSError, ValueError):
            found = None
        if found is not None:
            return str(found).strip()
    return ""


def mask_livekit(text: str) -> str:
    """text with LiveKit's address (hide_livekit_address) and its API key and secret ('***') hidden."""
    out = hide_livekit_address(str(text), env_value("LIVEKIT_URL"))
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        value = env_value(name)
        if value:
            out = out.replace(value, "***")
    return out


def _api_url(ws_url: str) -> str:
    url = (ws_url or "").strip()
    for ws_scheme, http_scheme in (("ws://", "http://"), ("wss://", "https://")):
        if url.startswith(ws_scheme):
            return http_scheme + url[len(ws_scheme):]
    return url


async def _room_presence_async(room: str, url: str, key: str, secret: str) -> str:
    # pylint: disable=import-outside-toplevel,no-member  # livekit.api re-exports the protobuf messages
    from livekit import api

    lk = api.LiveKitAPI(_api_url(url), key, secret)
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[room]))
        if not any(r.name == room for r in rooms.rooms):
            return ROOM_MISSING
        response = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
        agents = [p for p in response.participants if p.kind == AGENT_KIND and p.state != GONE_STATE]
        return ROOM_PRESENT if agents else ROOM_NO_AGENT
    finally:
        await lk.aclose()


def room_presence(timeout: float = PRESENCE_TIMEOUT_S) -> str:
    """
    ROOM_PRESENT when the JARVIS room (JARVIS_ROOM_NAME, default DEFAULT_ROOM) has an AGENT participant that
    has not left, ROOM_NO_AGENT when it has none, ROOM_MISSING when the room does not exist. Read only:
    LiveKit's list_rooms and list_participants on a fresh event loop of the calling thread (the monitor's),
    within timeout; LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET from the environment or the files
    that ensure_room.py loads. Raises on a missing setting and on any API or network error (mask_livekit
    hides the credentials in its text).
    """
    url, key, secret = (env_value(n) for n in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not url or not key or not secret:
        raise RuntimeError("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
    room = env_value("JARVIS_ROOM_NAME") or DEFAULT_ROOM
    return asyncio.run(asyncio.wait_for(_room_presence_async(room, url, key, secret), timeout))


def request_worker_restart(reason: str, env: dict[str, str], owner: dict[str, Any] | None = None) -> int:
    """
    Starts 'agent_ctl restart --by worker --reason <reason>' detached with env and returns its pid (OSError
    when it cannot start). owner (this worker's run/agent.pid.json) adds --expect-pid and --expect-started:
    agent_ctl refuses once that worker no longer runs, another one holds the lock or its link is connected
    again. Otherwise it replaces this worker the usual way: run/restart.lock and the cooldown, Ctrl+Break
    or taskkill for the agent's processes only, a start that run/agent.lock keeps single (K-53). The
    helper gets its own hidden console, so that Ctrl+Break never reaches it, its own process group and,
    when allowed, leaves this worker's job object. Its output: logs/agent_recovery.log.
    """
    if not AGENT_CTL.is_file():
        raise FileNotFoundError(f"{AGENT_CTL} is missing")
    venv = agent_lock.PROJECT_ROOT / ".venv_312"
    venv /= "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    cmd = [str(venv if venv.is_file() else Path(sys.executable)), str(AGENT_CTL), "restart",
           "--by", "worker", "--reason", reason]
    if owner and isinstance(owner.get("pid"), int):
        cmd += ["--expect-pid", str(owner["pid"])]
        if owner.get("started_at"):
            cmd += ["--expect-started", str(owner["started_at"])]
    RECOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RECOVERY_LOG.open("ab") as out:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        out.write(f"\n===== {stamp} {reason} =====\n".encode("utf-8"))
        out.flush()
        kwargs: dict[str, Any] = {"cwd": agent_lock.PROJECT_ROOT, "env": env, "close_fds": True,
                                  "stdin": subprocess.DEVNULL, "stdout": out, "stderr": out}
        if sys.platform != "win32":
            return _popen(cmd, start_new_session=True, **kwargs).pid
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return _popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                          **kwargs).pid
        except PermissionError:  # a job that forbids breakaway
            return _popen(cmd, creationflags=flags, **kwargs).pid


class LinkLogHandler(logging.Handler):
    """
    On livekit.agents in the process that runs the LiveKit worker: hands its connection lines to the monitor
    and passes WARNING and above to logging.lastResort. That logger has propagate=False and no other handler,
    so lastResort printed exactly those lines to stderr before; its INFO lines stay out of the log.
    """

    def __init__(self, monitor: LinkMonitor) -> None:
        super().__init__()
        self.monitor = monitor

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = classify_record(record)
            if event is not None:
                self.monitor.note(*event)
        except Exception:  # pylint: disable=broad-exception-caught  # LiveKit's logging must not fail here
            pass
        last = logging.lastResort
        if last is not None and record.levelno >= last.level:
            last.handle(record)


class LinkMonitor:
    """
    The link state of this worker. note() takes the connection lines from LinkLogHandler; a daemon thread
    writes every change to run/agent_link.json (atomic), logs it in one INFO line and asks agent_ctl for a
    fresh worker when recovery_reason() says so. recoveries: inherited_recoveries() of this worker;
    base_env: the environment of agent_ctl's helper. Clock, probe, job count, room presence and restart are
    injectable.
    """

    def __init__(self, path: Path, owner: dict[str, Any], *, recovery: bool = True, recoveries: int = 0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 probe: Callable[[], bool] = probe_livekit, active_jobs: Callable[[], int] = worker_active_jobs,
                 presence: Callable[[], str] = room_presence,
                 restart: Callable[[str, dict[str, str]], int] | None = None,
                 base_env: Callable[[], dict[str, str]] = lambda: dict(os.environ),
                 redact: Callable[[str], str] = str, log: Callable[[str], Any] = LOG.info) -> None:
        self.path, self.owner, self.recovery, self.recoveries = path, owner, recovery, max(recoveries, 0)
        self._clock, self._wall, self._probe, self._active_jobs = clock, wall, probe, active_jobs
        self._presence = presence
        self._restart = restart or (lambda reason, env: request_worker_restart(reason, env, self.owner))
        self._base_env, self._redact, self._log = base_env, redact, log
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._handler: LinkLogHandler | None = None
        self._thread: threading.Thread | None = None
        self.state, self.since, self.detail, self.worker_id, self.cause = CONNECTING, wall(), "", "", ""
        self.connected_once = False
        self._born = clock()
        self._down_since: float | None = None
        self._lines: list[str] = []
        self._dirty = True
        self._reachable = 0
        self._next_request = 0.0
        self._postponed = False
        self._write_failed = False

    def note(self, state: str, detail: str = "", worker_id: str = "", cause: str = "") -> None:
        """A connection line, from any thread; the monitor thread writes and logs the change. LiveKit's
        address in detail becomes '<livekit>', a secret '***'. cause: that of a failed connection attempt
        (the line after a disconnect has none worth keeping)."""
        detail = _one_line(self._redact(hide_livekit_address(detail)))
        with self._lock:
            previous, now = self.state, self._clock()
            if cause and previous != CONNECTED and cause != self.cause:
                self.cause, self._dirty = cause, True  # written by the next tick
            if state == previous and state != CONNECTED:
                self.detail = detail  # one more retry: no change
                return
            if state == CONNECTED:
                down = f" after {now - self._down_since:.0f} s down" if self._down_since is not None else ""
                line = f"connected{down}: worker {worker_id or '?'} registered with LiveKit"
                self.worker_id, self.connected_once, self._down_since = worker_id or self.worker_id, True, None
                self.cause = ""
            elif previous == CONNECTED:
                self._down_since = now
                line = f"disconnected, livekit-agents retries: {detail}"
            else:
                line = f"{state}: {detail}" if self.connected_once else f"{state} (never connected yet): {detail}"
            self.state, self.since, self.detail = state, self._wall(), detail
            self._lines.append("LIVEKIT LINK " + line)
            self._dirty = True
        self._wake.set()

    def snapshot(self) -> dict[str, Any]:
        """run/agent_link.json: the state and who writes it; no key or token, and no LiveKit address."""
        return {"state": self.state, "since": round(self.since, 3), "worker_id": self.worker_id,
                "detail": self.detail, "cause": self.cause, "connected_once": self.connected_once,
                "recovery": self.recovery, "recoveries": self.recoveries, "pid": os.getpid(),
                "lock_pid": self.owner.get("pid"), "lock_started_at": self.owner.get("started_at")}

    def attach(self) -> None:
        """The handler on livekit.agents, at INFO for its 'registered worker' line; safe to repeat."""
        lk_logger = logging.getLogger(LK_LOGGER)
        if self._handler is None:
            self._handler = LinkLogHandler(self)
        if self._handler not in lk_logger.handlers:
            lk_logger.addHandler(self._handler)
        if lk_logger.level == logging.NOTSET or lk_logger.level > logging.INFO:
            lk_logger.setLevel(logging.INFO)

    def start(self) -> None:
        """Writes 'connecting' (before the worker's health server listens), then follows the link. When that
        first write fails it is not tried again: dev mode's reloader parent writes it too, and nothing else,
        so a late copy would overwrite the state that its child (the worker) wrote in the meantime."""
        with self._lock:
            doc, self._dirty = self.snapshot(), False
        self._write(doc, again=False)
        self.attach()
        self._thread = threading.Thread(target=self._run, name="jarvis-link-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()
        if self._handler is not None:
            logging.getLogger(LK_LOGGER).removeHandler(self._handler)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stopped.is_set():
            self._wake.wait(CHECK_S)
            self._wake.clear()
            if self._stopped.is_set():
                break
            try:
                self.tick()
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the thread outlives errors
                LOG.warning("LIVEKIT LINK monitor error: %s", exc)

    def tick(self) -> None:
        """One pass of the thread: keep the handler attached, write and log what changed, then check
        whether this worker should be replaced."""
        self.attach()
        with self._lock:
            lines, self._lines = self._lines, []
            doc = self.snapshot() if self._dirty else None
            self._dirty = False
        if doc is not None:
            self._write(doc)
        for line in lines:
            self._log(line)
        reason = self.recovery_reason()
        if reason:
            self._request(reason)

    def recovery_reason(self) -> str | None:
        """
        Why this worker should be replaced now, else None. Never while connected (no probe either), nor for a
        worker that self_recovers() rules out. The link is retrying for RECOVER_AFTER_S or failed
        (livekit-agents stopped retrying and never will); a worker that never registered also waits
        pause_s() from its start. LiveKit answered probe_livekit() at two checks in a row, and no counted job
        may still hold a live session (job_session_may_live). Only the monitor thread calls it.
        """
        with self._lock:
            doc, down_since = self.snapshot(), self._down_since
        state = doc["state"]
        if state not in (RETRYING, FAILED) or not self_recovers(doc):
            self._reachable = 0
            return None
        self._reachable = self._reachable + 1 if self._probe() else 0
        now = self._clock()
        needed = RECOVER_AFTER_S if state == RETRYING else 0.0
        if doc["connected_once"]:
            waited = now - down_since if down_since is not None else 0.0
        else:
            waited, needed = now - self._born, max(needed, pause_s(self.recoveries))
        if self._reachable < 2 or now < self._next_request or waited < needed:
            return None
        jobs = self._active_jobs()
        down_s = now - (down_since if down_since is not None else self._born)
        if jobs and self.job_session_may_live(jobs, down_s):
            return None
        self._postponed = False
        if not doc["connected_once"]:
            lost = f"not registered {waited:.0f} s after its start (automatic restart {self.recoveries + 1} in a row)"
        elif state == RETRYING:
            lost = f"retrying for {waited:.0f} s"
        else:
            lost = "livekit-agents stopped retrying"
        return f"C-136 link recovery: {lost}, the LiveKit host answers again"

    def job_session_may_live(self, jobs: int, down_s: float) -> bool:
        """
        Whether the jobs that livekit-agents still counts may hold a live session, so the recovery waits (one
        'postponed' line per wait). Asks room_presence only here, where a job would postpone the request: an
        AGENT in the room may be that session; a missing room or one without an AGENT means the session is
        gone (the counter kept a job from before the outage). While the room cannot be read, the jobs
        postpone it only until JOB_SESSION_GRACE_S after the link went down (down_s).
        """
        try:
            presence, error = self._presence(), ""
        except Exception as exc:  # pylint: disable=broad-exception-caught  # any failure: the room is unreadable
            presence, error = None, f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        if presence in (ROOM_MISSING, ROOM_NO_AGENT):
            line = f"LIVEKIT LINK recovery: {jobs} job(s) still counted but no agent in the room, their session is gone"
            self._log(line)
            return False
        if presence == ROOM_PRESENT:
            line = f"LIVEKIT LINK recovery postponed: {jobs} job(s) still running"
        else:
            error = _one_line(self._redact(mask_livekit(error or f"unexpected answer {presence!r}")))
            if down_s >= JOB_SESSION_GRACE_S:
                line = (f"LIVEKIT LINK recovery: {jobs} job(s) still counted and the room is not readable "
                        f"({error}) {down_s:.0f} s after the link went down, their session is taken as gone")
                self._log(line)
                return False
            line = (f"LIVEKIT LINK recovery postponed: {jobs} job(s) still running; the room is not readable "
                    f"({error}), at most {JOB_SESSION_GRACE_S:.0f} s after the link went down")
        if not self._postponed:
            self._log(line)
        self._postponed = True
        return True

    def _request(self, reason: str) -> None:
        """Asks agent_ctl for a fresh worker. RECOVERIES_ENV tells that worker how many automatic restarts in
        a row have not registered: 1 after this worker if it ever registered, else one more than before."""
        self._next_request = self._clock() + REQUEST_EVERY_S
        with self._lock:
            following = 1 if self.connected_once else self.recoveries + 1
        try:
            pid = self._restart(reason, {**self._base_env(), RECOVERIES_ENV: str(following)})
        except OSError as exc:
            line = f"LIVEKIT LINK recovery not started ({reason}): {exc}; next try in {REQUEST_EVERY_S:.0f} s"
            self._log(line)
            return
        line = (f"LIVEKIT LINK recovery: {reason}; 'agent_ctl restart --by worker' (pid {pid}) replaces "
                "this worker under run/restart.lock and run/agent.lock")
        self._log(line)
        with self._lock:
            self.detail = _one_line(f"recovery requested: {reason}")
            doc = self.snapshot()
        self._write(doc)

    def _write(self, doc: dict[str, Any], *, again: bool = True) -> None:
        """A write that fails is not lost (again): the next tick, at most CHECK_S later, writes the state of
        then."""
        try:
            write_json_atomic(self.path, doc)
        except OSError as exc:
            if again:
                with self._lock:
                    self._dirty = True
            if not self._write_failed:
                LOG.warning("LIVEKIT LINK could not write %s: %s", self.path.name, exc)
            self._write_failed = True
        else:
            self._write_failed = False
