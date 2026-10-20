"""Plan task C-127 step 6: scripts/ops/livekit_pc_pair.py, the PC fallback's own LiveKit pair (tool support only; it
is applied in the V6 LiveKit window). Nothing here touches LiveKit, ssh or docker: a local runner executes the exact
VPS programs against tmp files and a fake docker CLI plays the in-bridge ListRooms check. The generated pair is read
back from the files and must never reach stdout or stderr.
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
spec = importlib.util.spec_from_file_location("jarvis_ops_test_livekit_pc_pair",
                                              ROOT / "scripts" / "ops" / "livekit_pc_pair.py")
lpp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lpp)

COMPOSE_ENV = "LIVEKIT_API_KEY=APIc127main\nLIVEKIT_API_SECRET=c127-fake-main\nJARVIS_BRAIN=vps\n"
ROOT_ENV = "LIVEKIT_API_KEY=APIc127main\r\nLIVEKIT_API_SECRET=c127-fake-main\r\nJARVIS_BRAIN=vps\r\n"
MAGIC = b"FAKE-DPAPI:"


def fake_protect(data: bytes) -> bytes:
    return MAGIC + bytes(b ^ 0x33 for b in data)


def fake_unprotect(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise OSError("not ours")
    return bytes(b ^ 0x33 for b in blob[len(MAGIC):])


class LocalRunner:
    def __init__(self, docker: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.docker = docker

    def __call__(self, program: str, args: dict[str, Any], host: str, key: Path) -> tuple[int, str, str]:
        self.calls.append({"args": args})
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


FAKE_DOCKER = r'''
import json, sys
args = sys.argv[2:]
assert args[:3] == ["exec", "-i", "jarvis-bridge"] and args[3:5] == ["python", "-c"], args
pair = json.load(sys.stdin)
want = open(sys.argv[1], encoding="utf-8").read().split()
if [pair["key"], pair["secret"]] == want and pair["url"] == "http://jarvis-livekit:7880":
    print("PC pair accepted")
    raise SystemExit(0)
print("PC pair rejected: 401")
raise SystemExit(1)
'''


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    vps = tmp_path / "hostinger"
    vps.mkdir()
    (vps / ".env").write_text(COMPOSE_ENV, encoding="utf-8")
    root, overlay = tmp_path / "root.env", tmp_path / "overlay.env"
    root.write_bytes(ROOT_ENV.encode())
    overlay.write_text("USER_CITY=Lahore\n", encoding="utf-8")
    monkeypatch.setattr(lpp.er, "BACKUP_ROOT", str(tmp_path / "backups"))
    accepted = tmp_path / "livekit_keys.txt"   # what the fake LiveKit accepts (key, secret)
    script = tmp_path / "fake_docker.py"
    script.write_text(FAKE_DOCKER, encoding="utf-8")
    return {"vps": vps, "root": root, "overlay": overlay, "run": tmp_path / "run", "backups": tmp_path / "backups",
            "accepted": accepted, "docker": [sys.executable, str(script), str(accepted)],
            "args": ["--vps-dir", vps.as_posix(), "--env-file", str(root), "--env-file", str(overlay), "--run-dir",
                     str(tmp_path / "run"), "--host", "h", "--ssh-key", "k"]}


IO = {"protect": fake_protect, "unprotect": fake_unprotect, "restrict": lambda _p: None}


def _main(env: dict[str, Any], capsys: pytest.CaptureFixture, *argv: str, docker: bool = False) -> tuple[int, str]:
    code = lpp.main([*argv, *env["args"]], runner=LocalRunner(env["docker"] if docker else None), store_io=IO)
    out = capsys.readouterr()
    text = out.out + out.err
    for value in _secrets(env):
        assert value not in text, "a value reached the output"
    assert "c127-fake" not in text and "APIc127" not in text
    return code, text


def _secrets(env: dict[str, Any]) -> list[str]:
    values = list(lpp.er.env_values((env["vps"] / ".env").read_text(encoding="utf-8")).values())
    values += list(lpp.env_set.EnvFile(env["root"]).values.values())
    store = env["run"] / lpp.fallback_env.STORE_NAME
    if store.exists():
        for files in lpp.fallback_env.load_store(store, fake_unprotect).values():
            values += list(files.values())
    return [v for v in values if len(v) >= 8 and v not in ("vps", "Lahore")]


def _vps(env: dict[str, Any]) -> dict[str, str]:
    return lpp.er.env_values((env["vps"] / ".env").read_text(encoding="utf-8"))


def test_create_writes_the_pair_on_the_vps_then_on_this_pc(env, capsys) -> None:
    code, out = _main(env, capsys, "create")
    assert code == 0, out
    vps = _vps(env)
    root = lpp.env_set.EnvFile(env["root"])
    assert vps["LIVEKIT_PC_API_KEY"].startswith("API") and len(vps["LIVEKIT_PC_API_KEY"]) == 15
    assert root.value("LIVEKIT_PC_API_KEY") == vps["LIVEKIT_PC_API_KEY"]
    assert root.value("LIVEKIT_PC_API_SECRET") == vps["LIVEKIT_PC_API_SECRET"]
    assert vps["LIVEKIT_API_SECRET"] == "c127-fake-main" and root.value("LIVEKIT_API_SECRET") == "c127-fake-main"
    (backup,) = list(env["backups"].iterdir())
    assert (backup / "hostinger.env").read_text(encoding="utf-8") == COMPOSE_ENV
    assert "fp " in out and "next (V6 window)" in out
    # a second create is refused (the VPS .env already has a pair); --rotate replaces it
    code, out = _main(env, capsys, "create")
    assert code == 1 and "already in" in out and "--rotate replaces it" in out
    old = _vps(env)["LIVEKIT_PC_API_SECRET"]
    assert _main(env, capsys, "create", "--rotate")[0] == 0
    assert _vps(env)["LIVEKIT_PC_API_SECRET"] != old


def test_create_puts_the_secret_into_the_store_while_wrapped(env, capsys) -> None:
    store = env["run"] / lpp.fallback_env.STORE_NAME
    lpp.fallback_env.write_store(store, {"root": {"GOOGLE_API_KEY": "c127-fake-google"}}, protect=fake_protect,
                                 unprotect=fake_unprotect, restrict=lambda _p: None)
    assert _main(env, capsys, "create")[0] == 0
    stored = lpp.fallback_env.load_store(store, fake_unprotect)["root"]
    assert stored["LIVEKIT_PC_API_SECRET"] == _vps(env)["LIVEKIT_PC_API_SECRET"]
    assert "LIVEKIT_PC_API_SECRET" not in lpp.env_set.EnvFile(env["root"]).values
    assert lpp.env_set.EnvFile(env["root"]).value("LIVEKIT_PC_API_KEY") == _vps(env)["LIVEKIT_PC_API_KEY"]


def test_dry_runs_write_nothing(env, capsys) -> None:
    files = {p: p.read_bytes() for p in (env["vps"] / ".env", env["root"])}
    assert _main(env, capsys, "create", "--dry-run")[0] == 0
    assert _main(env, capsys, "revoke", "--dry-run")[0] == 1   # nothing to revoke yet
    assert {p: p.read_bytes() for p in files} == files and not env["backups"].exists()


def test_verify_and_activate_switch_this_pc_to_the_pc_pair(env, capsys) -> None:
    assert _main(env, capsys, "create")[0] == 0
    vps = _vps(env)
    # LiveKit does not know the pair yet (the V6 compose line and the recreate come first): refused
    env["accepted"].write_text("APIc127main c127-fake-main", encoding="utf-8")
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 1 and "PC pair rejected: 401" in out and "does not accept the PC pair yet" in out
    env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    code, out = _main(env, capsys, "verify", docker=True)
    assert code == 0 and "PC pair accepted" in out
    assert _main(env, capsys, "activate", "--dry-run", docker=True)[0] == 0
    assert lpp.env_set.EnvFile(env["root"]).value("LIVEKIT_API_SECRET") == "c127-fake-main"
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 0 and "activated" in out
    root = lpp.env_set.EnvFile(env["root"])
    assert root.value("LIVEKIT_API_KEY") == vps["LIVEKIT_PC_API_KEY"]
    assert root.value("LIVEKIT_API_SECRET") == vps["LIVEKIT_PC_API_SECRET"]
    assert "LIVEKIT_PC_API_KEY" not in root.values and "LIVEKIT_PC_API_SECRET" not in root.values
    assert _vps(env)["LIVEKIT_API_SECRET"] == "c127-fake-main"   # the VPS keeps the main pair


def test_activate_refuses_a_pending_key_that_is_not_the_vps_one(env, capsys) -> None:
    assert _main(env, capsys, "create")[0] == 0
    vps = _vps(env)
    env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    root_text = env["root"].read_text(encoding="utf-8").replace(vps["LIVEKIT_PC_API_KEY"], "APIc127otherkey")
    env["root"].write_text(root_text, encoding="utf-8")
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 1 and "is not the one in the VPS .env" in out


def test_verify_checks_this_pcs_own_copy_also_after_activate(env, capsys) -> None:
    """verify signs with the copy the PC worker would use: after 'activate' that is LIVEKIT_API_KEY/SECRET."""
    assert _main(env, capsys, "create")[0] == 0
    vps = _vps(env)
    env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    assert _main(env, capsys, "activate", docker=True)[0] == 0
    code, out = _main(env, capsys, "verify", docker=True)
    assert code == 0 and "PC pair accepted" in out and "LIVEKIT_API_KEY/LIVEKIT_API_SECRET are the VPS .env pair" in out


def test_verify_never_signs_with_a_pair_that_is_not_the_pc_pair(env, capsys) -> None:
    """Before 'create' this PC holds only the main pair: its key is not the VPS PC key, so no ListRooms call is made
    with it (LiveKit would accept the main pair and verify would say 'accepted')."""
    pc_pair = "LIVEKIT_PC_API_KEY=APIc127pcpair\nLIVEKIT_PC_API_SECRET=c127-fake-pc-secret\n"
    (env["vps"] / ".env").write_text(COMPOSE_ENV + pc_pair, encoding="utf-8")
    env["accepted"].write_text("APIc127main c127-fake-main", encoding="utf-8")   # LiveKit takes the main pair
    runner = LocalRunner(env["docker"])
    code = lpp.main(["verify", *env["args"]], runner=runner, store_io=IO)
    out = capsys.readouterr()
    text = out.out + out.err
    assert code == 1 and "not checked" in text and "PC pair accepted" not in text
    assert "this PC holds no copy of the PC pair" in text and "c127-fake" not in text


def test_after_revoke_verify_rejects_this_pcs_copy_and_activate_keeps_the_main_pair(env, capsys) -> None:
    """The review case: create, then revoke before activate. LiveKit, recreated from the VPS .env, accepts what that
    .env says (a value nobody holds); verify must judge this PC's copy (401 after the recreate) and activate must never
    move the stale pending secret over the working main pair."""
    assert _main(env, capsys, "create")[0] == 0
    vps = _vps(env)
    env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    assert _main(env, capsys, "revoke")[0] == 0
    # before the LiveKit recreate the old secret still works: accepted, but no longer the VPS one (exit 1)
    code, out = _main(env, capsys, "verify", docker=True)
    assert code == 1 and "PC pair accepted" in out and "was revoked" in out
    # the recreate: LiveKit now has exactly what the VPS .env says
    after = _vps(env)
    env["accepted"].write_text(f"{after['LIVEKIT_PC_API_KEY']} {after['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    code, out = _main(env, capsys, "verify", docker=True)
    assert code == 1 and "PC pair rejected: 401" in out and "was revoked" in out
    root_before = env["root"].read_bytes()
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 1 and "pending pair is not the one in the VPS .env" in out and "create --rotate" in out
    assert env["root"].read_bytes() == root_before
    assert lpp.env_set.EnvFile(env["root"]).value("LIVEKIT_API_SECRET") == "c127-fake-main"


def test_verify_and_activate_unwrap_the_pending_secret_from_the_store(env, capsys) -> None:
    """vps mode: the pending secret sits in run/fallback.env.dpapi; verify and activate unwrap it in their own process
    (never printed), and a revoke is caught there too."""
    store = env["run"] / lpp.fallback_env.STORE_NAME
    lpp.fallback_env.write_store(store, {"root": {"LIVEKIT_API_SECRET": "c127-fake-main"}}, protect=fake_protect,
                                 unprotect=fake_unprotect, restrict=lambda _p: None)
    root_text = env["root"].read_text(encoding="utf-8").replace("LIVEKIT_API_SECRET=c127-fake-main\n", "")
    env["root"].write_text(root_text, encoding="utf-8")
    assert _main(env, capsys, "create")[0] == 0
    vps = _vps(env)
    assert "LIVEKIT_PC_API_SECRET" not in lpp.env_set.EnvFile(env["root"]).values
    env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    code, out = _main(env, capsys, "verify", docker=True)
    assert code == 0 and "PC pair accepted" in out
    assert _main(env, capsys, "activate", docker=True)[0] == 0
    stored = lpp.fallback_env.load_store(store, fake_unprotect)["root"]
    assert stored["LIVEKIT_API_SECRET"] == vps["LIVEKIT_PC_API_SECRET"] and "LIVEKIT_PC_API_SECRET" not in stored
    assert lpp.env_set.EnvFile(env["root"]).value("LIVEKIT_API_KEY") == vps["LIVEKIT_PC_API_KEY"]


def _wrapped_and_created(env: dict[str, Any], capsys: pytest.CaptureFixture) -> Path:
    """vps mode: the main secret wrapped in the store, then 'create' (the pending secret goes into the store too) and
    the V6 recreate (LiveKit accepts the PC pair). Returns the store path."""
    store = env["run"] / lpp.fallback_env.STORE_NAME
    lpp.fallback_env.write_store(store, {"root": {"LIVEKIT_API_SECRET": "c127-fake-main"}}, protect=fake_protect,
                                 unprotect=fake_unprotect, restrict=lambda _p: None)
    env["root"].write_text(env["root"].read_text(encoding="utf-8").replace("LIVEKIT_API_SECRET=c127-fake-main\n", ""),
                           encoding="utf-8")
    assert _main(env, capsys, "create")[0] == 0
    vps = _vps(env)
    env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    return store


def test_activate_puts_the_store_back_when_the_root_env_cannot_be_written(env, capsys, monkeypatch) -> None:
    """Review finding (the repro): in vps mode 'activate' rewrote the store, then the root .env write failed (another
    program held it open, or it was read-only); the output said 'no file was changed', the store held the PC secret
    under LIVEKIT_API_SECRET beside the main key in the root .env, and a second 'activate' was refused because no
    pending secret was left. Now the store goes back byte for byte and 'activate' simply runs again."""
    store = _wrapped_and_created(env, capsys)
    vps = _vps(env)
    before = {"store": store.read_bytes(), "root": env["root"].read_bytes(), "overlay": env["overlay"].read_bytes()}
    real = os.replace

    def locked(src: Any, dst: Any) -> None:
        if Path(dst) == env["root"]:
            raise PermissionError(13, "The process cannot access the file because another process uses it")
        real(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 1 and "no file was changed" not in out, out
    assert "the env files were not written" in out and "was put back byte for byte" in out
    after = {"store": store.read_bytes(), "root": env["root"].read_bytes(), "overlay": env["overlay"].read_bytes()}
    assert after == before
    assert lpp.fallback_env.load_store(store, fake_unprotect)["root"] == {
        "LIVEKIT_API_SECRET": "c127-fake-main", "LIVEKIT_PC_API_SECRET": vps["LIVEKIT_PC_API_SECRET"]}
    monkeypatch.setattr(os, "replace", real)
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 0 and "activated" in out, out
    assert lpp.fallback_env.load_store(store, fake_unprotect)["root"] == {
        "LIVEKIT_API_SECRET": vps["LIVEKIT_PC_API_SECRET"]}
    assert lpp.env_set.EnvFile(env["root"]).value("LIVEKIT_API_KEY") == vps["LIVEKIT_PC_API_KEY"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_activate_leaves_no_copy_of_the_main_pair_on_this_pc(env, capsys, wrapped) -> None:
    """Review finding: deploy/windows-local/.env may hold the main pair (its template lists both). 'activate' replaced
    it only in the root .env, so this PC still held the main pair and revoking the PC pair did not cut it off."""
    env["overlay"].write_text("LIVEKIT_API_KEY=APIc127main\nLIVEKIT_API_SECRET=c127-fake-main\nUSER_CITY=Lahore\n",
                              encoding="utf-8")
    if wrapped:
        store = _wrapped_and_created(env, capsys)
        # the overlay's secret was wrapped as well (agent_ctl brain vps wraps both files); its key stays in the file
        lpp.fallback_env.write_store(store, {**lpp.fallback_env.load_store(store, fake_unprotect),
                                             "overlay": {"LIVEKIT_API_SECRET": "c127-fake-main"}},
                                     protect=fake_protect, unprotect=fake_unprotect, restrict=lambda _p: None)
        env["overlay"].write_text("LIVEKIT_API_KEY=APIc127main\nUSER_CITY=Lahore\n", encoding="utf-8")
    else:
        assert _main(env, capsys, "create")[0] == 0
        vps = _vps(env)
        env["accepted"].write_text(f"{vps['LIVEKIT_PC_API_KEY']} {vps['LIVEKIT_PC_API_SECRET']}", encoding="utf-8")
    vps = _vps(env)
    code, out = _main(env, capsys, "activate", docker=True)
    assert code == 0 and "activated" in out, out
    overlay = lpp.env_set.EnvFile(env["overlay"])
    assert "LIVEKIT_API_KEY" not in overlay.values and "LIVEKIT_API_SECRET" not in overlay.values
    assert overlay.value("USER_CITY") == "Lahore"
    found = lpp.fallback_env.find_values(["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], [env["root"], env["overlay"]],
                                         env["run"], unprotect=fake_unprotect)
    assert found == {"LIVEKIT_API_KEY": vps["LIVEKIT_PC_API_KEY"], "LIVEKIT_API_SECRET": vps["LIVEKIT_PC_API_SECRET"]}
    held = env["root"].read_text(encoding="utf-8") + env["overlay"].read_text(encoding="utf-8")
    store = env["run"] / lpp.fallback_env.STORE_NAME
    if store.exists():
        held += json.dumps(lpp.fallback_env.load_store(store, fake_unprotect))
    assert "c127-fake-main" not in held and "APIc127main" not in held   # the VPS keeps the main pair; this PC not
    assert _vps(env)["LIVEKIT_API_SECRET"] == "c127-fake-main"


def test_revoke_replaces_the_vps_secret_with_one_nobody_holds(env, capsys) -> None:
    assert _main(env, capsys, "create")[0] == 0
    before = _vps(env)
    code, out = _main(env, capsys, "revoke")
    assert code == 0 and "replaced by a value nobody holds" in out and "--force-recreate livekit" in out
    after = _vps(env)
    assert after["LIVEKIT_PC_API_SECRET"] != before["LIVEKIT_PC_API_SECRET"]
    assert after["LIVEKIT_PC_API_SECRET"] != lpp.env_set.EnvFile(env["root"]).value("LIVEKIT_PC_API_SECRET")
    assert after["LIVEKIT_PC_API_KEY"] == before["LIVEKIT_PC_API_KEY"] and after["LIVEKIT_API_SECRET"] == "c127-fake-main"


def test_usage_errors(capsys) -> None:
    for argv in (["verify", "--dry-run"], ["revoke", "--rotate"], ["nosuch"], ["create", "c127-fake-typed"]):
        with pytest.raises(SystemExit) as stop:
            lpp.parse_args(argv)
        assert stop.value.code == 2
    assert "c127-fake" not in capsys.readouterr().err
