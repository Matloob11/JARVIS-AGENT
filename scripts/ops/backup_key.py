"""The PC side of the VPS brain's sealed snapshots and backups: its one backup key (plan tasks C-125 step 6 and C-128;
contracts K-77 'Snapshot', K-75).

One key pair seals both: jarvis-agent-svc's daily snapshot, and the agent-volumes.tar.enc that backup_vps.sh makes in
every backup (services/utils/agent_volumes.py), both to BACKUP_RECIPIENT_PUBLIC_KEY in the one format of
services/utils/backup_crypto.py (X25519 + HKDF-SHA256 + ChaCha20-Poly1305). Only this PC holds the private key, wrapped
with Windows DPAPI for the current user (CRYPTPROTECT_UI_FORBIDDEN), the way the Desktop Node keeps its own key; this
tool is the only one that makes or writes it, and scripts/ops/restore_agent_data.py reads it through load_key(). The
Desktop Node pulls the newest snapshot into %LOCALAPPDATA%\\JARVIS\\vps_backups every day (C-122, 'agent_ctl backup
pull' on demand).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py keygen [--key-file PATH] [--rotate]
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py public [--key-file PATH]
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py export DEST [--key-file PATH] [--allow-open-acl]
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py decrypt (FILE | --latest) [--out PATH] [--key-file PATH]

keygen   Creates the X25519 key pair and writes it DPAPI-wrapped to %LOCALAPPDATA%\\JARVIS\\backup\\backup_key.dpapi
         (--key-file overrides; temp file + atomic rename). An existing key is kept and only its public key is
         printed, unless --rotate (the old file is kept as <name>.<time>-<random>.bak: snapshots and backups sealed to it
         still decrypt with it, --key-file <that file>). Put the printed BACKUP_RECIPIENT_PUBLIC_KEY line into the PC
         root .env; push_agent_env.py copies it to the VPS .env.agent, where agent-svc reads it at its next start and
         backup_vps.sh at its next run.
public   Prints the public key of the existing key file.
export   Writes the key unwrapped into DEST, a NEW file (never over one, never inside the repo), readable by this user
         only: the offline copy. The one-user ACL (a protected one-entry DACL; mode 600 elsewhere) is set while the
         file is still empty and printed as ACL=<what it got>. A drive without ACLs (FAT or exFAT, told by the
         volume's FILE_PERSISTENT_ACLS flag) keeps its own rules (ACL=none, not refused); on any other drive an ACL that
         cannot be set refuses with nothing written (exit 2), unless --allow-open-acl, which writes it with the
         permissions it inherits and prints ACL=NOT restricted plus a WARN line. The wrapped key opens only for this
         Windows user on this PC, so without such a copy a lost profile loses every snapshot and backup. Keep it
         offline (never in the repo, a chat or a synced folder); --key-file DEST then opens them on any PC.
decrypt  Opens one sealed file, a snapshot or a backup's agent-volumes.tar.enc (or, with --latest, the newest file in
         %LOCALAPPDATA%\\JARVIS\\vps_backups), into a tar next to it (or --out): written as '.<name>.<random>.part' and
         renamed only after every chunk verified, never over an existing file. It then counts the tar's members and
         names its kind (KIND=agent-volumes when the tar holds the AGENT-BACKUP.json record, else snapshot).
--key-file  every command: the key file, either this store or an offline copy from 'export' (read as it is).
Output: KEY=VALUE lines with public keys, paths, sizes, sha256 and counts only; the private key is never printed or
logged. Exit codes: 0 ok, 1 the file does not open (wrong key, damaged, truncated), 2 usage or key file problem.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import secrets
import sys
import tarfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.utils import agent_volumes as av  # pylint: disable=wrong-import-position  # noqa: E402
from services.utils import backup_crypto as bc  # pylint: disable=wrong-import-position  # noqa: E402
from services.utils import desktop_protocol as dp  # pylint: disable=wrong-import-position  # noqa: E402

KEY_KIND = "jarvis_backup_recipient_key"
DPAPI_DESCRIPTION = "JARVIS backup key"
RECIPIENT_NAME = "BACKUP_RECIPIENT_PUBLIC_KEY"
EXIT_OK, EXIT_BAD_SNAPSHOT, EXIT_USAGE = 0, 1, 2

Wrap = Callable[[bytes], bytes]


class BackupKeyError(Exception):
    """Stops the run with exit code 2; the message never holds key material."""


def emit(name: str, value: object) -> None:
    print(f"{name}={value}")


def local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))


def default_key_file() -> Path:
    return local_appdata() / "JARVIS" / "backup" / "backup_key.dpapi"


def default_backup_dir() -> Path:
    return local_appdata() / "JARVIS" / "vps_backups"


def dpapi_wrap(data: bytes) -> bytes:
    return dp.dpapi_protect(data, DPAPI_DESCRIPTION)


def dpapi_unwrap(blob: bytes) -> bytes:
    return dp.dpapi_unprotect(blob)


def key_document(key: bc.X25519PrivateKey, created: int | None = None) -> bytes:
    """The plaintext the key file wraps (canonical JSON). Never print or log it."""
    return dp.canonical_json({"alg": "x25519", "created": int(time.time() if created is None else created),
                              "kind": KEY_KIND, "private": bc.private_key_hex(key), "public": bc.public_key_hex(key),
                              "v": 1})


def parse_key_document(data: bytes) -> bc.X25519PrivateKey:
    try:
        doc = dp.loads_strict(data)
        if not isinstance(doc, dict) or doc.get("kind") != KEY_KIND or doc.get("alg") != "x25519":
            raise ValueError("wrong kind")
        key = bc.parse_private_key(str(doc.get("private") or ""))
    except ValueError:
        raise BackupKeyError("the key file is not a JARVIS backup key") from None
    if not dp.digest_equal(bc.public_key_hex(key), str(doc.get("public") or "")):
        raise BackupKeyError("the key file is inconsistent")
    return key


def load_key(path: Path, unwrap: Wrap = dpapi_unwrap) -> bc.X25519PrivateKey:
    """The private key of the store (DPAPI-wrapped for this Windows user) or of an offline copy that 'export' wrote
    (the key document itself, which starts with '{'; a DPAPI blob never does)."""
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        raise BackupKeyError(f"no backup key at {path}: run 'backup_key.py keygen' first") from None
    except OSError as exc:
        raise BackupKeyError(f"the key file cannot be read ({type(exc).__name__})") from None
    if blob.lstrip()[:1] == b"{":
        return parse_key_document(blob)
    try:
        data = unwrap(blob)
    except OSError:
        raise BackupKeyError("this Windows user cannot unwrap the key file") from None
    return parse_key_document(data)


def restrict_to_user(path: Path) -> None:
    """Only the current user may open path: a protected one-entry DACL on Windows, mode 600 elsewhere. Raises when that
    cannot be done (src.core.desktop.health or pywin32 not importable, SetNamedSecurityInfo or chmod failing):
    export_key reports it, never silently."""
    from src.core.desktop import health  # pylint: disable=import-outside-toplevel

    health.restrict_to_current_user(path)


FILE_PERSISTENT_ACLS = 0x00000008   # GetVolumeInformationW: the file system keeps ACLs (NTFS, ReFS; not FAT, exFAT)


def volume_keeps_acls(path: Path) -> bool | None:
    """True when the volume of path keeps ACLs, False when it does not (a FAT or exFAT stick), None when that cannot be
    told (not Windows, or the query failed)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes  # pylint: disable=import-outside-toplevel
        from ctypes import wintypes  # pylint: disable=import-outside-toplevel

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        volume_path = kernel32.GetVolumePathNameW
        volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        volume_path.restype = wintypes.BOOL
        volume_info = kernel32.GetVolumeInformationW
        volume_info.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPDWORD,
                                wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD]
        volume_info.restype = wintypes.BOOL
        root = ctypes.create_unicode_buffer(1024)
        if not volume_path(os.fspath(path), root, len(root)):
            return None
        flags = wintypes.DWORD(0)
        if not volume_info(root.value, None, 0, None, None, ctypes.byref(flags), None, 0):
            return None
    except (OSError, AttributeError, ValueError, TypeError):
        return None
    return bool(flags.value & FILE_PERSISTENT_ACLS)


def _restrict_export(dest: Path, restrict: Callable[[Path], None], acl_support: Callable[[Path], bool | None],
                     allow_open_acl: bool) -> tuple[str, str]:
    """The ACL step of an export, run while dest is still empty: (the ACL line, a warning or ''). A drive without
    ACLs keeps its own rules (said, not refused); a one-user ACL that fails on any other drive refuses, unless
    allow_open_acl."""
    if acl_support(dest) is False:
        return ("none: this drive keeps no ACLs (FAT or exFAT), so whoever holds the drive can read the file", "")
    try:
        restrict(dest)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # reported below, never passed over
        why = type(exc).__name__
        if not allow_open_acl:
            raise BackupKeyError(f"the one-user ACL could not be set on {dest} ({why}): nothing was written. Export "
                                 "to a folder of this Windows user, or to a FAT or exFAT stick (which keeps no ACLs), "
                                 "or pass --allow-open-acl to write it with the permissions it inherits") from None
        return (f"NOT restricted ({why}): the file keeps the permissions it inherits from its folder",
                f"the one-user ACL could not be set ({why}) and --allow-open-acl wrote the key anyway: check who can "
                f"read {dest}")
    return ("this user only (a protected one-entry DACL)" if sys.platform == "win32" else "this user only (mode 600)",
            "")


def export_key(path: Path, dest: Path, *, unwrap: Wrap = dpapi_unwrap, restrict: Callable[[Path], None] | None = None,
               acl_support: Callable[[Path], bool | None] | None = None, allow_open_acl: bool = False) -> str:
    """The offline copy: the key document unwrapped in a NEW file, never over one and never inside the repo, readable
    by this user only (the ACL is set while the file is still empty, and its result printed as ACL=...). On a drive
    that keeps ACLs a failed one-user ACL refuses with nothing written, unless allow_open_acl (then a WARN line); a
    FAT or exFAT drive keeps its own rules. Returns the public key hex."""
    resolved = dest.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise BackupKeyError(f"{dest} is inside the repo: the offline copy goes outside it (a USB stick)")
    if dest.exists():
        raise BackupKeyError(f"{dest} exists: an export never overwrites a file")
    key = load_key(path, unwrap)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600))
    except FileExistsError:
        raise BackupKeyError(f"{dest} appeared meanwhile: an export never overwrites a file") from None
    try:
        acl, warning = _restrict_export(dest, restrict or restrict_to_user, acl_support or volume_keeps_acls,
                                        allow_open_acl)
        with dest.open("r+b") as handle:
            handle.write(key_document(key))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            dest.unlink()   # this run's own new file, never another
        raise
    emit("EXPORTED", dest.resolve())
    emit("ACL", acl)
    if warning:
        emit("WARN", warning)
    emit(RECIPIENT_NAME, bc.public_key_hex(key))
    emit("FINGERPRINT", bc.fingerprint(key))
    emit("NOTE", "unwrapped: keep it offline, never in the repo, a chat or a synced folder")
    return bc.public_key_hex(key)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def keygen(path: Path, *, rotate: bool = False, wrap: Wrap = dpapi_wrap, unwrap: Wrap = dpapi_unwrap) -> str:
    """Creates (or keeps) the key file; returns the public key hex."""
    if path.exists() and not rotate:
        key = load_key(path, unwrap)
        emit(RECIPIENT_NAME, bc.public_key_hex(key))
        emit("FINGERPRINT", bc.fingerprint(key))
        emit("KEPT", path.resolve())
        return bc.public_key_hex(key)
    key = bc.generate_private_key()
    try:
        wrapped = wrap(key_document(key))  # fails before anything on disk changes
    except OSError:
        raise BackupKeyError("DPAPI could not wrap the key (run it on the PC, as the owner's Windows user)") from None
    if path.exists():
        backup = path.with_name(f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.bak")
        os.replace(path, backup)
        emit("BACKUP", backup.resolve())
    atomic_write(path, wrapped)
    emit(RECIPIENT_NAME, bc.public_key_hex(key))
    emit("FINGERPRINT", bc.fingerprint(key))
    emit("WROTE", path.resolve())
    return bc.public_key_hex(key)


def newest_snapshot(folder: Path) -> Path:
    try:
        files = [entry for entry in folder.iterdir() if entry.is_file() and not entry.name.startswith(".")]
    except OSError:
        files = []
    sealed = []
    for entry in files:
        try:
            with entry.open("rb") as handle:
                if bc.looks_sealed(handle.read(len(bc.MAGIC))):
                    sealed.append(entry)
        except OSError:
            continue
    if not sealed:
        raise BackupKeyError(f"no sealed snapshot in {folder}")
    return max(sealed, key=lambda entry: entry.stat().st_mtime)


def default_output(snapshot: Path) -> Path:
    name = snapshot.name
    stem = name[:-len(".enc")] if name.endswith(".enc") else f"{name}.tar"
    if not stem.endswith(".tar"):
        stem += ".tar"
    return snapshot.with_name(stem)


def decrypt(snapshot: Path, out: Path, key: bc.X25519PrivateKey) -> dict[str, object]:
    """Opens a sealed file (a snapshot or a backup's agent-volumes.tar.enc) into out (never over an existing file);
    returns {path, size, sha256, members, kind}. Raises bc.SnapshotCryptoError when it does not open (nothing is left
    behind)."""
    if out.exists():
        raise BackupKeyError(f"{out} exists; pass another --out")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(f".{out.name}.{secrets.token_hex(4)}.part")
    digest = hashlib.sha256()
    try:
        with snapshot.open("rb") as src, temp.open("xb") as raw:
            class _Hashing:
                def write(self, data: bytes) -> int:
                    digest.update(data)
                    return raw.write(data)
            size = bc.open_stream(src, _Hashing(), key)  # type: ignore[arg-type]
        members, kind = 0, "snapshot"
        with tarfile.open(temp, mode="r:") as tar:
            for member in tar:
                members += 1
                if member.name == av.META:   # backup_vps.sh's agent data (services/utils/agent_volumes.py)
                    kind = "agent-volumes"
        if out.exists():
            raise BackupKeyError(f"{out} appeared meanwhile; pass another --out")
        os.replace(temp, out)
    finally:
        if temp.exists():
            temp.unlink()
    return {"path": out.resolve(), "size": size, "sha256": digest.hexdigest(), "members": members, "kind": kind}


def main(argv: Sequence[str] | None = None, *, wrap: Wrap = dpapi_wrap, unwrap: Wrap = dpapi_unwrap) -> int:
    parser = argparse.ArgumentParser(description="JARVIS backup key: keygen, public, export, decrypt (C-125, C-128)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("keygen", "public", "export", "decrypt"):
        command = sub.add_parser(name)
        command.add_argument("--key-file", help="the key: DPAPI-wrapped (default %%LOCALAPPDATA%%\\JARVIS\\backup\\"
                                                "backup_key.dpapi), or an offline copy from 'export'")
        if name == "keygen":
            command.add_argument("--rotate", action="store_true", help="replace an existing key (kept as .bak)")
        if name == "export":
            command.add_argument("dest", help="the new file for the unwrapped offline copy (outside the repo)")
            command.add_argument("--allow-open-acl", action="store_true",
                                 help="write the copy even when the one-user ACL cannot be set on a drive that keeps "
                                      "ACLs (it then keeps its folder's permissions; a WARN line says so)")
        if name == "decrypt":
            command.add_argument("snapshot", nargs="?",
                                 help="the sealed file: a snapshot, or a backup's agent-volumes.tar.enc")
            command.add_argument("--latest", action="store_true",
                                 help="the newest snapshot in %%LOCALAPPDATA%%\\JARVIS\\vps_backups")
            command.add_argument("--out", help="the tar to write (default: next to the sealed file, without .enc)")
    args = parser.parse_args(argv)
    key_file = Path(args.key_file) if args.key_file else default_key_file()
    try:
        if args.command == "keygen":
            keygen(key_file, rotate=args.rotate, wrap=wrap, unwrap=unwrap)
            return EXIT_OK
        if args.command == "export":
            export_key(key_file, Path(args.dest), unwrap=unwrap, allow_open_acl=args.allow_open_acl)
            return EXIT_OK
        key = load_key(key_file, unwrap)
        if args.command == "public":
            emit(RECIPIENT_NAME, bc.public_key_hex(key))
            emit("FINGERPRINT", bc.fingerprint(key))
            return EXIT_OK
        if bool(args.snapshot) == bool(args.latest):
            raise BackupKeyError("name one sealed FILE or pass --latest")
        snapshot = newest_snapshot(default_backup_dir()) if args.latest else Path(args.snapshot)
        if not snapshot.is_file():
            raise BackupKeyError(f"no sealed file at {snapshot}")
        out = Path(args.out) if args.out else default_output(snapshot)
        try:
            info = decrypt(snapshot, out, key)
        except bc.SnapshotCryptoError as exc:
            emit("FAILED", snapshot.resolve())
            emit("REASON", str(exc))
            return EXIT_BAD_SNAPSHOT
        except tarfile.TarError:
            emit("FAILED", snapshot.resolve())
            emit("REASON", "the opened file is not a tar")
            return EXIT_BAD_SNAPSHOT
        emit("SNAPSHOT", snapshot.resolve())
        emit("KIND", info["kind"])
        emit("DECRYPTED", info["path"])
        emit("SIZE", info["size"])
        emit("SHA256", info["sha256"])
        emit("TAR_MEMBERS", info["members"])
        return EXIT_OK
    except BackupKeyError as exc:
        print(f"[backup_key] {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
