import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import jarvis_window_ctrl
from jarvis_window_ctrl import (
    focus_window, open_app, close, minimize_window, maximize_window,
    folder_file, create_folder, open_outputs_folder
)


@pytest.fixture
def mock_gw():
    with patch("jarvis_window_ctrl.gw") as mock:
        yield mock


@pytest.fixture
def mock_win32():
    with patch("jarvis_window_ctrl.win32gui") as m_gui:
        with patch("jarvis_window_ctrl.win32con") as m_con:
            yield m_gui, m_con


@pytest.fixture
def mock_subp():
    with patch("jarvis_window_ctrl.subprocess.Popen") as mock_p:
        with patch("jarvis_window_ctrl.subprocess.run") as mock_r:
            yield mock_p, mock_r


@pytest.mark.asyncio
async def test_focus_window_success(mock_gw, mock_win32):
    mock_win = MagicMock()
    mock_win.title = "Test Window"
    mock_win.isMinimized = False
    mock_gw.getAllWindows.return_value = [mock_win]

    res = await focus_window("Test")
    assert res is True
    mock_win.activate.assert_called()


@pytest.mark.asyncio
async def test_open_app_url():
    with patch("os.startfile") as mock_start:
        res = await open_app("open google.com")
        assert res["status"] == "success"
        mock_start.assert_called()


@pytest.mark.asyncio
async def test_open_app_subprocess_fallback(mock_subp):
    mock_p, mock_r = mock_subp
    with patch("os.startfile", side_effect=OSError("Startfile failed")):
        res = await open_app("open unknown_app")
        assert res["status"] == "success"
        mock_p.assert_called()


@pytest.mark.asyncio
async def test_close_window_notepad(mock_subp):
    mock_p, mock_r = mock_subp
    res = await close("notepad")
    assert "Notepad force close" in res
    mock_r.assert_called()


@pytest.mark.asyncio
async def test_close_window_generic(mock_win32):
    m_gui, m_con = mock_win32
    call_counts = {"count": 0}

    def mock_enum(callback, _):
        if call_counts["count"] == 0:
            callback(123, None)
        call_counts["count"] += 1
    m_gui.EnumWindows.side_effect = mock_enum
    m_gui.IsWindowVisible.return_value = True
    m_gui.GetWindowText.return_value = "Test App"
    m_gui.IsWindow.return_value = True
    res = await close("test app")
    assert res["status"] == "success"
    m_gui.PostMessage.assert_called()


@pytest.mark.asyncio
async def test_minimize_active(mock_gw):
    mock_win = MagicMock()
    mock_gw.getActiveWindow.return_value = mock_win
    res = await minimize_window("active")
    assert "minimize" in res.lower()
    mock_win.minimize.assert_called()


@pytest.mark.asyncio
async def test_minimize_named(mock_gw):
    mock_win = MagicMock()
    mock_gw.getWindowsWithTitle.return_value = [mock_win]
    res = await minimize_window("Notepad")
    assert "minimize" in res.lower()
    mock_win.minimize.assert_called()


@pytest.mark.asyncio
async def test_maximize_active(mock_gw):
    mock_win = MagicMock()
    mock_gw.getActiveWindow.return_value = mock_win
    res = await maximize_window("active")
    assert "maximize" in res.lower()
    mock_win.maximize.assert_called()


@pytest.mark.asyncio
async def test_maximize_named(mock_gw):
    mock_win = MagicMock()
    mock_gw.getWindowsWithTitle.return_value = [mock_win]
    res = await maximize_window("Notepad")
    assert "maximize" in res.lower()
    mock_win.maximize.assert_called()


@pytest.mark.asyncio
async def test_folder_file_exists():
    with patch("os.path.exists", return_value=True):
        with patch("os.startfile") as mock_start:
            res = await folder_file("C:/test")
            assert "open" in str(res).lower()
            mock_start.assert_called()


@pytest.mark.asyncio
async def test_create_folder():
    with patch("os.makedirs") as mock_mkdir:
        with patch("os.path.exists", return_value=True):
            res = await create_folder("NewFolder")
            assert "create" in res.lower()
            mock_mkdir.assert_called()


@pytest.mark.asyncio
async def test_open_outputs_folder():
    with patch("os.startfile") as mock_start:
        res = await open_outputs_folder("QR_Codes")
        assert "open" in str(res).lower()
        mock_start.assert_called()
