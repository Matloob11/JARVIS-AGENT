"""X-42: close_window, close alias, minimize/maximize ToolResult, active window context."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.system.jarvis_window_ctrl as wc
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.system.jarvis_desktop_common import WindowInfo


def _calc() -> WindowInfo:
    return WindowInfo(101, "Calculator", 500, "CalculatorApp.exe")


def _chrome() -> WindowInfo:
    return WindowInfo(202, "Google Chrome", 600, "chrome.exe")


def _protected() -> WindowInfo:
    return WindowInfo(303, "JARVIS-AGENT", 1, "electron.exe")


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"window_name": "calculator", "force": False}, "low"),
        ({"window_name": "calculator", "force": True}, "medium"),
        ({"window_name": "chrome", "force": False}, "medium"),
    ],
)
def test_close_window_risk_levels(arguments, expected):
    with patch.object(wc, "_safe_window_matches") as mock_match:
        if arguments.get("force"):
            mock_match.return_value = [_calc()]
        elif "chrome" in arguments["window_name"]:
            mock_match.return_value = [_chrome()]
        else:
            mock_match.return_value = [_calc()]
        assert wc._close_window_risk(arguments) == expected


def test_close_window_risk_medium_when_multiple_matches():
    with patch.object(wc, "_safe_window_matches", return_value=[_calc(), _calc()]):
        assert wc._close_window_risk({"window_name": "calc", "force": False}) == "medium"


def test_safe_matches_excludes_protected_windows():
    with patch.object(wc, "_match_windows_by_name", return_value=[_calc(), _protected()]):
        safe = wc._safe_window_matches("calculator")
    assert len(safe) == 1
    assert safe[0].title == "Calculator"


@pytest.mark.asyncio
async def test_close_window_graceful_without_taskkill():
    with patch.object(wc, "_safe_window_matches", return_value=[_calc()]), patch.object(
        wc, "close_windows_gracefully", new_callable=AsyncMock, return_value={"closed": [101], "still_open": []}
    ) as mock_close, patch.object(wc, "_force_kill_still_open", new_callable=AsyncMock) as mock_kill:
        result = await wc.close_window("calculator", force=False)
    mock_close.assert_awaited_once()
    mock_kill.assert_not_awaited()
    assert result["status"] == "success"
    assert result["data"]["closed_titles"] == ["Calculator"]
    assert result["data"]["still_open"] == []


@pytest.mark.asyncio
async def test_close_window_force_calls_taskkill_path():
    with patch.object(wc, "_safe_window_matches", return_value=[_calc()]), patch.object(
        wc,
        "close_windows_gracefully",
        new_callable=AsyncMock,
        return_value={"closed": [], "still_open": [101]},
    ), patch.object(
        wc, "_force_kill_still_open", new_callable=AsyncMock, return_value=[]
    ) as mock_kill:
        result = await wc.close_window("calculator", force=True)
    mock_kill.assert_awaited_once()
    assert result["status"] == "error"
    assert result["data"]["still_open"] == []


@pytest.mark.asyncio
async def test_close_alias_routes_to_close_window():
    with patch.object(wc, "close_window", new_callable=AsyncMock) as mock_cw:
        mock_cw.return_value = {"status": "success", "message": "ok", "data": {}}
        out = await wc.close("calculator")
    mock_cw.assert_awaited_once_with("calculator")
    assert out["status"] == "success"


def test_close_and_close_window_registered():
    names = {getattr(t, "__name__", "") for t in plugin_manager.get_tools()}
    assert "close" in names
    assert "close_window" in names


@pytest.mark.asyncio
async def test_minimize_window_returns_tool_result_shape():
    target = _calc()
    mock_win = MagicMock()
    with patch.object(wc, "_resolve_window_target", new_callable=AsyncMock, return_value=target), patch.object(
        wc, "_pygetwindow_for_hwnd", return_value=mock_win
    ):
        result = await wc.minimize_window("calculator")
    mock_win.minimize.assert_called_once()
    assert result["status"] == "success"
    assert "message" in result
    assert isinstance(result.get("data"), dict)


@pytest.mark.asyncio
async def test_maximize_active_skips_protected_match():
    with patch.object(wc, "_resolve_window_target", new_callable=AsyncMock, return_value=None):
        result = await wc.maximize_window("JARVIS-AGENT")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_get_active_window_context_includes_pid_and_process_name():
    active = MagicMock()
    active.title = "Calculator"
    active.isMaximized = True
    active.isMinimized = False
    info = WindowInfo(101, "Calculator", 500, "CalculatorApp.exe")
    with patch.object(wc, "_load_gw") as mock_load, patch.object(wc, "_hwnd_to_window_info", return_value=info):
        mock_load.return_value.getActiveWindow.return_value = active
        ctx = await wc.get_active_window_context()
    assert ctx["status"] == "success"
    assert ctx["title"] == "Calculator"
    assert ctx["is_maximized"] is True
    assert ctx["is_minimized"] is False
    assert ctx["pid"] == 500
    assert ctx["process_name"] == "CalculatorApp.exe"
    assert "message" in ctx
