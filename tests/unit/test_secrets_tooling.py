"""Plan task C-14: scripts/ops/gen_secrets.py and scripts/ops/rotate_env_secret.py.

Only files under tmp_path are touched and ssh never runs: a fake runner executes the exact program that would go
to the VPS (with its JSON-transported arguments) in this process against tmp files, and one test runs it through
the real loader in a fresh Python. The values below are throwaway fakes, and every test checks that no value
reaches stdout or stderr.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import ctypes
import importlib.util
import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
LEGACY = "c14-fake-legacy-token-123"
ROOT_ENV = f"# JARVIS PC\r\nVORTEX_SECURITY_TOKEN={LEGACY}\r\nGOOGLE_API_KEY=c14-fake-google\r\n\r\nUSER_CITY=Lahore\r\n"
OVERLAY_ENV = f"VORTEX_SECURITY_TOKEN={LEGACY}\nAGENT_TOKEN=\n# keep me\n"
VPS_ENV = (f"LIVEKIT_API_KEY=c14-fake-lk-key\nLIVEKIT_API_SECRET='c14 fake lk secret'\n"
           f"VORTEX_SECURITY_TOKEN={LEGACY}\nGOOGLE_API_KEY=c14-fake-google\n")
BRIDGE_KEYS = {"AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "SESSION_JWT_SECRET", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
               "VORTEX_SECURITY_TOKEN", "LEGACY_TOKEN_AUTH", "BRIDGE_EVENT_RETENTION_DAYS"}


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_test_{name}", ROOT / "scripts" / "ops" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_secrets = _load("gen_secrets")
rotate = _load("rotate_env_secret")


class LocalRunner:
    """Stands in for run_ssh: runs the program like the remote loader does, in this process."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
        self.calls.append({"program": program, "args": args, "host": host, "key": key})
        out, code = io.StringIO(), 0
        with redirect_stdout(out):
            try:
                exec(compile(program, "jarvis-remote", "exec"),  # pylint: disable=exec-used
                     {"__name__": "jarvis_remote", "ARGS": json.loads(json.dumps(args))})
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), ""


@pytest.fixture
def pc(tmp_path):
    root, overlay = tmp_path / "root.env", tmp_path / "overlay.env"
    root.write_bytes(ROOT_ENV.encode())
    overlay.write_bytes(OVERLAY_ENV.encode())
    return root, overlay


@pytest.fixture
def vps(tmp_path):
    folder = tmp_path / "vps"
    folder.mkdir()
    (folder / ".env").write_bytes(VPS_ENV.encode())
    return folder


def _pc_args(pc: tuple[Path, Path]) -> list[str]:
    return ["--env-file", str(pc[0]), "--env-file", str(pc[1])]


def _no_values(text: str, *values: str) -> None:
    for value in values:
        assert value and value not in text


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------- gen_secrets.py --pc


def test_gen_pc_adds_one_value_per_key_and_keeps_every_other_byte(pc, capsys, tmp_path):
    root, overlay = pc
    assert gen_secrets.main(["--pc", *_pc_args(pc)]) == 0

    a, b = dotenv_values(root), dotenv_values(overlay)
    assert a["AGENT_TOKEN"] == b["AGENT_TOKEN"] and re.fullmatch(r"[A-Za-z0-9_-]{64}", a["AGENT_TOKEN"])
    assert a["BRIDGE_HMAC_SECRET"] == b["BRIDGE_HMAC_SECRET"] and re.fullmatch(r"[0-9a-f]{64}",
                                                                               a["BRIDGE_HMAC_SECRET"])
    assert root.read_bytes().startswith(ROOT_ENV.encode()) and b"\r\nAGENT_TOKEN=" in root.read_bytes()
    text = overlay.read_text(encoding="utf-8")
    assert text.startswith(f"VORTEX_SECURITY_TOKEN={LEGACY}\nAGENT_TOKEN={a['AGENT_TOKEN']}\n# keep me\n")
    assert "\r" not in text  # the overlay keeps its own line endings
    out = capsys.readouterr()
    _no_values(out.out + out.err, a["AGENT_TOKEN"], a["BRIDGE_HMAC_SECRET"], LEGACY)
    assert f"fp {gen_secrets.fingerprint(a['AGENT_TOKEN'])}" in out.out and "len 64" in out.out
    assert not list(tmp_path.glob("*.tmp"))
    if os.name != "nt":
        assert _mode(root) == _mode(overlay) == 0o600

    before = root.read_bytes(), overlay.read_bytes()
    assert gen_secrets.main(["--pc", *_pc_args(pc)]) == 0
    assert (root.read_bytes(), overlay.read_bytes()) == before
    assert "no change" in capsys.readouterr().out


def test_gen_pc_copies_a_value_one_file_already_has(pc):
    root, overlay = pc
    root.write_bytes(ROOT_ENV.encode() + b"AGENT_TOKEN=c14-existing-agent-token\r\n")
    assert gen_secrets.main(["--pc", *_pc_args(pc)]) == 0
    assert dotenv_values(root)["AGENT_TOKEN"] == dotenv_values(overlay)["AGENT_TOKEN"] == "c14-existing-agent-token"


def test_gen_pc_refuses_files_that_disagree_and_writes_nothing(pc, capsys):
    root, overlay = pc
    root.write_bytes(ROOT_ENV.encode() + b"AGENT_TOKEN=c14-value-one\r\n")
    overlay.write_bytes(b"AGENT_TOKEN=c14-value-two\n")
    before = root.read_bytes(), overlay.read_bytes()
    assert gen_secrets.main(["--pc", *_pc_args(pc)]) == 2
    assert (root.read_bytes(), overlay.read_bytes()) == before
    err = capsys.readouterr().err
    assert "AGENT_TOKEN" in err and "rotate_env_secret" in err
    _no_values(err, "c14-value-one", "c14-value-two")


def test_gen_pc_dry_run_writes_nothing(pc, capsys):
    before = pc[0].read_bytes(), pc[1].read_bytes()
    assert gen_secrets.main(["--pc", "--dry-run", *_pc_args(pc)]) == 0
    assert (pc[0].read_bytes(), pc[1].read_bytes()) == before
    assert "dry run: nothing written" in capsys.readouterr().out


# ---------------------------------------------------------------- gen_secrets.py --vps (program run locally)


def test_gen_vps_creates_bridge_env_with_every_key(pc, vps, capsys):
    assert gen_secrets.main(["--pc", *_pc_args(pc)]) == 0
    runner = LocalRunner()
    assert gen_secrets.main(["--vps", *_pc_args(pc), "--vps-dir", str(vps)], runner=runner) == 0

    bridge_env = vps / "bridge.env"
    values, pc_values = dotenv_values(bridge_env), dotenv_values(pc[0])
    assert set(values) == BRIDGE_KEYS
    assert values["AGENT_TOKEN"] == pc_values["AGENT_TOKEN"]
    assert values["BRIDGE_HMAC_SECRET"] == pc_values["BRIDGE_HMAC_SECRET"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{86}", values["SESSION_JWT_SECRET"])  # token_urlsafe(64)
    assert (values["LIVEKIT_API_KEY"], values["LIVEKIT_API_SECRET"], values["VORTEX_SECURITY_TOKEN"]) == (
        "c14-fake-lk-key", "c14 fake lk secret", LEGACY)
    assert "LIVEKIT_API_SECRET='c14 fake lk secret'\n" in bridge_env.read_text(encoding="utf-8")  # line as-is
    assert (values["LEGACY_TOKEN_AUTH"], values["BRIDGE_EVENT_RETENTION_DAYS"]) == ("1", "0")
    assert "GOOGLE_API_KEY" not in values  # the bridge gets no agent secret
    assert (vps / ".env").read_bytes() == VPS_ENV.encode()
    assert not list(vps.glob("*tmp*"))
    if os.name != "nt":
        assert _mode(bridge_env) == 0o600
    out = capsys.readouterr()
    _no_values(out.out + out.err, *(set(values.values()) - {"1", "0"}))
    assert f"written {vps}/bridge.env" in out.out

    before = bridge_env.read_bytes()
    assert gen_secrets.main(["--vps", *_pc_args(pc), "--vps-dir", str(vps)], runner=runner) == 0
    assert bridge_env.read_bytes() == before  # everything present: nothing rewritten
    assert "not changed" in capsys.readouterr().out
    assert runner.calls[0]["host"] == gen_secrets.VPS_HOST


def test_gen_vps_keeps_a_differing_agent_token_and_says_so(pc, vps, capsys):
    gen_secrets.main(["--pc", *_pc_args(pc)])
    (vps / "bridge.env").write_bytes(b"AGENT_TOKEN=c14-other-agent-token\nLEGACY_TOKEN_AUTH=0\n")
    assert gen_secrets.main(["--vps", *_pc_args(pc), "--vps-dir", str(vps)], runner=LocalRunner()) == 1

    values = dotenv_values(vps / "bridge.env")
    assert values["AGENT_TOKEN"] == "c14-other-agent-token"
    assert values["LEGACY_TOKEN_AUTH"] == "0"  # an existing switch is never flipped back
    assert values["BRIDGE_HMAC_SECRET"] == dotenv_values(pc[0])["BRIDGE_HMAC_SECRET"]  # missing keys added
    out = capsys.readouterr().out
    assert "AGENT_TOKEN: differs" in out and "LEGACY_TOKEN_AUTH: kept" in out
    _no_values(out, "c14-other-agent-token")


def test_gen_vps_reports_a_key_missing_at_its_source(pc, vps, capsys):
    gen_secrets.main(["--pc", *_pc_args(pc)])
    (vps / ".env").write_bytes(b"LIVEKIT_API_KEY=c14-fake-lk-key\nLIVEKIT_API_SECRET=\n")
    assert gen_secrets.main(["--vps", *_pc_args(pc), "--vps-dir", str(vps)], runner=LocalRunner()) == 1
    out = capsys.readouterr().out
    assert "LIVEKIT_API_SECRET: missing_in_source" in out and "VORTEX_SECURITY_TOKEN: missing_in_source" in out


def test_gen_vps_needs_the_pc_values_first(pc, vps, capsys):
    assert gen_secrets.main(["--vps", *_pc_args(pc), "--vps-dir", str(vps)], runner=LocalRunner()) == 2
    assert "run --pc first" in capsys.readouterr().err
    assert not (vps / "bridge.env").exists()


def test_gen_vps_reports_an_ssh_failure_without_values(pc, capsys):
    gen_secrets.main(["--pc", *_pc_args(pc)])

    def failing(*_args: Any) -> tuple[int, str, str]:
        return 255, "", "ssh: connect to host 192.0.2.1 port 22: Connection timed out\n"

    assert gen_secrets.main(["--vps", *_pc_args(pc)], runner=failing) == 1
    assert "failed (exit 255): ssh: connect to host" in capsys.readouterr().out


def test_the_remote_program_writes_600_under_umask_077(tmp_path, monkeypatch, capsys):
    calls: list[tuple[str, Any]] = []
    real_chmod, real_umask = os.chmod, os.umask
    monkeypatch.setattr(os, "chmod", lambda path, mode: (calls.append(("chmod", mode)), real_chmod(path, mode)))
    monkeypatch.setattr(os, "umask", lambda mask: (calls.append(("umask", mask)), real_umask(mask))[1])
    target = tmp_path / "bridge.env"
    code = gen_secrets.remote_env_edit({"target": str(target), "source": None, "dry_run": False,
                                        "ops": [{"op": "set", "key": "AGENT_TOKEN", "value": "c14-x-1234"}]})
    assert code == 0 and dotenv_values(target) == {"AGENT_TOKEN": "c14-x-1234"}
    assert ("umask", 0o077) in calls and ("chmod", 0o600) in calls
    _no_values(capsys.readouterr().out, "c14-x-1234")


def test_the_remote_program_refuses_a_value_that_is_not_one_plain_token(tmp_path, capsys):
    target = tmp_path / "bridge.env"
    for value in ("two words", "quote'd", "multi\nline", "", "$HOME"):
        code = gen_secrets.remote_env_edit({"target": str(target), "source": None, "dry_run": False,
                                            "ops": [{"op": "set", "key": "AGENT_TOKEN", "value": value}]})
        assert code == 2
    assert not target.exists()
    assert "unsafe_value" in capsys.readouterr().out


def test_the_remote_loader_runs_the_program_in_a_fresh_python(tmp_path):
    loader = gen_secrets.REMOTE_LOADER
    assert loader.startswith("python3 -c '") and loader.endswith("'") and loader.count("'") == 2
    target = tmp_path / "bridge.env"
    args = {"target": str(target), "source": None, "dry_run": False,
            "ops": [{"op": "set", "key": "AGENT_TOKEN", "value": "c14-loader-value-1234"}]}
    proc = subprocess.run([sys.executable, "-c", loader[len("python3 -c '"):-1]],
                          input=json.dumps({"program": gen_secrets.remote_program(), "args": args}),
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert dotenv_values(target) == {"AGENT_TOKEN": "c14-loader-value-1234"}
    _no_values(proc.stdout + proc.stderr, "c14-loader-value-1234")


def test_run_ssh_puts_values_only_on_stdin(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(command=command, input=kwargs["input"])
        return subprocess.CompletedProcess(command, 0, stdout='{"written": false, "mode": null}\n', stderr="")

    monkeypatch.setattr(gen_secrets.subprocess, "run", fake_run)
    secret = "c14-very-secret-value-xyz"
    code, out, _ = gen_secrets.run_ssh(gen_secrets.remote_program(),
                                          {"ops": [{"op": "set", "key": "AGENT_TOKEN", "value": secret}]},
                                          "root@host.invalid", Path("id_test"))
    assert code == 0 and json.loads(out) == {"written": False, "mode": None}
    assert seen["command"][:3] == ["ssh", "-i", "id_test"] and "BatchMode=yes" in seen["command"]
    assert secret not in " ".join(seen["command"]) and secret in seen["input"]


# ---------------------------------------------------------------- rotate_env_secret.py


def test_rotate_replaces_the_key_everywhere_and_leaves_no_old_value(tmp_path, capsys):
    root, overlay, bridge_env = tmp_path / "root.env", tmp_path / "overlay.env", tmp_path / "bridge.env"
    root.write_bytes(b"A=1\r\nAGENT_TOKEN=c14-old-agent-1\r\nexport AGENT_TOKEN = c14-old-agent-2  # dup\r\nZ=9\r\n")
    overlay.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    bridge_env.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\nLEGACY_TOKEN_AUTH=1\n")
    runner = LocalRunner()
    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", f"{root},{overlay}", "--vps-file", str(bridge_env)],
                       runner=runner)

    assert code == 0
    new = dotenv_values(root)["AGENT_TOKEN"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{64}", new)
    assert dotenv_values(overlay)["AGENT_TOKEN"] == dotenv_values(bridge_env)["AGENT_TOKEN"] == new
    text = root.read_bytes()
    assert text.startswith(b"A=1\r\n") and text.endswith(b"Z=9\r\n") and b"export AGENT_TOKEN =" in text
    for path in (root, overlay, bridge_env):
        assert b"c14-old-agent" not in path.read_bytes()  # no stale secret left in any file
    assert dotenv_values(bridge_env)["LEGACY_TOKEN_AUTH"] == "1"
    out = capsys.readouterr()
    assert f"AGENT_TOKEN rotated in 3 files (len 64, fp {gen_secrets.fingerprint(new)})" in out.out
    _no_values(out.out + out.err, new)
    assert new not in json.dumps(runner.calls[0]["program"])  # the value went only in the arguments


def test_rotate_takes_a_value_from_stdin(tmp_path, capsys):
    root = tmp_path / "root.env"
    root.write_bytes(b"LIVEKIT_URL=ws://192.0.2.1:7880\nOTHER=x\n")
    code = rotate.main(["--name", "LIVEKIT_URL", "--pc-files", str(root), "--value-from-stdin"],
                       stdin=io.StringIO("wss://jarvis.example\n"))
    assert code == 0
    assert root.read_bytes() == b"LIVEKIT_URL=wss://jarvis.example\nOTHER=x\n"
    out = capsys.readouterr().out
    assert "LIVEKIT_URL rotated in 1 files" in out and "jarvis.example" not in out


def test_rotate_sets_an_alias_only_where_it_exists(tmp_path, capsys):
    root, overlay, vps_env = tmp_path / "root.env", tmp_path / "overlay.env", tmp_path / "vps.env"
    root.write_bytes(b"VORTEX_SECURITY_TOKEN=c14-old\nVITE_VORTEX_SECURITY_TOKEN=c14-old\n")
    overlay.write_bytes(b"VORTEX_SECURITY_TOKEN=c14-old\n")
    vps_env.write_bytes(b"VORTEX_SECURITY_TOKEN=c14-old\nVITE_VORTEX_SECURITY_TOKEN=c14-old\n")
    code = rotate.main(["--name", "VORTEX_SECURITY_TOKEN", "--alias", "VITE_VORTEX_SECURITY_TOKEN",
                        "--pc-files", f"{root},{overlay}", "--vps-file", str(vps_env)], runner=LocalRunner())
    assert code == 0
    new = dotenv_values(root)["VORTEX_SECURITY_TOKEN"]
    assert dotenv_values(root)["VITE_VORTEX_SECURITY_TOKEN"] == new
    assert dotenv_values(overlay) == {"VORTEX_SECURITY_TOKEN": new}  # no alias added where there was none
    assert dotenv_values(vps_env) == {"VORTEX_SECURITY_TOKEN": new, "VITE_VORTEX_SECURITY_TOKEN": new}
    out = capsys.readouterr().out
    assert "VORTEX_SECURITY_TOKEN rotated in 3 files" in out
    assert "VITE_VORTEX_SECURITY_TOKEN rotated in 2 files" in out


def test_rotate_writes_nothing_on_this_pc_when_the_vps_step_fails(tmp_path, capsys):
    root = tmp_path / "root.env"
    root.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")

    def failing(*_args: Any) -> tuple[int, str, str]:
        return 255, "", "ssh: Could not resolve hostname host.invalid\n"

    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(root), "--vps-file", "/opt/x/bridge.env"],
                       runner=failing)
    assert code == 1 and root.read_bytes() == b"AGENT_TOKEN=c14-old-agent-1\n"
    out = capsys.readouterr()
    assert "Could not resolve hostname" in out.out and "nothing written on this PC" in out.err
    assert not list(tmp_path.glob("*.tmp"))  # the PC temp file written before the VPS step is gone

    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(root), "--vps-file", str(tmp_path / "v.env"),
                        "--value-from-stdin"], runner=LocalRunner(), stdin=io.StringIO("two words\n"))
    assert code == 1 and root.read_bytes() == b"AGENT_TOKEN=c14-old-agent-1\n"  # refused by the VPS side


def test_rotate_changes_nothing_anywhere_when_a_pc_file_cannot_be_written(tmp_path, capsys):
    root, bridge_env = tmp_path / "root.env", tmp_path / "bridge.env"
    root.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    bridge_env.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    (tmp_path / ("root.env" + gen_secrets.TEMP_SUFFIX)).mkdir()  # its temp file cannot be created
    runner = LocalRunner()
    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(root), "--vps-file", str(bridge_env)],
                       runner=runner)

    assert code == 2 and not runner.calls  # stopped before the VPS step
    assert root.read_bytes() == bridge_env.read_bytes() == b"AGENT_TOKEN=c14-old-agent-1\n"
    assert "no file was changed" in capsys.readouterr().err

    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", f"{root},{root}", "--vps-file", str(bridge_env)],
                       runner=runner)  # the second rename would fail after the VPS step
    assert code == 2 and not runner.calls and "given twice" in capsys.readouterr().err


def test_rotate_says_so_when_a_pc_file_cannot_be_replaced_after_the_vps_step(tmp_path, monkeypatch, capsys):
    root, overlay, bridge_env = tmp_path / "root.env", tmp_path / "overlay.env", tmp_path / "bridge.env"
    for path in (root, overlay, bridge_env):
        path.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    real_replace = os.replace

    def replace(src: Any, dst: Any) -> None:
        if Path(dst) == overlay:  # e.g. held open by another program
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", f"{root},{overlay}", "--vps-file", str(bridge_env)],
                       runner=LocalRunner())

    new = dotenv_values(bridge_env)["AGENT_TOKEN"]
    assert code == 1 and new != "c14-old-agent-1"
    assert root.read_bytes() == overlay.read_bytes() == b"AGENT_TOKEN=c14-old-agent-1\n"  # root was put back
    assert not list(tmp_path.glob("*.tmp"))
    out = capsys.readouterr()
    assert (f"the VPS file {bridge_env} already holds the new AGENT_TOKEN (fp {gen_secrets.fingerprint(new)})"
            in out.err)
    assert "no file was changed" in out.err and "run the same command again" in out.err
    _no_values(out.out + out.err, new)


@pytest.mark.skipif(os.name != "nt", reason="only Windows cannot rename over a read-only file")
def test_rotate_stops_at_a_read_only_pc_file_before_the_vps_step(tmp_path, capsys):
    root, bridge_env = tmp_path / "root.env", tmp_path / "bridge.env"
    root.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    bridge_env.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    runner = LocalRunner()
    os.chmod(root, stat.S_IREAD)
    try:
        code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(root), "--vps-file", str(bridge_env)],
                           runner=runner)
    finally:
        os.chmod(root, stat.S_IREAD | stat.S_IWRITE)

    assert code == 2 and not runner.calls
    assert root.read_bytes() == bridge_env.read_bytes() == b"AGENT_TOKEN=c14-old-agent-1\n"
    assert "read-only: " in capsys.readouterr().err
    assert not list(tmp_path.glob("*.tmp"))


def test_rotate_refuses_a_value_typed_into_git_bash_mintty(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root.env"
    root.write_bytes(b"LIVEKIT_API_SECRET=c14-old\n")
    monkeypatch.setattr(rotate, "echoing_terminal", lambda stream: True)
    typed = io.StringIO("c14-typed-secret-value\n")
    code = rotate.main(["--name", "LIVEKIT_API_SECRET", "--pc-files", str(root), "--value-from-stdin"], stdin=typed)

    assert code == 2 and typed.tell() == 0  # refused before anything was read
    assert root.read_bytes() == b"LIVEKIT_API_SECRET=c14-old\n"
    err = capsys.readouterr().err
    assert "Git Bash mintty" in err and "winpty" in err


@pytest.mark.parametrize(("name", "echoes"), [
    ("\\msys-1888ae32e00d56aa-pty0-from-master", True),
    ("\\msys-1888ae32e00d56aa-pty3-from-master-nat", True),
    ("\\cygwin-0123456789abcdef-pty1-to-master", True),
    ("\\Device\\NamedPipe\\msys-1888ae32e00d56aa-pty0-from-master", True),
    ("\\1888ae32e00d56aa-12564-pipe-nt-0x1", False),  # what '|' gives a program in Git Bash
    ("\\msys-1888ae32e00d56aa-10572-sigwait", False),
    ("\\conpty-4314213.80925376-in", False),
    ("", False)])
def test_git_bash_terminal_pipe_names(name, echoes):
    assert bool(rotate._MSYS_PTY.fullmatch(name)) is echoes


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipes")
def test_the_mintty_check_reads_real_windows_pipe_names():
    import msvcrt  # pylint: disable=import-outside-toplevel,import-error
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateNamedPipeW.restype = kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, *([wintypes.DWORD] * 6), ctypes.c_void_p]
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = ctypes.c_void_p(-1).value
    # Random keys: these names never belong to a real terminal on this machine.
    for name, echoes in ((f"msys-{secrets.token_hex(8)}-pty97-from-master", True),
                         (f"{secrets.token_hex(8)}-4242-pipe-nt-0x1", False)):
        path = "\\\\.\\pipe\\" + name
        server = kernel32.CreateNamedPipeW(path, 3, 0, 1, 4096, 4096, 0, None)  # duplex, one instance
        assert server not in (None, invalid)
        try:
            client = kernel32.CreateFileW(path, 0x80000000, 0, None, 3, 0, None)  # GENERIC_READ, OPEN_EXISTING
            assert client not in (None, invalid)
            with open(msvcrt.open_osfhandle(client, os.O_RDONLY), encoding="utf-8") as stream:
                assert rotate.echoing_terminal(stream) is echoes
        finally:
            kernel32.CloseHandle(server)
    read_end, write_end = os.pipe()
    os.close(write_end)
    with os.fdopen(read_end, encoding="utf-8") as stream:
        assert rotate.echoing_terminal(stream) is False
    assert rotate.echoing_terminal(io.StringIO("x")) is False


def test_rotate_dry_run_writes_nothing(tmp_path, capsys):
    root, vps_env = tmp_path / "root.env", tmp_path / "vps.env"
    root.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    vps_env.write_bytes(b"AGENT_TOKEN=c14-old-agent-1\n")
    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(root), "--vps-file", str(vps_env), "--dry-run"],
                       runner=LocalRunner())
    assert code == 0
    assert root.read_bytes() == vps_env.read_bytes() == b"AGENT_TOKEN=c14-old-agent-1\n"
    assert "AGENT_TOKEN would be rotated in 2 files" in capsys.readouterr().out


def test_rotate_refuses_a_value_spanning_lines_and_bad_usage(tmp_path, capsys):
    root = tmp_path / "root.env"
    root.write_bytes(b'AGENT_TOKEN="c14-line-1\nc14-line-2"\n')
    assert rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(root)]) == 2
    assert root.read_bytes() == b'AGENT_TOKEN="c14-line-1\nc14-line-2"\n'
    assert "spans several lines" in capsys.readouterr().err
    assert rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(tmp_path / "missing.env")]) == 2
    for argv in (["--name", "AGENT_TOKEN"], ["--name", "bad-name", "--pc-files", str(root)],
                 ["--name", "A", "--alias", "A", "--pc-files", str(root)],
                 ["--name", "A", "--pc-files", str(root), "c14-stray-value"]):
        with pytest.raises(SystemExit) as raised:
            rotate.main(argv)
        assert raised.value.code == 2
    assert "c14-stray-value" not in capsys.readouterr().err
