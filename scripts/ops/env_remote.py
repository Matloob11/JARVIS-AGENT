"""The functions that the VPS env tools of plan task C-127 ship to the VPS (contracts K-47, K-75).

split_bridge_env.py, push_agent_env.py and livekit_pc_pair.py each send one program and its arguments over ssh
stdin (gen_secrets.REMOTE_LOADER: python3 on the VPS, nothing on a command line). program() builds that program from
REMOTE_HEADER, the source of the functions it names and one call. So every shipped function:
- uses only the standard library modules of REMOTE_HEADER and works on python3.8 or newer (no 3.9+ syntax or API;
  annotations are strings through the __future__ import);
- calls only other shipped functions (by their bare names, which the program defines at its top level);
- never prints or returns a value read from an env file: names, lengths, file modes and HMAC digests only.
The PC side runs the same functions on its own copies (the tests run them on files under tmp_path).
"""
from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import textwrap
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The modules every shipped function may use; the program starts with these lines.
REMOTE_HEADER = ("from __future__ import annotations\n"
                 "import hashlib, hmac, json, os, re, secrets, subprocess, sys\n"
                 "from typing import Any\n")
# Where the env tools keep their backups: <BACKUP_ROOT>/<UTC stamp>-<label>. A folder of its own under
# /root/jarvis-backups, never a '2*' name beside backup_vps.sh's: the RUNBOOK's PC mirror copies only backup_vps.sh
# folders (env copies, .env.agent among them, never reach the PC that way) and its 'newest backup' commands must
# never pick an env tool's folder; backup_vps.sh's prune and rollback.sh never take it for their own either.
BACKUP_ROOT = "/root/jarvis-backups/env"
BACKUP_STAMP = "%Y%m%dT%H%M%SZ"


def program(*functions: Callable[..., Any], entry: str) -> str:
    """The source sent to the VPS: REMOTE_HEADER, each function's source, then the call of `entry` (one of them)
    with ARGS, the JSON the loader read from stdin; its return value is the exit code. An unexpected exception
    prints one JSON line with its type only and exits 1: a traceback could quote a byte of an env file."""
    if entry not in {fn.__name__ for fn in functions}:
        raise ValueError(f"{entry} is not among the shipped functions")
    guard = ("\n\ntry:\n"
             f"    _exit_code = {entry}(ARGS)\n"
             "except Exception as _exc:  # names the type only, never the message\n"
             "    print(json.dumps({'result': 'failed', 'error': 'unexpected ' + type(_exc).__name__}))\n"
             "    _exit_code = 1\n"
             "raise SystemExit(_exit_code)\n")
    return REMOTE_HEADER + "\n\n".join(textwrap.dedent(inspect.getsource(fn)) for fn in functions) + guard


def default_ssh_key() -> Path:
    """JARVIS_SSH_KEY (as agent_ctl and the deploy scripts read it), else ~/.ssh/id_ed25519_hostinger."""
    value = (os.getenv("JARVIS_SSH_KEY") or "").strip()
    return Path(os.path.expanduser(value)) if value else Path.home() / ".ssh" / "id_ed25519_hostinger"


def utc_stamp() -> str:
    """The backup folder stamp of this moment (BACKUP_STAMP, UTC)."""
    return datetime.now(timezone.utc).strftime(BACKUP_STAMP)


# Git Bash (MSYS) turns a typed POSIX argument such as /opt/x into C:/Program Files/Git/opt/x before Python sees it.
_MSYS_MANGLED = re.compile(r"^[A-Za-z]:[\\/]Program Files[\\/]Git(?P<posix>[\\/].+)$")


def vps_path(value: str) -> str:
    """A path on the VPS as it was typed: the POSIX path back when Git Bash rewrote it into its own folder."""
    match = _MSYS_MANGLED.match(value or "")
    return match.group("posix").replace("\\", "/") if match else value


# --------------------------------------------------------------------------- shipped to the VPS


def env_entries(text: str) -> list[list[Any]]:
    """[name or None, line] for each line of env-file text, its line ending kept: the name of an assignment line
    ('NAME=...', 'export NAME = ...') or of a bare name line ('NAME', which compose fills from its own environment),
    None for a comment, a blank line or anything else. A leading UTF-8 byte order mark (U+FEFF, as an editor on
    Windows may save it) is an entry of its own, [None, BOM]: the first line's name is read past it, and every writer
    (env_set_lines, env_drop_lines) keeps it where it was."""
    entries = []
    if text.startswith("\ufeff"):
        entries.append([None, "\ufeff"])
        text = text[1:]
    for line in text.splitlines(True):
        match = re.match(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?:=|[\r\n]*$)", line)
        entries.append([match.group(1) if match else None, line])
    return entries


def env_value(line: str) -> str:
    """The value of one assignment line as compose and python-dotenv read it: the text after the first '=',
    stripped; a quoted value without its quotes (taken literally); an unquoted value cut before ' #'. '' for a bare
    name line."""
    raw = line.split("=", 1)[1].strip() if "=" in line else ""
    if raw and raw[0] in "'\"":
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.split(r"[ \t]+#", raw, maxsplit=1)[0].strip()


def env_multiline(line: str) -> bool:
    """True for an assignment whose quoted value does not close on its own line (a value over several lines,
    which these tools never rewrite)."""
    raw = line.split("=", 1)[1].strip() if "=" in line else ""
    return bool(raw) and raw[0] in "'\"" and raw.find(raw[0], 1) < 0


def env_values(text: str) -> dict[str, str]:
    """name -> value of the last assignment of each name ('' for an empty one or a bare name), as compose takes it."""
    values = {}
    for name, line in env_entries(text):
        if name:
            values[name] = env_value(line)
    return values


def render_env_value(value: str) -> str | None:
    """The text after 'NAME=' for a value: unchanged when it holds only [A-Za-z0-9_.:/@+=,~%-], else in single
    quotes (compose and python-dotenv take a single-quoted value literally: no ${...} expansion, spaces kept).
    None for a value that no one-line env file holds unchanged: a single quote, a line break or a NUL."""
    if re.fullmatch(r"[A-Za-z0-9_.:/@+=,~%-]*", value):
        return value
    if any(ch in value for ch in "'\r\n\x00"):
        return None
    return "'" + value + "'"


def env_set_lines(text: str, values: dict[str, str]) -> str:
    """text with NAME=<rendered value> for each name of values: every assignment of the name is rewritten in place
    (its line ending kept), a missing name is appended with the file's own line ending; every other line stays
    byte for byte. The values must already be renderable (render_env_value is not None)."""
    newline = "\r\n" if "\r\n" in text else "\n"
    done = set()
    out = []
    for name, line in env_entries(text):
        if name in values:
            ending = line[len(line.rstrip("\r\n")):]
            out.append(name + "=" + str(render_env_value(values[name])) + (ending or newline))
            done.add(name)
        else:
            out.append(line)
    tail = "".join(out)
    for name, value in values.items():
        if name not in done:
            if tail and tail != "\ufeff" and not tail.endswith(("\n", "\r")):
                tail += newline
            tail += name + "=" + str(render_env_value(value)) + newline
    return tail


def env_drop_lines(text: str, names: Any) -> str:
    """text without any assignment (or bare name) line of the given names; every other line stays."""
    wanted = set(names)
    return "".join(line for name, line in env_entries(text) if name not in wanted)


def read_text(path: str) -> str | None:
    """A UTF-8 file's text, or None when it does not exist."""
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8")
    except FileNotFoundError:
        return None


def file_mode(path: str) -> dict[str, Any] | None:
    """{'mode': '600', 'uid': 0} of a file (the K-75 check: 600 root); None when it does not exist."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return {"mode": format(st.st_mode & 0o777, "o"), "uid": getattr(st, "st_uid", None)}


def atomic_write_private(path: str, data: bytes) -> None:
    """Writes path through a new temp file next to it (umask 077, O_EXCL, mode 600, fsynced, owner root when run as
    root) and an atomic rename (os.replace, the 'mv'); then the folder is fsynced (POSIX). A failed write leaves
    the old file untouched and removes the temp file."""
    folder = os.path.dirname(os.path.abspath(path))
    temp = os.path.join(folder, "." + os.path.basename(path) + "." + secrets.token_hex(4) + ".part")
    old_umask = os.umask(0o077)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        if getattr(os, "geteuid", lambda: -1)() == 0:
            os.chown(temp, 0, 0)
        os.replace(temp, path)
        if os.name != "nt":
            dir_fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        os.umask(old_umask)
        if os.path.exists(temp):
            os.unlink(temp)


def make_backup_dir(root: str, stamp: str, label: str) -> str:
    """A new folder <root>/<stamp>-<label>, mode 700 ('-2', '-3' ... appended when it exists); its path."""
    old_umask = os.umask(0o077)
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        for number in range(1, 100):
            path = os.path.join(root, stamp + "-" + label + ("" if number == 1 else "-" + str(number)))
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                continue
            return path
    finally:
        os.umask(old_umask)
    raise OSError("too many backup folders named " + stamp + "-" + label)


def backup_private(src: str, folder: str, name: str) -> bool:
    """Copies src into folder as name (mode 600, fsynced); False when src does not exist."""
    try:
        with open(src, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return False
    target = os.path.join(folder, name)
    old_umask = os.umask(0o077)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(target, 0o600)
    finally:
        os.umask(old_umask)
    return True


def value_digest(nonce_hex: str, name: str, value: str) -> str:
    """HMAC-SHA256 of 'NAME\\0value' keyed with a per-run random nonce: two sides compare a value without showing
    or sending it, and a digest says nothing outside that run."""
    return hmac.new(bytes.fromhex(nonce_hex), name.encode("utf-8") + b"\0" + value.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def compose_interpolations(text: str) -> dict[str, str]:
    """The variables a compose file interpolates: name -> 'required' (${X:?..} or ${X?..}: compose refuses to run
    without it), 'plain' (${X} or $X: compose substitutes '' and warns when unset) or 'default' (${X:-..}, ${X-..},
    ${X:+..}, ${X+..}). '$$' is a literal dollar; comment lines are skipped. The strongest use of a name wins."""
    rank = {"default": 0, "plain": 1, "required": 2}
    found = {}
    pattern = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for match in pattern.finditer(line):
            if match.group(0) == "$$":
                continue
            name, tail = match.group(1) or match.group(3), match.group(2) or ""
            if tail.startswith(("?", ":?")):
                kind = "required"
            elif tail.startswith(("-", "+", ":-", ":+")):
                kind = "default"
            else:
                kind = "plain"
            if name not in found or rank[kind] > rank[found[name]]:
                found[name] = kind
    return found


# --------------------------------------------------------------------------- PC side


def new_nonce() -> str:
    """A fresh 32-byte HMAC key for one comparison run (never printed)."""
    return secrets.token_hex(32)


def digests_equal(left: str | None, right: str | None) -> bool:
    """True when both digests exist and match (constant time)."""
    return bool(left) and bool(right) and hmac.compare_digest(str(left), str(right))


def names_line(names: Any, empty: str = "none") -> str:
    """A sorted, comma-separated list of names for the output ('none' for no names)."""
    items = sorted(set(names))
    return ", ".join(items) if items else empty


def json_rows(text: str) -> list[dict[str, Any]]:
    """The JSON object lines of a VPS program's stdout (anything else is skipped)."""
    rows = []
    for raw in (text or "").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def mode_text(mode: dict[str, Any] | None) -> str:
    """'600 root', '640 uid 1000' or 'absent' for a file_mode() result."""
    if not mode:
        return "absent"
    owner = "root" if mode.get("uid") == 0 else "uid " + str(mode.get("uid"))
    return str(mode.get("mode")) + " " + owner


def failure_line(err: str, limit: int = 200) -> str:
    """The last line of ssh's (or the remote python's) stderr, cut short: it names the problem, never a value (the
    programs print values nowhere, and ssh's own messages hold host and key names only)."""
    return ((err or "").strip().splitlines() or ["no answer"])[-1][:limit]
