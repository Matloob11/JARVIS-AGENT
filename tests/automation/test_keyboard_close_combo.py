"""Keyboard close-combo subset guard (X-65 handoff)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import services.automation.keyboard_mouse_ctrl as km
from services.system.jarvis_desktop_common import WindowInfo


@pytest.fixture(autouse=True)
def _stable_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(km, "DEFAULT_TOKEN", "test-controller-token", raising=False)


@pytest.mark.parametrize(
    "keys",
    [
        ["alt", "f4", "enter"],
        ["shift", "alt", "f4"],
        ["ctrl", "shift", "w", "tab"],
    ],
)
def test_is_close_combo_treats_superset_as_close(keys: list[str]) -> None:
    assert km._is_close_combo(keys) is True


@pytest.mark.asyncio
async def test_ctrl_shift_w_blocked_on_protected_window() -> None:
    protected = WindowInfo(hwnd=2, title="JARVIS-AGENT", pid=1, process_name="electron.exe")
    with patch.object(km, "_foreground_window_info", return_value=protected), patch.object(
        km.controller, "press_hotkey", new_callable=AsyncMock
    ) as mock_hotkey, patch.object(km, "desktop_control_enabled", return_value=True):
        result = await km.press_hotkey_tool(["ctrl", "shift", "w", "enter"])
    mock_hotkey.assert_not_awaited()
    assert result["status"] == "error"
