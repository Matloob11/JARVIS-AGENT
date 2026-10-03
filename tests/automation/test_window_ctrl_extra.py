import pytest
import asyncio
import pygetwindow as gw
from unittest.mock import MagicMock, patch, AsyncMock
from services.system.jarvis_window_ctrl import (
    focus_window,
    maximize_window,
    minimize_window,
    close,
    normalize_command,
    fuzzy_match_app,
    open_app,
    save_notepad,
    open_notepad_file,
    APP_MAPPINGS
)

def test_normalize_command():
    assert normalize_command("Jarvis open whatsapp please") == "whatsapp"
    assert normalize_command("kholo notepad") == "notepad"
    assert normalize_command("chalao chrome browser") == "chrome"

def test_fuzzy_match_app():
    # Assuming APP_MAPPINGS has "Notepad" and "Chrome"
    assert fuzzy_match_app("notpd").lower() == "notepad"
    assert fuzzy_match_app("chrom").lower() == "chrome"

@pytest.mark.asyncio
async def test_focus_window():
    mock_win = MagicMock()
    mock_win.title = "Notepad"
    mock_win.isMinimized = True

    with patch("pygetwindow.getAllWindows", return_value=[mock_win]):
        with patch("services.system.jarvis_window_ctrl.win32gui") as mock_gui:
            with patch("asyncio.sleep"):
                await focus_window("notepad")
            mock_gui.ShowWindow.assert_called()

    mock_win.restore.assert_called()
    mock_win.activate.assert_called()

@pytest.mark.asyncio
async def test_maximize_window():
    mock_win = MagicMock()
    mock_win.title = "Chrome"

    with patch("pygetwindow.getWindowsWithTitle", return_value=[mock_win]):
        await maximize_window("chrome")
        mock_win.maximize.assert_called()

@pytest.mark.asyncio
async def test_minimize_window():
    mock_win = MagicMock()
    mock_win.title = "Chrome"

    with patch("pygetwindow.getWindowsWithTitle", return_value=[mock_win]):
        await minimize_window("chrome")
        mock_win.minimize.assert_called()

@pytest.mark.asyncio
async def test_close_window():
    hwnd = 101

    def _enum_windows(callback, _param):
        callback(hwnd, None)

    with patch("services.system.jarvis_window_ctrl.win32gui") as mock_gui:
        mock_gui.IsWindowVisible.return_value = True
        mock_gui.GetWindowText.return_value = "Google Chrome"
        mock_gui.EnumWindows.side_effect = _enum_windows
        mock_gui.IsWindow.return_value = False
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await close("chrome")
        mock_gui.PostMessage.assert_called()

@pytest.mark.asyncio
async def test_open_app_whatsapp():
    with patch("services.system.jarvis_window_ctrl.focus_window", new_callable=AsyncMock) as mock_focus:
        with patch("subprocess.Popen") as mock_popen:
            # Patch at the SOURCE because it's already imported
            with patch("services.system.jarvis_window_ctrl.whatsapp_bot") as mock_ws:
                mock_ws.open_whatsapp = AsyncMock()
                mock_ws.ensure_whatsapp_focus = AsyncMock()

                await open_app("whatsapp")
                mock_ws.open_whatsapp.assert_called()
                mock_ws.ensure_whatsapp_focus.assert_called()


@pytest.mark.asyncio
async def test_save_notepad_success():
    mock_win = MagicMock()
    mock_pg = MagicMock()
    with patch("services.system.jarvis_window_ctrl.get_windows", return_value=[mock_win]):
        with patch("services.system.jarvis_window_ctrl._load_pg", return_value=mock_pg):
            with patch("services.system.jarvis_window_ctrl.config") as mock_cfg:
                mock_cfg.outputs_dir = "C:/Jarvis_Outputs_Test"
                with patch("os.makedirs"):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        res = await save_notepad("test_note.txt")
                        assert res["status"] == "success"
                        mock_pg.hotkey.assert_called_with("ctrl", "s")

@pytest.mark.asyncio
async def test_open_notepad_file_success():
    with patch("os.path.exists", return_value=True):
        with patch("subprocess.Popen") as mock_popen:
            res = await open_notepad_file("D:/test_note.txt")
            assert res["status"] == "success"
            mock_popen.assert_called()
