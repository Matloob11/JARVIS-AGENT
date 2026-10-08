"""X-41: WhatsApp automation helpers — install check, window match, send/close (mocked)."""

from __future__ import annotations

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.automation.jarvis_whatsapp_automation as wa
from services.system.jarvis_desktop_common import WindowInfo


@pytest.fixture(autouse=True)
def _clear_install_cache() -> None:
    wa._reset_whatsapp_install_cache()
    yield
    wa._reset_whatsapp_install_cache()


def test_is_whatsapp_installed_uses_localappdata_packages(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    local = tmp_path / "LocalAppData"
    family = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
    (local / "Packages" / family).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert wa.is_whatsapp_installed(force_refresh=True) is True
    (local / "Packages" / family).rmdir()
    assert wa.is_whatsapp_installed(force_refresh=True) is False


def test_install_check_cached_60_seconds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    local = tmp_path / "LocalAppData"
    family = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
    pkg = local / "Packages" / family
    pkg.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    times = iter([100.0, 100.5, 161.0])
    monkeypatch.setattr(wa.time, "monotonic", lambda: next(times))

    assert wa.is_whatsapp_installed(force_refresh=True) is True
    pkg.rmdir()
    # Still within 60 s TTL — cached True even though folder is gone.
    assert wa.is_whatsapp_installed() is True
    # Past TTL — re-read filesystem.
    assert wa.is_whatsapp_installed() is False


def test_find_whatsapp_windows_never_matches_browsers() -> None:
    windows = [
        WindowInfo(1, "WhatsApp", 11, "chrome.exe"),
        WindowInfo(2, "WhatsApp", 12, "msedge.exe"),
        WindowInfo(3, "WhatsApp", 13, "firefox.exe"),
        WindowInfo(4, "WhatsApp", 14, "WhatsApp.exe"),
        WindowInfo(5, "WhatsApp", 15, "WhatsApp.Root.exe"),
        WindowInfo(6, "WhatsApp", 16, "ApplicationFrameHost.exe"),
        WindowInfo(7, "Other", 17, "ApplicationFrameHost.exe"),
    ]
    with patch.object(wa, "find_windows", return_value=windows):
        matches = wa._find_whatsapp_windows()
    hwnds = {w.hwnd for w in matches}
    assert hwnds == {4, 5, 6}


def test_build_deep_link_encodes_urdu_and_emoji() -> None:
    message = "سلام Sir 👋"
    url = wa._build_deep_link("+92 300 1234567", message)
    assert url.startswith("whatsapp://send?phone=923001234567&text=")
    encoded = url.split("text=", 1)[1]
    assert urllib.parse.unquote(encoded) == message


@pytest.mark.asyncio
async def test_open_whatsapp_not_installed_sets_reason() -> None:
    bot = wa.WhatsAppAutomation()
    with patch.object(wa, "is_whatsapp_installed", return_value=False):
        ok = await bot.open_whatsapp()
    assert ok is False
    assert bot.last_error == "WHATSAPP_NOT_INSTALLED"


@pytest.mark.asyncio
async def test_open_whatsapp_launches_and_foregrounds() -> None:
    bot = wa.WhatsAppAutomation()
    win = WindowInfo(99, "WhatsApp", 1, "WhatsApp.exe")
    with patch.object(wa, "is_whatsapp_installed", return_value=True):
        with patch("os.startfile") as mock_start:
            with patch.object(bot, "_wait_for_whatsapp_window", new_callable=AsyncMock, return_value=win):
                with patch.object(wa, "force_foreground", new_callable=AsyncMock, return_value=True) as mock_fg:
                    ok = await bot.open_whatsapp()
    assert ok is True
    mock_start.assert_called_once()
    assert "shell:AppsFolder" in mock_start.call_args[0][0]
    mock_fg.assert_awaited_once_with(99)


@pytest.mark.asyncio
async def test_close_whatsapp_uses_wm_close_helper_not_alt_f4() -> None:
    bot = wa.WhatsAppAutomation()
    win = WindowInfo(42, "WhatsApp", 1, "WhatsApp.exe")
    mock_pg = MagicMock()
    with patch.object(wa, "_find_whatsapp_windows", return_value=[win]):
        with patch.object(wa, "close_windows_gracefully", new_callable=AsyncMock, return_value={"closed": [42], "still_open": []}) as mock_close:
            with patch.object(wa, "_pg_mod", return_value=mock_pg):
                ok = await bot.close_whatsapp()
    assert ok is True
    mock_close.assert_awaited_once_with([42], wait_s=2.0)
    mock_pg.hotkey.assert_not_called()


@pytest.mark.asyncio
async def test_send_via_search_focus_lost_before_enter() -> None:
    bot = wa.WhatsAppAutomation()
    mock_pg = MagicMock()
    with patch.object(wa, "_pg_mod", return_value=mock_pg):
        with patch.object(wa, "_is_whatsapp_foreground", side_effect=[True, False]):
            with patch.object(wa, "clipboard_paste_text", new_callable=AsyncMock):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await bot._send_via_search("Ali", "Hello")
    assert result["error_code"] == "FOCUS_LOST"
    assert not any(call.args == ("enter",) for call in mock_pg.press.call_args_list)


@pytest.mark.asyncio
async def test_send_via_search_restores_clipboard_on_focus_lost_after_paste() -> None:
    bot = wa.WhatsAppAutomation()
    mock_pg = MagicMock()
    saved = "previous clip"
    with patch.object(wa, "_pg_mod", return_value=mock_pg):
        with patch.object(wa, "_is_whatsapp_foreground", side_effect=[True, True, True, False]):
            with patch.object(wa, "clipboard_paste_text", new_callable=AsyncMock):
                with patch.object(wa, "_save_clipboard_text", return_value=saved):
                    with patch.object(wa, "_restore_clipboard_text") as mock_restore:
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            result = await bot._send_via_search("Ali", "Hello")
    assert result["error_code"] == "FOCUS_LOST"
    mock_restore.assert_called_once_with(saved)


@pytest.mark.asyncio
async def test_send_via_deep_link_focus_lost_aborts_before_enter() -> None:
    bot = wa.WhatsAppAutomation()
    mock_pg = MagicMock()
    with patch("os.startfile"):
        with patch.object(bot, "_wait_for_whatsapp_window", new_callable=AsyncMock, return_value=None):
            with patch.object(wa, "force_foreground", new_callable=AsyncMock):
                with patch.object(bot, "_is_focused", new_callable=AsyncMock, return_value=False):
                    with patch.object(wa, "_pg_mod", return_value=mock_pg):
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            result = await bot._send_via_deep_link("+923001234567", "سلام")
    assert result["error_code"] == "FOCUS_LOST"
    mock_pg.press.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_uses_deep_link_when_contact_resolves() -> None:
    bot = wa.WhatsAppAutomation()
    with patch.object(wa, "_match_contact_number", return_value="+923001234567"):
        with patch.object(bot, "_send_via_deep_link", new_callable=AsyncMock, return_value=wa.tool_result("success", "sent")) as mock_deep:
            with patch.object(bot, "_send_via_search", new_callable=AsyncMock) as mock_search:
                await bot.send_message("Ali", "Hi")
    mock_deep.assert_awaited_once()
    mock_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_match_contact_fuzzy_90_plus(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    contacts_file = tmp_path / "contacts.json"
    contacts_file.write_text('{"Matloob Khan": "+923001111111"}', encoding="utf-8")
    monkeypatch.setattr(wa.config, "whatsapp_contacts_file", str(contacts_file))
    number = wa._match_contact_number("Matloob Kahn")
    assert number == "+923001111111"
