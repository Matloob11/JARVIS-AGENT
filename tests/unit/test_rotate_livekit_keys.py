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
    # The vps-mode store (run/fallback.env.dpapi) lives in tmp_path too: a test must never meet the real one, which a
    # laptop in vps mode holds in the repo's run/ (JARVIS_RUN_DIR is the default when --run-dir is not given).
    run = tmp_path / "run"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run))
    args = rlk.parse_args(["--vps-dir", vps.as_posix(), "--pc-files", f"{root},{overlay}", "--host", "h",
                           "--ssh-key", "k", "--run-dir", str(run)])
    return {"vps": vps, "root": root, "overlay": overlay, "args": args, "backups": tmp_path / "backups", "run": run,
            "store": run / rlk.fallback_env.STORE_NAME}


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


# --------------------------------------------------------------------------- vps mode: the DPAPI store (C-127 review)

MAGIC = b"FAKE-DPAPI:"


def fake_protect(data: bytes) -> bytes:
    """A reversible scramble standing in for Windows DPAPI, so no value is ever plain text in the store."""
    return MAGIC + bytes(b ^ 0x2C for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise OSError("not ours")
    return bytes(b ^ 0x2C for b in blob[len(MAGIC):])


@pytest.fixture
def wrapped(env, monkeypatch):
    """vps mode as 'agent_ctl brain vps' leaves this PC: LIVEKIT_API_SECRET DPAPI-wrapped in run/fallback.env.dpapi
    (fallback_env.wrap with a fake DPAPI) and gone from the env files, while the key stays in them."""
    fe = rlk.fallback_env
    monkeypatch.setattr(fe, "dpapi_protect", fake_protect)
    monkeypatch.setattr(fe, "dpapi_unprotect", fake_unprotect)
    monkeypatch.setattr(fe, "restrict_to_user", lambda _path: None)
    report = fe.wrap([env["root"], env["overlay"]], env["run"], brain="vps", worker=False)
    assert report.code == 0 and fe.wrapped_names(env["store"]) == {rlk.SECRET_NAME}, report
    assert b"LIVEKIT_API_SECRET" not in env["root"].read_bytes() + env["overlay"].read_bytes()
    return env


def _files(env: dict[str, Any]) -> dict[Path, bytes]:
    paths = (env["root"], env["overlay"], env["store"], env["vps"] / ".env", env["vps"] / "bridge.env")
    return {path: path.read_bytes() for path in paths}


def test_vps_mode_puts_the_new_secret_into_the_store_never_into_an_env_file(wrapped, capsys):
    """The review case: after 'agent_ctl brain vps' wrapped LIVEKIT_API_SECRET, a rotation with the default PC files
    appended the new secret in plain text to both PC env files while the Desktop Node ran, and the store kept the stale
    old one (D13 undone). Now the secret goes into the store, the key into the root .env, and the overlay keeps no older
    key; 'agent_ctl brain pc' later unwraps the new pair."""
    assert rlk.rotate(wrapped["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 0
    for path in (wrapped["vps"] / ".env", wrapped["vps"] / "bridge.env"):
        assert (_values(path)["LIVEKIT_API_KEY"], _values(path)["LIVEKIT_API_SECRET"]) == (NEW_KEY, NEW_SECRET)
    on_pc = wrapped["root"].read_bytes() + wrapped["overlay"].read_bytes()
    assert NEW_SECRET.encode() not in on_pc and OLD_SECRET.encode() not in on_pc and b"LIVEKIT_API_SECRET" not in on_pc
    root = rlk.env_set.EnvFile(wrapped["root"])
    assert root.value("LIVEKIT_API_KEY") == NEW_KEY and root.value("LIVEKIT_URL") == "wss://jarvis.example.test"
    assert wrapped["root"].read_bytes().count(b"\r\n") == 3          # CRLF kept; only the key's value changed
    assert "LIVEKIT_API_KEY" not in rlk.env_set.EnvFile(wrapped["overlay"]).values   # no older key left on this PC
    assert b"# keep me" in wrapped["overlay"].read_bytes()
    assert rlk.fallback_env.load_store(wrapped["store"], fake_unprotect) == {"root": {rlk.SECRET_NAME: NEW_SECRET}}
    assert not [p for p in wrapped["root"].parent.rglob("*") if p.name.endswith((".tmp", ".part"))]
    text = _no_value_printed(capsys)
    assert "LIVEKIT_API_SECRET rotated (len " in text and "--force-recreate livekit ui-bridge" in text
    assert "--force-recreate agent agent-svc" in text and "agent_ctl brain pc" in text
    assert "agent_ctl restart" not in text     # no PC worker runs in vps mode
    # 'agent_ctl brain pc' (fallback_env.restore): the env files get the new pair, never the stale old secret
    assert rlk.fallback_env.restore([wrapped["root"], wrapped["overlay"]], wrapped["run"]).code == 0
    root = rlk.env_set.EnvFile(wrapped["root"])
    assert (root.value("LIVEKIT_API_KEY"), root.value("LIVEKIT_API_SECRET")) == (NEW_KEY, NEW_SECRET)
    assert not wrapped["store"].exists()


@pytest.mark.parametrize("cause", ["other-windows-user", "env-file-not-writable"])
def test_vps_mode_refuses_before_the_vps_step_when_this_pc_cannot_take_the_pair(wrapped, capsys, monkeypatch, cause):
    """The PC side is checked before the VPS changes (fallback_env.put_values with check, which writes nothing): a store
    this Windows user cannot unwrap, or an env file that cannot be written, stops the run with nothing written on the
    VPS or this PC, as a read-only PC file does in pc mode."""
    before = _files(wrapped)
    if cause == "other-windows-user":
        def other_user(_blob: bytes) -> bytes:
            raise OSError("DPAPI could not unwrap the data (another user)")

        monkeypatch.setattr(rlk.fallback_env, "dpapi_unprotect", other_user)
    else:
        wrapped["root"].with_name(wrapped["root"].name + rlk.gen_secrets.TEMP_SUFFIX).mkdir()   # its temp file blocked
    runner = LocalRunner()
    assert rlk.rotate(wrapped["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 2
    assert not runner.calls                                           # the VPS was never contacted
    assert _files(wrapped) == before and not wrapped["backups"].exists()
    text = _no_value_printed(capsys)
    assert "nothing was written on the VPS or this PC" in text


def test_vps_mode_dry_run_writes_nothing_and_names_the_store(wrapped, capsys):
    wrapped["args"].dry_run = True
    before = _files(wrapped)
    assert rlk.rotate(wrapped["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 0
    assert _files(wrapped) == before and not wrapped["backups"].exists()
    assert not [p for p in wrapped["root"].parent.rglob("*") if p.name.endswith((".tmp", ".part"))]
    text = _no_value_printed(capsys)
    assert "would set LIVEKIT_API_SECRET" in text and "on this PC (vps mode)" in text
    assert "LIVEKIT_API_SECRET into the DPAPI store; nothing written" in text


def test_vps_mode_a_pc_failure_after_the_vps_step_keeps_the_store_and_says_run_it_again(wrapped, capsys, monkeypatch):
    """The root .env is held open when the PC side is written (after the VPS step): the store goes back byte for byte,
    the env files stay as they were, and the message says to run the rotation again (a new pair for both sides)."""
    before = _files(wrapped)
    real = os.replace

    def locked(src: Any, dst: Any) -> None:
        if Path(dst) == wrapped["root"]:
            raise PermissionError(13, "The process cannot access the file because another process uses it")
        real(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    assert rlk.rotate(wrapped["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 1
    monkeypatch.setattr(os, "replace", real)
    assert _values(wrapped["vps"] / ".env")["LIVEKIT_API_SECRET"] == NEW_SECRET
    after = _files(wrapped)
    assert all(after[path] == before[path] for path in (wrapped["root"], wrapped["overlay"], wrapped["store"]))
    text = _no_value_printed(capsys)
    assert "but this PC does not" in text and "run the rotation again" in text and "put back byte for byte" in text


def test_vps_mode_with_no_pc_files_leaves_this_pc_and_the_store_alone(wrapped, capsys):
    """After V6 'activate' this PC holds the PC pair, not the main one: --pc-files '' rotates the VPS only."""
    wrapped["args"].pc_files = ""
    before = _files(wrapped)
    assert rlk.rotate(wrapped["args"], LocalRunner(), pair=(NEW_KEY, NEW_SECRET)) == 0
    after = _files(wrapped)
    assert all(after[path] == before[path] for path in (wrapped["root"], wrapped["overlay"], wrapped["store"]))
    assert _values(wrapped["vps"] / ".env")["LIVEKIT_API_KEY"] == NEW_KEY
    _no_value_printed(capsys)


# --------------------------------------------------------------------------- the PC pair guard (C-127 review)

PC_PAIR_KEY, PC_PAIR_SECRET = "APIc127pcPair42", "c127-pc-pair-fake-secret"


def _activated(env: dict[str, Any]) -> None:
    """This PC after V6 'livekit_pc_pair.py activate': its LIVEKIT_API_KEY/SECRET hold the PC pair (the overlay lost
    its copy of the main pair), and the VPS .env holds both pairs."""
    vps_env = env["vps"] / ".env"
    vps_env.write_bytes(VPS_ENV.encode() + f"LIVEKIT_PC_API_KEY={PC_PAIR_KEY}\n"
                                           f"LIVEKIT_PC_API_SECRET={PC_PAIR_SECRET}\n".encode())
    env["root"].write_bytes(ROOT_ENV.replace(OLD_KEY, PC_PAIR_KEY).replace(OLD_SECRET, PC_PAIR_SECRET).encode())
    env["overlay"].write_bytes(b"# keep me\n")


def _no_pc_pair_printed(capsys) -> str:
    text = _no_value_printed(capsys)
    assert PC_PAIR_KEY not in text and PC_PAIR_SECRET not in text, "a PC pair value reached the output"
    return text


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_rotation_that_would_overwrite_the_activated_pc_pair_is_refused(env, capsys, dry_run):
    """The review case: after V6 'activate' the PC's LIVEKIT_API_KEY/SECRET hold the PC pair, and a rotation with the
    default PC files overwrote it with the new main pair, so a later 'livekit_pc_pair.py revoke' after a lost laptop cut
    nothing off. Now the VPS step gets this PC's LIVEKIT_API_KEY (a key, never a secret) and refuses before any backup
    or write while it is the VPS .env's LIVEKIT_PC_API_KEY; --pc-files '' rotates the main pair on the VPS only."""
    _activated(env)
    env["args"].dry_run = dry_run
    files = (env["vps"] / ".env", env["vps"] / "bridge.env", env["root"], env["overlay"])
    before = {path: path.read_bytes() for path in files}
    runner = LocalRunner()
    assert rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 2
    assert {path: path.read_bytes() for path in files} == before and not env["backups"].exists()
    assert not list(env["root"].parent.glob("*.tmp"))                     # the staged PC temp files are gone
    (call,) = runner.calls
    assert call["args"]["pc_keys"] == [PC_PAIR_KEY] and call["args"]["pc_pair_key_name"] == "LIVEKIT_PC_API_KEY"
    assert PC_PAIR_SECRET not in json.dumps(call["args"])                # no PC secret travels
    text = _no_pc_pair_printed(capsys)
    assert "refused by the VPS step" in text and "--pc-files ''" in text
    assert "Nothing was written on the VPS or this PC" in text and "revoke" in text
    # the way the RUNBOOK names: the VPS only, and this PC keeps the PC pair
    env["args"].pc_files = ""
    runner = LocalRunner()
    assert rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 0
    assert runner.calls[0]["args"]["pc_keys"] == []
    assert {path: path.read_bytes() for path in files[2:]} == {path: before[path] for path in files[2:]}
    if not dry_run:
        vps = _values(env["vps"] / ".env")
        assert (vps["LIVEKIT_API_KEY"], vps["LIVEKIT_PC_API_KEY"]) == (NEW_KEY, PC_PAIR_KEY)
    _no_pc_pair_printed(capsys)


def test_the_pc_pair_guard_refuses_in_vps_mode_too_with_the_store_untouched(wrapped, capsys):
    """vps mode after 'activate': the check run of fallback_env.put_values passes (nothing written), then the VPS step
    refuses: the store, the env files and the VPS files all stay as they were."""
    _activated(wrapped)
    rlk.fallback_env.write_store(wrapped["store"], {"root": {rlk.SECRET_NAME: PC_PAIR_SECRET}}, protect=fake_protect,
                                 unprotect=fake_unprotect, restrict=lambda _path: None)
    wrapped["root"].write_bytes(ROOT_ENV.replace(OLD_KEY, PC_PAIR_KEY).replace(
        f"LIVEKIT_API_SECRET={OLD_SECRET}\r\n", "").encode())               # the secret lives in the store
    before = _files(wrapped)
    runner = LocalRunner()
    assert rlk.rotate(wrapped["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 2
    assert _files(wrapped) == before and not wrapped["backups"].exists()
    assert runner.calls[0]["args"]["pc_keys"] == [PC_PAIR_KEY]
    assert "refused by the VPS step" in _no_pc_pair_printed(capsys)


def test_a_pending_pc_pair_does_not_stop_a_rotation_of_the_main_pair(env, capsys):
    """Before 'activate' (after 'create') this PC holds the main pair plus the pending LIVEKIT_PC_API_KEY: its
    LIVEKIT_API_KEY is not the VPS .env's PC key, so the rotation goes ahead and leaves the PC pair alone."""
    vps_env = env["vps"] / ".env"
    vps_env.write_bytes(VPS_ENV.encode() + f"LIVEKIT_PC_API_KEY={PC_PAIR_KEY}\nLIVEKIT_PC_API_SECRET={PC_PAIR_SECRET}\n"
                        .encode())
    with open(env["root"], "ab") as handle:
        handle.write(f"LIVEKIT_PC_API_KEY={PC_PAIR_KEY}\r\n".encode())
    runner = LocalRunner()
    assert rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 0
    assert runner.calls[0]["args"]["pc_keys"] == [OLD_KEY]
    vps = _values(vps_env)
    assert (vps["LIVEKIT_API_KEY"], vps["LIVEKIT_PC_API_KEY"], vps["LIVEKIT_PC_API_SECRET"]) == (
        NEW_KEY, PC_PAIR_KEY, PC_PAIR_SECRET)
    root = _values(env["root"])
    assert (root["LIVEKIT_API_KEY"], root["LIVEKIT_PC_API_KEY"]) == (NEW_KEY, PC_PAIR_KEY)
    _no_pc_pair_printed(capsys)


P2_KEY, P2_SECRET = "APIc127pcPair77", "c127-pc-pair-second-fake-secret"


def _rotated_pc_pair(env: dict[str, Any]) -> None:
    """This PC after V6 'activate' and a later 'livekit_pc_pair.py create --rotate': its LIVEKIT_API_KEY/SECRET still
    hold the activated pair, the pending names hold the new one, and the VPS .env names the new one as
    LIVEKIT_PC_API_KEY next to the main pair."""
    _activated(env)
    (env["vps"] / ".env").write_bytes(VPS_ENV.encode() + f"LIVEKIT_PC_API_KEY={P2_KEY}\n"
                                                         f"LIVEKIT_PC_API_SECRET={P2_SECRET}\n".encode())
    with open(env["root"], "ab") as handle:
        handle.write(f"LIVEKIT_PC_API_KEY={P2_KEY}\r\nLIVEKIT_PC_API_SECRET={P2_SECRET}\r\n".encode())


def _no_pc_pairs_printed(capsys) -> str:
    text = _no_pc_pair_printed(capsys)
    assert P2_KEY not in text and P2_SECRET not in text, "a pending PC pair value reached the output"
    return text


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("case", ["earlier_pc_pair", "no_key"])
def test_a_pc_that_does_not_hold_the_main_pair_never_gets_it_while_the_vps_has_a_pc_pair(env, capsys, case, dry_run):
    """The review case: after 'activate' and a later 'livekit_pc_pair.py create --rotate' the VPS .env names a new
    pending PC key while this PC still holds the activated one, so the first guard (this PC's key is the VPS .env's
    LIVEKIT_PC_API_KEY) let a rotation with the default PC files write the new main pair into this PC: until 'activate'
    (or for good, if forgotten) the laptop held the main pair and a 'revoke' cut nothing off. Now the VPS step also
    refuses, before any backup or write, while the VPS .env has a PC pair and no LIVEKIT_API_KEY of the PC files is the
    VPS .env's LIVEKIT_API_KEY (a PC with no key at all included); --pc-files '' rotates the VPS only."""
    if case == "earlier_pc_pair":
        _rotated_pc_pair(env)
        sent = [PC_PAIR_KEY]
    else:
        (env["vps"] / ".env").write_bytes(VPS_ENV.encode() + f"LIVEKIT_PC_API_KEY={PC_PAIR_KEY}\n"
                                                             f"LIVEKIT_PC_API_SECRET={PC_PAIR_SECRET}\n".encode())
        env["root"].write_bytes(b"# PC\r\nLIVEKIT_URL=wss://jarvis.example.test\r\n")
        env["overlay"].write_bytes(b"# keep me\n")
        sent = []
    env["args"].dry_run = dry_run
    files = (env["vps"] / ".env", env["vps"] / "bridge.env", env["root"], env["overlay"])
    before = {path: path.read_bytes() for path in files}
    runner = LocalRunner()
    assert rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 2
    assert {path: path.read_bytes() for path in files} == before and not env["backups"].exists()
    assert not list(env["root"].parent.glob("*.tmp"))                     # the staged PC temp files are gone
    (call,) = runner.calls
    assert call["args"]["pc_files"] is True and call["args"]["pc_keys"] == sent
    assert PC_PAIR_SECRET not in json.dumps(call["args"]) and P2_SECRET not in json.dumps(call["args"])
    text = _no_pc_pairs_printed(capsys)
    assert "refused by the VPS step" in text and "does not hold the main pair" in text and "--pc-files ''" in text
    assert "'activate'" in text and "revoke" in text and "Nothing was written on the VPS or this PC" in text
    # the VPS only: this PC keeps what it holds
    env["args"].pc_files = ""
    runner = LocalRunner()
    assert rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 0
    assert runner.calls[0]["args"]["pc_files"] is False and runner.calls[0]["args"]["pc_keys"] == []
    assert {path: path.read_bytes() for path in files[2:]} == {path: before[path] for path in files[2:]}
    if not dry_run:
        vps = _values(env["vps"] / ".env")
        assert (vps["LIVEKIT_API_KEY"], vps["LIVEKIT_PC_API_KEY"]) == (
            NEW_KEY, P2_KEY if case == "earlier_pc_pair" else PC_PAIR_KEY)
    _no_pc_pairs_printed(capsys)


def test_the_main_pair_guard_refuses_in_vps_mode_too_with_the_store_untouched(wrapped, capsys):
    """vps mode after 'activate' and a later 'create --rotate': the activated secret and the pending one are in the
    store, the keys in the root .env. The check run of fallback_env.put_values passes (nothing written), then the VPS
    step refuses: the store, the env files and the VPS files all stay as they were."""
    _rotated_pc_pair(wrapped)
    rlk.fallback_env.write_store(wrapped["store"], {"root": {rlk.SECRET_NAME: PC_PAIR_SECRET,
                                                             "LIVEKIT_PC_API_SECRET": P2_SECRET}},
                                 protect=fake_protect, unprotect=fake_unprotect, restrict=lambda _path: None)
    root = ROOT_ENV.replace(OLD_KEY, PC_PAIR_KEY).replace(f"LIVEKIT_API_SECRET={OLD_SECRET}\r\n", "")
    wrapped["root"].write_bytes(f"{root}LIVEKIT_PC_API_KEY={P2_KEY}\r\n".encode())   # the secrets are in the store
    before = _files(wrapped)
    runner = LocalRunner()
    assert rlk.rotate(wrapped["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 2
    assert _files(wrapped) == before and not wrapped["backups"].exists()
    assert runner.calls[0]["args"]["pc_keys"] == [PC_PAIR_KEY] and runner.calls[0]["args"]["pc_files"] is True
    text = _no_pc_pairs_printed(capsys)
    assert "refused by the VPS step" in text and "does not hold the main pair" in text


def test_without_a_pc_pair_a_pc_an_earlier_rotation_missed_is_brought_up_to_date(env, capsys):
    """Before V6 'create' the VPS .env has no LIVEKIT_PC_API_KEY, so neither PC pair guard applies: a PC whose
    LIVEKIT_API_KEY is an older main key (an earlier rotation whose PC side failed after its VPS step) gets the new
    pair, as the 'run the rotation again' message promises."""
    stale = "APIc17staleFake"
    env["root"].write_bytes(ROOT_ENV.replace(OLD_KEY, stale).encode())
    env["overlay"].write_bytes(OVERLAY_ENV.replace(OLD_KEY, stale).encode())
    runner = LocalRunner()
    assert rlk.rotate(env["args"], runner, pair=(NEW_KEY, NEW_SECRET)) == 0
    assert runner.calls[0]["args"]["pc_keys"] == [stale] and runner.calls[0]["args"]["pc_files"] is True
    root = _values(env["root"])
    assert (root["LIVEKIT_API_KEY"], root["LIVEKIT_API_SECRET"]) == (NEW_KEY, NEW_SECRET)
    assert _values(env["overlay"])["LIVEKIT_API_KEY"] == NEW_KEY
    assert _values(env["vps"] / ".env")["LIVEKIT_API_KEY"] == NEW_KEY
    assert stale not in _no_value_printed(capsys)


def test_the_shipped_rotate_program_stays_python38_code():
    """The guard runs on the VPS (python3.8+, standard library only), inside the program rotate_program() ships."""
    import ast  # pylint: disable=import-outside-toplevel

    source = rlk.rotate_program()
    tree = ast.parse(source, feature_version=(3, 8))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "hashlib", "json", "os", "re", "secrets", "typing"}, imported
    assert "def value_of(" in source and "pc_pair_key_name" in source
