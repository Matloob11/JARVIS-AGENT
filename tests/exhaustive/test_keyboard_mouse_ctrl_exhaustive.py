"""
Plan C-61: exhaustive tests of services/automation/keyboard_mouse_ctrl.py (X-26 behaviour).

tests/exhaustive/conftest.py keeps the K-27 guard on, so no real keyboard, mouse, clipboard or
volume call can happen; every controller here gets mock keyboard and mouse objects. The module is
imported only by its services.automation path, CONTROLLER_TOKEN is a throwaway value set through
monkeypatch, logs/control_log.txt goes to tmp_path and asyncio.sleep returns at once.

pyautogui, pythoncom and pycaw are patched on their own module and class objects, never as km.<name>
globals, so the tests keep working when X-65 moves those imports inside the functions.
"""
# pylint: disable=protected-access

from __future__ import annotations

import os
import subprocess
import sys
import types
from unittest.mock import AsyncMock, MagicMock, call, patch

import pyautogui
import pytest
import pythoncom
import pywintypes
import win32clipboard
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key
from pynput.mouse import Button
from pynput.mouse import Controller as MouseController

import services.automation.keyboard_mouse_ctrl as km
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.system import jarvis_desktop_common as desktop_common
from services.system.jarvis_desktop_common import WindowInfo
from services.utils import jarvis_bridge

LIVE = os.environ.get("JARVIS_LIVE_GUI_TESTS", "").strip() == "1"
TEST_TOKEN = "c61-throwaway-controller-token"
INACTIVE = "\U0001f6d1 Controller is inactive."
URDU = "سلام دنیا"

# Odd pids are never real Windows pids, so none of these is this test process or its parent.
NOTEPAD = WindowInfo(hwnd=11, title="Untitled - Notepad", pid=101, process_name="notepad.exe")
BROWSER = WindowInfo(hwnd=12, title="Inbox - Google Chrome", pid=103, process_name="chrome.exe")
PROTECTED = (
    WindowInfo(hwnd=13, title="JARVIS-AGENT", pid=105, process_name="electron.exe"),
    WindowInfo(hwnd=14, title="agent.py - JARVIS-AGENT-main - Cursor", pid=107,
               process_name="Cursor.exe"),
    WindowInfo(hwnd=15, title="Windows PowerShell", pid=109, process_name="powershell.exe"),
)
CLOSE_COMBOS = (["alt", "f4"], ["ctrl", "w"], ["ctrl", "q"], ["ctrl", "shift", "w"],
                ["ALT", "F4"], ["Control", "W"])


@pytest.fixture(name="notify", autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path, instant_sleep) -> AsyncMock:
    """Throwaway CONTROLLER_TOKEN, control log in tmp_path; returns the notify_thinking mock."""
    monkeypatch.setenv("CONTROLLER_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(km.config, "controller_token", TEST_TOKEN, raising=False)
    monkeypatch.setattr(km, "DEFAULT_TOKEN", km._init_default_token())
    monkeypatch.setattr(km.config, "project_root", str(tmp_path))
    notify = AsyncMock()
    monkeypatch.setattr(jarvis_bridge, "notify_thinking", notify)
    return notify


@pytest.fixture(name="ctl")
def _ctl() -> km.SafeController:
    """An inactive controller with mock keyboard and mouse objects."""
    controller = km.SafeController()
    controller.keyboard = MagicMock(name="keyboard")
    controller.mouse = MagicMock(name="mouse")
    controller.mouse.position = (100, 100)
    return controller


@pytest.fixture(name="tool_ctl")
def _tool_ctl(monkeypatch: pytest.MonkeyPatch, ctl: km.SafeController) -> km.SafeController:
    """ctl behind the module's *_tool functions; desktop control on, Notepad in front."""
    monkeypatch.setattr(km, "controller", ctl)
    monkeypatch.setattr(km, "desktop_control_enabled", lambda: True)
    monkeypatch.setattr(km, "_foreground_window_info", lambda: NOTEPAD)
    return ctl


def _pressed(mock: MagicMock) -> list[object]:
    return [c.args[0] for c in mock.call_args_list]


def _control_log(tmp_path) -> str:
    return (tmp_path / "logs" / "control_log.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- guard


def _open_and_close_clipboard() -> None:
    win32clipboard.OpenClipboard()  # pylint: disable=c-extension-no-member
    win32clipboard.CloseClipboard()  # pylint: disable=c-extension-no-member


@pytest.mark.skipif(LIVE, reason="JARVIS_LIVE_GUI_TESTS=1 turns the guard off")
def test_guard_blocks_real_input_devices() -> None:
    """Every call below would reach a real device; the arguments are harmless anyway."""
    mouse = MouseController()
    attempts = {
        "pyautogui.write": lambda: pyautogui.write(""),
        "pyautogui.moveTo": lambda: pyautogui.moveTo(*pyautogui.position()),
        "pyautogui.moveRel": lambda: pyautogui.moveRel(0, 0),
        "pynput keyboard type": lambda: KeyboardController().type(""),
        "pynput mouse move": lambda: mouse.move(0, 0),
        "pynput mouse position": lambda: setattr(mouse, "position", mouse.position),
        "pycaw speakers": AudioUtilities.GetSpeakers,
        "clipboard": _open_and_close_clipboard,
        "subprocess.run": lambda: subprocess.run([sys.executable, "-c", "pass"], check=False),
    }
    for name, attempt in attempts.items():
        with pytest.raises(AssertionError, match="REAL SIDE EFFECT"):
            attempt()
            pytest.fail(f"{name} was not blocked")


# --------------------------------------------------------------------------- token and activation


def test_default_token_comes_from_controller_token() -> None:
    assert km.DEFAULT_TOKEN == TEST_TOKEN


def test_token_from_environment_when_config_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(km.config, "controller_token", None, raising=False)
    monkeypatch.setenv("CONTROLLER_TOKEN", "c61-env-only-token")
    assert km._init_default_token() == "c61-env-only-token"


def test_missing_token_gives_a_per_process_secret_and_one_warning(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(km.config, "controller_token", None, raising=False)
    monkeypatch.delenv("CONTROLLER_TOKEN", raising=False)
    monkeypatch.setattr(km, "_TOKEN_WARNING_EMITTED", False)
    with patch.object(km.logger, "warning") as warning:
        first, second = km._init_default_token(), km._init_default_token()
    assert len(first) >= 32 and len(second) >= 32
    assert first != second
    warning.assert_called_once_with("CONTROLLER_TOKEN missing; using per-process token")


def test_activation_needs_the_token(ctl: km.SafeController, tmp_path) -> None:
    for wrong in ("wrong-token", None, ""):
        ctl.activate(wrong)
        assert ctl.is_active() is False
    ctl.activate(TEST_TOKEN)
    assert ctl.is_active() is True
    assert ctl.activation_time is not None
    ctl.deactivate()
    assert ctl.is_active() is False
    body = _control_log(tmp_path)
    assert body.count("activation refused") == 3
    assert "wrong-token" not in body
    assert TEST_TOKEN not in body


async def test_control_log_keeps_lengths_not_text_or_token(ctl: km.SafeController,
                                                           tmp_path) -> None:
    ctl.activate("not-the-token")
    ctl.activate(TEST_TOKEN)
    private = "my private note"
    await ctl.type_text(private)
    body = _control_log(tmp_path)
    assert f"type_text typed len={len(private)}" in body
    for value in ("not-the-token", TEST_TOKEN, private):
        assert value not in body


async def test_every_action_refuses_while_inactive(ctl: km.SafeController) -> None:
    actions = (
        ctl.move_cursor("up"), ctl.mouse_click(), ctl.scroll_cursor("up"), ctl.type_text("test"),
        ctl.type_code_animation("print(1)"), ctl.press_key("enter"),
        ctl.press_hotkey(["ctrl", "c"]), ctl.control_volume("up"),
        ctl.set_volume_percentage(50), ctl.swipe_gesture("up"),
    )
    for action in actions:
        assert await action == INACTIVE
    assert ctl.keyboard.mock_calls == []
    assert ctl.mouse.mock_calls == []


# --------------------------------------------------------------------------- mouse


async def test_move_cursor_stays_on_the_screen(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    steps = (("right", 50, (150, 100)), ("left", 50, (100, 100)), ("down", 50, (100, 150)),
             ("up", 50, (100, 100)), ("up", 500, (100, 0)), ("right", 5000, (1919, 0)),
             ("down", 5000, (1919, 1079)), ("left", 5000, (0, 1079)))
    with patch.object(pyautogui, "size", return_value=(1920, 1080)):
        for direction, distance, expected in steps:
            assert await ctl.move_cursor(direction, distance) == f"Mouse {direction} move ho gaya."
            assert ctl.mouse.position == expected


async def test_mouse_click_buttons(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    assert await ctl.mouse_click("left") == "Left click ho gaya."
    assert await ctl.mouse_click("right") == "Right click ho gaya."
    assert await ctl.mouse_click("double") == "Double click ho gaya."
    assert ctl.mouse.click.call_args_list == [call(Button.left, 1), call(Button.right, 1),
                                              call(Button.left, 2)]


async def test_scroll_cursor(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    assert await ctl.scroll_cursor("up", 10) == "Scroll up ho gaya."
    assert await ctl.scroll_cursor("down", 10) == "Scroll down ho gaya."
    assert ctl.mouse.scroll.call_args_list == [call(0, 10), call(0, -10)]


async def test_scroll_cursor_falls_back_to_pyautogui(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    ctl.mouse.scroll.side_effect = RuntimeError("pynput failed")
    with patch.object(pyautogui, "scroll") as py_scroll:
        assert await ctl.scroll_cursor("up", 10) == "Scroll up ho gaya."
    py_scroll.assert_called_once_with(1000)


async def test_swipe_gestures_drag_through_the_centre(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(pyautogui, "size", return_value=(1000, 800)), \
            patch.object(pyautogui, "moveTo") as move_to, \
            patch.object(pyautogui, "dragTo") as drag_to:
        for direction in ("up", "down", "left", "right"):
            assert await ctl.swipe_gesture(direction) == f"Swipe {direction} complete ho gaya."
    assert move_to.call_args_list == [call(500, 600), call(500, 200), call(700, 400),
                                      call(300, 400)]
    assert drag_to.call_args_list == [call(500, 200, duration=0.5), call(500, 600, duration=0.5),
                                      call(300, 400, duration=0.5), call(700, 400, duration=0.5)]


async def test_swipe_failsafe_is_caught(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(pyautogui, "size", return_value=(1000, 800)), \
            patch.object(pyautogui, "moveTo", side_effect=pyautogui.FailSafeException()), \
            patch.object(pyautogui, "dragTo") as drag_to:
        assert await ctl.swipe_gesture("up") == "Swipe up complete ho gaya."
    drag_to.assert_not_called()


# --------------------------------------------------------------------------- typing


async def test_short_ascii_is_typed_key_by_key(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock) as paste:
        assert await ctl.type_text("Hi!") == "Text type ho gaya (3 characters)."
    paste.assert_not_awaited()
    assert _pressed(ctl.keyboard.press) == ["H", "i", "!"]
    assert _pressed(ctl.keyboard.release) == ["H", "i", "!"]


async def test_literal_escapes_become_enter_and_tab(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    await ctl.type_text("a\\nb\\tc")
    assert _pressed(ctl.keyboard.press) == ["a", Key.enter, "b", Key.tab, "c"]


@pytest.mark.parametrize("text", ["C:\\new\\temp.txt", "\\\\server\\share\\new", "x\ny\\n"])
async def test_paths_and_real_newlines_keep_their_backslashes(ctl: km.SafeController,
                                                              text: str) -> None:
    ctl.activate(TEST_TOKEN)
    await ctl.type_text(text)
    assert _pressed(ctl.keyboard.press) == [Key.enter if ch == "\n" else ch for ch in text]


async def test_typing_goes_on_after_a_failed_key(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    ctl.keyboard.press.side_effect = [None, ValueError("bad key"), None]
    assert await ctl.type_text("abc") == "Text type ho gaya (3 characters)."
    assert _pressed(ctl.keyboard.press) == ["a", "b", "c"]
    assert _pressed(ctl.keyboard.release) == ["a", "c"]


async def test_unicode_is_pasted_through_the_clipboard_unchanged(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock,
                      return_value=True) as paste:
        assert await ctl.type_text(URDU) == f"Text paste ho gaya ({len(URDU)} characters)."
    paste.assert_awaited_once_with(URDU, restore=True)
    ctl.keyboard.press.assert_not_called()


async def test_long_ascii_text_is_pasted(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    text = "a" * 60
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock,
                      return_value=True) as paste:
        assert await ctl.type_text(text) == "Text paste ho gaya (60 characters)."
    paste.assert_awaited_once_with(text, restore=True)
    ctl.keyboard.press.assert_not_called()


async def test_failed_paste_falls_back_to_typing(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock, return_value=False):
        assert await ctl.type_text("a" * 60) == "Text type ho gaya (60 characters)."
    assert _pressed(ctl.keyboard.press) == ["a"] * 60


async def test_force_typing_skips_the_clipboard(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock) as paste:
        await ctl.type_text(URDU, force_typing=True)
    paste.assert_not_awaited()
    assert _pressed(ctl.keyboard.press) == list(URDU)


async def test_code_animation_types_the_head_and_pastes_the_rest(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    code = "x" * 1500 + "y" * 600
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock,
                      return_value=True) as paste:
        assert await ctl.type_code_animation(code) == "Code animation complete (2100 characters)."
    assert ctl.keyboard.press.call_count == 1500
    paste.assert_awaited_once_with("y" * 600, restore=True)


async def test_code_animation_is_capped_at_6000_characters(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock,
                      return_value=True) as paste:
        assert await ctl.type_code_animation("a" * 7000) == \
            "Code animation complete (6000 characters)."
    paste.assert_awaited_once_with("a" * 4500, restore=True)


# --------------------------------------------------------------------------- keys and hotkeys


@pytest.mark.parametrize(("name", "expected"), [
    ("enter", Key.enter), ("f5", Key.f5), ("F12", Key.f12), ("escape", Key.esc),
    ("control", Key.ctrl), ("pgdn", Key.page_down), ("windows", Key.cmd), ("insert", Key.insert),
    ("print_screen", Key.print_screen), ("media_volume_up", Key.media_volume_up),
    ("/", "/"), ("a", "a"), ("7", "7"),
])
async def test_press_key_knows_the_full_key_set(ctl: km.SafeController, name: str,
                                                expected: object) -> None:
    ctl.activate(TEST_TOKEN)
    assert await ctl.press_key(name) == f"Key '{name}' press ho gaya."
    ctl.keyboard.press.assert_called_once_with(expected)
    ctl.keyboard.release.assert_called_once_with(expected)


@pytest.mark.parametrize("name", ["invalid_key_123", "", "ctrl+c"])
async def test_press_key_rejects_unknown_names(ctl: km.SafeController, name: str) -> None:
    ctl.activate(TEST_TOKEN)
    assert await ctl.press_key(name) == f"❌ Invalid key: {name}"
    ctl.keyboard.press.assert_not_called()


async def test_press_key_failure_is_reported(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    ctl.keyboard.press.side_effect = AttributeError("fail")
    assert await ctl.press_key("enter") == "❌ Failed key: enter — fail"


async def test_press_hotkey_presses_in_order_and_releases_in_reverse(
        ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    assert await ctl.press_hotkey(["ctrl", "shift", "esc"]) == \
        "Hotkey ctrl + shift + esc press ho gaya."
    assert _pressed(ctl.keyboard.press) == [Key.ctrl, Key.shift, Key.esc]
    assert _pressed(ctl.keyboard.release) == [Key.esc, Key.shift, Key.ctrl]


async def test_press_hotkey_checks_every_key_before_pressing(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    assert await ctl.press_hotkey(["ctrl", "invalid"]) == "❌ Invalid key in hotkey: invalid"
    ctl.keyboard.press.assert_not_called()


@pytest.mark.parametrize("window", PROTECTED, ids=lambda w: w.process_name)
@pytest.mark.parametrize("keys", CLOSE_COMBOS, ids="+".join)
async def test_close_shortcut_is_refused_on_a_protected_window(
        tool_ctl: km.SafeController, monkeypatch: pytest.MonkeyPatch, window: WindowInfo,
        keys: list[str]) -> None:
    monkeypatch.setattr(km, "_foreground_window_info", lambda: window)
    result = await km.press_hotkey_tool(keys)
    assert result["status"] == "error"
    assert result["tool"] == "press_hotkey_tool"
    assert result["error_code"] == "DESKTOP_CONTROL_DISABLED"
    assert result["message"] == "Yeh shortcut protected window par allowed nahi hai."
    assert tool_ctl.activation_time is None
    tool_ctl.keyboard.press.assert_not_called()


@pytest.mark.parametrize("keys", CLOSE_COMBOS, ids="+".join)
async def test_close_shortcut_works_on_a_normal_window(tool_ctl: km.SafeController,
                                                       keys: list[str]) -> None:
    result = await km.press_hotkey_tool(keys)
    assert result["status"] == "success"
    assert tool_ctl.keyboard.press.call_count == len(keys)
    assert tool_ctl.is_active() is False


async def test_other_shortcuts_ignore_the_foreground_window(tool_ctl: km.SafeController,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    foreground = MagicMock(return_value=PROTECTED[0])
    monkeypatch.setattr(km, "_foreground_window_info", foreground)
    assert (await km.press_hotkey_tool(["ctrl", "s"]))["status"] == "success"
    assert (await km.press_key_tool("f4"))["status"] == "success"
    foreground.assert_not_called()
    assert _pressed(tool_ctl.keyboard.press) == [Key.ctrl, "s", Key.f4]


def test_close_shortcut_risk_is_medium_only_with_a_browser_in_front(
        monkeypatch: pytest.MonkeyPatch) -> None:
    for window, close_risk in ((BROWSER, "medium"), (NOTEPAD, "low"), (PROTECTED[0], "low"),
                               (None, "low")):
        monkeypatch.setattr(km, "_foreground_window_info", lambda w=window: w)
        assert km._press_hotkey_risk({"keys": ["alt", "f4"]}) == close_risk
        assert km._press_hotkey_risk({"keys": ["ctrl", "shift", "w"]}) == close_risk
        assert km._press_hotkey_risk({"keys": ["ctrl", "c"]}) == "low"
    assert km._press_hotkey_risk({}) == "low"


# --------------------------------------------------------------------------- volume


async def test_volume_interface_prefers_endpoint_volume(ctl: km.SafeController) -> None:
    devices = MagicMock(EndpointVolume="endpoint")
    with patch.object(pythoncom, "CoInitialize"), \
            patch.object(AudioUtilities, "GetSpeakers", return_value=devices):
        assert await ctl._get_volume_interface() == "endpoint"
    devices.Activate.assert_not_called()


async def test_volume_interface_uses_the_legacy_volume_attribute(ctl: km.SafeController) -> None:
    with patch.object(pythoncom, "CoInitialize"), patch.object(
            AudioUtilities, "GetSpeakers", return_value=types.SimpleNamespace(volume="legacy")):
        assert await ctl._get_volume_interface() == "legacy"


async def test_volume_interface_activates_an_old_device(ctl: km.SafeController) -> None:
    devices = MagicMock(spec=["Activate"])
    with patch.object(pythoncom, "CoInitialize"), \
            patch.object(AudioUtilities, "GetSpeakers", return_value=devices), \
            patch.object(km, "cast", return_value="activated") as cast:
        assert await ctl._get_volume_interface() == "activated"
    devices.Activate.assert_called_once_with(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    cast.assert_called_once()


async def test_volume_interface_falls_back_to_the_default_endpoint(ctl: km.SafeController) -> None:
    with patch.object(pythoncom, "CoInitialize"), \
            patch.object(AudioUtilities, "GetSpeakers", side_effect=AttributeError("old")), \
            patch.object(AudioUtilities, "GetDeviceEnumerator") as enumerator, \
            patch.object(km, "cast", return_value="fallback"):
        assert await ctl._get_volume_interface() == "fallback"
    enumerator.return_value.GetDefaultAudioEndpoint.assert_called_once_with(0, 1)


async def test_volume_interface_is_none_when_everything_fails(ctl: km.SafeController) -> None:
    with patch.object(pythoncom, "CoInitialize"), \
            patch.object(AudioUtilities, "GetSpeakers", side_effect=OSError("no device")), \
            patch.object(AudioUtilities, "GetDeviceEnumerator",
                         side_effect=RuntimeError("no audio")):
        assert await ctl._get_volume_interface() is None


@pytest.mark.parametrize(("action", "current", "expected"),
                         [("up", 0.5, 60), ("down", 0.5, 40), ("up", 0.95, 100), ("down", 0.05, 0)])
async def test_volume_up_and_down_move_ten_percent(ctl: km.SafeController, action: str,
                                                  current: float, expected: int) -> None:
    ctl.activate(TEST_TOKEN)
    volume = MagicMock()
    volume.GetMasterVolumeLevelScalar.return_value = current
    with patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=volume):
        assert await ctl.control_volume(action) == (f"Volume ab {expected} percent hai.",
                                                    {"percent": expected})
    assert volume.SetMasterVolumeLevelScalar.call_args.args == (pytest.approx(expected / 100),
                                                                None)


async def test_mute_and_unmute_use_the_endpoint(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    volume = MagicMock()
    with patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=volume), \
            patch.object(pyautogui, "press") as press:
        assert await ctl.control_volume("mute") == ("Volume mute ho gaya.", {})
        assert await ctl.control_volume("Unmute ") == ("Volume unmute ho gaya.", {})
    assert volume.SetMute.call_args_list == [call(1, None), call(0, None)]
    press.assert_not_called()


async def test_volume_com_error_falls_back_to_the_media_key(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    volume = MagicMock()
    volume.SetMute.side_effect = AttributeError("com fail")
    with patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=volume), \
            patch.object(pyautogui, "press") as press:
        assert await ctl.control_volume("mute") == ("Volume mute (media key fallback).", {})
    press.assert_called_once_with("volumemute")


@pytest.mark.parametrize(("action", "key"),
                         [("up", "volumeup"), ("down", "volumedown"), ("mute", "volumemute")])
async def test_media_keys_without_a_volume_interface(ctl: km.SafeController, action: str,
                                                    key: str) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=None), \
            patch.object(pyautogui, "press") as press:
        assert await ctl.control_volume(action) == (f"Volume {action} (media key fallback).", {})
    press.assert_called_once_with(key)


async def test_unknown_volume_action(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    with patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=None), \
            patch.object(pyautogui, "press") as press:
        assert await ctl.control_volume("sideways") == ("❌ Unknown volume action: sideways", {})
    press.assert_not_called()


async def test_set_volume_clamps_and_reports_the_percent(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    volume = MagicMock()
    with patch.object(pythoncom, "CoInitialize"), \
            patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=volume):
        assert await ctl.set_volume_percentage(30) == ("Volume 30 percent par set ho gaya.",
                                                       {"percent": 30})
        assert await ctl.set_volume_percentage(150) == ("Volume 100 percent par set ho gaya.",
                                                        {"percent": 100})
        assert await ctl.set_volume_percentage(-5) == ("Volume 0 percent par set ho gaya.",
                                                       {"percent": 0})
    assert volume.SetMasterVolumeLevelScalar.call_args_list == [call(0.3, None), call(1.0, None),
                                                                call(0.0, None)]


async def test_set_volume_without_an_interface(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    com_error = pywintypes.error(  # pylint: disable=no-member
        -2147417850, "CoInitialize", "Cannot change thread mode")
    with patch.object(pythoncom, "CoInitialize", side_effect=com_error), \
            patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=None):
        assert await ctl.set_volume_percentage(50) == ("❌ Volume control interface nahi mila.", {})


async def test_set_volume_error_is_reported(ctl: km.SafeController) -> None:
    ctl.activate(TEST_TOKEN)
    volume = MagicMock()
    volume.SetMasterVolumeLevelScalar.side_effect = ValueError("bad level")
    with patch.object(pythoncom, "CoInitialize"), \
            patch.object(ctl, "_get_volume_interface", new_callable=AsyncMock, return_value=volume):
        assert await ctl.set_volume_percentage(50) == ("❌ Volume set nahi ho paaya: bad level", {})


# --------------------------------------------------------------------------- tools


async def test_tools_return_tool_results_and_deactivate(tool_ctl: km.SafeController) -> None:
    volume = MagicMock()
    volume.GetMasterVolumeLevelScalar.return_value = 0.5
    with patch.object(tool_ctl, "_get_volume_interface", new_callable=AsyncMock,
                      return_value=volume), patch.object(pythoncom, "CoInitialize"), \
            patch.object(km, "clipboard_paste_text", new_callable=AsyncMock) as paste, \
            patch.object(pyautogui, "size", return_value=(1920, 1080)), \
            patch.object(pyautogui, "moveTo"), patch.object(pyautogui, "dragTo") as drag_to:
        results = {
            "move_cursor_tool": await km.move_cursor_tool("right", 10),
            "mouse_click_tool": await km.mouse_click_tool("left"),
            "scroll_cursor_tool": await km.scroll_cursor_tool("up", 5),
            "type_text_tool": await km.type_text_tool("hi"),
            "type_code_animation_tool": await km.type_code_animation_tool("x = 1"),
            "press_key_tool": await km.press_key_tool("enter"),
            "press_hotkey_tool": await km.press_hotkey_tool(["ctrl", "s"]),
            "control_volume_tool": await km.control_volume_tool("up"),
            "set_volume_tool": await km.set_volume_tool(20),
            "swipe_gesture_tool": await km.swipe_gesture_tool("left"),
        }
    for name, result in results.items():
        assert result["status"] == "success", name
        assert result["tool"] == name
    assert results["type_text_tool"]["message"] == "Text type ho gaya (2 characters)."
    assert results["control_volume_tool"]["data"] == {"percent": 60}
    assert results["set_volume_tool"]["data"] == {"percent": 20}
    assert tool_ctl.mouse.position == (110, 100)
    tool_ctl.mouse.click.assert_called_once_with(Button.left, 1)
    tool_ctl.mouse.scroll.assert_called_once_with(0, 5)
    drag_to.assert_called_once()
    paste.assert_not_awaited()
    assert tool_ctl.is_active() is False


async def test_type_text_tool_pastes_urdu(tool_ctl: km.SafeController) -> None:
    with patch.object(km, "clipboard_paste_text", new_callable=AsyncMock,
                      return_value=True) as paste:
        result = await km.type_text_tool(URDU)
    assert result == {"status": "success", "tool": "type_text_tool",
                      "message": f"Text paste ho gaya ({len(URDU)} characters)."}
    paste.assert_awaited_once_with(URDU, restore=True)
    tool_ctl.keyboard.press.assert_not_called()


async def test_tools_refuse_while_desktop_control_is_off(tool_ctl: km.SafeController,
                                                         monkeypatch: pytest.MonkeyPatch,
                                                         notify: AsyncMock) -> None:
    monkeypatch.setattr(km, "desktop_control_enabled", desktop_common.desktop_control_enabled)
    monkeypatch.setenv("JARVIS_DESKTOP_CONTROL", "0")
    calls = (
        km.move_cursor_tool("up"), km.mouse_click_tool(), km.scroll_cursor_tool("up"),
        km.type_text_tool("hi"), km.type_code_animation_tool("x"), km.press_key_tool("enter"),
        km.press_hotkey_tool(["ctrl", "c"]), km.control_volume_tool("mute"),
        km.set_volume_tool(10), km.swipe_gesture_tool("up"),
    )
    for pending in calls:
        result = await pending
        assert result["status"] == "error"
        assert result["error_code"] == "DESKTOP_CONTROL_DISABLED"
    assert tool_ctl.activation_time is None
    assert tool_ctl.keyboard.mock_calls == []
    assert tool_ctl.mouse.mock_calls == []
    notify.assert_not_called()


def test_tool_out_maps_controller_answers() -> None:
    assert km._tool_out("t", INACTIVE) == {
        "status": "error", "message": "Controller abhi active nahi hai.", "tool": "t",
        "error_code": "DESKTOP_CONTROL_DISABLED"}
    assert km._tool_out("t", "❌ Invalid key: zz") == {
        "status": "error", "message": "Invalid key: zz", "tool": "t"}
    assert km._tool_out("t", ("Volume mute ho gaya.", {})) == {
        "status": "success", "message": "Volume mute ho gaya.", "tool": "t", "data": {}}
    assert km._tool_out("t", {"status": "error", "message": "x"}) == {
        "status": "error", "message": "x", "tool": "t"}
    assert km._tool_out("t", "Key 'a' press ho gaya.")["status"] == "success"


def test_desktop_tools_are_registered_with_the_desktop_policy() -> None:
    for name in ("move_cursor_tool", "mouse_click_tool", "scroll_cursor_tool", "type_text_tool",
                 "type_code_animation_tool", "press_key_tool", "press_hotkey_tool",
                 "swipe_gesture_tool"):
        meta = plugin_manager.get_tool_meta(name)
        assert meta is not None, name
        assert meta["exclusive"] == "desktop_input", name
    text_meta = plugin_manager.get_tool_meta("type_text_tool")
    code_meta = plugin_manager.get_tool_meta("type_code_animation_tool")
    hotkey_meta = plugin_manager.get_tool_meta("press_hotkey_tool")
    assert text_meta["arg_policies"]["text"] == "raw" and text_meta["timeout"] == 60
    assert code_meta["arg_policies"]["code"] == "raw" and code_meta["timeout"] == 180
    assert callable(hotkey_meta["risk"])
