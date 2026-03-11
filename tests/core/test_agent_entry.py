import sys
import os
import io
import pytest
from unittest.mock import patch, MagicMock


def test_agent_main_logic():
    # We want to test the block inside if __name__ == "__main__":
    # We use runpy to run the module as __main__
    import runpy

    # Mock agents.cli.run_app to avoid starting a real worker
    with patch("livekit.agents.cli.run_app") as mock_run:
        with patch("sys.stdout", new=io.TextIOWrapper(io.BytesIO(), encoding='utf-8')):
            # Mock entrypoint from agent_runner
            with patch("agent_runner.entrypoint"):
                # Run the script
                # We need to simulate being in __main__
                runpy.run_module("agent", run_name="__main__")

                mock_run.assert_called()
                # Verify WorkerOptions was created with entrypoint
                args, kwargs = mock_run.call_args
                opts = args[0]
                from agent_runner import entrypoint
                assert opts.entrypoint_fnc == entrypoint


def test_agent_env_vars():
    # Ensure GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK is set
    import agent  # This will set the env var on import
    assert os.environ.get("GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK") == "1"
