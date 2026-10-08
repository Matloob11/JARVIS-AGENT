"""Plan task C-17 (OP-14): scripts/ops/rotate_livekit_keys.py rotates the LiveKit pair without printing it.

Only files under tmp_path are touched and ssh never runs: a local runner executes the exact program that would go to
the VPS (with its JSON-transported arguments) in this process against tmp files, as in test_secrets_tooling.py. The
values are throwaway fakes, and every test checks that none of them reaches stdout or stderr.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("jarvis_ops_test_rotate_livekit_keys",
                                              ROOT / "scripts" / "ops" / "rotate_livekit_keys.py")
rlk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rlk)

OLD_KEY, OLD_SECRET = "APIc17oldFake01", "c17-old-fake-secret-value"
NEW_KEY, NEW_SECRET = "APIc17newFake99", "c17-new-fake-secret-value"
VPS_ENV = f"# compose\nLIVEKIT_API_KEY={OLD_KEY}\nLIVEKIT_API_SECRET={OLD_SECRET}\nGOOGLE_API_KEY=c17-keep\n"
BRIDGE_ENV = f"AGENT_TOKEN=c17-keep-token\nLIVEKIT_API_KEY={OLD_KEY}\nLIVEKIT_API_SECRET={OLD_SECRET}\n"
ROOT_ENV = f"# PC\r\nLIVEKIT_URL=wss://jarvis.example.test\r\nLIVEKIT_API_KEY={OLD_KEY}\r\nLIVEKIT_API_SECRET={OLD_SECRET}\r\n"
OVERLAY_ENV = f"LIVEKIT_API_KEY={OLD_KEY}\n# keep me\n"
VALUES = (OLD_KEY, OLD_SECRET, NEW_KEY, NEW_SECRET)


class LocalRunner:
    """Stands in for run_ssh: runs the program like the remote loader does, in this process."""

    def __init__(self, code: int | None = None, out: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self.code, self.out = code, out

    def __call__(self, program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
        self.calls.append({"program": program, "args": args, "host": host, "key": key})
        if self.code is not None:
            return self.code, self.out, "fake failure"
        out, code = io.StringIO(), 0
        with redirect_stdout(out):
            try:
                exec(compile(program, "jarvis-remote", "exec"),  # pylint: disable=exec-used
                     {"__name__": "jarvis_remote", "ARGS": json.loads(json.dumps(args))})
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), ""


@pytest.fixture
def env(tmp_path, monkeypatch):
    vps = tmp_path / "vps"
    vps.mkdir()
    (vps / ".env").write_bytes(VPS_ENV.encode())
    (vps / "bridge.env").write_bytes(BRIDGE_ENV.encode())
    root, overlay = tmp_path / "root.env", tmp_path / "overlay.env"
    root.write_bytes(ROOT_ENV.encode())
    overlay.write_bytes(OVERLAY_ENV.encode())
    monkeypatch.setattr(rlk, "BACKUP_ROOT", str(tmp_path / "backups"))
    args = rlk.parse_args(["--vps-dir", vps.as_posix(), "--pc-files", f"{root},{overlay}", "--host", "h",
                           "--ssh-key", "k"])
    return {"vps": vps, "root": root, "overlay": overlay, "args": args, "backups": tmp_path / "backups"}


def _values(path: Path) -> dict[str, str]:
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep:
            pairs[name.strip()] = value.strip()
    return pairs


def _no_value_printed(capsys) -> str:
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert not [value for value in VALUES if value in text], "a LiveKit value reached the output"
    return text


def test_the_pair_is_rotated_everywhere_and_never_printed(env, capsys):
    assert rlk.rotate(env["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 0
    for path in (env["vps"] / ".env", env["vps"] / "bridge.env", env["root"]):
        assert (_values(path)["LIVEKIT_API_KEY"], _values(path)["LIVEKIT_API_SECRET"]) == (NEW_KEY, NEW_SECRET)
    assert _values(env["overlay"])["LIVEKIT_API_KEY"] == NEW_KEY
    assert _values(env["vps"] / ".env")["GOOGLE_API_KEY"] == "c17-keep"  # every other line stays
    assert _values(env["vps"] / "bridge.env")["AGENT_TOKEN"] == "c17-keep-token"
    assert env["root"].read_bytes().count(b"\r\n") == 4  # the PC file keeps its CRLF line endings
    (backup,) = list(env["backups"].glob("*/livekit-old.env"))
    assert backup.read_text(encoding="utf-8") == f"LIVEKIT_API_KEY={OLD_KEY}\nLIVEKIT_API_SECRET={OLD_SECRET}\n"
    if os.name != "nt":
        assert oct(backup.stat().st_mode & 0o777) == "0o600"
        assert oct((env["vps"] / "bridge.env").stat().st_mode & 0o777) == "0o600"
    text = _no_value_printed(capsys)
    assert "LIVEKIT_API_KEY rotated (len 15, fp " in text and "old pair backed up on the VPS:" in text
    assert "--force-recreate livekit ui-bridge" in text


def test_env_agent_gets_the_pair_only_when_it_has_it(env, capsys):
    agent = env["vps"] / ".env.agent"
    agent.write_bytes(b"JARVIS_ROLE=agent\n")
    assert rlk.rotate(env["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 0
    assert agent.read_bytes() == b"JARVIS_ROLE=agent\n"
    agent.write_bytes(f"LIVEKIT_API_KEY={NEW_KEY}\nLIVEKIT_API_SECRET={NEW_SECRET}\n".encode())
    second = (OLD_KEY.replace("old", "nx2"), OLD_SECRET.replace("old", "nx2"))
    assert rlk.rotate(env["args"], LocalRunner(), pair=second) == 0
    assert _values(agent)["LIVEKIT_API_KEY"] == second[0]
    _no_value_printed(capsys)


def test_a_failed_vps_step_changes_nothing_on_this_pc(env, capsys):
    before = {path: path.read_bytes() for path in (env["root"], env["overlay"])}
    assert rlk.rotate(env["args"], LocalRunner(code=1), pair=(NEW_KEY, NEW_SECRET)) == 1
    assert {path: path.read_bytes() for path in before} == before
    assert not list(env["root"].parent.glob("*.tmp"))  # the staged temp files are gone
    assert "nothing written on this PC" in _no_value_printed(capsys)


def test_a_vps_env_without_the_pair_stops_before_any_write(env, capsys):
    (env["vps"] / ".env").write_bytes(b"GOOGLE_API_KEY=c17-keep\n")
    assert rlk.rotate(env["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 1
    assert (env["vps"] / "bridge.env").read_bytes() == BRIDGE_ENV.encode()
    assert env["root"].read_bytes() == ROOT_ENV.encode() and not env["backups"].exists()
    _no_value_printed(capsys)


def test_a_dry_run_writes_nothing(env, capsys):
    env["args"].dry_run = True
    assert rlk.rotate(env["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 0
    assert (env["vps"] / ".env").read_bytes() == VPS_ENV.encode()
    assert env["root"].read_bytes() == ROOT_ENV.encode() and not env["backups"].exists()
    assert "dry run" in _no_value_printed(capsys)


def test_the_new_pair_has_the_planned_shape_and_survives_the_remote_editor():
    key, secret = rlk.new_pair()
    assert re.fullmatch(r"API[A-Za-z0-9]{12}", key) and len(secret) == 64
    assert re.fullmatch(r"[A-Za-z0-9_.:/@+=,~%-]+", secret)  # remote_env_edit's safe value check
    assert rlk.new_pair() != rlk.new_pair()


def test_the_values_travel_on_stdin_never_in_the_program(env):
    runner = LocalRunner()
    rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET))
    (call,) = runner.calls
    assert NEW_KEY not in call["program"] and NEW_SECRET not in call["program"]
    assert call["args"]["values"] == [NEW_KEY, NEW_SECRET]  # the JSON that run_ssh sends on ssh stdin


@pytest.mark.parametrize("code, line, exit_code", [(0, "old key rejected: 401", 0), (1, "OLD KEY STILL WORKS", 1),
                                                   (1, "old key rejected: 500", 1), (255, "", 1)])
def test_check_old_reports_only_the_status(env, capsys, code, line, exit_code):
    env["args"].check_old = "/root/jarvis-backups/20261008T140000Z/livekit-old.env"
    runner = LocalRunner(code=code, out=line + "\n")
    assert rlk.check_old(env["args"], runner) == exit_code
    (call,) = runner.calls
    assert set(call["args"]) == {"file", "key", "secret", "container", "url"}
    assert call["args"]["key"] == "LIVEKIT_API_KEY"  # names only: the values stay on the VPS
    _no_value_printed(capsys)


def test_the_ssh_key_defaults_to_jarvis_ssh_key(monkeypatch):
    monkeypatch.setenv("JARVIS_SSH_KEY", "~/.ssh/hostinger")
    assert rlk.parse_args([]).ssh_key == Path(os.path.expanduser("~/.ssh/hostinger"))
    monkeypatch.delenv("JARVIS_SSH_KEY")
    assert rlk.parse_args([]).ssh_key == rlk.gen_secrets.VPS_KEY


@pytest.mark.parametrize("typed, sent", [
    ("C:/Program Files/Git/root/jarvis-backups/T/livekit-old.env", "/root/jarvis-backups/T/livekit-old.env"),
    (r"C:\Program Files\Git\root\jarvis-backups\T\livekit-old.env", "/root/jarvis-backups/T/livekit-old.env"),
    ("/root/jarvis-backups/T/livekit-old.env", "/root/jarvis-backups/T/livekit-old.env"),
])
def test_a_git_bash_rewritten_vps_path_is_sent_as_typed(env, typed, sent):
    env["args"].check_old = typed
    runner = LocalRunner(code=0, out="old key rejected: 401\n")
    assert rlk.check_old(env["args"], runner) == 0
    assert runner.calls[0]["args"]["file"] == sent
