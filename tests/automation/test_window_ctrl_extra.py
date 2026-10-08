import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from services.system.jarvis_desktop_common import WindowInfo
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
)


def test_normalize_command():
    assert normalize_command("Jarvis open whatsapp please") == "whatsapp"
    assert normalize_command("kholo notepad") == "notepad"
    assert normalize_command("chalao chrome browser") == "chrome"


def test_fuzzy_match_app():
    assert fuzzy_match_app("notpd").lower() == "notepad"
    assert fuzzy_match_app("chrom").lower() == "chrome"


@pytest.mark.asyncio
async def test_focus_window():
    mock_win = WindowInfo(55, "Untitled - Notepad", 900, "Notepad.exe")
    with patch("services.system.jarvis_window_ctrl._match_windows_by_name", return_value=[mock_win]), patch(
        "services.system.jarvis_window_ctrl.force_foreground", new_callable=AsyncMock, return_value=True
    ) as mock_fg, patch("asyncio.sleep", new_callable=AsyncMock):
        ok = await focus_window("notepad")
    assert ok is True
    mock_fg.assert_awaited()


@pytest.mark.asyncio
async def test_maximize_window():
    target = WindowInfo(101, "Calculator", 500, "CalculatorApp.exe")
    mock_win = MagicMock()
    with patch(
        "services.system.jarvis_window_ctrl._resolve_window_target", new_callable=AsyncMock, return_value=target
    ), patch("services.system.jarvis_window_ctrl._pygetwindow_for_hwnd", return_value=mock_win):
        await maximize_window("chrome")
        mock_win.maximize.assert_called()


@pytest.mark.asyncio
async def test_minimize_window():
    target = WindowInfo(101, "Calculator", 500, "CalculatorApp.exe")
    mock_win = MagicMock()
    with patch(
        "services.system.jarvis_window_ctrl._resolve_window_target", new_callable=AsyncMock, return_value=target
    ), patch("services.system.jarvis_window_ctrl._pygetwindow_for_hwnd", return_value=mock_win):
        await minimize_window("chrome")
        mock_win.minimize.assert_called()


@pytest.mark.asyncio
async def test_close_window():
    calc = WindowInfo(101, "Calculator", 500, "CalculatorApp.exe")
    with patch("services.system.jarvis_window_ctrl._safe_window_matches", return_value=[calc]), patch(
        "services.system.jarvis_window_ctrl.close_windows_gracefully",
        new_callable=AsyncMock,
        return_value={"closed": [101], "still_open": []},
    ):
        result = await close("chrome")
    assert result["status"] == "success"
    assert result["data"]["closed_titles"] == ["Calculator"]


@pytest.mark.asyncio
async def test_open_app_whatsapp():
    with patch("services.system.jarvis_window_ctrl.whatsapp_bot") as mock_ws, patch(
        "services.system.jarvis_window_ctrl._get_app_index", new_callable=AsyncMock, return_value=[]
    ):
        mock_ws.open_whatsapp = AsyncMock(return_value=True)
        mock_ws.ensure_whatsapp_focus = AsyncMock()
        await open_app("whatsapp")
        mock_ws.open_whatsapp.assert_called()
        mock_ws.ensure_whatsapp_focus.assert_called()


@pytest.mark.asyncio
async def test_save_notepad_save_as_flow():
    target = WindowInfo(77, "Untitled - Notepad", 901, "Notepad.exe")
    mock_pg = MagicMock()
    with patch("services.system.jarvis_window_ctrl._pick_notepad_target", new_callable=AsyncMock, return_value=target), patch(
        "services.system.jarvis_window_ctrl.force_foreground", new_callable=AsyncMock, return_value=True
    ), patch("services.system.jarvis_window_ctrl._load_pg", return_value=mock_pg), patch(
        "services.system.jarvis_window_ctrl.get_windows", return_value=[MagicMock()]
    ), patch(
        "services.system.jarvis_window_ctrl.clipboard_paste_text", new_callable=AsyncMock, return_value=True
    ), patch(
        "services.system.jarvis_window_ctrl.notepad_automation.handle_save_as_dialog",
        new_callable=AsyncMock,
        return_value=True,
    ), patch("services.system.jarvis_window_ctrl.config") as mock_cfg, patch(
        "os.makedirs"
    ), patch(
        "os.path.isfile", return_value=True
    ), patch(
        "asyncio.sleep", new_callable=AsyncMock
    ):
        mock_cfg.outputs_dir = "C:/Jarvis_Outputs_Test"
        res = await save_notepad("test_note.txt")
    assert res["status"] == "success"
    mock_pg.hotkey.assert_any_call("ctrl", "shift", "s")
    mock_pg.write.assert_not_called()


@pytest.mark.asyncio
async def test_save_notepad_named_in_place():
    target = WindowInfo(78, "notes.txt - Notepad", 902, "Notepad.exe")
    mock_pg = MagicMock()
    with patch("services.system.jarvis_window_ctrl._pick_notepad_target", new_callable=AsyncMock, return_value=target), patch(
        "services.system.jarvis_window_ctrl.force_foreground", new_callable=AsyncMock, return_value=True
    ), patch("services.system.jarvis_window_ctrl._load_pg", return_value=mock_pg), patch(
        "asyncio.sleep", new_callable=AsyncMock
    ):
        res = await save_notepad("jarvis_notes.txt")
    assert res["status"] == "success"
    mock_pg.hotkey.assert_called_with("ctrl", "s")
    assert res["data"]["method"] == "save_in_place"


@pytest.mark.asyncio
async def test_open_notepad_file_success():
    from pathlib import Path

    resolved = Path("C:/docs/notes.txt")
    with patch(
        "services.system.jarvis_window_ctrl._resolve_notepad_open_path",
        return_value=(resolved, None),
    ), patch("os.path.isfile", return_value=True), patch(
        "asyncio.to_thread", new_callable=AsyncMock
    ) as mock_thread:
        res = await open_notepad_file("notes.txt")
    assert res["status"] == "success"
    mock_thread.assert_awaited()


@pytest.mark.asyncio
async def test_open_notepad_file_path_not_allowed():
    with patch(
        "services.system.jarvis_window_ctrl._resolve_notepad_open_path",
        return_value=(None, "PATH_NOT_ALLOWED"),
    ):
        res = await open_notepad_file("../../.env")
    assert res["status"] == "error"
    assert res["error_code"] == "PATH_NOT_ALLOWED"
