"""
# services/utils/jarvis_filelock.py
Cross-process file lock for read-modify-write of shared JSON state (plan task C-125 step 1, landed early for X-66).

On the VPS a job and jarvis-agent-svc both touch reminders.json, identity.json, learning_state.json, the delivery
ledger and location.json; on the PC brain several job threads can too. A plain read-modify-write then loses updates.

    from services.utils.jarvis_filelock import locked

    with locked(path):          # waits up to 10 s, then TimeoutError
        data = json.loads(path.read_text())
        data.append(item)
        write atomically (temp file + os.replace)

The lock is an OS lock on byte 0 of a sidecar file '<path>.lock' (msvcrt.locking on Windows, fcntl.flock elsewhere,
the same scheme as src/core/agent_lock.py), so the data file itself can be replaced atomically while it is held, and
a process that dies releases it at once. It works between processes and between threads (each `with` opens its own
handle). It is NOT re-entrant: a thread that nests locked() on the same path waits for itself until the timeout.
Stdlib only; no project imports.
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

LOCK_SUFFIX = ".lock"
DEFAULT_TIMEOUT_S = 10.0
POLL_S = 0.02

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


def lock_path_for(path: str | os.PathLike[str]) -> Path:
    """The sidecar lock file of a data file: '<path>.lock' next to it."""
    return Path(os.fspath(path) + LOCK_SUFFIX)


@contextlib.contextmanager
def locked(path: str | os.PathLike[str], timeout: float = DEFAULT_TIMEOUT_S) -> Iterator[Path]:
    """Holds the exclusive inter-process lock of `path` for the `with` block and yields the data path. Raises
    TimeoutError when another holder keeps it longer than `timeout` seconds (0 = one try)."""
    data_path = Path(path)
    lock_file = lock_path_for(data_path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"file lock busy for {timeout:.1f} s: {lock_file.name}") from None
                time.sleep(POLL_S)
        try:
            yield data_path
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
