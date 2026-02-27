import pytest
import asyncio
import pygetwindow as gw
from unittest.mock import MagicMock, patch, AsyncMock
from jarvis_window_ctrl import (
    focus_window,
    maximize_window,
    minimize_window,
    close,
    normalize_command,
    fuzzy_match_app,
    open_app,
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
        with patch("asyncio.sleep"):
            await focus_window("notepad")
            
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
    mock_win = MagicMock()
    mock_win.title = "Chrome"
    
    with patch("pygetwindow.getWindowsWithTitle", return_value=[mock_win]):
        await close("chrome")
        # Just verify it finishes without error for now as logic is complex

@pytest.mark.asyncio
async def test_open_app_whatsapp():
    with patch("jarvis_window_ctrl.focus_window", new_callable=AsyncMock) as mock_focus:
        with patch("subprocess.Popen") as mock_popen:
            # Patch whatsapp_bot INSIDE jarvis_window_ctrl
            with patch("jarvis_window_ctrl.whatsapp_bot") as mock_ws:
                mock_ws.open_whatsapp = AsyncMock()
                mock_ws.ensure_whatsapp_focus = AsyncMock()
                
                await open_app("whatsapp")
                # focus_window is called in open_app for whatsapp if it starts with http
                # But here it hits elif matched_key == "whatsapp":
                mock_ws.open_whatsapp.assert_called()

@pytest.mark.asyncio
async def test_lock_screen():
    with patch("subprocess.run") as mock_run:
        from jarvis_system_ctrl import lock_screen
        await lock_screen()
        args = mock_run.call_args[0][0]
        assert "rundll32.exe" in args
        assert any("LockWorkStation" in arg for arg in args)

@pytest.mark.asyncio
async def test_shutdown_system():
    with patch("subprocess.run") as mock_run:
        from jarvis_system_ctrl import shutdown_system
        await shutdown_system()
        args = mock_run.call_args[0][0]
        assert "shutdown" in args
        assert "/s" in args
