import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from jarvis_search import get_current_city, search_tavily, search_google, search_duckduckgo, get_formatted_datetime, search_internet

@pytest.mark.asyncio
async def test_get_current_city_env():
    with patch("os.getenv", side_effect=lambda k, d=None: "TestCity" if k == "USER_CITY" else d):
        city = await get_current_city()
        assert city == "TestCity"

@pytest.mark.asyncio
async def test_get_current_city_api():
    mock_response = MagicMock()
    mock_response.json.return_value = {"city": "DetectionCity"}
    with patch("os.getenv", return_value=None):
        with patch("requests.get", return_value=mock_response):
            city = await get_current_city()
            assert city == "DetectionCity"

@pytest.mark.asyncio
async def test_get_current_city_fallback():
    # Properly mock os.getenv to return default if key is not found
    def mock_getenv(key, default=None):
        return default
    with patch("os.getenv", side_effect=mock_getenv):
        with patch("requests.get", side_effect=ValueError("API Error")):
            city = await get_current_city()
            assert city == "Lahore"

@pytest.mark.asyncio
async def test_search_tavily_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "results": [{"title": "T1", "content": "C1", "url": "U1"}]
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("jarvis_search.TAVILY_API_KEY", "test_key"):
        with patch("requests.post", return_value=mock_response):
            result = await search_tavily("test query")
            assert result["status"] == "success"
            assert "T1" in result["message"]

@pytest.mark.asyncio
async def test_search_google_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [{"title": "G1", "snippet": "S1", "link": "L1"}]
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("jarvis_search.GOOGLE_SEARCH_API_KEY", "test_key"):
        with patch("jarvis_search.SEARCH_ENGINE_ID", "test_id"):
            with patch("requests.get", return_value=mock_response):
                result = await search_google("test query")
                assert result["status"] == "success"
                assert "G1" in result["message"]

@pytest.mark.asyncio
async def test_search_duckduckgo_success():
    # Mocking DDGS sequence
    mock_ddgs_instance = MagicMock()
    mock_ddgs_instance.text.return_value = [{"title": "D1", "body": "B1", "href": "H1"}]
    mock_ddgs_instance.__enter__.return_value = mock_ddgs_instance
    
    with patch("jarvis_search.DDGS", return_value=mock_ddgs_instance):
        result = await search_duckduckgo("test query")
        assert result["status"] == "success"
        assert "D1" in result["message"]

@pytest.mark.asyncio
async def test_search_internet_cascade():
    # Mock Tavily to fail, Google to fail, DDG to success
    with patch("jarvis_search.search_tavily", return_value={"status": "error"}):
        with patch("jarvis_search.search_google", return_value={"status": "error"}):
            with patch("jarvis_search.search_duckduckgo", return_value={"status": "success", "message": "DDG Win"}):
                result = await search_internet("query")
                assert result["message"] == "DDG Win"

@pytest.mark.asyncio
async def test_get_formatted_datetime():
    result = await get_formatted_datetime()
    assert "formatted" in result
    assert "day" in result
    assert "date" in result
    assert "time" in result
