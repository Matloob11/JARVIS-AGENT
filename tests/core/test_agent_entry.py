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

    class FakeWorkerOptions:
        def __init__(self, entrypoint_fnc):
            self.entrypoint_fnc = entrypoint_fnc

    fake_agents = types.SimpleNamespace(
        WorkerOptions=FakeWorkerOptions,
        cli=types.SimpleNamespace(run_app=mock_run),
    )
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.agents = fake_agents
    fake_agent_runner = types.ModuleType("src.core.agent_runner")
    fake_agent_runner.entrypoint = fake_entrypoint

    modules = {
        "livekit": fake_livekit,
        "livekit.agents": fake_agents,
        "src.core.agent_runner": fake_agent_runner,
    }
    with patch.dict(sys.modules, modules):
        with patch("sys.stdout", new=io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
            runpy.run_module("src.core.agent", run_name="__main__")

    mock_run.assert_called_once()
    opts = mock_run.call_args.args[0]
    assert opts.entrypoint_fnc == fake_entrypoint


def test_agent_env_vars():
    # Ensure GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK is set
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.agents = types.SimpleNamespace()
    fake_agent_runner = types.ModuleType("src.core.agent_runner")
    fake_agent_runner.entrypoint = MagicMock(name="entrypoint")

    modules = {
        "livekit": fake_livekit,
        "livekit.agents": fake_livekit.agents,
        "src.core.agent_runner": fake_agent_runner,
    }
    sys.modules.pop("src.core.agent", None)
    with patch.dict(sys.modules, modules):
        importlib.import_module("src.core.agent")

    assert os.environ.get("GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK") == "1"
