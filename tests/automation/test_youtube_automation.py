import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import jarvis_youtube_automation
from jarvis_youtube_automation import YouTubeAutomation, automate_youtube


def _mock_ytdl(entries):
    mock_ydl_cls = MagicMock()
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.__exit__.return_value = False
    mock_ydl.extract_info.return_value = {"entries": entries}
    mock_ydl_cls.return_value = mock_ydl
    return mock_ydl_cls


@pytest.fixture
def mock_yt_dlp():
    with patch("jarvis_youtube_automation.yt_dlp") as mock:
        mock.YoutubeDL = _mock_ytdl([{"id": "test_id", "title": "Test"}])
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
async def test_get_video_url(mock_yt_dlp):
    bot = YouTubeAutomation()
    url = await bot.get_video_url("test video")
    assert url == "https://www.youtube.com/watch?v=test_id"


@pytest.mark.asyncio
async def test_open_url_in_app_edge(mock_shutil, mock_popen):
    bot = YouTubeAutomation()
    mock_shorthand = mock_shutil.which
    mock_shorthand.return_value = "/path/to/edge"
    with patch("jarvis_youtube_automation.config", MagicMock(edge_path=None, chrome_path=None)):
        with patch("os.name", "nt"):
            res = await bot.open_url_in_app("https://youtube.com")
            assert res is True
            mock_popen.assert_called()
            args = mock_popen.call_args[0][0]
            assert args[0] == "/path/to/edge"
            assert args[1] == "--app=https://youtube.com"
            assert "--autoplay-policy=no-user-gesture-required" in args


@pytest.mark.asyncio
async def test_open_url_in_app_config_chrome(mock_popen):
    bot = YouTubeAutomation()
    chrome = r"C:\Chrome\chrome.exe"
    with patch("jarvis_youtube_automation.config", MagicMock(edge_path=None, chrome_path=chrome)):
        with patch("jarvis_youtube_automation.os.path.isfile", return_value=True):
            with patch("os.name", "nt"):
                res = await bot.open_url_in_app("https://youtube.com/watch?v=1")
                assert res is True
                mock_popen.assert_called()
                assert mock_popen.call_args[0][0][0] == chrome


@pytest.mark.asyncio
async def test_open_url_in_app_fallback(mock_shutil):
    bot = YouTubeAutomation()
    mock_shutil.which.return_value = None
    with patch("jarvis_youtube_automation.config", MagicMock(edge_path=None, chrome_path=None)):
        with patch("jarvis_youtube_automation.os.path.isfile", return_value=False):
            with patch("os.name", "posix"):
                with patch(
                    "jarvis_youtube_automation.jarvis_delivery.open_url",
                    return_value=None,
                ) as mock_open:
                    res = await bot.open_url_in_app("https://youtube.com")
                    assert res is True
                    mock_open.assert_called_once_with("https://youtube.com")


@pytest.mark.asyncio
async def test_automate_youtube_play():
    meta = {
        "url": "https://youtube.com/watch?v=123",
        "video_id": "123",
        "title": "Cool",
    }
    with patch("jarvis_youtube_automation.yt_bot.resolve_video", new_callable=AsyncMock) as m_get:
        with patch("jarvis_youtube_automation.yt_bot.open_url_in_app", new_callable=AsyncMock) as m_open:
            m_get.return_value = meta
            res = await automate_youtube("play", "cool song")
            assert res['status'] == "success"
            assert "play kar raha hoon" in res['message']
            assert res["data"]["video_id"] == "123"
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
