import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import jarvis_youtube_automation
from jarvis_youtube_automation import YouTubeAutomation, automate_youtube


@pytest.fixture
def mock_yt_search():
    with patch("jarvis_youtube_automation.YoutubeSearch") as mock:
        yield mock


@pytest.fixture
def mock_shutil():
    with patch("jarvis_youtube_automation.shutil") as mock:
        yield mock


@pytest.fixture
def mock_popen():
    with patch("jarvis_youtube_automation.subprocess.Popen") as mock:
        yield mock


@pytest.fixture
def mock_webbrowser():
    with patch("jarvis_youtube_automation.webbrowser") as mock:
        yield mock


@pytest.mark.asyncio
async def test_get_video_url(mock_yt_search):
    bot = YouTubeAutomation()
    mock_inst = mock_yt_search.return_value
    mock_inst.to_dict.return_value = [{'id': 'test_id'}]

    url = await bot.get_video_url("test video")
    assert url == "https://www.youtube.com/watch?v=test_id"


@pytest.mark.asyncio
async def test_open_url_in_app_edge(mock_shutil, mock_popen):
    bot = YouTubeAutomation()
    mock_shutil.which.return_value = "/path/to/edge"
    with patch("os.name", "nt"):
        res = await bot.open_url_in_app("https://youtube.com")
        assert res is True
        mock_popen.assert_called()


@pytest.mark.asyncio
async def test_open_url_in_app_fallback(mock_shutil, mock_webbrowser):
    bot = YouTubeAutomation()
    mock_shutil.which.return_value = None
    with patch("os.name", "posix"):
        res = await bot.open_url_in_app("https://youtube.com")
        assert res is True
        mock_webbrowser.open.assert_called_with("https://youtube.com")


@pytest.mark.asyncio
async def test_automate_youtube_play():
    with patch("jarvis_youtube_automation.yt_bot.get_video_url", new_callable=AsyncMock) as m_get:
        with patch("jarvis_youtube_automation.yt_bot.open_url_in_app", new_callable=AsyncMock) as m_open:
            m_get.return_value = "https://youtube.com/watch?v=123"
            res = await automate_youtube("play", "cool song")
            assert res['status'] == "success"
            assert "play kar raha hoon" in res['message']
            m_open.assert_awaited_with("https://youtube.com/watch?v=123")


@pytest.mark.asyncio
async def test_automate_youtube_search():
    with patch("jarvis_youtube_automation.yt_bot.open_url_in_app", new_callable=AsyncMock) as m_open:
        res = await automate_youtube("search", "how to code")
        assert res['status'] == "success"
        assert "search kar raha hoon" in res['message']
        m_open.assert_awaited()
        assert "results?search_query=how%20to%20code" in m_open.call_args[0][0]


@pytest.mark.asyncio
async def test_automate_youtube_open():
    with patch("jarvis_youtube_automation.yt_bot.open_url_in_app", new_callable=AsyncMock) as m_open:
        res = await automate_youtube("open")
        assert res['status'] == "success"
        m_open.assert_awaited_with("https://www.youtube.com")
