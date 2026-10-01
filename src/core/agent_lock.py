"""
# agent_lock.py
Single agent worker guarantee (K-53 COORD-3).

The worker holds an OS lock on byte 0 of run/agent.lock for its whole lifetime and describes
itself in run/agent.pid.json. The OS drops the lock when the process exits, so a stale pid.json
never blocks a new start: the lock is the source of truth, pid.json is only information.
Stdlib only, because src/core/agent.py imports it before any heavy dependency.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl  # pylint: disable=import-error  # POSIX only; lint runs on Windows

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


EXIT_ALREADY_RUNNING = 3
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# is_locked() takes the lock for a moment while probing; retrying for a second keeps a probe
# that races a real start from making that start exit 3.
_ACQUIRE_RETRY_S = 1.0
_SECRET_OPTION_WORDS = ("secret", "key", "token", "password")

_lock_fd: int | None = None


def run_dir() -> Path:
    """Directory of agent.lock and agent.pid.json: JARVIS_RUN_DIR, default <project>/run."""
    return Path(os.environ.get("JARVIS_RUN_DIR") or PROJECT_ROOT / "run")


def lock_path() -> Path:
    return run_dir() / "agent.lock"


def pid_path() -> Path:
    return run_dir() / "agent.pid.json"


def lock_disabled() -> bool:
    """acquire_or_exit() is a no-op under pytest or with JARVIS_NO_LOCK=1."""
    return (
        os.environ.get("JARVIS_NO_LOCK") == "1"
        or "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
    )


def read_pid_info() -> dict[str, Any] | None:
    """The last worker's pid.json, or None. It may be stale: check is_locked()."""
    try:
        data = json.loads(pid_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_locked() -> bool:
    """True when some process holds the worker lock. Probes without keeping the lock."""
    path = lock_path()
    if not path.is_file():
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            _try_lock(fd)
        except OSError:
            return True
        _unlock(fd)
        return False
    finally:
        os.close(fd)


def acquire_or_exit(*, force: bool = False) -> bool:
    """Hold the worker lock until this process exits, or print who holds it and exit 3.

    Returns True when the lock is held and False when locking is disabled (pytest or
    JARVIS_NO_LOCK=1). force=True locks even then; tests use it in a subprocess.
    """
    global _lock_fd  # pylint: disable=global-statement
    if _lock_fd is not None:
        return True
    if lock_disabled() and not force:
        return False
    directory = run_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "agent.lock", os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + _ACQUIRE_RETRY_S
    while True:
        try:
            _try_lock(fd)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                _exit_already_running()
            time.sleep(0.1)
    _lock_fd = fd
    _write_pid_info()
    return True


def release() -> None:
    """Drop the lock before exit (tests). Normally the OS releases it with the process."""
    global _lock_fd  # pylint: disable=global-statement
    if _lock_fd is None:
        return
    fd, _lock_fd = _lock_fd, None
    try:
        _unlock(fd)
    except OSError:
        pass
    os.close(fd)


def redact_argv(argv: list[str]) -> list[str]:
    """argv with the values of secret-looking options (--api-secret, --api-key ...) masked."""
    redacted: list[str] = []
    mask_next = False
    for arg in argv:
        if mask_next:
            redacted.append("***")
            mask_next = False
            continue
        name, sep, _ = arg.partition("=")
        secret = name.startswith("-") and any(w in name.lower() for w in _SECRET_OPTION_WORDS)
        if secret and sep:
            redacted.append(f"{name}=***")
        else:
            redacted.append(arg)
            mask_next = secret
    return redacted


def _git_sha() -> str:
    """HEAD commit read from .git without starting git; '' when unknown."""
    git_dir = PROJECT_ROOT / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        ref = head[len("ref: "):]
        ref_file = git_dir / ref
        if ref_file.is_file():
            return ref_file.read_text(encoding="utf-8").strip()
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha
    except OSError:
        pass
    return ""


def _write_pid_info() -> None:
    info = {
        "pid": os.getpid(),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "by": os.environ.get("JARVIS_STARTED_BY") or "manual",
        "reason": os.environ.get("JARVIS_START_REASON", ""),
        "argv": redact_argv(sys.argv),
        "git_sha": _git_sha(),
    }
    # Written in place: os.replace fails on Windows while a reader has the file open, and
    # read_pid_info() already treats a half-written file as missing.
    try:
        pid_path().write_text(json.dumps(info, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[JARVIS] Warning: could not write {pid_path()}: {exc}", file=sys.stderr)


def _exit_already_running() -> NoReturn:
    info = read_pid_info() or {}
    print(
        f"[JARVIS] Agent already running (pid {info.get('pid', '?')}, by {info.get('by', '?')}, "
        f"since {info.get('started_at', '?')}). "
        "Use: python scripts/agent_ctl.py restart --by <you> --reason <why>",
        file=sys.stderr,
        flush=True,
    )
    sys.exit(EXIT_ALREADY_RUNNING)
