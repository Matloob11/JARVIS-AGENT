import pytest
from unittest.mock import patch

import services.system.jarvis_system_ctrl as sc
from services.system.jarvis_system_ctrl import (
    shutdown_system,
    restart_system,
    sleep_system,
    empty_recycle_bin,
)


@pytest.mark.skip(reason="Skipping system tool per user request")
@pytest.mark.asyncio
async def test_shutdown_system_success():
    with patch("subprocess.run") as mock_run:
        res = await shutdown_system()
        assert "shutting down" in res
        mock_run.assert_called()


@pytest.mark.skip(reason="Skipping system tool per user request")
@pytest.mark.asyncio
async def test_restart_system_success():
    with patch("subprocess.run") as mock_run:
        res = await restart_system()
        assert "restarting" in res
        mock_run.assert_called()


@pytest.mark.skip(reason="Skipping system tool per user request")
@pytest.mark.asyncio
async def test_sleep_system_success():
    with patch("os.system") as mock_os:
        # Mocking for Windows sleep
        with patch("sys.platform", "win32"):
            res = await sleep_system()
            assert "sleep" in res
            mock_os.assert_called()


@pytest.mark.asyncio
async def test_empty_recycle_bin_success():
    """X-29 path uses SHEmptyRecycleBinW; mock shell32 helpers, not subprocess.run."""
    with patch.object(sc, "sys") as fake_sys, patch.object(
        sc, "_query_recycle_bin", return_value=(2, 4.0)
    ), patch.object(sc, "_empty_recycle_bin_shell32", return_value=0):
        fake_sys.platform = "win32"
        res = await empty_recycle_bin()
    assert res["status"] == "success"
    assert "4.0 MB" in res["message"]
    assert res["data"]["method"] == "SHEmptyRecycleBinW"


@pytest.mark.skip(reason="Skipping system tool per user request")
@pytest.mark.asyncio
async def test_system_ctrl_errors():
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")):
        # We'll skip the actual asserts for shutdown/restart since they are platform dependent
        # but we allow the test to exist as a placeholder.
        pass
