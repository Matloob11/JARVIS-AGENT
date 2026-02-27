import pytest
import asyncio
import os
from unittest.mock import MagicMock, patch, AsyncMock
import pythoncom
import pywintypes
from pynput.keyboard import Key
from pynput.mouse import Button
from keyboard_mouse_ctrl import SafeController, controller, move_cursor_tool, mouse_click_tool, scroll_cursor_tool, type_text_tool, press_key_tool, press_hotkey_tool, control_volume_tool, set_volume_tool, swipe_gesture_tool, DEFAULT_TOKEN


@pytest.fixture
def clean_controller():
    c = SafeController()
    c.active = False
    return c


@pytest.mark.asyncio
async def test_controller_activation(clean_controller):
    clean_controller.activate("wrong")
    assert clean_controller.active is False

    clean_controller.activate(DEFAULT_TOKEN)
    assert clean_controller.active is True
    assert clean_controller.is_active() is True

    clean_controller.deactivate()
    assert clean_controller.active is False


@pytest.mark.asyncio
async def test_inactive_returns(clean_controller):
    # Test all methods when inactive
    assert await clean_controller.move_cursor("up") == "🛑 Controller is inactive."
    assert await clean_controller.mouse_click() == "🛑 Controller is inactive."
    assert await clean_controller.scroll_cursor("up") == "🛑 Controller is inactive."
    assert await clean_controller.type_text("test") == "🛑 Controller is inactive."
    assert await clean_controller.press_key("enter") == "🛑 Controller is inactive."
    assert await clean_controller.press_hotkey(["ctrl", "c"]) == "🛑 Controller is inactive."
    assert await clean_controller.control_volume("up") == "🛑 Controller is inactive."
    assert await clean_controller.set_volume_percentage(50) == "🛑 Controller is inactive."
    assert await clean_controller.swipe_gesture("up") == "🛑 Controller is inactive."


@pytest.mark.asyncio
async def test_move_cursor(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch("pyautogui.size", return_value=(1920, 1080)):
        clean_controller.mouse.position = (100, 100)

        await clean_controller.move_cursor("right", 50)
        assert clean_controller.mouse.position == (150, 100)

        await clean_controller.move_cursor("left", 50)
        assert clean_controller.mouse.position == (100, 100)

        await clean_controller.move_cursor("down", 50)
        assert clean_controller.mouse.position == (100, 150)

        await clean_controller.move_cursor("up", 50)
        assert clean_controller.mouse.position == (100, 100)


@pytest.mark.asyncio
async def test_mouse_click(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.mouse, "click") as mock_click:
        await clean_controller.mouse_click("left")
        mock_click.assert_called_with(Button.left, 1)

        await clean_controller.mouse_click("right")
        mock_click.assert_called_with(Button.right, 1)

        await clean_controller.mouse_click("double")
        mock_click.assert_called_with(Button.left, 2)


@pytest.mark.asyncio
async def test_scroll_cursor(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.mouse, "scroll") as mock_scroll:
        await clean_controller.scroll_cursor("up", 10)
        mock_scroll.assert_called_with(0, 10)

        await clean_controller.scroll_cursor("down", 10)
        mock_scroll.assert_called_with(0, -10)


@pytest.mark.asyncio
async def test_scroll_cursor_fallback(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    # Simulate pynput failure
    with patch.object(clean_controller.mouse, "scroll", side_effect=RuntimeError("fail")), \
            patch("pyautogui.scroll") as mock_py_scroll:
        await clean_controller.scroll_cursor("up", 10)
        mock_py_scroll.assert_called()


@pytest.mark.asyncio
async def test_type_text_basic(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.keyboard, "press") as mock_press, \
            patch.object(clean_controller.keyboard, "release") as mock_release:
        # Use \\n and \\t to trigger those branches
        await clean_controller.type_text("a\\n\\t")
        # 'a', enter, tab
        assert mock_press.call_count >= 3


@pytest.mark.asyncio
async def test_type_text_exception_in_loop(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.keyboard, "press", side_effect=[None, ValueError("fail"), None]):
        # First char 'a' succeeds, second char 'b' fails, third char 'c' succeeds
        await clean_controller.type_text("abc")
        # Should continue to 'c' despite 'b' failing


@pytest.mark.asyncio
async def test_type_text_clipboard(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    long_text = "a" * 60
    with patch("pyperclip.copy") as mock_copy, \
            patch.object(clean_controller.keyboard, "press") as mock_press:
        await clean_controller.type_text(long_text)
        mock_copy.assert_called_with(long_text)
        mock_press.assert_any_call(Key.ctrl)


@pytest.mark.asyncio
async def test_type_text_unicode(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    unicode_text = "سلام"
    with patch("pyperclip.copy") as mock_copy, \
            patch.object(clean_controller.keyboard, "press") as mock_press:
        await clean_controller.type_text(unicode_text)
        mock_copy.assert_called()


@pytest.mark.asyncio
async def test_type_text_clipboard_fallback(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    long_text = "a" * 60
    with patch("pyperclip.copy", side_effect=ImportError("fail")), \
            patch.object(clean_controller.keyboard, "press") as mock_press:
        await clean_controller.type_text(long_text)
        # Should fallback to traditional typing
        mock_press.assert_any_call('a')


@pytest.mark.asyncio
async def test_press_key(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.keyboard, "press") as mock_press:
        await clean_controller.press_key("enter")
        mock_press.assert_called()

        res = await clean_controller.press_key("invalid_key_123")
        assert "Invalid key" in res


@pytest.mark.asyncio
async def test_press_key_exception(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.keyboard, "press", side_effect=AttributeError("fail")):
        res = await clean_controller.press_key("enter")
        assert "Failed key" in res


@pytest.mark.asyncio
async def test_press_hotkey(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller.keyboard, "press") as mock_press:
        await clean_controller.press_hotkey(["ctrl", "c"])
        assert mock_press.call_count == 2

        res = await clean_controller.press_hotkey(["ctrl", "invalid"])
        assert "Invalid key" in res


@pytest.mark.asyncio
async def test_volume_interface_branches(clean_controller):
    # Case 1: already has volume (line 77)
    with patch("pythoncom.CoInitialize"), \
            patch("keyboard_mouse_ctrl.AudioUtilities.GetSpeakers") as mock_get_speakers:
        mock_get_speakers.return_value.volume = "already_found"
        res = await clean_controller._get_volume_interface()
        assert res == "already_found"

    # Case 2: No volume, but Activate succeeds (line 81)
    with patch("keyboard_mouse_ctrl.AudioUtilities.GetSpeakers") as mock_get_speakers:
        mock_devices = MagicMock()
        mock_get_speakers.return_value = mock_devices
        # Ensure 'volume' attribute does not exist to skip lines 76-77
        if hasattr(mock_devices, 'volume'):
            del mock_devices.volume
        mock_interface = MagicMock()
        mock_devices.Activate.return_value = mock_interface
        with patch("keyboard_mouse_ctrl.cast", return_value="activated_interface"):
            res = await clean_controller._get_volume_interface()
            assert res == "activated_interface"


@pytest.mark.asyncio
async def test_volume_interface_fallback(clean_controller):
    with patch("keyboard_mouse_ctrl.AudioUtilities.GetSpeakers", side_effect=AttributeError("fail")), \
            patch("keyboard_mouse_ctrl.AudioUtilities.GetDeviceEnumerator") as mock_enum:
        mock_dev = MagicMock()
        mock_enum.return_value.GetDefaultAudioEndpoint.return_value = mock_dev
        mock_interface = MagicMock()
        mock_dev.Activate.return_value = mock_interface
        with patch("keyboard_mouse_ctrl.cast", return_value="fallback_interface"):
            res = await clean_controller._get_volume_interface()
            assert res == "fallback_interface"


@pytest.mark.asyncio
async def test_volume_interface_complete_failure(clean_controller):
    with patch("keyboard_mouse_ctrl.AudioUtilities.GetSpeakers", side_effect=AttributeError("fail")), \
            patch("keyboard_mouse_ctrl.AudioUtilities.GetDeviceEnumerator", side_effect=Exception("total fail")):
        res = await clean_controller._get_volume_interface()
        assert res is None


@pytest.mark.asyncio
async def test_control_volume_com_success(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    mock_vol = MagicMock()
    with patch.object(clean_controller, "_get_volume_interface", return_value=mock_vol):
        # Mute
        await clean_controller.control_volume("mute")
        mock_vol.SetMute.assert_called_with(1, None)
        # Unmute
        await clean_controller.control_volume("unmute")
        mock_vol.SetMute.assert_called_with(0, None)


@pytest.mark.asyncio
async def test_control_volume_com_error(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    mock_vol = MagicMock()
    mock_vol.SetMute.side_effect = AttributeError("com fail")
    with patch.object(clean_controller, "_get_volume_interface", return_value=mock_vol), \
            patch("pyautogui.press") as mock_py_press:
        await clean_controller.control_volume("mute")
        # Should fallback to pyautogui.press("volumemute")
        mock_py_press.assert_called_with("volumemute")


@pytest.mark.asyncio
async def test_control_volume_up_down(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch.object(clean_controller, "_get_volume_interface", return_value=None), \
            patch("pyautogui.press") as mock_py_press:
        await clean_controller.control_volume("up")
        mock_py_press.assert_called_with("volumeup")
        await clean_controller.control_volume("down")
        mock_py_press.assert_called_with("volumedown")


@pytest.mark.asyncio
async def test_set_volume_percentage_branches(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    # Test CoInitialize error and interface failure
    with patch("pythoncom.CoInitialize", side_effect=pywintypes.error(0, "err")), \
            patch.object(clean_controller, "_get_volume_interface", return_value=None):
        res = await clean_controller.set_volume_percentage(50)
        assert "nahi mila" in res


@pytest.mark.asyncio
async def test_set_volume_percentage_exception(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    mock_vol = MagicMock()
    mock_vol.SetMasterVolumeLevelScalar.side_effect = ValueError("fail")
    with patch.object(clean_controller, "_get_volume_interface", return_value=mock_vol):
        res = await clean_controller.set_volume_percentage(50)
        assert "nahi ho paaya" in res


@pytest.mark.asyncio
async def test_swipe_gesture_branches(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    with patch("pyautogui.size", return_value=(1000, 1000)), \
            patch("pyautogui.dragTo") as mock_drag:
        await clean_controller.swipe_gesture("up")
        await clean_controller.swipe_gesture("down")
        await clean_controller.swipe_gesture("left")
        await clean_controller.swipe_gesture("right")
        assert mock_drag.call_count == 4


@pytest.mark.asyncio
async def test_swipe_gesture_exception(clean_controller):
    clean_controller.activate(DEFAULT_TOKEN)
    from pyautogui import FailSafeException
    with patch("pyautogui.size", return_value=(1000, 1000)), \
            patch("pyautogui.moveTo", side_effect=FailSafeException()):
        # Should catch and pass
        await clean_controller.swipe_gesture("up")


@pytest.mark.asyncio
async def test_tools(clean_controller):
    with patch("keyboard_mouse_ctrl.controller", clean_controller), \
            patch("pyautogui.size", return_value=(1920, 1080)), \
            patch("keyboard_mouse_ctrl.AudioUtilities.GetSpeakers", return_value=None), \
            patch("pyautogui.press"):

        await move_cursor_tool("right", 10)
        assert clean_controller.active is False

        await mouse_click_tool("left")
        await scroll_cursor_tool("up", 5)
        await type_text_tool("hi")
        await press_key_tool("enter")
        await press_hotkey_tool(["ctrl", "s"])
        await control_volume_tool("mute")
        await set_volume_tool(20)
        await swipe_gesture_tool("left")
