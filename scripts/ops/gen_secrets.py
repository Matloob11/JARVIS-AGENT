"""Create the agent link secrets (plan task C-14, contract C-ENV-1 / K-47) without ever printing a value.

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/gen_secrets.py --pc  [--dry-run]
    .venv_312/Scripts/python.exe scripts/ops/gen_secrets.py --vps [--dry-run] [--host H] [--ssh-key K] [--vps-dir D]
    (--pc --vps together does both, the PC first.)

--pc   adds AGENT_TOKEN (secrets.token_urlsafe(48)) and BRIDGE_HMAC_SECRET (secrets.token_hex(32)) to the PC
       env files (the root .env and its overlay deploy/windows-local/.env) where they are missing. Both files get
       one value; a value one file already has is copied to the other. A value is never replaced here: use
       rotate_env_secret.py for that. Files given with --env-file (repeatable) replace the pair, e.g. in tests.
--vps  creates or completes <vps-dir>/bridge.env on the VPS (default /opt/jarvis-agent/deploy/hostinger) over
       ssh: AGENT_TOKEN and BRIDGE_HMAC_SECRET with the PC root .env's values (sent on ssh stdin, never on a
       command line), SESSION_JWT_SECRET (token_urlsafe(64), generated on the VPS), LIVEKIT_API_KEY,
       LIVEKIT_API_SECRET and VORTEX_SECURITY_TOKEN copied line by line from the VPS .env next to it,
       LEGACY_TOKEN_AUTH=1 and BRIDGE_EVENT_RETENTION_DAYS=0. Keys bridge.env already has are kept; an
       AGENT_TOKEN or BRIDGE_HMAC_SECRET that differs from the PC is reported (exit 1) and left alone.

Every file is written through a temp file in its own folder and an atomic rename, mode 600 (POSIX; on the VPS
with umask 077), keeping every other line byte for byte; a read-only PC file stops the run on Windows before any
file is written. Output: key names, what happened, the value length and an 8-hex fingerprint (sha256 of
'jarvis-fp:' + value) for comparing the PC with the VPS. Never a value.
Back up first: scripts/ops/backup_local.py --now (PC) and deploy/hostinger/backup_vps.sh (VPS).
Exit codes: 0 ok, 1 a key differs, is missing at its source or ssh failed, 2 usage or file error.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


env_set = _load_sibling("env_set")
EnvError = env_set.EnvError

ROOT = Path(__file__).resolve().parents[2]
PC_FILES = (ROOT / ".env", ROOT / "deploy" / "windows-local" / ".env")
VPS_HOST = "root@187.127.123.235"
VPS_KEY = Path.home() / ".ssh" / "id_ed25519_hostinger"
VPS_DIR = "/opt/jarvis-agent/deploy/hostinger"
TEMP_SUFFIX = ".gen_secrets.tmp"  # git-ignored ('*.tmp')
PC_COMMENT = "Agent link v2 (C-14): scripts/ops/gen_secrets.py"

GENERATORS: dict[str, Callable[[], str]] = {
    "AGENT_TOKEN": lambda: secrets.token_urlsafe(48),
    "BRIDGE_HMAC_SECRET": lambda: secrets.token_hex(32),
    "SESSION_JWT_SECRET": lambda: secrets.token_urlsafe(64),
}
PC_KEYS = ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET")
# The remote side runs this loader; the program and its arguments (values included) arrive on stdin.
REMOTE_LOADER = ("python3 -c 'import json,sys; d=json.load(sys.stdin); "
                 "exec(compile(d[\"program\"], \"jarvis-remote\", \"exec\"), "
                 "{\"__name__\": \"jarvis_remote\", \"ARGS\": d[\"args\"]})'")

Runner = Callable[[str, dict[str, Any], str, Path], tuple[int, str, str]]


def fingerprint(value: str) -> str:
    """8 hex chars that tell two copies of a secret apart without revealing it."""
    return hashlib.sha256(b"jarvis-fp:" + value.encode("utf-8")).hexdigest()[:8]


def generate(name: str) -> str:
    """A new value for name: its own generator, else secrets.token_urlsafe(32)."""
    return GENERATORS.get(name, lambda: secrets.token_urlsafe(32))()


Staged = list[tuple[Any, Path]]  # (env_set.EnvFile, its temp file)


def stage_private(files: list[Any]) -> Staged:
    """First half of write_private(): writes each changed env_set.EnvFile to a temp file next to it (fsynced, mode
    600 on POSIX). Replaces nothing yet, so a caller can stage, change something else, then commit_private(). A
    read-only file stops it on Windows, where os.replace could not overwrite it later. On an error no temp file
    is left."""
    changed = [f for f in files if f.changed()]
    if os.name == "nt":
        read_only = [f.label for f in changed if f.path.exists() and not os.access(f.path, os.W_OK)]
        if read_only:
            raise EnvError(f"read-only: {', '.join(read_only)}; no file was changed")
    staged: Staged = []
    try:
        for f in changed:
            tmp = f.path.with_name(f.path.name + TEMP_SUFFIX)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            staged.append((f, tmp))
            with os.fdopen(fd, "wb") as handle:
                handle.write(f.render())
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(tmp, 0o600)
    except OSError as exc:
        discard_private(staged)
        raise EnvError(f"writing failed ({exc.strerror or type(exc).__name__}); no file was changed") from None
    return staged


def commit_private(staged: Staged) -> list[str]:
    """Second half of write_private(): renames each staged temp file over its file (os.replace). When one rename
    fails, the files already replaced get their old bytes back. Returns the labels written."""
    done: list[Any] = []
    try:
        for f, tmp in staged:
            os.replace(tmp, f.path)
            done.append(f)
    except OSError as exc:
        lost = _put_back(done)
        note = (f"; could not put back {', '.join(lost)} (restore it from the backup)" if lost
                else "; no file was changed")
        raise EnvError(f"writing failed ({exc.strerror or type(exc).__name__}){note}") from None
    finally:
        discard_private(staged)
    return [f.label for f, _ in staged]


def _put_back(files: list[Any]) -> list[str]:
    """Writes each file's original bytes back; returns the labels where that failed."""
    failed = []
    for f in files:
        try:
            f.path.write_bytes(f.raw)
        except OSError:
            failed.append(f.label)
    return failed


def discard_private(staged: Staged) -> None:
    """Removes the staged temp files that are still there (after a commit none are)."""
    for _, tmp in staged:
        if tmp.exists():
            tmp.unlink()


def write_private(files: list[Any]) -> list[str]:
    """Writes each changed env_set.EnvFile through a temp file and os.replace, mode 600 on POSIX. When one write
    fails, the files already replaced get their old bytes back. Returns the labels written."""
    return commit_private(stage_private(files))


def ensure_pc(paths: list[Path], dry_run: bool = False) -> tuple[list[str], dict[str, str]]:
    """Adds the missing PC_KEYS to every file (one value per key). Returns report lines (no values) and the
    values, which only ensure_vps() uses."""
    if len({os.path.normcase(str(p.resolve())) for p in paths}) != len(paths):
        raise EnvError("the same file is given twice")
    files = [env_set.EnvFile(path) for path in paths]
    for f in files:
        f.comment = PC_COMMENT
    report: list[str] = []
    values: dict[str, str] = {}
    for key in PC_KEYS:
        held = {f.value(key) for f in files if f.value(key)}
        if len(held) > 1:
            raise EnvError(f"{key}: the files hold different values; make them equal with rotate_env_secret.py")
        value = held.pop() if held else generate(key)
        missing = [f for f in files if not f.value(key)]
        token = env_set.render_value(key, value)
        actions = [(f.set(key, token), f.label) for f in missing]
        how = "generated" if len(missing) == len(files) else ("copied" if missing else "present")
        where = "; ".join(f"{done} in {label}" for done, label in actions)
        report.append(f"{key}: {how}{f' ({where})' if where else ''}, len {len(value)}, fp {fingerprint(value)}")
        values[key] = value
    if dry_run:
        pending = [f.label for f in files if f.changed()]
        report.append(f"dry run: nothing written (would change {', '.join(pending) or 'nothing'})")
    else:
        written = write_private(files)
        report.append(f"written: {', '.join(written)}" if written else "no change")
    return report, values


def remote_env_edit(args: dict[str, Any]) -> int:
    """
    Edits one env file and prints one JSON line per key, then {"written": bool}; returns the exit code (0 ok,
    1 a key differs or is missing at its source, 2 a value that cannot be written as one plain line).
    Runs on the VPS (python3, standard library only; its source is sent over ssh stdin, so it uses nothing
    from this module) and in the tests. Never prints a value.

    args: target (the file), source (where 'copy' reads), dry_run, ops: a list of
      {op: 'set', key, value, strict}   add when missing; a different value is kept ('differs', exit 1 if strict)
      {op: 'replace', key, value, only_present}  replace every assignment of key, or add it (with only_present:
                                        leave a file without a value for key alone, 'absent')
      {op: 'generate', key, nbytes}     add secrets.token_urlsafe(nbytes) when missing
      {op: 'copy', key}                 add the source file's last raw assignment line of key when missing
    The file is written once at the end through a temp file and os.replace, mode 600, under umask 077.
    """
    import hashlib  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import json  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import re  # pylint: disable=import-outside-toplevel
    import secrets  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported

    safe_value = re.compile(r"[A-Za-z0-9_.:/@+=,~%-]+")

    def fp(value: str) -> str:
        return hashlib.sha256(b"jarvis-fp:" + value.encode("utf-8")).hexdigest()[:8]

    def read(path: str) -> str | None:
        try:
            with open(path, "rb") as handle:
                return handle.read().decode("utf-8")
        except FileNotFoundError:
            return None

    def assignment(key: str) -> Any:
        return re.compile(r"^(\s*(?:export\s+)?" + re.escape(key) + r"\s*=)(.*?)(\r\n|\n|\r)?$")

    def plain(raw: str) -> str:
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        return value

    def current(lines: list[str], key: str) -> str | None:
        found = None
        for line in lines:
            match = assignment(key).match(line)
            if match:
                found = plain(match.group(2))
        return found or None

    target = args["target"]
    text = read(target)
    lines = (text or "").splitlines(keepends=True)
    eol = "\r\n" if text and "\r\n" in text else "\n"
    source_lines = (read(args["source"]) or "").splitlines() if args.get("source") else []
    code, changed, results = 0, False, []

    def put(key: str, replace_all: bool, value: str = "", raw: str | None = None) -> str:
        """Writes key over every assignment (replace_all) or over the last empty one, else at the end. The
        line is raw (a whole source line) or the existing 'KEY=' part ('KEY=' for a new line) plus value."""
        hits = [i for i, line in enumerate(lines) if assignment(key).match(line)]
        if not replace_all:
            hits = [i for i in hits if not plain(assignment(key).match(lines[i]).group(2))][-1:]
        for i in hits:
            match = assignment(key).match(lines[i])
            lines[i] = (raw if raw is not None else match.group(1) + value) + (match.group(3) or "")
        if hits:
            return "replaced" if replace_all else "filled"
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        lines.append((raw if raw is not None else f"{key}={value}") + eol)
        return "added"

    for op in args["ops"]:
        kind, key = op["op"], op["key"]
        have = current(lines, key)
        row: dict[str, Any] = {"key": key}
        if kind in ("copy", "generate") and have:
            row["action"] = "present"
        elif kind == "copy":
            found = [line for line in source_lines if assignment(key).match(line)]
            copied = plain(assignment(key).match(found[-1]).group(2)) if found else ""
            if copied:
                put(key, False, raw=found[-1])  # the source line, byte for byte
                row.update(action="copied", len=len(copied))
                changed = True
            else:
                row["action"] = "missing_in_source"
                code = max(code, 1)
        elif kind == "generate":
            value = secrets.token_urlsafe(int(op.get("nbytes", 32)))
            put(key, False, value)
            row.update(action="generated", len=len(value), fp=fp(value))
            changed = True
        else:
            value = str(op["value"])
            if not safe_value.fullmatch(value):
                print(json.dumps({"key": key, "action": "unsafe_value"}))
                print(json.dumps({"written": False, "mode": None}))
                return 2
            if kind == "replace" and have is None and op.get("only_present"):
                row["action"] = "absent"
            elif kind == "replace":
                row.update(action=put(key, have is not None, value), len=len(value), fp=fp(value))
                changed = True
            elif have is None:
                row.update(action=put(key, False, value), len=len(value), fp=fp(value))
                changed = True
            elif have == value:
                row.update(action="present", len=len(value), fp=fp(value))
            else:
                row.update(action="differs" if op.get("strict") else "kept", len=len(have), fp=fp(have))
                if op.get("strict"):
                    code = max(code, 1)
        results.append(row)

    written = bool(changed and not args.get("dry_run"))
    if written:
        old_umask = os.umask(0o077)
        tmp = target + ".jarvis-tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write("".join(lines).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        finally:
            os.umask(old_umask)
            if os.path.exists(tmp):
                os.unlink(tmp)
    for row in results:
        print(json.dumps(row))
    print(json.dumps({"written": written, "mode": oct(os.stat(target).st_mode & 0o777)
                      if os.path.exists(target) else None}))
    return code


def remote_program() -> str:
    """The source sent to the VPS: remote_env_edit() and the call that runs it with ARGS."""
    return ("from __future__ import annotations\nfrom typing import Any\n"
            + inspect.getsource(remote_env_edit) + "\nraise SystemExit(remote_env_edit(ARGS))\n")


def run_ssh(program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
    """Runs program with ARGS=args on host; both travel on stdin, so no value is on a command line."""
    command = ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, REMOTE_LOADER]
    try:
        proc = subprocess.run(command, input=json.dumps({"program": program, "args": args}), text=True,
                              capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 255, "", f"ssh did not run: {type(exc).__name__}"
    return proc.returncode, proc.stdout, proc.stderr


def describe(rows_text: str) -> tuple[list[str], dict[str, Any]]:
    """Report lines from the remote JSON lines (which hold no values) and the final status row."""
    lines, status = [], {}
    for raw in rows_text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if "key" not in row:
            status = row
            continue
        extra = "".join(f", {name} {row[name]}" for name in ("len", "fp") if name in row)
        lines.append(f"{row['key']}: {row['action']}{extra}")
    return lines, status


def ensure_vps(pc_values: dict[str, str], *, host: str = VPS_HOST, key: Path = VPS_KEY, vps_dir: str = VPS_DIR,
               dry_run: bool = False, runner: Runner = run_ssh) -> tuple[list[str], int]:
    """Creates or completes bridge.env on the VPS (see the module docstring). Returns report lines and the exit
    code."""
    args = {
        "target": f"{vps_dir.rstrip('/')}/bridge.env",
        "source": f"{vps_dir.rstrip('/')}/.env",
        "dry_run": dry_run,
        "ops": [
            {"op": "set", "key": "AGENT_TOKEN", "value": pc_values["AGENT_TOKEN"], "strict": True},
            {"op": "set", "key": "BRIDGE_HMAC_SECRET", "value": pc_values["BRIDGE_HMAC_SECRET"], "strict": True},
            {"op": "generate", "key": "SESSION_JWT_SECRET", "nbytes": 64},
            {"op": "copy", "key": "LIVEKIT_API_KEY"},
            {"op": "copy", "key": "LIVEKIT_API_SECRET"},
            {"op": "copy", "key": "VORTEX_SECURITY_TOKEN"},
            {"op": "set", "key": "LEGACY_TOKEN_AUTH", "value": "1"},
            {"op": "set", "key": "BRIDGE_EVENT_RETENTION_DAYS", "value": "0"},
        ],
    }
    code, out, err = runner(remote_program(), args, host, key)
    report, status = describe(out)
    if not status:  # ssh or python3 failed; their last stderr line names the problem, never a value
        last = (err.strip().splitlines() or ["no answer"])[-1]
        return report + [f"failed (exit {code}): {last[:200]}"], 1
    target = args["target"]
    if status.get("written"):
        report.append(f"written {target} (mode {status.get('mode')})")
    else:
        report.append(f"{'dry run: ' if dry_run else ''}{target} not changed (mode {status.get('mode')})")
    return report, min(code, 2)


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="gen_secrets.py",
                     description="Create AGENT_TOKEN, BRIDGE_HMAC_SECRET and bridge.env without printing values.")
    parser.add_argument("--pc", action="store_true", help="add the missing agent link keys to the PC env files")
    parser.add_argument("--vps", action="store_true", help="create or complete bridge.env on the VPS over ssh")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--env-file", action="append", type=Path, metavar="PATH",
                        help="PC file instead of .env and deploy/windows-local/.env (repeatable; the first one is "
                             "the source of the values sent to the VPS)")
    parser.add_argument("--host", default=VPS_HOST, help=f"ssh target (default {VPS_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=VPS_KEY, help="ssh identity file")
    parser.add_argument("--vps-dir", default=VPS_DIR, help=f"folder of bridge.env on the VPS (default {VPS_DIR})")
    args = parser.parse_args(argv)
    if not (args.pc or args.vps):
        parser.error("give --pc and/or --vps")
    return args


def main(argv: list[str] | None = None, runner: Runner = run_ssh) -> int:
    args = parse_args(argv)
    paths = args.env_file or list(PC_FILES)
    try:
        if args.pc:
            report, values = ensure_pc(paths, dry_run=args.dry_run)
            for line in report:
                print(f"pc  {line}")
        if args.vps:
            if not args.pc or args.dry_run:
                source = env_set.EnvFile(paths[0])
                values = {key: source.value(key) for key in PC_KEYS}
                missing = [key for key, value in values.items() if not value]
                if missing:
                    raise EnvError(f"{', '.join(missing)} missing in {source.label}: run --pc first")
            report, code = ensure_vps(values, host=args.host, key=args.ssh_key, vps_dir=args.vps_dir,
                                      dry_run=args.dry_run, runner=runner)
            for line in report:
                print(f"vps {line}")
            return code
        return 0
    except EnvError as exc:
        print(f"gen_secrets: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
