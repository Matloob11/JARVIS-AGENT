"""Rotate the LiveKit API key and secret without printing them (plan task C-17, OP-14).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/rotate_livekit_keys.py [--dry-run] [--host H] [--ssh-key K] [--run-dir D]
    .venv_312/Scripts/python.exe scripts/ops/rotate_livekit_keys.py --check-old /root/jarvis-backups/<TS>/livekit-old.env

Rotation: a new pair is generated on this PC (key 'API' + 12 letters and digits, secret secrets.token_urlsafe(48)).
One ssh run (python3 on the VPS; the program and every value travel on ssh stdin, never on a command line) first
copies the current pair from deploy/hostinger/.env to /root/jarvis-backups/<TS>/livekit-old.env (mode 600, folder
700) and then replaces LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env and bridge.env, and in .env.agent when that
file has them (each file atomic, mode 600, every other line byte for byte). It stops at the first failure. The PC env
files (.env and deploy/windows-local/.env) are staged before the VPS step and renamed into place after it, as
rotate_env_secret.py does: nothing on this PC changes when the VPS step fails. Only lengths and fingerprints (sha256
of 'jarvis-fp:' + value, first 8 hex) are printed.

vps mode (C-127 step 5, D13): while run/fallback.env.dpapi exists ('agent_ctl brain vps' DPAPI-wrapped the PC
fallback secrets), the new secret never goes into a PC env file. Before the VPS step scripts/ops/fallback_env.py
put_values checks, writing nothing, that this Windows user can unwrap the store and that the env file edits can be
written (else exit 2, nothing written anywhere); after it the secret goes into the store and the key into the root
.env (the first PC file), and no other PC env file keeps an older LiveKit key. When that fails after the VPS step,
run the rotation again: it makes a new pair for both sides (between 'livekit_pc_pair.py create' and 'activate' the
'pc_not_main' guard below refuses that run, since this PC's key is no longer the VPS .env's: run 'activate', or copy
the VPS .env's main pair over by hand with fallback_env.py --set). The PC worker takes the pair at the next
'agent_ctl brain pc', which unwraps the store. After V6 'livekit_pc_pair.py activate' this PC holds the PC pair, not
the main one (and still the earlier PC pair after a later 'create --rotate', until its 'activate'): rotate the main
pair with --pc-files '' then.

The PC pair guards (C-127 step 6): the VPS step also gets this PC's current LIVEKIT_API_KEY values (keys, never a
secret; on ssh stdin like the rest). While the VPS .env holds a PC fallback pair (LIVEKIT_PC_API_KEY, from
'livekit_pc_pair.py create') it refuses before any backup or write (exit 2, nothing written on the VPS or this PC, the
dry run too) when one of them is that LIVEKIT_PC_API_KEY ('pc_pair': this PC holds the PC pair since 'activate'), or
when none of them is the VPS .env's LIVEKIT_API_KEY ('pc_not_main': this PC does not hold the main pair, for example the
earlier PC pair while a 'create --rotate' waits for its 'activate', an older main key, or no key). A rotation with PC
files would give such a PC the new main pair, so a later 'livekit_pc_pair.py revoke' (a lost laptop) would cut nothing
off. Without a PC pair in the VPS .env (before V6 'create') neither guard applies, so a PC that an earlier rotation
missed is brought up to date; before 'activate' this PC holds the main pair and the rotation goes ahead. --pc-files ''
sends no key and rotates the VPS only.

The pair takes effect when the readers restart (printed as the next steps): LiveKit (LIVEKIT_KEYS from the compose
.env) and the bridge (bridge.env) with 'docker compose -p jarvis up -d --no-deps --force-recreate livekit ui-bridge'
on the VPS, then the PC worker with agent_ctl restart (in vps mode the VPS brain's agent and agent-svc instead: they
read the pair from .env.agent). Back up first: deploy/hostinger/backup_vps.sh on the VPS.

--check-old FILE: the old pair from FILE (on the VPS) goes on stdin into the jarvis-bridge container, which mints a
token with it and calls ListRooms on http://jarvis-livekit:7880. Prints 'old key rejected: 401' (exit 0) or 'OLD KEY
STILL WORKS' (exit 1); never a value or the server's message.

Exit codes: 0 ok, 1 the VPS step failed (nothing written on this PC), a PC file could not be replaced after the VPS
files were written, or the old key still works; 2 usage or file error, or refused (nothing written).
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
from types import ModuleType
from typing import Any


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# fallback_env.py (the vps-mode DPAPI store) loads rotate_env_secret.py, which loads gen_secrets.py and env_set.py;
# this tool uses those same copies, so the EnvError that fallback_env raises is the one caught here.
fallback_env = _load_sibling("fallback_env")
rotate_env_secret = fallback_env.rotate_env_secret
gen_secrets = rotate_env_secret.gen_secrets
env_set = rotate_env_secret.env_set
EnvError = env_set.EnvError

KEY_NAME, SECRET_NAME = "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"
PC_PAIR_KEY_NAME = "LIVEKIT_PC_API_KEY"  # the PC fallback's own pair in the VPS .env (livekit_pc_pair.py, V6)
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
    0 ok, 1 when the backup or a file edit fails (later files are left alone), 2 refused before anything was written
    (the PC pair guards, only while the first file has a LIVEKIT_PC_API_KEY): 'pc_pair' when one of the PC's
    LIVEKIT_API_KEY values (args['pc_keys']) is that LIVEKIT_PC_API_KEY, 'pc_not_main' when the PC files are written
    (args['pc_files']) and none of those values is the first file's LIVEKIT_API_KEY.
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

    def value_of(line: str | None) -> str:
        """An assignment line's value as compose reads it: quotes removed, an unquoted value cut before ' #'."""
        raw = line.split("=", 1)[1].strip() if line and "=" in line else ""
        if raw[:1] in ("'", '"'):
            end = raw.find(raw[0], 1)
            return raw[1:end] if end > 0 else raw[1:]
        return re.split(r"[ \t]+#", raw, maxsplit=1)[0].strip()

    source = args["files"][0]
    old = [last_line(source, key) for key in args["keys"]]
    if not all(old):
        print(json.dumps({"backup": None, "error": "the current pair is not in " + source}))
        return 1
    # The PC pair guards (C-127 step 6), while the first file holds a PC fallback pair: never overwrite that pair on a
    # PC that holds it, and never hand the main pair to a PC that does not hold it (the earlier PC pair while a
    # 'create --rotate' waits for its 'activate', an older main key, or no key at all)
    pc_pair_key = value_of(last_line(source, args["pc_pair_key_name"])) if args.get("pc_pair_key_name") else ""
    held_keys = args.get("pc_keys") or []
    if pc_pair_key and pc_pair_key in held_keys:
        print(json.dumps({"refused": "pc_pair"}))
        return 2
    if pc_pair_key and args.get("pc_files") and value_of(old[0]) not in held_keys:
        print(json.dumps({"refused": "pc_not_main"}))
        return 2
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
        if not isinstance(row, dict) or "refused" in row:   # a refusal: _refusal() reads it
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


def _refusal(rows_text: str) -> str | None:
    """The reason the VPS step refused before writing anything ('pc_pair', 'pc_not_main'), else None."""
    import json  # pylint: disable=import-outside-toplevel

    for raw in rows_text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("refused"):
            return str(row["refused"])
    return None


def pc_keys(files: list[Any]) -> list[str]:
    """This PC's LIVEKIT_API_KEY values, one per distinct value of the PC files (keys, never a secret): the VPS step
    compares them with its LIVEKIT_PC_API_KEY and its LIVEKIT_API_KEY (the PC pair guards)."""
    return sorted({f.value(KEY_NAME) for f in files if f.value(KEY_NAME)})


def _refusal_text(reason: str, vps_dir: str) -> str:
    """Why the VPS step refused (names only) and the way on."""
    if reason == "pc_pair":
        return (f"this PC's {KEY_NAME} is {PC_PAIR_KEY_NAME} of {vps_dir}/.env, the PC fallback's own pair "
                "('livekit_pc_pair.py activate', V6). A rotation that also writes this PC would overwrite that pair "
                "with the new main one, and a later 'livekit_pc_pair.py revoke' would then cut nothing off. Rotate the "
                "main pair with --pc-files '' (this PC keeps the PC pair).")
    if reason == "pc_not_main":
        return (f"{vps_dir}/.env holds a PC fallback pair ({PC_PAIR_KEY_NAME}, V6) and no {KEY_NAME} of this PC is "
                f"that file's {KEY_NAME}, so this PC does not hold the main pair: it holds the earlier PC pair (a "
                "'livekit_pc_pair.py create --rotate' after 'activate' made a new pending one), an older main key or "
                "none. A rotation that also writes this PC would hand it the new main pair, and a later "
                "'livekit_pc_pair.py revoke' would then cut nothing off. Rotate the main pair with --pc-files '' (the "
                "VPS only); this PC gets the PC pair with 'livekit_pc_pair.py verify' and 'activate' (V6).")
    return f"{reason}."


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
                        help="PC env files, comma-separated, the root .env first (default .env,deploy/windows-local/"
                             ".env); '' leaves this PC alone (after V6 'activate' it holds the PC pair: then '')")
    parser.add_argument("--run-dir", type=Path, default=None,
                        help="folder of run/fallback.env.dpapi, the vps-mode store (default JARVIS_RUN_DIR or run/)")
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


def _wrapped_pc_put(args: argparse.Namespace, paths: list[Path], key: str, secret: str, *, check: bool) -> Any:
    """vps mode: the secret into run/fallback.env.dpapi (never an env file), the key into the root .env, and no other
    env file keeps an older key (fallback_env.put_values; with check nothing is written). Its report: names only."""
    return fallback_env.put_values({KEY_NAME: key, SECRET_NAME: secret}, paths, args.run_dir,
                                   exclusive=True, check=check)


def rotate(args: argparse.Namespace, runner: Any, pair: tuple[str, str] | None = None) -> int:
    key, secret = pair or new_pair()
    staged: Any = []
    try:
        paths = [Path(p.strip()) for p in args.pc_files.split(",") if p.strip()]
        files = [env_set.EnvFile(path) for path in paths]
        # vps mode (C-127 step 5, D13): while the DPAPI store exists the new secret must never reach an env file
        wrapped = bool(paths) and fallback_env.store_path(args.run_dir).exists()
        if wrapped:
            # Before the VPS step: this Windows user can unwrap the store and the env file edits stage (nothing written)
            check = _wrapped_pc_put(args, paths, key, secret, check=True)
            if check.code:
                for line in check.lines:
                    print(f"pc  {line}", file=sys.stderr)
                print("rotate_livekit_keys: this PC cannot take the pair (vps mode: its secret goes into the DPAPI "
                      "store); nothing was written on the VPS or this PC", file=sys.stderr)
                return 2
            if args.dry_run:
                for line in check.lines:
                    print(f"pc  {line}")
        else:
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
                  "backup_dir": f"{BACKUP_ROOT}/{stamp}", "backup_name": BACKUP_NAME,
                  # the PC pair guards: whether this PC is written, and its LIVEKIT_API_KEY values (none with
                  # --pc-files '')
                  "pc_files": bool(files), "pc_keys": pc_keys(files), "pc_pair_key_name": PC_PAIR_KEY_NAME}
        code, out, err = runner(rotate_program(), remote, args.host, args.ssh_key)
        report, backup = _report(out)
        for line in report:
            print(f"vps {line}")
        refusal = _refusal(out)
        if refusal:
            print(f"rotate_livekit_keys: refused by the VPS step: {_refusal_text(refusal, vps_dir)} Nothing was "
                  "written on the VPS or this PC.", file=sys.stderr)
            return 2
        if code:
            last = (err.strip().splitlines() or ["no answer"])[-1]
            print(f"rotate_livekit_keys: the VPS step failed (exit {code}): {last[:200]}; nothing written on this PC",
                  file=sys.stderr)
            return 1
        if args.dry_run:
            where = (f"; on this PC (vps mode) {KEY_NAME} would go into {files[0].label} and {SECRET_NAME} into the "
                     "DPAPI store" if wrapped else f" and {len(files)} PC files")
            print(f"dry run: the pair would be rotated in {len(VPS_FILES)} VPS files (when present){where}; "
                  "nothing written")
            return 0
        if wrapped:
            done = _wrapped_pc_put(args, paths, key, secret, check=False)
            for line in done.lines:
                print(f"pc  {line}", file=sys.stderr if done.code else sys.stdout)
            if done.code:
                print(f"rotate_livekit_keys: the VPS files already hold the new pair (key fp "
                      f"{gen_secrets.fingerprint(key)}), but this PC does not (see above). Fix the cause, then run the "
                      "rotation again: it makes a new pair for the VPS and this PC.", file=sys.stderr)
                return 1
        else:
            try:
                written = gen_secrets.commit_private(staged)
            except EnvError as exc:
                print(f"rotate_livekit_keys: the VPS files already hold the new pair (key fp "
                      f"{gen_secrets.fingerprint(key)}), but this PC's files were not updated: {exc}. Copy the pair "
                      "over by hand from the VPS .env.", file=sys.stderr)
                return 1
            print(f"written: {', '.join(written)}")
        print(f"{KEY_NAME} rotated (len {len(key)}, fp {gen_secrets.fingerprint(key)}); "
              f"{SECRET_NAME} rotated (len {len(secret)}, fp {gen_secrets.fingerprint(secret)})")
        print(f"old pair backed up on the VPS: {backup}")
        recreate = f"'cd {vps_dir} && docker compose -p jarvis up -d --no-deps --force-recreate livekit ui-bridge'"
        if wrapped:
            print(f"next: ssh to the VPS and run {recreate}, then the VPS brain's agents, which read the pair from "
                  f".env.agent: 'cd {vps_dir} && docker compose -p jarvis --profile vps-brain up -d --no-deps "
                  f"--no-build --pull never --force-recreate agent agent-svc', then --check-old {backup}. This PC "
                  "takes the new pair at its next 'agent_ctl brain pc' (it unwraps the store).")
        else:
            print(f"next: ssh to the VPS and run {recreate}, then agent_ctl restart, then --check-old {backup}")
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
