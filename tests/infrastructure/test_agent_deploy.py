"""C-128: backup, deploy and rollback of the VPS brain (agent, agent-svc), offline, in the deploy tooling's sandbox.

The real backup_vps.sh v2, deploy.sh v3, rollback.sh and deploy_from_pc.sh run under bash in the scratch sandbox of
tests/unit/test_deploy_tooling.py (JARVIS_DEPLOY_SANDBOX moves every VPS path into tmp_path; docker, nginx, curl, ssh,
flock and df are fakes). The agent and bridge containers have a scratch file system there, so the agent data is really
sealed (by the image's own module, services/utils/agent_volumes.py with services/utils/backup_crypto.py, run from this
repo as 'python -m' to a test key) and the bridge's SQLite copy and volumes tar really run; the agent gate's
in-container checks answer from the fake's canned list. Nothing reaches the VPS, Docker, a real key or real memory.

The acceptance in test form: the backup holds the agent data sealed and no plain memory; a deploy of 'agent' recreates
only jarvis-agent (dry-run plan and real runs); the rollback drill (dry run, and the agent pair in rounds); the gate
fails on a broken image and prints the rollback commands. The C-128 review added: the node update whatever the PC checks
give and after --attach; the manifest check on a scratch copy of the shipped commit; the shared agent tag left on the
agent's image after rounds; JARVIS_BRAIN read as compose reads it, an unreadable one gated as vps; the node update
printed after an agent rollback; the dry run's verdict following the idle gate. The port onto C-125 added: one sealed
format and one backup key (a backup opens with backup_key.py decrypt and restore_agent_data.py), --gate-only skipping
the stopped agent containers only when JARVIS_BRAIN reads exactly 'pc', and the manifest check of the commit the node
is pinned to (AGENT_IMAGE_SHA) when it is not the shipped one. The second review added: --gate-only failing in vps mode
on a vps-brain service with no container at all, and the manifest check of the pinned commit after every --attach (also
of a run deploy.sh started on the VPS). Full deploy runs take 20-40 s each under Git Bash: the broken image one always
runs, the others with JARVIS_DEPLOY_E2E=1 (as in the shared module).
"""
# pylint: disable=redefined-outer-name,unused-import  # 'sb' is the shared module's fixture
from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from services.utils import agent_volumes as av
from services.utils import backup_crypto as bc
from tests.unit.test_deploy_tooling import (BASH, B_OLD, COMPOSE_NEW, F_OLD, HOSTINGER, NEW_SHA, OLD_SHA, Sandbox,
                                            _container, _git, _make_pc_repo, _out, _pc_env, _posix, _shell_function,
                                            _write, e2e, sb)

pytestmark = pytest.mark.skipif(BASH is None, reason="bash (Git Bash on Windows) is not available")

ROOT = Path(__file__).resolve().parents[2]
A_OLD = "sha256:" + "a0" * 32   # what jarvis-agent runs before the deploy
A_NEW = "sha256:" + "a2" * 32   # what the dedicated builder made for the deploy (jarvis-agent:hostinger)
A_SVC = "sha256:" + "a5" * 32   # what jarvis-agent-svc runs (an older image: a deploy of 'agent' alone)
REV = "org.opencontainers.image.revision"
AGENT_SECRET = "c128-fake-agent-secret"
SENTINEL = "c128-plain-memory-sentinel"
TASK_SENTINEL = "c128-plain-task-sentinel"
AGENT_VOLUMES = ["jarvis_agent_conversations", "jarvis_agent_chroma", "jarvis_agent_data"]
BRIDGE_VOLUMES = ["jarvis_logs", "jarvis_outputs", "jarvis_shared", "jarvis_bridge_data"]
COMPOSE_AGENT = COMPOSE_NEW + "".join(f"""  {svc}:
    image: jarvis-agent:hostinger
    container_name: {ctr}
    profiles: ["vps-brain"]
    depends_on:
      livekit:
        condition: service_started
      ui-bridge:
        condition: service_healthy
""" for svc, ctr in (("agent", "jarvis-agent"), ("agent-svc", "jarvis-agent-svc")))
WORKER_JSON = ('{"agent_name": "jarvis", "worker_type": "JT_ROOM", "worker_load": 0.0, "active_jobs": 0, '
               '"sdk_version": "1.4.6", "project_type": "python"}')
NET_GUARD_OK = '{\n  "allowed": {"bridge": "allowed", "livekit": "allowed"},\n  "failures": [],\n  "ok": true\n}\n'
LIVEKIT_LOG = ('2026-10-21T09:00:05.000Z\tINFO\tlivekit\tagent/handler.go:1\tworker registered\t{"namespace": "", '
               '"jobType": "JT_ROOM", "agentName": "jarvis", "workerID": "AW_test"}\n')
STARTED = "2026-09-30T10:00:00Z"      # when the sandbox's containers started (the shared module's _container)
REGISTERED = "2026-09-30T10:00:07Z"   # when LiveKit logged the running agent's registration (docker's time of the line)
BRIDGE_LOG = "2026-10-21 09:00:06 INFO Desktop link: agent:job-1 connected (internal listener) from 172.18.0.7\n"


def _load_restore_tool():
    spec = importlib.util.spec_from_file_location("jarvis_restore_agent_data_c128", ROOT / "scripts" / "ops" /
                                                  "restore_agent_data.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rad = _load_restore_tool()
bkey = rad.backup_key   # scripts/ops/backup_key.py, the one key store, as the restore tool loads it
KEY = X25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
PUBLIC = bc.public_key_hex(KEY)


def offline_key(folder: Path, key: X25519PrivateKey = KEY) -> Path:
    """The PC backup key as 'backup_key.py export' writes its offline copy (both PC tools read it with --key-file)."""
    path = folder / "jarvis-backup-key.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bkey.key_document(key))
    return path


# --------------------------------------------------------------------------- the agent sandbox


def bridge_db(path: Path, activity_age_s: float | None) -> None:
    """The bridge's store, as far as the idle gate reads it (kv last_activity_ts, messages, tool events)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts REAL NOT NULL)")
        con.execute("CREATE TABLE messages (seq INTEGER PRIMARY KEY, id TEXT, role TEXT, text TEXT, source TEXT, "
                    "final INTEGER, ts REAL, extra TEXT, created_ts REAL, updated_ts REAL)")
        con.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, kind TEXT, ts REAL, data TEXT)")
        if activity_age_s is not None:
            ts = time.time() - activity_age_s
            con.execute("INSERT INTO kv VALUES ('last_activity_ts', ?, ?)", (json.dumps(round(ts, 3)), ts))
            con.execute("INSERT INTO messages (id, role, text, source, final, ts, extra, created_ts, updated_ts) "
                        "VALUES ('m1', 'user', 'hello', 'voice', 1, ?, '{}', ?, ?)", (ts - 30, ts - 30, ts - 30))
        con.commit()
    finally:
        con.close()


def agent_sandbox(sb: Sandbox, *, brain: str = "pc", key: bool = True, agent: str | None = "running",
                  svc: str | None = "running", svc_image: str = A_SVC, volumes: bool = True, tag: str = A_NEW,
                  activity_age_s: float | None = 3600, real_backup: bool = False) -> dict[str, Path]:
    """The VPS of V4: the vps-brain services in the compose file, an agent image tagged, the agent containers with
    their volumes (memory with a plain sentinel, a live WAL SQLite) and the bridge with its volumes and store."""
    sb.stage(compose=COMPOSE_AGENT)
    if real_backup:   # the deploy's backup is then the real v2 one (seals the agent data), not the shared fake
        shutil.copy(HOSTINGER / "backup_vps.sh", sb.run_dir / "tree" / "deploy" / "hostinger" / "backup_vps.sh")
    _write(sb.hostinger / "docker-compose.yml", COMPOSE_AGENT)
    _write(sb.hostinger / ".env", "LIVEKIT_API_KEY=placeholder\nLIVEKIT_API_SECRET=placeholder\nSENTINEL=before\n"
                                  f"JARVIS_BRAIN={brain}\n")
    _write(sb.hostinger / ".env.agent", f"GOOGLE_API_KEY={AGENT_SECRET}\n"
                                        + (f"BACKUP_RECIPIENT_PUBLIC_KEY={PUBLIC}\n" if key else ""))
    agent_root, bridge_root = sb.root / "ctr" / "agent", sb.root / "ctr" / "bridge"
    app = agent_root / "app"
    for folder in (app / "conversations", app / "chroma_db", app / "data" / "identity", agent_root / "tmp"):
        folder.mkdir(parents=True, exist_ok=True)
    _write(app / "conversations" / "matloob_memory.json", json.dumps({"facts": [SENTINEL]}))
    _write(app / "data" / "identity" / "identity.json", '{"name": "owner"}')
    con = sqlite3.connect(app / "conversations" / "tasks.db")
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (TASK_SENTINEL,))
    con.commit()
    con.close()
    for folder in ("logs", "Jarvis_Outputs", "Jarvis_Shared/Downloads", "data"):
        (bridge_root / "app" / folder).mkdir(parents=True, exist_ok=True)
    (bridge_root / "tmp").mkdir(exist_ok=True)
    _write(bridge_root / "app" / "Jarvis_Shared" / "note.txt", "shared note\n")
    _write(bridge_root / "app" / "Jarvis_Shared" / "Downloads" / "song.mp3", "media bytes\n")
    _write(bridge_root / "app" / "logs" / "bridge.log", "log line\n")
    bridge_db(bridge_root / "app" / "data" / "bridge.db", activity_age_s)
    state = sb.load_state()
    state["tags"]["jarvis-agent:hostinger"] = tag
    state["images"] += [A_OLD, A_NEW, A_SVC]
    state["labels"] = {A_OLD: {REV: OLD_SHA}, A_NEW: {REV: NEW_SHA}, A_SVC: {REV: "0000aaa"}}
    state["volumes"] = BRIDGE_VOLUMES + (AGENT_VOLUMES if volumes else [])
    roots = {"root": str(agent_root), "root_sh": sb.sh(agent_root)}
    state["service_roots"] = {"agent": roots, "agent-svc": roots,
                              "ui-bridge": {"root": str(bridge_root), "root_sh": sb.sh(bridge_root)}}
    state["containers"]["jarvis-bridge"].update(root=str(bridge_root), root_sh=sb.sh(bridge_root))
    env = ["JARVIS_AGENT_NAME=jarvis", "UI_BRIDGE_URL=http://jarvis-bridge:5002", f"GOOGLE_API_KEY={AGENT_SECRET}"]
    for name, service, image, status in (("jarvis-agent", "agent", A_OLD, agent),
                                         ("jarvis-agent-svc", "agent-svc", svc_image, svc)):
        if status is None:
            continue
        ctr = _container((f"00000a9e{len(state['containers']):04x}", service, "jarvis"), "jarvis-agent:hostinger",
                         image, "healthy", "")
        ctr.update(status=status, env=list(env), **roots)
        state["containers"][name] = ctr
    state["exec"] = [["127.0.0.1:8081/worker", 0, WORKER_JSON], ["net_guard --self-test", 0, NET_GUARD_OK],
                     ["check_models.py", 0, "MiniLM ok\nECAPA ok\nMODELS_OK\n"]]
    state["code_root"] = str(ROOT)   # the agent image's /app code: 'python -m services.utils.agent_volumes' runs it
    # LiveKit's log is dated ('docker logs --since' filters it); an 'up' that starts the agent logs a new registration
    state["logs"] = {"jarvis-livekit": [[REGISTERED, LIVEKIT_LOG]], "jarvis-bridge": BRIDGE_LOG}
    state["registers"] = {"agent": "jarvis"}
    sb.save_state(state)
    return {"app": app, "agent_root": agent_root, "bridge_root": bridge_root}


def deploy(sb: Sandbox, *args: str) -> subprocess.CompletedProcess:
    """deploy.sh from a freshly staged run folder (a dry run removes the extracted tree when it ends)."""
    if not (sb.run_dir / "tree").exists():
        sb.stage(compose=COMPOSE_AGENT)
    return sb.deploy(*args)


def forget_calls(sb: Sandbox) -> None:
    """A fresh docker call log: what the setup did (its backup) is not what the step under test does."""
    (sb.state / "docker_calls.log").write_text("", encoding="utf-8")


def run_backup(sb: Sandbox, *args: str) -> tuple[subprocess.CompletedProcess, Path | None]:
    proc = sb.run(HOSTINGER / "backup_vps.sh", *args)
    match = re.search(r"^BACKUP_DIR=(.+)$", proc.stdout, re.M)
    folder = None
    if match:
        folder = sb.base / match.group(1).strip().rsplit("/", 1)[1]
    return proc, folder


def all_bytes(folder: Path) -> list[tuple[str, bytes]]:
    """Every file of a backup folder, and every member of its gzip tars, as (name, content)."""
    found = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        data = path.read_bytes()
        found.append((path.relative_to(folder).as_posix(), data))
        if path.name.endswith(".tgz"):
            with tarfile.open(path) as tar:
                for info in tar:
                    handle = tar.extractfile(info) if info.isfile() else None
                    if handle is not None:
                        found.append((f"{path.name}:{info.name}", handle.read()))
    return found


def manifest(folder: Path) -> dict[str, str]:
    lines = (folder / "MANIFEST.txt").read_text(encoding="utf-8").splitlines()
    out = {}
    for line in lines:
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" ") and key not in out:
            out[key] = value.strip()
    return out


# --------------------------------------------------------------------------- backup_vps.sh v2


def test_backup_v2_seals_the_agent_data_and_keeps_the_env_copies_in_their_folder(sb: Sandbox, capsys) -> None:
    """Acceptance: the backup holds the agent data sealed to the PC key (decryptable with it, every SQLite file an
    online copy) and no plain memory anywhere; the env copies sit in env/; MANIFEST records the brain and the env
    layout; Jarvis_Shared/Downloads stays out; the agent images get their rollback tags. The in-container step is the
    image's own module, and its file opens with both PC tools and the one backup key (K-77)."""
    agent_sandbox(sb, brain="vps")
    proc, folder = run_backup(sb, "c128")
    out = _out(proc)
    assert proc.returncode == 0 and folder is not None, out
    assert AGENT_SECRET not in out and SENTINEL not in out
    assert AGENT_SECRET not in "\n".join(sb.calls())       # the agent step ran under 'env -i', no secret on its line
    # the image's module from /app under 'env -i' (Git Bash rewrites the /app arguments a native fake receives)
    (step,) = [c for c in sb.calls() if c.startswith("exec ") and "agent_volumes" in c]
    assert step.startswith("exec -w ") and " jarvis-agent env -i " in step and step.endswith(" " + PUBLIC), step
    assert " LANG=C.UTF-8 python -m services.utils.agent_volumes " in step, step
    names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert {"env/hostinger.env", "env/env.agent", "agent-volumes.tar.enc", "volumes.tgz", "bridge.db",
            "opt-jarvis-agent.tgz", "MANIFEST.txt", "SHA256SUMS", "images.txt"} <= set(names), names
    assert not [n for n in names if n in ("hostinger.env", "env.agent", "bridge.env", "root.env")], names
    # the agent data: sealed in backup_crypto's format (agent-svc's snapshot format) to the PC key, and complete
    sealed = (folder / av.ARCHIVE).read_bytes()
    assert bc.looks_sealed(sealed)
    inv = rad.check_file(folder / av.ARCHIVE, KEY)
    assert inv.roots == {"conversations": 2, "chroma_db": 0, "data": 1} and inv.meta["sqlite"] == 1
    with pytest.raises(bc.SnapshotCryptoError, match="does not verify"):
        rad.check_file(folder / av.ARCHIVE, X25519PrivateKey.generate())
    plain = bc.open_bytes(sealed, KEY)
    with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        memory = tar.extractfile("conversations/matloob_memory.json")
        assert memory is not None and SENTINEL.encode() in memory.read()
    # both PC tools open it with the one backup key: backup_key.py decrypt (as a snapshot) and restore_agent_data.py
    key_file = offline_key(sb.tmp / "usb")
    opened = sb.tmp / "opened" / "agent-volumes.tar"
    capsys.readouterr()
    assert bkey.main(["decrypt", str(folder / av.ARCHIVE), "--key-file", str(key_file), "--out", str(opened)]) == 0
    printed = capsys.readouterr().out
    assert "KIND=agent-volumes" in printed and opened.read_bytes() == plain, printed
    assert rad.main(["--check-file", str(folder / av.ARCHIVE), "--key-file", str(key_file)]) == 0
    printed = capsys.readouterr().out
    assert "CHECK=ok" in printed and SENTINEL not in printed and bc.private_key_hex(KEY) not in printed
    # ...and nowhere in plain text: not in any file, not in any member of the code or volumes archives
    for name, data in all_bytes(folder):
        assert SENTINEL.encode() not in data and TASK_SENTINEL.encode() not in data, name
    meta = manifest(folder)
    assert meta["script"] == "backup_vps.sh v2" and meta["brain"] == "vps"
    assert meta["env_dir"].startswith("env/ (hostinger.env env.agent")
    assert meta["agent_data"].startswith("sealed via docker exec jarvis-agent: ") and "sqlite=1" in meta["agent_data"]
    assert meta["agent_data"].endswith(f" recipient={bc.fingerprint(KEY)}")   # the key it is sealed to
    assert meta["media"].startswith("excluded (Jarvis_Shared/Downloads")
    text = (folder / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "env_layout:    .env.agent present BACKUP_RECIPIENT_PUBLIC_KEY,GOOGLE_API_KEY" in text
    assert AGENT_SECRET not in text and PUBLIC not in text
    with tarfile.open(folder / "volumes.tgz") as tar:
        members = tar.getnames()
    assert "Jarvis_Shared/note.txt" in members and not [m for m in members if "Downloads" in m], members
    assert "env/hostinger.env" in (folder / "SHA256SUMS").read_text(encoding="utf-8")
    check = subprocess.run([BASH, "-c", 'cd "$1" && sha256sum -c --quiet SHA256SUMS && echo SUMS_OK', "sums",
                            sb.sh(folder)], env=sb.env(), capture_output=True, text=True, check=False)
    assert "SUMS_OK" in check.stdout, check.stdout + check.stderr
    tags = sb.load_state()["tags"]
    assert tags[f"jarvis-agent:rollback-{folder.name}"] == A_OLD                 # the running agent's image
    assert tags[f"jarvis-agent:rollback-{folder.name}-agent-svc"] == A_SVC       # agent-svc runs another one
    assert tags[f"jarvis-bridge:rollback-{folder.name}"] == B_OLD
    assert tags[f"jarvis-frontend:rollback-{folder.name}"] == F_OLD
    assert f"rollback\tjarvis-agent:rollback-{folder.name}-agent-svc\t{A_SVC}" in (folder / "images.txt").read_text(
        encoding="utf-8")


def test_backup_v2_with_media_without_a_key_and_with_the_agent_stopped(sb: Sandbox) -> None:
    """--with-media takes the downloads; without BACKUP_RECIPIENT_PUBLIC_KEY the agent data is skipped (a warning, never
    plain text) and the backup still succeeds (bridge deploys go on); a stopped agent is read by a one-off agent
    container."""
    agent_sandbox(sb, key=False, agent="exited", svc="exited")
    proc, folder = run_backup(sb, "--with-media", "nokey")
    assert proc.returncode == 0 and folder is not None, _out(proc)
    assert "agent data not backed up: SKIPPED: .env.agent has no BACKUP_RECIPIENT_PUBLIC_KEY" in proc.stderr
    assert not (folder / "agent-volumes.tar.enc").exists() and not list(folder.glob(".agent-volumes*"))
    meta = manifest(folder)
    assert meta["agent_data"].startswith("SKIPPED") and meta["media"] == "included (--with-media)"
    with tarfile.open(folder / "volumes.tgz") as tar:
        assert "Jarvis_Shared/Downloads/song.mp3" in tar.getnames()
    for name, data in all_bytes(folder):
        assert SENTINEL.encode() not in data, name
    # with the key, the stopped agent's data goes through a one-off 'compose run' of agent
    _write(sb.hostinger / ".env.agent", f"BACKUP_RECIPIENT_PUBLIC_KEY={PUBLIC}\n")
    proc, folder = run_backup(sb, "stopped")
    assert proc.returncode == 0 and folder is not None, _out(proc)
    assert manifest(folder)["agent_data"].startswith(
        "sealed via docker compose -p jarvis run --rm --no-deps agent: ")
    assert (sb.state / "runs.log").read_text(encoding="utf-8").splitlines() == ["agent"]
    assert rad.check_file(folder / "agent-volumes.tar.enc", KEY).roots["conversations"] == 2


def test_backup_v2_falls_back_to_a_one_off_container_and_never_fails_a_bridge_deploy(sb: Sandbox) -> None:
    """An agent in a restart loop (running, but no exec): a one-off agent container seals the data instead. When the
    sealing fails anyway, that is a warning (agent_data: FAILED), not an error: the backup stands, so a bridge deploy
    goes on, while an agent deploy refuses such a backup."""
    agent_sandbox(sb)
    sb.tweak(exec_fails=["jarvis-agent"])
    proc, folder = run_backup(sb, "restarting")
    assert proc.returncode == 0 and folder is not None, _out(proc)
    assert "docker exec in jarvis-agent failed (exit 126); trying a one-off agent container" in proc.stderr
    assert manifest(folder)["agent_data"].startswith("sealed via docker compose -p jarvis run --rm --no-deps agent:")
    assert rad.check_file(folder / "agent-volumes.tar.enc", KEY).files == 3
    state = sb.load_state()
    state["exec"].append(["python -m services.utils.agent_volumes", 1, ""])   # the step itself fails, in both places
    sb.save_state(state)
    proc, folder = run_backup(sb, "sealfails")
    assert proc.returncode == 0 and folder is not None, _out(proc)
    assert manifest(folder)["agent_data"].startswith("FAILED via docker compose -p jarvis run --rm --no-deps agent")
    assert "agent data not backed up: FAILED" in proc.stderr and not (folder / "agent-volumes.tar.enc").exists()
    assert not list(folder.glob(".agent-volumes*")) and not list(sb.base.glob(".agent-seal.*"))


def test_backup_v2_before_the_vps_brain_exists_records_none(sb: Sandbox) -> None:
    """Today's VPS: no agent volume, container or image. Nothing to seal, no agent tag, no error."""
    agent_sandbox(sb, agent=None, svc=None, volumes=False)
    state = sb.load_state()
    del state["tags"]["jarvis-agent:hostinger"]
    sb.save_state(state)
    proc, folder = run_backup(sb)
    assert proc.returncode == 0 and folder is not None, _out(proc)
    assert manifest(folder)["agent_data"].startswith("none (no jarvis_agent_* volume")
    assert "jarvis-agent: no container and no jarvis-agent:hostinger image yet" in proc.stdout
    assert not [t for t in sb.load_state()["tags"] if t.startswith("jarvis-agent:")]
    assert "ERROR" not in _out(proc) and "WARNING" not in proc.stderr.replace("LiveKit image id", "")


# --------------------------------------------------------------------------- deploy.sh: plan, gate, idle gate


def _deploy_plan_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("[deploy]   ") and ": " in line]


def test_deploy_dry_run_of_agent_recreates_only_jarvis_agent(sb: Sandbox) -> None:
    """Acceptance: deploying 'agent' recreates only jarvis-agent ('up -d --no-deps agent'); agent-svc, the bridge,
    LiveKit and the frontend are not touched. In pc mode the idle gate is not needed and the plan makes no docker call
    that changes anything."""
    agent_sandbox(sb, brain="pc")
    proc = deploy(sb, "--services", "agent", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert re.search(r"^\[deploy\] 11 recreate     docker compose -p jarvis .* up -d --no-deps --no-build --pull never "
                     r"agent$", out, re.M), out
    deployed = [line for line in out.splitlines() if line.endswith("<- deploy")]
    assert len(deployed) == 1 and deployed[0].startswith("[deploy]   agent: jarvis-agent "), deployed
    for svc in ("agent-svc", "ui-bridge", "frontend", "livekit"):
        assert any(line.startswith(f"[deploy]   {svc}: ") and line.endswith("(not touched)")
                   for line in out.splitlines()), svc
    assert "idle gate    not needed (brain pc" in out
    assert "agent/agent-svc: healthy within 180s; agent: /worker agent_name, LiveKit 'worker registered'" in out
    assert "an agent deploy needs it identical" in out and "brain pc --now" in out
    assert "15 agent image  AGENT_IMAGE_SHA=" in out and f"now: {NEW_SHA}" in out
    assert "agent image: built from " + NEW_SHA in out and "while this deploy ships" not in out
    assert "VERDICT: a real run would proceed" in out
    assert not sb.mutating_docker_calls() and not sb.backups()


def test_deploy_warns_when_the_agent_image_is_from_another_commit_and_checks_the_disk(sb: Sandbox) -> None:
    agent_sandbox(sb, tag=A_OLD)
    proc = deploy(sb, "--services", "agent", "--dry-run")
    assert proc.returncode == 0 and f"the agent image was built from {OLD_SHA}, while this deploy ships {NEW_SHA}" \
        in _out(proc), _out(proc)
    _write(sb.state / "df_gb", "10")
    proc = deploy(sb, "--services", "agent", "--dry-run")
    assert proc.returncode == 1 and "a deploy of the VPS brain needs 15 GB" in _out(proc), _out(proc)
    proc = deploy(sb, "--services", "ui-bridge", "--dry-run")   # the bridge keeps its own 5 GB rule
    assert proc.returncode == 0 and "VERDICT: a real run would proceed" in _out(proc), _out(proc)
    _write(sb.state / "df_gb", "20")
    proc = deploy(sb, "--services", "agent", "--dry-run")
    assert proc.returncode == 0 and "the VPS brain wants 30 GB" in _out(proc), _out(proc)
    assert not sb.backups()


def test_the_idle_gate_refuses_an_agent_restart_in_vps_mode_unless_now(sb: Sandbox) -> None:
    """K-72: 10 min without speech, typed chat or a tool call before the VPS agent restarts; preflight refuses (exit 1,
    nothing changed, no backup). --now passes it (the notice comes right before the restart)."""
    agent_sandbox(sb, brain="vps", activity_age_s=60)
    proc = deploy(sb, "--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 1 and "idle gate: the last speech, typed chat or tool call was" in out, out
    assert "add --now" in out and not sb.backups()
    assert all(" exec jarvis-bridge python -c " in f" {c}" for c in sb.mutating_docker_calls())   # read-only query
    # the dry run (deploy_from_pc.sh --check runs it on the VPS) gives the real run's answer: refuse now, exit 1
    proc = deploy(sb, "--services", "agent", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 1 and "idle gate    NOT idle: the last speech" in out, out
    assert "VERDICT: a real run would REFUSE now: idle gate: the last speech, typed chat or tool call was" in out
    assert "--now: a 0 s notice instead" in out and "VERDICT: a real run would proceed" not in out
    proc = deploy(sb, "--services", "agent", "--dry-run", "--now")
    assert proc.returncode == 0 and "idle gate    skipped (--now" in _out(proc), _out(proc)
    assert "VERDICT: a real run would proceed" in _out(proc)
    bridge_db_path = sb.root / "ctr" / "bridge" / "app" / "data" / "bridge.db"
    bridge_db_path.unlink()
    bridge_db(bridge_db_path, 3600)
    proc = deploy(sb, "--services", "agent", "--dry-run")
    assert proc.returncode == 0 and re.search(r"idle gate    ok: the last activity was \d+ s ago", _out(proc)), \
        _out(proc)
    proc = deploy(sb, "--services", "agent-svc", "--dry-run")   # agent-svc holds no session: no idle gate
    assert proc.returncode == 0 and "idle gate" not in _out(proc), _out(proc)


def lib(sb: Sandbox, body: str, *args: str) -> subprocess.CompletedProcess:
    """deploy.sh's functions (sourced as rollback.sh does) run in the sandbox with the fake docker."""
    source = _posix(HOSTINGER / "deploy.sh")
    return subprocess.run([BASH, "-c", f'source "{source}"\n{body}', "lib", *args], env=sb.env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180, check=False)


@pytest.mark.parametrize(("line", "brain"), [
    ("JARVIS_BRAIN=vps   # set by agent_ctl", "vps"),
    ("JARVIS_BRAIN=pc\t# a tab before the comment", "pc"),
    ('JARVIS_BRAIN="vps"  # quoted', "vps"),
    ("export JARVIS_BRAIN = 'pc'", "pc"),
    ("\ufeffJARVIS_BRAIN=vps", "vps"),          # a UTF-8 BOM in front of the first line
    ("JARVIS_BRAIN=vps\r", "vps"),              # a CRLF file
    ("JARVIS_BRAIN=vps#x", "unknown"),          # no blank before '#': compose keeps it in the value
    ("JARVIS_BRAIN=", "unknown"),
    ("JARVIS_BRAIN=VPS", "unknown"),
    ("OTHER=vps", "unknown"),
], ids=["inline-comment", "tab-comment", "double-quoted", "export-single-quoted", "bom", "crlf", "hash-in-value",
        "empty", "upper-case", "absent"])
def test_the_brain_is_read_as_compose_reads_it(tmp_path: Path, line: str, brain: str) -> None:
    """deploy.sh's env_brain (the idle gate, the hello check) and backup_vps.sh's env_value_of (MANIFEST brain:, the
    PC backup public key) read a .env line the way compose does, as rollback.sh's env_value_digest does: an inline
    comment no longer turns 'vps' into an unknown brain that skipped the idle gate."""
    env_file = tmp_path / ".env"
    env_file.write_bytes((line + "\nOTHER=1\n").encode("utf-8"))   # a BOM belongs in front of the first line
    script = (_shell_function("deploy.sh", "env_brain") + _shell_function("backup_vps.sh", "env_value_of")
              + "BOM=$'\\xef\\xbb\\xbf'\nENV_FILE=\"$1\"\n"
              + 'printf "deploy=%s\\n" "$(env_brain)"\nprintf "backup=%s\\n" "$(env_value_of "$1" JARVIS_BRAIN)"\n')
    proc = subprocess.run([BASH, "-c", script, "brain", _posix(env_file)], capture_output=True, text=True,
                          check=False)
    assert proc.returncode == 0, _out(proc)
    got = dict(item.split("=", 1) for item in proc.stdout.splitlines())
    assert got["deploy"] == brain, got
    # backup_vps.sh's brain_of applies the same vps|pc rule to the value it reads
    assert (got["backup"] if got["backup"] in ("vps", "pc") else "unknown") == brain, got


def test_a_commented_or_unreadable_brain_never_skips_the_idle_gate(sb: Sandbox) -> None:
    """K-72: 'JARVIS_BRAIN=vps   # set by agent_ctl' is vps (compose strips the comment), so an agent restart in
    mid-conversation is refused; a value that reads as neither vps nor pc is gated as vps (it fails closed), and the
    hello check warns instead of skipping."""
    agent_sandbox(sb, brain="vps", activity_age_s=60)
    env_file = sb.hostinger / ".env"
    base = env_file.read_text(encoding="utf-8").replace("JARVIS_BRAIN=vps\n", "")
    _write(env_file, base + "JARVIS_BRAIN=vps   # set by agent_ctl\n")
    proc = deploy(sb, "--services", "agent", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 1 and "idle gate    NOT idle: the last speech" in out, out
    assert "VERDICT: a real run would REFUSE now: idle gate" in out and "not needed" not in out
    proc = deploy(sb, "--services", "agent", "--foreground")
    assert proc.returncode == 1 and "idle gate: the last speech" in _out(proc) and not sb.backups(), _out(proc)
    _write(env_file, base + "JARVIS_BRAIN=${BRAIN:-vps}\n")   # compose would resolve it; this reader cannot
    proc = deploy(sb, "--services", "agent", "--dry-run")
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "reads as neither vps nor pc (as compose reads it): gated as in vps mode" in out
    assert "idle gate    NOT idle" in out
    proc = deploy(sb, "--services", "agent", "--foreground")
    assert proc.returncode == 1 and "idle gate: the last speech" in _out(proc) and not sb.backups(), _out(proc)
    sb.tweak(logs={"jarvis-livekit": [], "jarvis-bridge": ""})   # no hello since the recreate yet
    proc = lib(sb, "agent_hello_check agent WARN\n")
    assert "WARN: agent: no agent_hello on :5002 since the recreate yet (brain unknown" in proc.stdout, _out(proc)
    _write(env_file, base + "JARVIS_BRAIN=pc  # the PC brain\n")
    proc = lib(sb, "agent_hello_check agent WARN\n")
    assert "skip agent: agent_hello on :5002 (brain pc" in proc.stdout, _out(proc)
    proc = deploy(sb, "--services", "agent", "--dry-run")
    assert proc.returncode == 0 and "idle gate    not needed (brain pc" in _out(proc), _out(proc)


def test_the_agent_gate_fails_on_a_broken_image_and_prints_the_rollback_commands(sb: Sandbox) -> None:
    """Acceptance: the new agent image never gets healthy. The gate fails, the automatic rollback puts jarvis-agent back
    on its old image (exit 2), and the summary names 'rollback.sh <backup>' and 'agent_ctl brain pc'. Only jarvis-agent
    was recreated, twice (the deploy and its rollback); agent-svc, the bridge, LiveKit and the frontend never."""
    agent_sandbox(sb, brain="pc")
    sb.tweak(bad_images=[A_NEW], bad_effects=["unhealthy"])
    svc_before = sb.load_state()["containers"]["jarvis-agent-svc"]
    proc = deploy(sb, "--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 2, out
    (backup,) = sb.backups()
    assert "agent: container not healthy" in out and "AUTOMATIC ROLLBACK" in out and "RESULT=rolled-back" in out
    # the broken container this run started never registered with LiveKit (the one before it did, weeks ago); the
    # rollback's container did, right after its own start
    assert "FAIL: livekit: no 'worker registered' for agent jarvis since this run started agent" in out
    assert "ok   livekit: worker registered (agent jarvis)" in out.split("AUTOMATIC ROLLBACK", 1)[1]
    assert "AGENT GATE FAILED: if the VPS brain does not answer, go back with" in out
    assert f"rollback: bash {sb.sh(sb.base / 'tools')}/rollback.sh {backup.name}" in out
    assert "PC brain: .venv_312/Scripts/python.exe scripts/agent_ctl.py brain pc --now" in out
    state = sb.load_state()
    agent = state["containers"]["jarvis-agent"]
    assert agent["image"] == A_OLD and agent["health"] == "healthy"
    assert state["containers"]["jarvis-agent-svc"] == svc_before
    ups = [c for c in sb.calls() if " up -d " in c]
    assert len(ups) == 2 and all(c.endswith("up -d --no-deps --no-build --pull never agent") for c in ups), ups
    assert "AGENT_IMAGE_SHA" not in out and AGENT_SECRET not in out
    assert "RESULT=rolled-back" in (backup / "deploy.env").read_text(encoding="utf-8")


def _read_only_calls(sb: Sandbox) -> list[str]:
    """The docker calls that change something; the gate's in-container checks ('exec') only read."""
    return [c for c in sb.mutating_docker_calls() if not c.startswith("exec ")]


def test_gate_only_takes_the_registration_since_the_agent_started_and_only_warns_when_it_rotated_away(
        sb: Sandbox) -> None:
    """The RUNBOOK's read-only check of an agent that started weeks ago: its 'worker registered' line is looked up since
    its container started (not in the last 15 min); once LiveKit's log rotated it away that is a warning, as this run
    started nothing, never GATE=fail."""
    agent_sandbox(sb, brain="vps")
    script = sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh"
    proc = sb.run(script, "--gate-only", "--services", "agent")
    out = _out(proc)
    assert proc.returncode == 0 and "GATE=ok" in out, out
    assert "ok   livekit: worker registered (agent jarvis)" in out
    assert f"logs --since {STARTED} jarvis-livekit" in sb.calls()
    sb.tweak(logs={"jarvis-livekit": [], "jarvis-bridge": BRIDGE_LOG})   # rotated out of LiveKit's 3 x 10 MB
    proc = sb.run(script, "--gate-only", "--services", "agent")
    out = _out(proc)
    assert proc.returncode == 0 and "GATE=ok" in out, out
    assert (f"WARN: livekit: no 'worker registered' for agent jarvis since agent started ({STARTED}); this run did "
            "not start it") in out
    assert not _read_only_calls(sb) and not sb.backups()


def test_an_agent_deploy_whose_up_changes_no_container_passes_the_gate(sb: Sandbox) -> None:
    """jarvis-agent:hostinger was not rebuilt, so 'up -d --no-deps agent' leaves the healthy agent running and LiveKit
    logs no new registration (and here the old one rotated away). That only warns: no rollback, no exit 3, no 'brain
    pc' for an agent that never changed; the same container keeps running."""
    agent_sandbox(sb, brain="pc", tag=A_OLD)
    state = sb.load_state()
    state["containers"]["jarvis-agent"]["env"] = []   # the environment compose gives it: the 'up' changes nothing
    state["logs"]["jarvis-livekit"] = []
    sb.save_state(state)
    before = sb.load_state()["containers"]["jarvis-agent"]
    proc = deploy(sb, "--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0 and "RESULT=ok" in out, out
    assert f"the agent image was built from {OLD_SHA}, while this deploy ships {NEW_SHA}" in out
    assert f"WARN: livekit: no 'worker registered' for agent jarvis since agent started ({STARTED})" in out
    assert "AUTOMATIC ROLLBACK" not in out and "AGENT GATE FAILED" not in out and f"AGENT_IMAGE_SHA={OLD_SHA}" in out
    assert sb.load_state()["containers"]["jarvis-agent"] == before   # the same container: never recreated
    assert len([c for c in sb.calls() if " up -d " in c]) == 1


@e2e
def test_agent_deploy_runs_every_check_seals_its_backup_and_reports_the_image_sha(sb: Sandbox) -> None:
    """A healthy agent deploy in vps mode (idle for an hour): the real v2 backup seals the agent data first, the gate
    runs each K-75 check (in-container checks under 'env -i'), the smoke diff stays identical, only jarvis-agent is
    recreated, and AGENT_IMAGE_SHA names the commit the image was built from."""
    agent_sandbox(sb, brain="vps", real_backup=True)
    proc = deploy(sb, "--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 0, out
    for line in ("idle gate    ok: the last activity was", "ok   agent: container healthy",
                 "ok   agent: /worker shows agent_name jarvis",
                 "ok   livekit: worker registered (agent jarvis)", "ok   agent: offline model check (MODELS_OK)",
                 "ok   agent: net_guard self-test", "ok   agent: agent_hello accepted on :5002",
                 "ok   other apps: identical before and after", f"AGENT_IMAGE_SHA={NEW_SHA}", "RESULT=ok"):
        assert line in out, line
    (backup,) = sb.backups()
    assert (backup / "agent-volumes.tar.enc").exists() and manifest(backup)["agent_data"].startswith("sealed")
    ups = [c for c in sb.calls() if " up -d " in c]
    assert len(ups) == 1 and ups[0].endswith("up -d --no-deps --no-build --pull never agent"), ups
    execs = [c for c in sb.calls() if c.startswith("exec jarvis-agent ")]
    assert execs and all(" env -i " in f" {c} " for c in execs) and AGENT_SECRET not in "\n".join(execs)
    assert sb.load_state()["containers"]["jarvis-agent"]["image"] == A_NEW


@e2e
def test_an_agent_deploy_is_refused_when_its_backup_could_not_seal_the_agent_data(sb: Sandbox) -> None:
    agent_sandbox(sb, key=False, real_backup=True)
    proc = deploy(sb, "--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 1 and "the pre-deploy backup holds no agent data (SKIPPED" in out, out
    assert not [c for c in sb.calls() if " up -d " in c]
    assert sb.load_state()["containers"]["jarvis-agent"]["image"] == A_OLD


@e2e
def test_an_agent_deploy_needs_the_other_apps_smoke_identical(sb: Sandbox) -> None:
    """K-75: a change in another app (here a site going 301 -> 200), which a bridge deploy only warns about, fails an
    agent deploy once the re-check confirms it; the automatic rollback follows."""
    agent_sandbox(sb)
    sb.tweak(bad_images=[A_NEW], bad_effects=["change"])
    proc = deploy(sb, "--services", "agent", "--foreground")
    out = _out(proc)
    assert proc.returncode == 2, out
    assert "WARN site" in out and "K-75: the smoke diff must stay identical; confirmed by the re-check" in out
    assert sb.load_state()["containers"]["jarvis-agent"]["image"] == A_OLD


# --------------------------------------------------------------------------- rollback.sh: the drill


def agent_deployed_state(sb: Sandbox, **kwargs) -> str:
    """What a deploy of 'agent agent-svc' leaves (its real v2 backup, records, LAST_DEPLOY, tools), both containers
    on A_NEW now."""
    agent_sandbox(sb, **kwargs)
    proc, folder = run_backup(sb, "pre-deploy-" + NEW_SHA)
    assert proc.returncode == 0 and folder is not None, _out(proc)
    _write(folder / "deploy.env", f"KIND=deploy\nSHA={NEW_SHA}\nPREV_SHA={OLD_SHA}\nSERVICES=agent agent-svc\nNGINX=0\n"
                                  "CODE=1\nRESULT=ok\n")
    _write(folder / "deploy-images.txt", "# service\tcontainer\tref\timage_id\n"
                                         f"agent\tjarvis-agent\tjarvis-agent:hostinger\t{A_OLD}\n"
                                         f"agent-svc\tjarvis-agent-svc\tjarvis-agent:hostinger\t{A_SVC}\n")
    _write(sb.base / "LAST_DEPLOY", folder.name + "\n")
    for tool in ("deploy.sh", "rollback.sh", "smoke_other_apps.sh", "backup_vps.sh"):
        _write(sb.base / "tools" / tool, (HOSTINGER / tool).read_text(encoding="utf-8"), 0o700)
    state = sb.load_state()
    state["tags"]["jarvis-agent:hostinger"] = A_NEW
    for name in ("jarvis-agent", "jarvis-agent-svc"):
        state["containers"][name]["image"] = A_NEW
    sb.save_state(state)
    return folder.name


def test_the_rollback_drill_dry_run_brings_both_agent_images_back(sb: Sandbox) -> None:
    """Acceptance (the drill, dry): the last deploy's backup puts agent and agent-svc back on their own recorded images,
    with its code; the env layout of the v2 env/ copies equals the live files; nothing changes."""
    name = agent_deployed_state(sb)
    forget_calls(sb)
    n_backups, state = len(sb.backups()), sb.load_state()
    tools = sb.base / "tools" / "rollback.sh"
    proc = sb.run(tools, "--dry-run")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert f"image        agent: jarvis-agent:hostinger -> {A_OLD[7:19]}" in out
    assert f"image        agent-svc: jarvis-agent:hostinger -> {A_SVC[7:19]}" in out
    assert "code         restore" in out and f"env layout   same as {name} (.env .env.agent; names only)" in out
    assert "VERDICT: a real run would proceed" in out and "restore_agent_data.py" not in out
    # the laptop's Desktop Node follows the restored agent image (the last agent deploy pinned it to the newer one)
    assert ("node         after the recreate: AGENT_IMAGE_SHA=<the revision the agent runs then> and "
            "'.venv_312/Scripts/python.exe scripts/agent_ctl.py node update --sha <it>' for the PC") in out
    proc = sb.run(tools, "--dry-run", "--data", "--services", "agent,agent-svc")   # the rollback, then the data
    out = _out(proc)
    assert proc.returncode == 0, out
    assert f"image        agent-svc: jarvis-agent:hostinger -> {A_SVC[7:19]}" in out
    assert f".venv_312/Scripts/python.exe scripts/ops/restore_agent_data.py {name} --dry-run" in out
    assert "moves the live data aside into .pre-restore-<UTC>/, never deleting it" in out
    assert len(sb.backups()) == n_backups and sb.load_state() == state and not sb.mutating_docker_calls()


def test_rollback_data_alone_only_prints_and_refuses_a_backup_without_agent_data(sb: Sandbox) -> None:
    name = agent_deployed_state(sb)
    forget_calls(sb)
    n_backups, state = len(sb.backups()), sb.load_state()
    tools = sb.base / "tools" / "rollback.sh"
    proc = sb.run(tools, "--data")                     # no --dry-run: --data on its own changes nothing anyway
    out = _out(proc)
    assert proc.returncode == 0, out
    assert f"target       {sb.sh(sb.base / name)} (agent data only" in out
    assert f"scripts/ops/restore_agent_data.py {name}" in out and "VERDICT: nothing changed" in out
    assert "image        " not in out and len(sb.backups()) == n_backups and sb.load_state() == state
    assert not sb.mutating_docker_calls() and not (sb.base / ".deploy.lock").exists()
    plain = sb.base / "2026-10-02_0001Z-plain"
    _write(plain / "MANIFEST.txt",
           "backup:        2026-10-02_0001Z-plain\nagent_data:    none (no jarvis_agent_* volume)\n")
    proc = sb.run(tools, plain.name, "--data")
    assert proc.returncode == 1, _out(proc)
    assert "holds no agent data (no agent-volumes.tar.enc; its MANIFEST says agent_data: none" in _out(proc)


def test_rounds_leave_the_shared_agent_tag_on_the_agents_image(sb: Sandbox) -> None:
    """agent and agent-svc share jarvis-agent:hostinger. A rollback (or an automatic one) whose records differ runs in
    rounds, the agent's first; afterwards the shared tag names the agent's image again, so the next plain 'up' of the
    pair (agent_ctl brain vps) leaves jarvis-agent where it is. It used to end on agent-svc's older image, onto which
    that 'up' then silently moved the owner's brain."""
    agent_sandbox(sb)
    records = _write(sb.tmp / "records.txt", "# service\tcontainer\tref\timage_id\n"
                                             f"agent\tjarvis-agent\tjarvis-agent:hostinger\t{A_OLD}\n"
                                             f"agent-svc\tjarvis-agent-svc\tjarvis-agent:hostinger\t{A_SVC}\n")
    for order in ("agent agent-svc", "agent-svc agent"):
        state = sb.load_state()   # what a deploy of 'agent agent-svc' leaves: both on the new image
        state["tags"]["jarvis-agent:hostinger"] = A_NEW
        for name in ("jarvis-agent", "jarvis-agent-svc"):
            state["containers"][name]["image"] = A_NEW
        sb.save_state(state)
        forget_calls(sb)
        proc = lib(sb, f'recreate_in_rounds "$1" "docker compose up (rollback) failed" {order}\n'
                       'printf "FAILS=%s\\n" "${#FAILS[@]}"\n', sb.sh(records))
        out = _out(proc)
        assert proc.returncode == 0 and "FAILS=0" in out, out
        state = sb.load_state()
        assert state["containers"]["jarvis-agent"]["image"] == A_OLD, order
        assert state["containers"]["jarvis-agent-svc"]["image"] == A_SVC, order
        assert state["tags"]["jarvis-agent:hostinger"] == A_OLD, order          # the agent's image, not agent-svc's
        assert [c.rsplit(" ", 1)[1] for c in sb.calls() if " up -d " in c] == ["agent", "agent-svc"], order
        assert ("WARN: agent-svc runs its own recorded image, while jarvis-agent:hostinger names agent's again: a "
                "plain 'up' of agent-svc (agent_ctl brain vps, a deploy that names it) recreates it on that image") \
            in out
    # agent_ctl brain vps: 'up -d --no-deps --no-build --pull never agent agent-svc' keeps the agent on its image
    proc = lib(sb, "compose up -d --no-deps --no-build --pull never agent agent-svc\n")
    assert proc.returncode == 0, _out(proc)
    state = sb.load_state()
    assert state["containers"]["jarvis-agent"]["image"] == A_OLD
    assert state["containers"]["jarvis-agent-svc"]["image"] == A_OLD   # the move the warning announced
    # one image for both (a single round): nothing to point back, no warning
    _write(records, f"agent\tjarvis-agent\tjarvis-agent:hostinger\t{A_OLD}\n"
                    f"agent-svc\tjarvis-agent-svc\tjarvis-agent:hostinger\t{A_OLD}\n")
    proc = lib(sb, 'recreate_in_rounds "$1" "up failed" agent agent-svc\n', sb.sh(records))
    assert proc.returncode == 0 and "WARN" not in _out(proc) and "the shared tag back" not in _out(proc), _out(proc)


def test_node_pin_hint_names_the_agent_images_commit_and_the_node_update(sb: Sandbox) -> None:
    """rollback.sh calls it after recreating agent or agent-svc: the revision label of the image the agent runs, and
    the agent_ctl call that pins the laptop's Desktop Node to it (K-71: else MANIFEST_MISMATCH)."""
    agent_sandbox(sb)   # jarvis-agent runs A_OLD, built from OLD_SHA
    proc = lib(sb, "node_pin_hint C-128-rollback-2026-10-21_0900Z-pre-rollback\n")
    out = _out(proc)
    assert proc.returncode == 0, out
    assert f"[deploy] AGENT_IMAGE_SHA={OLD_SHA}\n" in proc.stdout
    assert (f"on the PC, in the repo: .venv_312/Scripts/python.exe scripts/agent_ctl.py node update --sha {OLD_SHA} "
            "--by claude --reason C-128-rollback-2026-10-21_0900Z-pre-rollback   (add --canary while the pinned node "
            "runs in canary mode)") in proc.stdout
    sb.tweak(labels={})   # an image without its revision label
    proc = lib(sb, "node_pin_hint C-128-rollback-x\n")
    assert "[deploy] AGENT_IMAGE_SHA=unknown" in proc.stdout and "has no revision label" in proc.stdout, _out(proc)
    assert "node update --sha <that commit> --by claude --reason C-128-rollback-x" in proc.stdout


@e2e
def test_the_rollback_drill_puts_each_agent_container_on_its_own_image(sb: Sandbox) -> None:
    """agent and agent-svc share jarvis-agent:hostinger. Their recorded images differ, so the rollback runs in two
    rounds (tag, then up, per service); each container ends on its own image, the shared tag names the agent's image
    again, the run prints the node update for the PC, and the printed undo goes forward again."""
    name = agent_deployed_state(sb)
    tools = sb.base / "tools" / "rollback.sh"
    proc = sb.run(tools, "--foreground", timeout=300)
    out = _out(proc)
    assert proc.returncode == 0, out
    state = sb.load_state()
    containers = state["containers"]
    assert containers["jarvis-agent"]["image"] == A_OLD and containers["jarvis-agent-svc"]["image"] == A_SVC
    assert state["tags"]["jarvis-agent:hostinger"] == A_OLD
    ups = [c for c in sb.calls() if " up -d " in c]
    assert [u.rsplit(" ", 1)[1] for u in ups] == ["agent", "agent-svc"], ups
    undo = sb.backups()[-1]
    assert (undo / "agent-volumes.tar.enc").exists()      # the pre-rollback backup sealed the agent data too
    assert f"[rollback] AGENT_IMAGE_SHA={OLD_SHA}" in out and name != undo.name
    assert (f"scripts/agent_ctl.py node update --sha {OLD_SHA} --by claude --reason C-128-rollback-{undo.name}"
            in out)


# --------------------------------------------------------------------------- deploy_from_pc.sh


FAKE_EXPORT_MANIFEST = r'''"""Fake scripts/desktop/export_manifest.py: records the call; exit 1 while STALE exists."""
import os
import sys

state = os.environ["FAKE_MANIFEST_DIR"]
with open(os.path.join(state, "manifest_calls.log"), "a", encoding="utf-8") as fh:
    fh.write("check %s strict=%s\n" % (" ".join(sys.argv[1:]), os.environ.get("JARVIS_MANIFEST_STRICT", "")))
if os.path.exists(os.path.join(state, "STALE")):
    print("desktop manifest STALE (1 differences; live: 3 tools)")
    sys.exit(1)
print("desktop manifest up to date (3 tools)")
'''
FAKE_AGENT_CTL = '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$FAKE_STATE_SH/agent_ctl_calls.log"\n' \
                 'exit "$(cat "$FAKE_STATE_SH/agent_ctl_rc" 2>/dev/null || echo 0)"\n'


@pytest.fixture(scope="module")
def agent_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shared module's small JARVIS-shaped repo, with the vps-brain services and a fake manifest exporter."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = _make_pc_repo(tmp_path_factory.mktemp("pc-agent") / "repo")
    _write(repo / "deploy" / "hostinger" / "docker-compose.yml", COMPOSE_AGENT)
    _write(repo / "deploy" / "hostinger" / "requirements-agent.txt", "livekit-agents==1.4.6\n")
    _write(repo / "deploy" / "hostinger" / "constraints-agent.txt", "torch==2.0.0+cpu\n")
    _write(repo / "scripts" / "desktop" / "export_manifest.py", FAKE_EXPORT_MANIFEST)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "vps brain")   # HEAD~1: an agent image built from it is another commit
    _write(repo / "README.md", "not shipped either\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a later commit")
    return repo


def _agent_pc_env(sb: Sandbox, **extra: str) -> dict[str, str]:
    agent_ctl = _write(sb.root / "bin" / "fake_agent_ctl", FAKE_AGENT_CTL, 0o755)
    return _pc_env(sb, JARVIS_PY=_posix(Path(sys.executable)), FAKE_MANIFEST_DIR=str(sb.state),
                   JARVIS_AGENT_CTL=sb.sh(agent_ctl), **extra)


def test_pc_dry_run_of_agent_runs_the_strict_manifest_check_and_plans_the_node_update(sb: Sandbox,
                                                                                      agent_repo: Path) -> None:
    sha = _git(agent_repo, "rev-parse", "--short", "HEAD")
    script = agent_repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    proc = sb.run(script, "--dry-run", "--services", "agent", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "--services 'agent'" in out and "manifest: JARVIS_MANIFEST_STRICT=1" in out
    assert (sb.state / "manifest_calls.log").read_text(encoding="utf-8") == "check --check strict=1\n"
    assert f"4. after a healthy agent deploy (run): {sb.sh(sb.root / 'bin' / 'fake_agent_ctl')} node update --sha " \
           f"<AGENT_IMAGE_SHA> --by claude --reason C-128-deploy-{sha}" in out
    assert (f"(another commit than {sha}: the strict manifest check runs on a scratch copy of it first; stale or "
            "missing: no node update, exit 6)") in out
    # the shipped tree: scripts/ (the backup and restore tools) and the deploy/hostinger requirements files
    for shipped in ("scripts/desktop/export_manifest.py", "scripts/ops/tool.sh",
                    "deploy/hostinger/requirements-agent.txt", "deploy/hostinger/constraints-agent.txt"):
        assert f"      {shipped}" in out, shipped
    assert "VERDICT: a real run would go ahead" in out and not sb.calls("ssh")
    assert "in a scratch copy of commit " + sha in out and not list(sb.tmp.glob("jarvis-manifest.*"))
    _write(sb.state / "STALE", "")
    proc = sb.run(script, "--dry-run", "--services", "agent", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 1 and f"VERDICT: a real run would REFUSE: the desktop manifest of commit {sha} is stale" \
        in out, out
    (sb.state / "manifest_calls.log").unlink()
    proc = sb.run(script, "--dry-run", "--services", "ui-bridge", env=_agent_pc_env(sb))   # bridge deploys as before
    assert proc.returncode == 0 and "manifest: not checked" in _out(proc), _out(proc)
    assert not (sb.state / "manifest_calls.log").exists()
    proc = sb.run(script, "--dry-run", "--services", "ui-bridge", env=_agent_pc_env(sb, JARVIS_MANIFEST_STRICT="1"))
    assert proc.returncode == 1 and "stale" in _out(proc), _out(proc)


def test_pc_agent_deploy_pins_the_node_to_the_agent_image(sb: Sandbox, agent_repo: Path) -> None:
    """C-128 step 2: after a healthy agent deploy the laptop's Desktop Node is pinned to the commit the agent image
    runs (AGENT_IMAGE_SHA from deploy.sh), in canary mode while the pinned node runs in it; a failed pin is exit 6."""
    sha = _git(agent_repo, "rev-parse", "--short", "HEAD")
    _write(sb.home / "key", "not a real key\n")
    _write(sb.state / "ssh_deploy_extra", f"[deploy] AGENT_IMAGE_SHA={sha}\n")
    script = agent_repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    calls = sb.state / "agent_ctl_calls.log"
    proc = sb.run(script, "--services", "agent", "--now", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0, out
    _, deploy = sb.calls("ssh")
    assert "--services 'agent' --now" in deploy
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"node update --sha {sha} --by claude --reason C-128-deploy-{sha}"]
    assert f"node: pinned to {sha}" in out and f"DONE: {sha} is deployed (agent)" in out
    calls.unlink()
    run = agent_repo / "run"
    try:
        _write(run / "node_current.json", '{"sha": "x", "run_mode": "canary"}\n')
        _write(sb.state / "ssh_deploy_extra", "[deploy] AGENT_IMAGE_SHA=unknown\n")
        proc = sb.run(script, "--services", "agent", env=_agent_pc_env(sb))
        assert proc.returncode == 0, _out(proc)
        assert "deploy.sh reported no agent image revision" in _out(proc)
        assert calls.read_text(encoding="utf-8").splitlines() == [
            f"node update --sha {sha} --by claude --reason C-128-deploy-{sha} --canary"]
    finally:
        shutil.rmtree(run, ignore_errors=True)
    calls.unlink()
    proc = sb.run(script, "--services", "agent", "--no-node-update", env=_agent_pc_env(sb))
    assert proc.returncode == 0 and "node: not updated (--no-node-update); run it on this PC:" in _out(proc)
    assert not calls.exists()
    _write(sb.state / "agent_ctl_rc", "1")
    proc = sb.run(script, "--services", "agent", env=_agent_pc_env(sb))
    assert proc.returncode == 6 and "'agent_ctl node update' failed (exit 1); run it again" in _out(proc), _out(proc)
    proc = sb.run(script, "--services", "ui-bridge", env=_agent_pc_env(sb))   # no node update for the bridge
    assert proc.returncode == 0 and calls.read_text(encoding="utf-8").count("node update") == 1, _out(proc)


def test_pc_agent_deploy_pins_the_node_even_when_the_pc_checks_fail(sb: Sandbox, agent_repo: Path) -> None:
    """deploy.sh exited 0, so jarvis-agent runs the new image; https://<domain>/ answering 502 for a moment from the PC
    must not leave the laptop's node pinned to the old commit (every changed laptop tool would answer
    MANIFEST_MISMATCH). Both steps run, the exit is 6, and each failure is listed."""
    sha = _git(agent_repo, "rev-parse", "--short", "HEAD")
    _write(sb.home / "key", "not a real key\n")
    _write(sb.state / "ssh_deploy_extra", f"[deploy] AGENT_IMAGE_SHA={sha}\n")
    codes = sb.state / "curl_codes.txt"
    _write(codes, codes.read_text(encoding="utf-8").replace("jarvis.test.invalid/ 401", "jarvis.test.invalid/ 502"))
    script = agent_repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    calls = sb.state / "agent_ctl_calls.log"
    proc = sb.run(script, "--services", "agent", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 6, out
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"node update --sha {sha} --by claude --reason C-128-deploy-{sha}"]
    assert f"node: pinned to {sha}" in out and "the deploy is live (deploy.sh exit 0), but a PC-side step failed" in out
    assert "  - the PC checks (check https://jarvis.test.invalid/vortex/health" in out and "DONE" not in out
    assert "  - the node update" not in out
    _write(sb.state / "agent_ctl_rc", "1")   # both fail: both are listed
    proc = sb.run(script, "--services", "agent", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 6 and "  - the PC checks" in out and "  - the node update (run the command" in out, out
    assert calls.read_text(encoding="utf-8").count("node update") == 2


def test_pc_attach_after_a_dropped_link_pins_the_node(sb: Sandbox, agent_repo: Path) -> None:
    """A dropped link (exit 5) prints the attach command and, for an agent deploy, the node update; '--attach' then
    ends the run like the deploy: the PC checks and the node update to the AGENT_IMAGE_SHA of the run's log (here an
    older commit than the run's, so its manifest is checked first), with the commit from the run id."""
    sha = _git(agent_repo, "rev-parse", "--short", "HEAD")
    image = _git(agent_repo, "rev-parse", "--short", "HEAD~1")
    _write(sb.home / "key", "not a real key\n")
    _write(sb.state / "ssh_deploy_rc", "255")
    script = agent_repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    calls = sb.state / "agent_ctl_calls.log"
    proc = sb.run(script, "--services", "agent", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 5, out
    match = re.search(r"Follow it: bash \S+ --attach ([0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]+)", out)
    assert match is not None, out
    run_id = match.group(1)
    assert f"once the run ends healthy, '--attach {run_id}' also pins the laptop's Desktop Node" in out
    assert f"node update --sha <AGENT_IMAGE_SHA> --by claude --reason C-128-deploy-{sha}" in out
    assert not calls.exists() and not sb.calls("curl")
    # the run went on and ended healthy on the VPS: its log names the services and the agent image's revision
    (sb.state / "ssh_deploy_rc").unlink()
    _write(sb.state / "attach_log", "[deploy] == preflight\n[deploy]   services: agent; nginx: no\n"
                                    "[deploy] BACKUP=2026-10-02_0901Z-pre-deploy-fake\n"
                                    f"[deploy] AGENT_IMAGE_SHA={image}\n[deploy] RESULT=ok\n")
    proc = sb.run(script, "--attach", run_id, env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0, out
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"node update --sha {image} --by claude --reason C-128-deploy-{sha}"]
    assert "RUN_RC=0" in out and f"node: pinned to {image}" in out and f"DONE: {sha} is deployed (agent)" in out
    assert f"in a scratch copy of commit {image} (git archive)" in out   # the commit the node is pinned to
    assert any("jarvis.test.invalid/vortex/health" in c for c in sb.calls("curl"))
    assert "rollback.sh 2026-10-02_0901Z-pre-deploy-fake" in out
    # dropped again while following: the hint again; --no-node-update only prints the call
    _write(sb.state / "ssh_down", "")
    proc = sb.run(script, "--attach", run_id, "--no-node-update", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 5 and "connection lost again" in out and "--attach " + run_id in out, out
    assert "node update --sha <AGENT_IMAGE_SHA>" in out and not sb.calls("curl")[2:]
    (sb.state / "ssh_down").unlink()
    proc = sb.run(script, "--attach", run_id, "--no-node-update", env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0 and "node: not updated (--no-node-update); run it on this PC:" in out, out
    assert calls.read_text(encoding="utf-8").count("node update") == 1
    # a bridge run followed again: no node update
    _write(sb.state / "attach_log", "[deploy]   services: ui-bridge; nginx: no\n[deploy] RESULT=ok\n")
    proc = sb.run(script, "--attach", run_id, env=_agent_pc_env(sb))
    out = _out(proc)
    assert proc.returncode == 0 and f"DONE: {sha} is deployed (ui-bridge)" in out, out
    assert "node update" not in out and "node:" not in out
    assert calls.read_text(encoding="utf-8").count("node update") == 1


FAKE_EXPORT_FROM_TREE = r'''"""Fake scripts/desktop/export_manifest.py that reads the tree it sits in: STALE when the
committed manifest (services/ai_core/desktop_manifest.json) is not the registry (services/ai_core/registry.txt)."""
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
state = os.environ["FAKE_MANIFEST_DIR"]
with open(os.path.join(state, "manifest_roots.log"), "a", encoding="utf-8") as fh:
    fh.write(root + "\n")


def read(name):
    with open(os.path.join(root, "services", "ai_core", name), encoding="utf-8") as fh:
        return fh.read().strip()


if os.path.exists(os.path.join(state, "NOTE")):
    print("export_manifest: note: uncommitted changes, so this checks the working tree rather than the commit (K-71): "
          "services/x.py")
registry, manifest = read("registry.txt"), read("desktop_manifest.json")
if registry != manifest:
    print("desktop manifest STALE (1 differences; live: %s, committed: %s)" % (registry, manifest))
    sys.exit(1)
print("desktop manifest up to date (%s)" % registry)
'''


def test_pc_manifest_check_reads_the_shipped_commit_not_the_working_tree(sb: Sandbox, tmp_path: Path) -> None:
    """K-71: the strict check compares the shipped commit's registry with the shipped commit's manifest (the builder
    bakes that one into the image), in a scratch copy of the commit. A manifest regenerated but not committed used to
    pass it while HEAD shipped the stale one; Cursor's uncommitted tool edits used to fail a consistent HEAD (and
    --allow-dirty, which Cursor's work in progress often needs, passes both into a deploy)."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = _make_pc_repo(tmp_path / "manifest-repo")
    _write(repo / "deploy" / "hostinger" / "docker-compose.yml", COMPOSE_AGENT)
    _write(repo / "scripts" / "desktop" / "export_manifest.py", FAKE_EXPORT_FROM_TREE)
    ai_core = repo / "services" / "ai_core"
    _write(ai_core / "registry.txt", "tools-v2\n")
    _write(ai_core / "desktop_manifest.json", "tools-v1\n")          # committed stale
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a registry change without its manifest")
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    script = repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    env = _agent_pc_env(sb)
    _write(ai_core / "desktop_manifest.json", "tools-v2\n")          # regenerated, not committed: HEAD ships v1
    proc = sb.run(script, "--dry-run", "--services", "agent", "--allow-dirty", env=env)
    out = _out(proc)
    assert proc.returncode == 1, out
    assert "desktop manifest STALE (1 differences; live: tools-v2, committed: tools-v1)" in out
    assert f"VERDICT: a real run would REFUSE: the desktop manifest of commit {sha} is stale" in out
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the manifest regenerated")
    sha = _git(repo, "rev-parse", "--short", "HEAD")
    _write(ai_core / "registry.txt", "tools-v3 (an uncommitted tool edit)\n")   # Cursor's work in progress
    proc = sb.run(script, "--dry-run", "--services", "agent", "--allow-dirty", env=env)
    out = _out(proc)
    assert proc.returncode == 0, out
    assert "desktop manifest up to date (tools-v2)" in out and "VERDICT: a real run would go ahead" in out
    assert f"in a scratch copy of commit {sha} (git archive)" in out
    roots = (sb.state / "manifest_roots.log").read_text(encoding="utf-8").splitlines()
    assert len(roots) == 2 and all("jarvis-manifest." in root for root in roots), roots   # never the repo itself
    assert not list(sb.tmp.glob("jarvis-manifest.*"))                                    # the scratch copies are gone
    assert "tools-v3" in (ai_core / "registry.txt").read_text(encoding="utf-8")          # the tree stays as it was
    _write(sb.state / "NOTE", "")   # an exporter that says it read uncommitted files did not check the commit
    proc = sb.run(script, "--dry-run", "--services", "agent", "--allow-dirty", env=env)
    out = _out(proc)
    assert proc.returncode == 1 and f"the desktop manifest of commit {sha} was not verified" in out, out


def test_pc_node_update_checks_the_manifest_of_the_commit_the_node_is_pinned_to(sb: Sandbox, tmp_path: Path) -> None:
    """C-128 review (low c): the strict check before the deploy runs on the shipped commit, while the node is pinned to
    AGENT_IMAGE_SHA, the commit the agent image was built from. When the two differ, that commit gets the same
    scratch-copy check before 'agent_ctl node update': a stale manifest there (or a commit this repo does not have)
    leaves the node as it is and ends in exit 6, --no-node-update too; a current one is pinned."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = _make_pc_repo(tmp_path / "pin-repo")
    _write(repo / "deploy" / "hostinger" / "docker-compose.yml", COMPOSE_AGENT)
    _write(repo / "scripts" / "desktop" / "export_manifest.py", FAKE_EXPORT_FROM_TREE)
    ai_core = repo / "services" / "ai_core"
    commits = {}
    for name, registry, committed in (("old", "tools-v1", "tools-v1"),       # an older image: its manifest current
                                      ("stale", "tools-v2", "tools-v1"),     # a registry change without its manifest
                                      ("head", "tools-v2", "tools-v2")):     # the shipped commit: current again
        _write(ai_core / "registry.txt", registry + "\n")
        _write(ai_core / "desktop_manifest.json", committed + "\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
        commits[name] = _git(repo, "rev-parse", "--short", "HEAD")
    sha = commits["head"]
    _write(sb.home / "key", "not a real key\n")
    script = repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    calls, roots = sb.state / "agent_ctl_calls.log", sb.state / "manifest_roots.log"

    def deploy_with_image(image_sha: str, *extra: str) -> tuple[int, str, int]:
        _write(sb.state / "ssh_deploy_extra", f"[deploy] AGENT_IMAGE_SHA={image_sha}\n")
        before = len(roots.read_text(encoding="utf-8").splitlines()) if roots.exists() else 0
        proc = sb.run(script, "--services", "agent", *extra, env=_agent_pc_env(sb))
        after = len(roots.read_text(encoding="utf-8").splitlines())
        return proc.returncode, _out(proc), after - before

    code, out, checks = deploy_with_image(commits["stale"])
    assert code == 6 and checks == 2, out              # the shipped commit before the deploy, the image's after it
    assert f"note: the agent image was built from {commits['stale']}, not {sha}" in out
    assert "desktop manifest STALE (1 differences; live: tools-v2, committed: tools-v1)" in out
    assert (f"node: NOT updated (the laptop's Desktop Node keeps its current commit): the desktop manifest of commit "
            f"{commits['stale']} is stale") in out
    assert f"  - the desktop manifest check of commit {commits['stale']}, which the agent image runs" in out
    assert "  - the node update (run the command" not in out and "DONE" not in out
    assert not calls.exists() and not list(sb.tmp.glob("jarvis-manifest.*"))
    code, out, checks = deploy_with_image(commits["stale"], "--no-node-update")
    assert code == 6 and checks == 2 and "node: NOT updated" in out and not calls.exists(), out
    code, out, checks = deploy_with_image("deadbee")   # an image built from a commit this repo does not have
    assert code == 6 and checks == 1, out
    assert "the desktop manifest of commit deadbee could not be checked: this repo has no such commit" in out
    assert not calls.exists()
    code, out, checks = deploy_with_image(commits["old"])
    assert code == 0 and checks == 2, out
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"node update --sha {commits['old']} --by claude --reason C-128-deploy-{sha}"]
    assert f"node: pinned to {commits['old']}" in out and f"DONE: {sha} is deployed (agent)" in out
    code, out, checks = deploy_with_image(sha)        # the usual case: the image is the shipped commit, checked once
    assert code == 0 and checks == 1 and f"node: pinned to {sha}" in out, out
    assert all("jarvis-manifest." in root for root in roots.read_text(encoding="utf-8").splitlines())


def test_pc_attach_checks_the_manifest_of_the_pinned_commit_even_for_a_run_it_did_not_start(sb: Sandbox,
                                                                                             tmp_path: Path) -> None:
    """C-128 review (low): '--attach' takes any run id, also one deploy.sh made on the VPS (<UTC>-manual-<hex>, whose
    commit comes from the log's 'sha' line), and pinned the node to AGENT_IMAGE_SHA without a manifest check whenever
    that equalled the run's commit. A commit shipped by a deploy that never ran the strict check (no agent named), its
    agent image built and deployed there by hand, so reached the laptop node unchecked. The node update now checks the
    commit it pins unless this same run already checked it (the shipped commit, before its deploy): every --attach."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = _make_pc_repo(tmp_path / "attach-repo")
    _write(repo / "deploy" / "hostinger" / "docker-compose.yml", COMPOSE_AGENT)
    _write(repo / "scripts" / "desktop" / "export_manifest.py", FAKE_EXPORT_FROM_TREE)
    ai_core = repo / "services" / "ai_core"
    commits = {}
    for name, registry, committed in (("stale", "tools-v2", "tools-v1"), ("head", "tools-v2", "tools-v2")):
        _write(ai_core / "registry.txt", registry + "\n")
        _write(ai_core / "desktop_manifest.json", committed + "\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
        commits[name] = _git(repo, "rev-parse", "--short", "HEAD")
    script = repo / "deploy" / "hostinger" / "deploy_from_pc.sh"
    calls, roots = sb.state / "agent_ctl_calls.log", sb.state / "manifest_roots.log"
    run_id = "20261021T090000Z-manual-ab12"   # deploy.sh's own run folder on the VPS (new_run_dir manual)

    def attach(commit: str) -> tuple[int, str, int]:
        _write(sb.state / "attach_log", "[deploy] == preflight\n"
                                        f"[deploy]   sha {commit} (now deployed: {commit}); staged code: no\n"
                                        "[deploy]   services: agent; nginx: no\n"
                                        "[deploy] BACKUP=2026-10-21_0900Z-pre-deploy-manual\n"
                                        f"[deploy] AGENT_IMAGE_SHA={commit}\n[deploy] RESULT=ok\n")
        before = len(roots.read_text(encoding="utf-8").splitlines()) if roots.exists() else 0
        proc = sb.run(script, "--attach", run_id, env=_agent_pc_env(sb))
        after = len(roots.read_text(encoding="utf-8").splitlines()) if roots.exists() else 0
        return proc.returncode, _out(proc), after - before

    code, out, checks = attach(commits["stale"])
    assert code == 6 and checks == 1, out
    assert "desktop manifest STALE (1 differences; live: tools-v2, committed: tools-v1)" in out
    assert (f"node: NOT updated (the laptop's Desktop Node keeps its current commit): the desktop manifest of commit "
            f"{commits['stale']} is stale") in out
    assert not calls.exists() and "DONE" not in out
    code, out, checks = attach(commits["head"])
    assert code == 0 and checks == 1, out
    assert f"in a scratch copy of commit {commits['head']} (git archive)" in out
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"node update --sha {commits['head']} --by claude --reason C-128-deploy-{commits['head']}"]
    assert f"node: pinned to {commits['head']}" in out and f"DONE: {commits['head']} is deployed (agent)" in out
    assert not list(sb.tmp.glob("jarvis-manifest.*"))


@pytest.mark.parametrize(("line", "skipped"), [
    ("JARVIS_BRAIN=pc", True),
    ("JARVIS_BRAIN=pc   # set by agent_ctl", True),   # compose strips the comment: still pc
    ("JARVIS_BRAIN=${BRAIN:-vps}", False),            # compose would resolve it; read here as neither: vps
    ("JARVIS_BRAIN: vps", False),                     # not an assignment compose reads: neither vps nor pc
    ("JARVIS_BRAIN=vps", False),
], ids=["pc", "pc-commented", "unexpanded-default", "yaml-style", "vps"])
def test_gate_only_skips_the_stopped_vps_brain_only_in_pc_mode(sb: Sandbox, line: str, skipped: bool) -> None:
    """K-72: in pc mode agent and agent-svc stay stopped, so a plain --gate-only does not fail on them. C-128 review (low
    b): only a JARVIS_BRAIN that reads exactly 'pc' skips them; one that reads as neither vps nor pc is checked as in
    vps mode (as the idle gate does), so a dead VPS brain never reads GATE=ok."""
    agent_sandbox(sb, brain="pc", agent="exited", svc="exited")
    env_file = sb.hostinger / ".env"
    _write(env_file, env_file.read_text(encoding="utf-8").replace("JARVIS_BRAIN=pc\n", line + "\n"))
    proc = sb.run(sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh", "--gate-only")
    out = _out(proc)
    if skipped:
        assert proc.returncode == 0 and "GATE=ok" in out, out
        assert "skip agent: stopped, as it stays in pc mode" in out
        assert "skip agent-svc: stopped, as it stays in pc mode" in out
    else:
        assert proc.returncode == 1 and "GATE=fail" in out, out
        assert "FAIL: agent: container not healthy" in out and "FAIL: agent-svc: container not healthy" in out
        assert "skip agent" not in out
    assert not sb.mutating_docker_calls() and not sb.backups()


def test_gate_only_fails_when_vps_mode_has_no_agent_container_at_all(sb: Sandbox) -> None:
    """C-128 review (low): a plain --gate-only built its list from the containers that exist, so with JARVIS_BRAIN=vps
    and no jarvis-agent at all (removed, or an 'up' that failed before it made one) the VPS brain was never checked and
    the run said GATE=ok. A vps-brain service that the compose file defines but that has no container now fails the
    gate in vps mode and for a brain that reads as neither vps nor pc; pc mode, and a compose file without those
    services (before C-126), stay as they were."""
    agent_sandbox(sb, brain="vps", agent=None, svc=None)
    script = sb.run_dir / "tree" / "deploy" / "hostinger" / "deploy.sh"
    env_file = sb.hostinger / ".env"
    base = env_file.read_text(encoding="utf-8").replace("JARVIS_BRAIN=vps\n", "")
    proc = sb.run(script, "--gate-only")
    out = _out(proc)
    assert proc.returncode == 1 and "GATE=fail (2 failure(s))" in out, out
    for svc in ("agent", "agent-svc"):
        assert (f"FAIL: {svc}: no jarvis-{svc} container while JARVIS_BRAIN reads vps: the VPS brain is not running"
                in out), out
    assert ".venv_312/Scripts/python.exe scripts/agent_ctl.py brain pc --now" in out
    _write(env_file, base + "JARVIS_BRAIN=${BRAIN:-vps}\n")   # reads as neither vps nor pc: gated as vps
    proc = sb.run(script, "--gate-only")
    out = _out(proc)
    assert proc.returncode == 1 and ("FAIL: agent: no jarvis-agent container while JARVIS_BRAIN reads as neither vps "
                                     "nor pc (gated as vps)") in out, out
    _write(env_file, base + "JARVIS_BRAIN=pc\n")              # pc mode: the VPS brain is not needed
    proc = sb.run(script, "--gate-only")
    out = _out(proc)
    assert proc.returncode == 0 and "GATE=ok" in out and "container while JARVIS_BRAIN" not in out, out
    _write(env_file, base + "JARVIS_BRAIN=vps\n")
    _write(sb.hostinger / "docker-compose.yml", COMPOSE_NEW)  # no vps-brain services in it: nothing can be missing
    proc = sb.run(script, "--gate-only")
    out = _out(proc)
    assert proc.returncode == 0 and "GATE=ok" in out and "container while JARVIS_BRAIN" not in out, out
    assert not sb.mutating_docker_calls() and not sb.backups()


def test_the_runbook_has_the_vps_brain_sections_and_no_destructive_command() -> None:
    text = (HOSTINGER / "RUNBOOK.md").read_text(encoding="utf-8")
    section = text[text.index("## VPS brain operations (C-128)"):text.index("## Secrets split (V2a, V2b, push)")]
    for heading in ("### Agent operations", "### Brain switch", "### Data restore", "### Monitoring thresholds",
                    "### Retention (report only)"):
        assert heading in section, heading
    for needle in ("deploy_from_pc.sh --dry-run --services agent", "JARVIS_MANIFEST_STRICT=1", "AGENT_IMAGE_SHA",
                   "rollback.sh <TS> --data", "restore_agent_data.py <TS> --dry-run", "backup_key.py keygen",
                   "backup_key.py export", "backup_key.py decrypt", "services.utils.agent_volumes", "ASIDE_MADE",
                   "brain pc --now", ".pre-restore-<UTC>/", "--with-media", "4 GiB", "15 GB"):
        assert needle in section, needle
    for forbidden in ("prune -", "system prune", "volume rm", "compose down", "rm -rf", "image rm", "--gen-key",
                      "--export-key", "backup_key.json"):
        assert forbidden not in section, forbidden
