"""Plan task C-127 review: scripts/ops/rotate_env_secret.py in vps mode. While run/fallback.env.dpapi exists ('agent_ctl
brain vps' DPAPI-wrapped the PC fallback secrets there, user decision D13), a fallback secret named with --pc-files goes
into that store through fallback_env.put_values, never into a PC env file.

Only files under tmp_path are touched (JARVIS_RUN_DIR and the store live there) and ssh never runs: a local runner
executes the exact program that would go to the VPS against a tmp file. A fake DPAPI (a reversible scramble, so no
value is ever plain text in the store) stands in for Windows. Every value is a throwaway fake containing 'c127-fake',
and every test checks that none of them reaches stdout or stderr. The pc-mode behaviour is tested in
test_secrets_tooling.py.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import importlib.util
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_test_res_{name}",
                                                  ROOT / "scripts" / "ops" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rotate = _load("rotate_env_secret")
fe = _load("fallback_env")

MAGIC = b"FAKE-DPAPI:"
ROOT_ENV = ("JARVIS_BRAIN=vps\r\nVORTEX_SECURITY_TOKEN=c127-fake-old-legacy\r\n"
            "VITE_VORTEX_SECURITY_TOKEN=c127-fake-old-legacy\r\nGOOGLE_API_KEY=c127-fake-old-google\r\n"
            "AGENT_TOKEN=c127-fake-agent-stays\r\n")
OVERLAY_ENV = "USER_CITY=Lahore\n"
VPS_ENV = ("GOOGLE_API_KEY=c127-fake-old-google\nVORTEX_SECURITY_TOKEN=c127-fake-old-legacy\n"
           "AGENT_TOKEN=c127-fake-agent-stays\n")


def fake_protect(data: bytes) -> bytes:
    return MAGIC + bytes(b ^ 0x4D for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise OSError("not ours")
    return bytes(b ^ 0x4D for b in blob[len(MAGIC):])


class LocalRunner:
    """Stands in for run_ssh: runs the program like the remote loader does, in this process."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
        self.calls.append({"program": program, "args": args})
        out, code = io.StringIO(), 0
        with redirect_stdout(out):
            try:
                exec(compile(program, "jarvis-remote", "exec"),  # pylint: disable=exec-used
                     {"__name__": "jarvis_remote", "ARGS": json.loads(json.dumps(args))})
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), ""


@pytest.fixture
def vps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """This PC as 'agent_ctl brain vps' leaves it: GOOGLE_API_KEY and the legacy token pair wrapped in the store (fake
    DPAPI), AGENT_TOKEN (no fallback secret) still in the root .env; a VPS env file under tmp_path."""
    root, overlay, vps_env = tmp_path / "root.env", tmp_path / "overlay.env", tmp_path / "agent.env"
    root.write_bytes(ROOT_ENV.encode())
    overlay.write_bytes(OVERLAY_ENV.encode())
    vps_env.write_bytes(VPS_ENV.encode())
    run = tmp_path / "run"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run))          # never the real run/ of this checkout
    monkeypatch.setattr(fe, "dpapi_protect", fake_protect)
    monkeypatch.setattr(fe, "dpapi_unprotect", fake_unprotect)
    monkeypatch.setattr(fe, "restrict_to_user", lambda _path: None)
    # the patched module, as main() would load it (raising=False: the same assertions run against a tool without it)
    monkeypatch.setattr(rotate, "_fallback_env", lambda: fe, raising=False)
    report = fe.wrap([root, overlay], run, brain="vps", worker=False)
    assert report.code == 0 and fe.wrapped_names(run / fe.STORE_NAME) == {
        "GOOGLE_API_KEY", "VORTEX_SECURITY_TOKEN", "VITE_VORTEX_SECURITY_TOKEN"}, report
    return {"root": root, "overlay": overlay, "vps_env": vps_env, "run": run, "store": run / fe.STORE_NAME,
            "pc_files": f"{root},{overlay}"}


def _value(path: Path, name: str) -> str:
    return fe.env_set.EnvFile(path).value(name)


def _stored(env: dict[str, Any]) -> dict[str, str]:
    return fe.load_store(env["store"], fake_unprotect)["root"]


def _files(env: dict[str, Any]) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in (env["root"], env["overlay"], env["store"], env["vps_env"])}


def _output(capsys: pytest.CaptureFixture[str], *values: str) -> str:
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "c127-fake" not in text and not [v for v in values if v in text], "a value reached the output"
    return text


def test_vps_mode_puts_a_rotated_fallback_secret_into_the_store_never_into_an_env_file(vps, capsys):
    """The review case: after 'agent_ctl brain vps' wrapped GOOGLE_API_KEY, a rotation with --pc-files appended the new
    value in plain text to the root .env while the store kept the old one (D13 undone). Now the PC side goes into the
    store, the given env files lose any copy of it, and 'agent_ctl brain pc' later restores the new value."""
    with open(vps["overlay"], "ab") as handle:            # a stale plain copy someone pasted after the wrap
        handle.write(b"GOOGLE_API_KEY=c127-fake-stale-google\n")
    runner = LocalRunner()
    code = rotate.main(["--name", "GOOGLE_API_KEY", "--pc-files", vps["pc_files"], "--vps-file", str(vps["vps_env"]),
                        "--value-from-stdin"], runner=runner, stdin=io.StringIO("c127-fake-new-google\n"))
    assert code == 0
    on_pc = vps["root"].read_bytes() + vps["overlay"].read_bytes()
    assert b"GOOGLE_API_KEY" not in on_pc and b"c127-fake-new-google" not in on_pc
    assert _stored(vps)["GOOGLE_API_KEY"] == "c127-fake-new-google"
    assert _value(vps["vps_env"], "GOOGLE_API_KEY") == "c127-fake-new-google"
    assert _value(vps["root"], "AGENT_TOKEN") == "c127-fake-agent-stays" and _value(vps["overlay"], "USER_CITY")
    assert not [p for p in vps["root"].parent.rglob("*") if p.name.endswith((".tmp", ".part"))]
    text = _output(capsys)
    assert "GOOGLE_API_KEY rotated in 2 files" in text and "(vps mode)" in text and "removed GOOGLE_API_KEY" in text
    # 'agent_ctl brain pc' (fallback_env.restore): the env files get the new value, never the stale old one
    assert fe.restore([vps["root"], vps["overlay"]], vps["run"]).code == 0
    assert _value(vps["root"], "GOOGLE_API_KEY") == "c127-fake-new-google" and not vps["store"].exists()


def test_vps_mode_a_generated_value_and_its_alias_go_into_the_store(vps, capsys):
    """The C-22 legacy-token step (one last rotation, PC files plus a VPS file) in vps mode: a generated value for
    VORTEX_SECURITY_TOKEN and its alias in one run; the alias follows into the store (this PC holds it there) and, on
    the VPS, goes only where the file has it."""
    code = rotate.main(["--name", "VORTEX_SECURITY_TOKEN", "--alias", "VITE_VORTEX_SECURITY_TOKEN",
                        "--pc-files", vps["pc_files"], "--vps-file", str(vps["vps_env"])], runner=LocalRunner())
    assert code == 0
    stored = _stored(vps)
    new = stored["VORTEX_SECURITY_TOKEN"]
    assert new != "c127-fake-old-legacy" and len(new) == 43 and stored["VITE_VORTEX_SECURITY_TOKEN"] == new
    assert _value(vps["vps_env"], "VORTEX_SECURITY_TOKEN") == new
    assert "VITE_VORTEX_SECURITY_TOKEN" not in fe.env_set.EnvFile(vps["vps_env"]).values
    assert new.encode() not in vps["root"].read_bytes() + vps["overlay"].read_bytes()
    text = _output(capsys, new)
    assert "VORTEX_SECURITY_TOKEN rotated in 2 files" in text
    assert "VITE_VORTEX_SECURITY_TOKEN rotated in 1 files" in text


@pytest.mark.parametrize("cause", ["other-windows-user", "env-file-not-writable"])
def test_vps_mode_refuses_before_the_vps_step_when_this_pc_cannot_take_the_value(vps, capsys, monkeypatch, cause):
    """put_values runs with check before the VPS step (nothing written): a store this Windows user cannot unwrap, or an
    env file that must lose a stale copy but cannot be written, stops the run with nothing written anywhere."""
    if cause == "other-windows-user":
        def other_user(_blob: bytes) -> bytes:
            raise OSError("DPAPI could not unwrap the data (another user)")

        monkeypatch.setattr(fe, "dpapi_unprotect", other_user)
    else:
        with open(vps["root"], "ab") as handle:
            handle.write(b"GOOGLE_API_KEY=c127-fake-stale-google\r\n")
        vps["root"].with_name(vps["root"].name + fe.gen_secrets.TEMP_SUFFIX).mkdir()   # its temp file is blocked
    before = _files(vps)
    runner = LocalRunner()
    code = rotate.main(["--name", "GOOGLE_API_KEY", "--pc-files", vps["pc_files"], "--vps-file", str(vps["vps_env"]),
                        "--value-from-stdin"], runner=runner, stdin=io.StringIO("c127-fake-new-google\n"))
    assert code == 2 and not runner.calls                                  # the VPS was never contacted
    assert _files(vps) == before
    assert "nothing was written on the VPS or this PC" in _output(capsys)


def test_vps_mode_dry_run_writes_nothing_and_names_the_store(vps, capsys):
    before = _files(vps)
    code = rotate.main(["--name", "GOOGLE_API_KEY", "--pc-files", vps["pc_files"], "--vps-file", str(vps["vps_env"]),
                        "--value-from-stdin", "--dry-run"], runner=LocalRunner(),
                       stdin=io.StringIO("c127-fake-new-google\n"))
    assert code == 0 and _files(vps) == before
    text = _output(capsys)
    assert "would set GOOGLE_API_KEY" in text and "would be rotated in 2 files" in text and "vps mode" in text


def test_vps_mode_a_pc_failure_after_the_vps_step_puts_the_store_back(vps, capsys, monkeypatch):
    """The store cannot be replaced once the VPS file holds the new value: put_values puts the store back byte for
    byte, the env files stay, and the message says that the VPS has the new value (exit 1)."""
    before = _files(vps)
    real = os.replace

    def locked(src: Any, dst: Any) -> None:
        if Path(dst) == vps["store"]:
            raise PermissionError(13, "The process cannot access the file because another process uses it")
        real(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    code = rotate.main(["--name", "GOOGLE_API_KEY", "--pc-files", vps["pc_files"], "--vps-file", str(vps["vps_env"]),
                        "--value-from-stdin"], runner=LocalRunner(), stdin=io.StringIO("c127-fake-new-google\n"))
    monkeypatch.setattr(os, "replace", real)
    assert code == 1
    assert _value(vps["vps_env"], "GOOGLE_API_KEY") == "c127-fake-new-google"
    after = _files(vps)
    assert all(after[path] == before[path] for path in (vps["root"], vps["overlay"], vps["store"]))
    text = _output(capsys)
    assert "already holds the new GOOGLE_API_KEY" in text and "run the same command again" in text


def test_vps_mode_a_name_that_is_no_fallback_secret_still_goes_into_the_env_files(vps, capsys):
    """AGENT_TOKEN is not wrapped (push_agent_env.py reads it): it is rotated in the PC files as in pc mode, and the
    store stays as it was."""
    store = vps["store"].read_bytes()
    code = rotate.main(["--name", "AGENT_TOKEN", "--pc-files", str(vps["root"]), "--vps-file", str(vps["vps_env"])],
                       runner=LocalRunner())
    assert code == 0 and vps["store"].read_bytes() == store
    new = _value(vps["root"], "AGENT_TOKEN")
    assert new != "c127-fake-agent-stays" and _value(vps["vps_env"], "AGENT_TOKEN") == new
    assert "AGENT_TOKEN rotated in 2 files" in _output(capsys, new)


def test_vps_mode_refuses_a_name_and_alias_of_which_only_one_is_a_fallback_secret(vps, capsys):
    """One would go into the store and the other into the env files: refused before anything (exit 2)."""
    before = _files(vps)
    runner = LocalRunner()
    code = rotate.main(["--name", "AGENT_TOKEN", "--alias", "GOOGLE_API_KEY", "--pc-files", vps["pc_files"],
                        "--vps-file", str(vps["vps_env"])], runner=runner)
    assert code == 2 and not runner.calls and _files(vps) == before
    assert "rotate them one at a time" in _output(capsys)


def test_without_the_store_a_fallback_name_is_rotated_in_the_env_files_as_before(tmp_path, capsys, monkeypatch):
    """pc mode (no store): the store route never applies; the value goes into the env files."""
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "run"))
    root = tmp_path / "root.env"
    root.write_bytes(b"GOOGLE_API_KEY=c127-fake-old-google\n")
    code = rotate.main(["--name", "GOOGLE_API_KEY", "--pc-files", str(root), "--value-from-stdin"],
                       stdin=io.StringIO("c127-fake-new-google\n"))
    assert code == 0 and root.read_bytes() == b"GOOGLE_API_KEY=c127-fake-new-google\n"
    assert not (tmp_path / "run").exists()
    assert "GOOGLE_API_KEY rotated in 1 files" in _output(capsys)
