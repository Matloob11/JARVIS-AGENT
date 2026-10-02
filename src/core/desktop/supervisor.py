"""Desktop Node supervisor (plan task C-120 step 1; K-53 v4, K-72, K-76 Process rules).

Started by the logon task as 'pythonw -m src.core.desktop.supervisor [--canary]' from the node snapshot
(C-123); 'agent_ctl node start|stop' drive it. It:
- refuses an elevated (administrator) token (exit 4);
- holds run/desktop_node.lock (a second supervisor exits 3) and the shared run/desktop_actor.lock (the PC
  brain, vortex.py and Electron local take it too; exit 3 while one of them runs), under JARVIS_RUN_DIR;
- writes run/desktop_node.token (user-only ACL) and serves 127.0.0.1:8082: GET /health, and POST /stop with
  that token, which stops the node gracefully and exits 0;
- exports JARVIS_DATA_ROOT and JARVIS_RUN_DIR from %LOCALAPPDATA%/JARVIS/node/node_config.json (never from
  the snapshot path) to the node child, with secrets scrubbed and PYTHONPATH set to the snapshot;
- starts the node child ('python -P -m src.core.desktop node') and restarts it: at once after EXIT_RESTART (a
  restart-only setting changed) or EXIT_STALLED (its loop stalled for 60 s, C-31 watchdog), else with backoff
  BACKOFF_FIRST_S -> BACKOFF_MAX_S; a child whose status file stops changing for HANG_AFTER_S is ended and
  restarted (a pause of the whole machine, such as sleep, is not counted).
Standard library only: no services module and no .env is ever loaded here.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.core import agent_lock
from src.core.desktop import (
    EXIT_ALREADY_RUNNING,
    EXIT_CONFIG,
    EXIT_ELEVATED,
    EXIT_OK,
    EXIT_RESTART,
    EXIT_STALLED,
    HEALTH_HOST,
    HEALTH_PORT,
    HEALTH_PORT_ENV,
    RUN_MODES,
)
from src.core.desktop import health
from src.core.desktop import settings as node_settings

logger = logging.getLogger("JARVIS-DESKTOP.supervisor")

BACKOFF_FIRST_S = 1.0
BACKOFF_MAX_S = 60.0
STABLE_RUN_S = 300.0
HANG_AFTER_S = 120.0
PAUSE_GAP_S = 5.0          # a supervisor tick this late means the whole machine paused (sleep)
STOP_GRACE_S = 20.0
TICK_S = 0.5
SUPERVISED_ENV = "JARVIS_NODE_SUPERVISED"
NODE_SHA_ENV = "JARVIS_NODE_SHA"
SUPERVISOR_LOG = Path("logs") / "desktop_supervisor.log"
CHILD_STDIO_LOG = Path("logs") / "desktop_node.stdio.log"
_STDIO_LOG_MAX = 5 * 1024 * 1024
# Python start-up variables the child must not inherit: it imports only from the snapshot.
_PYTHON_ENV = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONSAFEPATH")
CREATE_NO_WINDOW = 0x08000000


# --- the token check -----------------------------------------------------------------------------------------

def is_elevated() -> bool:
    """True when this process runs with an elevated (administrator) token. Windows: TokenElevation; POSIX:
    effective uid 0."""
    if sys.platform != "win32":
        return hasattr(os, "geteuid") and os.geteuid() == 0  # pylint: disable=no-member
    import ctypes  # pylint: disable=import-outside-toplevel
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel

    token_query, token_elevation = 0x0008, 20
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
    try:
        elevation, size = wintypes.DWORD(0), wintypes.DWORD(0)
        if not advapi32.GetTokenInformation(token, token_elevation, ctypes.byref(elevation),
                                            ctypes.sizeof(elevation), ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
        return bool(elevation.value)
    finally:
        kernel32.CloseHandle(token)


# --- locks ---------------------------------------------------------------------------------------------------

def acquire_locks() -> int:
    """run/desktop_node.lock, then the shared run/desktop_actor.lock (both under JARVIS_RUN_DIR) for the life of
    this process. EXIT_OK, or EXIT_ALREADY_RUNNING with the reason on stderr (nothing is held then)."""
    if not agent_lock.hold(agent_lock.NODE_LOCK_FILE):
        print("[JARVIS] A Desktop Node supervisor already runs (run/desktop_node.lock is held).", file=sys.stderr,
              flush=True)
        return EXIT_ALREADY_RUNNING
    if not agent_lock.hold_actor(agent_lock.HOLDER_DESKTOP_NODE):
        agent_lock.release_held(agent_lock.NODE_LOCK_FILE)
        print(agent_lock.actor_held_message(), file=sys.stderr, flush=True)
        return EXIT_ALREADY_RUNNING
    return EXIT_OK


def release_locks() -> None:
    agent_lock.release_held(agent_lock.ACTOR_LOCK_FILE)
    agent_lock.release_held(agent_lock.NODE_LOCK_FILE)


# --- the child -----------------------------------------------------------------------------------------------

def child_python(executable: str | None = None) -> str:
    """python.exe beside pythonw.exe (the child needs working stdio for its stop pipe), else the same one."""
    exe = Path(executable or sys.executable)
    if exe.name.lower() == "pythonw.exe":
        sibling = exe.with_name("python.exe")
        if sibling.is_file():
            return str(sibling)
    return str(exe)


def node_command(run_mode: str = "active", *, python: str | None = None) -> list[str]:
    """-P: no script or cwd folder on sys.path, so every import comes from PYTHONPATH (the snapshot)."""
    command = [child_python(python), "-P", "-m", "src.core.desktop", "node"]
    if run_mode == "canary":
        command.append("--canary")
    return command


def child_environment(base: Mapping[str, str], cfg: node_settings.NodeConfig, *, code_root: Path,
                      run_mode: str = "active", node_sha: str | None = None) -> dict[str, str]:
    """The node child's environment: secrets scrubbed, the Python start-up variables replaced, and the data
    root from node_config.json."""
    env = dict(base)
    node_settings.scrub_secrets(env)
    for name in _PYTHON_ENV:
        env.pop(name, None)
    env.update({"PYTHONPATH": str(code_root), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                node_settings.DATA_ROOT_ENV: str(cfg.data_root), node_settings.RUN_DIR_ENV: str(cfg.run_dir),
                SUPERVISED_ENV: "1", "JARVIS_NODE_RUN_MODE": run_mode})
    if node_sha:
        env[NODE_SHA_ENV] = node_sha
    node_settings.assert_clean(env)
    return env


def _open_stdio_log(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > _STDIO_LOG_MAX:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    return path.open("ab")


class Supervisor:
    """The supervisor loop (see the module docstring). popen and command are injectable (tests run a small
    fake node)."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, cfg: node_settings.NodeConfig, *, run_mode: str = "active", code_root: Path | None = None,
                 command: Sequence[str] | None = None, popen: Callable[..., Any] = subprocess.Popen,
                 health_port: int | None = None, backoff_first_s: float = BACKOFF_FIRST_S,
                 backoff_max_s: float = BACKOFF_MAX_S, stable_run_s: float = STABLE_RUN_S,
                 hang_after_s: float = HANG_AFTER_S, stop_grace_s: float = STOP_GRACE_S, tick_s: float = TICK_S,
                 node_sha: str | None = None, base_env: Mapping[str, str] | None = None,
                 restrict_token: Callable[[Path], None] = health.restrict_to_current_user,
                 mono: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        if run_mode not in RUN_MODES:
            raise ValueError(f"run mode must be one of {RUN_MODES}")
        self.cfg, self.run_mode = cfg, run_mode
        self.code_root = code_root or node_settings.code_root()
        self._command = list(command) if command is not None else node_command(run_mode)
        self._popen = popen
        port = health_port if health_port is not None else int(os.environ.get(HEALTH_PORT_ENV) or HEALTH_PORT)
        self._backoff_first, self._backoff_max, self._stable = backoff_first_s, backoff_max_s, stable_run_s
        self._hang_after, self._stop_grace, self._tick = hang_after_s, stop_grace_s, tick_s
        self._node_sha, self._base_env = node_sha, base_env
        self._restrict = restrict_token
        self._mono, self._wall = mono, wall
        self._stop = threading.Event()
        self._child: Any = None
        self._child_started: float | None = None
        self._token: str | None = None
        self.status_file = health.StatusFile(cfg.run_dir / health.STATUS_FILE, wall=wall)
        self.server = health.HealthServer(host=HEALTH_HOST, port=port, status=self.health,
                                          stop=self.request_stop, token=lambda: self._token)
        self.restarts = 0
        self.last_exit: int | None = None
        self.started_at = wall()
        self.state = "starting"

    # --- control -----------------------------------------------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    def health(self) -> dict[str, Any]:
        node = self.status_file.read() or {}
        age = self.status_file.age_s(node) if node else None
        child = self._child
        alive = child is not None and child.poll() is None
        doc: dict[str, Any] = {key: value for key, value in node.items() if key not in ("status", "supervisor")}
        node_ok = alive and age is not None and age <= 3 * health.STATUS_EVERY_S
        doc.update({
            "status": "ok" if node_ok and self.state == "running" else self.state,
            "node_status_age_s": None if age is None else round(age, 1),
            "supervisor": {"pid": os.getpid(), "run_mode": self.run_mode, "restarts": self.restarts,
                           "last_exit": self.last_exit, "child_pid": child.pid if alive else None,
                           "uptime_s": round(self._wall() - self.started_at, 1)}})
        return doc

    # --- the loop ---------------------------------------------------------------------------------------------

    def run(self) -> int:
        """Locks, token, health server, then the child loop until /stop. Returns the exit code."""
        code = acquire_locks()
        if code != EXIT_OK:
            return code
        try:
            self._token = health.new_token_file(self.cfg.run_dir / health.TOKEN_FILE, restrict=self._restrict)
            try:
                port = self.server.start()
            except OSError as exc:
                logger.error("Desktop Node health port is not available (%s); not starting", type(exc).__name__)
                return EXIT_CONFIG
            logger.info("Desktop Node supervisor up (pid %d, health 127.0.0.1:%d, run mode %s)", os.getpid(), port,
                        self.run_mode)
            self._loop()
            return EXIT_OK
        finally:
            self.state = "stopped"
            self.server.close()
            (self.cfg.run_dir / health.TOKEN_FILE).unlink(missing_ok=True)
            release_locks()
            logger.info("Desktop Node supervisor stopped")

    def _loop(self) -> None:
        delay = self._backoff_first
        while not self._stop.is_set():
            self.state = "running" if self.restarts == 0 else "restarting"
            started = self._mono()
            exit_code = self._run_child()
            self.last_exit = exit_code
            if self._stop.is_set():
                break
            ran = self._mono() - started
            if exit_code in (EXIT_RESTART, EXIT_STALLED):
                wait = 0.0 if exit_code == EXIT_RESTART else self._backoff_first
                logger.warning("Desktop Node exited %d (%s); restarting", exit_code,
                               "settings changed" if exit_code == EXIT_RESTART else "event loop stalled")
            else:
                if ran >= self._stable:
                    delay = self._backoff_first
                wait = delay
                delay = min(delay * 2, self._backoff_max)
                logger.warning("Desktop Node exited %s after %.0f s; restarting in %.0f s", exit_code, ran, wait)
            self.restarts += 1
            self.state = "restarting"
            if self._stop.wait(wait):
                break

    def _run_child(self) -> int | None:
        env = child_environment(self._base_env if self._base_env is not None else os.environ, self.cfg,
                                code_root=self.code_root, run_mode=self.run_mode, node_sha=self._node_sha)
        stdio = _open_stdio_log(self.cfg.data_root / CHILD_STDIO_LOG)
        try:
            kwargs: dict[str, Any] = {"cwd": str(self.cfg.data_root), "env": env, "stdin": subprocess.PIPE,
                                      "stdout": stdio, "stderr": subprocess.STDOUT}
            if sys.platform == "win32":
                kwargs["creationflags"] = CREATE_NO_WINDOW
            try:
                child = self._popen(self._command, **kwargs)
            except OSError as exc:
                logger.error("Desktop Node could not start (%s)", type(exc).__name__)
                return None
            self._child, self._child_started = child, self._mono()
            self.state = "running"
            logger.info("Desktop Node started (pid %d)", child.pid)
            return self._watch(child)
        finally:
            stdio.close()

    def _watch(self, child: Any) -> int | None:
        last_tick = last_change = self._mono()
        last_seen: Any = None
        while True:
            code = child.poll()
            if code is not None:
                return code
            if self._stop.wait(self._tick):
                return self._stop_child(child)
            now = self._mono()
            if now - last_tick > self._tick + PAUSE_GAP_S:
                last_change = now  # the machine slept: nothing to hold against the node
            last_tick = now
            updated = (self.status_file.read() or {}).get("updated_at")
            if updated != last_seen:
                last_seen, last_change = updated, now
            elif now - last_change > self._hang_after:
                logger.error("Desktop Node status unchanged for %.0f s; ending it for a restart", self._hang_after)
                return self._end_child(child)

    def _stop_child(self, child: Any) -> int | None:
        """The graceful stop: 'stop' on its stdin, then STOP_GRACE_S, then terminate."""
        self.state = "stopping"
        try:
            if child.stdin is not None:
                child.stdin.write(b"stop\n")
                child.stdin.flush()
                child.stdin.close()
        except OSError:
            pass
        try:
            return child.wait(timeout=self._stop_grace)
        except subprocess.TimeoutExpired:
            logger.warning("Desktop Node did not stop within %.0f s; terminating it", self._stop_grace)
            return self._end_child(child)

    @staticmethod
    def _end_child(child: Any) -> int | None:
        try:
            child.terminate()  # only the supervisor's own child
            return child.wait(timeout=10.0)
        except (OSError, subprocess.TimeoutExpired):
            return child.poll()


# --- entry ---------------------------------------------------------------------------------------------------

def _setup_logging(data_root: Path | None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    target = (data_root / SUPERVISOR_LOG) if data_root is not None else (node_settings.node_home() / "supervisor.log")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(target, maxBytes=2 * 1024 * 1024, backupCount=3,
                                                       encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.core.desktop.supervisor",
                                     description="JARVIS Desktop Node supervisor (C-120)")
    parser.add_argument("--canary", action="store_true", help="run the node in canary mode (no sensors)")
    args = parser.parse_args(argv)
    try:
        cfg = node_settings.load_node_config()
    except node_settings.NodeConfigError as exc:
        _setup_logging(None)
        logger.error("Desktop Node not started: %s", exc)
        print(f"[JARVIS] Desktop Node not started: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    _setup_logging(cfg.data_root)
    if is_elevated():
        logger.error("Desktop Node refuses an elevated (administrator) token; start it as the normal user")
        print("[JARVIS] Desktop Node refuses an elevated (administrator) token.", file=sys.stderr)
        return EXIT_ELEVATED
    os.environ[node_settings.RUN_DIR_ENV] = str(cfg.run_dir)  # agent_lock.run_dir() for both locks
    node_sha = (os.environ.get(NODE_SHA_ENV) or "").strip() or None
    return Supervisor(cfg, run_mode="canary" if args.canary else "active", node_sha=node_sha).run()


if __name__ == "__main__":
    sys.exit(main())
