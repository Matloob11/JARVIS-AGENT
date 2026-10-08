"""X-54: send_email — UTF-8 body, attachment allow-list, SMTP auth errors."""

from __future__ import annotations

import smtplib
from email import message_from_string
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.multimedia import jarvis_advanced_tools as adv


@pytest.fixture
def smtp_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_USER", "sender@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "app-password-secret")


@pytest.mark.asyncio
@patch("services.multimedia.jarvis_advanced_tools.smtplib.SMTP")
async def test_send_email_body_newlines_preserved(mock_smtp: MagicMock, smtp_creds: None) -> None:
    instance = mock_smtp.return_value
    body = "pehli line\ndoosri line"

    result = await adv.send_email("recipient@example.com", "Test", body)

    assert result["status"] == "success"
    instance.starttls.assert_called()
    instance.login.assert_called()
    instance.sendmail.assert_called()
    raw = instance.sendmail.call_args[0][2]
    msg = message_from_string(raw)
    text_part = msg.get_payload()[0]
    decoded = text_part.get_payload(decode=True).decode("utf-8")
    assert decoded == body


@pytest.mark.asyncio
@patch("services.multimedia.jarvis_advanced_tools.smtplib.SMTP")
async def test_send_email_urdu_subject_rfc2047(mock_smtp: MagicMock, smtp_creds: None) -> None:
    instance = mock_smtp.return_value
    subject = "ٹیسٹ"

    result = await adv.send_email("recipient@example.com", subject, "body")

    assert result["status"] == "success"
    raw = instance.sendmail.call_args[0][2]
    assert "Subject:" in raw
    assert "=?utf-8?" in raw.lower() or "ٹیسٹ" in raw


@pytest.mark.asyncio
async def test_send_email_attachment_outside_allow_list(
    monkeypatch: pytest.MonkeyPatch, smtp_creds: None, tmp_path: Path,
) -> None:
    bad_file = tmp_path / "secret.txt"
    bad_file.write_text("x", encoding="utf-8")

    with patch("services.multimedia.jarvis_advanced_tools.smtplib.SMTP") as mock_smtp:
        result = await adv.send_email(
            "recipient@example.com", "hi", "body", attachment_path=str(bad_file),
        )

    assert result["status"] == "error"
    assert result.get("error_code") == "ATTACHMENT_NOT_ALLOWED"
    mock_smtp.return_value.sendmail.assert_not_called()


@pytest.mark.asyncio
@patch("services.multimedia.jarvis_advanced_tools.smtplib.SMTP")
async def test_send_email_smtp_auth_error_message(mock_smtp: MagicMock, smtp_creds: None) -> None:
    instance = mock_smtp.return_value
    instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")

    result = await adv.send_email("recipient@example.com", "Subject", "Body")

    assert result["status"] == "error"
    assert "apppasswords" in result["message"].lower()
    assert "app-password-secret" not in result["message"]


@pytest.mark.asyncio
@patch("services.multimedia.jarvis_advanced_tools.smtplib.SMTP")
async def test_send_email_allowed_attachment_under_outputs(
    mock_smtp: MagicMock, smtp_creds: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = Path(adv.config.project_root) / "Jarvis_Outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    attach = outputs / "x54_test_attach.txt"
    attach.write_text("payload", encoding="utf-8")
    try:
        result = await adv.send_email(
            "recipient@example.com", "Sub", "Body", attachment_path=str(attach),
        )
    finally:
        attach.unlink(missing_ok=True)

    assert result["status"] == "success"
    mock_smtp.return_value.sendmail.assert_called()
