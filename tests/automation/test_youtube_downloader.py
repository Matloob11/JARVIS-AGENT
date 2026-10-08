import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.multimedia.jarvis_youtube_downloader import (
    YouTubeDownloader,
    download_youtube_media,
)


@pytest.fixture
def mock_yt_bot():
    with patch("services.multimedia.jarvis_youtube_downloader.yt_bot") as mock:
        mock.get_video_url = AsyncMock()
        mock.open_url_in_app = AsyncMock()
        yield mock


class _FakeStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self._idx = 0

    async def readline(self) -> bytes:
        if self._idx >= len(self._lines):
            return b""
        line = self._lines[self._idx]
        self._idx += 1
        await asyncio.sleep(0)
        return line


class FakeProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[bytes] | None = None,
        stderr_lines: list[bytes] | None = None,
        returncode: int = 0,
    ):
        self.stdout = _FakeStream(stdout_lines or [])
        self.stderr = _FakeStream(stderr_lines or [])
        self._returncode = returncode
        self.returncode: int | None = None
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self._returncode
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


@pytest.fixture
def mock_subprocess_exec():
    with patch(
        "services.multimedia.jarvis_youtube_downloader.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


def test_is_valid_url():
    dl = YouTubeDownloader()
    assert dl.is_valid_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert dl.is_valid_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert dl.is_valid_url("not a url") is False


def test_resolve_target_search():
    dl = YouTubeDownloader()
    assert dl.resolve_target("some song") == "ytsearch1:some song"
    watch = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    assert dl.resolve_target(watch) == watch


@pytest.mark.asyncio
async def test_download_success(mock_subprocess_exec):
    dl = YouTubeDownloader()
    mock_subprocess_exec.return_value = FakeProcess(
        stdout_lines=[b"C:/tmp/video.mp3\n"],
        returncode=0,
    )

    with patch("os.path.exists", return_value=True):
        with patch(
            "services.multimedia.jarvis_youtube_downloader.jarvis_delivery.open_artifact"
        ) as mock_open:
            res = await dl.download("https://youtube.com/watch?v=123", "audio")
            assert res["status"] == "success"
            assert res["file_path"] == "C:/tmp/video.mp3"
            mock_open.assert_called()
    args = mock_subprocess_exec.await_args[0]
    assert args[0] == __import__("sys").executable
    assert "-m" in args and "yt_dlp" in args


@pytest.mark.asyncio
async def test_download_query_uses_ytsearch(mock_subprocess_exec, mock_yt_bot):
    dl = YouTubeDownloader()
    mock_subprocess_exec.return_value = FakeProcess(
        stdout_lines=[b"file.mp3\n"],
        returncode=0,
    )

    with patch("os.path.exists", return_value=True):
        with patch(
            "services.multimedia.jarvis_youtube_downloader.jarvis_delivery.open_artifact"
        ):
            res = await dl.download("some song")
            assert res["status"] == "success"
            mock_yt_bot.get_video_url.assert_not_awaited()
    cmd = mock_subprocess_exec.await_args[0]
    assert cmd[-1] == "ytsearch1:some song"


@pytest.mark.asyncio
async def test_download_error_fallback(mock_subprocess_exec, mock_yt_bot):
    dl = YouTubeDownloader()
    mock_subprocess_exec.return_value = FakeProcess(
        stderr_lines=[b"some error\n"],
        returncode=1,
    )
    mock_yt_bot.open_url_in_app.return_value = True

    res = await dl.download("https://youtube.com/watch?v=123")
    assert "Browser mein play" in res["message"]
    mock_yt_bot.open_url_in_app.assert_awaited()


@pytest.mark.asyncio
async def test_download_error_fallback_fails_when_browser_unavailable(
    mock_subprocess_exec, mock_yt_bot
):
    dl = YouTubeDownloader()
    mock_subprocess_exec.return_value = FakeProcess(returncode=1)
    mock_yt_bot.open_url_in_app.return_value = False

    res = await dl.download("https://youtube.com/watch?v=123")
    assert res["status"] == "error"
    assert "Browser fallback" in res["message"]


@pytest.mark.asyncio
async def test_tool_download_youtube_media_starts_job():
    with patch(
        "services.multimedia.jarvis_youtube_downloader._start_job_available",
        return_value=True,
    ):
        with patch(
            "services.utils.jarvis_jobs.start_job", return_value="job-abc123"
        ) as start_job:
            res = await download_youtube_media("song", "audio")
            assert res["status"] == "started"
            assert res["job_id"] == "job-abc123"
            start_job.assert_called_once()


@pytest.mark.asyncio
async def test_tool_sync_fallback_when_jobs_missing():
    with patch(
        "services.multimedia.jarvis_youtube_downloader._start_job_available",
        return_value=False,
    ):
        with patch(
            "services.multimedia.jarvis_youtube_downloader.yt_downloader.download",
            new_callable=AsyncMock,
        ) as m_dl:
            m_dl.return_value = {"status": "success", "message": "Done"}
            res = await download_youtube_media("song", "audio")
            assert res["status"] == "success"
            m_dl.assert_awaited_with("song", "audio", auto_open=True)
