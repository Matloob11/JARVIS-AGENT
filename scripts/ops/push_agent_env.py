"""Fill the VPS brain's .env.agent from this PC and make the agent's Desktop Link key on the VPS (plan task C-127
step 3; contracts K-47 v4/v36, K-70 and K-75). Prints names, public keys and verdicts, never a secret value.

Usage (from the repo root on the PC, with the project venv; each VPS step is one ssh run whose program and arguments
travel on ssh stdin, never on a command line; python3 on the VPS, standard library only, 3.8 or newer):
    .venv_312/Scripts/python.exe scripts/ops/push_agent_env.py --dry-run   names only: what a push would change
    .venv_312/Scripts/python.exe scripts/ops/push_agent_env.py             push, then compare (HMAC digests)
    .venv_312/Scripts/python.exe scripts/ops/push_agent_env.py --keys      the agent's link key, made on the VPS
    .venv_312/Scripts/python.exe scripts/ops/push_agent_env.py --check     compare only: nothing written, no value sent
    options: --env-file PATH (default: the root .env), --host H, --ssh-key K (default JARVIS_SSH_KEY), --vps-dir D,
             --rotate (with --keys: replace an existing agent key), --dry-run (also with --keys)

push      Copies PC_NAMES from the PC root .env (K-47's single source) to <vps-dir>/.env.agent (default
          /opt/jarvis-agent/deploy/hostinger, the RUNBOOK's path): the K-47 v4 .env.agent names this PC holds,
          AGENT_TOKEN and BRIDGE_HMAC_SECRET (K-47 v36: the same values as bridge.env) and the K-35 switches
          (JARVIS_DOC_DIRS stays on the PC, its folders are the PC's). LIVEKIT_API_KEY and LIVEKIT_API_SECRET are
          copied on the VPS from the compose .env next to it (the VPS brain uses the main LiveKit pair; the PC
          fallback may hold its own, step 6), so those values never travel. Every assignment of a pushed name is
          rewritten in place and a missing one appended; every other line (the link keys of --keys) stays byte for
          byte; a name this PC does not have is left as it is on the VPS. The old file is first copied to
          /root/jarvis-backups/env/<UTC>-pre-agent-env/env.agent (mode 600); the new one is written through a temp file
          in the same folder and a rename (umask 077, mode 600, owner root). It refuses, before anything is sent,
          while GOOGLE_API_KEY, JARVIS_ENCRYPTION_KEY, AGENT_TOKEN or BRIDGE_HMAC_SECRET is missing on this PC, a value
          cannot be written to a one-line env file unchanged, or a pushed name is DPAPI-wrapped on this PC (vps mode,
          run/fallback.env.dpapi, step 5: push before 'agent_ctl brain vps'). The VPS step refuses (nothing written)
          while AGENT_TOKEN or BRIDGE_HMAC_SECRET is missing from bridge.env or differs from it (compared there).
--keys    Runs scripts/ops/gen_desktop_keys.py --role agent in a one-off jarvis-agent:hostinger container with the
          FOLDER of .env.agent mounted (the C-115 As-built form, plus --network none: no network is needed):
              docker run --rm --network none --user 0 -v <vps-dir>:/envdir jarvis-agent:hostinger \\
                  python scripts/ops/gen_desktop_keys.py --role agent --env-file /envdir/.env.agent [--pc-public HEX]
          DESKTOP_PC_PUBLIC_KEY from the PC root .env goes along as --pc-public when it is set. After a backup of
          .env.agent and bridge.env (/root/jarvis-backups/env/<UTC>-pre-agent-keys), the .env.agent.tmp the tool
          wrote on the host is moved over .env.agent (mode 600, root); DESKTOP_AGENT_PUBLIC_KEY (and
          DESKTOP_PC_PUBLIC_KEY) go into bridge.env and DESKTOP_AGENT_PUBLIC_KEY into the PC root .env ('agent_ctl
          node update' pins it from there). An existing agent key is kept unless --rotate. Only public keys are
          printed.
--check   Both sides compute HMAC-SHA256(nonce, name + NUL + value) with a fresh random nonce and only the digests
          travel back: each pushed name equals this PC, AGENT_TOKEN and BRIDGE_HMAC_SECRET equal bridge.env, the
          LiveKit pair equals the compose .env, the public keys equal bridge.env. A push ends with the same check.

The readers pick the values up when they start: the agent containers (C-131) and the bridge (bridge.env; recreate
it, deploy/hostinger/RUNBOOK.md 'Push the agent env').
Exit codes: 0 ok, 1 refused on the VPS, a comparison failed or the ssh step failed, 2 usage or PC-side refusal.
"""
from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import os
import re
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


rotate_env_secret = _load_sibling("rotate_env_secret")
gen_secrets = rotate_env_secret.gen_secrets
env_set = gen_secrets.env_set
EnvError = env_set.EnvError
er = _load_sibling("env_remote")
fallback_env = _load_sibling("fallback_env")
# The shipped helpers under their bare names (program() defines them on the VPS).
env_values, env_set_lines, render_env_value = er.env_values, er.env_set_lines, er.render_env_value
read_text, file_mode, value_digest = er.read_text, er.file_mode, er.value_digest
atomic_write_private, make_backup_dir, backup_private = er.atomic_write_private, er.make_backup_dir, er.backup_private

ROOT = Path(__file__).resolve().parents[2]
PC_ENV = ROOT / ".env"
VPS_DIR = gen_secrets.VPS_DIR
AGENT_IMAGE = "jarvis-agent:hostinger"

# Copied from the PC root .env: K-47 v4's .env.agent names that come from this PC, the notify credentials (K-47 v36)
# and the K-35 switches. JARVIS_DOC_DIRS is left out: its folders are this PC's (the VPS uses its defaults).
PC_NAMES = (
    "GOOGLE_API_KEY", "WEATHER_API_KEY", "OPENWEATHER_API_KEY", "GOOGLE_SEARCH_API_KEY", "SEARCH_ENGINE_ID",
    "TAVILY_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "EMAIL_USER", "EMAIL_APP_PASSWORD",
    "SIM_LOOKUP_API_URL", "SIM_LOOKUP_API_KEY", "POLLINATIONS_TOKEN", "JARVIS_ENCRYPTION_KEY",
    "BACKUP_RECIPIENT_PUBLIC_KEY", "AGENT_TOKEN", "BRIDGE_HMAC_SECRET",
    "JARVIS_TEXT_MODEL", "JARVIS_VISION_MODEL", "JARVIS_GROQ_TEXT_MODEL", "JARVIS_GROQ_VISION_MODEL",
    "JARVIS_OPENROUTER_MODEL", "JARVIS_IMAGE_MODEL", "JARVIS_IMAGE_PROVIDER", "TAVILY_SEARCH_DEPTH", "SMTP_HOST",
    "SMTP_PORT", "JARVIS_RAG_WARM_DELAY_S", "JARVIS_FRAME_MAX_AGE_S", "JARVIS_SELF_REPAIR_APPLY", "SIM_SOURCE",
    "SIM_LOOKUP_DAILY_LIMIT", "SIM_LOOKUP_SHOW_ADDRESS",
)
# Without these the VPS agent cannot start (K-72 agent+remote) or its notifies are dropped (K-47 v36).
PC_REQUIRED = ("GOOGLE_API_KEY", "JARVIS_ENCRYPTION_KEY", "AGENT_TOKEN", "BRIDGE_HMAC_SECRET")
# Copied on the VPS from the compose .env: the main LiveKit pair.
VPS_COPIED = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
# The same values as bridge.env (K-47 v36).
SAME_AS_BRIDGE = ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET")
AGENT_SIGNING, AGENT_PUBLIC = "DESKTOP_AGENT_SIGNING_KEY", "DESKTOP_AGENT_PUBLIC_KEY"
PC_PUBLIC = "DESKTOP_PC_PUBLIC_KEY"
PUBLIC_RE = re.compile(r"[0-9a-f]{64}")


# --------------------------------------------------------------------------- shipped to the VPS


def remote_push(args: dict[str, Any]) -> int:
    """Runs on the VPS. mode 'write' (values given) rewrites .env.agent; 'dry_run' and 'check' write nothing and get
    no value. Every mode prints HMAC digests of the requested names before and after (keyed 'file:name', None for
    an absent or empty value) and a result line; never a value. 0 ok, 1 refused."""
    folder = args["dir"]
    paths = {".env.agent": os.path.join(folder, ".env.agent"), ".env": os.path.join(folder, ".env"),
             "bridge.env": os.path.join(folder, "bridge.env")}

    def digests() -> dict[str, Any]:
        out = {}
        texts = dict((label, env_values(read_text(path) or "")) for label, path in paths.items())
        for label, name in args["digest"]:
            value = texts[label].get(name)
            out[label + ":" + name] = value_digest(args["nonce"], name, value) if value else None
        return out

    before = digests()
    agent_text = read_text(paths[".env.agent"])
    row = {"before": before, "existed": agent_text is not None}
    if args["mode"] != "write":
        row.update(after=before, result=args["mode"], mode=file_mode(paths[".env.agent"]))
        print(json.dumps(row))
        return 0
    values = dict(args["values"])
    compose_env = env_values(read_text(paths[".env"]) or "")
    refuse = [name + " is not set in " + paths[".env"] for name in args["copy"] if not compose_env.get(name)]
    bridge_env = env_values(read_text(paths["bridge.env"]) or "")
    for name in args.get("same_as_bridge", []):  # K-47 v36: compared here, so no value leaves the VPS
        if name not in values:
            continue
        if not bridge_env.get(name):
            refuse.append(name + " is not set in " + paths["bridge.env"] + " (gen_secrets.py --vps writes it)")
        elif not hmac.compare_digest(bridge_env[name].encode("utf-8"), values[name].encode("utf-8")):
            refuse.append(name + " differs from bridge.env, so the agent's notifies would be dropped: make them equal "
                                 "first (rotate_env_secret.py --name " + name + " with --vps-file bridge.env)")
    values.update(dict((name, compose_env[name]) for name in args["copy"] if compose_env.get(name)))
    refuse += [name + ": its value cannot be written to a one-line env file unchanged" for name, value in
               sorted(values.items()) if render_env_value(value) is None]
    if refuse:
        row.update(after=before, result="refused", refuse=refuse, mode=file_mode(paths[".env.agent"]))
        print(json.dumps(row))
        return 1
    new_text = env_set_lines(agent_text or "", values)
    if agent_text is not None and new_text == agent_text:
        row.update(after=before, result="unchanged", mode=file_mode(paths[".env.agent"]))
        print(json.dumps(row))
        return 0
    if agent_text is not None:
        row["backup"] = make_backup_dir(args["backup_root"], args["stamp"], "pre-agent-env")
        backup_private(paths[".env.agent"], row["backup"], "env.agent")
    atomic_write_private(paths[".env.agent"], new_text.encode("utf-8"))
    row.update(after=digests(), result="written", mode=file_mode(paths[".env.agent"]))
    print(json.dumps(row))
    return 0


def remote_keys(args: dict[str, Any]) -> int:
    """Runs on the VPS: the agent's link key from gen_desktop_keys.py in a one-off agent container, its .env.agent.tmp
    moved over .env.agent, the public keys into bridge.env. Prints one JSON line (public keys, names, modes); 0 ok,
    1 refused or failed."""
    folder = args["dir"]
    agent_path, bridge_path = os.path.join(folder, ".env.agent"), os.path.join(folder, "bridge.env")
    tmp_path = agent_path + ".tmp"
    row = {}

    def finish(result: str, code: int, **extra: Any) -> int:
        row.update(extra)
        row.update(result=result, modes={".env.agent": file_mode(agent_path), "bridge.env": file_mode(bridge_path)})
        print(json.dumps(row))
        return code

    if read_text(agent_path) is None:
        return finish("refused", 1, error=agent_path + " does not exist: run push_agent_env.py (push) first")
    if os.path.exists(tmp_path):
        return finish("refused", 1, error=tmp_path + " already exists (an unfinished run?): check it, then move it "
                                                     "over .env.agent or remove it")
    try:
        probe = subprocess.run(list(args["docker"]) + ["image", "inspect", "-f", "{{.Id}}", args["image"]],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60,
                               check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return finish("refused", 1, error="docker did not run (" + type(exc).__name__ + ")")
    if probe.returncode != 0:
        return finish("refused", 1, error="no image " + args["image"] + ": build it first (RUNBOOK 'Agent image')")
    command = list(args["docker"]) + ["run", "--rm", "--network", "none", "--user", "0", "-v", folder + ":/envdir",
                                      args["image"], "python", "scripts/ops/gen_desktop_keys.py", "--role", "agent",
                                      "--env-file", "/envdir/.env.agent"]
    if args.get("pc_public"):
        command += ["--pc-public", args["pc_public"]]
    if args.get("rotate"):
        command.append("--rotate")
    row["command"] = command  # public keys and paths only
    if args["dry_run"]:
        return finish("dry_run", 0)
    row["backup"] = make_backup_dir(args["backup_root"], args["stamp"], "pre-agent-keys")
    backup_private(agent_path, row["backup"], "env.agent")
    backup_private(bridge_path, row["backup"], "bridge.env")
    try:
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return finish("failed", 1, error="docker run did not finish (" + type(exc).__name__ + ")")
    public, status = "", ""
    for line in (proc.stdout or "").splitlines():
        match = re.fullmatch(r"DESKTOP_AGENT_PUBLIC_KEY=([0-9a-f]{64})", line.strip())
        if match:
            public = match.group(1)
        elif line.startswith(("WROTE=", "KEPT=")):
            status = line.split("=", 1)[0]
    if proc.returncode != 0 or not public or not status:
        errors = [line for line in (proc.stderr or "").splitlines() if line.startswith("ERROR:")]  # paths only
        detail = errors[-1][:300] if errors else "the key tool exited " + str(proc.returncode) + " without a public key"
        return finish("failed", 1, error=detail)
    if status == "WROTE":
        if not os.path.exists(tmp_path):
            return finish("failed", 1, error="the container wrote no " + tmp_path + " on the host (is the folder "
                                             "mounted?)")
        os.chmod(tmp_path, 0o600)
        if getattr(os, "geteuid", lambda: -1)() == 0:
            os.chown(tmp_path, 0, 0)
        os.replace(tmp_path, agent_path)
    agent_now = env_values(read_text(agent_path) or "")
    if not agent_now.get("DESKTOP_AGENT_SIGNING_KEY") or agent_now.get("DESKTOP_AGENT_PUBLIC_KEY") != public:
        return finish("failed", 1, error=agent_path + " does not hold the key whose public key was printed")
    shared = {"DESKTOP_AGENT_PUBLIC_KEY": public}
    if args.get("pc_public"):
        shared["DESKTOP_PC_PUBLIC_KEY"] = args["pc_public"]
    bridge_text = read_text(bridge_path) or ""
    new_bridge = env_set_lines(bridge_text, shared)
    if new_bridge != bridge_text:
        atomic_write_private(bridge_path, new_bridge.encode("utf-8"))
    return finish("done", 0, public=shared, env_agent=status.lower(),
                  bridge_env="changed" if new_bridge != bridge_text else "unchanged")


def push_program() -> str:
    return er.program(er.env_entries, er.env_value, er.env_values, er.render_env_value, er.env_set_lines,
                      er.read_text, er.file_mode, er.value_digest, er.atomic_write_private, er.make_backup_dir,
                      er.backup_private, remote_push, entry="remote_push")


def keys_program() -> str:
    return er.program(er.env_entries, er.env_value, er.env_values, er.render_env_value, er.env_set_lines,
                      er.read_text, er.file_mode, er.atomic_write_private, er.make_backup_dir, er.backup_private,
                      remote_keys, entry="remote_keys")


# --------------------------------------------------------------------------- PC side


def pc_values(path: Path) -> dict[str, str]:
    """The non-empty PC_NAMES and public keys of the PC root .env (python-dotenv's reading). Never printed."""
    source = env_set.EnvFile(path)
    names = (*PC_NAMES, AGENT_PUBLIC, PC_PUBLIC)
    return {name: source.value(name) for name in names if source.value(name)}


def wrapped_refusal(values: dict[str, str], run_dir: Path | None = None) -> str:
    """Why a push must wait while pushed names are DPAPI-wrapped on this PC (vps mode, step 5), else ''."""
    wrapped = fallback_env.wrapped_names(fallback_env.store_path(run_dir))
    missing = sorted(name for name in wrapped if name in PC_NAMES and name not in values)
    if not missing:
        return ""
    return (f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} DPAPI-wrapped on this PC "
            f"(vps mode, run/{fallback_env.STORE_NAME}); push before 'agent_ctl brain vps', or change one value with "
            "rotate_env_secret.py --vps-file <vps-dir>/.env.agent and fallback_env.py --set")


def digest_requests(names: list[str]) -> list[list[str]]:
    """The (file, name) pairs the VPS digests: every pushed name, the LiveKit pair on both sides, the notify
    credentials and the public keys in bridge.env, the link keys in .env.agent."""
    pairs = [[".env.agent", name] for name in (*names, *VPS_COPIED, AGENT_SIGNING, AGENT_PUBLIC, PC_PUBLIC)]
    pairs += [[".env", name] for name in VPS_COPIED]
    pairs += [["bridge.env", name] for name in (*SAME_AS_BRIDGE, AGENT_PUBLIC, PC_PUBLIC)]
    unique: list[list[str]] = []
    for pair in pairs:
        if pair not in unique:
            unique.append(pair)
    return unique


def compare(after: dict[str, Any], pc: dict[str, str], nonce: str, pushed: list[str]) -> tuple[list[str], bool]:
    """Verdict lines (names only) and whether every comparison holds."""
    lines: list[str] = []
    ok = True

    def vps(label: str, name: str) -> Any:
        return after.get(f"{label}:{name}")

    for name in pushed:
        if er.digests_equal(vps(".env.agent", name), er.value_digest(nonce, name, pc[name])):
            verdict = "equal to this PC"
        else:
            verdict, ok = ("ABSENT on the VPS" if vps(".env.agent", name) is None else "DIFFERS from this PC"), False
        lines.append(f"{name}: {verdict}")
    for name in VPS_COPIED:
        if er.digests_equal(vps(".env.agent", name), vps(".env", name)):
            lines.append(f"{name}: equal to the compose .env")
        else:
            ok = False
            lines.append(f"{name}: {'ABSENT in .env.agent' if vps('.env.agent', name) is None else 'DIFFERS from'} "
                         "the compose .env")
    for name in SAME_AS_BRIDGE:
        if er.digests_equal(vps(".env.agent", name), vps("bridge.env", name)):
            lines.append(f"{name}: equal to bridge.env")
        else:
            ok = False
            lines.append(f"{name}: NOT equal to bridge.env (the agent's notifies would be dropped)")
    for name in (AGENT_PUBLIC, PC_PUBLIC):
        agent_side, bridge_side = vps(".env.agent", name), vps("bridge.env", name)
        if agent_side is None and bridge_side is None:
            lines.append(f"{name}: not set yet (push_agent_env.py --keys)")
            continue
        same = er.digests_equal(agent_side, bridge_side)
        ok = ok and same
        tail = ""
        if name in pc:
            mine = er.digests_equal(agent_side, er.value_digest(nonce, name, pc[name]))
            ok = ok and mine
            tail = ", equal to this PC" if mine else ", DIFFERS from this PC"
        lines.append(f"{name}: {'equal to bridge.env' if same else 'NOT equal to bridge.env'}{tail}")
    lines.append(f"{AGENT_SIGNING}: {'set' if vps('.env.agent', AGENT_SIGNING) else 'not set yet (--keys)'}")
    return lines, ok


def push(args: argparse.Namespace, runner: Any, mode: str) -> int:
    """push ('write'), --dry-run ('dry_run') or --check ('check')."""
    try:
        pc = pc_values(args.env_file)
    except EnvError as exc:
        print(f"push_agent_env: {exc}", file=sys.stderr)
        return 2
    pushed = [name for name in PC_NAMES if name in pc]
    if mode != "check":
        why = wrapped_refusal(pc)
        if why:
            print(f"push_agent_env: refused: {why}", file=sys.stderr)
            return 2
        missing = [name for name in PC_REQUIRED if name not in pc]
        if missing:
            print(f"push_agent_env: refused: {', '.join(missing)} missing in {args.env_file.name} on this PC; "
                  "nothing was sent", file=sys.stderr)
            return 2
        bad = [name for name in pushed if render_env_value(pc[name]) is None]
        if bad:
            print(f"push_agent_env: refused: {', '.join(bad)}: a value with a single quote, a line break or a NUL "
                  "cannot be written to .env.agent unchanged; nothing was sent", file=sys.stderr)
            return 2
    nonce = er.new_nonce()
    remote = {"dir": args.vps_dir, "mode": mode, "nonce": nonce, "digest": digest_requests(pushed),
              "values": {name: pc[name] for name in pushed} if mode == "write" else {},
              "copy": list(VPS_COPIED), "same_as_bridge": list(SAME_AS_BRIDGE), "backup_root": er.BACKUP_ROOT,
              "stamp": er.utc_stamp()}
    code, out, err = runner(push_program(), remote, args.host, args.ssh_key)
    rows = er.json_rows(out)
    row = rows[-1] if rows else {}
    title = {"write": "push", "dry_run": "push (dry run)", "check": "check"}[mode]
    print(f"push_agent_env {title}: {args.vps_dir}/.env.agent ({len(pushed)} names from this PC, "
          f"{len(VPS_COPIED)} from the compose .env)")
    skipped = [name for name in PC_NAMES if name not in pc]
    if skipped:
        print(f"not on this PC (left as they are on the VPS): {er.names_line(skipped)}")
    if not row:
        print(f"push_agent_env: the VPS step failed (exit {code}): {er.failure_line(err)}", file=sys.stderr)
        return 1
    if row.get("result") in ("refused", "failed"):
        for text in row.get("refuse") or [row.get("error") or "the VPS program failed"]:
            print(f"{str(row.get('result')).upper()}: {text}")
        print("nothing was written" if row.get("result") == "refused" else "check .env.agent on the VPS")
        return 1
    before = row.get("before") or {}
    if mode in ("write", "dry_run"):
        for name in [*pushed, *VPS_COPIED]:
            old = before.get(f".env.agent:{name}")
            new = (er.value_digest(nonce, name, pc[name]) if name in pc else before.get(f".env:{name}"))
            action = "add" if old is None else ("keep (equal)" if er.digests_equal(old, new) else "replace")
            print(f"{name}: {action}")
    if row.get("backup"):
        print(f"backup: {row['backup']}/env.agent (mode 600)")
    if mode == "dry_run":
        print(f"dry run: nothing written ({args.vps_dir}/.env.agent {er.mode_text(row.get('mode'))})")
        return 0
    lines, ok = compare(row.get("after") or {}, pc, nonce, pushed)
    for line in lines:
        print(line)
    print(f".env.agent: {row.get('result')} ({er.mode_text(row.get('mode'))})")
    if not ok:
        print("push_agent_env: a comparison failed (see the lines above)", file=sys.stderr)
        return 1
    return 0


def set_pc_public(path: Path, value: str) -> str:
    """Writes DESKTOP_AGENT_PUBLIC_KEY into the PC root .env (every assignment replaced, else appended; atomic,
    every other line byte for byte). Returns 'unchanged', 'replaced' or 'added'."""
    target = env_set.EnvFile(path)
    if target.value(AGENT_PUBLIC) == value:
        return "unchanged"
    action = rotate_env_secret.replace_in(target, AGENT_PUBLIC, env_set.render_value(AGENT_PUBLIC, value))
    gen_secrets.write_private([target])
    return action


def keys(args: argparse.Namespace, runner: Any) -> int:
    try:
        pc = pc_values(args.env_file)
    except EnvError as exc:
        print(f"push_agent_env: {exc}", file=sys.stderr)
        return 2
    pc_public = pc.get(PC_PUBLIC, "").strip().lower()
    if pc_public and not PUBLIC_RE.fullmatch(pc_public):
        print(f"push_agent_env: refused: {PC_PUBLIC} in {args.env_file.name} is not 64 hex characters",
              file=sys.stderr)
        return 2
    remote = {"dir": args.vps_dir, "docker": ["docker"], "image": AGENT_IMAGE, "pc_public": pc_public or None,
              "rotate": args.rotate, "dry_run": args.dry_run, "backup_root": er.BACKUP_ROOT, "stamp": er.utc_stamp()}
    code, out, err = runner(keys_program(), remote, args.host, args.ssh_key)
    rows = er.json_rows(out)
    row = rows[-1] if rows else {}
    print(f"push_agent_env --keys{' (dry run)' if args.dry_run else ''}: {args.vps_dir}/.env.agent")
    if not pc_public:
        print(f"{PC_PUBLIC}: not on this PC yet (gen_desktop_keys.py --role pc on the laptop, then --keys again)")
    if not row:
        print(f"push_agent_env: the VPS step failed (exit {code}): {er.failure_line(err)}", file=sys.stderr)
        return 1
    if row.get("command"):
        print("container: " + " ".join(str(part) for part in row["command"]))
    if row.get("result") in ("refused", "failed"):
        print(f"{str(row['result']).upper()}: {row.get('error')}")
        if row.get("backup"):
            print(f"backup: {row['backup']} (env.agent, bridge.env)")
        return 1
    if row.get("result") == "dry_run":
        print("dry run: nothing run or written")
        return 0
    public = row.get("public") or {}
    agent_public = str(public.get(AGENT_PUBLIC) or "")
    if not PUBLIC_RE.fullmatch(agent_public):
        print("push_agent_env: the VPS returned no usable public key", file=sys.stderr)
        return 1
    modes = row.get("modes") or {}
    print(f"{AGENT_PUBLIC}={agent_public}")
    if public.get(PC_PUBLIC):
        print(f"{PC_PUBLIC}={public[PC_PUBLIC]}")
    print(f"backup: {row.get('backup')} (env.agent, bridge.env)")
    print(f".env.agent: {'new key' if row.get('env_agent') == 'wrote' else 'key kept'} "
          f"({er.mode_text(modes.get('.env.agent'))}); bridge.env: {row.get('bridge_env')} "
          f"({er.mode_text(modes.get('bridge.env'))})")
    try:
        action = set_pc_public(args.env_file, agent_public)
    except EnvError as exc:
        print(f"push_agent_env: the VPS holds the key, but {args.env_file.name} on this PC was not updated: {exc}. "
              f"Put {AGENT_PUBLIC} (printed above, a public key) there by hand.", file=sys.stderr)
        return 1
    print(f"{args.env_file.name}: {AGENT_PUBLIC} {action}")
    print("next: recreate ui-bridge so it reads bridge.env (RUNBOOK 'Push the agent env'); 'agent_ctl node update' "
          "pins the agent public key on the laptop (C-123); then --check")
    return 0


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="push_agent_env.py",
                     description="Fill the VPS .env.agent from this PC and make the agent link key on the VPS; "
                                 "names, public keys and verdicts only.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--keys", action="store_true", help="make the agent's link key on the VPS (public keys out)")
    group.add_argument("--check", action="store_true", help="compare by HMAC digest only; write and send nothing")
    parser.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    parser.add_argument("--rotate", action="store_true", help="with --keys: replace an existing agent key")
    parser.add_argument("--env-file", type=Path, default=PC_ENV, help="the PC root .env (default: the repo's)")
    parser.add_argument("--host", default=gen_secrets.VPS_HOST, help=f"ssh target (default {gen_secrets.VPS_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=None, help="ssh identity file (default JARVIS_SSH_KEY)")
    parser.add_argument("--vps-dir", default=VPS_DIR, help=f"folder of .env.agent on the VPS (default {VPS_DIR})")
    args = parser.parse_args(argv)
    if args.rotate and not args.keys:
        parser.error("--rotate goes with --keys")
    if args.check and args.dry_run:
        parser.error("--check writes nothing anyway; drop --dry-run")
    args.ssh_key = args.ssh_key or er.default_ssh_key()
    args.vps_dir = er.vps_path(args.vps_dir).rstrip("/") or "/"
    return args


def main(argv: list[str] | None = None, runner: Any = None) -> int:
    args = parse_args(argv)
    runner = runner or gen_secrets.run_ssh
    if args.keys:
        return keys(args, runner)
    return push(args, runner, "check" if args.check else "dry_run" if args.dry_run else "write")


if __name__ == "__main__":
    sys.exit(main())
