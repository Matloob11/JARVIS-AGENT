import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import jarvis_window_ctrl
from jarvis_window_ctrl import save_notepad, open_notepad_file, normalize_command, fuzzy_match_app


@pytest.mark.asyncio
async def test_normalize_command():
    assert normalize_command("open notepad kholo") == "notepad"
    assert normalize_command("zara whatsapp open karo") == "whatsapp"
    assert normalize_command("please chrome") == "chrome"


@pytest.mark.asyncio
async def test_fuzzy_match_app():
    assert fuzzy_match_app("notpad") == "notepad"
    res = fuzzy_match_app("goog")
    assert "google" in res.lower()


@pytest.mark.asyncio
async def test_save_notepad_success():
    mock_win = MagicMock()
    with patch("jarvis_window_ctrl.get_windows", return_value=[mock_win]):
        with patch("pyautogui.hotkey"):
            with patch("pyautogui.typewrite"):
                with patch("pyautogui.press"):
                    with patch("os.path.exists", return_value=True):
                        res = await save_notepad("D:/test.txt")
                        assert "status" in res
                        assert "success" == res["status"]


@pytest.mark.asyncio
async def test_save_notepad_error():
    # Force 'error' status by providing empty window list
    with patch("jarvis_window_ctrl.get_windows", return_value=[]):
        res = await save_notepad()
        assert res["status"] == "error"
        assert "window nahi mili" in res["message"]


@pytest.mark.asyncio
async def test_open_notepad_file_success():
    with patch("os.path.exists", return_value=True):
        with patch("subprocess.Popen") as mock_popen:
            res = await open_notepad_file("D:/test.txt")
            assert isinstance(res, dict)
            assert res["status"] == "success"
            mock_popen.assert_called()


@pytest.mark.asyncio
async def test_open_notepad_file_error():
    # Case 1: File exists but Popen fails
    with patch("os.path.exists", return_value=True):
        with patch("subprocess.Popen", side_effect=OSError("Fail")):
            res = await open_notepad_file("invalid.txt")
            assert isinstance(res, dict)
            assert res["status"] == "error"

    # Case 2: File doesn't exist (returns string currently)
    with patch("os.path.exists", return_value=False):
        res = await open_notepad_file("absent.txt")
        assert isinstance(res, str)
        assert "nahi mili" in res.lower()
