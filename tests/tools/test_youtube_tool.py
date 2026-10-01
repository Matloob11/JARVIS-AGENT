"""YouTube play tool: watch URL resolution (yt-dlp mocked — no live YouTube)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.automation import jarvis_youtube_automation as yt_mod
from services.automation.jarvis_youtube_automation import YouTubeAutomation, automate_youtube


def _mock_ytdl(entries: list[dict] | None) -> MagicMock:
    mock_ydl_cls = MagicMock()
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.__exit__.return_value = False
    mock_ydl.extract_info.return_value = {"entries": entries} if entries is not None else {}
    mock_ydl_cls.return_value = mock_ydl
    return mock_ydl_cls


@pytest.mark.asyncio
async def test_get_video_url_returns_watch_url() -> None:
    bot = YouTubeAutomation()
    with patch.object(yt_mod, "yt_dlp") as mock_yt_dlp:
        mock_yt_dlp.YoutubeDL = _mock_ytdl([{"id": "abc123", "title": "Lofi Mix"}])
        url = await bot.get_video_url("lofi hip hop")
    assert url == "https://www.youtube.com/watch?v=abc123"


@pytest.mark.asyncio
async def test_get_video_url_none_when_no_results() -> None:
    bot = YouTubeAutomation()
    with patch.object(yt_mod, "yt_dlp") as mock_yt_dlp:
        mock_yt_dlp.YoutubeDL = _mock_ytdl([])
        with (
            patch.object(yt_mod, "YoutubeSearch", None),
            patch.object(
                YouTubeAutomation,
                "_youtube_search_pkg_sync",
                staticmethod(lambda _q: None),
            ),
            patch.object(
                YouTubeAutomation,
                "_youtubesearchpython_sync",
                staticmethod(lambda _q: None),
            ),
        ):
            url = await bot.get_video_url("zzzz_no_hits_xyz")
    assert url is None


@pytest.mark.asyncio
async def test_get_video_url_never_search_results_url() -> None:
    bot = YouTubeAutomation()
    with patch.object(yt_mod, "yt_dlp") as mock_yt_dlp:
        mock_yt_dlp.YoutubeDL = _mock_ytdl([{"id": "vid99", "title": "Song"}])
        url = await bot.get_video_url("tajdar e haram")
    assert url is not None
    assert "results?search_query" not in url
    assert url.startswith("https://www.youtube.com/watch?v=")


@pytest.mark.asyncio
async def test_automate_play_tool_result_shape() -> None:
    meta = {"url": "https://www.youtube.com/watch?v=xyz", "video_id": "xyz", "title": "Track"}
    with (
        patch.object(yt_mod.yt_bot, "resolve_video", return_value=meta),
        patch.object(yt_mod.yt_bot, "open_url_in_app", return_value=True),
    ):
        res = await automate_youtube("play", "cool song")
    assert res["status"] == "success"
    assert res["data"]["video_id"] == "xyz"
    assert res["url"] == meta["url"]
    assert res["action"] == "play"
    assert res["query"] == "cool song"
