import pytest
import os
from unittest.mock import MagicMock, patch, AsyncMock, mock_open
from jarvis_notepad_automation import (
    notepad_automation,
    create_template_code,
    write_custom_code,
    write_custom_code,
    run_cmd_command,
    open_notepad_simple,
    get_template_content
)

@pytest.mark.asyncio
async def test_open_notepad_simple():
    # Patch subprocess.Popen in the module namespace
    with patch("jarvis_notepad_automation.subprocess.Popen") as mock_popen:
        await open_notepad_simple()
        mock_popen.assert_called()

@pytest.mark.asyncio
async def test_notepad_automation_typing():
    with patch("pyautogui.write") as mock_write:
        with patch("pyautogui.press"):
            await notepad_automation.simulate_typing("Hello")
            mock_write.assert_called()

@pytest.mark.asyncio
async def test_save_file_safely():
    with patch("builtins.open", mock_open()) as mock_file:
        with patch("os.makedirs"):
            await notepad_automation.save_file_safely("content", "test.txt")
            mock_file.assert_called()

@pytest.mark.asyncio
async def test_create_template_code():
    # Patch get_template_content and Popen in jarvis_notepad_automation
    with patch("jarvis_notepad_automation.get_template_content", return_value=("<html></html>", "test.html")):
        with patch("jarvis_notepad_automation.subprocess.Popen") as mock_popen:
            with patch.object(notepad_automation, "ensure_notepad_focus", return_value=True):
                with patch.object(notepad_automation, "simulate_typing", new_callable=AsyncMock) as mock_type:
                    with patch.object(notepad_automation, "save_file_safely", return_value=(True, "path")) as mock_save:
                        await create_template_code("html", "test.html", auto_run=False)
                        mock_popen.assert_called()

@pytest.mark.asyncio
async def test_write_custom_code():
    with patch("jarvis_notepad_automation.subprocess.Popen") as mock_popen:
        with patch.object(notepad_automation, "ensure_notepad_focus", return_value=True):
            with patch.object(notepad_automation, "simulate_typing", new_callable=AsyncMock) as mock_type:
                await write_custom_code("Custom Code", "custom.py", auto_run=False)
                mock_type.assert_called()

@pytest.mark.asyncio
async def test_run_cmd_command_error():
    # Raise OSError so it's caught by the try-except in jarvis_notepad_automation.py
    with patch("jarvis_notepad_automation.subprocess.Popen", side_effect=OSError("CMD fail")):
        res = await run_cmd_command("invalid")
        assert res["status"] == "error"
        assert "Error running command" in res["message"]

@pytest.mark.asyncio
async def test_get_template_content():
    content, filename = get_template_content("html_login", "login.html")
    assert "<!DOCTYPE html>" in content
    assert filename == "login.html"

    # Test fallback
    content2, filename2 = get_template_content("unknown", "default.txt")
    assert filename2 == "default.txt"
