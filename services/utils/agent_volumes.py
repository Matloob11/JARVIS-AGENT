"""
# services/utils/agent_volumes.py
The VPS brain's data in a backup_vps.sh backup: agent-volumes.tar.enc (plan task C-128; contracts K-75, K-77).

Both sides of one format, sealed with services.utils.backup_crypto (the sealed format and the key pair of the daily
snapshot, K-77: X25519 + HKDF-SHA256 + ChaCha20-Poly1305 records of 1 MiB):

- seal: backup_vps.sh runs, inside the agent image (the running jarvis-agent, else jarvis-agent-svc, else a one-off
  'compose run' of agent), as uid 10001 under 'env -i' (no secret in the process), from /app (the image's code):

      python -m services.utils.agent_volumes /app <BACKUP_RECIPIENT_PUBLIC_KEY>

  It writes a tar of /app/{conversations,chroma_db,data} to stdout, sealed while it is made, so no plain byte reaches
  stdout or a file. stderr ends with one 'AGENT_SEAL key=value ...' line of counts (recipient=<fingerprint of the key,
  as 'backup_key.py public' prints it>), or 'AGENT_SEAL_ERROR <reason>'. Exit 0 sealed, 1 failed (stdout holds no
  usable file), 2 usage (no usable key).
- check: scripts/ops/restore_agent_data.py on the PC opens it with backup_crypto.iter_open and reads it through
  inventory(); scripts/ops/backup_key.py decrypt opens it like a snapshot.

Members: the three folders, every folder and regular file under them, and last AGENT-BACKUP.json (kind
jarvis_agent_volumes: the counts, the SQLite copies, missing roots). Every SQLite file is an online copy (the backup API
from a read-only connection, so a running agent keeps writing; then PRAGMA integrity_check), kept in memory, never in a
file; its -wal/-shm/-journal stay out. Other files are read whole, again once when they changed meanwhile. Links and
special files are counted, never followed; a file or folder the running agent removed between its listing and its read
(a temp file renamed into place) is counted as vanished, while any other read error fails the whole step.
'.pre-restore-*' folders at the top of a volume (live data that a restore moved aside) stay out. Nothing here prints a
file's name or content to stdout, and no error message holds content.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import sys
import tarfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote

from services.utils import backup_crypto as bc

ROOTS = ("conversations", "chroma_db", "data")
META = "AGENT-BACKUP.json"
META_KIND = "jarvis_agent_volumes"
ARCHIVE = "agent-volumes.tar.enc"
ASIDE_PREFIX = ".pre-restore-"
SIDECARS = ("-wal", "-shm", "-journal")
SQLITE_MAGIC = b"SQLite format 3\x00"
COUNT_NAMES = ("files", "dirs", "bytes", "sqlite", "sqlite_raw", "sqlite_bad", "changed", "links", "special", "aside",
               "vanished")
_READ_SIZE = 1 << 20
_META_MAX = 4 << 20


class AgentVolumesError(ValueError):
    """The opened data is not a complete JARVIS agent backup. The message never holds a name or content."""


# ---------------------------------------------------------------- seal (inside the agent image)


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def _online_copy(path: str) -> tuple[bytes, str]:
    """A consistent copy of a live SQLite database (WAL content included) and its integrity check, in memory."""
    src = sqlite3.connect("file:" + quote(path) + "?mode=ro", uri=True, timeout=30)
    try:
        dst = sqlite3.connect(":memory:")
        try:
            src.backup(dst)
            check = str(dst.execute("PRAGMA integrity_check").fetchone()[0])
            data = dst.serialize()
        finally:
            dst.close()
    finally:
        src.close()
    return data, check


def _read_once(path: str) -> tuple[bytes, os.stat_result, bool]:
    before = os.lstat(path)
    with open(path, "rb") as handle:
        data = handle.read()
    after = os.lstat(path)
    stable = (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size) and len(data) == after.st_size
    return data, after, stable


def _read_stable(path: str) -> tuple[bytes, os.stat_result, bool]:
    """The file's bytes and stat; read again once when it changed while it was read (changed=True if it still did)."""
    data, st, stable = _read_once(path)
    if not stable:
        data, st, stable = _read_once(path)
    return data, st, not stable


def _member(name: str, st: os.stat_result | None, kind: bytes = tarfile.REGTYPE, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = size
    info.mode = stat.S_IMODE(st.st_mode) if st is not None else 0o700
    info.uid = st.st_uid if st is not None else getattr(os, "getuid", int)()
    info.gid = st.st_gid if st is not None else getattr(os, "getgid", int)()
    info.mtime = int(st.st_mtime) if st is not None else int(time.time())
    return info


class _Walk:
    """One sealing run: the tar members of the three volumes and the record of what went in."""

    def __init__(self, base: str, tar: tarfile.TarFile) -> None:
        self.base = base
        self.tar = tar
        self.counts = dict.fromkeys(COUNT_NAMES, 0)
        self.meta: dict[str, Any] = {"kind": META_KIND, "v": 1,
                                     "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     "roots": {}, "sqlite_files": [], "missing": []}

    def _add(self, arc: str, st: os.stat_result, data: bytes) -> None:
        self.tar.addfile(_member(arc, st, size=len(data)), io.BytesIO(data))
        self.counts["files"] += 1
        self.counts["bytes"] += len(data)

    def _walk_error(self, error: OSError) -> None:
        # a folder the running agent removed after it was listed is gone, not lost; any other folder that cannot be
        # read fails the whole step: never an incomplete backup that looks whole
        if isinstance(error, FileNotFoundError):
            self.counts["vanished"] += 1
            return
        raise error

    def root(self, root: str) -> None:
        top = os.path.join(self.base, root)
        if not os.path.isdir(top) or os.path.islink(top):
            self.meta["missing"].append(root)
            self.tar.addfile(_member(root, None, tarfile.DIRTYPE))
            return
        files0, bytes0 = self.counts["files"], self.counts["bytes"]
        for dirpath, dirnames, filenames in os.walk(top, onerror=self._walk_error):
            rel = os.path.relpath(dirpath, self.base).replace(os.sep, "/")
            try:
                dir_st = os.lstat(dirpath)
            except FileNotFoundError:
                self.counts["vanished"] += 1
                dirnames[:] = []
                continue
            self.tar.addfile(_member(rel, dir_st, tarfile.DIRTYPE))
            self.counts["dirs"] += 1
            if dirpath == top:
                kept = [d for d in dirnames if not d.startswith(ASIDE_PREFIX)]
                self.counts["aside"] += len(dirnames) - len(kept)
                dirnames[:] = kept
            real = []
            for name in sorted(dirnames):
                if os.path.islink(os.path.join(dirpath, name)):
                    self.counts["links"] += 1
                else:
                    real.append(name)
            dirnames[:] = real
            self._files(dirpath, rel, sorted(filenames))
        self.meta["roots"][root] = {"files": self.counts["files"] - files0, "bytes": self.counts["bytes"] - bytes0}

    def _files(self, dirpath: str, rel: str, names: list[str]) -> None:
        stats: dict[str, os.stat_result] = {}
        for name in names:
            try:
                stats[name] = os.lstat(os.path.join(dirpath, name))
            except FileNotFoundError:   # removed by the running agent after the listing (a temp file renamed away)
                self.counts["vanished"] += 1
        dbs = {name for name, st in stats.items()
               if stat.S_ISREG(st.st_mode) and _is_sqlite(os.path.join(dirpath, name))}
        for name, st in stats.items():
            if stat.S_ISLNK(st.st_mode):
                self.counts["links"] += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                self.counts["special"] += 1
                continue
            if any(name == db + side for db in dbs for side in SIDECARS):
                continue   # the online copy holds what the -wal held
            try:
                if name in dbs:
                    self._sqlite(dirpath, rel, name, st)
                    continue
                data, st, moved = _read_stable(os.path.join(dirpath, name))
            except FileNotFoundError:
                self.counts["vanished"] += 1
                continue
            self.counts["changed"] += int(moved)
            self._add(rel + "/" + name, st, data)

    def _sqlite(self, dirpath: str, rel: str, name: str, st: os.stat_result) -> None:
        path, arc = os.path.join(dirpath, name), rel + "/" + name
        try:
            data, check = _online_copy(path)
        except (sqlite3.Error, OSError) as exc:
            # no online copy: the raw files go in (the database and whichever side files exist), named in the record
            raw, raw_st, _moved = _read_stable(path)   # FileNotFoundError here: the database itself vanished
            self.counts["sqlite_raw"] += 1
            self.meta["sqlite_files"].append({"path": arc, "copy": "raw", "why": type(exc).__name__})
            self._add(arc, raw_st, raw)
            for side in SIDECARS:
                if not os.path.isfile(path + side):
                    continue
                try:
                    raw, raw_st, _moved = _read_stable(path + side)
                except FileNotFoundError:   # checkpointed away meanwhile
                    continue
                self._add(arc + side, raw_st, raw)
            return
        self.counts["sqlite"] += 1
        if check != "ok":
            self.counts["sqlite_bad"] += 1
        self.meta["sqlite_files"].append({"path": arc, "copy": "online", "bytes": len(data), "integrity": check[:80]})
        self._add(arc, st, data)

    def record(self) -> None:
        self.meta.update(self.counts)
        blob = json.dumps(self.meta, indent=1, sort_keys=True).encode("utf-8")
        self.tar.addfile(_member(META, None, size=len(blob)), io.BytesIO(blob))


def seal_volumes(base: str, recipient: bc.X25519PublicKey, out: BinaryIO, *,
                 chunk_size: int = bc.DEFAULT_CHUNK_SIZE) -> dict[str, int]:
    """Writes the sealed tar of <base>/{conversations,chroma_db,data} into out; returns the counts plus 'sealed', the
    number of sealed bytes written. On an exception out holds an unfinished sealed stream (no final chunk), which never
    opens: the caller drops it."""
    writer = bc.SealingWriter(out, recipient, chunk_size=chunk_size)
    with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        walk = _Walk(base, tar)
        for root in ROOTS:
            walk.root(root)
        walk.record()
    writer.close()
    return {**walk.counts, "sealed": writer.bytes_out}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("AGENT_SEAL_ERROR usage: python -m services.utils.agent_volumes <base folder> <recipient public key, "
              "64 hex digits>", file=sys.stderr)
        return 2
    base, key_text = args
    try:
        recipient = bc.parse_public_key(key_text)
    except bc.SnapshotCryptoError as exc:
        print(f"AGENT_SEAL_ERROR {exc}", file=sys.stderr)
        return 2
    try:
        counts = seal_volumes(base, recipient, sys.stdout.buffer)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # one line, the type only: never content
        print(f"AGENT_SEAL_ERROR {type(exc).__name__}", file=sys.stderr)
        return 1
    line = " ".join(f"{name}={counts[name]}" for name in sorted(COUNT_NAMES))
    print(f"AGENT_SEAL {line} sealed={counts['sealed']} recipient={bc.fingerprint(recipient)}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- check (on the PC)


@dataclass
class Inventory:
    """What an opened agent backup holds: counts only (no name leaves this object through lines())."""

    files: int = 0
    dirs: int = 0
    bytes: int = 0
    roots: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ROOTS, 0))
    meta: dict[str, Any] = field(default_factory=dict)
    sealed_bytes: int = 0
    plain_bytes: int = 0   # the opened tar's whole length, its zero padding included (what restore_agent_data streams)

    def lines(self) -> list[str]:
        meta = self.meta
        return [f"MEMBERS files={self.files} dirs={self.dirs} bytes={self.bytes} "
                + " ".join(f"{root}={self.roots[root]}" for root in ROOTS),
                f"SQLITE online={meta.get('sqlite', 0)} raw={meta.get('sqlite_raw', 0)} "
                f"bad={meta.get('sqlite_bad', 0)} changed_while_read={meta.get('changed', 0)}",
                f"CREATED_UTC={meta.get('created_utc', '?')}"]


def check_member(info: tarfile.TarInfo) -> str | None:
    """The member's root folder when it is a folder or regular file under one of ROOTS; None for the record."""
    name = info.name
    path = PurePosixPath(name)
    if name == META:
        if not info.isfile():
            raise AgentVolumesError("the AGENT-BACKUP.json record is not a file")
        return None
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in ROOTS or "\\" in name:
        raise AgentVolumesError("a member lies outside conversations/, chroma_db/ and data/")
    if not (info.isdir() or info.isfile()):
        raise AgentVolumesError("a member is neither a folder nor a regular file")
    return path.parts[0]


class _ChunkReader(io.RawIOBase):
    """A read-only stream over an iterator of byte chunks (tarfile's stream mode reads from it)."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        super().__init__()
        self._chunks = chunks
        self._buf = b""
        self.total = 0   # bytes taken from the chunks so far

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                return 0
            self.total += len(self._buf)
        size = min(len(buffer), len(self._buf))
        buffer[:size] = self._buf[:size]
        self._buf = self._buf[size:]
        return size


def _record(handle: Any, size: int) -> dict[str, Any]:
    if size > _META_MAX:
        raise AgentVolumesError("the AGENT-BACKUP.json record is too large")
    try:
        meta = json.loads(handle.read().decode("utf-8"))
    except ValueError:
        raise AgentVolumesError("the AGENT-BACKUP.json record is not JSON") from None
    if not isinstance(meta, dict) or meta.get("kind") != META_KIND:
        raise AgentVolumesError("the AGENT-BACKUP.json record is not a JARVIS agent backup record")
    return meta


def inventory(chunks: Iterable[bytes]) -> Inventory:
    """Reads a whole opened agent backup (backup_crypto.iter_open's chunks, each verified on the way) and checks every
    member; the AGENT-BACKUP.json record must come last. The chunks are read to their end even after the tar's own
    end, so the sealed stream's final chunk and its end are verified too. Raises AgentVolumesError for the layout and
    lets backup_crypto.SnapshotCryptoError through."""
    inv = Inventory()
    seen_meta = False
    raw = _ChunkReader(iter(chunks))
    reader = io.BufferedReader(raw, _READ_SIZE)
    try:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for info in tar:
                if seen_meta:
                    raise AgentVolumesError("a member follows the AGENT-BACKUP.json record")
                root = check_member(info)
                handle = tar.extractfile(info) if info.isfile() else None
                if root is None:
                    seen_meta = True
                    inv.meta = _record(handle, info.size)
                elif handle is None:
                    inv.dirs += 1
                else:
                    while block := handle.read(_READ_SIZE):
                        inv.bytes += len(block)
                    inv.files += 1
                    inv.roots[root] += 1
    except tarfile.TarError:
        raise AgentVolumesError("the opened data is not a complete tar archive") from None
    while reader.read(_READ_SIZE):
        pass   # the rest of the sealed stream (the tar's zero padding): its final chunk and end verify here
    if not seen_meta:
        raise AgentVolumesError("the AGENT-BACKUP.json record is missing (an incomplete backup)")
    inv.plain_bytes = raw.total
    return inv


if __name__ == "__main__":
    sys.exit(main())
