"""Desktop Node encrypted backup pull (plan task C-122 step 4; contract K-73 'Backup pull').

The VPS data needs an automatic off-host copy: the newest encrypted snapshot that agent-svc wrote to
jarvis_agent_snapshots is fetched from the PC-signed GET /vortex/desktop/backup/latest and kept in
%LOCALAPPDATA%\\JARVIS\\vps_backups (inside the K-76 sensitive set, so no laptop path tool can read it). Every
snapshot is kept (nothing is deleted or overwritten); the same snapshot pulled twice is kept once; a name clash
with different content gets ' (n)'. The size of the pulled file and of the whole folder is reported.

- Daily: install(node) adds an active-only node task that pulls when the last successful pull is DAILY_S (24 h)
  old or older (checked every CHECK_S, retried after RETRY_S on a failure).
- On demand: cli_main(argv) / 'python -m src.core.desktop.backup_pull', which 'agent_ctl backup pull' calls. It
  loads node_config.json and the DPAPI-wrapped node key exactly as the node does (the key never leaves the
  process, nothing is printed but KEY=VALUE lines of paths, sizes and the sha256), signs with the clock offset the
  node last measured (run/desktop_node.json offset_s, when at most a day old; else the own clock, and an auth
  error then says the laptop clock may be off), and records the pull in
  run/desktop_backup_pull.json so the daily task knows. Exit 0 pulled or already held, 1 pull failed, 2 unusable
  configuration.
Download order: '.download.<random>.part' (created new) -> size check against Content-Length -> Mark-of-the-Web
(Zone.Identifier ZoneId=3, as the mirror) -> rename without overwriting. The file name follows the mirror's rules: a
name the mirror would refuse (an invalid component, a partial download, or a name Explorer parses while browsing,
such as .url, .lnk or desktop.ini) becomes 'jarvis_snapshot_<UTC time>.bin'. A file that looks like plaintext (SQLite, ZIP, gzip, tar or JSON) is kept but reported encrypted=false
with a warning, since the snapshot should arrive encrypted.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.desktop import mirror as mirror_mod

logger = logging.getLogger("JARVIS-DESKTOP.backup")

BACKUP_DIR_NAME = "vps_backups"
BACKUP_PATH = "/desktop/backup/latest"
STATE_FILE = "desktop_backup_pull.json"
DAILY_S = 24 * 3600.0
CHECK_S = 3600.0
RETRY_S = 3600.0
MAX_SNAPSHOT_BYTES = 64 * 1024 ** 3
FREE_MARGIN_BYTES = 1024 ** 3
LOCK_FILE = ".pull.lock"
LOCK_STALE_S = 6 * 3600.0
OFFSET_MAX_AGE_S = 24 * 3600.0   # the CLI uses the node's measured clock offset when its status is this fresh
OFFSET_MAX_ABS_S = 24 * 3600.0
_NAME_RE = re.compile(r'filename="?([^";]+)"?', re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_PLAINTEXT_MAGIC = (b"SQLite format 3\x00", b"PK\x03\x04", b"\x1f\x8b", b"{", b"[")

EXIT_PULLED, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2


class BackupPullError(Exception):
    """A pull that did not complete; reason is a short code (never a path or content)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PullResult:
    """One pull: the local file, its size and sha256, whether it was new, whether it looks encrypted, and the
    folder's totals after it."""

    path: Path
    name: str
    size: int
    sha256: str
    new: bool
    encrypted: bool
    total_bytes: int
    count: int

    def lines(self) -> list[str]:
        """The CLI output, in this order: PULLED=<path> (a new file) or KEPT=<path> (an identical snapshot was
        already there), NEW=true|false, SIZE, SHA256, ENCRYPTED=true|false, TOTAL_BYTES, COUNT."""
        return [f"{'PULLED' if self.new else 'KEPT'}={self.path}", f"NEW={'true' if self.new else 'false'}",
                f"SIZE={self.size}", f"SHA256={self.sha256}", f"ENCRYPTED={'true' if self.encrypted else 'false'}",
                f"TOTAL_BYTES={self.total_bytes}", f"COUNT={self.count}"]


def backups_dir() -> Path:
    """%LOCALAPPDATA%\\JARVIS\\vps_backups (next to the node home)."""
    from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

    return node_settings.node_home().parent / BACKUP_DIR_NAME


def snapshot_name(headers: dict[str, str], now: float) -> str:
    """The bridge's Content-Disposition file name reduced to [A-Za-z0-9._-], checked as a mirror component and
    against the mirror's excluded names (partial downloads and names Explorer parses: SHELL_PARSED_NAMES,
    SHELL_PARSED_SUFFIXES); else 'jarvis_snapshot_<UTC time>.bin'."""
    match = _NAME_RE.search(headers.get("content-disposition", ""))
    if match:
        name = _SAFE_NAME_RE.sub("_", match.group(1).strip()).strip("._")[:200]
        if name and mirror_mod.validate_component(name) is None and not mirror_mod.is_excluded_name(name):
            return name
    return time.strftime("jarvis_snapshot_%Y%m%d-%H%M%S.bin", time.gmtime(now))


def looks_encrypted(head: bytes) -> bool:
    """False when the first bytes are a well-known plaintext container (SQLite, ZIP, gzip, tar, JSON)."""
    if any(head.startswith(magic) for magic in _PLAINTEXT_MAGIC):
        return False
    return not (len(head) >= 262 and head[257:262] == b"ustar")


def folder_totals(folder: Path) -> tuple[int, int]:
    """(bytes, files) of the kept snapshots (regular files, no dotfiles or partial downloads)."""
    total = count = 0
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return 0, 0
    for entry in entries:
        if entry.name.startswith(".") or entry.name.endswith(".part"):
            continue
        try:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
                count += 1
        except OSError:
            continue
    return total, count


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


class _Lock:
    """One pull at a time per folder (the node's daily task and the CLI): an O_EXCL lock file; a lock older than
    LOCK_STALE_S is taken over (a pull that died)."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / LOCK_FILE
        self._fd: int | None = None

    def __enter__(self) -> _Lock:
        for _ in range(2):
            try:
                self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                raise BackupPullError("busy") from None
        raise BackupPullError("busy")

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except OSError:
            pass


def pull_latest(http: mirror_mod.NodeHttp, dest: Path, *, wall: Callable[[], float] = time.time,
                max_bytes: int = MAX_SNAPSHOT_BYTES) -> PullResult:
    """Fetches the newest snapshot into dest (see the module docstring). Synchronous."""
    # pylint: disable=too-many-locals
    dest = Path(dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise BackupPullError("dest_unusable") from None
    with _Lock(dest):
        free = shutil.disk_usage(dest).free
        if free < FREE_MARGIN_BYTES:
            raise BackupPullError("disk_full")
        limit = min(max_bytes, free - FREE_MARGIN_BYTES)
        part = dest / f".download.{secrets.token_hex(4)}.part"
        digest = hashlib.sha256()
        head = bytearray()
        written = 0
        try:
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except OSError:
            raise BackupPullError("part_create") from None
        try:
            with os.fdopen(fd, "wb") as handle:
                def sink(chunk: bytes) -> None:
                    nonlocal written
                    written += len(chunk)
                    digest.update(chunk)
                    if len(head) < 512:
                        head.extend(chunk[:512 - len(head)])
                    handle.write(chunk)

                try:
                    headers = http.stream_get(BACKUP_PATH, None, sink, max_bytes=limit)
                except mirror_mod.NodeHttpError as exc:
                    raise BackupPullError(f"http_{exc.reason}") from None
                handle.flush()
                os.fsync(handle.fileno())
            declared = headers.get("content-length")
            if declared is not None and (not declared.isdigit() or int(declared) != written):
                raise BackupPullError("size_mismatch")
            if written == 0:
                raise BackupPullError("empty")
            sha = digest.hexdigest()
            try:
                mirror_mod.write_mark_of_the_web(part)  # the stream moves with the rename
            except OSError:
                raise BackupPullError("motw") from None
            final, new = _place_snapshot(part, dest, snapshot_name(headers, wall()), size=written, sha256=sha)
        except BaseException:
            try:
                part.unlink()
            except OSError:
                pass
            raise
        try:
            part.unlink()  # still there only when an identical snapshot was already kept
        except OSError:
            pass
        # pylint: disable-next=protected-access  # the mirror's own reparse check: never written through a link
        if not new and not mirror_mod._is_link_or_reparse(final):
            try:  # a snapshot kept by an older pull may lack the mark
                mirror_mod.write_mark_of_the_web(final)
            except OSError:
                logger.debug("Mark-of-the-Web not added to an already kept snapshot")
    encrypted = looks_encrypted(bytes(head))
    if not encrypted:
        logger.warning("The pulled VPS snapshot does not look encrypted; it is kept, check agent-svc snapshots")
    total, count = folder_totals(dest)
    return PullResult(path=final, name=final.name, size=written, sha256=sha, new=new, encrypted=encrypted,
                      total_bytes=total, count=count)


def _place_snapshot(part: Path, dest: Path, name: str, *, size: int, sha256: str) -> tuple[Path, bool]:
    """Renames part to name (or 'name (n)'), never over an existing file; an identical file already there is kept
    and (path, False) returned."""
    for n in range(0, mirror_mod.CLASH_MAX + 1):
        candidate = dest / (name if n == 0 else mirror_mod.clash_name(name, n))
        if candidate.exists() or os.path.lexists(candidate):
            try:
                same = candidate.is_file() and candidate.stat().st_size == size and \
                    _sha256_file(candidate) == sha256
            except OSError:
                same = False
            if same:
                return candidate, False
            continue
        try:
            mirror_mod._place(part, candidate)  # pylint: disable=protected-access  # the mirror's no-overwrite move
        except FileExistsError:
            continue
        except OSError:
            raise BackupPullError("rename") from None
        return candidate, True
    raise BackupPullError("too_many_clashes")


# --- state ---------------------------------------------------------------------------------------------------

def read_state(run_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads((Path(run_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_state(run_dir: Path, doc: dict[str, Any]) -> None:
    path = Path(run_dir) / STATE_FILE
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Backup pull state not saved (%s)", type(exc).__name__)
        try:
            tmp.unlink()
        except OSError:
            pass


def record_pull(run_dir: Path, *, result: PullResult | None, error: str | None, now: float) -> None:
    doc = read_state(run_dir)
    if result is not None:
        doc.update({"last_success": round(now, 3), "last_size": result.size, "last_name": result.name,
                    "last_new": result.new, "encrypted": result.encrypted, "total_bytes": result.total_bytes,
                    "count": result.count})
        doc.pop("last_error", None)
    else:
        doc.update({"last_error": error or "failed", "last_error_at": round(now, 3)})
    write_state(run_dir, doc)


# --- node task -----------------------------------------------------------------------------------------------

class BackupPuller:
    """The node's daily pull (see the module docstring)."""

    def __init__(self, node: Any, *, http: Callable[[], mirror_mod.NodeHttp | None] | None = None,
                 dest: Path | None = None, wall: Callable[[], float] = time.time, daily_s: float = DAILY_S,
                 check_s: float = CHECK_S, retry_s: float = RETRY_S) -> None:
        self._node = node
        self._http = http or self._default_http
        self._dest = dest
        self._wall = wall
        self.daily_s, self.check_s, self.retry_s = daily_s, check_s, retry_s
        self._last_failure: float | None = None

    def _default_http(self) -> mirror_mod.NodeHttp | None:
        try:
            return mirror_mod.node_http(self._node)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    @property
    def dest(self) -> Path:
        return self._dest if self._dest is not None else backups_dir()

    def due(self) -> bool:
        now = self._wall()
        if self._last_failure is not None and now - self._last_failure < self.retry_s:
            return False
        last = read_state(Path(self._node.run_dir)).get("last_success")
        return not isinstance(last, (int, float)) or now - float(last) >= self.daily_s

    def pull_now(self) -> PullResult:
        """One pull now (synchronous); recorded in the state file and the audit log."""
        http = self._http()
        now = self._wall()
        audit = getattr(getattr(self._node, "audit", None), "record", None)
        try:
            if http is None:
                raise BackupPullError("no_link")
            result = pull_latest(http, self.dest, wall=self._wall)
        except BackupPullError as exc:
            self._last_failure = now
            record_pull(Path(self._node.run_dir), result=None, error=exc.reason, now=now)
            if audit is not None:
                audit("backup_pull_failed", reason=exc.reason)
            raise
        self._last_failure = None
        record_pull(Path(self._node.run_dir), result=result, error=None, now=now)
        if audit is not None:
            audit("backup_pulled", count=result.size, state="new" if result.new else "same",
                  reason="encrypted" if result.encrypted else "plaintext_suspected")
        logger.info("VPS backup pulled: %d bytes (%s); %d snapshot(s), %d bytes kept", result.size,
                    "new" if result.new else "already held", result.count, result.total_bytes)
        return result

    async def run(self) -> None:
        while True:
            if self.due():
                try:
                    await asyncio.to_thread(self.pull_now)
                except BackupPullError as exc:
                    logger.warning("VPS backup pull failed (%s); next try in %.0f min", exc.reason,
                                   self.retry_s / 60)
                except Exception:  # pylint: disable=broad-exception-caught  # the task never ends
                    logger.exception("VPS backup pull crashed")
            await asyncio.sleep(self.check_s)


def install(node: Any) -> None:
    """NodeRuntime plugin hook: the daily pull as an active-only node task (never in --canary)."""
    puller = BackupPuller(node)
    node.backup_puller = puller
    node.add_task(puller.run, active_only=True, name="backup-pull")
    logger.info("Desktop backup pull installed (daily)")


# --- CLI ('agent_ctl backup pull') ---------------------------------------------------------------------------

def _cli_http(transport: Any = None) -> tuple[mirror_mod.NodeHttp, Path]:
    """The node's own signed client and run dir, from node_config.json, keys.dpapi and the PC root .env."""
    # pylint: disable=import-outside-toplevel
    from src.core.desktop import settings as node_settings

    node_settings.neutralize_dotenv()
    from src.core.desktop.node import KeyLoader

    cfg = node_settings.load_node_config()
    keys = KeyLoader(node_settings.node_home()).load(cfg)
    current = node_settings.SettingsSource(Path(cfg.data_root) / node_settings.ROOT_ENV_FILE,
                                           Path(cfg.data_root)).load()
    base = node_settings.link_target(current.link_url)[0]
    run_dir = Path(cfg.run_dir)
    offset = saved_clock_offset(run_dir)
    return mirror_mod.NodeHttp(base, keys.signing_key, clock_offset=lambda: offset,
                               transport=transport), run_dir


def saved_clock_offset(run_dir: Path, *, now: float | None = None) -> float | None:
    """The VPS-minus-laptop clock offset the node last measured (run/desktop_node.json offset_s), when that status
    was written at most OFFSET_MAX_AGE_S ago and the value is sane; else None (the own clock)."""
    from src.core.desktop import health  # pylint: disable=import-outside-toplevel

    doc = health.read_json(Path(run_dir) / health.STATUS_FILE) or {}
    offset, updated = doc.get("offset_s"), doc.get("updated_at")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or abs(offset) > OFFSET_MAX_ABS_S:
        return None
    if isinstance(updated, bool) or not isinstance(updated, (int, float)):
        return None
    age = (time.time() if now is None else now) - float(updated)
    return float(offset) if -60.0 <= age <= OFFSET_MAX_AGE_S else None


def cli_main(argv: Sequence[str] | None = None, *,
             http_factory: Callable[[], tuple[mirror_mod.NodeHttp, Path]] | None = None,
             out: Any = None, err: Any = None) -> int:
    """'agent_ctl backup pull [--dest PATH]': pulls the newest encrypted VPS snapshot now. On success it prints
    KEY=VALUE lines on stdout (PULLED|KEPT=<path>, NEW, SIZE, SHA256, ENCRYPTED, TOTAL_BYTES, COUNT; see
    PullResult.lines) and exits 0; else ERROR=<reason> (and HINT=<text> on a signature refusal) on stderr, exit 1
    (pull failed) or 2 (configuration)."""
    out = out or sys.stdout
    err = err or sys.stderr
    parser = argparse.ArgumentParser(prog="agent_ctl backup pull",
                                     description="Pull the newest encrypted VPS snapshot to this PC (C-122)")
    parser.add_argument("--dest", help=f"target folder (default %%LOCALAPPDATA%%\\JARVIS\\{BACKUP_DIR_NAME})")
    args = parser.parse_args(list(argv or []))
    try:
        http, run_dir = (http_factory or _cli_http)()
    except Exception as exc:  # pylint: disable=broad-exception-caught  # config, key or DPAPI problem
        print(f"ERROR=config_{type(exc).__name__}", file=err)
        return EXIT_CONFIG
    dest = Path(args.dest).expanduser() if args.dest else backups_dir()
    now = time.time()
    try:
        result = pull_latest(http, dest)
    except BackupPullError as exc:
        record_pull(run_dir, result=None, error=exc.reason, now=now)
        print(f"ERROR={exc.reason}", file=err)
        if exc.reason == "http_auth":
            print("HINT=the bridge refused the signature: the laptop clock may be off the VPS clock (sync Windows "
                  "time, or start the node once so it measures the offset), or the link keys differ", file=err)
        return EXIT_FAILED
    record_pull(run_dir, result=result, error=None, now=now)
    for line in result.lines():
        print(line, file=out)
    return EXIT_PULLED


if __name__ == "__main__":
    sys.exit(cli_main(sys.argv[1:]))
