"""Rotate the LiveKit API key and secret without printing them (plan task C-17, OP-14).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/rotate_livekit_keys.py [--dry-run] [--host H] [--ssh-key K]
    .venv_312/Scripts/python.exe scripts/ops/rotate_livekit_keys.py --check-old /root/jarvis-backups/<TS>/livekit-old.env

Rotation: a new pair is generated on this PC (key 'API' + 12 letters and digits, secret secrets.token_urlsafe(48)).
One ssh run (python3 on the VPS; the program and every value travel on ssh stdin, never on a command line) first
copies the current pair from deploy/hostinger/.env to /root/jarvis-backups/<TS>/livekit-old.env (mode 600, folder
700) and then replaces LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env and bridge.env, and in .env.agent when that
file has them (each file atomic, mode 600, every other line byte for byte). It stops at the first failure. The PC env
files (.env and deploy/windows-local/.env) are staged before the VPS step and renamed into place after it, as
rotate_env_secret.py does: nothing on this PC changes when the VPS step fails. Only lengths and fingerprints (sha256
of 'jarvis-fp:' + value, first 8 hex) are printed.

The pair takes effect when the readers restart (printed as the next steps): LiveKit (LIVEKIT_KEYS from the compose
.env) and the bridge (bridge.env) with 'docker compose -p jarvis up -d --no-deps --force-recreate livekit ui-bridge'
on the VPS, then the PC worker with agent_ctl restart. Back up first: deploy/hostinger/backup_vps.sh on the VPS.

--check-old FILE: the old pair from FILE (on the VPS) goes on stdin into the jarvis-bridge container, which mints a
token with it and calls ListRooms on http://jarvis-livekit:7880. Prints 'old key rejected: 401' (exit 0) or 'OLD KEY
STILL WORKS' (exit 1); never a value or the server's message.

Exit codes: 0 ok, 1 the VPS step failed (nothing written on this PC), a PC file could not be replaced after the VPS
files were written, or the old key still works; 2 usage or file error (nothing written).
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import secrets
import string
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# scripts/ops is not a package: rotate_env_secret.py (which loads gen_secrets.py and env_set.py) is loaded by path.
_ROTATE_SPEC = importlib.util.spec_from_file_location("jarvis_ops_rotate_env_secret",
                                                      Path(__file__).with_name("rotate_env_secret.py"))
assert _ROTATE_SPEC is not None and _ROTATE_SPEC.loader is not None
rotate_env_secret = importlib.util.module_from_spec(_ROTATE_SPEC)
_ROTATE_SPEC.loader.exec_module(rotate_env_secret)
gen_secrets = rotate_env_secret.gen_secrets
env_set = rotate_env_secret.env_set
EnvError = env_set.EnvError

KEY_NAME, SECRET_NAME = "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"
VPS_FILES = (".env", "bridge.env", ".env.agent")  # .env.agent (VPS brain) only when it already has the pair
BACKUP_ROOT = "/root/jarvis-backups"
BACKUP_NAME = "livekit-old.env"
BRIDGE_CONTAINER = "jarvis-bridge"
LIVEKIT_INTERNAL_URL = "http://jarvis-livekit:7880"
_ALNUM = string.ascii_letters + string.digits
# Git Bash (MSYS) turns a POSIX argument such as /root/x into C:/Program Files/Git/root/x before Python sees it.
_MSYS_MANGLED = re.compile(r"^[A-Za-z]:[\\/]Program Files[\\/]Git(?P<posix>[\\/].+)$")


def new_pair() -> tuple[str, str]:
    """(key, secret): 'API' + 12 letters and digits, and secrets.token_urlsafe(48)."""
    return "API" + "".join(secrets.choice(_ALNUM) for _ in range(12)), secrets.token_urlsafe(48)


def vps_path(value: str) -> str:
    """A path on the VPS as typed: the POSIX path back when Git Bash rewrote it into its own install folder."""
    match = _MSYS_MANGLED.match(value or "")
    return match.group("posix").replace("\\", "/") if match else value


def default_ssh_key() -> Path:
    """JARVIS_SSH_KEY (as agent_ctl and the deploy scripts use it), else gen_secrets' default."""
    value = (os.getenv("JARVIS_SSH_KEY") or "").strip()
    return Path(os.path.expanduser(value)) if value else gen_secrets.VPS_KEY


def remote_rotate(args: dict[str, Any]) -> int:
    """
    Runs on the VPS (python3, standard library only, with remote_env_edit defined beside it): backs up the current
    pair from the first file, then edits every file with remote_env_edit. Prints JSON lines without values; returns
    0 ok, 1 when the backup or a file edit fails (later files are left alone).
    """
    import json  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import re  # pylint: disable=import-outside-toplevel

    def last_line(path: str, key: str) -> str | None:
        try:
            with open(path, "rb") as handle:
                lines = handle.read().decode("utf-8").splitlines()
        except FileNotFoundError:
            return None
        found = [line for line in lines if re.match(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=\s*\S", line)]
        return found[-1] if found else None

    source = args["files"][0]
    old = [last_line(source, key) for key in args["keys"]]
    if not all(old):
        print(json.dumps({"backup": None, "error": "the current pair is not in " + source}))
        return 1
    if not args["dry_run"]:
        old_umask = os.umask(0o077)
        try:
            os.makedirs(args["backup_dir"], mode=0o700, exist_ok=True)
            backup = os.path.join(args["backup_dir"], args["backup_name"])
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(old) + "\n")
            os.chmod(backup, 0o600)
        finally:
            os.umask(old_umask)
    print(json.dumps({"backup": os.path.join(args["backup_dir"], args["backup_name"]), "dry_run": args["dry_run"]}))
    for target in args["files"]:
        print(json.dumps({"file": target}))
        only_present = target not in args["required"]
        ops = [{"op": "replace", "key": key, "value": value, "only_present": only_present}
               for key, value in zip(args["keys"], args["values"])]
        if remote_env_edit({"target": target, "source": None, "dry_run": args["dry_run"], "ops": ops}):  # noqa: F821
            return 1
    return 0


def rotate_program() -> str:
    """The source sent to the VPS: remote_env_edit, remote_rotate and the call that runs it with ARGS."""
    import inspect  # pylint: disable=import-outside-toplevel

    return ("from __future__ import annotations\nfrom typing import Any\n"
            + inspect.getsource(gen_secrets.remote_env_edit) + "\n" + inspect.getsource(remote_rotate)
            + "\nraise SystemExit(remote_rotate(ARGS))\n")


def check_old_program() -> str:
    """Runs on the VPS host: reads the backup file and hands the old pair to a program inside the bridge container
    on stdin; relays its one line."""
    return '''
import json, subprocess, sys
pair = {}
with open(ARGS["file"], encoding="utf-8") as handle:
    for line in handle.read().splitlines():
        name, sep, value = line.partition("=")
        if sep:
            pair[name.strip().replace("export ", "")] = value.strip().strip("'\\"")
if not pair.get(ARGS["key"]) or not pair.get(ARGS["secret"]):
    print("no old pair in the file")
    raise SystemExit(2)
inner = """
import asyncio, json, sys
from livekit import api
d = json.load(sys.stdin)
async def main():
    lk = api.LiveKitAPI(d["url"], d["key"], d["secret"])
    try:
        await lk.room.list_rooms(api.ListRoomsRequest())
        print("OLD KEY STILL WORKS")
        return 1
    except Exception as exc:
        # a TwirpError, or aiohttp's ContentTypeError when the 401 body is not JSON: both carry the HTTP status
        status = getattr(exc, "status", None)
        if status is None:
            print("check failed: %s" % type(exc).__name__)
            return 2
        print("old key rejected: %s" % status)
        return 0 if status == 401 else 1
    finally:
        await lk.aclose()
raise SystemExit(asyncio.run(main()))
"""
done = subprocess.run(["docker", "exec", "-i", ARGS["container"], "python", "-c", inner], text=True,
                      input=json.dumps({"url": ARGS["url"], "key": pair[ARGS["key"]], "secret": pair[ARGS["secret"]]}),
                      capture_output=True, timeout=60)
print((done.stdout.strip().splitlines() or ["no answer"])[-1])
raise SystemExit(done.returncode)
'''


def _report(rows_text: str) -> tuple[list[str], str | None]:
    """Report lines (no values) and the backup path from the remote JSON lines."""
    import json  # pylint: disable=import-outside-toplevel

    lines: list[str] = []
    backup: str | None = None
    current = ""
    for raw in rows_text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if "backup" in row:
            backup = row["backup"]
            if row.get("error"):
                lines.append(f"backup failed: {row['error']}")
        elif "file" in row:
            current = row["file"]
        elif "key" in row:
            extra = "".join(f", {name} {row[name]}" for name in ("len", "fp") if name in row)
            lines.append(f"{current}: {row['key']} {row['action']}{extra}")
        elif "written" in row:
            lines.append(f"{current}: {'written' if row['written'] else 'not changed'} (mode {row.get('mode')})")
    return lines, backup


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="rotate_livekit_keys.py",
                     description="Rotate the LiveKit API key and secret on the VPS and this PC without printing them.")
    parser.add_argument("--check-old", metavar="FILE", help="on the VPS: does the old pair in FILE still work?")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--host", default=gen_secrets.VPS_HOST, help=f"ssh target (default {gen_secrets.VPS_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=None, help="ssh identity file (default JARVIS_SSH_KEY)")
    parser.add_argument("--vps-dir", default=gen_secrets.VPS_DIR, help=f"default {gen_secrets.VPS_DIR}")
    parser.add_argument("--pc-files", default=",".join(str(p) for p in gen_secrets.PC_FILES),
                        help="PC env files, comma-separated (default .env,deploy/windows-local/.env)")
    args = parser.parse_args(argv)
    args.ssh_key = args.ssh_key or default_ssh_key()
    return args


def check_old(args: argparse.Namespace, runner: Any) -> int:
    payload = {"file": vps_path(args.check_old), "key": KEY_NAME, "secret": SECRET_NAME, "container": BRIDGE_CONTAINER,
               "url": LIVEKIT_INTERNAL_URL}
    code, out, err = runner(check_old_program(), payload, args.host, args.ssh_key)
    line = (out.strip().splitlines() or (err.strip().splitlines() or ["no answer"]))[-1]
    print(line[:200])
    return 0 if code == 0 and line.startswith("old key rejected: 401") else 1


def rotate(args: argparse.Namespace, runner: Any, pair: tuple[str, str] | None = None) -> int:
    key, secret = pair or new_pair()
    staged: Any = []
    try:
        paths = [Path(p.strip()) for p in args.pc_files.split(",") if p.strip()]
        files = [env_set.EnvFile(path) for path in paths]
        for f in files:
            rotate_env_secret.replace_in(f, KEY_NAME, env_set.render_value(KEY_NAME, key))
            rotate_env_secret.replace_in(f, SECRET_NAME, env_set.render_value(SECRET_NAME, secret))
        # Temp files before the VPS step: a PC file that cannot be written stops the run while nothing has changed.
        staged = [] if args.dry_run else gen_secrets.stage_private(files)

        vps_dir = args.vps_dir.rstrip("/")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        remote = {"files": [f"{vps_dir}/{name}" for name in VPS_FILES],
                  "required": [f"{vps_dir}/{name}" for name in VPS_FILES[:2]],
                  "keys": [KEY_NAME, SECRET_NAME], "values": [key, secret], "dry_run": args.dry_run,
                  "backup_dir": f"{BACKUP_ROOT}/{stamp}", "backup_name": BACKUP_NAME}
        code, out, err = runner(rotate_program(), remote, args.host, args.ssh_key)
        report, backup = _report(out)
        for line in report:
            print(f"vps {line}")
        if code:
            last = (err.strip().splitlines() or ["no answer"])[-1]
            print(f"rotate_livekit_keys: the VPS step failed (exit {code}): {last[:200]}; nothing written on this PC",
                  file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"dry run: the pair would be rotated in {len(VPS_FILES)} VPS files (when present) and "
                  f"{len(files)} PC files; nothing written")
            return 0
        try:
            written = gen_secrets.commit_private(staged)
        except EnvError as exc:
            print(f"rotate_livekit_keys: the VPS files already hold the new pair (key fp {gen_secrets.fingerprint(key)})"
                  f", but this PC's files were not updated: {exc}. Copy the pair over by hand from the VPS .env.",
                  file=sys.stderr)
            return 1
        print(f"written: {', '.join(written)}")
        print(f"{KEY_NAME} rotated (len {len(key)}, fp {gen_secrets.fingerprint(key)}); "
              f"{SECRET_NAME} rotated (len {len(secret)}, fp {gen_secrets.fingerprint(secret)})")
        print(f"old pair backed up on the VPS: {backup}")
        print("next: ssh to the VPS and run 'cd " + vps_dir + " && docker compose -p jarvis up -d --no-deps "
              "--force-recreate livekit ui-bridge', then agent_ctl restart, then --check-old " + str(backup))
        return 0
    except EnvError as exc:
        print(f"rotate_livekit_keys: {exc}", file=sys.stderr)
        return 2
    finally:
        gen_secrets.discard_private(staged)


def main(argv: list[str] | None = None, runner: Any = None) -> int:
    args = parse_args(argv)
    runner = runner or gen_secrets.run_ssh
    if args.check_old:
        return check_old(args, runner)
    return rotate(args, runner)


if __name__ == "__main__":
    sys.exit(main())
