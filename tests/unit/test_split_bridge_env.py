"""Plan task C-127 steps 2 and 7: scripts/ops/split_bridge_env.py splits the VPS env files without printing a value.

Only files under tmp_path are touched and ssh never runs: a local runner executes the exact program that would go to
the VPS (with its JSON-transported arguments) in this process, as in test_rotate_livekit_keys.py. Every value below is
a throwaway fake containing 'c127-fake', and every test checks that none of them reaches stdout or stderr.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("jarvis_ops_test_split_bridge_env",
                                              ROOT / "scripts" / "ops" / "split_bridge_env.py")
sbe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbe)

COMPOSE = (ROOT / "deploy" / "hostinger" / "docker-compose.yml").read_text(encoding="utf-8")
FAKE_MARKERS = ("c127-fake", "APIc127fake", "owner@example.invalid")
VPS_ENV = (
    "# JARVIS VPS env (pre-split)\n"
    "VORTEX_ENV=production\n"
    "LOG_LEVEL=INFO\n"
    "LOG_FILE=logs/jarvis.log\n"
    "USER_NAME=Owner\n"
    "VORTEX_SECURITY_TOKEN=c127-fake-legacy-token\n"
    "CONTROLLER_TOKEN=c127-fake-controller\n"
    "JARVIS_ENCRYPTION_KEY=c127-fake-fernet-key\n"
    "\n"
    "GOOGLE_API_KEY=c127-fake-google  # the Gemini key\n"
    "LIVEKIT_API_KEY=APIc127fake\n"
    "LIVEKIT_API_SECRET=c127-fake-livekit-secret\n"
    "export GROQ_API_KEY=c127-fake-groq\n"
    "EMAIL_USER=owner@example.invalid\n"
    "EMAIL_APP_PASSWORD='c127-fake mail'\n"
    "VITE_VORTEX_SECURITY_TOKEN=c127-fake-legacy-token\n"
    "JARVIS_BRAIN=pc\n"
)
BRIDGE_ENV = (
    "AGENT_TOKEN=c127-fake-agent-token\r\n"
    "BRIDGE_HMAC_SECRET=c127-fake-hmac\r\n"
    "SESSION_JWT_SECRET=c127-fake-jwt\r\n"
    "LIVEKIT_API_KEY=APIc127fake\r\n"
    "LIVEKIT_API_SECRET=c127-fake-livekit-secret\r\n"
    "VORTEX_SECURITY_TOKEN=c127-fake-legacy-token\r\n"
    "LEGACY_TOKEN_AUTH=1\r\n"
    "BRIDGE_EVENT_RETENTION_DAYS=0\r\n"
)
REMOVED = ["CONTROLLER_TOKEN", "EMAIL_APP_PASSWORD", "EMAIL_USER", "GOOGLE_API_KEY", "GROQ_API_KEY",
           "JARVIS_ENCRYPTION_KEY"]


class LocalRunner:
    """Stands in for gen_secrets.run_ssh: runs the program like the remote loader does, in this process. docker: a
    command list that replaces args['docker'] (the fake docker CLI)."""

    def __init__(self, code: int | None = None, err: str = "", docker: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.code, self.err, self.docker = code, err, docker

    def __call__(self, program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
        self.calls.append({"program": program, "args": args, "host": host, "key": key})
        if self.code is not None:
            return self.code, "", self.err
        args = json.loads(json.dumps(args))
        if self.docker is not None and "docker" in args:
            args["docker"] = self.docker
        out, code = io.StringIO(), 0
        with redirect_stdout(out):
            try:
                exec(compile(program, "jarvis-remote", "exec"),  # pylint: disable=exec-used
                     {"__name__": "jarvis_remote", "ARGS": args})
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), ""


@pytest.fixture
def vps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    folder = tmp_path / "hostinger"
    folder.mkdir()
    (folder / ".env").write_bytes(VPS_ENV.encode())
    (folder / "bridge.env").write_bytes(BRIDGE_ENV.encode())
    (folder / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    backups = tmp_path / "backups"
    monkeypatch.setattr(sbe.er, "BACKUP_ROOT", str(backups))
    return {"dir": folder, "backups": backups, "args": ["--vps-dir", folder.as_posix(), "--host", "h", "--ssh-key", "k"]}


def _assert_no_values(text: str) -> None:
    for marker in FAKE_MARKERS:
        assert marker not in text, f"a value reached the output ({marker})"


def _names(path: Path) -> list[str]:
    return sorted(name for name, _ in sbe.er.env_entries(path.read_text(encoding="utf-8")) if name)


def _run(vps: dict[str, Any], capsys: pytest.CaptureFixture, *extra: str, runner: Any = None) -> tuple[int, str]:
    code = sbe.main([*vps["args"], *extra], runner=runner or LocalRunner())
    out = capsys.readouterr()
    text = out.out + out.err
    _assert_no_values(text)
    return code, text


def test_dry_run_prints_names_only_and_writes_nothing(vps, capsys) -> None:
    before = {p.name: p.read_bytes() for p in vps["dir"].iterdir()}
    code, out = _run(vps, capsys, "--dry-run")
    assert code == 0, out
    assert f".env: 15 names; removes 6 (agent secrets): {', '.join(REMOVED)}" in out
    assert "docker-compose.yml interpolates GIT_SHA, JARVIS_BRAIN, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, " \
           "VORTEX_SECURITY_TOKEN (required: JARVIS_BRAIN)" in out
    assert "bridge.env: 8 names; adds from .env: LOG_FILE, LOG_LEVEL; drops: none" in out
    assert "dry run: nothing written" in out
    assert {p.name: p.read_bytes() for p in vps["dir"].iterdir()} == before
    assert not vps["backups"].exists()


def test_split_backs_up_then_writes_both_files(vps, capsys) -> None:
    code, out = _run(vps, capsys)
    assert code == 0, out
    env_text = (vps["dir"] / ".env").read_text(encoding="utf-8")
    bridge_text = (vps["dir"] / "bridge.env").read_bytes().decode("utf-8")
    assert not set(_names(vps["dir"] / ".env")) & set(sbe.AGENT_ONLY)
    # every other .env line stays byte for byte (the blank line and the comment too)
    expected_env = "".join(line for line in VPS_ENV.splitlines(True)
                           if not re.match(r"^(export )?(" + "|".join(REMOVED) + ")=", line))
    assert env_text == expected_env
    # bridge.env keeps its own CRLF lines and gains the two bridge-side settings, copied byte for byte
    assert bridge_text == (BRIDGE_ENV + "# copied from .env by scripts/ops/split_bridge_env.py (C-127)\r\n"
                           "LOG_FILE=logs/jarvis.log\nLOG_LEVEL=INFO\n")
    interp = sbe.er.compose_interpolations(COMPOSE)
    kept = sbe.er.env_values(env_text)
    assert all(kept.get(name) for name, kind in interp.items() if kind != "default")
    (backup,) = list(vps["backups"].iterdir())
    assert re.fullmatch(r"\d{8}T\d{6}Z-pre-env-split", backup.name)
    assert (backup / "hostinger.env").read_bytes() == VPS_ENV.encode()
    assert (backup / "bridge.env").read_bytes() == BRIDGE_ENV.encode()
    assert sorted(p.name for p in vps["dir"].iterdir()) == [".env", "bridge.env", "docker-compose.yml"]  # no temp
    if os.name != "nt":
        for name in (".env", "bridge.env"):
            assert (vps["dir"] / name).stat().st_mode & 0o777 == 0o600
    assert "written: .env, bridge.env" in out and f"backup: {backup.as_posix()}".replace("/", os.sep) in \
        out.replace("/", os.sep)


def test_a_second_run_changes_nothing(vps, capsys) -> None:
    assert _run(vps, capsys)[0] == 0
    files = {name: (vps["dir"] / name).read_bytes() for name in (".env", "bridge.env")}
    code, out = _run(vps, capsys)
    assert code == 0 and "nothing to change" in out
    assert {name: (vps["dir"] / name).read_bytes() for name in files} == files
    assert len(list(vps["backups"].iterdir())) == 1


@pytest.mark.parametrize(("change", "reason"), [
    (lambda d: (d / "docker-compose.yml").write_text(COMPOSE + "x-leak: ${GOOGLE_API_KEY}\n", encoding="utf-8"),
     "docker-compose.yml interpolates GOOGLE_API_KEY, an agent secret"),
    (lambda d: (d / ".env").write_text(VPS_ENV.replace("JARVIS_BRAIN=pc\n", ""), encoding="utf-8"),
     "needs a non-empty JARVIS_BRAIN in .env"),
    (lambda d: (d / "bridge.env").write_text(BRIDGE_ENV + "OPERATOR_NOTE=1\n", encoding="utf-8"),
     "bridge.env holds names on neither list, which writing it from the allow-list would lose: OPERATOR_NOTE"),
    (lambda d: (d / ".env").write_text(VPS_ENV + 'MULTI="c127-fake-start\nc127-fake-end"\n', encoding="utf-8"),
     ".env: MULTI spans several lines"),
    (lambda d: ((d / ".env").write_text(VPS_ENV.replace("LIVEKIT_API_SECRET=c127-fake-livekit-secret\n", ""),
                                        encoding="utf-8"),
                (d / "bridge.env").write_text(BRIDGE_ENV.replace("LIVEKIT_API_SECRET=c127-fake-livekit-secret\r\n",
                                                                 ""), encoding="utf-8")),
     "the bridge needs LIVEKIT_API_SECRET to start"),
])
def test_refusals_write_nothing(vps, capsys, change, reason) -> None:
    change(vps["dir"])
    before = {p.name: p.read_bytes() for p in vps["dir"].iterdir()}
    for extra in ((), ("--dry-run",)):
        code, out = _run(vps, capsys, *extra)
        assert code == 1, out
        assert reason in out and "nothing was written" in out
    assert {p.name: p.read_bytes() for p in vps["dir"].iterdir()} == before
    assert not vps["backups"].exists()


def test_keep_bridge_keeps_a_name_on_no_list(vps, capsys) -> None:
    (vps["dir"] / "bridge.env").write_text(BRIDGE_ENV + "OPERATOR_NOTE=1\nJARVIS_PC_AGENT_NAME=jarvis-pc\n",
                                           encoding="utf-8")
    code, out = _run(vps, capsys, "--keep-bridge", "OPERATOR_NOTE", "--keep-bridge", "JARVIS_PC_AGENT_NAME",
                     "--keep-bridge", "USER_NAME")
    assert code == 0, out
    names = _names(vps["dir"] / "bridge.env")
    assert "OPERATOR_NOTE" in names and "JARVIS_PC_AGENT_NAME" in names
    assert "USER_NAME" not in names   # a kept name stays where it is; only BRIDGE_NAMES are copied from .env


def test_agent_secrets_in_bridge_env_are_dropped_and_warnings_name_names_only(vps, capsys) -> None:
    (vps["dir"] / "bridge.env").write_text(BRIDGE_ENV.replace("SESSION_JWT_SECRET=c127-fake-jwt\r\n", "")
                                           + "TAVILY_API_KEY=c127-fake-tavily\n", encoding="utf-8")
    (vps["dir"] / ".env").write_text(VPS_ENV + "SOME_OTHER_TOKEN=c127-fake-other\n", encoding="utf-8")
    code, out = _run(vps, capsys)
    assert code == 0, out
    assert "drops: TAVILY_API_KEY" in out
    assert "bridge.env has no SESSION_JWT_SECRET" in out
    assert ".env keeps SOME_OTHER_TOKEN: they look like secrets and are on no list" in out
    assert "TAVILY_API_KEY" not in _names(vps["dir"] / "bridge.env")


def test_a_missing_bridge_env_is_created_from_the_allow_list(vps, capsys) -> None:
    (vps["dir"] / "bridge.env").unlink()
    code, out = _run(vps, capsys)
    assert code == 0, out
    assert "bridge.env: absent (created)" in out
    assert _names(vps["dir"] / "bridge.env") == ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LOG_FILE", "LOG_LEVEL",
                                                 "VORTEX_SECURITY_TOKEN"]


def test_a_byte_order_mark_is_kept_and_the_secret_on_the_first_line_removed(vps, capsys) -> None:
    """Review finding: with a UTF-8 BOM in front of the first line, its name was invisible: an agent secret there
    stayed in .env, the check after the write missed it too, and the run ended 'written'."""
    (vps["dir"] / ".env").write_bytes(("\ufeffTAVILY_API_KEY=c127-fake-tavily-first\n" + VPS_ENV).encode())
    (vps["dir"] / "bridge.env").write_bytes(("\ufeff" + BRIDGE_ENV).encode())
    code, out = _run(vps, capsys)
    assert code == 0, out
    assert "removes 7 (agent secrets): CONTROLLER_TOKEN, EMAIL_APP_PASSWORD, EMAIL_USER, GOOGLE_API_KEY, GROQ_API_KEY, " \
           "JARVIS_ENCRYPTION_KEY, TAVILY_API_KEY" in out
    env_raw = (vps["dir"] / ".env").read_bytes().decode("utf-8")
    assert env_raw.startswith("\ufeff# JARVIS VPS env (pre-split)\n") and "TAVILY_API_KEY" not in env_raw
    bridge_raw = (vps["dir"] / "bridge.env").read_bytes().decode("utf-8")
    assert bridge_raw.startswith("\ufeff" + BRIDGE_ENV)   # its first line (AGENT_TOKEN) kept byte for byte
    assert "bridge.env: 8 names" in out and "bridge.env has no AGENT_TOKEN" not in out


def test_the_ssh_step_failing_is_reported_without_values(vps, capsys) -> None:
    code, out = _run(vps, capsys, runner=LocalRunner(code=255, err="ssh: connect to host h port 22: refused\n"))
    assert code == 1 and "the VPS step failed (exit 255): ssh: connect to host h port 22: refused" in out


def test_usage_errors_never_echo_the_typed_text(capsys) -> None:
    for argv in (["--keep-bridge", "GOOGLE_API_KEY"], ["--keep-bridge", "a b"],
                 ["--check-bridge", "--keep-bridge", "X"], ["c127-fake-typed-value"]):
        with pytest.raises(SystemExit) as stop:
            sbe.parse_args(argv)
        assert stop.value.code == 2
    _assert_no_values(capsys.readouterr().err)


# --------------------------------------------------------------------------- step 7: the names inside jarvis-bridge

def _fake_docker(tmp_path: Path, env: list[str], code: int = 0) -> list[str]:
    script = tmp_path / "fake_docker.py"
    script.write_text("import json, sys\n"
                      "assert sys.argv[1:4] == ['inspect', '--format', '{{json .Config.Env}}'], sys.argv\n"
                      f"print(json.dumps({env!r}))\n"
                      f"raise SystemExit({code})\n", encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.mark.parametrize(("env", "code", "verdict"), [
    (["PATH=/usr/bin", "LIVEKIT_API_SECRET=c127-fake-livekit-secret", "AGENT_TOKEN=c127-fake-agent-token"], 0,
     "jarvis-bridge: 3 env names; no AI, search, weather, email, SIM or encryption key"),
    (["PATH=/usr/bin", "GOOGLE_API_KEY=c127-fake-google", "JARVIS_ENCRYPTION_KEY=c127-fake-fernet-key"], 1,
     "AGENT SECRET NAMES PRESENT: GOOGLE_API_KEY, JARVIS_ENCRYPTION_KEY"),
])
def test_check_bridge_reads_names_only(vps, capsys, tmp_path, env, code, verdict) -> None:
    runner = LocalRunner(docker=_fake_docker(tmp_path, env))
    got, out = _run(vps, capsys, "--check-bridge", runner=runner)
    assert got == code and verdict in out
    assert runner.calls[0]["args"]["container"] == "jarvis-bridge"


def test_check_bridge_reports_a_docker_failure(vps, capsys, tmp_path) -> None:
    runner = LocalRunner(docker=_fake_docker(tmp_path, [], code=1))
    code, out = _run(vps, capsys, "--check-bridge", runner=runner)
    assert code == 1 and "the check did not run" in out and "docker inspect jarvis-bridge exited 1" in out


def test_check_names_takes_names_and_cuts_values(capsys) -> None:
    clean = io.StringIO("PATH\nLIVEKIT_API_SECRET=c127-fake-livekit-secret\n# comment\n\nAGENT_TOKEN\n")
    assert sbe.main(["--check-names", "-"], stdin=clean) == 0
    assert sbe.main(["--check-names", "-"], stdin=io.StringIO("EMAIL_APP_PASSWORD=c127-fake-mail\nPATH\n")) == 1
    assert sbe.main(["--check-names", "-"], stdin=io.StringIO("c127-fake not a name\n")) == 2
    assert sbe.main(["--check-names", "-"], stdin=io.StringIO("\n")) == 2
    out = capsys.readouterr()
    _assert_no_values(out.out + out.err)
    assert "AGENT SECRET NAMES PRESENT: EMAIL_APP_PASSWORD" in out.out and "line 1 holds no env name" in out.err
    assert sbe.forbidden_bridge_names(["PATH", "HF_TOKEN", "CONTROLLER_TOKEN"]) == ["CONTROLLER_TOKEN", "HF_TOKEN"]


# --------------------------------------------------------------------------- python3.8 on the VPS

def test_the_split_program_runs_under_an_older_python(vps, tmp_path) -> None:
    """The VPS host runs the program with its own python3: run it, as the remote loader does, under an older
    interpreter when one is installed (the program is checked against the 3.8 grammar in test_env_remote.py)."""
    candidates = [[shutil.which(f"python3.{minor}")] for minor in (8, 9, 10, 11) if shutil.which(f"python3.{minor}")]
    launcher = shutil.which("py") if os.name == "nt" else None
    for minor in (8, 9, 10, 11):
        if launcher and subprocess.run([launcher, f"-3.{minor}", "-c", "pass"], capture_output=True,
                                       check=False).returncode == 0:
            candidates.append([launcher, f"-3.{minor}"])
    if not candidates:
        pytest.skip("no python3.8..3.11 interpreter installed")
    loader = sbe.gen_secrets.REMOTE_LOADER.split("python3 -c ", 1)[1].strip("'")
    remote = {"dir": vps["dir"].as_posix(), "dry_run": False, "rules": sbe.name_rules(),
              "backup_root": (tmp_path / "old-py-backups").as_posix(), "stamp": "20261014T120000Z",
              "label": sbe.BACKUP_LABEL}
    proc = subprocess.run([*candidates[0], "-c", loader], input=json.dumps({"program": sbe.split_program(),
                                                                            "args": remote}),
                          capture_output=True, text=True, check=False, timeout=120)
    assert proc.returncode == 0, proc.stderr[-500:]
    _assert_no_values(proc.stdout + proc.stderr)
    rows = sbe.er.json_rows(proc.stdout)
    assert rows[-1]["result"] == "written" and rows[0]["plan"]["remove"] == REMOVED
    assert not set(_names(vps["dir"] / ".env")) & set(sbe.AGENT_ONLY)
