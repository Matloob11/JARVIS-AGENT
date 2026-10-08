
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jarvis_advanced_tools import zip_files, download_images, send_email


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    """Jarvis_Outputs in tmp_path (X-55: zip sources must sit under an allowed root, archives and downloads land
    in Jarvis_Outputs/Archives and Jarvis_Outputs/Downloads)."""
    from services.utils.jarvis_config import config

    root = tmp_path / "Jarvis_Outputs"
    root.mkdir()
    monkeypatch.setattr(config, "outputs_dir", str(root), raising=False)
    return root


@pytest.mark.asyncio
async def test_zip_files_success(outputs):
    folder = outputs / "TestFolder"
    folder.mkdir()
    (folder / "a.txt").write_text("x", encoding="utf-8")
    result = await zip_files(str(folder), "test.zip")
    assert result["status"] == "success"
    assert result["zip_path"] == os.path.join(str(outputs), "Archives", "test.zip")
    assert os.path.isfile(result["zip_path"])


@pytest.mark.asyncio
async def test_zip_files_refuses_a_folder_outside_the_allowed_roots(outputs, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = await zip_files(str(elsewhere), "test.zip")
    assert result["status"] == "error" and "Jarvis_Outputs" in result["message"]


@pytest.mark.asyncio
async def test_download_images_mock(outputs, monkeypatch):
    """X-55: the search goes through jarvis_search.search_images and only an image/* body is saved, into
    Jarvis_Outputs/Downloads/<folder>; no real request is made."""
    import jarvis_advanced_tools as tools  # the module object the functions above come from

    monkeypatch.setattr(tools, "search_images", AsyncMock(return_value=["http://example.com/test.jpg"]))
    response = MagicMock(status_code=200, headers={"Content-Type": "image/jpeg"}, content=b"fake image content")
    monkeypatch.setattr(tools.requests, "get", MagicMock(return_value=response))
    monkeypatch.setattr(tools, "_emit_download_media_update", AsyncMock())
    result = await download_images("test query", count=1)
    assert result["status"] == "success"
    assert result["downloaded_count"] == 1
    saved = list((outputs / "Downloads").rglob("*.jpg"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"fake image content"


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
