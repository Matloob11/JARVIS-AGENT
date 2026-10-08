"""X-41: automate_whatsapp tool — decorator metadata, install-check gate (web fallback), desktop
control gate, FOCUS_LOST propagation and the close_after flow. whatsapp_bot itself is mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import services.automation.jarvis_whatsapp_automation as waa
from services.ai_core import jarvis_plugin_manager as pm


@pytest.fixture(autouse=True)
def _reset_install_cache():
    waa._reset_whatsapp_install_cache()
    yield
    waa._reset_whatsapp_install_cache()


@pytest.fixture(autouse=True)
def _desktop_control_enabled_by_default():
    with patch.object(waa, "desktop_control_enabled", return_value=True):
        yield


# ---------------------------------------------------------------------------
# 7. Decorator metadata (K-11): timeout, risk, raw_args, exclusive, confirm_summary
# ---------------------------------------------------------------------------


def test_automate_whatsapp_tool_metadata():
    meta = pm.plugin_manager.get_tool_meta("automate_whatsapp")
    assert meta is not None
    assert meta["timeout"] == 60.0
    assert meta["risk"] == "medium"
    assert meta["exclusive"] == "desktop_input"
    assert meta["arg_policies"].get("message") == "raw"
    assert callable(meta["confirm_summary"])


def test_confirm_summary_is_roman_urdu_with_contact_and_120_char_snippet():
    long_message = "A" * 200
    summary = waa._whatsapp_confirm_summary({"contact_name": "Ali", "message": long_message})
    assert summary.startswith("WhatsApp par Ali ko message bhejna hai: ")
    assert "A" * 120 in summary
    assert "A" * 121 not in summary


def test_confirm_summary_defaults_when_arguments_missing():
    summary = waa._whatsapp_confirm_summary({})
    assert "contact" in summary


# ---------------------------------------------------------------------------
# 8. Not installed: error_code WHATSAPP_NOT_INSTALLED, web.whatsapp.com opened, nothing sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_installed_opens_web_whatsapp_and_sends_nothing():
    with patch.object(waa, "is_whatsapp_installed", return_value=False), \
         patch.object(waa.jarvis_delivery, "open_url") as mock_open_url, \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock) as mock_open:
        result = await waa.automate_whatsapp("Ali", "Salam")

    assert result["status"] == "error"
    assert result["error_code"] == "WHATSAPP_NOT_INSTALLED"
    mock_open_url.assert_called_once_with("https://web.whatsapp.com")
    mock_open.assert_not_awaited()  # WhatsApp Desktop itself is never launched


@pytest.mark.asyncio
async def test_not_installed_web_fallback_failure_does_not_crash_the_tool():
    with patch.object(waa, "is_whatsapp_installed", return_value=False), \
         patch.object(waa.jarvis_delivery, "open_url", side_effect=OSError("no browser")):
        result = await waa.automate_whatsapp("Ali", "Salam")
    assert result["status"] == "error"
    assert result["error_code"] == "WHATSAPP_NOT_INSTALLED"


# ---------------------------------------------------------------------------
# Desktop control gate (K-25)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desktop_control_disabled_blocks_before_anything_opens():
    with patch.object(waa, "desktop_control_enabled", return_value=False), \
         patch.object(waa, "is_whatsapp_installed") as mock_installed, \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock) as mock_open:
        result = await waa.automate_whatsapp("Ali", "Salam")
    assert result["status"] == "error"
    assert result["error_code"] == "DESKTOP_CONTROL_DISABLED"
    mock_installed.assert_not_called()
    mock_open.assert_not_awaited()


# ---------------------------------------------------------------------------
# open_whatsapp() failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_failure_is_reported_and_nothing_is_sent():
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=False), \
         patch.object(waa.whatsapp_bot, "send_message", new_callable=AsyncMock) as mock_send:
        result = await waa.automate_whatsapp("Ali", "Salam")
    assert result["status"] == "error"
    assert result["error_code"] == "WHATSAPP_OPEN_FAILED"
    mock_send.assert_not_awaited()


# ---------------------------------------------------------------------------
# FOCUS_LOST bubbles straight through, no close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_focus_lost_from_send_message_is_returned_as_is_and_whatsapp_is_not_closed():
    focus_lost = waa.tool_result("error", "focus khatam", error_code="FOCUS_LOST")
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True), \
         patch.object(waa.whatsapp_bot, "send_message", new_callable=AsyncMock, return_value=focus_lost), \
         patch.object(waa.whatsapp_bot, "close_whatsapp", new_callable=AsyncMock) as mock_close:
        result = await waa.automate_whatsapp("Ali", "Salam", close_after=True)
    assert result is focus_lost
    assert result["error_code"] == "FOCUS_LOST"
    mock_close.assert_not_awaited()


# ---------------------------------------------------------------------------
# Success paths: close_after True/False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_with_close_after_true_closes_whatsapp():
    sent = waa.tool_result("success", "sent")
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True), \
         patch.object(waa.whatsapp_bot, "send_message", new_callable=AsyncMock, return_value=sent), \
         patch.object(waa.whatsapp_bot, "close_whatsapp", new_callable=AsyncMock, return_value=True) as mock_close:
        result = await waa.automate_whatsapp("Ali", "Salam", close_after=True)
    assert result["status"] == "success"
    assert result["data"]["contact"] == "Ali"
    mock_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_with_close_after_false_leaves_whatsapp_open():
    sent = waa.tool_result("success", "sent")
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True), \
         patch.object(waa.whatsapp_bot, "send_message", new_callable=AsyncMock, return_value=sent), \
         patch.object(waa.whatsapp_bot, "close_whatsapp", new_callable=AsyncMock) as mock_close:
        result = await waa.automate_whatsapp("Ali", "Salam", close_after=False)
    assert result["status"] == "success"
    mock_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_called_with_contact_and_message():
    sent = waa.tool_result("success", "sent")
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True), \
         patch.object(waa.whatsapp_bot, "send_message", new_callable=AsyncMock, return_value=sent) as mock_send, \
         patch.object(waa.whatsapp_bot, "close_whatsapp", new_callable=AsyncMock):
        await waa.automate_whatsapp("Ali Khan", "Salam Ali, 5 baje aaunga \U0001F642")
    mock_send.assert_awaited_once_with("Ali Khan", "Salam Ali, 5 baje aaunga \U0001F642")


# ---------------------------------------------------------------------------
# Unexpected exceptions never crash the tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_runtime_error_is_caught_and_reported():
    with patch.object(waa, "is_whatsapp_installed", return_value=True), \
         patch.object(waa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock,
                      side_effect=RuntimeError("boom")):
        result = await waa.automate_whatsapp("Ali", "Salam")
    assert result["status"] == "error"
    assert "masla" in result["message"]


# ---------------------------------------------------------------------------
# The real signature keeps working for every other caller in the repo
# (services.system.jarvis_window_ctrl, the live-GUI scripts under JARVIS_LIVE_GUI_TESTS).
# ---------------------------------------------------------------------------


def test_backward_compatible_bot_methods_exist_for_window_ctrl():
    assert hasattr(waa.whatsapp_bot, "open_whatsapp")
    assert hasattr(waa.whatsapp_bot, "ensure_whatsapp_focus")
    assert hasattr(waa.whatsapp_bot, "close_whatsapp")
