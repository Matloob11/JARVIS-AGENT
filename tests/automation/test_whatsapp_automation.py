"""X-41: WhatsApp automation primitives — install check, process-based window match, deep link /
search send, FOCUS_LOST abort, WM_CLOSE-only close. Fully mocked desktop (K-27 guard active)."""

from __future__ import annotations

import json
import shutil
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.automation.jarvis_whatsapp_automation as waa
from services.system.jarvis_desktop_common import WindowInfo


@pytest.fixture(autouse=True)
def _reset_install_cache():
    waa._reset_whatsapp_install_cache()
    yield
    waa._reset_whatsapp_install_cache()


def _win(hwnd: int, title: str, pid: int, process_name: str) -> WindowInfo:
    return WindowInfo(hwnd=hwnd, title=title, pid=pid, process_name=process_name)


# ---------------------------------------------------------------------------
# 1. is_whatsapp_installed(): %LOCALAPPDATA%/Packages/<family>, cached 60 s
# ---------------------------------------------------------------------------


def test_is_whatsapp_installed_true_when_package_dir_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(waa.config, "whatsapp_app_id", "Fam.Test_abc!App", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "Packages" / "Fam.Test_abc").mkdir(parents=True)
    assert waa.is_whatsapp_installed() is True


def test_is_whatsapp_installed_false_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(waa.config, "whatsapp_app_id", "Fam.Missing_xyz!App", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert waa.is_whatsapp_installed() is False


def test_is_whatsapp_installed_result_is_cached_for_60s(monkeypatch, tmp_path):
    monkeypatch.setattr(waa.config, "whatsapp_app_id", "Fam.Test_abc!App", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    pkg_dir = tmp_path / "Packages" / "Fam.Test_abc"
    pkg_dir.mkdir(parents=True)

    calls = []
    real_isdir = waa.os.path.isdir

    def spy_isdir(path):
        calls.append(path)
        return real_isdir(path)

    t = [1000.0]
    monkeypatch.setattr(waa.time, "monotonic", lambda: t[0])

    with patch.object(waa.os.path, "isdir", side_effect=spy_isdir):
        assert waa.is_whatsapp_installed() is True
        shutil.rmtree(pkg_dir)  # removed mid-session: the cache must still say True for now
        assert waa.is_whatsapp_installed() is True
        assert len(calls) == 1  # no second isdir call while cached

        t[0] += 61.0  # cache expired: a fresh (now False) check
        assert waa.is_whatsapp_installed() is False
        assert len(calls) == 2


def test_is_whatsapp_installed_force_refresh_bypasses_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(waa.config, "whatsapp_app_id", "Fam.Test_abc!App", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    pkg_dir = tmp_path / "Packages" / "Fam.Test_abc"
    pkg_dir.mkdir(parents=True)
    assert waa.is_whatsapp_installed() is True
    shutil.rmtree(pkg_dir)
    assert waa.is_whatsapp_installed(force_refresh=True) is False


# ---------------------------------------------------------------------------
# 2. _find_whatsapp_windows(): process-based match, never a browser
# ---------------------------------------------------------------------------


def test_find_whatsapp_windows_matches_process_and_exact_frame_host_title():
    windows = [
        _win(1, "WhatsApp", 100, "WhatsApp.exe"),
        _win(2, "anything", 101, "WhatsApp.Root.exe"),
        _win(3, "WhatsApp", 102, "ApplicationFrameHost.exe"),  # exact title: matched
        _win(4, "WhatsApp - Settings", 103, "ApplicationFrameHost.exe"),  # not exact: skipped
        _win(5, "Random", 104, "notepad.exe"),
    ]
    with patch.object(waa, "find_windows", return_value=windows):
        matches = waa._find_whatsapp_windows()
    assert {w.hwnd for w in matches} == {1, 2, 3}


@pytest.mark.parametrize("process_name", ["chrome.exe", "msedge.exe", "firefox.exe"])
def test_find_whatsapp_windows_never_matches_a_browser(process_name):
    windows = [
        _win(9, "WhatsApp", 1, process_name),  # title says WhatsApp, process is a browser
    ]
    with patch.object(waa, "find_windows", return_value=windows):
        assert waa._find_whatsapp_windows() == []


def test_find_whatsapp_windows_empty_when_none_found():
    with patch.object(waa, "find_windows", return_value=[]):
        assert waa._find_whatsapp_windows() == []


# ---------------------------------------------------------------------------
# open_whatsapp / ensure_whatsapp_focus / close_whatsapp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_whatsapp_not_installed():
    bot = waa.WhatsAppAutomation()
    with patch.object(waa, "is_whatsapp_installed", return_value=False):
        result = await bot.open_whatsapp()
    assert result is False
    assert bot.last_error == "WHATSAPP_NOT_INSTALLED"


@pytest.mark.asyncio
async def test_open_whatsapp_launches_via_shell_apps_folder_and_focuses():
    bot = waa.WhatsAppAutomation()
    win = _win(42, "WhatsApp", 1, "WhatsApp.exe")
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch("os.startfile") as mock_start, \
         patch.object(waa.WhatsAppAutomation, "_wait_for_whatsapp_window",
                      new_callable=AsyncMock, return_value=win), \
         patch.object(waa, "force_foreground", new_callable=AsyncMock, return_value=True) as mock_focus:
        result = await bot.open_whatsapp()
    assert result is True
    mock_start.assert_called_once()
    uri = mock_start.call_args[0][0]
    assert uri.startswith("shell:AppsFolder\\")
    mock_focus.assert_awaited_with(42)


@pytest.mark.asyncio
async def test_open_whatsapp_times_out_when_no_window_appears():
    bot = waa.WhatsAppAutomation()
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch("os.startfile"), \
         patch.object(waa.WhatsAppAutomation, "_wait_for_whatsapp_window",
                      new_callable=AsyncMock, return_value=None):
        result = await bot.open_whatsapp()
    assert result is False
    assert bot.last_error == "WHATSAPP_WINDOW_TIMEOUT"


@pytest.mark.asyncio
async def test_ensure_whatsapp_focus_backward_compat_for_window_ctrl():
    """services.system.jarvis_window_ctrl.open_app('whatsapp') calls this with no args."""
    bot = waa.WhatsAppAutomation()
    win = _win(3, "WhatsApp", 1, "WhatsApp.exe")
    with patch.object(waa.WhatsAppAutomation, "_wait_for_whatsapp_window",
                      new_callable=AsyncMock, return_value=win), \
         patch.object(waa, "force_foreground", new_callable=AsyncMock, return_value=True) as mock_focus:
        result = await bot.ensure_whatsapp_focus()
    assert result is True
    mock_focus.assert_awaited_with(3)


@pytest.mark.asyncio
async def test_ensure_whatsapp_focus_false_without_a_window():
    bot = waa.WhatsAppAutomation()
    with patch.object(waa.WhatsAppAutomation, "_wait_for_whatsapp_window",
                      new_callable=AsyncMock, return_value=None):
        assert await bot.ensure_whatsapp_focus() is False


@pytest.mark.asyncio
async def test_close_whatsapp_uses_wm_close_only_never_alt_f4():
    bot = waa.WhatsAppAutomation()
    win = _win(7, "WhatsApp", 1, "WhatsApp.exe")
    with patch.object(waa, "_find_whatsapp_windows", return_value=[win]), \
         patch.object(waa, "close_windows_gracefully", new_callable=AsyncMock,
                      return_value={"closed": [7], "still_open": []}) as mock_close, \
         patch.object(waa, "_pg_mod") as mock_pg_mod:
        result = await bot.close_whatsapp()
    assert result is True
    mock_close.assert_awaited_once_with([7], wait_s=2.0)
    mock_pg_mod.assert_not_called()  # never Alt+F4 via pyautogui


@pytest.mark.asyncio
async def test_close_whatsapp_reports_still_open():
    bot = waa.WhatsAppAutomation()
    win = _win(7, "WhatsApp", 1, "WhatsApp.exe")
    with patch.object(waa, "_find_whatsapp_windows", return_value=[win]), \
         patch.object(waa, "close_windows_gracefully", new_callable=AsyncMock,
                      return_value={"closed": [], "still_open": [7]}):
        result = await bot.close_whatsapp()
    assert result is False


@pytest.mark.asyncio
async def test_close_whatsapp_noop_when_no_window_open():
    bot = waa.WhatsAppAutomation()
    with patch.object(waa, "_find_whatsapp_windows", return_value=[]), \
         patch.object(waa, "close_windows_gracefully", new_callable=AsyncMock) as mock_close:
        result = await bot.close_whatsapp()
    assert result is True
    mock_close.assert_not_called()


# ---------------------------------------------------------------------------
# Contact targeting + deep link (Unicode / emoji safe)
# ---------------------------------------------------------------------------


def test_build_deep_link_strips_plus_and_urlencodes_urdu_and_emoji():
    url = waa._build_deep_link("+92 300 1234567", "Salam! \U0001F642 \u06a9\u06cc\u0633\u06d2 \u06c1\u0648\u061f")
    assert url.startswith("whatsapp://send?phone=923001234567&text=")
    encoded_text = url.split("text=", 1)[1]
    assert urllib.parse.unquote(encoded_text) == "Salam! \U0001F642 \u06a9\u06cc\u0633\u06d2 \u06c1\u0648\u061f"
    assert " " not in encoded_text  # fully percent-encoded, no raw spaces


def test_match_contact_number_fuzzy_match_and_threshold(monkeypatch, tmp_path):
    contacts_file = tmp_path / "contacts.json"
    contacts_file.write_text(json.dumps({"Ali Khan": "+923001112233"}), encoding="utf-8")
    monkeypatch.setattr(waa.config, "whatsapp_contacts_file", str(contacts_file), raising=False)

    assert waa._match_contact_number("ali khan") == "+923001112233"
    assert waa._match_contact_number("Something Totally Unrelated") is None


def test_match_contact_number_missing_file_returns_none(monkeypatch):
    monkeypatch.setattr(waa.config, "whatsapp_contacts_file", "", raising=False)
    assert waa._match_contact_number("anyone") is None


# ---------------------------------------------------------------------------
# _send_via_deep_link / _send_via_search: FOCUS_LOST and success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_via_deep_link_focus_lost_sends_nothing():
    bot = waa.WhatsAppAutomation()
    with patch("os.startfile"), \
         patch.object(waa.WhatsAppAutomation, "_wait_for_whatsapp_window",
                      new_callable=AsyncMock, return_value=None), \
         patch.object(bot, "_is_focused", new_callable=AsyncMock, return_value=False), \
         patch.object(waa, "_pg_mod") as mock_pg_mod, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await bot._send_via_deep_link("+923000000000", "hi")
    assert result["status"] == "error"
    assert result["error_code"] == "FOCUS_LOST"
    mock_pg_mod.assert_not_called()  # Enter never pressed


@pytest.mark.asyncio
async def test_send_via_deep_link_success_presses_enter_once():
    bot = waa.WhatsAppAutomation()
    win = _win(5, "WhatsApp", 1, "WhatsApp.exe")
    mock_pg = MagicMock()
    with patch("os.startfile") as mock_start, \
         patch.object(waa.WhatsAppAutomation, "_wait_for_whatsapp_window",
                      new_callable=AsyncMock, return_value=win), \
         patch.object(waa, "force_foreground", new_callable=AsyncMock), \
         patch.object(bot, "_is_focused", new_callable=AsyncMock, return_value=True), \
         patch.object(waa, "_pg_mod", return_value=mock_pg), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await bot._send_via_deep_link("+923000000000", "hi")
    assert result["status"] == "success"
    mock_pg.press.assert_called_once_with("enter")
    assert mock_start.call_args[0][0].startswith("whatsapp://send?phone=923000000000")


@pytest.mark.asyncio
async def test_send_via_search_focus_lost_before_anything_sends_nothing():
    bot = waa.WhatsAppAutomation()
    with patch.object(bot, "_is_focused", new_callable=AsyncMock, return_value=False), \
         patch.object(waa, "_pg_mod") as mock_pg_mod:
        result = await bot._send_via_search("Ali", "Salam")
    assert result["status"] == "error"
    assert result["error_code"] == "FOCUS_LOST"
    mock_pg_mod.assert_not_called()


@pytest.mark.asyncio
async def test_send_via_search_success_full_sequence():
    bot = waa.WhatsAppAutomation()
    mock_pg = MagicMock()
    order: list[tuple] = []

    async def fake_paste(text, restore=True):
        order.append(("paste", text, restore))
        return True

    def fake_save():
        order.append(("save",))
        return "ORIGINAL"

    def fake_restore(text):
        order.append(("restore", text))

    with patch.object(waa, "_pg_mod", return_value=mock_pg), \
         patch.object(bot, "_is_focused", new_callable=AsyncMock, return_value=True), \
         patch.object(waa, "clipboard_paste_text", side_effect=fake_paste), \
         patch.object(waa, "_save_clipboard_text", side_effect=fake_save), \
         patch.object(waa, "_restore_clipboard_text", side_effect=fake_restore), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await bot._send_via_search("Ali", "Salam")

    assert result["status"] == "success"
    mock_pg.hotkey.assert_called_once_with("ctrl", "f")
    press_calls = [c.args[0] for c in mock_pg.press.call_args_list]
    assert press_calls == ["down", "enter", "enter"]
    # The contact-name paste is restored at once; the message is pasted un-restored and only
    # restored after the final Enter (the send) — never before.
    assert order[0] == ("paste", "Ali", True)
    assert order[1] == ("save",)
    assert order[2] == ("paste", "Salam", False)
    assert order[-1] == ("restore", "ORIGINAL")


@pytest.mark.asyncio
async def test_send_via_search_focus_lost_before_final_send_restores_clipboard():
    bot = waa.WhatsAppAutomation()
    mock_pg = MagicMock()
    focus_sequence = [True, True, True, False]  # lost right before the final send

    async def fake_focused():
        return focus_sequence.pop(0)

    restored: list = []
    with patch.object(waa, "_pg_mod", return_value=mock_pg), \
         patch.object(bot, "_is_focused", side_effect=fake_focused), \
         patch.object(waa, "clipboard_paste_text", new_callable=AsyncMock, return_value=True), \
         patch.object(waa, "_save_clipboard_text", return_value="ORIGINAL"), \
         patch.object(waa, "_restore_clipboard_text", side_effect=lambda t: restored.append(t)), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await bot._send_via_search("Ali", "Salam")

    assert result["status"] == "error"
    assert result["error_code"] == "FOCUS_LOST"
    assert restored == ["ORIGINAL"]  # clipboard restored even though the send was aborted
    # "down" then the chat-open "enter" happened; the final (send) "enter" never did.
    press_calls = [c.args[0] for c in mock_pg.press.call_args_list]
    assert press_calls == ["down", "enter"]


def test_send_message_prefers_deep_link_when_contact_known(monkeypatch):
    bot = waa.WhatsAppAutomation()
    monkeypatch.setattr(waa, "_match_contact_number", lambda name: "+923001112233")
    seen = {}

    async def fake_deep_link(phone, message):
        seen["phone"] = phone
        seen["message"] = message
        return waa.tool_result("success", "sent")

    async def fake_search(contact_name, message):
        seen["search_called"] = True
        return waa.tool_result("success", "sent")

    async def run():
        with patch.object(bot, "_send_via_deep_link", side_effect=fake_deep_link), \
             patch.object(bot, "_send_via_search", side_effect=fake_search):
            return await bot.send_message("Ali Khan", "Salam")

    import asyncio

    result = asyncio.run(run())
    assert result["status"] == "success"
    assert seen == {"phone": "+923001112233", "message": "Salam"}


def test_send_message_falls_back_to_search_when_contact_unknown(monkeypatch):
    bot = waa.WhatsAppAutomation()
    monkeypatch.setattr(waa, "_match_contact_number", lambda name: None)
    seen = {}

    async def fake_search(contact_name, message):
        seen["contact_name"] = contact_name
        seen["message"] = message
        return waa.tool_result("success", "sent")

    async def run():
        with patch.object(bot, "_send_via_search", side_effect=fake_search):
            return await bot.send_message("Unknown Person", "Hi")

    import asyncio

    result = asyncio.run(run())
    assert result["status"] == "success"
    assert seen == {"contact_name": "Unknown Person", "message": "Hi"}
