import pytest
import asyncio
from io import BytesIO
from unittest.mock import MagicMock, patch, AsyncMock
from PIL import Image
import jarvis_vision
from jarvis_vision import ScreenPerceiver, analyze_screen


@pytest.fixture
def mock_genai():
    with patch("jarvis_vision.genai.Client") as mock:
        yield mock


@pytest.fixture
def mock_pg():
    with patch("jarvis_vision.pyautogui") as mock:
        yield mock


@pytest.fixture
def mock_requests():
    with patch("jarvis_vision.requests") as mock:
        yield mock


@pytest.mark.asyncio
async def test_capture_screen(mock_pg):
    perceiver = ScreenPerceiver()
    mock_img = Image.new('RGB', (100, 100))
    mock_pg.screenshot.return_value = mock_img

    with patch("asyncio.to_thread", side_effect=asyncio.to_thread) as mock_thread:
        res = await perceiver.capture_screen()
        assert isinstance(res, bytes)
        assert len(res) > 0


@pytest.mark.asyncio
async def test_analyze_via_google():
    perceiver = ScreenPerceiver()
    with patch("jarvis_vision.client") as mock_client:
        mock_response = MagicMock()
        mock_response.text = "I see a desktop."
        mock_client.models.generate_content.return_value = mock_response

        mock_img = Image.new('RGB', (10, 10))
        res = await perceiver.analyze_via_google("test prompt", mock_img)
        assert res == "I see a desktop."


@pytest.mark.asyncio
async def test_analyze_via_openrouter(mock_requests):
    perceiver = ScreenPerceiver()
    with patch("os.getenv", return_value="fake_key"):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Fallback analysis"}}]
        }
        mock_requests.post.return_value = mock_resp

        mock_img = Image.new('RGB', (10, 10))
        res = await perceiver.analyze_via_openrouter("test prompt", mock_img)
        assert res == "Fallback analysis"


@pytest.mark.asyncio
async def test_analyze_content_success(mock_pg, mock_genai):
    perceiver = ScreenPerceiver()
    mock_pg.screenshot.return_value = Image.new('RGB', (10, 10))

    with patch.object(ScreenPerceiver, 'analyze_via_google', new_callable=AsyncMock) as m_google:
        m_google.return_value = "Gemini result"
        res = await perceiver.analyze_content()
        assert res == "Gemini result"


@pytest.mark.asyncio
async def test_analyze_content_fallback(mock_pg, mock_genai):
    perceiver = ScreenPerceiver()
    mock_pg.screenshot.return_value = Image.new('RGB', (10, 10))

    with patch.object(ScreenPerceiver, 'analyze_via_google', side_effect=ValueError("RESOURCE_EXHAUSTED")):
        with patch.object(ScreenPerceiver, 'analyze_via_openrouter', new_callable=AsyncMock) as m_fallback:
            m_fallback.return_value = "Fallback result"
            res = await perceiver.analyze_content()
            assert res == "Fallback result"


@pytest.mark.asyncio
async def test_analyze_screen_tool():
    with patch("jarvis_vision.vision_system.analyze_content", new_callable=AsyncMock) as m_analyze:
        m_analyze.return_value = "Everything looks good."
        res = await analyze_screen("What is this?")
        assert res['status'] == "success"
        assert "Everything looks good" in res['message']
