"""The secrets split on the VPS, stage V2b (plan task C-127 steps 2 and 7, contracts K-47 and K-75): bridge.env gets
every name the bridge needs, and the compose .env loses the agent's secrets. Only names are ever printed.

Usage (from the repo root on the PC, with the project venv; the VPS step is one ssh run whose program and arguments
travel on ssh stdin, never on a command line; python3 on the VPS, standard library only, 3.8 or newer):
    .venv_312/Scripts/python.exe scripts/ops/split_bridge_env.py --dry-run       the plan (names only); writes nothing
    .venv_312/Scripts/python.exe scripts/ops/split_bridge_env.py                 back up, then write bridge.env and .env
    .venv_312/Scripts/python.exe scripts/ops/split_bridge_env.py --check-bridge  step 7: the env names in jarvis-bridge
    ... | .venv_312/Scripts/python.exe scripts/ops/split_bridge_env.py --check-names -   the same check, names you pass
    options: --keep-bridge NAME (repeatable), --host H, --ssh-key K (default JARVIS_SSH_KEY), --vps-dir D

On the VPS, in the folder of docker-compose.yml (default /opt/jarvis-agent/deploy/hostinger):
- .env loses every AGENT_ONLY name: the AI, image, search, weather, email and SIM keys, JARVIS_ENCRYPTION_KEY,
  CONTROLLER_TOKEN and the agent link private key (they live in .env.agent and the PC root .env, K-47 v4). Every
  other line stays byte for byte, so every variable docker-compose.yml interpolates (its ${...} uses: JARVIS_BRAIN,
  the LiveKit pair, the legacy token ...), every COMPOSE_* name and JARVIS_PC_AGENT_NAME stay.
- bridge.env is written from BRIDGE_NAMES, the allow-list of what the bridge reads from its env files: its own
  K-47 names, the Desktop Link public keys and the bridge settings (logging, confirm timing, log store) that so far
  only .env held. Its own lines stay byte for byte; a BRIDGE_NAMES name it lacks is copied from .env (the last
  assignment line, byte for byte: the value the bridge used until now, since compose reads .env and then bridge.env);
  an AGENT_ONLY line in it is dropped. So bridge.env alone can carry the bridge once C-18 drops .env from its
  env_file; until then ui-bridge keeps env_file [.env, bridge.env] (bridge.env wins) and sees exactly the values it saw
  before, minus the agent secrets, once it is recreated.
- It refuses (exit 1, nothing written) when a needed name would be lost: an agent secret the compose file
  interpolates, a required ${X:?} that would be missing or empty, a name the bridge needs to start (the LiveKit pair
  and, while LEGACY_TOKEN_AUTH is on, VORTEX_SECURITY_TOKEN), a bridge.env name on neither list (keep its line with
  --keep-bridge NAME; such a name is never copied from .env), or a value over several lines.
- A file that starts with a UTF-8 byte order mark keeps it, and the name on its first line is read past it
  (env_remote.env_entries), so an agent secret there is removed and the check after the write sees it too.
- Before writing it copies .env and bridge.env into /root/jarvis-backups/env/<UTC>-pre-env-split/ (folder 700, files
  hostinger.env and bridge.env, 600; env_remote.BACKUP_ROOT, never mirrored to the PC). Then bridge.env (it only gains names) and then .env, each through a temp file
  in the same folder and an atomic rename (umask 077, mode 600, owner root). An unchanged file is not rewritten;
  nothing to change at all (already split): no backup, no write.
Run deploy/hostinger/backup_vps.sh pre-env-split first (it records the env layout for rollback.sh, RUNBOOK 'V2b').
Rolling back to a state from before the split then needs rollback.sh --with-env (the env layout check).

--check-bridge runs 'docker inspect' of the jarvis-bridge container on the VPS and cuts its environment to names
there, so no value leaves the VPS; --check-names reads names (one per line, NAME or NAME=..., cut at '=' before
anything is kept) from a file or stdin. Both fail when an AGENT_ONLY name is among them (forbidden_bridge_names).

Exit codes: 0 ok, 1 refused (nothing written), a forbidden name found, or the ssh step failed; 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_secrets = _load_sibling("gen_secrets")
er = _load_sibling("env_remote")
env_set = gen_secrets.env_set
# The shipped helpers under their bare names: the functions below call them so on the VPS (program() defines them
# there) and here alike.
env_entries, env_multiline, env_values, env_drop_lines = er.env_entries, er.env_multiline, er.env_values, \
    er.env_drop_lines
read_text, file_mode, compose_interpolations = er.read_text, er.file_mode, er.compose_interpolations
atomic_write_private, make_backup_dir, backup_private = er.atomic_write_private, er.make_backup_dir, er.backup_private

VPS_DIR = gen_secrets.VPS_DIR
BRIDGE_CONTAINER = "jarvis-bridge"
BACKUP_LABEL = "pre-env-split"

# Never in bridge.env or the compose .env (K-47 v4: they live only in .env.agent and the PC root .env).
AGENT_ONLY = (
    # AI and image providers
    "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "HF_TOKEN", "POLLINATIONS_TOKEN",
    # search and weather
    "GOOGLE_SEARCH_API_KEY", "SEARCH_ENGINE_ID", "TAVILY_API_KEY", "WEATHER_API_KEY", "OPENWEATHER_API_KEY",
    # email (the owner's own account)
    "EMAIL_USER", "EMAIL_APP_PASSWORD",
    # SIM lookup (K-34: never the bridge)
    "SIM_LOOKUP_API_URL", "SIM_LOOKUP_API_KEY",
    # the memory key, the PC controller token and the agent link private key
    "JARVIS_ENCRYPTION_KEY", "CONTROLLER_TOKEN", "DESKTOP_AGENT_SIGNING_KEY",
)
# bridge.env's allow-list: what the bridge reads from its env files (its compose 'environment:' sets the rest).
BRIDGE_NAMES = (
    # K-47 bridge.env: the agent link, the owner login, LiveKit, the legacy token, the event store
    "AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "SESSION_JWT_SECRET", "OWNER_PASSWORD_HASH", "OWNER_TOTP_SECRET",
    "OWNER_RECOVERY_HASHES", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN", "LEGACY_TOKEN_AUTH",
    "BRIDGE_EVENT_RETENTION_DAYS",
    # K-47 v4: the Desktop Link public keys
    "DESKTOP_AGENT_PUBLIC_KEY", "DESKTOP_PC_PUBLIC_KEY",
    # bridge settings that so far may come only from .env: the log store, the confirm timing, logging
    "BRIDGE_LOG_MAX_ROWS", "JARVIS_CONFIRM_TIMEOUT_S", "JARVIS_VOICE_CONFIRM_HIGH", "LOG_LEVEL", "LOG_FILE",
    "LOG_FILE_LEVEL", "LOG_STRUCTURED", "LOG_DIR",
)
# Stay in the compose .env whatever the compose file says, and never go to bridge.env (K-72: the brain switch's
# names; JARVIS_PC_AGENT_NAME is the compose .env's, K-47 v4).
COMPOSE_KEEP = ("JARVIS_BRAIN", "COMPOSE_PROFILES", "JARVIS_PC_AGENT_NAME")
# Known legacy names that stay in .env without a warning (the frontend's old token copy, dead after OP-21).
LEGACY_KEEP = ("VITE_VORTEX_SECURITY_TOKEN",)
# config.validate('bridge') (K-72) refuses to start in production without these (plus the legacy token while on).
BRIDGE_REQUIRED = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
# Without these the agent link v2 and the owner login are off (a warning, not a refusal).
BRIDGE_RECOMMENDED = ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "SESSION_JWT_SECRET")
LEGACY_TOKEN_NAME = "VORTEX_SECURITY_TOKEN"
FALSE_WORDS = ("0", "false", "no", "off")
# A name on no list that holds one of these words gets a warning (names only).
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "HASH")


# --------------------------------------------------------------------------- shipped to the VPS


def split_plan(env_text: str, bridge_text: str | None, compose_text: str, rules: dict[str, Any]) -> dict[str, Any]:
    """What the split does to these texts (names only): the names .env loses and keeps, the names bridge.env gains
    and drops, the compose file's interpolations, every reason to refuse and every warning. rules: agent_only,
    bridge_names, keep_bridge, compose_keep, legacy_keep, bridge_required, bridge_recommended, legacy_token,
    false_words, secret_hints."""
    agent_only = set(rules["agent_only"])
    bridge_names = set(rules["bridge_names"])
    allowed = bridge_names | set(rules["keep_bridge"])  # a --keep-bridge name is kept where it is, never copied
    refuse = []
    warn = []
    for label, text in ((".env", env_text), ("bridge.env", bridge_text or "")):
        for name, line in env_entries(text):
            if name and env_multiline(line):
                refuse.append(label + ": " + name + " spans several lines; edit that file by hand")
    env_vals = env_values(env_text)
    bridge_vals = env_values(bridge_text or "")
    interp = compose_interpolations(compose_text)
    remove = sorted(name for name in env_vals if name in agent_only)
    for name in sorted(agent_only & set(interp)):
        if name in env_vals:
            refuse.append("docker-compose.yml interpolates " + name + ", an agent secret; removing it from .env "
                          "would break the compose file: fix docker-compose.yml first")
        else:
            warn.append("docker-compose.yml interpolates " + name + ", an agent secret; it must never come from "
                        ".env (it is not there)")
    drop = sorted(name for name in bridge_vals if name in agent_only)
    unknown = sorted(name for name in bridge_vals if name not in allowed and name not in agent_only)
    if unknown:
        refuse.append("bridge.env holds names on neither list, which writing it from the allow-list would lose: "
                      + ", ".join(unknown) + " (keep one with --keep-bridge NAME)")
    add = sorted(name for name in env_vals if name in bridge_names and name not in bridge_vals and env_vals[name])
    env_after = dict((name, value) for name, value in env_vals.items() if name not in agent_only)
    bridge_after = dict((name, value) for name, value in bridge_vals.items() if name not in agent_only)
    for name in add:
        bridge_after[name] = env_vals[name]
    visible = dict(env_after)
    visible.update(bridge_after)  # compose reads .env, then bridge.env, whose values win
    for name, kind in sorted(interp.items()):
        if kind == "required" and not env_after.get(name):
            refuse.append("docker-compose.yml needs a non-empty " + name + " in .env (${" + name + ":?}) and .env "
                          "has none")
        elif kind == "plain" and name not in env_after:
            warn.append("docker-compose.yml interpolates " + name + " without a default and .env has none (compose "
                        "substitutes an empty value, as before)")
    legacy_on = (visible.get("LEGACY_TOKEN_AUTH") or "").strip().lower() not in rules["false_words"]
    needed = list(rules["bridge_required"]) + ([rules["legacy_token"]] if legacy_on else [])
    missing = [name for name in needed if not visible.get(name)]
    if missing:
        refuse.append("the bridge needs " + ", ".join(missing) + " to start, and neither .env nor bridge.env has it")
    lacking = [name for name in rules["bridge_recommended"] if not bridge_after.get(name)]
    if lacking:
        warn.append("bridge.env has no " + ", ".join(lacking) + " (gen_secrets.py --vps adds them; without them the "
                    "agent link v2 and the owner login stay off)")
    known = agent_only | allowed | set(rules["compose_keep"]) | set(interp) | set(rules["legacy_keep"])
    suspicious = sorted(name for name in env_after if name not in known and not name.startswith("COMPOSE_")
                        and any(hint in name.upper() for hint in rules["secret_hints"]))
    if suspicious:
        warn.append(".env keeps " + ", ".join(suspicious) + ": they look like secrets and are on no list (ui-bridge "
                    "still reads .env until C-18); check them")
    return {"env_names": sorted(env_vals), "remove": remove, "keep": sorted(env_after), "interpolation": interp,
            "bridge_exists": bridge_text is not None, "bridge_names": sorted(bridge_vals), "add": add, "drop": drop,
            "unknown": unknown, "bridge_after": sorted(bridge_after), "refuse": refuse, "warn": warn,
            "changed": bool(remove or add or drop)}


def split_texts(env_text: str, bridge_text: str | None, plan: dict[str, Any]) -> tuple[str, str]:
    """The new .env and bridge.env texts for a plan: the agent-only lines out of both, the added names' last .env
    lines appended to bridge.env byte for byte (under one comment line). Every other line is unchanged."""
    agent_only = set(plan["remove"]) | set(plan["drop"])
    new_env = env_drop_lines(env_text, agent_only)
    new_bridge = env_drop_lines(bridge_text or "", agent_only)
    if plan["add"]:
        last = {}
        for name, line in env_entries(env_text):
            if name in plan["add"]:
                last[name] = line
        newline = "\r\n" if "\r\n" in (bridge_text or env_text) else "\n"
        if new_bridge and new_bridge != "\ufeff" and not new_bridge.endswith(("\n", "\r")):
            new_bridge += newline
        new_bridge += "# copied from .env by scripts/ops/split_bridge_env.py (C-127)" + newline
        for name in plan["add"]:
            line = last[name]
            new_bridge += line if line.endswith(("\n", "\r")) else line + newline
    return new_env, new_bridge


def remote_split(args: dict[str, Any]) -> int:
    """Runs on the VPS: plan, then (unless dry_run or refused) back up and write. Prints JSON lines with names only;
    returns 0 (done, dry run or nothing to change) or 1 (refused or failed)."""
    folder = args["dir"]
    env_path, bridge_path = os.path.join(folder, ".env"), os.path.join(folder, "bridge.env")
    env_text = read_text(env_path)
    compose_text = read_text(os.path.join(folder, "docker-compose.yml"))
    if env_text is None or compose_text is None:
        absent = [label for label, text in ((".env", env_text), ("docker-compose.yml", compose_text)) if text is None]
        print(json.dumps({"result": "refused", "refuse": [", ".join(absent) + " not found in " + folder]}))
        return 1
    bridge_text = read_text(bridge_path)
    plan = split_plan(env_text, bridge_text, compose_text, args["rules"])
    print(json.dumps({"plan": plan}))
    modes = {".env": file_mode(env_path), "bridge.env": file_mode(bridge_path)}
    if plan["refuse"]:
        print(json.dumps({"result": "refused", "refuse": plan["refuse"], "modes": modes}))
        return 1
    if not plan["changed"]:
        print(json.dumps({"result": "unchanged", "modes": modes}))
        return 0
    if args["dry_run"]:
        print(json.dumps({"result": "dry_run", "modes": modes}))
        return 0
    new_env, new_bridge = split_texts(env_text, bridge_text, plan)
    backup = make_backup_dir(args["backup_root"], args["stamp"], args["label"])
    saved = [name for src, name in ((env_path, "hostinger.env"), (bridge_path, "bridge.env"))
             if backup_private(src, backup, name)]
    print(json.dumps({"backup": backup, "files": saved}))
    written = []
    if new_bridge != (bridge_text or ""):
        atomic_write_private(bridge_path, new_bridge.encode("utf-8"))  # first: it only gains names
        written.append("bridge.env")
    if new_env != env_text:
        atomic_write_private(env_path, new_env.encode("utf-8"))
        written.append(".env")
    env_now, bridge_now = env_values(read_text(env_path) or ""), env_values(read_text(bridge_path) or "")
    agent_only = set(args["rules"]["agent_only"])
    left = sorted((set(env_now) | set(bridge_now)) & agent_only)
    lost = sorted((set(plan["bridge_after"]) - set(bridge_now)) | (set(plan["keep"]) - set(env_now)))
    modes = {".env": file_mode(env_path), "bridge.env": file_mode(bridge_path)}
    if left or lost:
        print(json.dumps({"result": "failed", "left": left, "lost": lost, "written": written, "modes": modes}))
        return 1
    print(json.dumps({"result": "written", "written": written, "modes": modes}))
    return 0


def remote_env_names(args: dict[str, Any]) -> int:
    """Runs on the VPS: the environment NAMES of one container ('docker inspect' of .Config.Env, cut to names
    here, so no value leaves the VPS). Prints one JSON line; 0, or 1 when docker failed."""
    try:
        proc = subprocess.run(list(args["docker"]) + ["inspect", "--format", "{{json .Config.Env}}",
                                                      args["container"]],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": "docker did not run (" + type(exc).__name__ + ")"}))
        return 1
    if proc.returncode != 0:
        print(json.dumps({"error": "docker inspect " + args["container"] + " exited " + str(proc.returncode)}))
        return 1
    try:
        items = json.loads(proc.stdout or "null") or []
    except ValueError:
        print(json.dumps({"error": "docker inspect gave no JSON list"}))
        return 1
    names = sorted(set(str(item).split("=", 1)[0] for item in items if str(item)))
    print(json.dumps({"container": args["container"], "names": names}))
    return 0


def split_program() -> str:
    return er.program(er.env_entries, er.env_value, er.env_multiline, er.env_values, er.env_drop_lines,
                      er.read_text, er.file_mode, er.atomic_write_private, er.make_backup_dir, er.backup_private,
                      er.compose_interpolations, split_plan, split_texts, remote_split, entry="remote_split")


def names_program() -> str:
    return er.program(remote_env_names, entry="remote_env_names")


# --------------------------------------------------------------------------- PC side


def name_rules(keep_bridge: list[str] | None = None) -> dict[str, Any]:
    """The name lists the VPS program uses (one source: this module)."""
    return {"agent_only": list(AGENT_ONLY), "bridge_names": list(BRIDGE_NAMES), "keep_bridge": list(keep_bridge or []),
            "compose_keep": list(COMPOSE_KEEP), "legacy_keep": list(LEGACY_KEEP),
            "bridge_required": list(BRIDGE_REQUIRED), "bridge_recommended": list(BRIDGE_RECOMMENDED),
            "legacy_token": LEGACY_TOKEN_NAME, "false_words": list(FALSE_WORDS),
            "secret_hints": list(SECRET_HINTS)}


def forbidden_bridge_names(names: Iterable[str]) -> list[str]:
    """Step 7's check, the function the RUNBOOK calls (through --check-bridge or --check-names): the AGENT_ONLY names
    among a container's environment names. None may be in jarvis-bridge: no AI, image, search, weather, email, SIM or
    encryption key, no controller token, no agent link private key."""
    return sorted(set(names) & set(AGENT_ONLY))


def names_from_text(text: str) -> list[str]:
    """Env names from text, one per line: 'NAME' or 'NAME=...' (cut at the first '=' before anything is kept, so a
    value never travels further), blank lines and '#' comments skipped. Raises ValueError for a line that holds no
    env name; the message gives its line number only."""
    names = []
    for number, raw in enumerate(text.splitlines(), 1):
        name = raw.split("=", 1)[0].strip()
        if not name or name.startswith("#"):
            continue
        if not env_set.KEY_RE.fullmatch(name):
            raise ValueError(f"line {number} holds no env name")
        names.append(name)
    return names


def report_split(rows: list[dict[str, Any]], vps_dir: str, dry_run: bool) -> tuple[list[str], int]:
    """Human lines (names only) from the VPS JSON lines, and the exit code."""
    lines: list[str] = []
    plan = next((row["plan"] for row in rows if "plan" in row), None)
    result = next((row for row in rows if "result" in row), {})
    if plan:
        interp = plan["interpolation"]
        lines.append(f".env: {len(plan['env_names'])} names; removes {len(plan['remove'])} (agent secrets): "
                     f"{er.names_line(plan['remove'])}")
        lines.append(f".env: keeps {len(plan['keep'])}: {er.names_line(plan['keep'])}")
        lines.append(f"docker-compose.yml interpolates {er.names_line(interp)} (required: "
                     f"{er.names_line(n for n, k in interp.items() if k == 'required')})")
        state = f"{len(plan['bridge_names'])} names" if plan.get("bridge_exists", True) else "absent (created)"
        lines.append(f"bridge.env: {state}; adds from .env: {er.names_line(plan['add'])}; "
                     f"drops: {er.names_line(plan['drop'])}")
        lines.append(f"bridge.env afterwards: {len(plan['bridge_after'])} names: {er.names_line(plan['bridge_after'])}")
        lines.extend(f"warning: {text}" for text in plan["warn"])
    for row in rows:
        if "backup" in row:
            lines.append(f"backup: {row['backup']} ({er.names_line(row.get('files') or [])}; mode 600)")
    outcome = result.get("result")
    modes = result.get("modes") or {}
    if outcome == "refused":
        lines.extend(f"REFUSED: {text}" for text in result.get("refuse") or [])
        lines.append("nothing was written")
        return lines, 1
    if outcome == "dry_run":
        lines.append(f"dry run: nothing written; a real run backs up to {er.BACKUP_ROOT}/<UTC>-{BACKUP_LABEL}/, then "
                     f"writes {vps_dir}/bridge.env and {vps_dir}/.env (mode 600 root)")
        return lines, 0
    if outcome == "unchanged":
        lines.append("nothing to change: .env holds no agent secret and bridge.env has every bridge name "
                     f"(.env {er.mode_text(modes.get('.env'))}, bridge.env {er.mode_text(modes.get('bridge.env'))})")
        return lines, 0
    if outcome == "written":
        lines.append(f"written: {er.names_line(result.get('written') or [])} (.env {er.mode_text(modes.get('.env'))}, "
                     f"bridge.env {er.mode_text(modes.get('bridge.env'))})")
        lines.append("next: recreate ui-bridge so it reads them (RUNBOOK 'V2b'), then 'split_bridge_env.py "
                     "--check-bridge'")
        return lines, 0
    if outcome == "failed":
        if result.get("error"):
            lines.append(f"FAILED on the VPS: {result['error']}; check the files against the backup above")
        else:
            lines.append(f"FAILED after writing: agent secret names left: {er.names_line(result.get('left') or [])}; "
                         f"names lost: {er.names_line(result.get('lost') or [])}; restore from the backup above")
        return lines, 1
    lines.append("no answer from the VPS program" + (" (dry run)" if dry_run else ""))
    return lines, 1


def report_names(names: list[str], label: str) -> int:
    """Prints the step 7 verdict for one name list (names only); 0 when no forbidden name is among them."""
    found = forbidden_bridge_names(names)
    if found:
        print(f"{label}: {len(names)} env names; AGENT SECRET NAMES PRESENT: {', '.join(found)}")
        return 1
    print(f"{label}: {len(names)} env names; no AI, search, weather, email, SIM or encryption key, no controller "
          "token, no agent link key (NO_AGENT_SECRETS_IN_BRIDGE)")
    return 0


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="split_bridge_env.py",
                     description="Split the VPS env files (C-127 V2b): bridge.env from an allow-list, the agent "
                                 "secrets out of the compose .env. Names only, never a value.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the plan (names only); write nothing")
    mode.add_argument("--check-bridge", action="store_true",
                      help="names only: fail when an agent secret name is in the jarvis-bridge environment")
    mode.add_argument("--check-names", metavar="FILE",
                      help="the same check on env names from FILE ('-' for stdin; NAME or NAME=..., cut at '=')")
    parser.add_argument("--keep-bridge", action="append", default=[], metavar="NAME",
                        help="keep the bridge.env line of NAME, a name on neither list (never copied from .env; "
                             "repeatable)")
    parser.add_argument("--host", default=gen_secrets.VPS_HOST, help=f"ssh target (default {gen_secrets.VPS_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=None, help="ssh identity file (default JARVIS_SSH_KEY)")
    parser.add_argument("--vps-dir", default=VPS_DIR, help=f"folder of the compose file (default {VPS_DIR})")
    args = parser.parse_args(argv)
    for name in args.keep_bridge:
        if not env_set.KEY_RE.fullmatch(name):
            parser.error("--keep-bridge: not a valid NAME")
        if name in AGENT_ONLY:
            parser.error(f"--keep-bridge: {name} is an agent secret and never goes to bridge.env")
    if (args.check_bridge or args.check_names) and args.keep_bridge:
        parser.error("--keep-bridge goes with the split, not with a check")
    args.ssh_key = args.ssh_key or er.default_ssh_key()
    args.vps_dir = er.vps_path(args.vps_dir).rstrip("/") or "/"
    return args


def check_bridge(args: argparse.Namespace, runner: Any) -> int:
    code, out, err = runner(names_program(), {"docker": ["docker"], "container": BRIDGE_CONTAINER}, args.host,
                            args.ssh_key)
    rows = er.json_rows(out)
    row = rows[-1] if rows else {}
    if code or "names" not in row:
        detail = row.get("error") or er.failure_line(err)
        print(f"split_bridge_env: the check did not run (exit {code}): {str(detail)[:200]}", file=sys.stderr)
        return 1
    return report_names([str(name) for name in row["names"]], BRIDGE_CONTAINER)


def check_names(source: str, stdin: TextIO) -> int:
    try:
        if source == "-":
            text = stdin.read()
        else:
            text = Path(source).read_text(encoding="utf-8")
        names = names_from_text(text)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"split_bridge_env: cannot read the names ({type(exc).__name__})", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"split_bridge_env: {exc}: pass env names, one per line", file=sys.stderr)
        return 2
    if not names:
        print("split_bridge_env: no env names given", file=sys.stderr)
        return 2
    return report_names(names, "the names given")


def main(argv: list[str] | None = None, runner: Any = None, stdin: TextIO | None = None) -> int:
    args = parse_args(argv)
    if args.check_names:
        return check_names(args.check_names, stdin or sys.stdin)
    runner = runner or gen_secrets.run_ssh
    if args.check_bridge:
        return check_bridge(args, runner)
    remote = {"dir": args.vps_dir, "dry_run": args.dry_run, "rules": name_rules(args.keep_bridge),
              "backup_root": er.BACKUP_ROOT, "stamp": er.utc_stamp(), "label": BACKUP_LABEL}
    code, out, err = runner(split_program(), remote, args.host, args.ssh_key)
    rows = er.json_rows(out)
    lines, result = report_split(rows, args.vps_dir, args.dry_run)
    print(f"split_bridge_env{' (dry run)' if args.dry_run else ''}: {args.vps_dir}")
    for line in lines:
        print(line)
    if not rows:
        print(f"split_bridge_env: the VPS step failed (exit {code}): {er.failure_line(err)}", file=sys.stderr)
        return 1
    return result if result else (1 if code else 0)


if __name__ == "__main__":
    sys.exit(main())
