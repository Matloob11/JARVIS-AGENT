"""C-123: the agent_ctl node and brain commands, the pinned read-only snapshot and the two-runtimes rule.

These tests extend the C-09/C-16/C-137 suite (tests/unit/test_agent_ctl.py) with the VPS-brain additions.
Nothing real is ever touched: ssh, the bridge (PC-signed HTTP), schtasks, icacls, git archive and DPAPI are
all mocked, and every file lives under tmp_path (JARVIS_RUN_DIR, the node home, the data root). No scheduled
task is created, no snapshot or ACL is made on the real machine, the live worker and node are never stopped,
and no /stop is POSTed anywhere.

Run: .venv_312/Scripts/python.exe -m pytest tests/test_agent_ctl.py tests/unit/test_agent_ctl.py -q
"""
# pylint: disable=protected-access,redefined-outer-name  # one CLI module and its node_snapshot helper
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.desktop import node_snapshot  # noqa: E402
from src.core.desktop import settings as node_settings  # noqa: E402


def _load_agent_ctl() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("agent_ctl_c123", ROOT / "scripts" / "agent_ctl.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent_ctl = _load_agent_ctl()

# A throwaway 64-hex Ed25519-shaped public key (never a real key) for node_config.json round-trips.
FAKE_PUBKEY = "ab" * 32
# Throwaway token values for the secrets round-trip; they must never appear in the written file.
THROWAWAY_PINCHTAB = "throwaway-pinchtab-7Hq"
THROWAWAY_CONTROLLER = "throwaway-controller-9Xz"


# =============================================================================================================
# fixtures
# =============================================================================================================


@pytest.fixture
def ctl(monkeypatch, tmp_path):
    """agent_ctl with its run dir and logs under tmp_path, every process call refused, and its root .env a
    tmp file that does not exist yet (so nothing is read from the real .env files, and the brain switch's
    laptop JARVIS_BRAIN write lands under tmp_path, never in the repo's .env)."""
    run_dir = tmp_path / "run"
    logs = tmp_path / "logs"
    root_env = tmp_path / "root.env"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(run_dir))
    monkeypatch.setattr(agent_ctl, "LOG_DIR", logs)
    monkeypatch.setattr(agent_ctl, "ENV_FILES", (root_env,))
    for name in ("JARVIS_BRAIN", "JARVIS_WORKER_HOST", "JARVIS_WORKER_PORT", "JARVIS_PC_AGENT_NAME"):
        monkeypatch.delenv(name, raising=False)

    def refuse(cmd, **_kwargs):
        raise AssertionError(f"unexpected process call: {cmd[:3]}")

    monkeypatch.setattr(agent_ctl, "_run", refuse)
    monkeypatch.setattr(agent_ctl, "_popen", refuse)
    return types.SimpleNamespace(run=run_dir, logs=logs, tmp=tmp_path, env=root_env, mp=monkeypatch)


def audit_lines(logs: Path) -> list[dict]:
    path = logs / "agent_ctl.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# =============================================================================================================
# the two-runtimes rule: a PC worker start/restart is refused in vps mode (C-123 step 5, K-72)
# =============================================================================================================


def test_current_brain_defaults_to_pc_and_reads_the_env(ctl, monkeypatch):
    assert agent_ctl.current_brain() == "pc"
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    assert agent_ctl.current_brain() == "vps"
    monkeypatch.setenv("JARVIS_BRAIN", "nonsense")
    assert agent_ctl.current_brain() == "pc"  # an unknown value is treated as pc


@pytest.mark.parametrize("command", ["start", "restart"])
def test_worker_start_is_refused_in_vps_mode(ctl, monkeypatch, capsys, command):
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    # Any real process/lock work would raise (ctl refuses _run/_popen); nothing should be spawned.
    monkeypatch.setattr(agent_ctl, "start_worker",
                        lambda *a, **k: pytest.fail("no worker may start in vps mode"))
    monkeypatch.setattr(agent_ctl, "stop_worker",
                        lambda *a, **k: pytest.fail("no worker may be stopped by a vps-mode restart"))

    code = agent_ctl.main([command, "--by", "claude", "--reason", "C-123"])

    assert code == agent_ctl.EXIT_REFUSED == 4
    err = capsys.readouterr().err
    assert "JARVIS_BRAIN=vps" in err and "agent_ctl node" in err
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["brain"]) == (command, "refused", "vps")


def test_pc_mode_start_is_not_blocked_by_the_brain_gate(ctl, monkeypatch):
    """In pc mode the gate is transparent: start_worker is still called (the rest is the C-09 path)."""
    monkeypatch.setenv("JARVIS_BRAIN", "pc")
    called = {}
    monkeypatch.setattr(agent_ctl, "start_worker",
                        lambda args, mode, **k: (called.setdefault("yes", True), (0, {"result": "started"}))[1])
    assert agent_ctl.main(["start", "--by", "claude", "--reason", "pc"]) == 0
    assert called.get("yes")


def test_brain_agent_name_maps_vps_and_pc(ctl, monkeypatch):
    assert agent_ctl.brain_agent_name("vps") == "jarvis"
    assert agent_ctl.brain_agent_name("pc") == "jarvis-pc"
    monkeypatch.setenv("JARVIS_PC_AGENT_NAME", "jarvis-pc-custom")
    assert agent_ctl.brain_agent_name("pc") == "jarvis-pc-custom"


# =============================================================================================================
# the pinned snapshot is read-only (C-123 step 1, K-76 step 4)
# =============================================================================================================


def _fake_git(runner_calls: list, sha: str = "c" * 40):
    """A runner that answers git rev-parse with `sha` and git archive by writing a real one-file tar."""
    def runner(cmd, **_kwargs):
        runner_calls.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, sha, "")
        if "archive" in cmd:
            out = Path(cmd[cmd.index("-o") + 1])
            with tarfile.open(out, "w:") as tar:
                payload = b"# pinned tree\n"
                info = tarfile.TarInfo("README.md")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        # icacls / chmod: record and succeed, never touch the real ACL.
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return runner


def test_lock_read_only_grants_read_execute_but_never_write(monkeypatch, tmp_path):
    monkeypatch.setattr(node_snapshot.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "matloob")
    cmd = node_snapshot.lock_read_only(tmp_path, runner=lambda c, **k: subprocess.CompletedProcess(c, 0, "", ""),
                                       user="matloob")
    text = " ".join(cmd)
    assert cmd[0] == "icacls"
    assert "/inheritance:r" in cmd  # inherited (possibly writable) ACEs are dropped
    assert "matloob:(OI)(CI)RX" in cmd  # read + execute only
    # No write, modify or full-control grant anywhere.
    for forbidden in (":(OI)(CI)F", ":(OI)(CI)M", ":(OI)(CI)W", ":(OI)(CI)(RX,W)", ":F", ":M", ":W"):
        assert forbidden not in text


def test_update_snapshot_builds_read_only_and_wraps_secrets_without_leaking(monkeypatch, tmp_path):
    monkeypatch.setattr(node_snapshot.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "matloob")
    home = tmp_path / "node_home"
    data_root = tmp_path / "repo"
    (data_root / "run").mkdir(parents=True)
    # The root .env holds the two tokens the node needs (plus an unrelated secret that must be ignored).
    (data_root / ".env").write_text(
        f"PINCHTAB_TOKEN={THROWAWAY_PINCHTAB}\nCONTROLLER_TOKEN=\"{THROWAWAY_CONTROLLER}\"\n"
        "LIVEKIT_API_SECRET=unrelated\nJARVIS_DESKTOP_CONTROL=true\n", encoding="utf-8")
    runner_calls: list = []
    protect_calls: list = []
    restrict_calls: list = []

    def protect(data: bytes) -> bytes:
        protect_calls.append(data)
        return b"DPAPI::" + data[::-1]  # a stand-in for CryptProtectData: obscures the plaintext bytes

    plan = node_snapshot.update_snapshot(
        sha=None, data_root=data_root, run_dir=data_root / "run", home=home, root=data_root,
        agent_public_key=FAKE_PUBKEY, pc_public_key=FAKE_PUBKEY, owner_room="jarvis-hybrid",
        protect=protect, restrict=restrict_calls.append, runner=_fake_git(runner_calls))

    # The snapshot was archived and locked read-only.
    assert plan.snapshot == home / ("c" * 40)
    assert (plan.snapshot / "README.md").is_file()
    icacls = [c for c in runner_calls if c and c[0] == "icacls"]
    assert icacls and "matloob:(OI)(CI)RX" in icacls[0] and "/inheritance:r" in icacls[0]
    # node_config.json is valid for the supervisor's loader.
    cfg = node_settings.load_node_config(plan.node_config, environ={"JARVIS_NODE_HOME": str(home)})
    assert cfg.data_root == data_root.resolve() and cfg.agent_public_key == FAKE_PUBKEY
    # secrets.dpapi is wrapped and holds no plaintext token; the tokens were never printed.
    blob = plan.secrets_file.read_bytes()
    assert blob.startswith(b"DPAPI::")
    assert THROWAWAY_PINCHTAB.encode() not in blob and THROWAWAY_CONTROLLER.encode() not in blob
    # The wrapped document round-trips to exactly the two node secrets.
    assert node_settings.parse_node_secrets(blob[len(b"DPAPI::"):][::-1]) == {
        "PINCHTAB_TOKEN": THROWAWAY_PINCHTAB, "CONTROLLER_TOKEN": THROWAWAY_CONTROLLER}
    assert restrict_calls == [plan.secrets_file]  # the secrets file was locked to the user
    # run/node_current.json points the supervisor at the new snapshot.
    current = node_snapshot.read_current(data_root / "run")
    assert current["sha"] == "c" * 40 and current["snapshot"] == str(plan.snapshot)


def test_update_snapshot_relocks_a_reused_snapshot_on_retry(monkeypatch, tmp_path):
    """A prior run archived the tree but raised before/while locking (icacls failed); a same-sha retry reuses
    the non-empty dest. The read-only ACL must still be applied (K-76 step 4), not skipped with the build."""
    monkeypatch.setattr(node_snapshot.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "matloob")
    home = tmp_path / "node_home"
    data_root = tmp_path / "repo"
    (data_root / "run").mkdir(parents=True)
    (data_root / ".env").write_text("JARVIS_DESKTOP_CONTROL=true\n", encoding="utf-8")
    # Simulate the half-finished earlier run: the snapshot dir already holds the archived tree.
    dest = node_snapshot.snapshot_dir("c" * 40, home=home)
    dest.mkdir(parents=True)
    (dest / "README.md").write_text("# already archived\n", encoding="utf-8")

    runner_calls: list = []

    def runner(cmd, **_kwargs):
        runner_calls.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "c" * 40, "")
        if "archive" in cmd:
            pytest.fail("git archive must not run when the snapshot is reused")
        return subprocess.CompletedProcess(cmd, 0, "", "")  # icacls

    node_snapshot.update_snapshot(
        sha=None, data_root=data_root, run_dir=data_root / "run", home=home, root=data_root,
        agent_public_key=FAKE_PUBKEY, pc_public_key=FAKE_PUBKEY, owner_room="jarvis-hybrid",
        protect=lambda b: b"DPAPI::" + b[::-1], restrict=lambda _p: None, runner=runner)

    icacls = [c for c in runner_calls if c and c[0] == "icacls"]
    assert icacls, "lock_read_only must run on a reused snapshot, not only on a fresh build"
    assert "matloob:(OI)(CI)RX" in icacls[0] and "/inheritance:r" in icacls[0]


def test_build_snapshot_refuses_a_non_empty_destination(tmp_path):
    dest = tmp_path / "snap"
    dest.mkdir()
    (dest / "stale").write_text("x", encoding="utf-8")
    with pytest.raises(node_snapshot.SnapshotError):
        node_snapshot.build_snapshot("c" * 40, dest, runner=_fake_git([]))


def test_root_env_secrets_reads_only_the_two_tokens(tmp_path):
    (tmp_path / ".env").write_text(
        f"PINCHTAB_TOKEN={THROWAWAY_PINCHTAB}\nOPENAI_API_KEY=sk-should-be-ignored\n", encoding="utf-8")
    found = node_snapshot.root_env_secrets(tmp_path)
    assert found == {"PINCHTAB_TOKEN": THROWAWAY_PINCHTAB}


# =============================================================================================================
# node start / stop / update through the mocked scheduled task and health server (C-123 steps 1-3)
# =============================================================================================================


TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _task_definition(xml_bytes: bytes):
    """The parsed Task Scheduler XML that create_node_task hands to 'schtasks /Create /XML' (UTF-16 with a BOM)."""
    import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel  # parses our own test output

    assert xml_bytes[:2] in (b"\xff\xfe", b"\xfe\xff")  # the BOM schtasks needs for the declared UTF-16
    return ET.fromstring(xml_bytes.decode("utf-16").split("?>", 1)[1])


def test_create_node_task_pins_logon_scope_limited_rights_and_the_snapshot(ctl, monkeypatch, tmp_path):
    """The real create_node_task()/node_task_xml() must build the security-relevant task: a logon trigger for this
    user, LeastPrivilege with the interactive token (never elevated), the pythonw interpreter and the pinned
    snapshot as the working directory. These are the K-76 'never elevated, runs from the snapshot' guarantees, so a
    regression that drops LeastPrivilege or the logon trigger must fail here rather than pass under mocking. No
    console program runs (no cmd.exe), so no console window shows at logon."""
    snapshot = Path("C:/Users/owner/AppData/Local/JARVIS/node") / ("f" * 40)
    monkeypatch.setenv("USERNAME", "owner")
    monkeypatch.setenv("USERDOMAIN", "LAPTOP")
    captured: dict = {}

    def fake_run(cmd, **_kwargs):
        captured["cmd"] = list(cmd)
        captured["xml"] = Path(cmd[cmd.index("/XML") + 1]).read_bytes()  # removed again after the call
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(agent_ctl, "_run", fake_run)

    assert agent_ctl.create_node_task(snapshot) is True
    cmd = captured["cmd"]
    assert cmd[:4] == ["schtasks", "/Create", "/TN", agent_ctl.NODE_TASK_NAME]
    assert "/F" in cmd  # replace an existing task rather than failing
    assert not any(flag in cmd for flag in ("/TR", "/SC", "/RU", "/RP"))  # the XML holds the whole definition
    assert not Path(cmd[cmd.index("/XML") + 1]).exists()  # the temporary definition was removed
    task = _task_definition(captured["xml"])
    # Logon-scoped (this user only), never elevated, interactive token.
    assert task.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=TASK_NS) == "LAPTOP\\owner"
    assert task.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=TASK_NS) == "LeastPrivilege"
    assert task.findtext("t:Principals/t:Principal/t:LogonType", namespaces=TASK_NS) == "InteractiveToken"
    # The node runs for the whole session: no 72 h limit, also on battery.
    assert task.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=TASK_NS) == "PT0S"
    assert task.findtext("t:Settings/t:DisallowStartIfOnBatteries", namespaces=TASK_NS) == "false"
    # One action: pythonw (node_python_w, not the console python) runs the supervisor module with the pinned
    # snapshot as its working directory, so the module resolves against the pinned tree.
    execs = task.findall("t:Actions/t:Exec", namespaces=TASK_NS)
    assert len(execs) == 1
    command = execs[0].findtext("t:Command", namespaces=TASK_NS)
    arguments = execs[0].findtext("t:Arguments", namespaces=TASK_NS)
    assert command == str(agent_ctl.node_python_w())
    assert arguments == "-B -m src.core.desktop.supervisor"  # -B: no __pycache__ writes into the snapshot
    assert execs[0].findtext("t:WorkingDirectory", namespaces=TASK_NS) == str(snapshot)
    whole = captured["xml"].decode("utf-16").lower()
    assert "cmd.exe" not in whole and "cmd /c" not in whole and "&&" not in whole and "&amp;&amp;" not in whole
    assert Path(command).name.lower() != "cmd.exe"


def test_node_task_action_adds_the_canary_flag_and_runs_from_the_snapshot(tmp_path):
    """The canary task runs the same supervisor module with --canary appended (nothing else changes)."""
    snapshot = tmp_path / "snap"
    program, active, workdir = agent_ctl.node_task_action(snapshot, canary=False)
    _program, canary, _workdir = agent_ctl.node_task_action(snapshot, canary=True)
    assert active == "-B -m src.core.desktop.supervisor" and canary == active + " --canary"
    assert workdir == str(snapshot) and program == str(agent_ctl.node_python_w())
    exec_ = _task_definition(agent_ctl.node_task_xml(snapshot, canary=True, user="").encode("utf-16")).find(
        "t:Actions/t:Exec", namespaces=TASK_NS)
    assert exec_.findtext("t:Arguments", namespaces=TASK_NS) == canary
    assert exec_.findtext("t:WorkingDirectory", namespaces=TASK_NS) == str(snapshot)


def test_node_task_xml_escapes_paths(tmp_path):
    snapshot = tmp_path / "a&b <c>"
    text = agent_ctl.node_task_xml(snapshot, user="DOM\\o&wner")
    task = _task_definition(text.encode("utf-16"))
    assert task.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=TASK_NS) == str(snapshot)
    assert task.findtext("t:Principals/t:Principal/t:UserId", namespaces=TASK_NS) == "DOM\\o&wner"


def test_node_stop_posts_stop_and_disables_the_task(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    (ctl.run / "desktop_node.token").write_text("tok-abc", encoding="utf-8")
    posts: list = []
    schtasks: list = []
    running = {"up": True}

    def fake_post(url, headers=None, timeout=None, trust_env=None):
        posts.append((url, headers.get("X-Node-Token")))
        running["up"] = False  # the supervisor stops after the reply
        return types.SimpleNamespace(status_code=200)

    monkeypatch.setattr(agent_ctl.httpx, "post", fake_post)
    monkeypatch.setattr(agent_ctl, "node_running", lambda: running["up"])
    monkeypatch.setattr(agent_ctl, "_schtasks", lambda *a, **k: schtasks.append(a) or
                        subprocess.CompletedProcess(list(a), 0, "", ""))

    code = agent_ctl.main(["node", "stop", "--by", "claude", "--reason", "C-123"])

    assert code == 0
    assert posts == [(agent_ctl.node_stop_url(), "tok-abc")]
    assert ("/Change", "/TN", agent_ctl.NODE_TASK_NAME, "/Disable") in schtasks  # stays disabled until start
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["task_disabled"]) == ("node_stop", "stopped", True)
    assert "stays disabled until" in capsys.readouterr().out


def test_node_start_enables_runs_the_task_and_waits_for_health(ctl, monkeypatch):
    ctl.run.mkdir(parents=True)
    (ctl.run / "node_current.json").write_text(json.dumps({"sha": "c" * 40, "short_sha": "cccccccccccc",
                                                           "snapshot": str(ctl.tmp / "snap")}), encoding="utf-8")
    schtasks: list = []
    health = {"polls": 0}
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: True)
    monkeypatch.setattr(agent_ctl, "set_node_task_enabled", lambda enabled: schtasks.append(("enable", enabled)))
    monkeypatch.setattr(agent_ctl, "run_node_task", lambda: schtasks.append(("run",)) or True)

    def node_running():
        health["polls"] += 1
        return health["polls"] >= 2  # up after one poll

    monkeypatch.setattr(agent_ctl, "node_running", node_running)
    monkeypatch.setattr(agent_ctl, "_wait_until", lambda cond, timeout, step=0.5: cond() or cond())

    assert agent_ctl.main(["node", "start", "--by", "claude", "--reason", "C-123"]) == 0
    assert ("enable", True) in schtasks and ("run",) in schtasks
    assert audit_lines(ctl.logs)[-1]["result"] == "started"


def test_node_start_refuses_without_a_pinned_snapshot(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    monkeypatch.setattr(agent_ctl, "node_running", lambda: False)
    assert agent_ctl.main(["node", "start", "--by", "claude"]) == 1
    assert "No pinned node snapshot" in capsys.readouterr().err
    assert audit_lines(ctl.logs)[-1]["result"] == "no_snapshot"


def test_node_update_pins_creates_the_task_and_restarts(ctl, monkeypatch, capsys):
    """vps mode: the laptop target is the node, so update (re)starts it on the new snapshot."""
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    ctl.run.mkdir(parents=True)
    plan = node_snapshot.UpdatePlan(sha="d" * 40, snapshot=ctl.tmp / "snap", run_mode="active",
                                    node_config=ctl.tmp / "node_config.json", secrets_file=ctl.tmp / "s.dpapi")
    captured: dict = {}
    monkeypatch.setattr(node_snapshot, "update_snapshot", lambda **kwargs: captured.update(kwargs) or plan)
    created: list = []
    monkeypatch.setattr(agent_ctl, "create_node_task", lambda snap, canary=False: created.append((snap, canary)) or True)
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: True)
    monkeypatch.setattr(agent_ctl, "set_node_task_enabled", lambda enabled: None)
    monkeypatch.setattr(agent_ctl, "run_node_task", lambda: True)
    monkeypatch.setattr(agent_ctl, "node_running", lambda: False)
    monkeypatch.setattr(agent_ctl, "_wait_until", lambda cond, timeout, step=0.5: True)

    code = agent_ctl.main(["node", "update", "--by", "claude", "--reason", "C-123 deploy"])

    assert code == 0
    assert captured["data_root"] == agent_ctl.ROOT and captured["run_mode"] == "active"
    assert created == [(plan.snapshot, False)]
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["restarted"]) == ("node_update", "updated", True)
    out = capsys.readouterr().out
    assert "Pinned snapshot dddddddddddd" in out
    assert "Desktop Node update (agent_ctl)" in out  # done_highlight entry for the agents


def test_node_update_canary_passes_the_run_mode_through(ctl, monkeypatch):
    ctl.run.mkdir(parents=True)
    captured: dict = {}
    plan = node_snapshot.UpdatePlan("e" * 40, ctl.tmp / "snap", "canary", ctl.tmp / "c.json", None)
    monkeypatch.setattr(node_snapshot, "update_snapshot", lambda **kwargs: captured.update(kwargs) or plan)
    monkeypatch.setattr(agent_ctl, "create_node_task", lambda snap, canary=False: captured.update(canary=canary) or True)
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: True)
    monkeypatch.setattr(agent_ctl, "set_node_task_enabled", lambda enabled: None)
    monkeypatch.setattr(agent_ctl, "run_node_task", lambda: True)
    monkeypatch.setattr(agent_ctl, "node_running", lambda: False)
    monkeypatch.setattr(agent_ctl, "_wait_until", lambda cond, timeout, step=0.5: True)

    assert agent_ctl.main(["node", "update", "--canary"]) == 0
    assert captured["run_mode"] == "canary" and captured["canary"] is True


# =============================================================================================================
# the brain switch: journal resume and automatic restore (C-123 steps 4, 7, K-72)
# =============================================================================================================


class LiveStatus:
    """A /vortex/status that shows `brain` with its agent running; a FakeLaptop given it moves it to the brain it
    starts, the way the bridge would once that brain's agent joins the room."""

    def __init__(self, brain: str, **extra) -> None:
        self.brain, self.extra = brain, dict(extra)

    def __call__(self):
        return {**_running_status(self.brain), **self.extra}


class FakeLaptop:
    """Records start/stop/ensure for each brain; every call succeeds."""

    def __init__(self, status: LiveStatus | None = None) -> None:
        self.calls: list = []
        self.status = status

    def start(self, brain: str) -> int:
        self.calls.append(("start", brain))
        if self.status is not None:
            self.status.brain = brain
        return agent_ctl.EXIT_OK

    def stop(self, brain: str) -> int:
        self.calls.append(("stop", brain))
        return agent_ctl.EXIT_OK

    def ensure(self, brain: str) -> int:
        self.calls.append(("ensure", brain))
        return agent_ctl.EXIT_OK


# What PREFLIGHT's provisioning check prints on a VPS that has every helper and both brain services (C-126..C-129).
PROVISIONED = "service:livekit\nservice:ui-bridge\nservice:frontend\nservice:agent\nservice:agent-svc\n"


def _provision_answer(cmd):
    """The answer of a provisioned VPS to PREFLIGHT's check, or None for any other command."""
    if "config --services" in " ".join(cmd):
        return subprocess.CompletedProcess(cmd, 0, PROVISIONED, "")
    return None


class FakeVps:
    """A mocked ssh to the VPS that records every command. The provisioning check prints `provision` (exit
    `check_code`), 'set_env.py --get' prints `brain`, a command containing `fail_on` exits 1, the rest exit 0."""

    def __init__(self, *, brain="pc", provision=PROVISIONED, check_code=0, fail_on=None) -> None:
        self.brain, self.provision, self.check_code, self.fail_on = brain, provision, check_code, fail_on
        self.calls: list = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if "config --services" in joined:
            return subprocess.CompletedProcess(cmd, self.check_code, self.provision, "")
        if "--get" in joined:
            return subprocess.CompletedProcess(cmd, 0, self.brain, "")
        if self.fail_on and self.fail_on in joined:
            return subprocess.CompletedProcess(cmd, 1, "", "failed")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def joined(self) -> str:
        return "\n".join(" ".join(c) for c in self.calls)


def _running_status(target: str):
    """A /vortex/status that shows `target` as the brain with its agent participant running."""
    return {"brain": target, "agent": {"lk.agent.name": agent_ctl.brain_agent_name(target),
                                       "jarvis.session": "running"}}


def test_status_shows_brain_requires_brain_and_a_running_agent(ctl):
    assert agent_ctl.status_shows_brain(_running_status("vps"), "vps") is True
    assert agent_ctl.status_shows_brain(_running_status("vps"), "pc") is False  # wrong brain
    assert agent_ctl.status_shows_brain({"brain": "pc", "agent": {"lk.agent.name": "jarvis-pc",
                                                                  "jarvis.session": "stopped"}}, "pc") is False
    assert agent_ctl.status_shows_brain(None, "vps") is False


def test_idle_gate_last_activity_is_read_from_status(ctl):
    assert agent_ctl._status_last_activity({"last_activity_ts": 1234.5}) == 1234.5
    assert agent_ctl._status_last_activity({"desktop": {"last_activity_ts": 99}}) == 99.0
    assert agent_ctl._status_last_activity({}) is None


def _make_switch(ctl, target, *, ssh, status, laptop, **kwargs):
    return agent_ctl.BrainSwitch(target, journal_path=ctl.run / agent_ctl.BRAIN_JOURNAL_FILE, ssh=ssh,
                                 status=status, laptop=laptop, sleep=lambda _s: None, **kwargs)


def test_brain_switch_resumes_from_the_journal_after_an_interruption(ctl, monkeypatch):
    ctl.run.mkdir(parents=True)
    (ctl.run / "node_current.json").write_text(json.dumps({"snapshot": "x"}), encoding="utf-8")
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: True)
    live = LiveStatus("pc")
    laptop = FakeLaptop(live)
    ssh_calls: list = []

    def crash_ssh(cmd):
        ssh_calls.append(cmd)
        joined = " ".join(cmd)
        if _provision_answer(cmd) is not None:
            return _provision_answer(cmd)
        if "--get" in joined:
            return subprocess.CompletedProcess(cmd, 0, "pc", "")
        if "sync_brain_data.sh" in joined:
            raise KeyboardInterrupt  # the machine is interrupted mid-switch (not a BrainSwitchError)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    first = _make_switch(ctl, "vps", ssh=crash_ssh, status=live, laptop=laptop)
    with pytest.raises(KeyboardInterrupt):
        first.run()
    journal = json.loads((ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "STOP_OLD" and journal["previous"] == "pc"  # progress was recorded

    # Resume with a working ssh: it must not repeat BACKUP/STOP_OLD, and must finish.
    resume_ssh: list = []
    second = _make_switch(ctl, "vps", ssh=lambda cmd: resume_ssh.append(cmd) or
                          subprocess.CompletedProcess(cmd, 0, "pc" if "--get" in " ".join(cmd) else "", ""),
                          status=live, laptop=laptop)
    code, details = second.run()

    assert code == 0 and details["result"] == "switched"
    done = " ".join(" ".join(c) for c in resume_ssh)
    assert "sync_brain_data.sh" in done and "set_env.py JARVIS_BRAIN vps" in done  # the remaining states ran
    assert "backup.sh" not in done  # BACKUP was already done before the interruption
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()  # cleared on success


def test_brain_switch_restores_the_previous_brain_on_a_mid_switch_failure(ctl, monkeypatch):
    ctl.run.mkdir(parents=True)
    (ctl.run / "node_current.json").write_text(json.dumps({"snapshot": "x"}), encoding="utf-8")
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: True)
    laptop = FakeLaptop()
    ssh_calls: list = []

    def failing_ssh(cmd):
        ssh_calls.append(cmd)
        joined = " ".join(cmd)
        if _provision_answer(cmd) is not None:
            return _provision_answer(cmd)
        if "--get" in joined:
            return subprocess.CompletedProcess(cmd, 0, "pc", "")  # previous brain is pc
        if "--profile vps-brain up" in joined:
            return subprocess.CompletedProcess(cmd, 1, "", "compose failed")  # START_NEW fails
        return subprocess.CompletedProcess(cmd, 0, "", "")

    # After the failure the restore brings pc back, so status reports pc.
    switch = _make_switch(ctl, "vps", ssh=failing_ssh, status=lambda: _running_status("pc"), laptop=laptop)
    code, details = switch.run()

    assert code == agent_ctl.EXIT_FAILED
    assert details["result"] == "failed" and details["restored"] is True and details["brain"] == "pc"
    joined = " ".join(" ".join(c) for c in ssh_calls)
    assert "set_env.py JARVIS_BRAIN vps" in joined     # it flipped to vps first
    assert "set_env.py JARVIS_BRAIN pc" in joined       # then restored pc
    assert ("start", "pc") in laptop.calls              # the pc worker was brought back
    # STOP_OLD stopped the worker; the restore first makes sure the new side (the node) is down, since it would
    # hold run/desktop_actor.lock against the worker, then starts the worker and ensures the room.
    assert laptop.calls == [("stop", "pc"), ("stop", "vps"), ("start", "pc"), ("ensure", "pc")]
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()  # cleared after restore


def test_brain_switch_is_a_no_op_when_already_on_the_target(ctl):
    ctl.run.mkdir(parents=True)
    laptop = FakeLaptop()
    switch = _make_switch(ctl, "pc", ssh=lambda cmd: subprocess.CompletedProcess(cmd, 0, "pc", ""),
                          status=lambda: _running_status("pc"), laptop=laptop)
    code, details = switch.run()
    assert code == 0 and details["result"] == "already"
    assert laptop.calls == []  # nothing was stopped or started


def test_brain_now_speaks_a_notice_then_switches(ctl, monkeypatch):
    ctl.run.mkdir(parents=True)
    live = LiveStatus("vps")  # the VPS brain runs; the PC worker's agent shows up once it is started
    laptop = FakeLaptop(live)
    spoken: list = []
    vps = FakeVps(brain="vps")
    sleeps: list = []
    switch = agent_ctl.BrainSwitch("pc", journal_path=ctl.run / agent_ctl.BRAIN_JOURNAL_FILE, ssh=vps,
                                   status=live, laptop=laptop, notify=spoken.append,
                                   now_force=True, sleep=sleeps.append)
    code, details = switch.run()
    assert code == 0 and details["result"] == "switched"
    assert spoken and "60 seconds" in spoken[0]  # the spoken 60 s notice (C-123 step 6)
    assert sleeps[0] == agent_ctl.SPOKEN_NOTICE_S  # ...and the 60 s wait after it
    # The vps -> pc path really ran: the node and the VPS brain were stopped, the PC worker started and ensured.
    assert laptop.calls == [("stop", "vps"), ("start", "pc"), ("ensure", "pc")]
    assert "set_env.py JARVIS_BRAIN pc" in vps.joined()
    assert "--profile vps-brain stop agent agent-svc" in vps.joined()


# =============================================================================================================
# PC-mode safety: only an explicit JARVIS_BRAIN=vps ever blocks the owner's worker (C-123 step 5, K-72)
# =============================================================================================================


@pytest.mark.parametrize("value, brain, warned", [
    (None, "pc", False), ("", "pc", False), ("   ", "pc", False), ("pc", "pc", False), ("PC ", "pc", False),
    ("vsp", "pc", True), ("cloud", "pc", True), ("vps", "vps", False), (" VPS ", "vps", False)])
def test_current_brain_falls_back_to_pc_and_warns_only_for_an_unknown_value(ctl, monkeypatch, capsys, value,
                                                                             brain, warned):
    monkeypatch.setattr(agent_ctl, "_brain_warned", set())
    if value is not None:
        monkeypatch.setenv("JARVIS_BRAIN", value)
    assert agent_ctl.current_brain() == brain
    assert agent_ctl.current_brain() == brain  # the warning comes once per process
    err = capsys.readouterr().err
    assert err.count("is neither 'vps' nor 'pc'; treating it as 'pc'") == (1 if warned else 0)


def _brain_setting(ctl, monkeypatch, value, source):
    """JARVIS_BRAIN=value in the process environment or in the root .env (as agent_ctl reads it, K-47)."""
    monkeypatch.setattr(agent_ctl, "_brain_warned", set())
    if value is None:
        return
    if source == "environ":
        monkeypatch.setenv("JARVIS_BRAIN", value)
    else:
        env_file = ctl.tmp / "root.env"
        env_file.write_text(f"LOG_LEVEL=INFO\nJARVIS_BRAIN={value}\n", encoding="utf-8")
        monkeypatch.setattr(agent_ctl, "ENV_FILES", (env_file,))


@pytest.mark.parametrize("source", ["environ", "root_env"])
@pytest.mark.parametrize("value", [None, "pc", "PC ", "unknown-brain", "vps"])
def test_only_an_explicit_vps_refuses_a_worker_start(ctl, monkeypatch, capsys, value, source):
    _brain_setting(ctl, monkeypatch, value, source)
    started: list = []
    monkeypatch.setattr(agent_ctl, "start_worker", lambda args, mode, **k: started.append(mode) or
                        (0, {"result": "started", "new_pid": 4242, "start_s": 1.0}))

    code = agent_ctl.main(["start", "--by", "user", "--reason", "pc-safety"])

    err = capsys.readouterr().err
    if value == "vps":
        assert code == agent_ctl.EXIT_REFUSED and started == []
        assert audit_lines(ctl.logs)[-1]["result"] == "refused"
    else:
        assert code == 0 and started == ["start"]  # the C-09 path runs exactly as before
        assert "refused" not in err
        assert ("treating it as 'pc'" in err) is (value == "unknown-brain")


@pytest.mark.parametrize("value", [None, "pc", "PC ", "unknown-brain"])
def test_pc_mode_restart_is_not_blocked_by_the_brain_gate(ctl, monkeypatch, value):
    _brain_setting(ctl, monkeypatch, value, "environ")
    ran: list = []
    monkeypatch.setattr(agent_ctl, "_restart", lambda args: ran.append(args.by) or
                        (0, {"result": "restarted", "stop": {"old_pid": 1}, "start": {"new_pid": 2},
                             "restart_s": 1.0}))
    assert agent_ctl.main(["restart", "--by", "user", "--reason", "pc-safety"]) == 0
    assert ran == ["user"]


def test_status_wait_and_logs_never_consult_the_brain(ctl, monkeypatch):
    """status, wait and logs (and stop) behave the same in every mode: they never read JARVIS_BRAIN."""
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    monkeypatch.setattr(agent_ctl, "current_brain", lambda: pytest.fail("JARVIS_BRAIN must not be read here"))
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: None)
    monkeypatch.setattr(agent_ctl, "wait_healthy", lambda timeout, proc=None, progress=False: (False, "no worker"))
    assert agent_ctl.main(["status"]) == 1          # no worker under tmp_path: NOT RUNNING, as before
    assert agent_ctl.main(["wait", "--timeout", "1"]) == 1
    assert agent_ctl.main(["logs", "-n", "1"]) == 0
    assert agent_ctl.main(["stop", "--by", "user"]) == 0  # nothing holds run/agent.lock: a no-op


# =============================================================================================================
# brain PREFLIGHT: refuse an unprovisioned VPS before any BACKUP/STOP_OLD (C-126..C-129 not done yet)
# =============================================================================================================


def _changing_calls(vps: FakeVps) -> list:
    """The ssh commands beyond PREFLIGHT's reads (the provisioning check and 'set_env.py --get')."""
    return [c for c in vps.calls if "config --services" not in " ".join(c) and "--get" not in " ".join(c)]


def _pin_snapshot(ctl) -> None:
    """What 'node update' leaves behind: the pinned snapshot and the node's logon task (its read-only /Query)."""
    ctl.run.mkdir(parents=True, exist_ok=True)
    (ctl.run / "node_current.json").write_text(json.dumps({"snapshot": "x", "sha": "c" * 40}), encoding="utf-8")
    ctl.mp.setattr(agent_ctl, "node_task_exists", lambda: True)


@pytest.mark.parametrize("provision, expect", [
    ("missing:deploy/hostinger/backup.sh\n" + PROVISIONED, ["deploy/hostinger/backup.sh"]),
    ("missing:deploy/hostinger/sync_brain_data.sh\n" + PROVISIONED, ["deploy/hostinger/sync_brain_data.sh"]),
    ("missing:deploy/hostinger/set_env.py\n" + PROVISIONED, ["deploy/hostinger/set_env.py"]),
    ("missing:/opt/jarvis-agent\n", ["/opt/jarvis-agent", "compose service jarvis-agent",
                                     "compose service jarvis-agent-svc"]),
    ("service:livekit\nservice:ui-bridge\nservice:frontend\n", ["compose service jarvis-agent",
                                                                 "compose service jarvis-agent-svc"]),
    ("service:agent\n", ["compose service jarvis-agent-svc"]),
    ("missing:a docker compose config that compose accepts (deploy/hostinger)\n",
     ["a docker compose config that compose accepts (deploy/hostinger)", "compose service jarvis-agent",
      "compose service jarvis-agent-svc"]),
])
def test_preflight_refuses_an_unprovisioned_vps_before_any_change(ctl, provision, expect):
    _pin_snapshot(ctl)
    laptop = FakeLaptop()
    vps = FakeVps(brain="pc", provision=provision)
    spoken: list = []
    switch = _make_switch(ctl, "vps", ssh=vps, status=lambda: _running_status("pc"), laptop=laptop,
                          notify=spoken.append, now_force=True)

    code, details = switch.run()

    assert code == agent_ctl.EXIT_REFUSED == 4
    assert (details["result"], details["state"], details["missing"]) == ("refused", "PREFLIGHT", expect)
    assert details["error"] == f"VPS brain not provisioned yet: missing {', '.join(expect)} (C-126..C-129)"
    assert laptop.calls == []           # STOP_OLD never ran: the PC worker and the node were not touched
    assert _changing_calls(vps) == []   # no backup, sync, env flip or compose command on the VPS
    assert spoken == []                 # a refused switch is never announced
    journal = json.loads((ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert (journal["result"], journal["refused_at"], journal["state"]) == ("refused", "PREFLIGHT", None)
    assert journal["missing"] == expect and journal["previous"] == "pc"  # the brain is unchanged


def test_preflight_refusal_through_the_cli_exits_4_and_is_audited(ctl, monkeypatch, capsys):
    _pin_snapshot(ctl)
    vps = FakeVps(brain="pc", provision="missing:deploy/hostinger/backup.sh\n" + PROVISIONED)
    laptop = FakeLaptop()
    spoken: list = []
    monkeypatch.setattr(agent_ctl, "_default_ssh", vps)
    monkeypatch.setattr(agent_ctl, "vortex_status", lambda base=None: _running_status("pc"))
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: laptop)
    monkeypatch.setattr(agent_ctl, "_speak_notice", spoken.append)

    code = agent_ctl.main(["brain", "vps", "--now", "--by", "claude", "--reason", "C-123"])

    assert code == agent_ctl.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "VPS brain not provisioned yet: missing deploy/hostinger/backup.sh (C-126..C-129)" in err
    assert "Nothing was backed up, stopped or changed" in err
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["state"], entry["exit"]) == ("brain", "refused", "PREFLIGHT", 4)
    assert entry["missing"] == ["deploy/hostinger/backup.sh"] and entry["target"] == "vps"
    assert laptop.calls == [] and spoken == [] and _changing_calls(vps) == []


def test_preflight_refuses_when_the_vps_cannot_be_reached(ctl, monkeypatch):
    _pin_snapshot(ctl)
    laptop = FakeLaptop()
    vps = FakeVps(brain="pc", check_code=255)
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: _running_status("pc"),
                                 laptop=laptop).run()
    assert code == 4 and details["state"] == "PREFLIGHT" and "exited 255" in details["error"]
    assert laptop.calls == [] and _changing_calls(vps) == []

    # No ssh target configured at all: refused the same way, before any process is run (ctl refuses _run).
    for name in ("JARVIS_VPS_SSH", "JARVIS_VPS_HOST"):
        monkeypatch.delenv(name, raising=False)
    code, details = _make_switch(ctl, "vps", ssh=agent_ctl._default_ssh, status=lambda: _running_status("pc"),
                                 laptop=laptop).run()
    assert code == 4 and "JARVIS_VPS_SSH is not set" in details["error"] and laptop.calls == []


def test_preflight_refuses_without_a_status_answer_or_a_snapshot(ctl):
    ctl.run.mkdir(parents=True)
    laptop = FakeLaptop()
    vps = FakeVps(brain="pc")
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: _running_status("pc"), laptop=laptop).run()
    assert code == 4 and "no pinned node snapshot" in details["error"]
    _pin_snapshot(ctl)
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: None, laptop=laptop).run()
    assert code == 4 and "/vortex/status does not answer" in details["error"]
    assert laptop.calls == [] and _changing_calls(vps) == []


def test_a_provisioned_vps_proceeds_through_every_state(ctl):
    _pin_snapshot(ctl)
    live = LiveStatus("pc")
    laptop = FakeLaptop(live)
    vps = FakeVps(brain="pc")
    code, details = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=laptop).run()

    assert code == 0 and details == {"result": "switched", "brain": "vps"}
    assert laptop.calls == [("stop", "pc"), ("start", "vps"), ("ensure", "vps")]
    steps = [" ".join(c) for c in _changing_calls(vps)]
    assert steps == [
        "bash -lc cd /opt/jarvis-agent && ./deploy/hostinger/backup.sh",
        "bash -lc cd /opt/jarvis-agent && ./deploy/hostinger/sync_brain_data.sh vps",
        "bash -lc cd /opt/jarvis-agent && python3 deploy/hostinger/set_env.py JARVIS_BRAIN vps",
        "bash -lc cd /opt/jarvis-agent/deploy/hostinger && docker compose -p jarvis --profile vps-brain up -d "
        "agent agent-svc"]
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


def test_container_named_compose_services_are_used_as_found(ctl):
    _pin_snapshot(ctl)
    vps = FakeVps(brain="pc", provision="service:jarvis-agent\nservice:jarvis-agent-svc\n")
    live = LiveStatus("pc")
    code, _ = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=FakeLaptop(live)).run()
    assert code == 0
    assert "up -d jarvis-agent jarvis-agent-svc" in vps.joined()


def test_a_backup_failure_changes_nothing_and_restores_nothing(ctl):
    _pin_snapshot(ctl)
    laptop = FakeLaptop()
    vps = FakeVps(brain="pc", fail_on="./deploy/hostinger/backup.sh")
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: _running_status("pc"), laptop=laptop).run()
    assert code == agent_ctl.EXIT_FAILED
    assert (details["state"], details["changed"], details["brain"]) == ("BACKUP", False, "pc")
    assert laptop.calls == []  # the PC worker was never stopped, so there is nothing to restore
    assert "set_env.py JARVIS_BRAIN" not in vps.joined()
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


def test_a_half_done_switch_to_the_other_brain_blocks_a_new_switch(ctl):
    ctl.run.mkdir(parents=True)
    journal = ctl.run / agent_ctl.BRAIN_JOURNAL_FILE
    journal.write_text(json.dumps({"target": "vps", "previous": "pc", "state": "SYNC_DATA"}), encoding="utf-8")
    vps = FakeVps(brain="pc")
    laptop = FakeLaptop()
    code, details = _make_switch(ctl, "pc", ssh=vps, status=lambda: _running_status("pc"), laptop=laptop).run()
    assert code == 4 and "run 'agent_ctl brain vps' first" in details["error"]
    assert vps.calls == [] and laptop.calls == []
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "SYNC_DATA"  # kept for the resume


def test_a_refusal_record_is_never_resumed(ctl):
    """After a PREFLIGHT refusal the next run starts a fresh PREFLIGHT (nothing had been done)."""
    _pin_snapshot(ctl)
    journal = ctl.run / agent_ctl.BRAIN_JOURNAL_FILE
    journal.write_text(json.dumps({"target": "vps", "previous": "pc", "state": None, "result": "refused",
                                   "refused_at": "PREFLIGHT"}), encoding="utf-8")
    vps = FakeVps(brain="pc")
    live = LiveStatus("pc")
    code, _ = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=FakeLaptop(live)).run()
    assert code == 0
    assert "config --services" in vps.joined() and "backup.sh" in vps.joined()


def test_brain_without_now_waits_for_the_idle_gate(ctl, monkeypatch):
    """The CLI applies the idle gate unless --now: last activity must be 10 min old (bridge last_activity_ts)."""
    _pin_snapshot(ctl)
    live = LiveStatus("pc", last_activity_ts=time.time() - 120)
    laptop = FakeLaptop(live)
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: laptop)
    monkeypatch.setattr(agent_ctl, "_speak_notice", lambda m: pytest.fail("no notice without --now"))
    monkeypatch.setattr(agent_ctl, "vortex_status", lambda base=None: live())
    monkeypatch.setattr(agent_ctl, "_default_ssh", FakeVps(brain="pc"))

    assert agent_ctl.main(["brain", "vps"]) == agent_ctl.EXIT_REFUSED  # active 2 min ago
    assert "not idle for 600 s" in audit_lines(ctl.logs)[-1]["error"] and laptop.calls == []

    live.extra["last_activity_ts"] = time.time() - 700  # idle for > 10 min
    assert agent_ctl.main(["brain", "vps", "--when-idle"]) == 0
    assert laptop.calls == [("stop", "pc"), ("start", "vps"), ("ensure", "vps")]


@pytest.mark.parametrize("status, why", [
    ({"status": "ok", "brain": "pc"}, "no agent participant whose lk.agent.name is 'jarvis-pc'"),
    ({"status": "ok", "brain": "pc", "agent": {"online": True, "voice_ready": True}},
     "no agent participant whose lk.agent.name is 'jarvis-pc'"),
    ({"status": "ok", "agent": {"online": True}}, "does not report the brain field yet"),
    ({"status": "ok", "brain": "vps"}, "reports brain 'vps' but the VPS .env says 'pc'"),
])
def test_preflight_refuses_a_status_that_could_never_confirm_the_switch(ctl, status, why):
    """/vortex/status answers but cannot show the K-72 condition (today's bridge has no lk.agent.name /
    jarvis.session fields): VERIFY and the restore check could never pass, so it is refused before BACKUP."""
    _pin_snapshot(ctl)
    laptop = FakeLaptop()
    vps = FakeVps(brain="pc")
    spoken: list = []
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: status, laptop=laptop,
                                 notify=spoken.append, now_force=True).run()

    assert code == agent_ctl.EXIT_REFUSED and details["state"] == "PREFLIGHT"
    assert why in details["error"] and "could ever confirm a switch" in details["error"]
    assert laptop.calls == [] and _changing_calls(vps) == [] and spoken == []  # no BACKUP/STOP_OLD, no notice
    journal = json.loads((ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert (journal["result"], journal["refused_at"], journal["previous"]) == ("refused", "PREFLIGHT", "pc")


def test_a_journal_left_at_preflight_starts_over_with_a_full_preflight(ctl):
    """A crash right after PREFLIGHT changed nothing: the next run repeats PREFLIGHT (provisioning check, idle
    gate) instead of resuming at BACKUP."""
    _pin_snapshot(ctl)
    journal = ctl.run / agent_ctl.BRAIN_JOURNAL_FILE
    journal.write_text(json.dumps({"target": "vps", "previous": "pc", "state": "PREFLIGHT"}), encoding="utf-8")
    vps = FakeVps(brain="pc")
    live = LiveStatus("pc", last_activity_ts=time.time() - 60)  # active a minute ago
    laptop = FakeLaptop(live)
    code, details = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=laptop, when_idle=True).run()

    assert code == agent_ctl.EXIT_REFUSED and details["state"] == "PREFLIGHT"
    assert "not idle for 600 s" in details["error"]
    assert "config --services" in vps.joined()          # the provisioning check ran again
    assert laptop.calls == [] and _changing_calls(vps) == []  # BACKUP was not resumed


class _Boom(RuntimeError):
    """An unexpected (non-BrainSwitchError) failure inside a state."""


def test_an_unexpected_error_after_stop_old_still_restores(ctl):
    _pin_snapshot(ctl)
    live = LiveStatus("pc")

    class StopBreaks(FakeLaptop):
        def stop(self, brain):
            super().stop(brain)
            raise _Boom("a bug in the laptop side")

    laptop = StopBreaks(live)
    vps = FakeVps(brain="pc")
    code, details = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=laptop).run()

    assert code == agent_ctl.EXIT_FAILED
    assert details["result"] == "failed" and details["restored"] is True and details["brain"] == "pc"
    assert details["error"] == "failed at STOP_OLD: unexpected _Boom"  # the type only, never the message
    assert "set_env.py JARVIS_BRAIN pc" in vps.joined()     # the restore ran
    assert ("start", "pc") in laptop.calls and ("ensure", "pc") in laptop.calls
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


@pytest.mark.parametrize("state", ["_preflight", "_backup"])
def test_an_unexpected_error_before_stop_old_changes_nothing(ctl, monkeypatch, state):
    _pin_snapshot(ctl)
    live = LiveStatus("pc")
    laptop = FakeLaptop(live)
    vps = FakeVps(brain="pc")
    switch = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=laptop)

    def boom():
        raise _Boom("unexpected")

    monkeypatch.setattr(switch, state, boom)
    code, details = switch.run()
    assert code == agent_ctl.EXIT_FAILED and details["changed"] is False and details["brain"] == "pc"
    assert "unexpected _Boom" in details["error"]
    assert laptop.calls == [] and "set_env.py JARVIS_BRAIN" not in vps.joined()
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


# --- the REAL LaptopBrain, driven through 'agent_ctl brain ...'; only the process/HTTP/schtasks edges are mocked


def _real_laptop(ctl, monkeypatch, *, node_up: bool, live: LiveStatus, vps: FakeVps) -> dict:
    """Patch only node_running / node_stop_request / _schtasks / start_worker / stop_worker / run_ensure_room (and
    the sleeps, ssh, bridge and notice), so LaptopBrain, node_start and node_stop run for real. Returns the state
    with the ordered events."""
    state: dict = {"node_up": node_up, "events": []}
    events = state["events"]

    def stop_request(timeout=agent_ctl.NODE_STOP_TIMEOUT_S):
        events.append(("node_stop_request", timeout))
        state["node_up"] = False
        return True, ""

    def schtasks(*args, check=False):
        events.append(("schtasks", *args))
        if args[:1] == ("/Run",):
            state["node_up"] = True
        return subprocess.CompletedProcess(["schtasks", *args], 0, "", "")

    def start_worker(args, mode, **_kwargs):
        # The real worker exits 3 while the node holds run/desktop_actor.lock.
        events.append(("start_worker", mode, "node_up" if state["node_up"] else "node_down"))
        if state["node_up"]:
            return agent_ctl.EXIT_ALREADY_RUNNING, {"result": "failed"}
        live.brain = "pc"
        return agent_ctl.EXIT_OK, {"result": "started"}

    def stop_worker(timeout, force=False):
        events.append(("stop_worker",))
        return agent_ctl.EXIT_OK, {"result": "stopped"}

    monkeypatch.setattr(agent_ctl, "node_running", lambda: state["node_up"])
    monkeypatch.setattr(agent_ctl, "node_stop_request", stop_request)
    monkeypatch.setattr(agent_ctl, "_schtasks", schtasks)
    monkeypatch.setattr(agent_ctl, "start_worker", start_worker)
    monkeypatch.setattr(agent_ctl, "stop_worker", stop_worker)
    monkeypatch.setattr(agent_ctl, "run_ensure_room", lambda env: events.append(("ensure_room",)) or 0)
    monkeypatch.setattr(agent_ctl, "_sleep", lambda _s: None)
    monkeypatch.setattr(agent_ctl, "_default_ssh", vps)
    monkeypatch.setattr(agent_ctl, "vortex_status", lambda base=None: live())
    monkeypatch.setattr(agent_ctl, "_speak_notice", lambda _m: None)
    return state


DISABLE = ("schtasks", "/Change", "/TN", agent_ctl.NODE_TASK_NAME, "/Disable")


def test_real_laptop_brain_vps_to_pc_stops_the_node_and_starts_the_worker(ctl, monkeypatch):
    """The 'brain' arguments have no --timeout: STOP_OLD must still stop the node (NODE_STOP_TIMEOUT_S) and
    disable its logon task, then the PC worker starts. Before the fix this raised AttributeError at STOP_OLD."""
    ctl.run.mkdir(parents=True)
    live = LiveStatus("vps")
    vps = FakeVps(brain="vps")
    state = _real_laptop(ctl, monkeypatch, node_up=True, live=live, vps=vps)

    code = agent_ctl.main(["brain", "pc", "--now", "--by", "claude", "--reason", "C-123"])

    assert code == 0
    assert state["events"] == [("node_stop_request", agent_ctl.NODE_STOP_TIMEOUT_S), DISABLE,
                               ("start_worker", "start", "node_down"), ("ensure_room",)]
    assert "--profile vps-brain stop agent agent-svc" in vps.joined()
    assert "set_env.py JARVIS_BRAIN pc" in vps.joined()
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["brain"]) == ("brain", "switched", "pc")
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


def test_real_laptop_brain_pc_to_vps_verify_failure_restores_the_pc_worker(ctl, monkeypatch):
    """VERIFY never sees the vps agent: the restore must stop the node (it would hold run/desktop_actor.lock),
    disable its logon task, put JARVIS_BRAIN back and start the PC worker, which then really comes up."""
    _pin_snapshot(ctl)
    live = LiveStatus("pc")  # the bridge keeps showing pc: VERIFY for vps fails
    vps = FakeVps(brain="pc")
    state = _real_laptop(ctl, monkeypatch, node_up=False, live=live, vps=vps)

    code = agent_ctl.main(["brain", "vps", "--now", "--by", "claude", "--reason", "C-123"])

    assert code == agent_ctl.EXIT_FAILED
    events = state["events"]
    assert events[0] == ("stop_worker",)                                    # STOP_OLD
    assert ("schtasks", "/Run", "/TN", agent_ctl.NODE_TASK_NAME) in events  # START_NEW ran the node
    restore = events[events.index(("node_stop_request", agent_ctl.NODE_STOP_TIMEOUT_S)):]
    assert restore == [("node_stop_request", agent_ctl.NODE_STOP_TIMEOUT_S), DISABLE,
                       ("start_worker", "start", "node_down"), ("ensure_room",)]
    assert state["node_up"] is False
    joined = vps.joined()
    assert joined.index("set_env.py JARVIS_BRAIN vps") < joined.index("set_env.py JARVIS_BRAIN pc")
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["restored"], entry["brain"]) == ("brain", "failed", True, "pc")
    assert entry["error"].startswith("failed at VERIFY")
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


def test_node_stop_cli_still_honours_its_timeout(ctl, monkeypatch):
    live = LiveStatus("pc")
    state = _real_laptop(ctl, monkeypatch, node_up=True, live=live, vps=FakeVps())
    assert agent_ctl.main(["node", "stop", "--timeout", "7"]) == 0
    assert state["events"] == [("node_stop_request", 7.0), DISABLE]


def test_default_ssh_sends_the_command_as_one_quoted_argument(ctl, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent_ctl, "_run", lambda cmd, **k: captured.update(cmd=cmd) or
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setenv("JARVIS_VPS_SSH", "jarvis-vps-alias")
    monkeypatch.delenv("JARVIS_SSH_KEY", raising=False)
    command = ["bash", "-lc", "cd /opt/jarvis-agent && ./deploy/hostinger/sync_brain_data.sh 'pc'"]
    agent_ctl._default_ssh(command)
    argv = captured["cmd"]
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and argv[-2] == "jarvis-vps-alias"
    assert shlex.split(argv[-1]) == command  # the remote shell gets back exactly this argv


def _posix_bash() -> str | None:
    bash = shutil.which("bash")
    if not bash or (sys.platform == "win32" and "system32" in bash.lower()):  # the WSL launcher, not Git Bash
        return None
    return bash


def test_the_provisioning_check_runs_in_a_real_shell(monkeypatch, tmp_path):
    """The generated check script (quoted the way _default_ssh sends it) reports each missing helper and the
    compose services, against a fake checkout and a fake docker on PATH. It only reads."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash on this machine")
    app = tmp_path / "app"
    hostinger = app / "deploy" / "hostinger"
    hostinger.mkdir(parents=True)
    (hostinger / "backup.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(hostinger / "backup.sh", 0o755)
    (hostinger / "set_env.py").write_text("print('pc')\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text("#!/bin/sh\nprintf 'livekit\\nagent\\nagent-svc\\n'\n", encoding="utf-8")
    os.chmod(docker, 0o755)
    monkeypatch.setattr(agent_ctl, "VPS_APP_DIR", app.as_posix())
    monkeypatch.setattr(agent_ctl, "VPS_COMPOSE_DIR", hostinger.as_posix())
    env = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}

    def check() -> tuple[list[str], dict[str, str]]:
        remote = shlex.join(["bash", "-c", agent_ctl.vps_provision_script()])
        result = subprocess.run([bash, "-c", remote], capture_output=True, text=True, env=env, timeout=60,
                                check=False)
        assert result.returncode == 0, result.stderr
        return agent_ctl.parse_vps_provision(result.stdout)

    missing, services = check()
    assert missing == ["deploy/hostinger/sync_brain_data.sh"]
    assert services == {"jarvis-agent": "agent", "jarvis-agent-svc": "agent-svc"}

    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")  # compose rejects its file or .env
    missing, services = check()
    assert "a docker compose config that compose accepts (deploy/hostinger)" in missing
    assert "compose service jarvis-agent" in missing and services == {}


# =============================================================================================================
# VERIFY: only the target brain with a running agent of the target's lk.agent.name counts
# =============================================================================================================


def test_status_shows_brain_reads_livekit_participant_attributes(ctl):
    status = {"brain": "vps", "livekit": {"participants": [
        {"identity": "owner-1", "attributes": {"role": "owner"}},
        {"identity": "agent-AJ_x", "attributes": {"lk.agent.name": "jarvis", "jarvis.session": "running"}}]}}
    assert agent_ctl.status_shows_brain(status, "vps") is True
    # A display name or identity is never taken for lk.agent.name, and only a running session counts.
    assert agent_ctl.status_shows_brain({"brain": "vps", "agent": {"name": "jarvis",
                                                                    "jarvis.session": "running"}}, "vps") is False
    for session in ("starting", "restarting", "idle", "closed", "online"):
        assert agent_ctl.status_shows_brain({"brain": "vps", "agent": {"lk.agent.name": "jarvis",
                                                                        "jarvis.session": session}}, "vps") is False
    assert agent_ctl.status_shows_brain({"brain": "pc", "agent": {"worker_agent_name": "jarvis-pc",
                                                                   "session": "running"}}, "pc") is True


# =============================================================================================================
# node start/update/status in pc mode, the logon task command line
# =============================================================================================================


def test_node_start_is_refused_while_a_pc_worker_holds_the_lock(ctl, monkeypatch, capsys):
    _pin_snapshot(ctl)
    monkeypatch.setattr(agent_ctl, "node_running", lambda: False)
    monkeypatch.setattr(agent_ctl.agent_lock, "is_locked", lambda: True)
    monkeypatch.setattr(agent_ctl, "run_node_task", lambda: pytest.fail("the node must not start"))
    monkeypatch.setattr(agent_ctl, "set_node_task_enabled", lambda enabled: pytest.fail("no task change"))
    assert agent_ctl.main(["node", "start", "--by", "claude"]) == agent_ctl.EXIT_REFUSED
    assert "a PC agent worker runs" in capsys.readouterr().err
    assert audit_lines(ctl.logs)[-1]["result"] == "refused"


def _mock_update(ctl, monkeypatch, *, running: bool, locked: bool = False):
    ctl.run.mkdir(parents=True, exist_ok=True)
    plan = node_snapshot.UpdatePlan("d" * 40, ctl.tmp / "snap", "active", ctl.tmp / "c.json", None)
    monkeypatch.setattr(node_snapshot, "update_snapshot", lambda **kwargs: plan)
    monkeypatch.setattr(agent_ctl, "create_node_task", lambda snap, canary=False: True)
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: True)
    monkeypatch.setattr(agent_ctl.agent_lock, "is_locked", lambda: locked)
    calls: list = []
    state = {"up": running}
    monkeypatch.setattr(agent_ctl, "set_node_task_enabled", lambda enabled: calls.append(("enabled", enabled)))
    monkeypatch.setattr(agent_ctl, "run_node_task", lambda: calls.append(("run",)) or state.update(up=True) or True)
    monkeypatch.setattr(agent_ctl, "node_running", lambda: state["up"])
    monkeypatch.setattr(agent_ctl, "node_stop_request", lambda timeout: calls.append(("stop",)) or
                        state.update(up=False) or (True, ""))
    monkeypatch.setattr(agent_ctl, "_wait_until", lambda cond, timeout, step=0.5: cond())
    return calls


def test_node_update_in_pc_mode_leaves_a_stopped_node_stopped_and_its_task_disabled(ctl, monkeypatch, capsys):
    calls = _mock_update(ctl, monkeypatch, running=False)
    assert agent_ctl.main(["node", "update", "--by", "claude", "--reason", "C-123"]) == 0
    assert calls == [("enabled", False)]  # never run: the owner's PC worker stays the laptop target
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["restarted"], entry["brain"]) == (False, "pc")
    assert "pc mode: the node stays stopped" in capsys.readouterr().out


def test_node_update_restarts_a_running_node_in_pc_mode(ctl, monkeypatch):
    calls = _mock_update(ctl, monkeypatch, running=True)
    assert agent_ctl.main(["node", "update"]) == 0
    assert calls == [("stop",), ("enabled", True), ("run",)]
    assert audit_lines(ctl.logs)[-1]["restarted"] is True


def test_node_update_never_starts_the_node_beside_a_pc_worker(ctl, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    calls = _mock_update(ctl, monkeypatch, running=False, locked=True)
    assert agent_ctl.main(["node", "update"]) == 0
    assert ("run",) not in calls
    assert "a PC agent worker holds run/agent.lock" in capsys.readouterr().out


def test_create_node_task_refuses_a_command_line_schtasks_would_reject(ctl, monkeypatch, capsys):
    monkeypatch.setattr(agent_ctl, "_schtasks", lambda *a, **k: pytest.fail("schtasks must not run"))
    assert agent_ctl.create_node_task(Path("C:/" + "x" * 250) / ("f" * 40)) is False
    err = capsys.readouterr().err
    assert "snapshot path (294 characters) is longer than 259 characters" in err
    assert not ctl.run.exists() or not list(ctl.run.glob(".node_task.*.xml"))  # no definition was written


@pytest.mark.parametrize("stdout, code, expected", [
    ("HostName: PC\nTaskName: \\JARVIS Desktop Node\nStatus: Disabled\nScheduled Task State: Disabled\n", 0,
     "disabled"),
    ("Status: Ready\nScheduled Task State: Enabled\n", 0, "enabled, ready"),
    ("Status: Running\nScheduled Task State: Enabled\n", 0, "enabled, running"),
    ("something else\n", 0, "present"),
    ("", 1, None)])
def test_node_task_state_reads_only_the_state_lines(ctl, monkeypatch, stdout, code, expected):
    seen: list = []
    monkeypatch.setattr(agent_ctl, "_schtasks", lambda *a, **k: seen.append(a) or
                        subprocess.CompletedProcess(list(a), code, stdout, ""))
    assert agent_ctl.node_task_state() == expected
    assert seen == [("/Query", "/TN", agent_ctl.NODE_TASK_NAME, "/FO", "LIST", "/V")]  # a read-only query


# =============================================================================================================
# node_snapshot: a half-extracted snapshot is never pinned; nothing runs at import time
# =============================================================================================================


def test_an_interrupted_build_never_leaves_a_half_tree_under_the_snapshot_name(monkeypatch, tmp_path):
    home = tmp_path / "home"
    dest = node_snapshot.snapshot_dir("c" * 40, home=home)
    real_extract = node_snapshot._extract_tar

    def failing_extract(tar_path, into):
        (into / "half.py").write_text("# only part of the tree\n", encoding="utf-8")
        raise node_snapshot.SnapshotError("disk full (simulated)")

    monkeypatch.setattr(node_snapshot, "_extract_tar", failing_extract)
    with pytest.raises(node_snapshot.SnapshotError):
        node_snapshot.build_snapshot("c" * 40, dest, runner=_fake_git([]))
    assert not dest.exists()  # so update_snapshot's 'non-empty means complete' never sees a half tree
    assert list(home.iterdir()) == []  # no staging folder or temporary tar is left behind

    # A crash (no cleanup at all) leaves a staging folder: the next build removes it and builds the full tree.
    stale = home / f".{dest.name}.partial-99999"
    stale.mkdir()
    (stale / "half.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(node_snapshot, "_extract_tar", real_extract)
    assert node_snapshot.build_snapshot("c" * 40, dest, runner=_fake_git([])) == dest
    assert (dest / "README.md").is_file() and not stale.exists()
    assert sorted(p.name for p in home.iterdir()) == [dest.name]


def test_node_snapshot_runs_nothing_at_import_time(tmp_path):
    """Its top level holds only imports, constants, functions, classes and the sys.path guard; importing it in a
    fresh interpreter creates no file under the node home or the run dir."""
    tree = ast.parse((ROOT / "scripts" / "desktop" / "node_snapshot.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant), "only the docstring may be a bare expression"
        elif isinstance(node, ast.If):
            assert ast.unparse(node.test) == "str(ROOT) not in sys.path"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            calls = [n for n in ast.walk(value) if isinstance(n, ast.Call)] if value is not None else []
            assert all(ast.unparse(c.func) in ("Path(__file__).resolve", "Path") for c in calls), ast.unparse(node)
        else:
            assert isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)), ast.dump(node)
    home, run = tmp_path / "home", tmp_path / "run"
    env = {**os.environ, "JARVIS_NODE_HOME": str(home), "JARVIS_RUN_DIR": str(run), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", "import scripts.desktop.node_snapshot as m; print(m.ROOT.name)"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    assert not home.exists() and not run.exists()


# =============================================================================================================
# the laptop's own JARVIS_BRAIN follows the switch (FLIP_ENV, restore, resume), so every laptop reader agrees
# =============================================================================================================


def test_set_laptop_brain_edits_only_that_line_in_place(ctl):
    """CRLF endings, a BOM, comments, a commented-out line and a look-alike key are kept byte for byte; every
    JARVIS_BRAIN line (dotenv reads the last) is replaced."""
    original = ("\ufeffLOG_LEVEL=INFO\r\n# JARVIS_BRAIN=vps (a comment, kept)\r\nJARVIS_BRAIN=pc\r\n"
                "JARVIS_BRAIN_NOTE=keep\r\nexport JARVIS_BRAIN = pc\r\nLAST=1")
    ctl.env.write_bytes(original.encode("utf-8"))

    agent_ctl.set_laptop_brain("vps")

    expected = original.replace("\r\nJARVIS_BRAIN=pc\r\n", "\r\nJARVIS_BRAIN=vps\r\n").replace(
        "export JARVIS_BRAIN = pc", "JARVIS_BRAIN=vps")
    assert ctl.env.read_bytes() == expected.encode("utf-8")
    assert agent_ctl.current_brain() == "vps"
    assert [p.name for p in ctl.tmp.iterdir() if p.name.endswith(".tmp")] == []  # the temp file was moved in

    agent_ctl.set_laptop_brain("pc")
    assert agent_ctl.current_brain() == "pc"
    assert ctl.env.read_text(encoding="utf-8-sig").count("JARVIS_BRAIN=pc") == 2


def test_set_laptop_brain_appends_the_line_and_follows_a_process_env_value(ctl, monkeypatch):
    ctl.env.write_text("LOG_LEVEL=INFO", encoding="utf-8")  # no trailing newline, no JARVIS_BRAIN yet
    agent_ctl.set_laptop_brain("vps")
    assert ctl.env.read_text(encoding="utf-8") == "LOG_LEVEL=INFO\nJARVIS_BRAIN=vps\n"

    # A JARVIS_BRAIN already in this process's environment wins over the .env, so it is updated too.
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    agent_ctl.set_laptop_brain("pc")
    assert os.environ["JARVIS_BRAIN"] == "pc" and agent_ctl.current_brain() == "pc"

    missing = ctl.tmp / "fresh" / ".env"  # no root .env yet: it is created with just that line
    agent_ctl.set_laptop_brain("vps", missing)
    assert missing.read_text(encoding="utf-8") == "JARVIS_BRAIN=vps\n"


def test_a_successful_pc_to_vps_switch_makes_the_laptop_vps_and_refuses_a_worker_start(ctl, monkeypatch, capsys):
    _pin_snapshot(ctl)
    ctl.env.write_text("LOG_LEVEL=INFO\nJARVIS_ROOM_NAME=owner-room\n", encoding="utf-8")
    assert agent_ctl.current_brain() == "pc"
    live = LiveStatus("pc")
    code, details = _make_switch(ctl, "vps", ssh=FakeVps(brain="pc"), status=live, laptop=FakeLaptop(live)).run()
    assert code == 0 and details["result"] == "switched"

    assert agent_ctl.current_brain() == "vps"
    assert ctl.env.read_text(encoding="utf-8") == "LOG_LEVEL=INFO\nJARVIS_ROOM_NAME=owner-room\nJARVIS_BRAIN=vps\n"
    monkeypatch.setattr(agent_ctl, "start_worker", lambda *a, **k: pytest.fail("no PC worker in vps mode"))
    capsys.readouterr()
    assert agent_ctl.main(["start", "--by", "user", "--reason", "after brain vps"]) == agent_ctl.EXIT_REFUSED
    assert "JARVIS_BRAIN=vps" in capsys.readouterr().err
    assert audit_lines(ctl.logs)[-1]["result"] == "refused"


def test_a_failed_switch_writes_pc_back_on_the_laptop(ctl):
    """VERIFY never sees the vps agent: the restore puts the laptop's JARVIS_BRAIN back to pc after FLIP_ENV had
    written vps, so the owner's worker is not refused afterwards."""
    _pin_snapshot(ctl)
    ctl.env.write_text("JARVIS_BRAIN=pc\nLOG_LEVEL=INFO\n", encoding="utf-8")
    writes: list = []

    def laptop_env(brain):
        writes.append(brain)
        agent_ctl.set_laptop_brain(brain)

    code, details = _make_switch(ctl, "vps", ssh=FakeVps(brain="pc"), status=lambda: _running_status("pc"),
                                 laptop=FakeLaptop(), laptop_env=laptop_env).run()

    assert code == agent_ctl.EXIT_FAILED and details["restored"] is True and details["brain"] == "pc"
    assert writes == ["vps", "pc"]                   # FLIP_ENV wrote vps, the restore wrote pc back
    assert agent_ctl.current_brain() == "pc"
    assert ctl.env.read_text(encoding="utf-8") == "JARVIS_BRAIN=pc\nLOG_LEVEL=INFO\n"


def test_a_vps_to_pc_switch_sets_the_laptop_to_pc(ctl):
    ctl.run.mkdir(parents=True)
    ctl.env.write_text("JARVIS_BRAIN=vps\n", encoding="utf-8")
    assert agent_ctl.current_brain() == "vps"
    live = LiveStatus("vps")
    code, _ = _make_switch(ctl, "pc", ssh=FakeVps(brain="vps"), status=live, laptop=FakeLaptop(live)).run()
    assert code == 0 and agent_ctl.current_brain() == "pc"
    assert ctl.env.read_text(encoding="utf-8") == "JARVIS_BRAIN=pc\n"


def test_a_laptop_env_write_failure_at_flip_env_restores_both_sides(ctl):
    _pin_snapshot(ctl)
    writes: list = []

    def laptop_env(brain):
        writes.append(brain)
        if brain == "vps":
            raise agent_ctl.BrainSwitchError("could not write JARVIS_BRAIN to the laptop .env (PermissionError)")

    vps = FakeVps(brain="pc")
    laptop = FakeLaptop()
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: _running_status("pc"), laptop=laptop,
                                 laptop_env=laptop_env).run()
    assert code == agent_ctl.EXIT_FAILED and details["error"].startswith("failed at FLIP_ENV")
    assert writes == ["vps", "pc"] and "set_env.py JARVIS_BRAIN pc" in vps.joined()
    assert ("start", "vps") not in laptop.calls and ("start", "pc") in laptop.calls


def test_a_resume_past_flip_env_reapplies_the_laptop_brain(ctl):
    """The journal says FLIP_ENV is done but the laptop .env says pc (edited by hand, or the crash came between the
    two writes): the resume writes vps again before START_NEW."""
    _pin_snapshot(ctl)
    ctl.env.write_text("JARVIS_BRAIN=pc\n", encoding="utf-8")
    (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).write_text(json.dumps(
        {"target": "vps", "previous": "pc", "state": "FLIP_ENV", "vps_services": ["agent", "agent-svc"]}),
        encoding="utf-8")
    live = LiveStatus("pc")
    laptop = FakeLaptop(live)
    vps = FakeVps(brain="pc")
    code, details = _make_switch(ctl, "vps", ssh=vps, status=live, laptop=laptop).run()

    assert code == 0 and details["result"] == "switched"
    assert agent_ctl.current_brain() == "vps"
    assert laptop.calls == [("start", "vps"), ("ensure", "vps")]   # only START_NEW, ENSURE, VERIFY ran
    assert "set_env.py" not in vps.joined() and "backup.sh" not in vps.joined()


def test_a_resumed_restore_writes_the_previous_brain_on_the_laptop(ctl):
    _pin_snapshot(ctl)
    ctl.env.write_text("JARVIS_BRAIN=vps\n", encoding="utf-8")
    (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).write_text(json.dumps(
        {"target": "vps", "previous": "pc", "state": "START_NEW", "restoring": True}), encoding="utf-8")
    code, details = _make_switch(ctl, "vps", ssh=FakeVps(brain="vps"), status=lambda: _running_status("pc"),
                                 laptop=FakeLaptop()).run()
    assert code == agent_ctl.EXIT_FAILED and details["restored"] is True
    assert agent_ctl.current_brain() == "pc"


def test_the_real_cli_switch_writes_the_laptop_env_then_node_status_and_update_follow_it(ctl, monkeypatch, capsys):
    """End to end through 'agent_ctl brain vps' with the real LaptopBrain and the real laptop .env writer: the
    laptop then counts as vps, so 'node status' says vps and a 'node update' with the node down starts it and
    leaves its logon task enabled (in pc mode it would disable it)."""
    _pin_snapshot(ctl)
    live = LiveStatus("pc")
    vps = FakeVps(brain="pc")
    state = _real_laptop(ctl, monkeypatch, node_up=False, live=live, vps=vps)
    monkeypatch.setattr(agent_ctl.agent_lock, "is_locked", lambda: False)

    def schtasks(*args, check=False):
        state["events"].append(("schtasks", *args))
        if args[:1] == ("/Run",):
            state["node_up"] = True
            live.brain = "vps"  # the VPS agent joins once the node and the containers are up
        return subprocess.CompletedProcess(["schtasks", *args], 0, "", "")

    monkeypatch.setattr(agent_ctl, "_schtasks", schtasks)
    assert agent_ctl.main(["brain", "vps", "--now", "--by", "claude", "--reason", "C-123"]) == 0
    assert agent_ctl.current_brain() == "vps" and "JARVIS_BRAIN=vps" in ctl.env.read_text(encoding="utf-8")

    capsys.readouterr()
    monkeypatch.setattr(agent_ctl, "probe_node_health", lambda timeout=2.0: None)
    monkeypatch.setattr(agent_ctl, "node_task_state", lambda: "enabled")
    agent_ctl.main(["node", "status"])
    assert "brain     vps" in capsys.readouterr().out

    calls = _mock_update(ctl, monkeypatch, running=False)
    assert agent_ctl.main(["node", "update"]) == 0
    assert calls == [("enabled", True), ("run",)] and ("enabled", False) not in calls


# =============================================================================================================
# node update with a failed schtasks /Create; PREFLIGHT needs the logon task; Ctrl+C during a switch
# =============================================================================================================


@pytest.mark.parametrize("running", [True, False])
def test_node_update_with_a_failed_task_create_leaves_the_node_alone_and_fails(ctl, monkeypatch, capsys, running):
    """vps mode, schtasks /Create returns 1: a running node is not stopped (it stays on its old snapshot), a
    stopped one is not started, and the exit code says failure."""
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    calls = _mock_update(ctl, monkeypatch, running=running)
    seen: list = []

    def schtasks(*args, check=False):
        seen.append(args[0])
        return subprocess.CompletedProcess(["schtasks", *args], 1 if args[0] == "/Create" else 0, "", "denied")

    monkeypatch.setattr(agent_ctl, "create_node_task", REAL_CREATE_NODE_TASK)
    monkeypatch.setattr(agent_ctl, "_schtasks", schtasks)
    monkeypatch.setattr(agent_ctl, "node_python_w", lambda: Path("C:/py/pythonw.exe"))

    assert agent_ctl.main(["node", "update", "--by", "claude", "--reason", "C-123"]) == agent_ctl.EXIT_FAILED
    assert seen == ["/Create"]
    assert calls == []                      # no POST /stop, no enable/disable, no /Run: the node is untouched
    err = capsys.readouterr().err
    assert "could not be (re)created" in err
    assert ("left on its old snapshot" in err) is running
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["task"], entry["restarted"]) == ("failed", False, False)


REAL_CREATE_NODE_TASK = agent_ctl.create_node_task


def test_preflight_refuses_brain_vps_without_the_node_logon_task(ctl, monkeypatch):
    _pin_snapshot(ctl)
    monkeypatch.setattr(agent_ctl, "node_task_exists", lambda: False)
    laptop = FakeLaptop()
    vps = FakeVps(brain="pc")
    spoken: list = []
    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: _running_status("pc"), laptop=laptop,
                                 notify=spoken.append, now_force=True).run()
    assert code == agent_ctl.EXIT_REFUSED and details["state"] == "PREFLIGHT"
    assert "'JARVIS Desktop Node' logon task is missing" in details["error"]
    assert laptop.calls == [] and _changing_calls(vps) == [] and spoken == []
    assert agent_ctl.current_brain() == "pc" and not ctl.env.exists()


def test_ctrl_c_after_stop_old_is_audited_and_says_how_to_finish(ctl, monkeypatch, capsys):
    _pin_snapshot(ctl)
    live = LiveStatus("pc")
    laptop = FakeLaptop(live)
    fake = FakeVps(brain="pc")

    def ssh(cmd):
        joined = " ".join(cmd)
        # SYNC_DATA itself (PREFLIGHT's provisioning check names the script too, so it is let through).
        if "sync_brain_data.sh" in joined and "config --services" not in joined:
            raise KeyboardInterrupt
        return fake(cmd)

    monkeypatch.setattr(agent_ctl, "_default_ssh", ssh)
    monkeypatch.setattr(agent_ctl, "vortex_status", lambda base=None: live())
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: laptop)
    monkeypatch.setattr(agent_ctl, "_speak_notice", lambda _m: None)
    monkeypatch.setattr(agent_ctl, "_sleep", lambda _s: None)

    code = agent_ctl.main(["brain", "vps", "--now", "--by", "claude", "--reason", "C-123"])

    assert code == agent_ctl.EXIT_INTERRUPTED == 130
    err = capsys.readouterr().err
    assert "INTERRUPTED after STOP_OLD" in err and "half-done" in err and "'agent_ctl brain vps'" in err
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["state"], entry["half_done"], entry["exit"]) == (
        "brain", "interrupted", "STOP_OLD", True, 130)
    journal = json.loads((ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "STOP_OLD"    # kept for the resume
    assert laptop.calls == [("stop", "pc")]  # no restore ran in the interrupted process


def test_ctrl_c_during_the_notice_changes_nothing(ctl, monkeypatch, capsys):
    _pin_snapshot(ctl)
    laptop = FakeLaptop()
    monkeypatch.setattr(agent_ctl, "_default_ssh", FakeVps(brain="pc"))
    monkeypatch.setattr(agent_ctl, "vortex_status", lambda base=None: _running_status("pc"))
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: laptop)
    monkeypatch.setattr(agent_ctl, "_speak_notice", lambda _m: None)

    def interrupted(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(agent_ctl, "_sleep", interrupted)  # Ctrl+C during the 60 s notice

    assert agent_ctl.main(["brain", "vps", "--now"]) == 130
    assert "before anything was stopped or changed; 'pc' is still the brain" in capsys.readouterr().err
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["half_done"]) == ("interrupted", False)
    assert laptop.calls == []


# =============================================================================================================
# 'brain <target>' compares BOTH sides: a laptop .env that disagrees with the VPS is repaired alone
# =============================================================================================================


def _vps_reads(vps: FakeVps) -> list:
    """The ssh commands that only read (the provisioning check and 'set_env.py --get')."""
    return [c for c in vps.calls if c not in _changing_calls(vps)]


def test_brain_pc_repairs_a_laptop_left_at_vps_when_the_vps_already_says_pc(ctl, monkeypatch, capsys):
    """The laptop .env says vps but the VPS says pc: the owner's worker is refused, the refusal names the exact
    repair command, and that command rewrites only the laptop's JARVIS_BRAIN (no VPS change, nothing stopped or
    started), after which the worker start goes through."""
    ctl.run.mkdir(parents=True)
    ctl.env.write_text("LOG_LEVEL=INFO\nJARVIS_BRAIN=vps\n", encoding="utf-8")
    vps = FakeVps(brain="pc")
    laptop = FakeLaptop()
    monkeypatch.setattr(agent_ctl, "_default_ssh", vps)
    monkeypatch.setattr(agent_ctl, "vortex_status", lambda base=None: pytest.fail("a repair reads no status"))
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: laptop)
    monkeypatch.setattr(agent_ctl, "_speak_notice", lambda _m: pytest.fail("a repair is not announced"))
    started: list = []
    monkeypatch.setattr(agent_ctl, "start_worker", lambda args, mode, **k: started.append(mode) or
                        (0, {"result": "started", "new_pid": 1, "start_s": 1.0, "room_result": "OK"}))

    assert agent_ctl.main(["start", "--by", "claude", "--reason", "C-123"]) == agent_ctl.EXIT_REFUSED
    err = capsys.readouterr().err
    command = ".venv_312/Scripts/python.exe scripts/agent_ctl.py brain pc --by claude"
    assert command in err and started == []

    assert agent_ctl.main(command.split()[2:]) == 0   # exactly the command the refusal named

    out = capsys.readouterr().out
    assert "already 'pc' but this laptop says 'vps'" in out and "is now 'pc'" in out
    assert ctl.env.read_text(encoding="utf-8") == "LOG_LEVEL=INFO\nJARVIS_BRAIN=pc\n"
    assert agent_ctl.current_brain() == "pc"
    assert _changing_calls(vps) == [] and len(_vps_reads(vps)) == 1   # only 'set_env.py --get'
    assert laptop.calls == []                                         # nothing stopped or started
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["result"], entry["brain"], entry["laptop_was"]) == ("brain", "repaired", "pc",
                                                                                       "vps")
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()
    assert not (ctl.run / agent_ctl.BRAIN_LOCK_FILE).exists()

    assert agent_ctl.main(["start", "--by", "claude", "--reason", "C-123"]) == 0   # no longer refused
    assert started == ["start"]


def test_brain_vps_repairs_a_laptop_left_at_pc_when_the_vps_already_says_vps(ctl):
    ctl.run.mkdir(parents=True)
    ctl.env.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")  # no JARVIS_BRAIN: the laptop counts as pc
    vps = FakeVps(brain="vps")
    laptop = FakeLaptop()
    journals: list = []

    def laptop_env(brain):
        journals.append(json.loads((ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).read_text(encoding="utf-8")))
        agent_ctl.set_laptop_brain(brain)

    code, details = _make_switch(ctl, "vps", ssh=vps, status=lambda: pytest.fail("no status read"), laptop=laptop,
                                 laptop_env=laptop_env).run()

    assert code == 0 and details == {"result": "repaired", "repaired": "laptop", "brain": "vps", "laptop_was": "pc"}
    assert ctl.env.read_text(encoding="utf-8") == "LOG_LEVEL=INFO\nJARVIS_BRAIN=vps\n"
    # Journalled while the laptop line was written, cleared afterwards.
    assert journals and (journals[0]["repair"], journals[0]["laptop_was"], journals[0]["target"]) == ("laptop", "pc",
                                                                                                      "vps")
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()
    assert _changing_calls(vps) == [] and laptop.calls == []


@pytest.mark.parametrize("brain", ["pc", "vps"])
def test_already_needs_both_sides_on_the_target(ctl, brain):
    ctl.run.mkdir(parents=True)
    ctl.env.write_text(f"JARVIS_BRAIN={brain}\n", encoding="utf-8")
    writes: list = []
    vps = FakeVps(brain=brain)
    code, details = _make_switch(ctl, brain, ssh=vps, status=lambda: _running_status(brain), laptop=FakeLaptop(),
                                 laptop_env=writes.append).run()
    assert code == 0 and details == {"result": "already", "brain": brain}
    assert writes == [] and _changing_calls(vps) == []
    assert ctl.env.read_text(encoding="utf-8") == f"JARVIS_BRAIN={brain}\n"


def test_a_failed_laptop_repair_changes_nothing_else(ctl):
    ctl.run.mkdir(parents=True)
    ctl.env.write_text("JARVIS_BRAIN=vps\n", encoding="utf-8")

    def broken(_brain):
        raise agent_ctl.BrainSwitchError("could not write JARVIS_BRAIN to the laptop .env (PermissionError)")

    vps = FakeVps(brain="pc")
    laptop = FakeLaptop()
    code, details = _make_switch(ctl, "pc", ssh=vps, status=lambda: None, laptop=laptop, laptop_env=broken).run()
    assert code == agent_ctl.EXIT_FAILED
    assert (details["state"], details["changed"], details["laptop_was"]) == ("REPAIR_LAPTOP", False, "vps")
    assert "PermissionError" in details["error"]
    assert _changing_calls(vps) == [] and laptop.calls == [] and agent_ctl.current_brain() == "vps"
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


def test_the_refusal_names_a_shell_environment_value_too(ctl, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    monkeypatch.setattr(agent_ctl, "start_worker", lambda *a, **k: pytest.fail("no worker in vps mode"))
    assert agent_ctl.main(["start", "--by", "user"]) == agent_ctl.EXIT_REFUSED
    err = capsys.readouterr().err
    assert ".venv_312/Scripts/python.exe scripts/agent_ctl.py brain pc --by user" in err
    assert "also set in this shell's environment" in err


# =============================================================================================================
# a failed vps -> pc switch restores the VPS brain: its containers up AND the Desktop Node started again
# =============================================================================================================


class ContainerVps(FakeVps):
    """FakeVps whose brain containers are up until a compose 'stop' and again after a compose 'up -d'."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.up = True

    def __call__(self, cmd):
        joined = " ".join(cmd)
        if "--profile vps-brain up -d" in joined:
            self.up = True
        elif "--profile vps-brain stop" in joined:
            self.up = False
        return super().__call__(cmd)


class NodeLaptop(FakeLaptop):
    """A laptop whose Desktop Node runs until stopped; the PC worker fails to start when fail_pc is set."""

    def __init__(self, *, fail_pc: bool) -> None:
        super().__init__()
        self.node_up, self.fail_pc = True, fail_pc

    def start(self, brain: str) -> int:
        self.calls.append(("start", brain))
        if brain == "vps":
            self.node_up = True
            return agent_ctl.EXIT_OK
        return agent_ctl.EXIT_FAILED if self.fail_pc else agent_ctl.EXIT_OK

    def stop(self, brain: str) -> int:
        self.calls.append(("stop", brain))
        if brain == "vps":
            self.node_up = False
        return agent_ctl.EXIT_OK


def _vps_brain_status(vps: ContainerVps, laptop: NodeLaptop):
    """/vortex/status: the vps brain's agent runs only while its containers are up AND the Desktop Node runs (the
    PC worker's agent never shows, so a switch to pc that gets past START_NEW fails at VERIFY)."""
    def status():
        running = vps.up and laptop.node_up
        return {"brain": "vps", "agent": {"lk.agent.name": "jarvis",
                                          "jarvis.session": "running" if running else "stopped"}}
    return status


@pytest.mark.parametrize("fail_pc, failed_at", [(True, "START_NEW"), (False, "VERIFY")])
def test_a_failed_vps_to_pc_switch_brings_the_vps_containers_and_the_node_back(ctl, fail_pc, failed_at):
    """Removing either restore action ('start the VPS brain' = compose up, or 'start the Desktop Node') leaves the
    vps agent down, so 'restored' turns False and the ordered calls below no longer match."""
    ctl.run.mkdir(parents=True)
    ctl.env.write_text("JARVIS_BRAIN=vps\n", encoding="utf-8")
    vps = ContainerVps(brain="vps")
    laptop = NodeLaptop(fail_pc=fail_pc)
    code, details = _make_switch(ctl, "pc", ssh=vps, status=_vps_brain_status(vps, laptop), laptop=laptop).run()

    assert code == agent_ctl.EXIT_FAILED and details["error"].startswith(f"failed at {failed_at}")
    assert (details["result"], details["restored"], details["brain"]) == ("failed", True, "vps")
    assert vps.up and laptop.node_up                                  # both halves of the vps brain run again
    steps = [" ".join(c) for c in _changing_calls(vps)]
    flip_back = steps.index("bash -lc cd /opt/jarvis-agent && python3 deploy/hostinger/set_env.py JARVIS_BRAIN vps")
    assert steps[flip_back + 1:] == [
        "bash -lc cd /opt/jarvis-agent/deploy/hostinger && docker compose -p jarvis --profile vps-brain up -d "
        "agent agent-svc"]                                            # the restore's compose up, after the flip back
    assert laptop.calls[-2:] == [("stop", "pc"), ("start", "vps")]   # the worker down, then the node started again
    assert agent_ctl.current_brain() == "vps"                         # the laptop .env is vps again
    assert not (ctl.run / agent_ctl.BRAIN_JOURNAL_FILE).exists()


def test_real_laptop_failed_vps_to_pc_switch_runs_the_node_task_and_compose_up_again(ctl, monkeypatch):
    """Through 'agent_ctl brain pc' with the real LaptopBrain: the PC worker does not come up, so the restore puts
    JARVIS_BRAIN back to vps, brings the VPS containers up and starts the Desktop Node through its logon task."""
    _pin_snapshot(ctl)
    ctl.env.write_text("JARVIS_BRAIN=vps\n", encoding="utf-8")
    live = LiveStatus("vps")
    vps = FakeVps(brain="vps")
    state = _real_laptop(ctl, monkeypatch, node_up=True, live=live, vps=vps)
    monkeypatch.setattr(agent_ctl.agent_lock, "is_locked", lambda: False)

    def failing_worker(args, mode, **_kwargs):
        state["events"].append(("start_worker", mode, "failed"))
        return agent_ctl.EXIT_FAILED, {"result": "failed", "error": "the worker exited with code 1"}

    monkeypatch.setattr(agent_ctl, "start_worker", failing_worker)

    assert agent_ctl.main(["brain", "pc", "--now", "--by", "claude", "--reason", "C-123"]) == agent_ctl.EXIT_FAILED

    events = state["events"]
    failed = events.index(("start_worker", "start", "failed"))
    assert events[0] == ("node_stop_request", agent_ctl.NODE_STOP_TIMEOUT_S)   # STOP_OLD stopped the node
    restore = events[failed + 1:]
    assert ("schtasks", "/Change", "/TN", agent_ctl.NODE_TASK_NAME, "/Enable") in restore
    assert restore[-1] == ("schtasks", "/Run", "/TN", agent_ctl.NODE_TASK_NAME)  # the node started again
    assert state["node_up"] is True
    joined = vps.joined()
    back = joined.index("set_env.py JARVIS_BRAIN vps")
    assert joined.index("set_env.py JARVIS_BRAIN pc") < back
    assert "--profile vps-brain up -d agent agent-svc" in joined[back:]      # the VPS brain started again
    assert agent_ctl.current_brain() == "vps"
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["restored"], entry["brain"]) == ("failed", True, "vps")
    assert entry["error"].startswith("failed at START_NEW")


# =============================================================================================================
# run/brain.lock: two 'agent_ctl brain' runs never drive a switch at the same time
# =============================================================================================================


def test_a_second_brain_run_is_refused_while_one_runs(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    seen: dict = {}

    class SlowSwitch:
        """Stands in for a switch in progress: while it 'runs', a second 'brain' run and a worker start are made."""

        def __init__(self, target, **_kwargs) -> None:
            self.target = target
            self.journal_path = ctl.run / agent_ctl.BRAIN_JOURNAL_FILE

        def run(self):
            lock = json.loads((ctl.run / agent_ctl.BRAIN_LOCK_FILE).read_text(encoding="utf-8"))
            seen["lock"] = (lock["pid"], lock["by"])
            seen["second"] = agent_ctl.main(["brain", "vps", "--now", "--by", "cursor"])
            seen["start"] = agent_ctl.main(["start", "--by", "user"])
            seen["recovery"] = agent_ctl._brain_switch_worker_refused(
                types.SimpleNamespace(by="worker"), "restart")
            return agent_ctl.EXIT_OK, {"result": "switched", "brain": self.target}

    monkeypatch.setattr(agent_ctl, "BrainSwitch", SlowSwitch)
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: FakeLaptop())
    monkeypatch.setattr(agent_ctl, "start_worker", lambda *a, **k: pytest.fail("no worker start during a switch"))

    assert agent_ctl.main(["brain", "pc", "--now", "--by", "claude", "--reason", "C-123"]) == 0

    assert seen["lock"] == (os.getpid(), "claude")
    assert seen["second"] == agent_ctl.EXIT_REFUSED and seen["start"] == agent_ctl.EXIT_REFUSED
    assert seen["recovery"] is None    # the worker's own link recovery is let through
    err = capsys.readouterr().err
    assert "Brain switch refused: run/brain.lock is held by a brain switch by claude" in err
    assert "Start refused: a brain switch is running" in err
    refused = [e for e in audit_lines(ctl.logs) if e["action"] == "brain" and e["result"] == "refused"]
    assert len(refused) == 1 and refused[0]["by"] == "cursor" and refused[0]["exit"] == 4
    assert not (ctl.run / agent_ctl.BRAIN_LOCK_FILE).exists()   # released after the switch


def test_a_held_brain_lock_refuses_before_any_vps_call(ctl, monkeypatch, capsys):
    ctl.run.mkdir(parents=True)
    lock = ctl.run / agent_ctl.BRAIN_LOCK_FILE
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": time.time() - 5, "by": "user"}), encoding="utf-8")
    vps = FakeVps(brain="pc")
    monkeypatch.setattr(agent_ctl, "_default_ssh", vps)
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: pytest.fail("no laptop step"))

    assert agent_ctl.main(["brain", "vps", "--now"]) == agent_ctl.EXIT_REFUSED
    assert vps.calls == []
    assert f"run/brain.lock is held by a brain switch by user (pid {os.getpid()})" in capsys.readouterr().err
    assert json.loads(lock.read_text(encoding="utf-8"))["by"] == "user"   # left alone


def _exited_pid() -> int:
    """The pid of a child that has already exited by itself (only waited for, never ended by the test)."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])  # pylint: disable=consider-using-with
    child.wait(timeout=60)
    return child.pid


@pytest.mark.parametrize("stale", ["old", "exited"])
def test_a_stale_brain_lock_is_replaced(ctl, monkeypatch, capsys, stale):
    ctl.run.mkdir(parents=True)
    lock = ctl.run / agent_ctl.BRAIN_LOCK_FILE
    if stale == "old":
        payload = {"pid": os.getpid(), "ts": time.time() - agent_ctl.BRAIN_LOCK_STALE_S - 60, "by": "cursor"}
    else:
        payload = {"pid": _exited_pid(), "ts": time.time() - 5, "by": "cursor"}
    lock.write_text(json.dumps(payload), encoding="utf-8")
    vps = FakeVps(brain="pc")
    monkeypatch.setattr(agent_ctl, "_default_ssh", vps)
    monkeypatch.setattr(agent_ctl, "LaptopBrain", lambda args: FakeLaptop())

    assert agent_ctl.main(["brain", "pc", "--by", "claude"]) == 0   # the VPS and this laptop already say pc
    out = capsys.readouterr().out
    assert "Removing a stale run/brain.lock (a brain switch by cursor" in out and "Nothing to switch" in out
    assert not lock.exists()
    assert audit_lines(ctl.logs)[-1]["result"] == "already"
