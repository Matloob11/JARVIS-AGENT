import pytest
import subprocess
from unittest.mock import patch
from jarvis_system_ctrl import shutdown_system, restart_system, sleep_system, lock_screen


@pytest.mark.asyncio
async def test_shutdown_system_success():
    with patch("subprocess.run") as mock_run:
        res = await shutdown_system()
        assert res["status"] == "success"
        mock_run.assert_called()


@pytest.mark.asyncio
async def test_restart_system_success():
    with patch("subprocess.run") as mock_run:
        res = await restart_system()
        assert res["status"] == "success"
        mock_run.assert_called()


@pytest.mark.asyncio
async def test_sleep_system_success():
    with patch("subprocess.run") as mock_run:
        res = await sleep_system()
        assert res["status"] == "success"
        mock_run.assert_called()


@pytest.mark.asyncio
async def test_lock_screen_success():
    with patch("subprocess.run") as mock_run:
        res = await lock_screen()
        assert res["status"] == "success"
        mock_run.assert_called()


@pytest.mark.asyncio
async def test_system_ctrl_errors():
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmd")):
        assert (await shutdown_system())["status"] == "error"
        assert (await restart_system())["status"] == "error"
        assert (await sleep_system())["status"] == "error"
        assert (await lock_screen())["status"] == "error"
