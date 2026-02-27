import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from jarvis_get_weather import get_weather, get_weather_via_search

@pytest.mark.asyncio
async def test_get_weather_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "weather": [{"description": "clear sky"}],
        "main": {"temp": 25, "humidity": 50},
        "wind": {"speed": 5}
    }
    
    with patch("os.getenv", side_effect=lambda k, d=None: "test_key" if "API_KEY" in k or "CITY" in k else d):
        with patch("requests.get", return_value=mock_response):
            result = await get_weather("London")
            assert isinstance(result, dict)
            assert result["status"] == "success"
            assert result["city"] == "London"
            assert "Clear Sky" in result["message"]

@pytest.mark.asyncio
async def test_get_weather_api_failure_search_fallback():
    mock_response = MagicMock()
    mock_response.status_code = 404
    
    # Mock search_internet for fallback
    mock_search_result = {"status": "success", "message": "Search Weather Info"}
    
    with patch("os.getenv", side_effect=lambda k, d=None: "test_key" if "API_KEY" in k else d):
        with patch("requests.get", return_value=mock_response):
            with patch("jarvis_get_weather.search_internet", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = mock_search_result
                result = await get_weather("London")
                assert result["status"] == "success"
                assert "Search Weather Info" in result["message"]

@pytest.mark.asyncio
async def test_get_weather_no_api_key():
    with patch("os.getenv", return_value=None):
        result = await get_weather("London")
        assert "OpenWeather API key" in result

@pytest.mark.asyncio
async def test_get_weather_via_search_success():
    mock_search_result = {"status": "success", "message": "Sunny 20C"}
    # Use AsyncMock for the patched coroutine
    with patch("jarvis_get_weather.search_internet", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = mock_search_result
        result = await get_weather_via_search("London")
        assert result["status"] == "success"
        assert "Sunny 20C" in result["message"]
