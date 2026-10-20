"""Plan task C-127 step 3: scripts/ops/push_agent_env.py fills the VPS .env.agent and makes the agent link key on the
VPS, comparing values by HMAC digest only and never printing a secret.

Only files under tmp_path are touched; ssh and docker never run: a local runner executes the exact program that would
go to the VPS in this process, and a fake docker CLI runs the real scripts/ops/gen_desktop_keys.py against the tmp
folder (the container's /envdir mount). Every value is a throwaway fake containing 'c127-fake', and every test checks
that none of them (nor the generated private key) reaches stdout or stderr.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("jarvis_ops_test_push_agent_env",
                                              ROOT / "scripts" / "ops" / "push_agent_env.py")
pae = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pae)

from services.utils import desktop_protocol as dp  # noqa: E402  pylint: disable=wrong-import-position

FAKE_MARKERS = ("c127-fake", "owner@example.invalid", "APIc127")
PC_ENV = (
    "# PC root .env\r\n"
    "GOOGLE_API_KEY=c127-fake-google\r\n"
    "GROQ_API_KEY=c127-fake-groq\r\n"
    "EMAIL_USER=owner@example.invalid\r\n"
    "EMAIL_APP_PASSWORD='c127-fake mail pass'\r\n"
    "JARVIS_ENCRYPTION_KEY=c127-fake-fernet\r\n"
    "AGENT_TOKEN=c127-fake-agent-token\r\n"
    "BRIDGE_HMAC_SECRET=c127-fake-hmac\r\n"
    "JARVIS_TEXT_MODEL=c127-fake-model-name\r\n"
    "LIVEKIT_API_KEY=APIc127pcpair\r\n"
    "LIVEKIT_API_SECRET=c127-fake-pc-livekit\r\n"
    "JARVIS_DOC_DIRS=C:/c127-fake-docs\r\n"
)
COMPOSE_ENV = "LIVEKIT_API_KEY=APIc127main\nLIVEKIT_API_SECRET=c127-fake-main-livekit\nJARVIS_BRAIN=pc\n"
BRIDGE_ENV = "AGENT_TOKEN=c127-fake-agent-token\nBRIDGE_HMAC_SECRET=c127-fake-hmac\nLEGACY_TOKEN_AUTH=1\n"


class LocalRunner:
    """Stands in for gen_secrets.run_ssh (see test_split_bridge_env.py); docker replaces args['docker']."""

    def __init__(self, docker: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.docker = docker

    def __call__(self, program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
        self.calls.append({"program": program, "args": args})
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
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    vps = tmp_path / "hostinger"
    vps.mkdir()
    (vps / ".env").write_text(COMPOSE_ENV, encoding="utf-8")
    (vps / "bridge.env").write_text(BRIDGE_ENV, encoding="utf-8")
    (vps / ".env.agent").write_bytes(b"")   # the C-126 placeholder
    pc = tmp_path / "pc.env"
    pc.write_bytes(PC_ENV.encode())
    monkeypatch.setattr(pae.er, "BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("FAKE_NO_IMAGE", raising=False)
    return {"vps": vps, "pc": pc, "backups": tmp_path / "backups", "tmp": tmp_path,
            "args": ["--vps-dir", vps.as_posix(), "--env-file", str(pc), "--host", "h", "--ssh-key", "k"]}


def _run(env: dict[str, Any], capsys: pytest.CaptureFixture, *extra: str, runner: Any = None,
         secrets: tuple[str, ...] = ()) -> tuple[int, str]:
    code = pae.main([*env["args"], *extra], runner=runner or LocalRunner())
    out = capsys.readouterr()
    text = out.out + out.err
    for marker in (*FAKE_MARKERS, *secrets):
        assert marker not in text, f"a value reached the output ({marker[:12]}...)"
    return code, text


def _values(path: Path) -> dict[str, str]:
    return pae.er.env_values(path.read_text(encoding="utf-8"))


def test_dry_run_names_the_changes_and_writes_nothing(env, capsys) -> None:
    runner = LocalRunner()
    code, out = _run(env, capsys, "--dry-run", runner=runner)
    assert code == 0, out
    for name in ("GOOGLE_API_KEY", "EMAIL_APP_PASSWORD", "AGENT_TOKEN", "LIVEKIT_API_SECRET", "JARVIS_TEXT_MODEL"):
        assert f"{name}: add" in out
    assert "JARVIS_DOC_DIRS" not in out.split("not on this PC")[0]
    assert (env["vps"] / ".env.agent").read_bytes() == b"" and not env["backups"].exists()
    assert runner.calls[0]["args"]["values"] == {}   # a dry run sends no value


def test_push_writes_the_allow_list_and_compares_by_digest(env, capsys) -> None:
    runner = LocalRunner()
    code, out = _run(env, capsys, runner=runner)
    assert code == 0, out
    agent = _values(env["vps"] / ".env.agent")
    pc = pae.env_set.EnvFile(env["pc"])
    for name in ("GOOGLE_API_KEY", "GROQ_API_KEY", "EMAIL_USER", "EMAIL_APP_PASSWORD", "JARVIS_ENCRYPTION_KEY",
                 "AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "JARVIS_TEXT_MODEL"):
        assert agent[name] == pc.value(name), name
    # the main LiveKit pair comes from the compose .env on the VPS, never the PC's own pair
    assert agent["LIVEKIT_API_KEY"] == "APIc127main" and agent["LIVEKIT_API_SECRET"] == "c127-fake-main-livekit"
    assert "LIVEKIT_API_SECRET" not in runner.calls[0]["args"]["values"]
    assert "JARVIS_DOC_DIRS" not in agent
    for line in ("GOOGLE_API_KEY: equal to this PC", "LIVEKIT_API_SECRET: equal to the compose .env",
                 "AGENT_TOKEN: equal to bridge.env", "BRIDGE_HMAC_SECRET: equal to bridge.env",
                 "DESKTOP_AGENT_SIGNING_KEY: not set yet (--keys)"):
        assert line in out
    (backup,) = list(env["backups"].iterdir())
    assert backup.name.endswith("-pre-agent-env") and (backup / "env.agent").read_bytes() == b""
    if os.name != "nt":
        assert (env["vps"] / ".env.agent").stat().st_mode & 0o777 == 0o600
    # a check afterwards: digests only, nothing sent or written
    before = (env["vps"] / ".env.agent").read_bytes()
    check_runner = LocalRunner()
    code, out = _run(env, capsys, "--check", runner=check_runner)
    assert code == 0 and "GOOGLE_API_KEY: equal to this PC" in out
    assert check_runner.calls[0]["args"]["values"] == {} and (env["vps"] / ".env.agent").read_bytes() == before


def test_push_keeps_every_other_line_and_a_second_push_changes_nothing(env, capsys) -> None:
    (env["vps"] / ".env.agent").write_text("# made on the VPS\nDESKTOP_AGENT_SIGNING_KEY=c127-fake-signing\n"
                                           "GOOGLE_API_KEY=c127-fake-stale\n", encoding="utf-8")
    assert _run(env, capsys)[0] == 0
    text = (env["vps"] / ".env.agent").read_text(encoding="utf-8")
    assert text.startswith("# made on the VPS\nDESKTOP_AGENT_SIGNING_KEY=c127-fake-signing\n"
                           "GOOGLE_API_KEY=c127-fake-google\n")
    code, out = _run(env, capsys)
    assert code == 0 and ".env.agent: unchanged" in out
    assert len(list(env["backups"].iterdir())) == 1


def test_a_bridge_token_that_differs_or_is_missing_refuses_the_push(env, capsys) -> None:
    """K-47 v36: .env.agent's notify credentials must equal bridge.env's; the VPS compares them before writing."""
    (env["vps"] / "bridge.env").write_text(BRIDGE_ENV.replace("c127-fake-agent-token", "c127-fake-other-token"),
                                           encoding="utf-8")
    code, out = _run(env, capsys)
    assert code == 1 and "REFUSED: AGENT_TOKEN differs from bridge.env" in out and "nothing was written" in out
    assert (env["vps"] / ".env.agent").read_bytes() == b""
    (env["vps"] / "bridge.env").write_text("LEGACY_TOKEN_AUTH=1\n", encoding="utf-8")
    code, out = _run(env, capsys)
    assert code == 1 and "AGENT_TOKEN is not set in" in out and "BRIDGE_HMAC_SECRET is not set in" in out
    assert (env["vps"] / ".env.agent").read_bytes() == b""
    # a check still reports a mismatch that arose later (names only)
    (env["vps"] / "bridge.env").write_text(BRIDGE_ENV, encoding="utf-8")
    assert _run(env, capsys)[0] == 0
    (env["vps"] / "bridge.env").write_text(BRIDGE_ENV.replace("c127-fake-hmac", "c127-fake-rotated-hmac"),
                                           encoding="utf-8")
    code, out = _run(env, capsys, "--check")
    assert code == 1 and "BRIDGE_HMAC_SECRET: NOT equal to bridge.env (the agent's notifies would be dropped)" in out


@pytest.mark.parametrize(("edit", "reason"), [
    (lambda t: t.replace("JARVIS_ENCRYPTION_KEY=c127-fake-fernet\r\n", ""), "JARVIS_ENCRYPTION_KEY missing"),
    (lambda t: t.replace("GROQ_API_KEY=c127-fake-groq", "GROQ_API_KEY=\"c127-fake-it's\""), "GROQ_API_KEY: a value"),
])
def test_pc_side_refusals_send_nothing(env, capsys, edit, reason) -> None:
    env["pc"].write_bytes(edit(PC_ENV).encode())
    runner = LocalRunner()
    code, out = _run(env, capsys, runner=runner)
    assert code == 2 and reason in out and not runner.calls


def test_a_push_waits_while_the_names_are_dpapi_wrapped(env, capsys) -> None:
    store = env["tmp"] / "run" / "fallback.env.dpapi"
    store.parent.mkdir()
    store.write_text(json.dumps({"kind": "jarvis_pc_fallback_env", "v": 1, "created": 1,
                                 "names": {"root": ["GOOGLE_API_KEY"]}, "blob": "AA=="}), encoding="utf-8")
    env["pc"].write_bytes(PC_ENV.replace("GOOGLE_API_KEY=c127-fake-google\r\n", "").encode())
    runner = LocalRunner()
    code, out = _run(env, capsys, runner=runner)
    assert code == 2 and "GOOGLE_API_KEY is DPAPI-wrapped on this PC" in out and not runner.calls


def test_the_vps_refuses_without_the_main_livekit_pair(env, capsys) -> None:
    (env["vps"] / ".env").write_text("JARVIS_BRAIN=pc\nLIVEKIT_API_KEY=APIc127main\n", encoding="utf-8")
    code, out = _run(env, capsys)
    assert code == 1 and "REFUSED: LIVEKIT_API_SECRET is not set in" in out and "nothing was written" in out
    assert (env["vps"] / ".env.agent").read_bytes() == b""


# --------------------------------------------------------------------------- --keys

FAKE_DOCKER = r'''
import os, subprocess, sys
REPO = sys.argv[1]
args = sys.argv[2:]
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docker_calls.log")
with open(log, "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")
if args[:2] == ["image", "inspect"]:
    print("sha256:" + "ab" * 32)
    raise SystemExit(1 if os.environ.get("FAKE_NO_IMAGE") else 0)
assert args[0] == "run" and "--network" in args and args[args.index("--network") + 1] == "none", args
assert args[args.index("--user") + 1] == "0", args
host, _, inner = args[args.index("-v") + 1].rpartition(":")
assert inner == "/envdir", args
rest = [a.replace("/envdir", host) for a in args[args.index("python") + 1:]]
proc = subprocess.run([sys.executable, os.path.join(REPO, rest[0])] + rest[1:])
raise SystemExit(proc.returncode)
'''


def _docker(env: dict[str, Any]) -> list[str]:
    script = env["tmp"] / "fake_docker.py"
    script.write_text(FAKE_DOCKER, encoding="utf-8")
    return [sys.executable, str(script), str(ROOT)]


def _pc_public() -> str:
    return dp.public_key_hex(dp.generate_signing_key())


def test_keys_makes_the_agent_key_on_the_vps_and_spreads_public_keys_only(env, capsys) -> None:
    assert _run(env, capsys)[0] == 0   # push first: .env.agent holds the secrets
    pc_public = _pc_public()
    env["pc"].write_bytes((PC_ENV + f"DESKTOP_PC_PUBLIC_KEY={pc_public}\r\n").encode())
    docker = _docker(env)
    code = pae.main([*env["args"], "--keys"], runner=LocalRunner(docker=docker))
    out = capsys.readouterr()
    text = out.out + out.err
    agent = _values(env["vps"] / ".env.agent")
    signing = agent["DESKTOP_AGENT_SIGNING_KEY"]
    assert code == 0, text
    assert signing and signing not in text and not any(marker in text for marker in FAKE_MARKERS)
    public = agent["DESKTOP_AGENT_PUBLIC_KEY"]
    assert f"DESKTOP_AGENT_PUBLIC_KEY={public}" in text and f"DESKTOP_PC_PUBLIC_KEY={pc_public}" in text
    assert dp.public_key_hex(dp.signing_key_from_hex(signing)) == public
    bridge = _values(env["vps"] / "bridge.env")
    assert bridge["DESKTOP_AGENT_PUBLIC_KEY"] == public and bridge["DESKTOP_PC_PUBLIC_KEY"] == pc_public
    assert pae.env_set.EnvFile(env["pc"]).value("DESKTOP_AGENT_PUBLIC_KEY") == public
    assert agent["GOOGLE_API_KEY"] == "c127-fake-google"            # the pushed lines survive the key tool
    assert not (env["vps"] / ".env.agent.tmp").exists()
    assert any(p.name.endswith("-pre-agent-keys") for p in env["backups"].iterdir())
    # a second run keeps the key; --rotate replaces it
    assert pae.main([*env["args"], "--keys"], runner=LocalRunner(docker=docker)) == 0
    assert "key kept" in capsys.readouterr().out and _values(env["vps"] / ".env.agent")["DESKTOP_AGENT_SIGNING_KEY"] \
        == signing
    assert pae.main([*env["args"], "--keys", "--rotate"], runner=LocalRunner(docker=docker)) == 0
    rotated = capsys.readouterr()
    assert signing not in rotated.out + rotated.err
    assert _values(env["vps"] / ".env.agent")["DESKTOP_AGENT_SIGNING_KEY"] != signing
    # and the comparison agrees: public keys equal on both sides and to this PC
    code, out_text = _run(env, capsys, "--check", secrets=(signing,))
    assert code == 0 and "DESKTOP_AGENT_PUBLIC_KEY: equal to bridge.env, equal to this PC" in out_text


def test_keys_dry_run_and_refusals(env, capsys, monkeypatch) -> None:
    docker = _docker(env)
    code, out = _run(env, capsys, "--keys", "--dry-run", runner=LocalRunner(docker=docker))
    assert code == 0 and "dry run: nothing run or written" in out
    assert "container: " in out and "--network none --user 0 -v" in out and "gen_desktop_keys.py --role agent" in out
    (env["vps"] / ".env.agent.tmp").write_text("x", encoding="utf-8")
    code, out = _run(env, capsys, "--keys", runner=LocalRunner(docker=docker))
    assert code == 1 and "already exists (an unfinished run?)" in out
    (env["vps"] / ".env.agent.tmp").unlink()
    monkeypatch.setenv("FAKE_NO_IMAGE", "1")
    code, out = _run(env, capsys, "--keys", runner=LocalRunner(docker=docker))
    assert code == 1 and "no image jarvis-agent:hostinger" in out
    assert "DESKTOP_AGENT_SIGNING_KEY" not in _values(env["vps"] / ".env.agent")
    env["pc"].write_bytes((PC_ENV + "DESKTOP_PC_PUBLIC_KEY=not-hex\r\n").encode())
    code, out = _run(env, capsys, "--keys", runner=LocalRunner(docker=docker))
    assert code == 2 and "is not 64 hex characters" in out


def test_usage_errors(capsys) -> None:
    for argv in (["--rotate"], ["--check", "--dry-run"], ["--keys", "--check"], ["c127-fake-typed"]):
        with pytest.raises(SystemExit) as stop:
            pae.parse_args(argv)
        assert stop.value.code == 2
    assert "c127-fake" not in capsys.readouterr().err
