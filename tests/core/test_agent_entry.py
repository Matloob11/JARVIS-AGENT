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
