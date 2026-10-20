"""The PC fallback's own LiveKit key pair (plan task C-127 step 6; contracts K-47 and K-72). It is applied only in the
V6 LiveKit window (deploy/hostinger/RUNBOOK.md, 'V6: the PC fallback's LiveKit pair') and revocable on its own.

Usage (from the repo root, with the project venv; each VPS step is one ssh run, values on ssh stdin only):
    .venv_312/Scripts/python.exe scripts/ops/livekit_pc_pair.py create [--rotate] [--dry-run]
    .venv_312/Scripts/python.exe scripts/ops/livekit_pc_pair.py verify
    .venv_312/Scripts/python.exe scripts/ops/livekit_pc_pair.py activate [--dry-run]
    .venv_312/Scripts/python.exe scripts/ops/livekit_pc_pair.py revoke [--dry-run]
    options: --host H, --ssh-key K (default JARVIS_SSH_KEY), --vps-dir D, --env-file PATH (repeatable), --run-dir D

create    Makes key 'API' + 12 letters and digits and secret secrets.token_urlsafe(48) on this PC. The VPS step backs
          the compose .env up (/root/jarvis-backups/env/<UTC>-pre-livekit-pc-pair/hostinger.env) and writes
          LIVEKIT_PC_API_KEY and LIVEKIT_PC_API_SECRET into it (atomic, mode 600, every other line byte for byte); a
          pair already there is refused unless --rotate. Then this PC stores the same pair under the same pending
          names: LIVEKIT_PC_API_KEY in the root .env, LIVEKIT_PC_API_SECRET in the root .env or, while the fallback
          secrets are DPAPI-wrapped (vps mode), in run/fallback.env.dpapi (scripts/ops/fallback_env.py). Nothing
          uses the pair yet: LiveKit learns it only when the V6 compose line names it and livekit is recreated.
verify    Checks THIS PC's copy of the PC pair, the one the PC worker would use: the pending names before 'activate',
          LIVEKIT_API_KEY/LIVEKIT_API_SECRET after it (the secret from the env files or, while wrapped, unwrapped from
          run/fallback.env.dpapi in this process). It goes on ssh stdin to the VPS, and when its key is the VPS .env's
          LIVEKIT_PC_API_KEY, a token signed with it calls ListRooms on http://jarvis-livekit:7880 inside jarvis-bridge
          (stdin again): 'PC pair accepted' or 'PC pair rejected: <status>'. The VPS also returns the HMAC digests of
          its LIVEKIT_PC_API_KEY and LIVEKIT_PC_API_SECRET, and a second line says whether this PC's copy is the VPS
          .env pair. Exit 0 only when LiveKit accepts this PC's copy and it equals the VPS .env pair; after a revoke it
          differs (and once livekit is recreated, LiveKit rejects it: 401).
activate  First verify with the pending pair: LiveKit must accept it and both its key and its secret must be the VPS
          .env ones (HMAC digests only), so a revoked or stale pending secret never replaces the working main pair.
          Then, on this PC only, LIVEKIT_API_KEY and LIVEKIT_API_SECRET take the pending pair (the key in the root
          .env, the secret in the root .env or, while wrapped, in run/fallback.env.dpapi), the pending names go, and
          no other env file keeps a copy of the main pair (deploy/windows-local/.env loses its LIVEKIT_API_KEY and
          LIVEKIT_API_SECRET lines); the next 'agent_ctl brain pc' starts the PC worker with it. From then on this PC
          no longer holds the main pair: rotate that one with rotate_livekit_keys.py --pc-files '' (while the VPS
          .env has a LIVEKIT_PC_API_KEY, its VPS step refuses a rotation with PC files when this PC's LIVEKIT_API_KEY
          is that key, or is not the VPS .env's LIVEKIT_API_KEY: also after a 'create --rotate' until 'activate'
          runs again). The env files
          are staged before the store is written; if they cannot be written, the store is put back as it was, so
          'activate' can simply run again.
revoke    On the VPS only: LIVEKIT_PC_API_SECRET gets a new random value that nobody holds (made on the VPS, never
          sent). After 'docker compose -p jarvis up -d --no-deps --force-recreate livekit' (a LiveKit restart, so in an
          announced window) the PC pair is dead ('verify' then prints 'PC pair rejected: 401'), while the bridge, the
          VPS agent and every browser keep the main pair. The PC fallback works again after create --rotate, that
          recreate and activate.
Prints names, lengths and fingerprints (sha256 of 'jarvis-fp:' + value, first 8 hex), never a value.
Exit codes: 0 ok, 1 refused, a check failed or the VPS step failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import os
import secrets
import string
import subprocess
import sys
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


fallback_env = _load_sibling("fallback_env")
gen_secrets = fallback_env.gen_secrets
env_set = fallback_env.env_set
EnvError = env_set.EnvError
er = _load_sibling("env_remote")
# The shipped helpers under their bare names (program() defines them on the VPS).
env_values, env_set_lines, read_text, file_mode = er.env_values, er.env_set_lines, er.read_text, er.file_mode
value_digest, atomic_write_private = er.value_digest, er.atomic_write_private
make_backup_dir, backup_private = er.make_backup_dir, er.backup_private

VPS_DIR = gen_secrets.VPS_DIR
PC_KEY, PC_SECRET = "LIVEKIT_PC_API_KEY", "LIVEKIT_PC_API_SECRET"
MAIN_KEY, MAIN_SECRET = "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"
BRIDGE_CONTAINER = "jarvis-bridge"
LIVEKIT_INTERNAL_URL = "http://jarvis-livekit:7880"
BACKUP_LABEL = "pre-livekit-pc-pair"
_ALNUM = string.ascii_letters + string.digits
# Runs inside jarvis-bridge (it has the livekit api package); the pair arrives on stdin, never on a command line.
VERIFY_INNER = """
import asyncio, json, sys
from livekit import api
d = json.load(sys.stdin)
async def main():
    lk = api.LiveKitAPI(d["url"], d["key"], d["secret"])
    try:
        await lk.room.list_rooms(api.ListRoomsRequest())
        print("PC pair accepted")
        return 0
    except Exception as exc:
        status = getattr(exc, "status", None)
        print("PC pair rejected: %s" % (status if status is not None else type(exc).__name__))
        return 1
    finally:
        await lk.aclose()
raise SystemExit(asyncio.run(main()))
"""


# --------------------------------------------------------------------------- shipped to the VPS


def remote_pair_write(args: dict[str, Any]) -> int:
    """Runs on the VPS: mode 'create' writes the given pair into the compose .env, 'revoke' gives LIVEKIT_PC_API_SECRET
    a new random value made here. Backup first, atomic write. One JSON line, names and lengths only."""
    env_path = os.path.join(args["dir"], ".env")
    text = read_text(env_path)
    row: dict[str, Any] = {"mode": args["mode"]}
    if text is None:
        row.update(result="refused", error=f"{env_path} not found")
        print(json.dumps(row))
        return 1
    have = env_values(text)
    present = [name for name in (args["key_name"], args["secret_name"]) if have.get(name)]
    if args["mode"] == "create":
        values = dict(args["values"])
        if present and not args["rotate"]:
            row.update(result="refused", error=f"{', '.join(present)} already in {env_path}: --rotate replaces it")
            print(json.dumps(row))
            return 1
    else:
        if len(present) < 2:
            row.update(result="refused", error=f"no PC pair in {env_path}: nothing to revoke")
            print(json.dumps(row))
            return 1
        values = {args["secret_name"]: secrets.token_urlsafe(48)}  # nobody ever holds it
    row["names"] = sorted(values)
    if args["dry_run"]:
        row.update(result="dry_run", mode_bits=file_mode(env_path))
        print(json.dumps(row))
        return 0
    row["backup"] = make_backup_dir(args["backup_root"], args["stamp"], args["label"])
    backup_private(env_path, row["backup"], "hostinger.env")
    atomic_write_private(env_path, env_set_lines(text, values).encode("utf-8"))
    row.update(result="written", lengths={name: len(value) for name, value in values.items()},
               mode_bits=file_mode(env_path))
    print(json.dumps(row))
    return 0


def remote_pair_verify(args: dict[str, Any]) -> int:
    """Runs on the VPS host: the caller's copy of the PC pair (args['pc'], from ssh stdin) goes on stdin to a program
    inside jarvis-bridge, which signs a ListRooms call with it; only when its key is the compose .env's PC key, so no
    other pair is ever tried. One JSON line: that program's one line and exit code, plus the HMAC digests (keyed with
    the caller's nonce) of the compose .env's PC key and secret, which the caller compares with its own copy."""
    have = env_values(read_text(os.path.join(args["dir"], ".env")) or "")
    key, secret = have.get(args["key_name"]), have.get(args["secret_name"])
    if not key or not secret:
        print(json.dumps({"result": "refused", "error": "no PC pair in the compose .env"}))
        return 1
    pc = args.get("pc") or {}
    pc_key, pc_secret = str(pc.get("key") or ""), str(pc.get("secret") or "")
    if not pc_key or not pc_secret:
        print(json.dumps({"result": "refused", "error": "no copy of the pair came from the PC"}))
        return 1
    row = {"result": "checked", "key_digest": value_digest(args["nonce"], args["key_name"], key),
           "secret_digest": value_digest(args["nonce"], args["secret_name"], secret)}
    if not hmac.compare_digest(pc_key.encode("utf-8"), key.encode("utf-8")):
        row.update(line="not checked: the PC's key is not the compose .env's " + args["key_name"], code=None)
        print(json.dumps(row))
        return 1
    try:
        done = subprocess.run(list(args["docker"]) + ["exec", "-i", args["container"], "python", "-c", args["inner"]],
                              input=json.dumps({"url": args["url"], "key": pc_key, "secret": pc_secret}), text=True,
                              capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"result": "failed", "error": "docker exec did not run (" + type(exc).__name__ + ")"}))
        return 1
    row.update(line=((done.stdout or "").strip().splitlines() or ["no answer"])[-1][:200], code=done.returncode)
    print(json.dumps(row))
    return 0 if done.returncode == 0 else 1


def write_program() -> str:
    return er.program(er.env_entries, er.env_value, er.env_values, er.render_env_value, er.env_set_lines,
                      er.read_text, er.file_mode, er.atomic_write_private, er.make_backup_dir, er.backup_private,
                      remote_pair_write, entry="remote_pair_write")


def verify_program() -> str:
    return er.program(er.env_entries, er.env_value, er.env_values, er.read_text, er.value_digest,
                      remote_pair_verify, entry="remote_pair_verify")


# --------------------------------------------------------------------------- PC side


def new_pair() -> tuple[str, str]:
    """(key, secret): 'API' + 12 letters and digits, and secrets.token_urlsafe(48) (rotate_livekit_keys' format)."""
    return "API" + "".join(secrets.choice(_ALNUM) for _ in range(12)), secrets.token_urlsafe(48)


def _remote(args: argparse.Namespace, **extra: Any) -> dict[str, Any]:
    return {"dir": args.vps_dir, "key_name": PC_KEY, "secret_name": PC_SECRET, "dry_run": args.dry_run,
            "backup_root": er.BACKUP_ROOT, "stamp": er.utc_stamp(),
            "label": BACKUP_LABEL, **extra}


def _vps_step(args: argparse.Namespace, runner: Any, program: str, remote: dict[str, Any]) -> dict[str, Any] | None:
    code, out, err = runner(program, remote, args.host, args.ssh_key)
    rows = er.json_rows(out)
    if not rows:
        print(f"livekit_pc_pair: the VPS step failed (exit {code}): {er.failure_line(err)}", file=sys.stderr)
        return None
    return rows[-1]


def _pc_paths(args: argparse.Namespace) -> list[Path]:
    return args.env_file or list(fallback_env.ENV_PATHS)


def create(args: argparse.Namespace, runner: Any, pair: tuple[str, str] | None = None) -> int:
    key, secret = pair or new_pair()
    paths = _pc_paths(args)
    store = fallback_env.store_path(args.run_dir)
    try:  # this PC must be able to take the pair before the VPS changes
        env_set.EnvFile(Path(paths[0]))
        if store.exists():
            fallback_env.load_store(store, fallback_env.dpapi_unprotect if args.unprotect is None else args.unprotect)
    except EnvError as exc:
        print(f"livekit_pc_pair: refused before the VPS step: {exc}", file=sys.stderr)
        return 1
    row = _vps_step(args, runner, write_program(), _remote(args, mode="create", rotate=args.rotate,
                                                           values={PC_KEY: key, PC_SECRET: secret}))
    if row is None:
        return 1
    if row.get("result") != "written" and row.get("result") != "dry_run":
        print(f"livekit_pc_pair: {row.get('result')}: {row.get('error')}", file=sys.stderr)
        return 1
    print(f"vps {args.vps_dir}/.env: {', '.join(row.get('names') or [])} "
          f"{'would be written' if args.dry_run else 'written'}"
          + (f" (backup {row['backup']}/hostinger.env)" if row.get("backup") else ""))
    if args.dry_run:
        print("dry run: nothing written on the VPS or this PC")
        return 0
    report = fallback_env.put_values({PC_KEY: key, PC_SECRET: secret}, paths, args.run_dir,
                                     **_store_io(args))
    for line in report.lines:
        print(f"pc  {line}")
    if report.code:
        print(f"livekit_pc_pair: the VPS .env holds the new pair (key fp {gen_secrets.fingerprint(key)}), but this PC "
              "does not; run 'create --rotate' again", file=sys.stderr)
        return 1
    print(f"{PC_KEY} len {len(key)}, fp {gen_secrets.fingerprint(key)}; {PC_SECRET} len {len(secret)}, "
          f"fp {gen_secrets.fingerprint(secret)}")
    print("next (V6 window): the second LIVEKIT_KEYS entry in docker-compose.yml, recreate livekit, then 'verify' and "
          "'activate' (RUNBOOK)")
    return 0


def _store_io(args: argparse.Namespace) -> dict[str, Any]:
    """The DPAPI calls and the ACL step (the tests pass fakes through the namespace)."""
    io: dict[str, Any] = {}
    if args.protect is not None:
        io["protect"] = args.protect
    if args.unprotect is not None:
        io["unprotect"] = args.unprotect
    if args.restrict is not None:
        io["restrict"] = args.restrict
    return io


def _pc_copy(args: argparse.Namespace) -> tuple[str, str, str, str]:
    """(key name, secret name, key, secret): this PC's copy of the PC pair, as the PC worker would use it. The
    pending names while the pending key exists (before 'activate'), else LIVEKIT_API_KEY/LIVEKIT_API_SECRET (which
    hold it after 'activate'). Each value comes from the env files or, while wrapped, from run/fallback.env.dpapi
    (unwrapped in this process only, never printed); '' when this PC has none. Raises EnvError."""
    paths = _pc_paths(args)
    names = (PC_KEY, PC_SECRET)
    found = fallback_env.find_values(names, paths, args.run_dir, unprotect=args.unprotect)
    if not found.get(PC_KEY):
        names = (MAIN_KEY, MAIN_SECRET)
        found = fallback_env.find_values(names, paths, args.run_dir, unprotect=args.unprotect)
    return names[0], names[1], found.get(names[0], ""), found.get(names[1], "")


def verify(args: argparse.Namespace, runner: Any) -> tuple[int, str]:
    """(exit code, verdict): 'accepted' (LiveKit takes this PC's copy and it is the VPS .env pair), 'rejected'
    (LiveKit refuses this PC's copy), 'differs' (this PC's copy is not the VPS .env pair: revoked, rotated elsewhere or
    never created here) or 'failed' (nothing could be checked). Prints names and verdicts only."""
    nonce = er.new_nonce()
    try:
        key_name, secret_name, key, secret = _pc_copy(args)
    except EnvError as exc:
        print(f"livekit_pc_pair: verify refused: {exc}", file=sys.stderr)
        return 1, "failed"
    if not key or not secret:
        print(f"livekit_pc_pair: verify refused: this PC has no {key_name if not key else secret_name}, so it holds "
              "no copy of the PC pair to check (run 'create' first)", file=sys.stderr)
        return 1, "failed"
    row = _vps_step(args, runner, verify_program(), {
        "dir": args.vps_dir, "key_name": PC_KEY, "secret_name": PC_SECRET, "docker": ["docker"],
        "container": BRIDGE_CONTAINER, "url": LIVEKIT_INTERNAL_URL, "inner": VERIFY_INNER, "nonce": nonce,
        "pc": {"key": key, "secret": secret}})
    if row is None or row.get("result") != "checked":
        if row is not None:
            print(f"livekit_pc_pair: verify {row.get('result')}: {row.get('error')}", file=sys.stderr)
        return 1, "failed"
    verdict = _copy_verdict(row, nonce, (key_name, secret_name, key, secret))
    return (0 if verdict == "accepted" else 1), verdict


def _copy_verdict(row: dict[str, Any], nonce: str, copy: tuple[str, str, str, str]) -> str:
    """Prints the VPS step's line and whether this PC's copy is the VPS .env pair (HMAC digests keyed with this run's
    nonce); 'accepted', 'rejected' or 'differs' (see verify)."""
    key_name, secret_name, key, secret = copy
    line = str(row.get("line") or "")
    print(line)
    if not er.digests_equal(row.get("key_digest"), er.value_digest(nonce, PC_KEY, key)):
        print(f"this PC's {key_name} is not {PC_KEY} of the VPS .env: this PC holds no copy of the PC pair (run "
              "'create', or 'create --rotate')")
        return "differs"
    if not er.digests_equal(row.get("secret_digest"), er.value_digest(nonce, PC_SECRET, secret)):
        print(f"this PC's {secret_name} is not {PC_SECRET} of the VPS .env: the PC pair was revoked (or rotated "
              "elsewhere); LiveKit drops this PC's copy at its next recreate ('create --rotate' makes a new pair)")
        return "differs"
    print(f"this PC's {key_name}/{secret_name} are the VPS .env pair (HMAC digests)")
    return "accepted" if row.get("code") == 0 and line == "PC pair accepted" else "rejected"


def activate(args: argparse.Namespace, runner: Any) -> int:
    paths = _pc_paths(args)
    try:
        pending = fallback_env.find_values((PC_KEY, PC_SECRET), paths, args.run_dir, unprotect=args.unprotect)
    except EnvError as exc:
        print(f"livekit_pc_pair: {exc}", file=sys.stderr)
        return 1
    pending_key = pending.get(PC_KEY)
    if not pending_key or not pending.get(PC_SECRET):
        print(f"livekit_pc_pair: refused: no {PC_KEY} and {PC_SECRET} on this PC (run 'create' first)",
              file=sys.stderr)
        return 1
    code, verdict = verify(args, runner)   # it checks the pending pair: the pending names win while they exist
    if verdict == "differs":
        print(f"livekit_pc_pair: refused: this PC's pending pair is not the one in the VPS .env (revoked or rotated "
              f"elsewhere), so it never replaces {MAIN_KEY}/{MAIN_SECRET} (run 'create --rotate')", file=sys.stderr)
        return 1
    if code:
        print("livekit_pc_pair: refused: LiveKit does not accept the PC pair yet (the V6 compose line and the "
              "livekit recreate come first)", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run: {MAIN_KEY} and {MAIN_SECRET} would take the PC pair on this PC; nothing written")
        return 0
    report = fallback_env.move_values({PC_KEY: MAIN_KEY, PC_SECRET: MAIN_SECRET}, paths, args.run_dir,
                                      **_store_io(args))
    for line in report.lines:
        print(f"pc  {line}")
    if report.code:
        return 1
    print(f"activated: this PC's {MAIN_KEY}/{MAIN_SECRET} are the PC pair (key fp "
          f"{gen_secrets.fingerprint(pending_key)}). Rotate the main pair from now on with "
          "rotate_livekit_keys.py --pc-files ''")
    return 0


def revoke(args: argparse.Namespace, runner: Any) -> int:
    row = _vps_step(args, runner, write_program(), _remote(args, mode="revoke", rotate=True, values={}))
    if row is None:
        return 1
    if row.get("result") not in ("written", "dry_run"):
        print(f"livekit_pc_pair: {row.get('result')}: {row.get('error')}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run: {PC_SECRET} in {args.vps_dir}/.env would get a new random value; nothing written")
        return 0
    print(f"vps {args.vps_dir}/.env: {PC_SECRET} replaced by a value nobody holds (len "
          f"{(row.get('lengths') or {}).get(PC_SECRET)}; backup {row.get('backup')}/hostinger.env)")
    print("next: in an announced window run 'cd " + args.vps_dir + " && docker compose -p jarvis up -d --no-deps "
          "--force-recreate livekit'; then 'verify' (it signs with this PC's copy) prints 'PC pair rejected: 401'")
    return 0


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="livekit_pc_pair.py", description="The PC fallback's own LiveKit key pair (C-127 step 6); "
                                                            "names, lengths and fingerprints only.")
    parser.add_argument("action", choices=("create", "verify", "activate", "revoke"))
    parser.add_argument("--rotate", action="store_true", help="create: replace a pair the VPS .env already has")
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    parser.add_argument("--host", default=gen_secrets.VPS_HOST, help=f"ssh target (default {gen_secrets.VPS_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=None, help="ssh identity file (default JARVIS_SSH_KEY)")
    parser.add_argument("--vps-dir", default=VPS_DIR, help=f"folder of the compose .env (default {VPS_DIR})")
    parser.add_argument("--env-file", action="append", type=Path, metavar="PATH",
                        help="PC env files instead of .env and deploy/windows-local/.env (the first is the root .env)")
    parser.add_argument("--run-dir", type=Path, help="folder of run/fallback.env.dpapi (default JARVIS_RUN_DIR)")
    args = parser.parse_args(argv)
    if args.rotate and args.action != "create":
        parser.error("--rotate goes with create")
    if args.dry_run and args.action == "verify":
        parser.error("verify writes nothing anyway")
    args.ssh_key = args.ssh_key or er.default_ssh_key()
    args.vps_dir = er.vps_path(args.vps_dir).rstrip("/") or "/"
    args.protect = args.unprotect = args.restrict = None  # the DPAPI defaults (tests replace them)
    return args


def main(argv: list[str] | None = None, runner: Any = None, store_io: dict[str, Any] | None = None) -> int:
    args = parse_args(argv)
    for name, value in (store_io or {}).items():
        setattr(args, name, value)
    runner = runner or gen_secrets.run_ssh
    if args.action == "create":
        return create(args, runner)
    if args.action == "verify":
        return verify(args, runner)[0]
    if args.action == "activate":
        return activate(args, runner)
    return revoke(args, runner)


if __name__ == "__main__":
    sys.exit(main())
