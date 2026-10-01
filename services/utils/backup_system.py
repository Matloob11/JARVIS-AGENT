"""
# services/utils/backup_system.py
Local (PC) backup system for JARVIS-AGENT (plan task C-11 / OP-07).

JarvisBackup zips the PC's runtime data into <backup_root>/backup_<YYYYmmdd_HHMMSS>.zip,
with paths kept relative to the project root (.env, conversations/..., chroma_db/...).

* backup_root = JARVIS_BACKUP_DIR or %USERPROFILE%/JARVIS-backups/local. It is always an
  absolute path outside the repo, so a backup never ends up in git or inside the tree it copies.
* Default sources: see DEFAULT_SOURCES. A missing source only logs a warning.
* SQLite files (for example chroma_db/chroma.sqlite3) are copied with SQLite's online backup
  API, so a running agent cannot leave a half-written copy. Their -wal/-shm files are then
  skipped because the copy already contains them.
* rotate_backups(keep=30) deletes ONLY files named backup_<YYYYmmdd_HHMMSS>[_N].zip directly
  inside backup_root. It never touches any other file.
* perform_tree_backup() writes tree_<ts>.tar.gz with the whole repo except .venv_312,
  stonix_ui/node_modules, pretrained_models and caches. Rotation never deletes these.

CLI: scripts/ops/backup_local.py (--now, --full-tree, --list).
"""

from __future__ import annotations

import datetime
import fnmatch
import glob
import os
import re
import sqlite3
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from services.utils.jarvis_config import config
from services.utils.jarvis_logger import jarvis_log as log

# Default PC sources, relative to the project root. Globs are allowed; missing ones only warn.
DEFAULT_SOURCES: tuple[str, ...] = (
    ".env",
    "deploy/windows-local/.env",
    "conversations",
    "chroma_db",
    "data/identity",
    "Jarvis_Outputs",
    "backups/checkpoints",
    "logs/*.log",
    "logs/telemetry.jsonl",
    "plan.md",
    "done_highlight.md",
)
# Matched (case-sensitive) against the archive path and the file name.
DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = ("__pycache__/*", "*/__pycache__/*", "*.pyc")
# Left out of perform_tree_backup(), relative to the project root.
TREE_EXCLUDE_DIRS: tuple[str, ...] = (".venv_312", "stonix_ui/node_modules", "pretrained_models")
# Cache folders skipped at any depth in perform_tree_backup().
TREE_CACHE_DIR_NAMES: frozenset[str] = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})

BACKUP_NAME_RE = re.compile(r"^backup_(\d{8}_\d{6})(?:_(\d+))?\.zip$")
TREE_NAME_RE = re.compile(r"^tree_(\d{8}_\d{6})(?:_(\d+))?\.tar\.gz$")
DEFAULT_KEEP = 30

_SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
_SQLITE_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
_SQLITE_MAGIC = b"SQLite format 3\x00"
_PARTIAL_SUFFIX = ".partial"


def _abs_under(path: str, root: str) -> str:
    """Expands ~ and env vars; a relative path is taken relative to `root`."""
    expanded = os.path.expandvars(os.path.expanduser(str(path)))
    if not os.path.isabs(expanded):
        expanded = os.path.join(root, expanded)
    return os.path.abspath(expanded)


def default_backup_root(project_root: str | None = None) -> str:
    """JARVIS_BACKUP_DIR (a relative value is under the project root) or %USERPROFILE%/JARVIS-backups/local."""
    root = project_root or config.project_root
    env_dir = (os.getenv("JARVIS_BACKUP_DIR") or "").strip()
    if env_dir:
        return _abs_under(env_dir, root)
    home = os.getenv("USERPROFILE") or os.path.expanduser("~")
    return os.path.abspath(os.path.join(home, "JARVIS-backups", "local"))


def _name_sort_key(match: re.Match[str]) -> tuple[str, int]:
    return match.group(1), int(match.group(2) or 0)


def _discard(path: str | None) -> None:
    """Removes a temporary file this module created itself (never a backup)."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            log.warning("[BACKUP] Could not remove temporary file %s: %s", path, e)


class JarvisBackup:
    """
    Automated backup system for JARVIS-AGENT.
    Archives critical PC data (env files, conversations, memory, identity, outputs, logs).
    """

    def __init__(self, source_dirs: Sequence[str] | None = None, backup_root: str | None = None, *,
                 exclude_globs: Sequence[str] | None = None, project_root: str | None = None) -> None:
        """
        source_dirs: files, folders or globs; relative ones are under the project root.
            None or empty means DEFAULT_SOURCES.
        backup_root: target folder; relative means under the project root. None means
            JARVIS_BACKUP_DIR or %USERPROFILE%/JARVIS-backups/local.
        exclude_globs: patterns skipped inside the zip (default DEFAULT_EXCLUDE_GLOBS).
        project_root: the tree to back up (default: this repo).
        """
        self.project_root = os.path.abspath(project_root or config.project_root)
        sources = list(source_dirs) if source_dirs else list(DEFAULT_SOURCES)
        self.source_dirs = [_abs_under(s, self.project_root) for s in sources]
        self.backup_root = (_abs_under(backup_root, self.project_root) if backup_root
                            else default_backup_root(self.project_root))
        self.exclude_globs = list(DEFAULT_EXCLUDE_GLOBS if exclude_globs is None else exclude_globs)
        self.last_warnings: list[str] = []
        self._sqlite_snapshotted: set[str] = set()

    # ------------------------------------------------------------------ helpers
    def _warn(self, message: str) -> None:
        self.last_warnings.append(message)
        log.warning("[BACKUP] %s", message)

    def _norm(self, path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _is_within_backup_root(self, path: str) -> bool:
        root = self._norm(self.backup_root)
        target = self._norm(path)
        return target == root or target.startswith(root.rstrip("\\/") + os.sep)

    def _unique_path(self, prefix: str, suffix: str) -> str:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self.backup_root, f"{prefix}_{stamp}")
        path, n = base + suffix, 1
        while os.path.exists(path) or os.path.exists(path + _PARTIAL_SUFFIX):
            n += 1
            path = f"{base}_{n}{suffix}"
        return path

    def _arcname(self, path: str, source: str) -> str:
        """Path inside the zip: relative to the project root, or _external/<source name>/... for outside paths."""
        try:
            rel = os.path.relpath(path, self.project_root)
        except ValueError:  # another drive on Windows
            rel = os.pardir
        if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
            src_base = os.path.basename(source.rstrip("\\/")) or "source"
            inner = os.path.relpath(path, source) if os.path.isdir(source) else ""
            rel = os.path.join("_external", src_base, inner) if inner not in ("", os.curdir) \
                else os.path.join("_external", src_base)
        return rel.replace(os.sep, "/")

    def _is_excluded(self, arcname: str) -> bool:
        name = arcname.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(arcname, pat) or fnmatch.fnmatchcase(name, pat)
                   for pat in self.exclude_globs)

    def _expand_source(self, source: str) -> list[str]:
        if any(ch in source for ch in "*?["):
            return sorted(glob.glob(source))
        return [source] if os.path.lexists(source) else []

    def _first_visit(self, path: str, seen: set[str]) -> bool:
        """False for a file already added or one inside the backup root (a backup never holds itself)."""
        key = self._norm(path)
        if key in seen or self._is_within_backup_root(path):
            return False
        seen.add(key)
        return True

    def _iter_folder(self, folder: str, seen: set[str]) -> Iterator[tuple[str, str]]:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if not self._is_within_backup_root(os.path.join(dirpath, d)))
            for name in sorted(filenames):  # sorted: a DB file comes before its -wal/-shm files
                full = os.path.join(dirpath, name)
                if self._first_visit(full, seen):
                    yield full, self._arcname(full, folder)

    def _iter_source_files(self) -> Iterator[tuple[str, str]]:
        """Yields (absolute file path, archive name) for every file of every source, without duplicates."""
        seen: set[str] = set()
        for source in self.source_dirs:
            matches = self._expand_source(source)
            if not matches:
                self._warn(f"Backup source not found: {source}")
                continue
            for match in matches:
                if os.path.isdir(match):
                    yield from self._iter_folder(match, seen)
                elif os.path.isfile(match):
                    if self._first_visit(match, seen):
                        yield match, self._arcname(match, match)
                else:
                    self._warn(f"Backup source is not a regular file or folder: {match}")

    @staticmethod
    def _looks_like_sqlite(path: str) -> bool:
        if not path.lower().endswith(_SQLITE_SUFFIXES):
            return False
        try:
            with open(path, "rb") as fh:
                return fh.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
        except OSError:
            return False

    def _sqlite_snapshot(self, path: str) -> str | None:
        """Consistent copy of a (possibly open) SQLite DB via the online backup API; None if that fails."""
        fd, tmp = tempfile.mkstemp(prefix=".sqlite_", suffix=".tmp", dir=self.backup_root)
        os.close(fd)
        src = dst = None
        try:
            # read-only URI: the source DB is never written, and never created if it vanished
            src = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            dst = sqlite3.connect(tmp)
            src.backup(dst)
            return tmp
        except (sqlite3.Error, OSError) as e:
            self._warn(f"SQLite online copy failed for {path} ({e}); copying the raw file instead")
            if dst is not None:
                dst.close()
                dst = None
            _discard(tmp)
            return None
        finally:
            if dst is not None:
                dst.close()
            if src is not None:
                src.close()

    def _is_sqlite_side_file(self, path: str) -> bool:
        """True for X-wal/X-shm/X-journal when X was already stored as a consistent snapshot."""
        for suffix in _SQLITE_SIDE_SUFFIXES:
            if path.endswith(suffix) and self._norm(path[: -len(suffix)]) in self._sqlite_snapshotted:
                return True
        return False

    def _add_file(self, zf: zipfile.ZipFile, path: str, arcname: str) -> bool:
        """Adds one file. A locked/vanished file only warns; other errors (disk full) propagate."""
        try:
            if self._looks_like_sqlite(path):
                snapshot = self._sqlite_snapshot(path)
                if snapshot:
                    try:
                        zf.write(snapshot, arcname)
                    finally:
                        _discard(snapshot)
                    self._sqlite_snapshotted.add(self._norm(path))
                    return True
            zf.write(path, arcname)
            return True
        except (PermissionError, FileNotFoundError, IsADirectoryError) as e:
            self._warn(f"Skipped {arcname}: {e}")
            return False

    # --------------------------------------------------------------- public API
    def perform_backup(self) -> str | None:
        """Zips every source into <backup_root>/backup_<ts>.zip. Returns the zip path, or None on failure."""
        self.last_warnings = []
        self._sqlite_snapshotted = set()
        try:
            os.makedirs(self.backup_root, exist_ok=True)
        except OSError as e:
            log.error("[ERROR] Backup root %s cannot be created: %s", self.backup_root, e)
            return None

        for source in self.source_dirs:
            if self._is_within_backup_root(source.split("*", 1)[0]):
                self._warn(f"Source {source} is inside the backup folder {self.backup_root} and is skipped "
                           "(a backup never holds itself); choose a backup_root outside the sources.")

        final_path = self._unique_path("backup", ".zip")
        partial = final_path + _PARTIAL_SUFFIX
        log.info("[BACKUP] Starting backup to %s ...", final_path)
        added = 0
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED,
                                 allowZip64=True, strict_timestamps=False) as zf:
                for path, arcname in self._iter_source_files():
                    if self._is_excluded(arcname) or self._is_sqlite_side_file(path):
                        continue
                    if self._add_file(zf, path, arcname):
                        added += 1
            if added == 0:
                log.error("[ERROR] Backup failed: none of the sources had any file")
                _discard(partial)
                return None
            os.replace(partial, final_path)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as e:
            log.error("[ERROR] Backup failed: %s", e)
            _discard(partial)
            return None

        log.info("[SUCCESS] Backup completed: %s (%d files, %d warnings)",
                 final_path, added, len(self.last_warnings))
        return final_path

    def rotate_backups(self, keep: int = DEFAULT_KEEP) -> list[str]:
        """
        Keeps the newest `keep` backup_<ts>.zip files in backup_root and deletes older ones.
        Only files matching backup_<YYYYmmdd_HHMMSS>[_N].zip directly inside backup_root are
        ever deleted. Returns the deleted paths.
        """
        if keep < 1:
            log.warning("[BACKUP] rotate_backups(keep=%s) refused: keep must be at least 1", keep)
            return []
        try:
            names = os.listdir(self.backup_root)
        except FileNotFoundError:
            return []

        candidates: list[tuple[tuple[str, int], str]] = []
        for name in names:
            match = BACKUP_NAME_RE.fullmatch(name)
            if not match:
                continue
            path = os.path.join(self.backup_root, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            candidates.append((_name_sort_key(match), path))
        candidates.sort(reverse=True)

        removed: list[str] = []
        for _, path in candidates[keep:]:
            try:
                os.remove(path)
                removed.append(path)
                log.info("[CLEANUP] Rotating old backup: %s", path)
            except OSError as e:
                log.warning("[BACKUP] Could not rotate %s: %s", path, e)
        return removed

    def perform_tree_backup(self, exclude_dirs: Sequence[str] = TREE_EXCLUDE_DIRS, *,
                            include_git: bool = True) -> str | None:
        """
        Writes <backup_root>/tree_<ts>.tar.gz with the whole project tree under its folder name,
        without `exclude_dirs` (relative to the project root), cache folders and *.pyc.
        Returns the archive path, or None on failure. Rotation never deletes these archives.
        """
        self.last_warnings = []
        try:
            os.makedirs(self.backup_root, exist_ok=True)
        except OSError as e:
            log.error("[ERROR] Backup root %s cannot be created: %s", self.backup_root, e)
            return None

        final_path = self._unique_path("tree", ".tar.gz")
        partial = final_path + _PARTIAL_SUFFIX
        top = os.path.basename(self.project_root.rstrip("\\/")) or "project"
        excluded = {self._norm(os.path.join(self.project_root, d)) for d in exclude_dirs}
        if not include_git:
            excluded.add(self._norm(os.path.join(self.project_root, ".git")))

        log.info("[BACKUP] Starting full-tree backup to %s ...", final_path)
        count = 0
        try:
            with tarfile.open(partial, "w:gz", compresslevel=6) as tar:
                for dirpath, dirnames, filenames in os.walk(self.project_root):
                    dirnames[:] = sorted(
                        d for d in dirnames
                        if d not in TREE_CACHE_DIR_NAMES
                        and self._norm(os.path.join(dirpath, d)) not in excluded
                        and not self._is_within_backup_root(os.path.join(dirpath, d))
                    )
                    rel_dir = os.path.relpath(dirpath, self.project_root)
                    arc_dir = top if rel_dir == os.curdir else f"{top}/{rel_dir.replace(os.sep, '/')}"
                    try:
                        tar.add(dirpath, arcname=arc_dir, recursive=False)
                    except (PermissionError, FileNotFoundError) as e:
                        self._warn(f"Skipped folder {arc_dir}: {e}")
                    for name in sorted(filenames):
                        full = os.path.join(dirpath, name)
                        if name.endswith(".pyc") or self._is_within_backup_root(full):
                            continue
                        try:
                            tar.add(full, arcname=f"{arc_dir}/{name}", recursive=False)
                            count += 1
                        except (PermissionError, FileNotFoundError) as e:
                            # raised before tarfile writes anything for this member
                            self._warn(f"Skipped {arc_dir}/{name}: {e}")
            os.replace(partial, final_path)
        except (OSError, tarfile.TarError) as e:
            log.error("[ERROR] Full-tree backup failed: %s", e)
            _discard(partial)
            return None

        log.info("[SUCCESS] Full-tree backup completed: %s (%d files, %d warnings)",
                 final_path, count, len(self.last_warnings))
        return final_path

    def list_backups(self) -> list[dict[str, Any]]:
        """backup_*.zip and tree_*.tar.gz in backup_root, newest first: {name, path, kind, size, mtime}."""
        try:
            names = os.listdir(self.backup_root)
        except FileNotFoundError:
            return []
        rows: list[tuple[tuple[str, int], dict[str, Any]]] = []
        for name in names:
            path = os.path.join(self.backup_root, name)
            for kind, pattern in (("data", BACKUP_NAME_RE), ("tree", TREE_NAME_RE)):
                match = pattern.fullmatch(name)
                if match and os.path.isfile(path):
                    stat = os.stat(path)
                    rows.append((_name_sort_key(match), {
                        "name": name, "path": path, "kind": kind,
                        "size": stat.st_size, "mtime": stat.st_mtime,
                    }))
                    break
        rows.sort(key=lambda row: row[0], reverse=True)
        return [info for _, info in rows]


if __name__ == "__main__":
    backup_system = JarvisBackup()
    if backup_system.perform_backup():
        backup_system.rotate_backups()
