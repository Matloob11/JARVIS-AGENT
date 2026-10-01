import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import services.automation.keyboard_mouse_ctrl as keyboard_mouse_ctrl
from services.automation.keyboard_mouse_ctrl import (
    SafeController,
    control_volume_tool,
    move_cursor_tool,
    mouse_click_tool,
    press_hotkey_tool,
    press_key_tool,
    scroll_cursor_tool,
    type_text_tool,
)


@pytest.fixture(autouse=True)
def _stable_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyboard_mouse_ctrl, "DEFAULT_TOKEN", "test-controller-token")


@pytest.fixture
def mock_pyautogui():
    with patch("services.automation.keyboard_mouse_ctrl.pyautogui") as mock:
        mock.size.return_value = (1920, 1080)
        yield mock


@pytest.fixture
def mock_pynput():
    with patch("services.automation.keyboard_mouse_ctrl.KeyboardController") as mock_kb:
        with patch("services.automation.keyboard_mouse_ctrl.MouseController") as mock_mouse:
            yield mock_kb, mock_mouse


@pytest.fixture
def mock_volume():
    with patch("services.automation.keyboard_mouse_ctrl.AudioUtilities") as mock_audio:
        with patch("services.automation.keyboard_mouse_ctrl.cast") as mock_cast:
            yield mock_audio, mock_cast


@pytest.mark.asyncio
async def test_activate_deactivate():
    controller = SafeController()
    token = keyboard_mouse_ctrl.DEFAULT_TOKEN
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
    assert controller.keyboard.press.call_count == 2
    assert controller.keyboard.release.call_count == 2


@pytest.mark.asyncio
async def test_move_cursor_tool(mock_pyautogui):
    with patch("services.automation.keyboard_mouse_ctrl.controller.activate"):
        with patch("services.automation.keyboard_mouse_ctrl.controller.is_active", return_value=True):
            res = await move_cursor_tool("up", 50)
            assert res["status"] == "success"
            assert "move" in res["message"].lower()


@pytest.mark.asyncio
async def test_mouse_click_tool(mock_pyautogui):
    with patch.object(
        keyboard_mouse_ctrl.controller,
        "mouse_click",
        new=AsyncMock(return_value="Left click ho gaya."),
    ):
        res = await mouse_click_tool("left")
        assert res["status"] == "success"
        assert "click" in res["message"].lower()


@pytest.mark.asyncio
async def test_scroll_cursor_tool(mock_pyautogui):
    with patch.object(
        keyboard_mouse_ctrl.controller,
        "scroll_cursor",
        new=AsyncMock(return_value="Scroll down ho gaya."),
    ):
        res = await scroll_cursor_tool("down", 10)
        assert res["status"] == "success"
        assert "scroll" in res["message"].lower()


@pytest.mark.asyncio
async def test_type_text_tool():
    with patch.object(
        keyboard_mouse_ctrl.controller,
        "type_text",
        new=AsyncMock(return_value="Text type ho gaya (11 characters)."),
    ):
        res = await type_text_tool("Hello World")
        assert res["status"] == "success"
        assert "characters" in res["message"]


@pytest.mark.asyncio
async def test_press_key_tool():
    with patch.object(
        keyboard_mouse_ctrl.controller,
        "press_key",
        new=AsyncMock(return_value="Key 'enter' press ho gaya."),
    ):
        res = await press_key_tool("enter")
        assert res["status"] == "success"
        assert "enter" in res["message"].lower()


@pytest.mark.asyncio
async def test_press_hotkey_tool():
    with patch.object(
        keyboard_mouse_ctrl.controller,
        "press_hotkey",
        new=AsyncMock(return_value="Hotkey ctrl + c press ho gaya."),
    ):
        res = await press_hotkey_tool(["ctrl", "c"])
        assert res["status"] == "success"
        assert "ctrl" in res["message"].lower()


@pytest.mark.asyncio
async def test_control_volume_tool(mock_volume):
    with patch("services.automation.keyboard_mouse_ctrl.controller.activate"):
        with patch("services.automation.keyboard_mouse_ctrl.controller.is_active", return_value=True):
            with patch(
                "services.automation.keyboard_mouse_ctrl.SafeController._get_volume_interface"
            ) as mock_get_iface:
                mock_iface = MagicMock()
                mock_get_iface.return_value = mock_iface

                res = await control_volume_tool("mute")
                assert res["status"] == "success"
                assert "mute" in res["message"].lower()
                mock_iface.SetMute.assert_called_with(1, None)
