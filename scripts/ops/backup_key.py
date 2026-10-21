"""The PC side of the VPS brain's sealed snapshots (plan task C-125 step 6; contract K-77 'Snapshot').

jarvis-agent-svc seals its daily snapshot to BACKUP_RECIPIENT_PUBLIC_KEY (services/utils/backup_crypto.py: X25519 +
HKDF-SHA256 + ChaCha20-Poly1305); only this PC holds the private key, wrapped with Windows DPAPI for the current user
(CRYPTPROTECT_UI_FORBIDDEN), the way the Desktop Node keeps its own key. The Desktop Node pulls the newest snapshot into
%LOCALAPPDATA%\\JARVIS\\vps_backups every day (C-122, 'agent_ctl backup pull' on demand).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py keygen [--key-file PATH] [--rotate]
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py public [--key-file PATH]
    .venv_312/Scripts/python.exe scripts/ops/backup_key.py decrypt (SNAPSHOT | --latest) [--out PATH] [--key-file PATH]

keygen   Creates the X25519 key pair and writes it DPAPI-wrapped to %LOCALAPPDATA%\\JARVIS\\backup\\backup_key.dpapi
         (--key-file overrides; temp file + atomic rename). An existing key is kept and only its public key is
         printed, unless --rotate (the old file is kept as <name>.<time>-<random>.bak: snapshots sealed to it still
         decrypt with it). Put the printed BACKUP_RECIPIENT_PUBLIC_KEY line into the PC root .env; push_agent_env.py
         copies it to the VPS .env.agent, and agent-svc reads it at its next start.
public   Prints the public key of the existing key file.
decrypt  Opens one sealed snapshot (or, with --latest, the newest file in %LOCALAPPDATA%\\JARVIS\\vps_backups) into a
         tar next to it (or --out): written as '.<name>.<random>.part' and renamed only after every chunk verified, never
         over an existing file. It then counts the tar's members.
Output: KEY=VALUE lines with public keys, paths, sizes, sha256 and counts only; the private key is never printed or
logged. Exit codes: 0 ok, 1 the snapshot does not open (wrong key, damaged, truncated), 2 usage or key file problem.
"""
from __future__ import annotations

import argparse
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
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        raise BackupKeyError(f"no backup key at {path}: run 'backup_key.py keygen' first") from None
    except OSError as exc:
        raise BackupKeyError(f"the key file cannot be read ({type(exc).__name__})") from None
    try:
        data = unwrap(blob)
    except OSError:
        raise BackupKeyError("this Windows user cannot unwrap the key file") from None
    return parse_key_document(data)


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
    """Opens snapshot into out (never over an existing file); returns {path, size, sha256, members}. Raises
    bc.SnapshotCryptoError when it does not open (nothing is left behind)."""
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
        with tarfile.open(temp, mode="r:") as tar:
            members = sum(1 for _ in tar)
        if out.exists():
            raise BackupKeyError(f"{out} appeared meanwhile; pass another --out")
        os.replace(temp, out)
    finally:
        if temp.exists():
            temp.unlink()
    return {"path": out.resolve(), "size": size, "sha256": digest.hexdigest(), "members": members}


def main(argv: Sequence[str] | None = None, *, wrap: Wrap = dpapi_wrap, unwrap: Wrap = dpapi_unwrap) -> int:
    parser = argparse.ArgumentParser(description="JARVIS backup key: keygen, public, decrypt (C-125)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("keygen", "public", "decrypt"):
        command = sub.add_parser(name)
        command.add_argument("--key-file", help="DPAPI-wrapped key (default %%LOCALAPPDATA%%\\JARVIS\\backup\\"
                                                "backup_key.dpapi)")
        if name == "keygen":
            command.add_argument("--rotate", action="store_true", help="replace an existing key (kept as .bak)")
        if name == "decrypt":
            command.add_argument("snapshot", nargs="?", help="the sealed snapshot file")
            command.add_argument("--latest", action="store_true",
                                 help="the newest snapshot in %%LOCALAPPDATA%%\\JARVIS\\vps_backups")
            command.add_argument("--out", help="the tar to write (default: next to the snapshot, without .enc)")
    args = parser.parse_args(argv)
    key_file = Path(args.key_file) if args.key_file else default_key_file()
    try:
        if args.command == "keygen":
            keygen(key_file, rotate=args.rotate, wrap=wrap, unwrap=unwrap)
            return EXIT_OK
        key = load_key(key_file, unwrap)
        if args.command == "public":
            emit(RECIPIENT_NAME, bc.public_key_hex(key))
            emit("FINGERPRINT", bc.fingerprint(key))
            return EXIT_OK
        if bool(args.snapshot) == bool(args.latest):
            raise BackupKeyError("name one SNAPSHOT or pass --latest")
        snapshot = newest_snapshot(default_backup_dir()) if args.latest else Path(args.snapshot)
        if not snapshot.is_file():
            raise BackupKeyError(f"no snapshot file at {snapshot}")
        out = Path(args.out) if args.out else default_output(snapshot)
        try:
            info = decrypt(snapshot, out, key)
        except bc.SnapshotCryptoError as exc:
            emit("FAILED", snapshot.resolve())
            emit("REASON", str(exc))
            return EXIT_BAD_SNAPSHOT
        except tarfile.TarError:
            emit("FAILED", snapshot.resolve())
            emit("REASON", "the opened snapshot is not a tar")
            return EXIT_BAD_SNAPSHOT
        emit("SNAPSHOT", snapshot.resolve())
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
