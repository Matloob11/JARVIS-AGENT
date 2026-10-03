"""
# room_watchdog.py
Room-presence watchdog of the PC brain's worker (W-room-watchdog; interim until C-23 makes the bridge the room
owner).

LiveKit server v1.13.7 never re-launches the job of a worker that went away: after a network blip the worker
reconnects under a new worker id, but the room keeps no agent (or expires) and JARVIS stays silent until
deploy/windows-local/ensure_room.py runs. This daemon thread checks the room every CHECK_S while the worker's
LiveKit link is connected (run/agent_link.json, C-136): a missing room or a room without an AGENT participant
is a miss, and after MISSES_NEEDED misses in a row it runs ensure_room.py once (at most every
ENSURE_EVERY_S). ensure_room.py owns every room decision (never a second agent, never a deleted live session);
this module only lists rooms and participants.

Started only by the worker's main process (src/core/agent.py, the one holding run/agent.lock) in pc mode,
never in a job process, never under pytest or on import, and not with JARVIS_ROOM_WATCHDOG=0. Nothing here may
stop or block the worker: its own thread, its own event loop, every call bounded by a timeout, every error
caught. Logs carry no key, secret or LiveKit address, and never ensure_room.py's output (only its result line).
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from src.core import agent_lock, livekit_link

ROOT = agent_lock.PROJECT_ROOT
ENSURE_ROOM = ROOT / "deploy" / "windows-local" / "ensure_room.py"
# The settings files ensure_room.py loads (the root .env wins, K-47); read here only when a key is not in the
# environment, and never written.
ENV_FILES = (ROOT / ".env", ROOT / "deploy" / "windows-local" / ".env")
DEFAULT_ROOM = "jarvis-hybrid"  # ensure_room.py's default for JARVIS_ROOM_NAME
SWITCH_ENV = "JARVIS_ROOM_WATCHDOG"
CHECK_S = 60.0
MISSES_NEEDED = 2
ENSURE_EVERY_S = 300.0
ENSURE_TIMEOUT_S = 150.0
API_TIMEOUT_S = 20.0
WARN_EVERY_S = 600.0
AGENT_KIND = 4  # livekit.protocol.models.ParticipantInfo.Kind.AGENT
GONE_STATE = 3  # ParticipantInfo.State.DISCONNECTED: a dropped agent that LiveKit still lists
RESULT_PREFIX = "[ROOM] Result:"

PRESENT, MISSING_ROOM, NO_AGENT = "present", "room missing", "no agent in the room"
_OFF = ("0", "false", "no", "off")

LOG = logging.getLogger("ROOM-WATCHDOG")
_run = subprocess.run  # replaced in tests: no test starts a real process
_watchdog: RoomWatchdog | None = None
_start_lock = threading.Lock()


def env_value(name: str) -> str:
    """name from the environment, else from the .env files ensure_room.py loads (first file wins); ''."""
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


def room_name() -> str:
    return env_value("JARVIS_ROOM_NAME") or DEFAULT_ROOM


def masked(text: object) -> str:
    """text on one line with the LiveKit key, secret and address hidden."""
    out = livekit_link.hide_livekit_address(str(text), env_value("LIVEKIT_URL"))
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        value = env_value(name)
        if value:
            out = out.replace(value, "***")
    return " ".join(out.split())[:200]


def why_not_started() -> str | None:
    """Why this process must not run the watchdog, else None: JARVIS_ROOM_WATCHDOG=0, a brain other than pc,
    pytest or JARVIS_NO_LOCK=1, a multiprocessing child (a LiveKit job), or a process that does not hold
    run/agent.lock."""
    if env_value(SWITCH_ENV).lower() in _OFF:
        return f"{SWITCH_ENV}=0"
    brain = env_value("JARVIS_BRAIN").lower()
    if brain not in ("", "pc"):
        return "JARVIS_BRAIN is not pc (the VPS dispatches its own job)"
    if agent_lock.lock_disabled():
        return "worker lock disabled (pytest or JARVIS_NO_LOCK=1)"
    if multiprocessing.parent_process() is not None:
        return "a job process"
    info = agent_lock.read_pid_info() or {}
    if info.get("pid") != os.getpid() or not agent_lock.is_locked():
        return "this process does not hold run/agent.lock"
    return None


def link_connected() -> bool:
    """True when run/agent_link.json of the worker holding the lock says 'connected'; False when it says
    otherwise, describes an earlier worker or cannot be read."""
    doc = livekit_link.read_link(agent_lock.read_pid_info())
    return bool(doc) and doc.get("state") == livekit_link.CONNECTED


def _http_url(ws_url: str) -> str:
    url = (ws_url or "").strip()
    if url.startswith("ws://"):
        return "http://" + url[len("ws://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    return url


async def _room_state_async(room: str, url: str, key: str, secret: str) -> str:
    # pylint: disable=import-outside-toplevel,no-member  # livekit.api re-exports the protobuf messages
    from livekit import api

    lk = api.LiveKitAPI(_http_url(url), key, secret)
    try:
        rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[room]))
        if not any(r.name == room for r in rooms.rooms):
            return MISSING_ROOM
        response = await lk.room.list_participants(api.ListParticipantsRequest(room=room))
        agents = [p for p in response.participants if p.kind == AGENT_KIND and p.state != GONE_STATE]
        return PRESENT if agents else NO_AGENT
    finally:
        await lk.aclose()


def room_state(room: str) -> str:
    """PRESENT, MISSING_ROOM or NO_AGENT, from LiveKit's list_rooms and list_participants on a fresh event loop
    of the calling thread, within API_TIMEOUT_S. Raises on a missing setting or any API/network error."""
    url, key, secret = (env_value(n) for n in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not url or not key or not secret:
        raise RuntimeError("LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
    return asyncio.run(asyncio.wait_for(_room_state_async(room, url, key, secret), API_TIMEOUT_S))


def run_ensure_room() -> str:
    """Runs '<this python> deploy/windows-local/ensure_room.py' (cwd: the repo root, ENSURE_TIMEOUT_S) with its
    output captured and dropped; returns its '[ROOM] Result: ...' line or a short note without it."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    kwargs: dict[str, Any] = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL,
                              "capture_output": True, "timeout": ENSURE_TIMEOUT_S}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = _run([sys.executable, str(ENSURE_ROOM)], check=False, **kwargs)
    output = done.stdout or b""
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else str(output)
    results = [line.strip() for line in text.splitlines() if line.strip().startswith(RESULT_PREFIX)]
    if results:
        return results[-1]
    return f"ensure_room.py exited {done.returncode} without a result line"


class RoomWatchdog:
    """
    The checks of one worker. tick() is one pass; the thread calls it every CHECK_S. Clock, link state, room
    check, ensure_room run and the log are injectable, so the tests need no network, process or sleep.
    """

    def __init__(self, room: str, *, clock: Callable[[], float] = time.monotonic,
                 connected: Callable[[], bool] = link_connected,
                 check: Callable[[str], str] = room_state,
                 ensure: Callable[[], str] = run_ensure_room,
                 log: logging.Logger = LOG, interval: float = CHECK_S) -> None:
        self.room, self.interval = room, interval
        self._clock, self._connected, self._check, self._ensure, self._log = clock, connected, check, ensure, log
        self.misses = 0
        self.ensure_runs = 0
        self._last_ensure: float | None = None
        self._last_warn: float | None = None
        self._failing = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="jarvis-room-watchdog", daemon=True)
        self._thread.start()
        self._log.info("ROOM WATCHDOG on: room '%s' checked every %.0f s while the LiveKit link is connected; "
                       "ensure_room.py after %d misses in a row, at most every %.0f s (%s=0 turns it off)",
                       self.room, self.interval, MISSES_NEEDED, ENSURE_EVERY_S, SWITCH_ENV)

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def _loop(self) -> None:
        while not self._stopped.wait(self.interval):
            try:
                self.tick()
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the thread outlives errors
                self._warn(f"ROOM WATCHDOG error: {type(exc).__name__}: {masked(exc)}")

    def _warn(self, message: str) -> None:
        """One WARNING per WARN_EVERY_S; the rest are dropped."""
        now = self._clock()
        if self._last_warn is None or now - self._last_warn >= WARN_EVERY_S:
            self._last_warn = now
            self._log.warning("%s (further warnings within %.0f s are not logged)", message, WARN_EVERY_S)

    def tick(self) -> None:
        """One pass: skip while the link is down (the miss count starts again), else check the room and run
        ensure_room.py after MISSES_NEEDED misses in a row unless it ran less than ENSURE_EVERY_S ago."""
        try:
            connected = self._connected()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._warn(f"ROOM WATCHDOG could not read the link state: {type(exc).__name__}")
            connected = False
        if not connected:
            self.misses = 0
            return
        try:
            state = self._check(self.room)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # network/API: keep running
            self._failing = True
            self._warn(f"ROOM WATCHDOG could not check room '{self.room}': {type(exc).__name__}: {masked(exc)}")
            return
        if self._failing:
            self._failing = False
            self._log.info("ROOM WATCHDOG: LiveKit answers again")
        if state == PRESENT:
            if self.misses:
                self._log.info("ROOM WATCHDOG: the agent is back in room '%s'", self.room)
            self.misses = 0
            return
        self.misses += 1
        self._log.info("ROOM WATCHDOG: %s '%s' (%d check(s) in a row; ensure_room.py after %d)", state, self.room,
                       self.misses, MISSES_NEEDED)
        if self.misses < MISSES_NEEDED:
            return
        now = self._clock()
        if self._last_ensure is None or now - self._last_ensure >= ENSURE_EVERY_S:
            self._last_ensure = now
            self._run_ensure()

    def _run_ensure(self) -> None:
        """ensure_room.py once, logged by its result line only; a timeout or a start error is logged."""
        self.misses = 0
        self.ensure_runs += 1
        self._log.warning("ROOM WATCHDOG: no agent in room '%s' for %d checks: running ensure_room.py",
                          self.room, MISSES_NEEDED)
        try:
            result = self._ensure()
        except subprocess.TimeoutExpired:
            self._log.warning("ROOM WATCHDOG: ensure_room.py did not finish within %.0f s", ENSURE_TIMEOUT_S)
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._log.warning("ROOM WATCHDOG: ensure_room.py could not run: %s: %s",
                              type(exc).__name__, masked(exc))
            return
        self._log.info("ROOM WATCHDOG: ensure_room.py %s", masked(result))


def start_room_watchdog(**kwargs: Any) -> RoomWatchdog | None:
    """Starts the watchdog thread once, in the worker's main process (see why_not_started); returns it, or None
    where it must not run. Never raises: the worker starts the same without it. kwargs go to RoomWatchdog."""
    global _watchdog  # pylint: disable=global-statement
    try:
        with _start_lock:
            if _watchdog is not None:
                return _watchdog
            reason = why_not_started()
            if reason is not None:
                if not agent_lock.lock_disabled():
                    LOG.info("ROOM WATCHDOG off: %s", reason)
                return None
            watchdog = RoomWatchdog(room_name(), **kwargs)
            watchdog.start()
            _watchdog = watchdog
            return watchdog
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOG.warning("ROOM WATCHDOG not started: %s", type(exc).__name__)
        return None
