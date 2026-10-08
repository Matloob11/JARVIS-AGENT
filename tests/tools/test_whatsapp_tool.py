"""X-41: automate_whatsapp tool — install gate, metadata, mocked happy path."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.automation.jarvis_whatsapp_automation as wa
from services.ai_core import jarvis_plugin_manager as pm


def test_automate_whatsapp_tool_metadata() -> None:
    meta = pm.plugin_manager.get_tool_meta("automate_whatsapp")
    assert meta is not None
    assert meta["timeout"] == 60
    assert meta["risk"] == "medium"
    assert meta["exclusive"] == "desktop_input"
    assert meta["arg_policies"].get("message") == "raw"
    assert callable(meta["confirm_summary"])


def test_whatsapp_confirm_summary_truncates_message() -> None:
    long_msg = "x" * 200
    summary = wa._whatsapp_confirm_summary({"contact_name": "Ali", "message": long_msg})
    assert summary.startswith("WhatsApp par Ali ko message bhejna hai:")
    assert len(summary) < len(long_msg) + 40


@pytest.mark.asyncio
async def test_not_installed_opens_web_and_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    wa._reset_whatsapp_install_cache()
    monkeypatch.setattr(wa, "is_whatsapp_installed", lambda **_: False)
    open_url = MagicMock()
    monkeypatch.setattr(wa.jarvis_delivery, "open_url", open_url)
    with patch.object(wa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock) as mock_open:
        result = await wa.automate_whatsapp("Ali", "سلام")
    assert result["status"] == "error"
    assert result["error_code"] == "WHATSAPP_NOT_INSTALLED"
    open_url.assert_called_once_with("https://web.whatsapp.com")
    mock_open.assert_not_awaited()


@pytest.mark.asyncio
async def test_desktop_control_disabled() -> None:
    with patch.object(wa, "desktop_control_enabled", return_value=False):
        result = await wa.automate_whatsapp("Ali", "Hi")
    assert result["error_code"] == "DESKTOP_CONTROL_DISABLED"


@pytest.mark.asyncio
async def test_installed_success_path_mocks_send_and_close() -> None:
    with patch.object(wa, "is_whatsapp_installed", return_value=True):
        with patch.object(wa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True):
            with patch.object(
                wa.whatsapp_bot,
                "send_message",
                new_callable=AsyncMock,
                return_value=wa.tool_result("success", "sent"),
            ) as mock_send:
                with patch.object(wa.whatsapp_bot, "close_whatsapp", new_callable=AsyncMock) as mock_close:
                    result = await wa.automate_whatsapp("Ali", "Main 5 baje aaunga", close_after=True)
    assert result["status"] == "success"
    mock_send.assert_awaited_once_with("Ali", "Main 5 baje aaunga")
    mock_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_failure_is_not_wrapped_as_success() -> None:
    focus_err = wa.tool_result("error", "focus", error_code="FOCUS_LOST")
    with patch.object(wa, "is_whatsapp_installed", return_value=True):
        with patch.object(wa.whatsapp_bot, "open_whatsapp", new_callable=AsyncMock, return_value=True):
            with patch.object(wa.whatsapp_bot, "send_message", new_callable=AsyncMock, return_value=focus_err):
                with patch.object(wa.whatsapp_bot, "close_whatsapp", new_callable=AsyncMock) as mock_close:
                    result = await wa.automate_whatsapp("Ali", "Hi")
    assert result["error_code"] == "FOCUS_LOST"
    mock_close.assert_not_awaited()
