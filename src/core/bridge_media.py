"""
# src/core/bridge_media.py
The bridge's single media path (plan task C-117; contract K-73 MEDIA-RELAY + FILES + BLOBS, DASH-C21/DASH-C30),
built on the C-115 library services/utils/desktop_protocol.py and wired into the HTTP app by src/core/ui_bridge.py.

One path serves both the dashboard (owner session, or the legacy token until C-22) and the laptop Desktop Node
(K-70 PC-signed HTTP). The bridge mounts jarvis_shared, jarvis_outputs and jarvis_agent_snapshots read-only and
never writes them; it offers:

- the media index (GET /media/index) over the allowed subtrees, with the K-73 exclusions;
- a media file (GET /media/file) with HTTP Range;
- an upload inbox (POST /desktop/blob -> GET /internal/blob/<id>) that keeps blobs in RAM only, under a hard
  cap, deleted after the first fetch; a fetched blob is served in chunks (blob_chunks) and stays counted until the
  server has accepted its last chunk;
- the newest encrypted backup snapshot (GET /desktop/backup/latest).

Symlink-race safety is the core rule. Every path is opened component by component with O_NOFOLLOW (directories
via dir_fd and O_DIRECTORY on POSIX); only regular files are listed or streamed, and a symlink swapped in between
the listing and the open fails the open (ELOOP) and is refused. The relpath is validated first, so '..', an
absolute path, a backslash, a drive letter or a control character never reaches the filesystem at all.

Cross-platform import safety: the relay runs in the Linux bridge container, but this module is imported on the
Windows laptop too (its tests run there). On POSIX the O_NOFOLLOW + dir_fd walk is the only path (Linux and macOS
always have it; a POSIX build without it refuses every media request with 503 rather than fall back). Only Windows
uses the lexical + realpath fallback, which refuses symlinks without the race guard (the race tests are POSIX-only).
Nothing here logs a path, a filename, an argument or any body.
"""
from __future__ import annotations

import errno
import gc
import hashlib
import math
import mimetypes
import os
import re
import secrets
import stat as stat_mod
import threading
import time
import weakref
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from services.utils import desktop_protocol as dp

# ---------------------------------------------------------------- constants (K-73)

AREA_SHARED, AREA_OUTPUTS = "shared", "outputs"
AREAS = frozenset({AREA_SHARED, AREA_OUTPUTS})

# Allowed subtrees per area (K-73 "Allowed paths").
SHARED_SUBDIRS = ("Generated_Images", "QR_Codes", "Downloads")   # Downloads includes YouTube/
OUTPUTS_SUBDIRS = ("Downloads", "leads")
OUTPUTS_TOP_SUFFIX = ".zip"                                       # outputs also serves top-level *.zip

# Exclusions (K-73): never listed or served, whatever the subtree. A dotfile is any path component starting with
# '.', so a file inside a dot-directory (Downloads/.cache/x.bin, Generated_Images/.thumbnails/y.png) is excluded too.
EXCLUDED_NAMES = frozenset({"sim_lookup_history.jsonl"})
EXCLUDED_SUFFIXES = (".part", ".ytdl", ".tmp")
EXCLUDE_AGE_S = 5.0            # a file modified less than 5 s ago is not listed (the index cursor is now - 5 s)
SHA256_MAX_BYTES = 25 * 1024 * 1024   # sha256 is computed for files up to this size ("sha256 when known")
_HASH_CHUNK = 1024 * 1024
_STREAM_CHUNK = 256 * 1024
_MAX_COMPONENT = 255
_MAX_DEPTH = 16                # directory levels below the area root that are walked or opened (fd + recursion bound)
_SHA_CACHE_MAX = 4096          # remembered digests, so a poll does not re-hash every unchanged file

# Blobs (laptop -> VPS): in RAM only.
BLOB_MAX_BYTES = dp.HTTP_MAX_BODY_BYTES   # 30 MiB, the signed per-blob cap
BLOB_MAX_TOTAL = 256 * 1024 * 1024        # 256 MB across all live blobs
BLOB_TTL_S = 15 * 60.0                    # 15 min
BLOB_MAX_CONCURRENT = 2                   # at most two uploads being streamed at once
BLOB_ID_LEN = 32                          # 16 bytes as hex

# POSIX O_NOFOLLOW + dir_fd are available: the race-safe walk is used only then.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)   # a FIFO swapped in must never block the open (fstat refuses it)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_BINARY | _O_NONBLOCK | _O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
_RANGE_NUM = re.compile(r"[0-9]{1,19}")       # ASCII digits only, at most 19 (no sign, no overflow)
# CPython never puts os.lstat in supports_dir_fd: the dir_fd lstat is os.stat(name, dir_fd=, follow_symlinks=False)
# (fstatat with AT_SYMLINK_NOFOLLOW), which needs os.stat in both supports_dir_fd and supports_follow_symlinks.
_DIR_FD_FUNCS = getattr(os, "supports_dir_fd", frozenset())
_HAS_DIRFD = bool(
    os.name == "posix" and _O_NOFOLLOW and _O_DIRECTORY
    and os.open in _DIR_FD_FUNCS
    and os.stat in _DIR_FD_FUNCS
    and os.stat in getattr(os, "supports_follow_symlinks", frozenset())
    and os.listdir in getattr(os, "supports_fd", frozenset()))
# The lexical fallback (no race guard) is allowed on Windows only; a POSIX build without dir_fd refuses (503).
_LEXICAL_OK = os.name == "nt"


# ---------------------------------------------------------------- errors


class MediaError(Exception):
    """A refused media request; `status` is the HTTP status the route returns, `reason` a short code (never a
    path or any content). A failed open, a traversal attempt and a swapped-in symlink all land here as 404, so
    nothing tells a client whether a given path exists."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"{status}:{reason}")
        self.status = status
        self.reason = reason


# ---------------------------------------------------------------- relpath validation (traversal)


def safe_components(relpath: Any) -> list[str]:
    """The relpath split into components, or MediaError(400, 'traversal') for anything unsafe.

    Refused: a non-string, empty, an absolute path, '.' or '..', a component with a backslash, a drive colon, a
    NUL or any other control character, or one longer than 255 bytes. So nothing that could escape the root, name
    a Windows stream or confuse the OS ever reaches an open().
    """
    if not isinstance(relpath, str) or not relpath:
        raise MediaError(400, "traversal")
    out: list[str] = []
    for part in relpath.split("/"):
        if part in ("", ".", ".."):
            raise MediaError(400, "traversal")
        if "\\" in part or ":" in part or len(part.encode("utf-8", "surrogatepass")) > _MAX_COMPONENT:
            raise MediaError(400, "traversal")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in part):
            raise MediaError(400, "traversal")
        out.append(part)
    return out


def _excluded_name(name: str) -> bool:
    """A name K-73 never lists or serves (a dotfile, a temp suffix, the SIM history), whatever its mtime."""
    return (name in EXCLUDED_NAMES or name.startswith(".")
            or name.endswith(EXCLUDED_SUFFIXES))


def _hidden_component(name: str) -> bool:
    """A dot-directory or dotfile component (K-73 'dotfiles'): never walked into, listed or opened."""
    return name.startswith(".")


def _excluded_components(components: list[str]) -> bool:
    """True when a relpath is excluded: any component is hidden (a dot-directory anywhere on the path) or the
    final name is an excluded file name."""
    return (not components or any(_hidden_component(comp) for comp in components)
            or _excluded_name(components[-1]))


def _allowed_relpath(area: str, components: list[str]) -> bool:
    """True when the components fall inside an allowed subtree of the area (K-73)."""
    if area == AREA_SHARED:
        return len(components) >= 2 and components[0] in SHARED_SUBDIRS
    if area == AREA_OUTPUTS:
        if len(components) >= 2 and components[0] in OUTPUTS_SUBDIRS:
            return True
        return len(components) == 1 and components[0].endswith(OUTPUTS_TOP_SUFFIX)
    return False


def content_type(relpath: str) -> str:
    guessed, _ = mimetypes.guess_type(relpath)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------- the race-safe opener


def _open_root_dir(root: str) -> int:
    """A dir fd of the mount root, opened with O_NOFOLLOW (POSIX)."""
    return os.open(root, _DIR_FLAGS)


def _open_child_dir(name: str, parent_fd: int) -> int:
    return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)


def _lstat_at(name: str, parent_fd: int) -> os.stat_result:
    """lstat of `name` relative to a dir fd (fstatat with AT_SYMLINK_NOFOLLOW): a symlink is reported, never
    followed."""
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)


def _require_safe_platform() -> None:
    """MediaError(503) on a POSIX build without dir_fd + O_NOFOLLOW: the race-unsafe fallback is Windows-only."""
    if not _HAS_DIRFD and not _LEXICAL_OK:
        raise MediaError(503, "platform")


def _open_regular_posix(root: str, components: list[str]) -> tuple[int, os.stat_result]:
    """Walks the components under root with dir_fd + O_NOFOLLOW and opens the final regular file.

    A symlink anywhere on the path (including one swapped in after a listing) makes the open fail with ELOOP,
    which becomes MediaError(404); so does a missing component or a non-regular final entry.
    """
    try:
        dir_fd = _open_root_dir(root)
    except OSError:
        raise MediaError(404, "no_file") from None   # a missing or symlinked mount root
    try:
        for comp in components[:-1]:
            nxt = _open_child_dir(comp, dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        fd = os.open(components[-1], _FILE_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise MediaError(404, "no_file" if exc.errno in (errno.ENOENT, errno.ENOTDIR) else "not_regular") from None
    finally:
        os.close(dir_fd)
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise MediaError(404, "no_file") from None
    if not stat_mod.S_ISREG(st.st_mode):
        os.close(fd)
        raise MediaError(404, "not_regular")
    return fd, st


def _open_regular_lexical(root: str, components: list[str]) -> tuple[int, os.stat_result]:
    """The Windows-only fallback (no dir_fd there): no component may be a symlink and the realpath must stay under
    the root. It carries no race guard, so open_regular never uses it on POSIX (the race tests are POSIX-only)."""
    full = os.path.join(root, *components)
    walked = root
    for comp in components:
        walked = os.path.join(walked, comp)
        if os.path.islink(walked):
            raise MediaError(404, "not_regular")
    try:
        root_real = os.path.realpath(root)
        real = os.path.realpath(full)
        st = os.lstat(full)
    except OSError:
        raise MediaError(404, "no_file") from None
    if not (real == root_real or real.startswith(root_real + os.sep)):
        raise MediaError(404, "no_file")
    # NTFS also opens a file by an alias the literal checks never saw: an 8.3 short name (THUMBN~1 for
    # .thumbnails), another letter case, or trailing dots and spaces. The relpath must name the file exactly as it
    # resolves, and the resolved names are checked again, so an alias can never reach a dot-directory or an
    # excluded name. The index lists real names only, so a mirrored fetch always matches.
    resolved = os.path.relpath(real, root_real).split(os.sep)
    if resolved != components or _excluded_components(resolved):
        raise MediaError(404, "no_file")
    if stat_mod.S_ISLNK(st.st_mode) or not stat_mod.S_ISREG(st.st_mode):
        raise MediaError(404, "not_regular")
    try:
        fd = os.open(full, _FILE_FLAGS)
    except OSError:
        raise MediaError(404, "no_file") from None
    try:
        fst = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise MediaError(404, "no_file") from None
    if not stat_mod.S_ISREG(fst.st_mode):
        os.close(fd)
        raise MediaError(404, "not_regular")
    return fd, fst


def open_regular(root: str, components: list[str]) -> tuple[int, os.stat_result]:
    """(fd, stat) of a regular file under root, opened without ever following a symlink. MediaError otherwise."""
    if _HAS_DIRFD:
        return _open_regular_posix(root, components)
    _require_safe_platform()
    return _open_regular_lexical(root, components)


def _hash_fd(fd: int) -> str | None:
    """sha256 of an fd opened with _FILE_FLAGS (O_NOFOLLOW + O_NONBLOCK), then closed. None unless it is a regular
    file, so a FIFO or device swapped in after the listing is never read and never blocks a worker thread."""
    try:
        if not stat_mod.S_ISREG(os.fstat(fd).st_mode):
            return None
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, _HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


def _hash_regular_posix(name: str, parent_fd: int, size: int) -> str | None:
    if size > SHA256_MAX_BYTES:
        return None
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError:
        return None   # a symlink swapped in after the listing fails with ELOOP
    return _hash_fd(fd)


def _hash_regular_lexical(path: str, size: int) -> str | None:
    if size > SHA256_MAX_BYTES:
        return None
    try:
        fd = os.open(path, _FILE_FLAGS)
    except OSError:
        return None
    return _hash_fd(fd)


class _FdStream:
    """Iterator over `length` bytes of an owned fd from `start`. The fd is closed after the last chunk, on a read
    error, on close() and when the object is dropped, including one never iterated: a client that disconnects
    before the response headers are sent never starts the body, so a generator's `finally` would never run and
    the descriptor would leak."""

    __slots__ = ("_fd", "_start", "_remaining", "_chunk")

    def __init__(self, fd: int, start: int, length: int, chunk_size: int) -> None:
        self._fd = fd
        self._start = max(0, int(start))
        self._remaining = max(0, int(length))
        self._chunk = max(1, int(chunk_size))

    def __iter__(self) -> _FdStream:
        return self

    def __next__(self) -> bytes:
        if self._fd < 0 or self._remaining <= 0:
            self.close()
            raise StopIteration
        try:
            if self._start:
                os.lseek(self._fd, self._start, os.SEEK_SET)
                self._start = 0
            data = os.read(self._fd, min(self._chunk, self._remaining))
        except BaseException:
            self.close()
            raise
        if not data:
            self.close()
            raise StopIteration
        self._remaining -= len(data)
        if self._remaining <= 0:
            self.close()   # released right after the last chunk
        return data

    def close(self) -> None:
        fd, self._fd = self._fd, -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass

    def __del__(self) -> None:
        self.close()


def stream_fd(fd: int, start: int, length: int, *, chunk_size: int = _STREAM_CHUNK) -> Iterator[bytes]:
    """Yields `length` bytes from `fd` starting at `start`. The iterator owns the fd: it is closed after the last
    chunk, on an error, and when the iterator is dropped even if it never started (a StreamingResponse whose
    client left before the headers were sent still releases the descriptor)."""
    return _FdStream(fd, start, length, chunk_size)


def parse_range(header: Any, size: int) -> tuple[int, int] | None:
    """(start, end) inclusive for a single 'bytes=' range, None when there is no Range header, or
    MediaError(416) when the range cannot be satisfied. Only the first range of a list is honoured."""
    if not header:
        return None
    text = str(header).strip()
    if not text.startswith("bytes="):
        raise MediaError(416, "range")
    spec = text[len("bytes="):].split(",", 1)[0].strip()
    if "-" not in spec:
        raise MediaError(416, "range")
    first, last = (part.strip() for part in spec.split("-", 1))
    if (first and not _RANGE_NUM.fullmatch(first)) or (last and not _RANGE_NUM.fullmatch(last)):
        raise MediaError(416, "range")   # a sign, a non-ASCII digit or an absurd length
    if first == "":
        if last == "":
            raise MediaError(416, "range")
        suffix = int(last)
        if suffix <= 0:
            raise MediaError(416, "range")
        start, end = max(0, size - suffix), size - 1
    else:
        start = int(first)
        end = int(last) if last != "" else size - 1
    if size <= 0 or start < 0 or start >= size or end < start:
        raise MediaError(416, "range")
    return start, min(end, size - 1)


# ---------------------------------------------------------------- the media relay


@dataclass
class _WalkCtx:
    area: str
    now: float
    entries: list[dict[str, Any]]


class MediaRelay:
    """Serves the media index and media files from the two read-only mounts (K-73).

    shared_dir and outputs_dir are callables looked up on every request, so a reconfigured root is picked up and
    the module needs no configuration at import time (it is imported on Windows too).
    """

    def __init__(self, *, shared_dir: Callable[[], str], outputs_dir: Callable[[], str],
                 clock: Callable[[], float] = time.time) -> None:
        self._shared_dir = shared_dir
        self._outputs_dir = outputs_dir
        self._clock = clock
        # (area, relpath, dev, ino, size, mtime_ns) -> sha256; a changed file has a new key, so a stale digest is
        # never served, and the oldest keys are dropped past _SHA_CACHE_MAX.
        self._sha_cache: OrderedDict[tuple[Any, ...], str] = OrderedDict()
        self._sha_lock = threading.Lock()

    def _root(self, area: str) -> str:
        if area == AREA_SHARED:
            return self._shared_dir()
        if area == AREA_OUTPUTS:
            return self._outputs_dir()
        raise MediaError(400, "area")

    def build_index(self, area: str, cursor: float | None = None) -> dict[str, Any]:
        """The index of one area: every allowed, non-excluded regular file modified at least 5 s ago.

        Entries are sorted by (mtime_ns, relpath) and numbered (seq from 1); each carries relpath, size, mtime_ns
        and, for a file up to SHA256_MAX_BYTES, sha256. `cursor` (epoch seconds) returns only files modified after
        it. The returned `cursor` is the listing time minus 5 s, the value the node sends back next time.
        """
        if area not in AREAS:
            raise MediaError(400, "area")
        cutoff_ns: int | None = None
        if cursor is not None:
            try:
                cursor_f = float(cursor)
            except (TypeError, ValueError):
                raise MediaError(400, "cursor") from None
            cursor_ns = cursor_f * 1_000_000_000
            if not math.isfinite(cursor_ns):
                raise MediaError(400, "cursor")   # inf/nan (or a value overflowing to inf) has no ns integer
            cutoff_ns = int(cursor_ns)
        now = self._clock()
        ctx = _WalkCtx(area=area, now=now, entries=[])
        self._collect(ctx)
        ctx.entries.sort(key=lambda item: (item["mtime_ns"], item["relpath"]))
        for seq, entry in enumerate(ctx.entries, start=1):
            entry["seq"] = seq
        rows = [self._public_entry(entry) for entry in ctx.entries
                if cutoff_ns is None or entry["mtime_ns"] > cutoff_ns]
        return {"area": area, "cursor": round(now - EXCLUDE_AGE_S, 3), "count": len(rows), "entries": rows}

    @staticmethod
    def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
        row = {"seq": entry["seq"], "relpath": entry["relpath"], "size": entry["size"],
               "mtime_ns": entry["mtime_ns"]}
        if entry.get("sha256"):
            row["sha256"] = entry["sha256"]
        return row

    def _collect(self, ctx: _WalkCtx) -> None:
        root = self._root(ctx.area)
        subdirs = SHARED_SUBDIRS if ctx.area == AREA_SHARED else OUTPUTS_SUBDIRS
        if _HAS_DIRFD:
            try:
                root_fd = _open_root_dir(root)
            except OSError:
                return
            try:
                for name in subdirs:
                    self._descend_posix(name, root_fd, name, ctx)
                if ctx.area == AREA_OUTPUTS:
                    self._top_zips_posix(root_fd, ctx)
            finally:
                os.close(root_fd)
        else:
            _require_safe_platform()
            for name in subdirs:
                self._descend_lexical(os.path.join(root, name), name, ctx)
            if ctx.area == AREA_OUTPUTS:
                self._top_zips_lexical(root, ctx)

    # --- POSIX walk (dir_fd + O_NOFOLLOW) ---

    def _descend_posix(self, name: str, parent_fd: int, prefix: str, ctx: _WalkCtx) -> None:
        try:
            st = _lstat_at(name, parent_fd)
        except OSError:
            return
        if not stat_mod.S_ISDIR(st.st_mode):
            return  # a symlink or a file where a subtree dir is expected: never followed
        try:
            dir_fd = _open_child_dir(name, parent_fd)
        except OSError:
            return
        try:
            self._walk_posix(dir_fd, prefix, ctx, 1)
        finally:
            os.close(dir_fd)

    def _walk_posix(self, dir_fd: int, prefix: str, ctx: _WalkCtx, depth: int) -> None:
        try:
            names = os.listdir(dir_fd)
        except OSError:
            return
        for name in names:
            if _hidden_component(name):
                continue  # a dotfile or a dot-directory (K-73): never listed, never walked into
            try:
                st = _lstat_at(name, dir_fd)
            except OSError:
                continue
            if stat_mod.S_ISLNK(st.st_mode):
                continue  # a symlink is never listed or followed
            rel = f"{prefix}/{name}"
            if stat_mod.S_ISDIR(st.st_mode):
                if depth >= _MAX_DEPTH:
                    continue  # deeper than the walk bound: not listed (open_file refuses it too)
                try:
                    child = _open_child_dir(name, dir_fd)
                except OSError:
                    continue
                try:
                    self._walk_posix(child, rel, ctx, depth + 1)
                finally:
                    os.close(child)
            elif stat_mod.S_ISREG(st.st_mode):
                self._add(ctx, rel, name, st, sha=lambda n=name, s=st.st_size: _hash_regular_posix(n, dir_fd, s))

    def _top_zips_posix(self, root_fd: int, ctx: _WalkCtx) -> None:
        try:
            names = os.listdir(root_fd)
        except OSError:
            return
        for name in names:
            if not name.endswith(OUTPUTS_TOP_SUFFIX):
                continue
            try:
                st = _lstat_at(name, root_fd)
            except OSError:
                continue
            if stat_mod.S_ISREG(st.st_mode):
                self._add(ctx, name, name, st, sha=lambda n=name, s=st.st_size: _hash_regular_posix(n, root_fd, s))

    # --- lexical walk (Windows-only fallback) ---

    def _descend_lexical(self, path: str, prefix: str, ctx: _WalkCtx) -> None:
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                return
        except OSError:
            return
        self._walk_lexical(path, prefix, ctx, 1)

    def _walk_lexical(self, path: str, prefix: str, ctx: _WalkCtx, depth: int) -> None:
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            return
        for entry in entries:
            if _hidden_component(entry.name):
                continue  # a dotfile or a dot-directory (K-73): never listed, never walked into
            try:
                if entry.is_symlink():
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            rel = f"{prefix}/{entry.name}"
            if stat_mod.S_ISDIR(st.st_mode):
                if depth < _MAX_DEPTH:
                    self._walk_lexical(entry.path, rel, ctx, depth + 1)
            elif stat_mod.S_ISREG(st.st_mode):
                self._add(ctx, rel, entry.name, st,
                          sha=lambda p=entry.path, s=st.st_size: _hash_regular_lexical(p, s))

    def _top_zips_lexical(self, root: str, ctx: _WalkCtx) -> None:
        try:
            with os.scandir(root) as it:
                entries = list(it)
        except OSError:
            return
        for entry in entries:
            if not entry.name.endswith(OUTPUTS_TOP_SUFFIX):
                continue
            try:
                if entry.is_symlink():
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat_mod.S_ISREG(st.st_mode):
                self._add(ctx, entry.name, entry.name, st,
                          sha=lambda p=entry.path, s=st.st_size: _hash_regular_lexical(p, s))

    def _add(self, ctx: _WalkCtx, relpath: str, name: str, st: os.stat_result,
             *, sha: Callable[[], str | None]) -> None:
        if _excluded_name(name) or _excluded_components(relpath.split("/")):
            return  # also a guard for every walker: nothing under a dot-directory is ever listed
        if st.st_mtime_ns > int((ctx.now - EXCLUDE_AGE_S) * 1_000_000_000):
            return  # modified less than 5 s ago: not listed yet (the cursor is now - 5 s)
        digest = self._cached_sha((ctx.area, relpath, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns), sha)
        ctx.entries.append({"relpath": relpath, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest})

    def _cached_sha(self, key: tuple[Any, ...], sha: Callable[[], str | None]) -> str | None:
        """The file's sha256 from the cache, or computed (and remembered) when its identity, size or mtime is new.
        A digest that could not be computed (too large, unreadable, swapped) is not remembered."""
        with self._sha_lock:
            known = self._sha_cache.get(key)
            if known is not None:
                self._sha_cache.move_to_end(key)
                return known
        digest = sha()
        if digest is not None:
            with self._sha_lock:
                self._sha_cache[key] = digest
                self._sha_cache.move_to_end(key)
                while len(self._sha_cache) > _SHA_CACHE_MAX:
                    self._sha_cache.popitem(last=False)
        return digest

    def open_file(self, area: str, relpath: str) -> tuple[int, os.stat_result]:
        """(fd, stat) of a media file: the relpath is validated, kept inside an allowed subtree and opened without
        ever following a symlink; a swapped-in symlink, an excluded name or any dot-directory on the path is
        refused (MediaError 404), so the node can never fetch what the index would not list."""
        if area not in AREAS:
            raise MediaError(400, "area")
        components = safe_components(relpath)
        if (len(components) - 1 > _MAX_DEPTH or not _allowed_relpath(area, components)
                or _excluded_components(components)):
            raise MediaError(404, "no_file")
        return open_regular(self._root(area), components)


# ---------------------------------------------------------------- blob inbox (laptop -> VPS)


class _BlobBuffer(bytearray):
    """The RAM buffer of one blob. A bytearray subclass only so that weakref.finalize can watch it: its bytes are
    freed exactly when the last reference to it (the store, take()'s memoryview, the blob_chunks() stream and any
    chunk view of it the server still holds) is gone."""

    __slots__ = ("__weakref__",)


def new_blob_buffer(size: int) -> bytearray:
    """A zeroed buffer of `size` bytes that BlobStore.put() keeps as is (no copy) and can track after take()."""
    return _BlobBuffer(max(0, int(size)))


class BlobStore:
    """In-RAM upload inbox (K-73 Blobs): blobs live only here, at most BLOB_MAX_TOTAL together, each for
    BLOB_TTL_S, and each is deleted the first time it is fetched. At most BLOB_MAX_CONCURRENT uploads stream at
    once. A bridge restart loses everything in flight, which the tool reports as retryable (UPLOAD_FAILED).

    The cap covers every blob byte in RAM: uploads in flight, stored blobs and fetched blobs still being sent.
    acquire(reserve) books the signed length against BLOB_MAX_TOTAL before a single body byte is buffered,
    put(..., reserved=) turns that booking into the stored blob, and release(reserved) frees whatever was not
    stored. take() removes a blob from the store (first-fetch delete) but keeps its bytes counted as 'taken' until
    its buffer is actually freed.

    GET /internal/blob/<id> must serve the view through blob_chunks() in a StreamingResponse. The stream holds the
    buffer until the server has accepted its last chunk, and uvicorn awaits its write flow control before it
    accepts each next chunk, so the copies the server makes of the body (h11 joins every chunk into new bytes for
    the transport) are never more than about one chunk plus the transport's write high-water mark beyond the
    count. A one-shot Response would hand over the whole body at once: the server would copy all of it into its
    transport buffer and the count would drop while those bytes still wait for the client. After a client
    disconnect or a send error the stream is dropped and the buffer freed with it.

    A weakref finalizer on the buffer reports the release through a lock-free queue (it can run inside the garbage
    collector, at any point and in any thread, so it never takes the lock), and every operation drains that queue
    under the lock. So stored + taken + booked bytes never pass the cap, and the uncounted rest of a fetch in
    progress stays bounded by the server's flow control.
    """

    def __init__(self, *, max_total: int = BLOB_MAX_TOTAL, ttl_s: float = BLOB_TTL_S,
                 max_concurrent: int = BLOB_MAX_CONCURRENT, max_blob: int = BLOB_MAX_BYTES,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max_total = max_total
        self._ttl = ttl_s
        self._max_concurrent = max_concurrent
        self._max_blob = max_blob
        self._clock = clock
        self._lock = threading.Lock()
        self._blobs: OrderedDict[str, tuple[_BlobBuffer, float]] = OrderedDict()  # id -> (data, expires_at)
        self._total = 0
        self._reserved = 0   # signed lengths booked by uploads still streaming
        self._taken = 0      # fetched blobs whose bytes are still in RAM (their chunk stream is still being sent)
        self._freed: deque[int] = deque()   # sizes of taken buffers freed since the last drain (finalizer -> lock)
        self._active = 0

    @property
    def max_blob(self) -> int:
        return self._max_blob

    def acquire(self, reserve: int = 0) -> bool:
        """Takes one of the concurrent-upload slots and books `reserve` bytes (the signed length) against the
        total cap. False when both slots are busy; MediaError(413) when `reserve` is above the per-blob cap and
        MediaError(507) when stored + taken + booked + `reserve` would pass the total cap (no slot is taken then)."""
        reserve = int(reserve)
        if reserve < 0:
            raise MediaError(400, "length")
        if reserve > self._max_blob:
            raise MediaError(413, "size")
        for attempt in range(2):
            with self._lock:
                if self._active >= self._max_concurrent:
                    return False
                self._purge_locked(self._clock())
                if self._in_ram_locked() + reserve <= self._max_total:
                    self._active += 1
                    self._reserved += reserve
                    return True
                retry = attempt == 0 and self._taken > 0
            if not retry:
                break
            _collect_cycles()   # a finished response caught in a reference cycle still pins its blob: reclaim once
        raise MediaError(507, "capacity")

    def release(self, reserved: int = 0) -> None:
        """Frees the upload slot and any booking that put() did not turn into a stored blob."""
        with self._lock:
            if self._active > 0:
                self._active -= 1
            self._reserved = max(0, self._reserved - max(0, int(reserved)))

    def _drain_locked(self) -> None:
        """Uncounts the taken buffers that have been freed since the last call (queued by their finalizers)."""
        while True:
            try:
                size = self._freed.popleft()
            except IndexError:
                return
            self._taken = max(0, self._taken - size)

    def _in_ram_locked(self) -> int:
        """Stored + taken-but-not-yet-freed + booked bytes: what the total cap is checked against."""
        self._drain_locked()
        return self._total + self._taken + self._reserved

    def _purge_locked(self, now: float) -> None:
        for blob_id in [bid for bid, (_data, exp) in self._blobs.items() if exp <= now]:
            data, _exp = self._blobs.pop(blob_id)
            self._total -= len(data)

    def put(self, data: bytes | bytearray, *, reserved: int = 0) -> str:
        """Stores a blob (after its length and sha256 were verified) and returns its id. `reserved` is the booking
        acquire() made for this upload: it is consumed on success and left untouched on failure (the caller's
        release(reserved) frees it). MediaError(507) when the blob would push stored + taken + other bookings over
        the cap, MediaError(413) when the blob itself is too large.

        A buffer from new_blob_buffer() (what receive_blob fills) is kept as given, so storing never needs a second
        copy of the blob in RAM; the caller must not change it afterwards. Any other bytes-like value is copied
        once into such a buffer, so that take() can tell when its bytes leave RAM."""
        size = len(data)
        if size > self._max_blob:
            raise MediaError(413, "size")
        own_req = max(0, int(reserved))
        for attempt in range(2):
            with self._lock:
                now = self._clock()
                self._purge_locked(now)
                own = min(own_req, self._reserved)
                if self._in_ram_locked() - own + size <= self._max_total:
                    buffer = data if isinstance(data, _BlobBuffer) else _BlobBuffer(data)
                    blob_id = secrets.token_hex(BLOB_ID_LEN // 2)
                    self._blobs[blob_id] = (buffer, now + self._ttl)
                    self._reserved -= own
                    self._total += size
                    return blob_id
                retry = attempt == 0 and self._taken > 0
            if not retry:
                break
            _collect_cycles()   # as in acquire(): reclaim a sent blob pinned by a reference cycle, once
        raise MediaError(507, "capacity")

    def take(self, blob_id: Any) -> memoryview | None:
        """The blob's bytes as a read-only memoryview over the stored buffer (no copy), removed from the store
        (first-fetch delete); None when it is unknown or expired. Serve it with blob_chunks(view) in a
        StreamingResponse, never as a one-shot Response body (see the class docstring).

        The bytes stay counted against the total cap ('taken_bytes') until the buffer is freed, that is until this
        view and everything holding it (the blob_chunks() stream, chunk views the server still holds) are gone:
        once the server has accepted the last chunk, or once the response was dropped on a client disconnect or a
        send error. Bytes the server has accepted but not yet written to the socket are no longer counted; chunked
        flow control keeps them to about one chunk plus the transport's write high-water mark."""
        if not isinstance(blob_id, str) or len(blob_id) != BLOB_ID_LEN:
            return None
        with self._lock:
            now = self._clock()
            self._purge_locked(now)
            self._drain_locked()
            item = self._blobs.pop(blob_id, None)
            if item is None:
                return None
            data, exp = item
            size = len(data)
            self._total -= size
            if exp <= now:
                return None
            self._taken += size
            # deque.append is atomic and takes no lock, so the finalizer is safe wherever the buffer dies (inside
            # the garbage collector, while this very lock is held, or in another thread).
            finalizer = weakref.finalize(data, self._freed.append, size)
            finalizer.atexit = False
            return memoryview(data).toreadonly()

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._drain_locked()
            return {"count": len(self._blobs), "total_bytes": self._total, "reserved_bytes": self._reserved,
                    "taken_bytes": self._taken, "active_uploads": self._active}


class _BlobChunks:
    """Async iterator over a taken blob's bytes in chunk_size memoryview slices (no copy). It holds the view until
    it is exhausted or closed and then drops it, so the blob stays counted while chunks are still to be sent. A
    plain class rather than an async generator: when a response is dropped mid-stream (a client disconnect, a
    cancelled task), the view goes with the last reference at once instead of waiting for the event loop to
    finalize a suspended generator."""

    __slots__ = ("_view", "_offset", "_chunk")

    def __init__(self, view: memoryview, chunk_size: int) -> None:
        self._view: memoryview | None = view
        self._offset = 0
        self._chunk = max(1, int(chunk_size))

    def __aiter__(self) -> _BlobChunks:
        return self

    async def __anext__(self) -> memoryview:
        if self._view is None or self._offset >= len(self._view):
            self._view = None   # no local keeps the view either, so a traceback held after the stop pins nothing
            raise StopAsyncIteration
        view = self._view
        start = self._offset
        self._offset = min(len(view), start + self._chunk)
        return view[start:self._offset]

    async def aclose(self) -> None:
        self._view = None


def blob_chunks(data: memoryview | bytes | bytearray, *,
                chunk_size: int = _STREAM_CHUNK) -> AsyncIterator[memoryview]:
    """The GET /internal/blob/<id> body: take()'s view served in chunks, as
    StreamingResponse(blob_chunks(view), headers={"Content-Length": str(len(view))},
    media_type="application/octet-stream"). Each chunk is its own more_body message, so uvicorn awaits its write
    flow control (the transport draining below its high-water mark) before it accepts the next one, and the blob
    stays counted in the store until the last chunk has been accepted (see BlobStore)."""
    view = data if isinstance(data, memoryview) else memoryview(data)
    if view.ndim != 1 or view.itemsize != 1:
        view = view.cast("B")
    return _BlobChunks(view, chunk_size)


def _collect_cycles() -> None:
    """A full garbage collection, run at most once per refused booking and only while taken blobs are counted:
    a sent response kept alive by a reference cycle (an exception traceback, say) is then freed, and its
    finalizer uncounts it, instead of the cap refusing uploads until the collector happens to run."""
    gc.collect()


def verify_pc_headers(*, public_key: Any, seen: Any, headers: Iterable[tuple[Any, Any]] | Any, method: str,
                      path: Any, query: Any, max_length: int, content_length: int | None,
                      now: float | None = None) -> dp.HttpAuth:
    """K-70 PC-signed HTTP headers, mapped to a MediaError whose status is HTTP_STATUS[reason]. The length cap
    and the signature are checked here, before any body byte is read."""
    try:
        return dp.verify_http_headers(headers, method=method, path=path, query=query, public_key=public_key,
                                      seen=seen, max_length=max_length, content_length=content_length, now=now)
    except dp.DesktopLinkError as exc:
        raise MediaError(dp.HTTP_STATUS.get(exc.reason, 400), exc.reason) from None


async def receive_blob(*, public_key: Any, seen: Any, headers: Iterable[tuple[Any, Any]] | Any, method: str,
                       path: Any, query: Any, content_length: int | None, body_iter: AsyncIterator[bytes],
                       store: BlobStore, now: float | None = None) -> tuple[str, int]:
    """POST /desktop/blob (K-73): verify the PC-signed length and sha256 BEFORE reading the body, take an upload
    slot and book the signed length against the in-RAM total, then stream and hash the body under the signed
    length (a hard cap). Returns (blob_id, size).

    The signed length cap (413), a missing or bad signature (400/403), both slots busy (429) and a full store
    (507) are all raised before `body_iter` is ever iterated, so an oversize, unsigned or over-capacity body is
    never read. The body is kept in RAM only and the sha256 is checked at the end; the stored bytes are the
    agent-sealed blob (the bridge never decrypts them). The booking is released on every failure path.
    """
    auth = verify_pc_headers(public_key=public_key, seen=seen, headers=headers, method=method, path=path,
                             query=query, max_length=store.max_blob, content_length=content_length, now=now)
    booked = auth.length
    if not store.acquire(booked):
        raise MediaError(429, "too_many_uploads")
    try:
        verifier = dp.HttpBodyVerifier(auth)
        # Exactly the booked (signed) length, allocated once and filled in place: no growth over-allocation and
        # no second copy when it is stored, so RAM stays within the booking.
        buffer = new_blob_buffer(booked)
        offset = 0
        with memoryview(buffer) as view:
            async for chunk in body_iter:
                if not chunk:
                    continue
                verifier.update(chunk)   # DesktopLinkError('length') once the signed length is exceeded
                end = offset + len(chunk)
                view[offset:end] = chunk
                offset = end
        verifier.finish()            # exact length (offset == booked) and sha256
        size = offset
        blob_id = store.put(buffer, reserved=booked)
        booked = 0                   # the booking is now the stored blob
    except dp.DesktopLinkError as exc:
        raise MediaError(dp.HTTP_STATUS.get(exc.reason, 400), exc.reason) from None
    finally:
        store.release(booked)
    return blob_id, size


# ---------------------------------------------------------------- backup snapshot pull


class BackupRelay:
    """GET /desktop/backup/latest (K-73): streams the newest file from jarvis_agent_snapshots, mounted read-only.
    The snapshots are already encrypted by the agent service; the bridge only picks the newest and streams it."""

    def __init__(self, *, snapshots_dir: Callable[[], str], clock: Callable[[], float] = time.time) -> None:
        self._snapshots_dir = snapshots_dir
        self._clock = clock

    def open_latest(self) -> tuple[int, os.stat_result, str]:
        """(fd, stat, filename) of the newest regular, non-symlink snapshot, opened without following a symlink.
        MediaError(404) when the directory is missing or holds no snapshot."""
        root = self._snapshots_dir()
        newest: tuple[int, str] | None = None
        try:
            with os.scandir(root) as it:
                entries = list(it)
        except OSError:
            raise MediaError(404, "no_snapshot") from None
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat_mod.S_ISREG(st.st_mode) and not _excluded_name(entry.name):  # no dotfile or partial write
                if newest is None or st.st_mtime_ns > newest[0]:
                    newest = (st.st_mtime_ns, entry.name)
        if newest is None:
            raise MediaError(404, "no_snapshot")
        fd, fst = open_regular(root, [newest[1]])
        return fd, fst, newest[1]
