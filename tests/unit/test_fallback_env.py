"""Plan task C-127 step 5 (D13): scripts/ops/fallback_env.py moves the PC fallback secrets into the DPAPI-wrapped
run/fallback.env.dpapi in vps mode and back for 'agent_ctl brain pc', printing names only.

Only files under tmp_path are touched. A fake DPAPI (a reversible scramble, so no value is ever plaintext in the store)
and a recording ACL call stand in for Windows; one test runs the real DPAPI helper on Windows. Every value is a
throwaway fake containing 'c127-fake', and every test checks that none of them reaches stdout or stderr.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("jarvis_ops_test_fallback_env",
                                              ROOT / "scripts" / "ops" / "fallback_env.py")
fe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fe)

ROOT_ENV = (
    "# PC root .env\r\n"
    "JARVIS_BRAIN=vps\r\n"
    "LIVEKIT_API_KEY=APIkeep\r\n"
    "LIVEKIT_API_SECRET=c127-fake-livekit\r\n"
    "\r\n"
    "GOOGLE_API_KEY=c127-fake-google  # Gemini\r\n"
    "EMAIL_USER=owner@example.test\r\n"
    "EMAIL_APP_PASSWORD='c127-fake mail'\r\n"
    "AGENT_TOKEN=c127-fake-agent-stays\r\n"
    "JARVIS_ENCRYPTION_KEY=c127-fake-fernet-stays\r\n"
)
OVERLAY_ENV = "VORTEX_SECURITY_TOKEN=c127-fake-legacy\nUSER_CITY=Lahore\n"
WRAPPED = {"root": ["EMAIL_APP_PASSWORD", "GOOGLE_API_KEY", "LIVEKIT_API_SECRET"], "overlay": ["VORTEX_SECURITY_TOKEN"]}
MAGIC = b"FAKE-DPAPI:"


def fake_protect(data: bytes) -> bytes:
    return MAGIC + bytes(b ^ 0x5A for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise OSError("not ours")
    return bytes(b ^ 0x5A for b in blob[len(MAGIC):])


class Acl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def __call__(self, path: Path) -> None:
        self.calls.append((Path(path).name, Path(path).stat().st_size))   # restricted while still empty


@pytest.fixture
def pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    root, overlay = tmp_path / "root.env", tmp_path / "overlay.env"
    root.write_bytes(ROOT_ENV.encode())
    overlay.write_bytes(OVERLAY_ENV.encode())
    acl = Acl()
    monkeypatch.setattr(fe, "dpapi_protect", fake_protect)
    monkeypatch.setattr(fe, "dpapi_unprotect", fake_unprotect)
    monkeypatch.setattr(fe, "restrict_to_user", acl)
    monkeypatch.setattr(fe, "worker_running", lambda: False)
    monkeypatch.delenv("JARVIS_BRAIN", raising=False)
    return {"paths": [root, overlay], "root": root, "overlay": overlay, "run": tmp_path / "run",
            "store": tmp_path / "run" / fe.STORE_NAME, "acl": acl}


def _no_values(*texts: str) -> None:
    for text in texts:
        assert "c127-fake" not in text, "a value reached the output"


def _report_text(report: Any) -> str:
    text = "\n".join(report.lines)
    _no_values(text)
    return text


def test_wrap_moves_the_fallback_secrets_into_the_store(pc) -> None:
    report = fe.wrap(pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 0 and report.changed, text
    assert "wrapped 4 values" in text
    root_text = pc["root"].read_bytes().decode()
    # only the wrapped assignments left; every other byte (blank line, comment lines, the stays) is the same
    assert root_text == ("# PC root .env\r\nJARVIS_BRAIN=vps\r\nLIVEKIT_API_KEY=APIkeep\r\n\r\n"
                         "EMAIL_USER=owner@example.test\r\nAGENT_TOKEN=c127-fake-agent-stays\r\n"
                         "JARVIS_ENCRYPTION_KEY=c127-fake-fernet-stays\r\n")
    assert pc["overlay"].read_text(encoding="utf-8") == "USER_CITY=Lahore\n"
    raw = pc["store"].read_bytes()
    _no_values(raw.decode("utf-8"))                                   # never plaintext in the store
    header = json.loads(raw)
    assert header["kind"] == fe.STORE_KIND and header["names"] == WRAPPED
    assert fe.wrapped_names(pc["store"]) == {name for names in WRAPPED.values() for name in names}
    assert pc["acl"].calls and pc["acl"].calls[0][1] == 0             # the ACL came before the content
    assert sorted(p.name for p in pc["run"].iterdir()) == [fe.STORE_NAME]   # no temp file left


def test_wrap_refuses_in_pc_mode_or_beside_a_worker_and_dry_run_writes_nothing(pc) -> None:
    before = [p.read_bytes() for p in pc["paths"]]
    pc["root"].write_bytes(ROOT_ENV.replace("JARVIS_BRAIN=vps", "JARVIS_BRAIN=pc").encode())
    report = fe.wrap(pc["paths"], pc["run"])
    assert report.code == 1 and "is pc, not vps" in _report_text(report)
    pc["root"].write_bytes(before[0])
    report = fe.wrap(pc["paths"], pc["run"], worker=True)
    assert report.code == 1 and "a PC worker holds run/agent.lock" in _report_text(report)
    report = fe.wrap(pc["paths"], pc["run"], dry_run=True)
    assert report.code == 0 and "dry run: nothing written" in _report_text(report)
    assert [p.read_bytes() for p in pc["paths"]] == before and not pc["store"].exists()


def test_restore_puts_each_value_back_into_its_file_and_deletes_the_store(pc) -> None:
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    report = fe.restore(pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 0 and "restored" in text, text
    assert not pc["store"].exists()
    root, overlay = fe.env_set.EnvFile(pc["root"]), fe.env_set.EnvFile(pc["overlay"])
    assert root.value("GOOGLE_API_KEY") == "c127-fake-google"
    assert root.value("EMAIL_APP_PASSWORD") == "c127-fake mail"
    assert root.value("LIVEKIT_API_SECRET") == "c127-fake-livekit"
    assert overlay.value("VORTEX_SECURITY_TOKEN") == "c127-fake-legacy"
    assert "VORTEX_SECURITY_TOKEN" not in root.values


def test_restore_keeps_a_newer_file_value_and_a_wrap_merges(pc) -> None:
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    with open(pc["root"], "ab") as fh:
        fh.write(b"GOOGLE_API_KEY=c127-fake-rotated\r\nGROQ_API_KEY=c127-fake-groq\r\n")
    report = fe.wrap(pc["paths"], pc["run"])                    # merges: the newer file value wins
    assert report.code == 0 and "2 to wrap" in _report_text(report)
    assert fe.load_store(pc["store"], fake_unprotect)["root"]["GOOGLE_API_KEY"] == "c127-fake-rotated"
    with open(pc["root"], "ab") as fh:
        fh.write(b"GROQ_API_KEY=c127-fake-groq-newer\r\n")
    report = fe.restore(pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 0 and "kept the file's own newer value: GROQ_API_KEY" in text
    assert fe.env_set.EnvFile(pc["root"]).value("GROQ_API_KEY") == "c127-fake-groq-newer"


def test_restore_failures_keep_the_store_and_the_files(pc, monkeypatch) -> None:
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    files, store = [p.read_bytes() for p in pc["paths"]], pc["store"].read_bytes()

    def other_user(_blob: bytes) -> bytes:
        raise OSError("DPAPI could not unwrap the data (another user, or a damaged file)")

    monkeypatch.setattr(fe, "dpapi_unprotect", other_user)
    report = fe.restore(pc["paths"], pc["run"])
    assert report.code == 1 and "could not be unwrapped by this Windows user (OSError)" in _report_text(report)
    assert [p.read_bytes() for p in pc["paths"]] == files and pc["store"].read_bytes() == store
    # the hook agent_ctl calls never raises
    report = fe.restore_for_brain_pc(pc["paths"], pc["run"])
    assert report.code == 1 and pc["store"].exists()


def test_status_names_only_and_set_follows_the_value(pc) -> None:
    report = fe.status(pc["paths"], pc["run"])
    text = _report_text(report)
    assert "3 fallback names: LIVEKIT_API_SECRET, GOOGLE_API_KEY, EMAIL_APP_PASSWORD" in text and "none" in text
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    text = _report_text(fe.status(pc["paths"], pc["run"]))
    assert "4 wrapped names" in text
    # --set: a wrapped name goes into the store (never back into an env file); another into the root .env
    code = fe.main(["--set", "GOOGLE_API_KEY", "--value-from-stdin", "--env-file", str(pc["root"]), "--env-file",
                    str(pc["overlay"]), "--run-dir", str(pc["run"])], stdin=io.StringIO("c127-fake-new-google\n"))
    assert code == 0
    assert fe.load_store(pc["store"], fake_unprotect)["root"]["GOOGLE_API_KEY"] == "c127-fake-new-google"
    assert "GOOGLE_API_KEY" not in fe.env_set.EnvFile(pc["root"]).values
    code = fe.main(["--set", "JARVIS_TEXT_MODEL", "--value-from-stdin", "--env-file", str(pc["root"]), "--run-dir",
                    str(pc["run"])], stdin=io.StringIO("c127-fake-model\n"))
    assert code == 0 and fe.env_set.EnvFile(pc["root"]).value("JARVIS_TEXT_MODEL") == "c127-fake-model"


def test_cli_output_is_names_only(pc, capsys) -> None:
    args = ["--env-file", str(pc["root"]), "--env-file", str(pc["overlay"]), "--run-dir", str(pc["run"])]
    assert fe.main(["--wrap", *args]) == 0
    assert fe.main(["--status", *args]) == 0
    assert fe.main(["--restore", "--dry-run", *args]) == 0
    assert fe.main(["--restore", *args]) == 0
    out = capsys.readouterr()
    _no_values(out.out, out.err)
    for argv in (["--set", "X"], ["--wrap", "--value-from-stdin"], ["--status", "--dry-run"], ["c127-fake-typed"]):
        with pytest.raises(SystemExit) as stop:
            fe.parse_args(argv)
        assert stop.value.code == 2
    _no_values(capsys.readouterr().err)


def test_an_emptied_store_is_removed_and_a_broken_one_refused(pc) -> None:
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    fe.write_store(pc["store"], {"root": {}}, protect=fake_protect, unprotect=fake_unprotect, restrict=pc["acl"])
    assert not pc["store"].exists()
    pc["run"].mkdir(exist_ok=True)
    doc = {"kind": fe.STORE_KIND, "v": 1, "created": 1, "names": {"root": ["A"]},
           "blob": base64.b64encode(fake_protect(json.dumps({"kind": fe.STORE_KIND, "v": 1,
                                                             "files": {"root": {"B": "c127-fake-b"}}}).encode()))
           .decode()}
    pc["store"].write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(fe.EnvError, match="header and its wrapped values name different keys"):
        fe.load_store(pc["store"], fake_unprotect)


def test_wrap_refuses_and_status_names_a_pc_copy_of_a_vps_env_file(pc) -> None:
    """C-127 review: --wrap and --status looked only at the root .env and the overlay, while this laptop also held a
    git-ignored, stale deploy/hostinger/.env (a copy of the pre-split VPS compose .env) with fallback secrets in plain
    text, which the Desktop Node would find on disk in vps mode. Now both scan the known PC copies (PC_COPY_NAMES, the
    files the pre-commit hook scans) and name them; --wrap refuses until they are moved out of the repo."""
    hostinger = pc["root"].parent / "deploy" / "hostinger"
    hostinger.mkdir(parents=True)
    stale = hostinger / ".env"
    stale.write_bytes(b"JARVIS_BRAIN=pc\nLIVEKIT_API_KEY=APIstale\nLIVEKIT_API_SECRET=c127-fake-stale-livekit\n"
                      b"GOOGLE_API_KEY=c127-fake-stale-google\nVITE_VORTEX_SECURITY_TOKEN=\n")
    (hostinger / "bridge.env").write_bytes(b"AGENT_TOKEN=c127-fake-bridge-agent\nLEGACY_TOKEN_AUTH=1\n")
    files = {p: p.read_bytes() for p in pc["paths"]}
    for dry_run in (True, False):
        report = fe.wrap(pc["paths"], pc["run"], dry_run=dry_run)
        text = _report_text(report)
        assert report.code == 1 and not report.changed, text
        assert "(LIVEKIT_API_SECRET, GOOGLE_API_KEY): other PC copies of VPS env files" in text   # an empty one: no
        assert "Before V4" in text and "bridge.env" not in text
    report = fe.wrap_for_brain_vps(pc["paths"], pc["run"], worker=False)          # 'agent_ctl brain vps' meets it too
    assert report.code == 1 and "other PC copies of VPS env files" in _report_text(report)
    assert {p: p.read_bytes() for p in pc["paths"]} == files and not pc["store"].exists()
    text = _report_text(fe.status(pc["paths"], pc["run"]))
    assert "(PC copy of a VPS env file): 2 fallback names in plain text: LIVEKIT_API_SECRET, GOOGLE_API_KEY" in text
    assert "bridge.env (PC copy of a VPS env file): no fallback name" in text
    # a copy that cannot be read may hold them as well
    stale.write_bytes(b"\xff\xfeGOOGLE_API_KEY=c127-fake-unreadable\n")
    report = fe.wrap(pc["paths"], pc["run"])
    assert report.code == 1 and "unreadable, names unknown" in _report_text(report)
    assert "unreadable, names unknown; move it out of the repo" in _report_text(fe.status(pc["paths"], pc["run"]))
    # moved out of the repo (RUNBOOK 'Before V4'): the wrap goes ahead; bridge.env holds no fallback secret
    stale.unlink()
    report = fe.wrap(pc["paths"], pc["run"])
    assert report.code == 0 and "wrapped 4 values" in _report_text(report)


def test_the_pc_copies_are_the_files_the_pre_commit_hook_scans() -> None:
    """PC_COPY_NAMES are the pre-commit hook's ENV_FILES beyond the two PC env files (scripts/coord/precommit.py), so
    --wrap refuses for every copy whose values the hook guards. Paths only: no env file is read."""
    hook_spec = importlib.util.spec_from_file_location("jarvis_coord_precommit_for_fallback_env",
                                                       ROOT / "scripts" / "coord" / "precommit.py")
    hook = importlib.util.module_from_spec(hook_spec)
    try:
        hook_spec.loader.exec_module(hook)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("not a git checkout")
    pc_files = [Path(p).resolve().relative_to(ROOT).as_posix() for p in fe.ENV_PATHS]
    assert list(hook.ENV_FILES) == pc_files + list(fe.PC_COPY_NAMES)
    assert fe.pc_copies(fe.ENV_PATHS) == [ROOT / name for name in fe.PC_COPY_NAMES]
    assert fe.pc_copies([ROOT / ".env", ROOT / "deploy" / "hostinger" / ".env"]) == [
        ROOT / name for name in fe.PC_COPY_NAMES[1:]]                 # an env file given is never its own copy
    assert hook.FALLBACK_STORE == fe.STORE_NAME


def test_the_wrap_hook_never_raises(pc, monkeypatch) -> None:
    def broken(_data: bytes) -> bytes:
        raise OSError("no DPAPI here")

    monkeypatch.setattr(fe, "dpapi_protect", broken)
    report = fe.wrap_for_brain_vps(pc["paths"], pc["run"], worker=False)
    assert report.code == 1 and "DPAPI could not wrap the values (OSError)" in _report_text(report)
    assert pc["root"].read_bytes() == ROOT_ENV.encode() and not pc["store"].exists()


# --------------------------------------------------------------------------- put_values / move_values (C-127 review)

PENDING = {"LIVEKIT_PC_API_KEY": "LIVEKIT_API_KEY", "LIVEKIT_PC_API_SECRET": "LIVEKIT_API_SECRET"}


def _vps_mode_with_a_pending_pair(pc: dict[str, Any]) -> None:
    """vps mode after 'livekit_pc_pair.py create': both secrets wrapped in the store, the keys in the root .env."""
    pc["root"].write_bytes(b"JARVIS_BRAIN=vps\r\nLIVEKIT_API_KEY=APIkeep\r\nLIVEKIT_PC_API_KEY=APIpcpair\r\n"
                           b"USER_NAME=Owner\r\n")
    fe.write_store(pc["store"], {"root": {"LIVEKIT_API_SECRET": "c127-fake-main-secret",
                                          "LIVEKIT_PC_API_SECRET": "c127-fake-pending-secret"}},
                   protect=fake_protect, unprotect=fake_unprotect, restrict=pc["acl"])


def _failing_replace(monkeypatch: pytest.MonkeyPatch, *, env: Path, store: Path | None = None,
                     store_calls_ok: int = 1) -> None:
    """os.replace as on Windows while another program (the Desktop Node, a virus scan) holds `env` open: every rename
    over it fails. With `store`, the renames over the store fail too after the first `store_calls_ok`."""
    real = os.replace
    seen = {"store": 0}

    def replace(src: Any, dst: Any) -> None:
        if Path(dst) == env:
            raise PermissionError(13, "The process cannot access the file because another process uses it")
        if store is not None and Path(dst) == store:
            seen["store"] += 1
            if seen["store"] > store_calls_ok:
                raise PermissionError(13, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(os, "replace", replace)


def test_move_values_puts_the_store_back_when_the_env_files_cannot_be_written(pc, monkeypatch) -> None:
    """Review finding: the store used to be written before the env files; when the env write then failed, the report
    said 'no file was changed' while the store already held the PC secret under LIVEKIT_API_SECRET, the main secret and
    the pending one were gone, and the root .env still named the main key. Now the env files are staged first and the
    store goes back byte for byte, so 'activate' can simply run again."""
    _vps_mode_with_a_pending_pair(pc)
    files, store = {p: p.read_bytes() for p in pc["paths"]}, pc["store"].read_bytes()
    real_replace = os.replace
    _failing_replace(monkeypatch, env=pc["root"])
    report = fe.move_values(PENDING, pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 1 and not report.changed, text
    assert "no file was changed" not in text
    assert "failed: the env files were not written" in text and "the env files are as they were" in text
    assert "was put back byte for byte" in text
    assert pc["store"].read_bytes() == store and {p: p.read_bytes() for p in pc["paths"]} == files
    assert sorted(p.name for p in pc["run"].iterdir()) == [fe.STORE_NAME]
    assert sorted(p.name for p in pc["root"].parent.iterdir() if p.is_file()) == ["overlay.env", "root.env"]
    found = fe.find_values(PENDING, pc["paths"], pc["run"], unprotect=fake_unprotect)
    assert found == {"LIVEKIT_PC_API_KEY": "APIpcpair", "LIVEKIT_PC_API_SECRET": "c127-fake-pending-secret"}
    # the cause gone, the same command works
    monkeypatch.setattr(os, "replace", real_replace)
    report = fe.move_values(PENDING, pc["paths"], pc["run"])
    assert report.code == 0, _report_text(report)
    assert fe.load_store(pc["store"], fake_unprotect) == {"root": {"LIVEKIT_API_SECRET": "c127-fake-pending-secret"}}
    root = fe.env_set.EnvFile(pc["root"])
    assert root.value("LIVEKIT_API_KEY") == "APIpcpair" and "LIVEKIT_PC_API_KEY" not in root.values


@pytest.mark.parametrize("cause", ["temp file blocked", "read-only"])
def test_a_staging_failure_changes_neither_the_store_nor_the_env_files(pc, cause) -> None:
    """The env files are staged before the store is written, so a root .env that cannot be written (read-only on
    Windows, or its temp file blocked) stops the run while nothing has changed: 'no file was changed' is true."""
    if cause == "read-only" and os.name != "nt":
        pytest.skip("gen_secrets.stage_private checks read-only files on Windows (POSIX renames over them)")
    _vps_mode_with_a_pending_pair(pc)
    files, store = {p: p.read_bytes() for p in pc["paths"]}, pc["store"].read_bytes()
    blocker = pc["root"].with_name(pc["root"].name + fe.gen_secrets.TEMP_SUFFIX)
    if cause == "read-only":
        os.chmod(pc["root"], stat.S_IREAD)
    else:
        blocker.mkdir()
    try:
        report = fe.move_values(PENDING, pc["paths"], pc["run"])
    finally:
        os.chmod(pc["root"], stat.S_IREAD | stat.S_IWRITE)
    text = _report_text(report)
    assert report.code == 1 and not report.changed and "no file was changed" in text, text
    assert pc["store"].read_bytes() == store and {p: p.read_bytes() for p in pc["paths"]} == files


def test_a_store_that_cannot_be_put_back_is_reported_never_as_unchanged(pc, monkeypatch) -> None:
    """The double failure: the env files cannot be written and the old store cannot go back either. The report says
    so (names only) instead of 'no file was changed'."""
    _vps_mode_with_a_pending_pair(pc)
    _failing_replace(monkeypatch, env=pc["root"], store=pc["store"], store_calls_ok=1)
    report = fe.move_values(PENDING, pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 1 and report.changed, text
    assert "no file was changed" not in text and "could NOT be put back (PermissionError)" in text
    assert "fallback_env.py --status" in text


def test_a_failed_store_write_puts_nothing_in_the_env_files(pc, monkeypatch) -> None:
    """The store write itself failing (here its read-back check) puts the old store back; the staged env files are
    discarded, so neither the env files nor their temp files change."""
    _vps_mode_with_a_pending_pair(pc)
    files, store = {p: p.read_bytes() for p in pc["paths"]}, pc["store"].read_bytes()
    real_load = fe.load_store
    calls = {"n": 0}

    def load_twice_then_differ(path: Path, unprotect: Any) -> dict[str, dict[str, str]]:
        calls["n"] += 1
        got = real_load(path, unprotect)
        return got if calls["n"] < 3 else {"root": {}}   # find_values, put_values, then write_store's read-back

    monkeypatch.setattr(fe, "load_store", load_twice_then_differ)
    report = fe.move_values(PENDING, pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 1 and "did not read back as written" in text and "was put back byte for byte" in text, text
    assert pc["store"].read_bytes() == store and {p: p.read_bytes() for p in pc["paths"]} == files
    assert sorted(p.name for p in pc["root"].parent.iterdir() if p.is_file()) == ["overlay.env", "root.env"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_move_values_leaves_no_copy_of_a_target_in_another_env_file(pc, wrapped) -> None:
    """Review finding: deploy/windows-local/.env may hold the main LiveKit pair (its template lists both). After
    'activate' this PC must hold no copy of it, with or without the store (pc mode allows activate too)."""
    pc["root"].write_bytes(b"LIVEKIT_API_KEY=APIkeep\r\nLIVEKIT_API_SECRET=c127-fake-main-secret\r\n"
                           b"LIVEKIT_PC_API_KEY=APIpcpair\r\nLIVEKIT_PC_API_SECRET=c127-fake-pending-secret\r\n")
    pc["overlay"].write_bytes(b"LIVEKIT_URL=wss://example.invalid\nLIVEKIT_API_KEY=APIkeep\n"
                              b"LIVEKIT_API_SECRET=c127-fake-main-secret\nUSER_CITY=Lahore\n")
    if wrapped:
        report = fe.wrap(pc["paths"], pc["run"], brain="vps", worker=False)
        assert report.code == 0, _report_text(report)
    overlay_label = fe.env_set.EnvFile(pc["overlay"]).label
    report = fe.move_values(PENDING, pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 0, text
    # the overlay line names what it lost (its secret was already wrapped in the vps-mode case)
    assert f"{overlay_label}: removed LIVEKIT_API_KEY" in text
    assert (f"{overlay_label}: removed LIVEKIT_API_KEY, LIVEKIT_API_SECRET" in text) == (not wrapped)
    overlay = fe.env_set.EnvFile(pc["overlay"])
    assert "LIVEKIT_API_KEY" not in overlay.values and "LIVEKIT_API_SECRET" not in overlay.values
    assert overlay.value("LIVEKIT_URL") == "wss://example.invalid" and overlay.value("USER_CITY") == "Lahore"
    found = fe.find_values(["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], pc["paths"], pc["run"], unprotect=fake_unprotect)
    assert found == {"LIVEKIT_API_KEY": "APIpcpair", "LIVEKIT_API_SECRET": "c127-fake-pending-secret"}
    held = fe.load_store(pc["store"], fake_unprotect) if wrapped else {}
    on_disk = b"".join(p.read_bytes() for p in pc["paths"]) + json.dumps(held).encode()
    assert b"c127-fake-main-secret" not in on_disk and b"APIkeep" not in on_disk


def test_put_values_check_writes_nothing_and_reports_what_a_run_would_do(pc, monkeypatch) -> None:
    """C-127 review: rotate_livekit_keys.py changes the VPS before this PC, so it asks first with check=True: every step
    before the store write runs (the unwrap and the staging included), nothing is written, and the report names what a
    run would set and remove. A refusal there is the one the run would meet."""
    pc["overlay"].write_bytes(b"LIVEKIT_API_KEY=APIkeep\nUSER_CITY=Lahore\n")
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    files, store = {p: p.read_bytes() for p in pc["paths"]}, pc["store"].read_bytes()
    values = {"LIVEKIT_API_KEY": "APInewkey", "LIVEKIT_API_SECRET": "c127-fake-new-livekit"}
    report = fe.put_values(values, pc["paths"], pc["run"], exclusive=True, check=True)
    text = _report_text(report)
    assert report.code == 0 and not report.changed, text
    root_label, overlay_label = (fe.env_set.EnvFile(p).label for p in pc["paths"])
    assert f"{fe.STORE_NAME}: would set LIVEKIT_API_SECRET" in text
    assert f"{root_label}: would set LIVEKIT_API_KEY" in text
    assert f"{overlay_label}: would remove LIVEKIT_API_KEY" in text
    assert pc["store"].read_bytes() == store and {p: p.read_bytes() for p in pc["paths"]} == files
    assert sorted(p.name for p in pc["root"].parent.iterdir() if p.is_file()) == ["overlay.env", "root.env"]
    assert sorted(p.name for p in pc["run"].iterdir()) == [fe.STORE_NAME]          # no temp file left anywhere

    def other_user(_blob: bytes) -> bytes:
        raise OSError("DPAPI could not unwrap the data (another user)")

    with monkeypatch.context() as patched:
        patched.setattr(fe, "dpapi_unprotect", other_user)
        report = fe.put_values(values, pc["paths"], pc["run"], exclusive=True, check=True)
    assert report.code == 1 and "could not be unwrapped by this Windows user" in _report_text(report)
    assert pc["store"].read_bytes() == store and {p: p.read_bytes() for p in pc["paths"]} == files
    # the run itself then does what the check said
    report = fe.put_values(values, pc["paths"], pc["run"], exclusive=True)
    assert report.code == 0 and report.changed, _report_text(report)
    assert fe.load_store(pc["store"], fake_unprotect)["root"]["LIVEKIT_API_SECRET"] == "c127-fake-new-livekit"
    assert fe.env_set.EnvFile(pc["root"]).value("LIVEKIT_API_KEY") == "APInewkey"
    assert "LIVEKIT_API_KEY" not in fe.env_set.EnvFile(pc["overlay"]).values


# --------------------------------------------------------------------------- --discard (C-127 review)


def _other_user(_blob: bytes) -> bytes:
    raise OSError("DPAPI could not unwrap the data (another Windows user, or a rebuilt profile)")


def _cli(pc: dict[str, Any], *argv: str) -> list[str]:
    return [*argv, "--env-file", str(pc["root"]), "--env-file", str(pc["overlay"]), "--run-dir", str(pc["run"])]


@pytest.mark.parametrize("cause", ["other-windows-user", "damaged"])
def test_discard_moves_a_store_this_user_can_never_unwrap_aside_and_the_values_can_be_entered_again(
        pc, monkeypatch, capsys, cause) -> None:
    """C-127 review: while run/fallback.env.dpapi exists every PC worker start is refused, and 'agent_ctl brain pc'
    cannot restore a store this Windows user can never unwrap (the repo copied to another laptop, a rebuilt profile);
    nothing said what to do, so the PC fallback stayed down. The refusal now names the way out, and --discard moves
    the store aside after a confirmation flag (a rename, never a delete); then --set enters the values again."""
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    if cause == "other-windows-user":
        monkeypatch.setattr(fe, "dpapi_unprotect", _other_user)
        held = "it holds 4 names: EMAIL_APP_PASSWORD, GOOGLE_API_KEY, LIVEKIT_API_SECRET, VORTEX_SECURITY_TOKEN"
        why = "could not be unwrapped by this Windows user (OSError): the PC fallback cannot start while it is there"
    else:
        pc["store"].write_bytes(b"\x00 not a store")
        held = "its names cannot be read"
        why = "is not a fallback store this Windows user can read"
    store = pc["store"].read_bytes()
    # the refusal that 'agent_ctl brain pc' prints (fallback_env.restore) names the way out
    report = fe.restore(pc["paths"], pc["run"])
    text = _report_text(report)
    assert report.code == 1 and "fallback_env.py --discard --confirm" in text, text
    assert "'The PC fallback secrets'" in text and pc["store"].read_bytes() == store
    # a confirmation flag is needed; --dry-run writes nothing
    for argv in (["--discard"], ["--discard", "--confirm", "--dry-run"], ["--status", "--confirm"]):
        with pytest.raises(SystemExit) as stop:
            fe.parse_args(_cli(pc, *argv))
        assert stop.value.code == 2
    assert fe.main(_cli(pc, "--discard", "--dry-run")) == 0
    assert pc["store"].read_bytes() == store and sorted(p.name for p in pc["run"].iterdir()) == [fe.STORE_NAME]
    out = capsys.readouterr()
    assert why in out.out and held in out.out and "dry run" in out.out and "nothing written" in out.out, out.out
    _no_values(out.out, out.err)
    # --confirm: renamed aside byte for byte, never deleted
    assert fe.main(_cli(pc, "--discard", "--confirm")) == 0
    assert not pc["store"].exists()
    (aside,) = list(pc["run"].iterdir())
    assert aside.name.startswith(fe.STORE_NAME + ".unreadable-") and aside.read_bytes() == store
    out = capsys.readouterr()
    assert held in out.out and f"{aside.name}: never deleted" in out.out and "moved " in out.out
    assert "--set NAME --value-from-stdin" in out.out
    assert "rotate_env_secret.py" in out.out and "--wrap" in out.out
    _no_values(out.out, out.err)
    # the PC fallback is free again: nothing to restore, and --set puts a value into the root .env (no store now)
    report = fe.restore(pc["paths"], pc["run"])
    assert report.code == 0 and "nothing to restore" in _report_text(report)
    assert fe.main(_cli(pc, "--set", "GOOGLE_API_KEY", "--value-from-stdin"),
                   stdin=io.StringIO("c127-fake-entered-again\n")) == 0
    assert fe.env_set.EnvFile(pc["root"]).value("GOOGLE_API_KEY") == "c127-fake-entered-again"
    assert not pc["store"].exists()
    assert fe.main(_cli(pc, "--discard", "--confirm")) == 0         # nothing left to discard; the aside file stays
    assert list(pc["run"].iterdir()) == [aside]
    out = capsys.readouterr()
    assert "nothing to discard" in out.out
    _no_values(out.out, out.err)


def test_discard_refuses_a_store_this_user_can_unwrap_and_never_overwrites_an_aside_file(pc, monkeypatch) -> None:
    """--discard is only for a store that can never be unwrapped here: a readable one is refused, the dry run too
    (--restore puts its values back). A second discard within the same second never overwrites the first aside
    file."""
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    store = pc["store"].read_bytes()
    for confirm in (False, True):
        report = fe.discard(pc["run"], confirm=confirm)
        text = _report_text(report)
        assert report.code == 1 and not report.changed, text
        assert "this Windows user can unwrap" in text and "'--restore'" in text
        assert pc["store"].read_bytes() == store and sorted(p.name for p in pc["run"].iterdir()) == [fe.STORE_NAME]
    monkeypatch.setattr(fe, "dpapi_unprotect", _other_user)
    monkeypatch.setattr(fe, "_utc_stamp", lambda: "20261020T120000Z")
    earlier = pc["run"] / f"{fe.STORE_NAME}.unreadable-20261020T120000Z"
    earlier.write_bytes(b"an earlier aside file")
    report = fe.discard(pc["run"], confirm=True)
    assert report.code == 0 and report.changed, _report_text(report)
    assert earlier.read_bytes() == b"an earlier aside file" and not pc["store"].exists()
    assert (pc["run"] / f"{fe.STORE_NAME}.unreadable-20261020T120000Z-2").read_bytes() == store


def test_every_refusal_over_an_unusable_store_names_the_way_out(pc, monkeypatch) -> None:
    """load_store raises StoreUnusable (an EnvError) for a store that is there but unusable for this Windows user;
    its message ends with UNUSABLE_HINT, so --set, the rotations and livekit_pc_pair (put_values, find_values) name
    the way out as 'brain pc' does. A missing store names none."""
    assert fe.wrap(pc["paths"], pc["run"]).code == 0
    monkeypatch.setattr(fe, "dpapi_unprotect", _other_user)
    with pytest.raises(fe.StoreUnusable) as caught:
        fe.load_store(pc["store"], fe.dpapi_unprotect)
    assert isinstance(caught.value, fe.EnvError) and str(caught.value).endswith(fe.UNUSABLE_HINT)
    assert caught.value.reason.endswith("could not be unwrapped by this Windows user (OSError)")
    report = fe.put_values({"GOOGLE_API_KEY": "c127-fake-new"}, pc["paths"], pc["run"])
    assert report.code == 1 and "--discard --confirm" in _report_text(report)
    with pytest.raises(fe.EnvError, match="--discard --confirm"):
        fe.find_values(["GOOGLE_API_KEY"], pc["paths"], pc["run"])
    with pytest.raises(fe.EnvError) as missing:
        fe.load_store(pc["run"] / "absent.dpapi", fe.dpapi_unprotect)
    assert not isinstance(missing.value, fe.StoreUnusable) and str(missing.value).endswith("is missing")
    pc["store"].write_bytes(b"{}")                                   # not a store at all: --status points the way too
    report = fe.status(pc["paths"], pc["run"])
    assert report.code == 1 and "present but not a fallback store ('--discard --dry-run'" in _report_text(report)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_the_real_dpapi_helper_round_trips(tmp_path: Path) -> None:
    """The store reuses the node keys' DPAPI helper (services/utils/desktop_protocol) as it is."""
    pytest.importorskip("win32crypt")
    blob = fe.dpapi_protect(b'{"c127-fake": 1}')
    assert b"c127-fake" not in blob and fe.dpapi_unprotect(blob) == b'{"c127-fake": 1}'
    target = tmp_path / "acl.txt"
    target.write_bytes(b"")
    fe.restrict_to_user(target)   # the real ACL call works on a fresh file
