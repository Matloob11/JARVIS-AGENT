import pytest
import asyncio
import os
from unittest.mock import MagicMock, patch, mock_open, AsyncMock
import jarvis_youtube_downloader
from jarvis_youtube_downloader import YouTubeDownloader, download_youtube_media


@pytest.fixture
def mock_yt_bot():
    with patch("jarvis_youtube_downloader.yt_bot") as mock:
        mock.get_video_url = AsyncMock()
        mock.open_url_in_app = AsyncMock()
        yield mock


@pytest.fixture
def mock_subprocess():
    with patch("jarvis_youtube_downloader.subprocess.run") as mock:
        yield mock


def test_is_valid_url():
    dl = YouTubeDownloader()
    assert dl.is_valid_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert dl.is_valid_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert dl.is_valid_url("not a url") is False


@pytest.mark.asyncio
async def test_download_success(mock_subprocess):
    dl = YouTubeDownloader()
    mock_subprocess.return_value = MagicMock(
        stdout="/path/to/video.mp3", check=True)

    with patch("os.path.exists", return_value=True):
        with patch("os.startfile") as mock_start:
            res = await dl.download("https://youtube.com/watch?v=123", "audio")
            assert res['status'] == "success"
            assert "/path/to/video.mp3" in res['file_path']
            mock_start.assert_called_with("/path/to/video.mp3")


@pytest.mark.asyncio
async def test_download_query(mock_yt_bot, mock_subprocess):
    dl = YouTubeDownloader()
    mock_yt_bot.get_video_url = AsyncMock(
        return_value="https://youtube.com/watch?v=123")
    mock_subprocess.return_value = MagicMock(stdout="file.mp3", check=True)

    with patch("os.path.exists", return_value=True):
        with patch("os.startfile"):
            res = await dl.download("some song")
            assert res['status'] == "success"
            mock_yt_bot.get_video_url.assert_awaited_with("some song")


@pytest.mark.asyncio
async def test_download_error_fallback(mock_subprocess, mock_yt_bot):
    dl = YouTubeDownloader()
    # Simulate failed yt-dlp call
    import subprocess
    mock_subprocess.side_effect = subprocess.CalledProcessError(
        1, "cmd", stderr="some error")

    mock_yt_bot.open_url_in_app.return_value = True
    mock_yt_bot.get_video_url.return_value = "https://youtube.com/watch?v=12345678901"

    res = await dl.download("https://youtube.com/watch?v=123")
    assert "Browser mein play" in res['message']
    mock_yt_bot.open_url_in_app.assert_awaited()


@pytest.mark.asyncio
async def test_tool_download_youtube_media():
    with patch("jarvis_youtube_downloader.yt_downloader.download", new_callable=AsyncMock) as m_dl:
        m_dl.return_value = {"status": "success", "message": "Done"}
        res = await download_youtube_media("song", "audio")
        assert res['status'] == "success"
        m_dl.assert_awaited_with("song", "audio")
