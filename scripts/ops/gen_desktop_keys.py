"""Generate the Desktop Link signing keys (contract K-70, plan task C-115). Prints public keys only.

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/gen_desktop_keys.py --role pc [--key-file PATH] [--rotate]
    python scripts/ops/gen_desktop_keys.py --role agent --env-file PATH [--pc-public HEX] [--rotate]

--role pc     On the laptop (Windows only), as the user who runs the Desktop Node. Creates the Ed25519 key
              behind DESKTOP_PC_PUBLIC_KEY and stores it wrapped with DPAPI (current user,
              CRYPTPROTECT_UI_FORBIDDEN) in %LOCALAPPDATA%\\JARVIS\\node\\keys.dpapi (--key-file overrides).
--role agent  On the VPS, inside a one-off agent container. Creates DESKTOP_AGENT_SIGNING_KEY and writes
              PATH.tmp ('.env.agent.tmp' next to --env-file PATH): the current lines of PATH (when it
              exists) with DESKTOP_AGENT_SIGNING_KEY and DESKTOP_AGENT_PUBLIC_KEY set, plus
              DESKTOP_PC_PUBLIC_KEY with --pc-public. It is written under umask 077 through a temp file and
              an atomic rename. PATH itself is never written: the deploy step moves PATH.tmp over it.
              Mount the DIRECTORY that holds PATH, never the file alone, for example
                  docker run --rm --user 0 -v <host dir of .env.agent>:/envdir <agent image> \\
                      python scripts/ops/gen_desktop_keys.py --role agent --env-file /envdir/.env.agent
              A file mounted on its own would leave PATH.tmp inside the container, lost with --rm, after
              its public key had been printed. So the tool refuses, before it makes or prints any key, a
              directory that does not exist (nothing mounted there) or is not writable, and an env file on
              another filesystem than its directory (a file mounted on its own). It prints WROTE=<absolute
              path of PATH.tmp> (a container path, under the mounted directory): check that the file is there
              on the host before distributing the public key.
An existing key is kept and only its public key is printed, unless --rotate is given (pc: the old file is
kept as keys.dpapi.<time>-<random>.bak). Re-running with nothing to change writes nothing.

Output: KEY=VALUE lines with public keys and absolute paths only, printed after the file is written; a private
value is never printed or logged.
Exit codes: 0 ok, 2 usage or environment error.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.utils import desktop_protocol as dp  # pylint: disable=wrong-import-position  # noqa: E402

AGENT_SIGNING = "DESKTOP_AGENT_SIGNING_KEY"
AGENT_PUBLIC = "DESKTOP_AGENT_PUBLIC_KEY"
PC_PUBLIC = "DESKTOP_PC_PUBLIC_KEY"
_ASSIGNMENT = r"^(?P<lead>[ \t]*(?:export[ \t]+)?{name}[ \t]*=)[^\r\n]*"


class KeyToolError(Exception):
    """Stops the run with exit code 2. The message never contains a key value."""


def is_windows() -> bool:
    return sys.platform == "win32"


def emit(name: str, value: str) -> None:
    print(f"{name}={value}")


def shown(path: Path) -> str:
    """The absolute form of a path for the output (inside a container, a container path)."""
    return os.path.abspath(path)


def default_key_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "JARVIS" / "node" / "keys.dpapi"


def atomic_write(path: Path, data: bytes) -> None:
    """Writes `path` through a unique temp file in the same folder (umask 077, mode 600) and os.replace."""
    old_umask = os.umask(0o077)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        os.umask(old_umask)
        if temp.exists():
            temp.unlink()


def env_value(text: str, name: str) -> str | None:
    """The last value assigned to `name` in env-file text (outer quotes removed), or None."""
    value = None
    for match in re.finditer(_ASSIGNMENT.format(name=re.escape(name)), text, flags=re.MULTILINE):
        value = match.group(0).split("=", 1)[1].strip().strip("'\"")
    return value


def set_env_values(text: str, values: dict[str, str]) -> str:
    """Rewrites each assignment in place (every other byte unchanged) and appends the missing names."""
    newline = "\r\n" if "\r\n" in text else "\n"
    for name, value in values.items():
        pattern = re.compile(_ASSIGNMENT.format(name=re.escape(name)), re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda match, new=value: match.group("lead") + new, text)
            continue
        if text and not text.endswith(("\n", "\r")):
            text += newline
        text += f"{name}={value}{newline}"
    return text


def run_pc(args: argparse.Namespace) -> int:
    if not is_windows():
        raise KeyToolError("--role pc needs Windows DPAPI: run it on the laptop, as the user who runs the "
                           "Desktop Node")
    path = Path(args.key_file) if args.key_file else default_key_file()
    if path.exists() and not args.rotate:
        try:
            key = dp.load_pc_key_file(path)
        except (OSError, ValueError):
            raise KeyToolError(f"{path} exists but this user cannot unwrap it; --rotate replaces it") from None
        emit(PC_PUBLIC, dp.public_key_hex(key))
        emit("KEPT", shown(path))
        return 0
    key = dp.generate_signing_key()
    wrapped = dp.dpapi_protect(dp.pc_key_document(key))  # fails before anything on disk changes
    if path.exists():
        backup = path.with_name(f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.bak")
        os.replace(path, backup)
        emit("BACKUP", shown(backup))
    atomic_write(path, wrapped)
    emit(PC_PUBLIC, dp.public_key_hex(key))
    emit("WROTE", shown(path))
    return 0


def _read_env(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_bytes().decode("utf-8")
    except OSError:
        raise KeyToolError(f"cannot read {path}: run as its owner") from None
    except UnicodeDecodeError:
        raise KeyToolError(f"{path} is not UTF-8 text") from None


def _writable(folder: Path) -> bool:
    return os.access(folder, os.W_OK)


def _device(path: Path) -> int:
    return os.stat(path).st_dev


def check_env_folder(env_path: Path, tmp_path: Path) -> None:
    """PATH.tmp must land next to PATH on the host (C-127): refuses, before any key is made, a directory that
    does not exist or is not writable, and an env file on another filesystem than its directory, as a file
    bind-mounted into a container on its own is (PATH.tmp would then stay inside the container)."""
    folder = env_path.parent
    if not folder.is_dir():
        raise KeyToolError(f"{shown(folder)} does not exist: mount the directory that holds the env file")
    if not _writable(folder):
        raise KeyToolError(f"{shown(folder)} is not writable: {tmp_path.name} is written next to {env_path.name}")
    if env_path.exists() and _device(env_path) != _device(folder):
        raise KeyToolError(f"{shown(env_path)} is mounted on its own: mount its directory instead, or "
                           f"{tmp_path.name} would not be written next to it")


def run_agent(args: argparse.Namespace) -> int:
    if not args.env_file:
        raise KeyToolError("--role agent needs --env-file (the .env.agent path)")
    env_path = Path(args.env_file)
    tmp_path = env_path.with_name(env_path.name + ".tmp")
    check_env_folder(env_path, tmp_path)
    if tmp_path.exists():
        raise KeyToolError(f"{shown(tmp_path)} already exists: install or remove it first")
    try:
        pc_public = dp.public_key_hex(dp.public_key_from_hex(args.pc_public)) if args.pc_public else None
    except ValueError:  # bad hex, or a small-order / non-canonical point on which forged signatures verify
        raise KeyToolError("--pc-public is not a usable Ed25519 public key (32 bytes as hex)") from None
    text = _read_env(env_path)
    existing = env_value(text, AGENT_SIGNING)
    if existing and not args.rotate:
        try:
            key = dp.signing_key_from_hex(existing)
        except ValueError:
            raise KeyToolError(f"{AGENT_SIGNING} in {env_path} is not a valid key; --rotate replaces it") from None
        values = {AGENT_PUBLIC: dp.public_key_hex(key)}
    else:
        key = dp.generate_signing_key()
        values = {AGENT_SIGNING: dp.signing_key_to_hex(key), AGENT_PUBLIC: dp.public_key_hex(key)}
    if pc_public:
        values[PC_PUBLIC] = pc_public
    new_text = set_env_values(text, values)
    if new_text == text:
        status = ("KEPT", env_path)
    else:
        atomic_write(tmp_path, new_text.encode("utf-8"))  # first: never print a public key whose key is not saved
        status = ("WROTE", tmp_path)
    emit(AGENT_PUBLIC, dp.public_key_hex(key))
    emit(status[0], shown(status[1]))
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Desktop Link signing keys; prints public keys only.")
    parser.add_argument("--role", choices=("pc", "agent"), required=True)
    parser.add_argument("--key-file", help="pc: wrapped key path (default %%LOCALAPPDATA%%\\JARVIS\\node\\keys.dpapi)")
    parser.add_argument("--env-file", help="agent: the .env.agent path; <env-file>.tmp is written next to it, so "
                                           "in a container mount its DIRECTORY, never the file alone")
    parser.add_argument("--pc-public", help="agent: also set DESKTOP_PC_PUBLIC_KEY (hex)")
    parser.add_argument("--rotate", action="store_true", help="replace an existing key")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_pc(args) if args.role == "pc" else run_agent(args)
    except (KeyToolError, OSError) as exc:  # messages name paths, never key values
        print(f"ERROR: {exc}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
