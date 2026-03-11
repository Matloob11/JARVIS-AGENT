import pytest
import subprocess
from unittest.mock import MagicMock, patch
import jarvis_notepad_automation
from jarvis_notepad_automation import run_cmd_command, open_notepad_simple, get_template_content


@pytest.mark.asyncio
async def test_run_cmd_command_success():
    with patch("jarvis_notepad_automation.subprocess.Popen") as mock_popen:
        res = await run_cmd_command("echo hello")
        assert res["status"] == "success"
        mock_popen.assert_called()


@pytest.mark.asyncio
async def test_run_cmd_command_error():
    # Raise OSError so it's caught by the try-except in jarvis_notepad_automation.py
    with patch("jarvis_notepad_automation.subprocess.Popen", side_effect=OSError("CMD fail")):
        res = await run_cmd_command("invalid")
        assert res["status"] == "error"
        assert "Error running command" in res["message"]


@pytest.mark.asyncio
async def test_open_notepad_simple():
    with patch("jarvis_notepad_automation.subprocess.Popen") as mock_popen:
        res = await open_notepad_simple()
        assert res["status"] == "success"
        mock_popen.assert_called()


@pytest.mark.asyncio
async def test_get_template_content():
    content, filename = get_template_content("html_login", "login.html")
    assert "<!DOCTYPE html>" in content
    assert filename == "login.html"

    # Test fallback
    content2, filename2 = get_template_content("unknown", "default.txt")
    assert filename2 == "default.txt"
