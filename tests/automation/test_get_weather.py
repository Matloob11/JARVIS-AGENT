import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from jarvis_get_weather import get_weather, get_weather_via_search


def _api_key_getenv(key, default=None):
    if key in ("OPENWEATHER_API_KEY", "WEATHER_API_KEY"):
        return "test_key"
    return default


@pytest.mark.asyncio
async def test_get_weather_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "weather": [{"description": "clear sky"}],
        "main": {"temp": 25, "humidity": 50},
        "wind": {"speed": 5},
    }

    with patch("os.getenv", side_effect=_api_key_getenv):
        with patch("requests.get", return_value=mock_response):
            result = await get_weather("London")
            assert isinstance(result, dict)
            assert result["status"] == "success"
            assert result["city"] == "London"
            assert "Clear Sky" in result["message"]


@pytest.mark.asyncio
async def test_get_weather_auto_detect_city():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "weather": [{"description": "cloudy"}],
        "main": {"temp": 20, "humidity": 60},
        "wind": {"speed": 2},
    }

    with patch("os.getenv", side_effect=_api_key_getenv):
        with patch(
            "jarvis_get_weather.get_current_city",
            new_callable=AsyncMock,
            return_value="Karachi",
        ):
            with patch("requests.get", return_value=mock_response) as mock_get:
                result = await get_weather("")
                assert result["city"] == "Karachi"
                assert mock_get.call_args[1]["params"]["q"] == "Karachi"


@pytest.mark.asyncio
async def test_get_weather_forecast_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "list": [
            {
                "main": {"temp": 22, "temp_min": 20, "temp_max": 24},
                "pop": 0.4,
                "rain": {"3h": 1.2},
                "weather": [{"description": "light rain"}],
            },
            {
                "main": {"temp": 18, "temp_min": 17, "temp_max": 19},
                "pop": 0.8,
                "rain": {"3h": 2.5},
                "weather": [{"description": "moderate rain"}],
            },
        ],
    }

    with patch("os.getenv", side_effect=_api_key_getenv):
        with patch("requests.get", return_value=mock_response) as mock_get:
            result = await get_weather("Lahore", forecast=True)
            assert result["status"] == "success"
            assert result["city"] == "Lahore"
            assert "forecast" in result
            fc = result["forecast"]
            assert fc["min_temp"] == 17
            assert fc["max_temp"] == 24
            assert fc["rain_probability_pct"] == 80
            assert fc["rain_mm"] == 3.7
            assert "Barish ki probability" in result["message"]
            assert mock_get.call_args[0][0].endswith("/forecast")
            assert mock_get.call_args[1]["params"]["cnt"] == 8


@pytest.mark.asyncio
async def test_get_weather_401_invalid_key_search_fallback():
    mock_response = MagicMock()
    mock_response.status_code = 401

    mock_search_result = {"status": "success", "message": "Search Weather Info"}

    with patch("os.getenv", side_effect=_api_key_getenv):
        with patch("requests.get", return_value=mock_response):
            with patch("jarvis_get_weather.search_internet", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = mock_search_result
                result = await get_weather("London")
                assert result["status"] == "success"
                assert "OpenWeather key invalid" in result["message"]
                assert "Search Weather Info" in result["message"]


@pytest.mark.asyncio
async def test_get_weather_api_failure_search_fallback():
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_search_result = {"status": "success", "message": "Search Weather Info"}

    with patch("os.getenv", side_effect=_api_key_getenv):
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
        assert result["status"] == "error"
        assert "OpenWeather API key" in result["message"]


@pytest.mark.asyncio
async def test_get_weather_via_search_success():
    mock_search_result = {"status": "success", "message": "Sunny 20C"}
    with patch("jarvis_get_weather.search_internet", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = mock_search_result
        result = await get_weather_via_search("London")
        assert result["status"] == "success"
        assert "Sunny 20C" in result["message"]
