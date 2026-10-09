"""C-126 (K-75): the VPS brain packaging, offline: Dockerfile.agent, the compose services, the media wrappers and
the ignore rules that keep env files out of images and commits.

Nothing here builds an image or talks to Docker. The compose file is read with PyYAML (anchors merged as compose
merges them), the Dockerfile as text, and the media wrappers run under bash (Git Bash on Windows) against fake
programs in tmp_path.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import functools
import importlib.metadata
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HOSTINGER = ROOT / "deploy" / "hostinger"
DOCKERFILE = HOSTINGER / "Dockerfile.agent"
COMPOSE = HOSTINGER / "docker-compose.yml"
WRAPPERS = ("ffmpeg", "ffprobe", "yt-dlp")
AGENT_SERVICES = ("agent", "agent-svc")
AGENT_VOLUMES = ("jarvis_agent_conversations:/app/conversations", "jarvis_agent_chroma:/app/chroma_db",
                 "jarvis_agent_data:/app/data", "jarvis_agent_backups:/app/backups", "jarvis_agent_logs:/app/logs")
# compose keys that would cap CPU, memory or processes, or put /tmp in memory (user decision 2026-10-01, D10)
LIMIT_KEYS = ("mem_limit", "memswap_limit", "mem_reservation", "mem_swappiness", "cpus", "cpu_shares", "cpu_quota",
              "cpu_period", "cpuset", "cpu_count", "cpu_percent", "pids_limit", "shm_size", "tmpfs", "deploy",
              "blkio_config")
SEARCH_LINE = "for dir in /usr/local/bin /usr/bin /bin; do"


def _instructions(text: str) -> list[str]:
    """The Dockerfile's instructions, continuation lines joined, comments dropped."""
    out: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not current and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        out.append(" ".join((current + line).split()))
        current = ""
    return out


DOCKER_TEXT = DOCKERFILE.read_text(encoding="utf-8")
STEPS = _instructions(DOCKER_TEXT)


def _index(prefix: str) -> int:
    matches = [i for i, step in enumerate(STEPS) if step.startswith(prefix)]
    assert matches, f"no Dockerfile instruction starts with {prefix!r}"
    return matches[0]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- Dockerfile.agent


def test_the_base_image_is_pinned_by_digest():
    froms = [step for step in STEPS if step.startswith("FROM ")]
    assert len(froms) == 1
    assert re.fullmatch(r"FROM python:3\.12-slim-trixie@sha256:[0-9a-f]{64}", froms[0])


def test_apt_installs_the_k75_packages_without_recommends():
    apt = STEPS[_index("RUN apt-get update")]
    assert "apt-get install -y --no-install-recommends ffmpeg nodejs curl ca-certificates tzdata" in apt
    assert "rm -rf /var/lib/apt/lists/*" in apt
    assert 'SHELL ["/bin/bash", "-o", "pipefail", "-c"]' in STEPS[:_index("RUN apt-get update")]


def test_the_runtime_user_is_10001_and_owns_only_the_data_folders():
    user = STEPS[_index("RUN groupadd")]
    assert "groupadd --gid 10001 jarvis" in user and "useradd --uid 10001 --gid 10001" in user
    users = [step for step in STEPS if step.startswith("USER ")]
    assert users == ["USER 10001"]
    assert _index("RUN groupadd") < _index("USER 10001")
    chown = STEPS[_index("RUN mkdir -p /app/conversations")]
    for folder in ("conversations", "chroma_db", "data", "backups", "logs", "snapshots", "Jarvis_Outputs",
                   "Jarvis_Shared", "run"):
        assert f"/app/{folder}" in chown.split("chown -R 10001:10001", 1)[1], folder
    assert "chmod 700 /app/data/identity" in chown
    assert "/app/src" not in chown and "/app/services" not in chown  # the code stays root-owned
    # the models get uid 10001 in their own bake layer (SpeechBrain writes its links into the ECAPA folder)
    assert "pretrained_models" not in chown
    assert STEPS[_index("RUN python scripts/models/download_voice_model.py")].endswith(
        "&& chown -R 10001:10001 /app/pretrained_models")
    assert re.search(r"^WORKDIR /app$", DOCKER_TEXT, re.M)


def test_no_later_layer_rewrites_the_baked_models():
    """Every layer after COPY src is built again with each code change. A chown or chmod of the models there would
    store all ~180 MB of them again in that layer, once per image and rollback tag kept on the VPS."""
    bake = _index("RUN python scripts/models/download_voice_model.py")
    assert bake < _index("COPY src /app/src")
    later = [step for step in STEPS[bake + 1:] if step.startswith("RUN ")]
    assert later and not [step for step in later if "pretrained_models" in step]


def test_the_code_comes_in_after_the_dependencies_and_the_models():
    copies = [step for step in STEPS if step.startswith(("COPY ", "ADD "))]
    assert not any(step.startswith("ADD ") for step in copies)
    assert not any(re.match(r"COPY \.\s", step) for step in copies), "never copy the whole context"
    for path in ("src", "services", "scripts"):
        assert f"COPY {path} /app/{path}" in STEPS
    bake = _index("RUN python scripts/models/download_voice_model.py")
    assert "python scripts/models/check_models.py --bake" in STEPS[bake]
    assert _index("COPY scripts/models/ /app/scripts/models/") < bake < _index("COPY src /app/src")
    assert _index("RUN pip install --no-deps") < bake
    assert "ENV HF_HOME=/app/pretrained_models/huggingface" in STEPS[:bake]
    assert _index("RUN python -m compileall") > _index("COPY scripts /app/scripts")


def test_the_media_wrappers_come_first_on_path():
    assert "COPY deploy/hostinger/agent-bin/ /usr/local/jarvis-bin/" in STEPS
    assert ("RUN chmod 0755 /usr/local/jarvis-bin/ffmpeg /usr/local/jarvis-bin/ffprobe /usr/local/jarvis-bin/yt-dlp"
            in STEPS)
    assert "ENV PATH=/usr/local/jarvis-bin:$PATH" in STEPS
    check = STEPS[_index("RUN node --version")]
    for tool in ("ffmpeg -hide_banner -version | sed -n 1p", "ffprobe -hide_banner -version | sed -n 1p",
                 "yt-dlp --version", "torch.__version__.endswith('+cpu')"):
        assert tool in check
    assert _index("ENV PATH=/usr/local/jarvis-bin:$PATH") < _index("RUN node --version")


def test_the_build_checks_import_and_offline_models_as_uid_10001():
    env = STEPS[_index("ENV PYTHONDONTWRITEBYTECODE=1")]
    for setting in ("HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "TZ=Asia/Karachi", "JARVIS_DESKTOP_MODE=remote",
                    "JARVIS_SHARED_DIR=/app/Jarvis_Shared"):
        assert setting in env
    check = STEPS[_index("RUN JARVIS_ROLE=agent python -c")]
    assert "import src.core.agent_runner; print('IMPORT_OK')" in check
    assert "python scripts/models/check_models.py --expect-uid 10001" in check
    assert "-mindepth 1 -delete" in check and "mkdir -m 700 /app/data/identity" in check  # volumes start empty
    assert _index("ENV PYTHONDONTWRITEBYTECODE=1") < _index("USER 10001") < _index("RUN JARVIS_ROLE=agent")


def test_healthcheck_cmd_and_the_git_sha_label():
    # exec form through 'env -i': no shell, and curl starts without the container's environment (no secret)
    assert 'HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD ["env", "-i", "curl", ' \
           '"-fsS", "http://127.0.0.1:8081/"]' in STEPS
    assert STEPS[_index("CMD ")] == 'CMD ["python", "-m", "src.core.agent", "start", "--drain-timeout", "20"]'
    assert not any(step.startswith("ENTRYPOINT") for step in STEPS)  # python itself is PID 1
    # GIT_SHA comes after the last RUN, so a new commit rebuilds only the metadata (K-75: label and ENV only)
    last_run = max(i for i, step in enumerate(STEPS) if step.startswith("RUN "))
    assert _index("ARG GIT_SHA=unknown") > last_run
    assert 'org.opencontainers.image.revision="${GIT_SHA}"' in STEPS[_index("LABEL ")]
    assert STEPS[-1] == "ENV GIT_SHA=${GIT_SHA}"
    assert not any(step.startswith("EXPOSE") for step in STEPS)


def test_no_env_file_or_secret_name_in_the_dockerfile():
    assert ".env" not in "\n".join(step for step in STEPS if step.startswith(("COPY", "ADD", "ENV", "ARG")))
    for name in ("GOOGLE_API_KEY", "LIVEKIT_API_SECRET", "JARVIS_ENCRYPTION_KEY", "DESKTOP_AGENT_SIGNING_KEY",
                 "HF_TOKEN", "EMAIL_APP_PASSWORD"):
        assert name not in DOCKER_TEXT


# ---------------------------------------------------------------- compose services


def test_the_agent_services_sit_behind_the_vps_brain_profile(compose):
    services = compose["services"]
    assert {"agent", "agent-svc"} <= set(services)
    for name in AGENT_SERVICES:
        assert services[name]["profiles"] == ["vps-brain"], name
    # everything else has no profile, so a plain 'up -d' starts exactly today's services and never the agents
    assert sorted(name for name, svc in services.items() if "profiles" not in svc) == ["frontend", "livekit",
                                                                                       "ui-bridge"]


@pytest.mark.parametrize("name", AGENT_SERVICES)
def test_the_shared_k75_settings(compose, name):
    svc = compose["services"][name]
    assert svc["image"] == "jarvis-agent:hostinger"
    assert "build" not in svc  # built only with the dedicated builder (D19), never by deploy.sh
    assert svc["container_name"] == {"agent": "jarvis-agent", "agent-svc": "jarvis-agent-svc"}[name]
    assert svc["user"] == "10001"
    assert svc["env_file"] == [{"path": ".env.agent", "required": True}]
    assert svc["depends_on"] == {"livekit": {"condition": "service_started"},
                                 "ui-bridge": {"condition": "service_healthy"}}
    assert svc["restart"] == "unless-stopped" and svc["stop_grace_period"] == "60s"
    assert svc["oom_score_adj"] == 500
    assert svc["security_opt"] == ["no-new-privileges:true"] and svc["cap_drop"] == ["ALL"]
    assert svc["logging"] == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    assert svc["networks"] == ["jarvis-net"]
    assert "ports" not in svc and "expose" not in svc and "network_mode" not in svc
    assert not [key for key in LIMIT_KEYS if key in svc], "no CPU/RAM/pids limit and no tmpfs (user decision)"


@pytest.mark.parametrize("name", AGENT_SERVICES)
def test_no_dumpable_process_in_the_container_holds_the_secrets(compose, name):
    """docker-init as PID 1 ('init: true') would keep the whole environment, every .env.agent secret, in a dumpable
    /proc/1/environ that any uid 10001 process can read (ffmpeg, yt-dlp, node). python is PID 1 instead and makes
    itself non-dumpable (harden_process, K-72). 'init: false' is explicit: an unset value takes dockerd's own default,
    which a daemon-wide "init": true turns on. Docker starts every healthcheck with that environment too: 'env -i'
    drops it before curl runs."""
    svc = compose["services"][name]
    assert svc["init"] is False and "entrypoint" not in svc
    assert svc["command"][:2] == ["python", "-m"]
    assert svc["healthcheck"]["test"][:4] == ["CMD", "env", "-i", "curl"]


def test_the_pid_1_worker_hardens_itself_and_drains_on_sigterm():
    """With no init the worker is PID 1, and the kernel hands PID 1 only the signals it handles: docker stop's
    SIGTERM needs the LiveKit CLI's handler (it drains), and the secrets stay private once harden_process() ran at
    import (agent-svc, C-125, has to do both itself)."""
    runner = (ROOT / "src" / "core" / "agent_runner.py").read_text(encoding="utf-8")
    assert re.search(r"^net_guard\.harden_process\(\)", runner, re.M)
    try:
        cli = Path(importlib.metadata.distribution("livekit-agents").locate_file("livekit/agents/cli/cli.py"))
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("livekit-agents is not installed")
    text = cli.read_text(encoding="utf-8")
    assert re.search(r"HANDLED_SIGNALS = \([^)]*signal\.SIGTERM", text)
    run_worker = text.split("def _run_worker(", 1)[1].split("\ndef ", 1)[0]
    assert "signal.signal(sig, _handle_exit)" in run_worker and "server.drain()" in run_worker


@pytest.mark.parametrize("name", AGENT_SERVICES)
def test_the_environment_per_k72_and_no_secret_or_git_sha(compose, name):
    env = compose["services"][name]["environment"]
    assert env["JARVIS_ROLE"] == {"agent": "agent", "agent-svc": "svc"}[name]
    assert env["JARVIS_BRAIN"].startswith("${JARVIS_BRAIN:?") and env["JARVIS_BRAIN"].endswith("}")
    expected = {"JARVIS_DESKTOP_MODE": "remote", "TZ": "Asia/Karachi", "USER_CITY": "Lahore",
                "LIVEKIT_URL": "ws://jarvis-livekit:7880", "UI_BRIDGE_URL": "http://jarvis-bridge:5002",
                "JARVIS_ROOM_NAME": "jarvis-hybrid", "JARVIS_SHARED_DIR": "/app/Jarvis_Shared",
                "JARVIS_MEDIA_PRIORITY": "normal", "JARVIS_NET_GUARD": "1", "JARVIS_REMINDER_EMAIL_AFTER_MIN": "0",
                "HF_HUB_OFFLINE": "1", "VORTEX_ENV": "production"}
    assert {key: env.get(key) for key in expected} == expected
    assert env["JARVIS_USER_ID"]
    assert env["LOG_FILE"] == {"agent": "logs/jarvis_agent.log", "agent-svc": "logs/jarvis_agent_svc.log"}[name]
    assert "GIT_SHA" not in env  # the image's label and ENV only (K-75)
    secretish = [key for key in env if re.search(r"(KEY|TOKEN|SECRET|PASSWORD|HASH|HASHES)$", key)]
    assert not secretish, "secrets come only from .env.agent"  # (JARVIS_CTX_TRIGGER_TOKENS is a count)


def test_the_agent_worker_settings(compose):
    env = compose["services"]["agent"]["environment"]
    assert {key: env[key] for key in ("JARVIS_AGENT_NAME", "AGENT_DISPATCH_MODE", "JARVIS_IDLE_PROCS",
                                      "JARVIS_PREWARM_TIMEOUT", "JARVIS_LOAD_THRESHOLD", "JARVIS_JOB_MEMORY_WARN_MB",
                                      "JARVIS_SHUTDOWN_PROC_TIMEOUT", "JARVIS_IDLE_CLOSE_MIN",
                                      "JARVIS_CTX_TRIGGER_TOKENS")} == {
        "JARVIS_AGENT_NAME": "jarvis", "AGENT_DISPATCH_MODE": "on_demand", "JARVIS_IDLE_PROCS": "2",
        "JARVIS_PREWARM_TIMEOUT": "120", "JARVIS_LOAD_THRESHOLD": "inf", "JARVIS_JOB_MEMORY_WARN_MB": "3000",
        "JARVIS_SHUTDOWN_PROC_TIMEOUT": "20", "JARVIS_IDLE_CLOSE_MIN": "30", "JARVIS_CTX_TRIGGER_TOKENS": "24000"}
    assert "JARVIS_IDLE_PROCS" not in compose["services"]["agent-svc"]["environment"]


def test_commands_healthchecks_and_volumes(compose):
    agent, svc = compose["services"]["agent"], compose["services"]["agent-svc"]
    assert agent["command"] == ["python", "-m", "src.core.agent", "start", "--drain-timeout", "20"]
    assert svc["command"] == ["python", "-m", "src.core.agent_services"]
    assert agent["healthcheck"]["test"] == ["CMD", "env", "-i", "curl", "-fsS", "http://127.0.0.1:8081/"]
    assert svc["healthcheck"]["test"] == ["CMD", "env", "-i", "curl", "-fsS", "http://127.0.0.1:8083/health"]
    assert agent["volumes"] == [*AGENT_VOLUMES, "jarvis_outputs:/app/Jarvis_Outputs",
                                "jarvis_shared:/app/Jarvis_Shared"]
    assert svc["volumes"] == [*AGENT_VOLUMES, "jarvis_agent_snapshots:/app/snapshots",
                              "jarvis_outputs:/app/Jarvis_Outputs:ro", "jarvis_shared:/app/Jarvis_Shared:ro"]
    for entry in AGENT_VOLUMES + ("jarvis_agent_snapshots:/app/snapshots",):
        volume = entry.split(":", 1)[0]
        assert compose["volumes"][volume] == {"name": volume}


def test_no_new_published_port_and_the_existing_ones_unchanged(compose):
    published = {name: svc.get("ports") for name, svc in compose["services"].items() if svc.get("ports")}
    assert published == {"livekit": ["7880:7880", "7881:7881", "50000-50050:50000-50050/udp"],
                         "ui-bridge": ["5101:5001"], "frontend": ["127.0.0.1:5100:3000"]}


def test_the_bridge_keeps_its_settings_and_reads_the_brain_from_compose(compose):
    bridge = compose["services"]["ui-bridge"]
    assert bridge["env_file"] == [".env", {"path": "bridge.env", "required": False}]
    # The switch's value (K-72), the same line as the agents': it stays when bridge.env replaces .env (V2b, C-127),
    # and compose refuses to run without it
    brain = bridge["environment"]["JARVIS_BRAIN"]
    assert brain == compose["services"]["agent"]["environment"]["JARVIS_BRAIN"]
    assert brain.startswith("${JARVIS_BRAIN:?") and brain.endswith("}")
    assert bridge["environment"]["GIT_SHA"] == "${GIT_SHA:-unknown}"  # deploy.sh and rollback.sh export it
    assert bridge["depends_on"] == ["livekit"] and bridge["oom_score_adj"] == 300
    assert "jarvis_agent_snapshots:/app/snapshots:ro" in bridge["volumes"]


# ---------------------------------------------------------------- env file templates and ignore rules


def _names(path: Path) -> list[str]:
    return [line.split("=", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_the_agent_env_template_lists_names_only():
    path = HOSTINGER / ".env.agent.example"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert lines and all(re.fullmatch(r"[A-Z][A-Z0-9_]*=", line) for line in lines), "names only, no values"
    names = _names(path)
    # K-47 v4's .env.agent names
    for name in ("GOOGLE_API_KEY", "WEATHER_API_KEY", "GOOGLE_SEARCH_API_KEY", "SEARCH_ENGINE_ID", "TAVILY_API_KEY",
                 "OPENROUTER_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "EMAIL_USER", "EMAIL_APP_PASSWORD",
                 "SIM_LOOKUP_API_URL", "SIM_LOOKUP_API_KEY", "POLLINATIONS_TOKEN", "JARVIS_ENCRYPTION_KEY",
                 "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DESKTOP_AGENT_SIGNING_KEY", "DESKTOP_AGENT_PUBLIC_KEY",
                 "DESKTOP_PC_PUBLIC_KEY", "BACKUP_RECIPIENT_PUBLIC_KEY"):
        assert name in names, name
    assert len(names) == len(set(names))
    assert "JARVIS_BRAIN" not in names and "JARVIS_ROLE" not in names  # compose settings, not secrets


def test_the_compose_env_template_names_the_brain_switch():
    names = _names(HOSTINGER / ".env.example")
    assert {"JARVIS_BRAIN", "COMPOSE_PROFILES", "JARVIS_PC_AGENT_NAME"} <= set(names)


def test_dockerignore_keeps_env_files_in_every_folder_out():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "**/.env*" in lines and "**/*.env" in lines
    assert not any(line.startswith(("deploy", "!deploy", "scripts", "src", "services")) for line in lines)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize(("path", "ignored"), [
    ("deploy/hostinger/.env.agent", True),
    ("deploy/hostinger/.env.agent.tmp", True),
    ("deploy/hostinger/bridge.env", True),
    ("somewhere/else/bridge.env", True),
    ("deploy/hostinger/.env.agent.example", False),
    ("deploy/hostinger/.env.example", False),
    ("deploy/hostinger/requirements-agent.txt", False),
    ("deploy/hostinger/agent-bin/ffmpeg", False),
])
def test_gitignore_treats_the_agent_and_bridge_env_files_as_secrets(path, ignored):
    proc = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT, capture_output=True,
                          check=False)
    assert proc.returncode == (0 if ignored else 1), proc.stderr


# ---------------------------------------------------------------- the media wrappers


def _find_bash() -> str | None:
    """Git's usr/bin/bash.exe on Windows (as tests/unit/test_deploy_tooling.py finds it), else bash on PATH."""
    if os.name != "nt":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        candidates += [parent / "usr" / "bin" / "bash.exe" for parent in Path(git).resolve().parents]
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe")
    for cand in candidates:
        if cand.is_file() and "system32" not in str(cand).lower():
            return str(cand)
    return None


BASH = _find_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="bash (Git Bash on Windows) is not available")


@functools.lru_cache(maxsize=None)
def _posix(path: Path) -> str:
    """A path as bash sees it (cygpath on Windows)."""
    if os.name != "nt":
        return str(path)
    assert BASH is not None
    cygpath = Path(BASH).parent / "cygpath.exe"
    return subprocess.run([str(cygpath), "-u", str(Path(path).resolve())], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_the_three_wrappers_are_one_lf_script_that_never_searches_its_own_folder():
    bodies = {name: (HOSTINGER / "agent-bin" / name).read_bytes() for name in WRAPPERS}
    assert len(set(bodies.values())) == 1
    body = bodies["ffmpeg"].decode("ascii")
    assert body.startswith("#!/bin/sh\n") and "\r" not in body
    # fixed folders only, never PATH or /usr/local/jarvis-bin: a wrapper cannot end up running itself
    assert [line.strip() for line in body.splitlines() if line.strip().startswith("for ")] == [SEARCH_LINE]
    assert "$PATH" not in body
    assert 'exec nice -n 10 ionice -c 3 -t "$real" "$@"' in body and 'exec "$real" "$@"' in body


def _write_script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("#!/bin/sh\n" + body).encode("ascii"))
    os.chmod(path, 0o755)
    return path


NICE_FAKE = 'printf "NICE %s\\n" "$*"; shift 2; exec "$@"\n'      # logs, then runs the rest (after -n 10)
IONICE_FAKE = 'printf "IONICE %s\\n" "$*"; shift 3; exec "$@"\n'  # logs, then runs the rest (after -c 3 -t)
REAL_FAKE = ('printf "REAL %s" "${0##*/}"; for a in "$@"; do printf " [%s]" "$a"; done; printf "\\n"; '
             'exit "${FAKE_EXIT:-0}"\n')


class WrapperBox:
    """Copies of the wrapper that search tmp_path/root/{usr/local/bin,usr/bin,bin} instead of the real folders, fake
    programs there, and two PATHs: 'fakebin' with fake nice and ionice, 'nofake' with nice only. Only the fakes are
    on PATH, so a real ionice of this machine never answers."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "root"
        folders = [self.root / "usr" / "local" / "bin", self.root / "usr" / "bin", self.root / "bin"]
        for folder in folders:
            folder.mkdir(parents=True)
        original = (HOSTINGER / "agent-bin" / "ffmpeg").read_text(encoding="ascii")
        boxed = original.replace(SEARCH_LINE, "for dir in " + " ".join(_posix(f) for f in folders) + "; do")
        assert boxed != original
        self.bin_dir = tmp_path / "jarvis-bin"
        self.bin_dir.mkdir()
        for name in WRAPPERS:
            (self.bin_dir / name).write_bytes(boxed.encode("ascii"))
        _write_script(self.root / "usr" / "bin" / "ffmpeg", REAL_FAKE)
        _write_script(self.root / "usr" / "bin" / "ffprobe", REAL_FAKE)
        _write_script(self.root / "usr" / "local" / "bin" / "yt-dlp", REAL_FAKE)
        _write_script(self.root / "usr" / "bin" / "yt-dlp", 'echo "WRONG yt-dlp"; exit 99\n')  # /usr/local/bin first
        self.fakebin = tmp_path / "fakebin"
        _write_script(self.fakebin / "nice", NICE_FAKE)
        _write_script(self.fakebin / "ionice", IONICE_FAKE)
        self.nofake = tmp_path / "nofake"
        _write_script(self.nofake / "nice", NICE_FAKE)

    def run(self, name: str, *args: str, priority: str | None = None, path: Path | None = None,
            fake_exit: int = 0) -> subprocess.CompletedProcess:
        keep = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "COMSPEC", "PATHEXT")
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        env.update(PATH=str(path or self.fakebin), LC_ALL="C", FAKE_EXIT=str(fake_exit))
        if priority is not None:
            env["JARVIS_MEDIA_PRIORITY"] = priority
        assert BASH is not None
        return subprocess.run([BASH, _posix(self.bin_dir / name), *args], env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, check=False)


@pytest.fixture
def box(tmp_path) -> WrapperBox:
    return WrapperBox(tmp_path)


@needs_bash
def test_normal_priority_runs_the_real_program_unchanged(box):
    proc = box.run("ffmpeg", "-i", "a file.webm", "-y", fake_exit=3)
    assert proc.returncode == 3, proc.stderr  # the program's own exit code
    assert proc.stdout.splitlines() == ["REAL ffmpeg [-i] [a file.webm] [-y]"]
    for priority in ("normal", "", "fast"):
        proc = box.run("ffprobe", "-v", "error", priority=priority)
        assert proc.returncode == 0 and proc.stdout.splitlines() == ["REAL ffprobe [-v] [error]"], priority


@needs_bash
def test_low_priority_runs_under_nice_and_idle_ionice(box):
    proc = box.run("yt-dlp", "--version", priority="low")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("NICE -n 10 ionice -c 3 -t ") and lines[0].endswith("/usr/local/bin/yt-dlp --version")
    assert lines[1].startswith("IONICE -c 3 -t ") and lines[2] == "REAL yt-dlp [--version]"


@needs_bash
def test_low_priority_without_ionice_uses_nice_only(box):
    proc = box.run("ffmpeg", "-version", priority="LOW", path=box.nofake)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("NICE -n 10 ") and lines[0].endswith("/usr/bin/ffmpeg -version")
    assert lines[1] == "REAL ffmpeg [-version]" and len(lines) == 2


@needs_bash
def test_a_missing_program_exits_127_with_a_message(box):
    (box.root / "usr" / "bin" / "ffprobe").unlink()
    proc = box.run("ffprobe", "-version")
    assert proc.returncode == 127
    assert "jarvis-bin: no ffprobe in /usr/local/bin, /usr/bin or /bin" in proc.stderr and proc.stdout == ""
