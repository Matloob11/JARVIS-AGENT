
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from jarvis_window_ctrl import normalize_command, fuzzy_match_app, open_app, close

def test_normalize_command():
    assert normalize_command("Jarvis please open Notepad kholo") == "notepad"
    assert normalize_command("Open Google Chrome browser") == "google chrome"
    assert normalize_command("kholo calculator") == "calculator"

def test_fuzzy_match_app():
    # 'notepd' should match 'notepad'
    assert fuzzy_match_app("notepd") == "notepad"
    assert fuzzy_match_app("calc") == "calc"

@pytest.mark.asyncio
@patch("os.startfile")
async def test_open_app_notepad(mock_startfile):
    result = await open_app("open notepad")
    assert result["status"] == "success"
    assert result["app"] == "notepad"
    mock_startfile.assert_called()

@pytest.mark.asyncio
@patch("subprocess.run")
@patch("win32gui.EnumWindows")
async def test_close_notepad(mock_enum, mock_run):
    # Simulate closing Notepad via taskkill
    mock_run.return_value = MagicMock(returncode=0)
    result = await close("notepad")
    assert "Notepad" in result
    mock_run.assert_called()
