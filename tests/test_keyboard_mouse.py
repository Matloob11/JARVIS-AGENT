import pytest
import asyncio
from unittest.mock import MagicMock, patch
import keyboard_mouse_ctrl
from keyboard_mouse_ctrl import (
    SafeController,
    move_cursor_tool,
    mouse_click_tool,
    scroll_cursor_tool,
    type_text_tool,
    press_key_tool,
    press_hotkey_tool,
    control_volume_tool,
    DEFAULT_TOKEN
)


@pytest.fixture
def mock_pyautogui():
    with patch("keyboard_mouse_ctrl.pyautogui") as mock:
        mock.size.return_value = (1920, 1080)
        yield mock


@pytest.fixture
def mock_pynput():
    with patch("keyboard_mouse_ctrl.KeyboardController") as mock_kb:
        with patch("keyboard_mouse_ctrl.MouseController") as mock_mouse:
            yield mock_kb, mock_mouse


@pytest.fixture
def mock_volume():
    with patch("keyboard_mouse_ctrl.AudioUtilities") as mock_audio:
        with patch("keyboard_mouse_ctrl.cast") as mock_cast:
            yield mock_audio, mock_cast


@pytest.mark.asyncio
async def test_activate_deactivate():
    controller = SafeController()
    token = DEFAULT_TOKEN
    assert controller.is_active() is False
    controller.activate("wrong_token")
    assert controller.is_active() is False
    controller.activate(token)
    assert controller.is_active() is True
    controller.deactivate()
    assert controller.is_active() is False


@pytest.mark.asyncio
async def test_move_cursor_method(mock_pyautogui):
    controller = SafeController()
    controller.active = True
    controller.mouse = MagicMock()
    controller.mouse.position = (100, 100)

    await controller.move_cursor("up", 50)
    assert controller.mouse.position == (100, 50)

    await controller.move_cursor("down", 50)
    assert controller.mouse.position == (100, 100)


@pytest.mark.asyncio
async def test_mouse_click_method():
    controller = SafeController()
    controller.active = True
    controller.mouse = MagicMock()

    await controller.mouse_click("left")
    controller.mouse.click.assert_called()


@pytest.mark.asyncio
async def test_scroll_method():
    controller = SafeController()
    controller.active = True
    controller.mouse = MagicMock()

    await controller.scroll_cursor("up", 10)
    controller.mouse.scroll.assert_called_with(0, 10)


@pytest.mark.asyncio
async def test_type_text_method():
    controller = SafeController()
    controller.active = True
    controller.keyboard = MagicMock()

    await controller.type_text("Hi")
    # type_text calls press/release for each char
    assert controller.keyboard.press.call_count == 2
    assert controller.keyboard.release.call_count == 2


@pytest.mark.asyncio
async def test_move_cursor_tool(mock_pyautogui):
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await move_cursor_tool("up", 50)
            assert "Moved mouse up" in res
            # Directional moves are absolute positions in the code, but it uses self.mouse.position
            # Actually, I should check the return string mostly as moveRel isn't directly called in move_cursor
            # It sets self.mouse.position instead.


@pytest.mark.asyncio
async def test_mouse_click_tool(mock_pyautogui):
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await mouse_click_tool("left")
            assert "Left click" in res


@pytest.mark.asyncio
async def test_scroll_cursor_tool(mock_pyautogui):
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await scroll_cursor_tool("down", 10)
            assert "Scrolled down" in res


@pytest.mark.asyncio
async def test_type_text_tool():
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await type_text_tool("Hello World")
            assert "Typed: Hello World" in res


@pytest.mark.asyncio
async def test_press_key_tool():
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await press_key_tool("enter")
            assert "Key 'enter' pressed" in res


@pytest.mark.asyncio
async def test_press_hotkey_tool():
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await press_hotkey_tool(["ctrl", "c"])
            assert "Hotkey ctrl + c pressed" in res


@pytest.mark.asyncio
async def test_control_volume_tool(mock_volume):
    with patch("keyboard_mouse_ctrl.controller.activate"):
        with patch("keyboard_mouse_ctrl.controller.is_active", return_value=True):
            with patch("keyboard_mouse_ctrl.SafeController._get_volume_interface") as mock_get_iface:
                mock_iface = MagicMock()
                mock_get_iface.return_value = mock_iface

                res = await control_volume_tool("mute")
                assert "mute kar diya gaya hai" in res
                mock_iface.SetMute.assert_called_with(1, None)
