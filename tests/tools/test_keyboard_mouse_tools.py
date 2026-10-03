"""X-26: keyboard/mouse tools — Unicode, paths, protected hotkeys, volume, logging, meta."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.automation.keyboard_mouse_ctrl as km
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.system.jarvis_desktop_common import WindowInfo


@pytest.fixture(autouse=True)
def _stable_controller_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(km, "DEFAULT_TOKEN", "test-controller-token", raising=False)


@pytest.mark.asyncio
async def test_urdu_reaches_clipboard_mock_unchanged() -> None:
    controller = km.SafeController()
    controller.active = True
    urdu = "سلام دنیا"
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock) as mock_paste:
        mock_paste.return_value = True
        await controller.type_text(urdu)
    mock_paste.assert_awaited_once_with(urdu, restore=True)


@pytest.mark.asyncio
async def test_windows_path_keeps_backslashes() -> None:
    controller = km.SafeController()
    controller.active = True
    path_text = "D:\\Projects\\" + ("nested\\" * 12) + "file.txt\\nline"
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock) as mock_paste:
        mock_paste.return_value = True
        await controller.type_text(path_text)
    pasted = mock_paste.call_args[0][0]
    assert pasted == path_text
    assert "\\n" in pasted


@pytest.mark.asyncio
async def test_alt_f4_normal_window_accepted() -> None:
    normal = WindowInfo(hwnd=1, title="Untitled - Notepad", pid=99, process_name="notepad.exe")
    with patch.object(km, "_foreground_window_info", return_value=normal), patch.object(
        km.controller, "press_hotkey", new_callable=AsyncMock, return_value="ok"
    ) as mock_hotkey, patch.object(km, "desktop_control_enabled", return_value=True):
        result = await km.press_hotkey_tool(["alt", "f4"])
    mock_hotkey.assert_awaited_once()
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_alt_f4_refused_on_protected_window() -> None:
    protected = WindowInfo(hwnd=2, title="JARVIS-AGENT", pid=1, process_name="electron.exe")
    with patch.object(km, "_foreground_window_info", return_value=protected), patch.object(
        km.controller, "press_hotkey", new_callable=AsyncMock
    ) as mock_hotkey, patch.object(km, "desktop_control_enabled", return_value=True):
        result = await km.press_hotkey_tool(["alt", "f4"])
    mock_hotkey.assert_not_awaited()
    assert result["status"] == "error"
    assert "protected" in result["message"].lower() or "allowed nahi" in result["message"]


@pytest.mark.asyncio
async def test_endpoint_volume_path_used() -> None:
    controller = km.SafeController()
    mock_devices = MagicMock()
    mock_devices.EndpointVolume = "endpoint_iface"
    mock_audio = MagicMock()
    mock_audio.GetSpeakers.return_value = mock_devices
    with patch.object(km, "_load_volume_stack"), patch.object(km, "pythoncom", MagicMock()), patch.object(
        km, "pywintypes", MagicMock()
    ), patch.object(km, "AudioUtilities", mock_audio):
        iface = await controller._get_volume_interface()
    assert iface == "endpoint_iface"


def test_control_log_has_no_token_or_typed_text(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(km.config, "project_root", str(tmp_path))
    controller = km.SafeController()
    secret = "super-secret-token-value"
    controller.activate(secret)
    controller.log(f"type_text typed len={12}")

    log_path = log_dir / "control_log.txt"
    assert log_path.is_file()
    body = log_path.read_text(encoding="utf-8")
    assert secret not in body
    assert "super-secret" not in body
    assert "activation refused" in body
    assert "len=12" in body


@pytest.mark.asyncio
async def test_swipe_failsafe_is_caught() -> None:
    controller = km.SafeController()
    controller.active = True
    pg = MagicMock()
    pg.size.return_value = (1000, 800)
    pg.FailSafeException = type("FailSafeException", (Exception,), {})
    pg.moveTo.side_effect = pg.FailSafeException("corner")
    with patch.object(km, "_load_pyautogui", return_value=pg):
        assert await controller.swipe_gesture("up") == "Swipe up complete ho gaya."
    pg.dragTo.assert_not_called()


@pytest.mark.asyncio
async def test_set_volume_reports_pywintypes_error() -> None:
    controller = km.SafeController()
    controller.active = True
    win_err = type("error", (Exception,), {})
    pwt = MagicMock()
    pwt.error = win_err
    volume = MagicMock()
    volume.SetMasterVolumeLevelScalar.side_effect = win_err("com")
    with patch.object(km, "_load_volume_stack"), patch.object(km, "pythoncom", MagicMock()), patch.object(
        km, "pywintypes", pwt
    ), patch.object(controller, "_get_volume_interface", new_callable=AsyncMock, return_value=volume):
        msg, data = await controller.set_volume_percentage(50)
    assert msg.startswith("❌ Volume set nahi ho paaya:")
    assert data == {}


def test_get_tool_meta_type_text_raw_and_exclusive() -> None:
    meta = plugin_manager.get_tool_meta("type_text_tool")
    assert meta is not None
    assert meta["exclusive"] == "desktop_input"
    assert meta["arg_policies"].get("text") == "raw"
