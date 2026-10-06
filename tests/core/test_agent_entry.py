import sys
import os
import io
import importlib
import types
from unittest.mock import MagicMock, patch


def test_agent_main_logic():
    # We want to test the block inside if __name__ == "__main__":
    # We use runpy to run the module as __main__
    import runpy

    mock_run = MagicMock()
    fake_entrypoint = MagicMock(name="entrypoint")
    fake_options = MagicMock(name="worker_options")
    # C-08: agent_runner's shared builder; its 127.0.0.1:8081 defaults are checked in
    # tests/unit/test_agent_lock.py against the installed livekit.
    fake_builder = MagicMock(name="build_worker_options", return_value=fake_options)

    fake_agents = types.SimpleNamespace(
        cli=types.SimpleNamespace(run_app=mock_run),
    )
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.agents = fake_agents
    fake_agent_runner = types.ModuleType("src.core.agent_runner")
    fake_agent_runner.entrypoint = fake_entrypoint
    fake_agent_runner.build_worker_options = fake_builder

    modules = {
        "livekit": fake_livekit,
        "livekit.agents": fake_agents,
        "src.core.agent_runner": fake_agent_runner,
    }
    with patch.dict(sys.modules, modules):
        with patch("sys.stdout", new=io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
            runpy.run_module("src.core.agent", run_name="__main__")

    fake_builder.assert_called_once_with(fake_entrypoint)
    mock_run.assert_called_once_with(fake_options)


def test_agent_env_vars():
    # Ensure GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK is set
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.agents = types.SimpleNamespace()
    fake_agent_runner = types.ModuleType("src.core.agent_runner")
    fake_agent_runner.entrypoint = MagicMock(name="entrypoint")
    fake_agent_runner.build_worker_options = MagicMock(name="build_worker_options")

    modules = {
        "livekit": fake_livekit,
        "livekit.agents": fake_livekit.agents,
        "src.core.agent_runner": fake_agent_runner,
    }
    sys.modules.pop("src.core.agent", None)
    with patch.dict(sys.modules, modules):
        importlib.import_module("src.core.agent")

    assert os.environ.get("GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK") == "1"


# ------------------------------------------------------------------ C-32: prewarm + worker options
# (design .claude/wip/jarvisruntime/design.md §1.1): build_worker_options wires the prewarm hook and
# the K-69 env knobs; the K-44 PC guard (load_fnc = pc_worker_load, always 0.0) must survive.


def _clear_worker_env(monkeypatch):
    for name in ("JARVIS_WORKER_HOST", "JARVIS_WORKER_PORT", "JARVIS_AGENT_NAME", "JARVIS_ROLE",
                 "JARVIS_BRAIN", "JARVIS_DESKTOP_MODE", "JARVIS_PREWARM_TIMEOUT", "JARVIS_IDLE_PROCS",
                 "JARVIS_LOAD_THRESHOLD", "JARVIS_SHUTDOWN_PROC_TIMEOUT", "JARVIS_JOB_MEMORY_WARN_MB"):
        monkeypatch.delenv(name, raising=False)


def _captured_worker_options(monkeypatch):
    """agent_runner with agents.WorkerOptions replaced by a kwargs capture (FakeWorkerOptions)."""
    from src.core import agent_runner as ar

    captured = {}

    class FakeWorkerOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ar.agents, "WorkerOptions", FakeWorkerOptions)
    monkeypatch.setattr(ar, "in_container", lambda: False)
    return ar, captured


def test_build_worker_options_sets_prewarm_and_its_env_defaults(monkeypatch):
    ar, captured = _captured_worker_options(monkeypatch)
    _clear_worker_env(monkeypatch)
    ar.build_worker_options()
    assert captured["prewarm_fnc"] is ar.prewarm
    assert captured["initialize_process_timeout"] == 120.0
    assert captured["num_idle_processes"] == 1
    assert captured["load_threshold"] == float("inf")
    # K-44: the PC worker's load guard stays byte for byte — always 0.0, never 'full'
    assert captured["load_fnc"] is ar.pc_worker_load
    assert ar.pc_worker_load() == 0.0
    # the remote-mode extras never reach the PC options
    assert "shutdown_process_timeout" not in captured and "job_memory_warn_mb" not in captured


def test_build_worker_options_reads_the_env_knobs(monkeypatch):
    ar, captured = _captured_worker_options(monkeypatch)
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("JARVIS_PREWARM_TIMEOUT", "60")
    monkeypatch.setenv("JARVIS_IDLE_PROCS", "2")
    monkeypatch.setenv("JARVIS_LOAD_THRESHOLD", "0.7")
    ar.build_worker_options()
    assert captured["initialize_process_timeout"] == 60.0
    assert captured["num_idle_processes"] == 2
    assert captured["load_threshold"] == 0.7


def test_a_malformed_env_knob_keeps_its_default(monkeypatch):
    ar, captured = _captured_worker_options(monkeypatch)
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("JARVIS_PREWARM_TIMEOUT", "two minutes")
    monkeypatch.setenv("JARVIS_IDLE_PROCS", "many")
    monkeypatch.setenv("JARVIS_LOAD_THRESHOLD", " ")
    ar.build_worker_options()
    assert captured["initialize_process_timeout"] == 120.0
    assert captured["num_idle_processes"] == 1
    assert captured["load_threshold"] == float("inf")


def test_remote_mode_gets_the_vps_extras_and_no_pc_load_fnc(monkeypatch):
    ar, captured = _captured_worker_options(monkeypatch)
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    from services.utils.jarvis_config import config
    monkeypatch.setattr(config, "validate", lambda *_a, **_k: True)
    ar.build_worker_options()
    assert "load_fnc" not in captured  # remote keeps the library default (K-44)
    assert captured["prewarm_fnc"] is ar.prewarm
    assert captured["shutdown_process_timeout"] == 20.0
    assert "job_memory_warn_mb" not in captured  # only when the compose sets it (K-75)
    captured.clear()
    monkeypatch.setenv("JARVIS_JOB_MEMORY_WARN_MB", "3000")
    ar.build_worker_options()
    assert captured["job_memory_warn_mb"] == 3000
