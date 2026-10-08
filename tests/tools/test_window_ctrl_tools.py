"""X-42/X-43: window control tools including open_app app index routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.system.jarvis_window_ctrl as wc
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.system.jarvis_desktop_common import WindowInfo


def _reset_app_index_cache() -> None:
    wc._app_index_cache = None  # pylint: disable=protected-access


def _fake_index(*entries: wc._IndexedApp) -> list[wc._IndexedApp]:  # pylint: disable=protected-access
    return list(entries)


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


def test_fuzzy_index_match_requires_score_75():
    index = _fake_index(
        wc._IndexedApp("Calculator", "calculator", r"C:\Windows\System32\calc.exe", "app_path"),
        wc._IndexedApp("Notepad", "notepad", "notepad.exe", "mapping"),
    )
    entry, _ = wc._fuzzy_index_match("calculatr", index)
    assert entry is not None
    assert entry.label == "Calculator"
    miss, suggestions = wc._fuzzy_index_match("zzzzunknown", index)
    assert miss is None
    assert suggestions


def test_build_app_index_includes_utf8_start_app_name():
    fake_json = '[{"Name":"Über App","AppID":"Fake.App!App"}]'
    with patch.object(wc.subprocess, "run", return_value=MagicMock(returncode=0, stdout=fake_json)), patch.object(
        wc, "_scan_start_menu_lnks", return_value=[]
    ), patch.object(wc, "_read_app_paths_registry", return_value=[]):
        entries = wc._build_app_index_sync()
    labels = [e.label for e in entries]
    assert "Über App" in labels


@pytest.mark.asyncio
async def test_open_app_index_lnk_uses_startfile():
    _reset_app_index_cache()
    lnk = wc._IndexedApp("CalcLink", "calclink", r"C:\Start\Calc.lnk", "lnk")
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=_fake_index(lnk)), patch.object(
        wc, "_resolve_mapping_key", return_value=None
    ), patch.object(wc, "_launch_entry", new_callable=AsyncMock) as mock_launch:
        result = await wc.open_app("open calclink")
    mock_launch.assert_awaited_once_with(lnk.target, "lnk")
    assert result["status"] == "success"
    assert result["data"]["method"] == "lnk"


@pytest.mark.asyncio
async def test_open_app_chrome_uses_config_path():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch.object(
        wc.config, "chrome_path", r"C:\Chrome\chrome.exe"
    ), patch.object(wc.asyncio, "to_thread", new_callable=AsyncMock) as mock_thread:
        result = await wc.open_app("open chrome")
    mock_thread.assert_awaited()
    assert result["status"] == "success"
    assert result["data"] == {"app": "chrome", "method": "chrome_path"}


@pytest.mark.asyncio
async def test_open_app_youtube_routes_to_handle_open():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch(
        "services.automation.jarvis_youtube_automation._handle_open",
        new_callable=AsyncMock,
        return_value={"status": "success", "message": "YT ok"},
    ) as mock_yt:
        result = await wc.open_app("youtube kholo")
    mock_yt.assert_awaited_once()
    assert result["data"]["method"] == "youtube_handler"


@pytest.mark.asyncio
async def test_open_app_whatsapp_native_success():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch.object(
        wc.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True
    ), patch.object(wc.whatsapp_bot, "ensure_whatsapp_focus", new_callable=AsyncMock) as mock_focus:
        result = await wc.open_app("whatsapp")
    mock_focus.assert_awaited_once()
    assert result["status"] == "success"
    assert result["data"]["method"] == "whatsapp_native"


@pytest.mark.asyncio
async def test_open_app_whatsapp_degraded_web():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch.object(
        wc.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=False
    ), patch.object(wc, "_launch_entry", new_callable=AsyncMock) as mock_launch:
        result = await wc.open_app("whatsapp")
    mock_launch.assert_awaited_once_with("https://web.whatsapp.com", "url")
    assert result["status"] == "degraded"
    assert result["data"]["method"] == "whatsapp_web"


@pytest.mark.asyncio
async def test_open_app_office_missing_opens_web_degraded():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch.object(
        wc, "_office_installed", return_value=False
    ), patch.object(wc, "_launch_entry", new_callable=AsyncMock) as mock_launch:
        result = await wc.open_app("excel kholo")
    mock_launch.assert_awaited_once_with("https://www.office.com/launch/excel", "url")
    assert result["status"] == "degraded"
    assert result["data"]["method"] == "office_web"


@pytest.mark.asyncio
async def test_open_app_vlc_missing_uses_default_media_player():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch.object(
        wc, "_vlc_installed", return_value=False
    ), patch.object(wc.asyncio, "to_thread", new_callable=AsyncMock) as mock_thread:
        result = await wc.open_app("vlc")
    mock_thread.assert_awaited()
    assert result["status"] == "degraded"
    assert result["data"]["method"] == "media_default"


@pytest.mark.asyncio
async def test_open_app_not_found_returns_top_suggestions():
    _reset_app_index_cache()
    index = _fake_index(
        wc._IndexedApp("Calculator", "calculator", "calc.exe", "mapping"),
        wc._IndexedApp("Notepad", "notepad", "notepad.exe", "mapping"),
        wc._IndexedApp("Chrome", "chrome", "chrome.exe", "mapping"),
    )
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=index), patch.object(
        wc, "_resolve_mapping_key", return_value=None
    ), patch.object(wc, "_fuzzy_index_match", return_value=(None, ["Calculator", "Notepad", "Chrome"])):
        result = await wc.open_app("open xyzzy_nope")
    assert result["status"] == "not_found"
    assert len(result["data"]["suggestions"]) == 3


@pytest.mark.asyncio
async def test_open_app_launch_never_uses_blocking_popen_context():
    mock_proc = MagicMock()
    mock_proc.__enter__ = MagicMock(side_effect=AssertionError("blocking with Popen"))
    with patch.object(wc.subprocess, "Popen", return_value=mock_proc) as mock_popen:
        wc._launch_sync("notepad.exe", "mapping")
    mock_popen.assert_called_once()
    mock_proc.__enter__.assert_not_called()


@pytest.mark.asyncio
async def test_open_app_uwp_launches_explorer_apps_folder():
    with patch.object(wc.subprocess, "Popen") as mock_popen:
        wc._launch_sync("Contoso.App!App", "uwp")
    args = mock_popen.call_args[0][0]
    assert args[0] == "explorer.exe"
    assert "shell:AppsFolder\\Contoso.App!App" in args[1]


@pytest.mark.asyncio
async def test_open_app_write_after_open_reads_type_text_message():
    _reset_app_index_cache()
    with patch.object(wc, "_get_app_index", new_callable=AsyncMock, return_value=[]), patch.object(
        wc, "_resolve_mapping_key", return_value="notepad"
    ), patch.object(wc, "_launch_entry", new_callable=AsyncMock), patch.object(
        wc, "focus_window", new_callable=AsyncMock
    ), patch.object(
        wc, "type_text_tool", new_callable=AsyncMock, return_value={"status": "success", "message": "typed ok"}
    ):
        result = await wc.open_app("notepad write hello")
    assert "typed ok" in result["message"]
    assert result["data"]["method"] == "mapping"
