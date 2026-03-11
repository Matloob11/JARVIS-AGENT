
import os
import shutil
from unittest.mock import patch, MagicMock

import pytest
from jarvis_advanced_tools import zip_files, download_images, send_email


@pytest.mark.asyncio
@patch("shutil.make_archive")
@patch("os.path.exists")
async def test_zip_files_success(mock_exists, mock_make_archive):
    mock_exists.return_value = True
    # Test with absolute path
    result = await zip_files("D:\\TestFolder", "test.zip")
    assert result["status"] == "success"
    assert "zip" in result["zip_path"]


@pytest.mark.asyncio
@patch("requests.get")
@patch("duckduckgo_search.DDGS.images")
async def test_download_images_mock(mock_ddgs, mock_get):
    # Mock DDGS results
    mock_ddgs.return_value = [{"image": "http://example.com/test.jpg"}]

    # Mock requests response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"fake image content"
    mock_get.return_value = mock_resp

    with patch("os.makedirs"):
        with patch("builtins.open", MagicMock()):
            result = await download_images("test query", count=1)
            assert result["status"] == "success"
            assert result["downloaded_count"] == 1


@pytest.mark.asyncio
@patch("os.getenv")
@patch("smtplib.SMTP")
async def test_send_email_success(mock_smtp, mock_getenv):
    mock_getenv.side_effect = lambda k: "test@gmail.com" if "USER" in k else "password"

    # Mock SMTP instance
    instance = mock_smtp.return_value

    result = await send_email("recipient@example.com", "Subject", "Body")
    assert result["status"] == "success"
    instance.starttls.assert_called()
    instance.login.assert_called()
    instance.sendmail.assert_called()
