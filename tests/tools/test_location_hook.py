"""X-64: jarvis_search location hook — device cache first, ipinfo only outside remote mode."""
# pylint: disable=redefined-outer-name
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from services.info import jarvis_get_weather, jarvis_search
from services.utils import jarvis_location

ISLAMABAD = ("Islamabad", 33.6844, 73.0479)


def _fake_requests(monkeypatch) -> list[str]:
    """Record outbound HTTP; fail loudly if ipinfo is reached in remote mode."""
    asked: list[str] = []

    def fake_get(url, *_args, **kwargs):
        asked.append("ipinfo" if "ipinfo" in url else (kwargs.get("params") or {}).get("q", url))
        if "ipinfo" in url:
            raise AssertionError("ipinfo reached in remote mode")
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "weather": [{"description": "clear sky"}],
            "main": {"temp": 31, "humidity": 40},
            "wind": {"speed": 3},
        }
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    return asked


@pytest.fixture
def location_cache(monkeypatch, tmp_path):
    path = tmp_path / "conversations" / "location.json"
    monkeypatch.setattr(jarvis_location, "location_path", lambda: str(path))
    monkeypatch.setattr(jarvis_location, "_last_warning", None)
    return path


@pytest.fixture
def remote_mode(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")


@pytest.mark.asyncio
async def test_remote_mode_empty_city_weather_uses_lahore_without_ipinfo(
    monkeypatch, location_cache, remote_mode
):
    monkeypatch.delenv("USER_CITY", raising=False)
    monkeypatch.setenv("OPENWEATHER_API_KEY", "x64-fake-key")
    asked = _fake_requests(monkeypatch)
    result = await jarvis_get_weather.get_weather("")
    assert result["status"] == "success"
    assert result["city"] == "Lahore"
    assert asked == ["Lahore"]
    assert "ipinfo" not in asked


@pytest.mark.asyncio
async def test_remote_mode_empty_city_prefers_device_then_user_city(
    monkeypatch, location_cache, remote_mode
):
    monkeypatch.setenv("USER_CITY", "Multan")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "x64-fake-key")
    asked = _fake_requests(monkeypatch)
    result = await jarvis_get_weather.get_weather("")
    assert result["city"] == "Multan"
    jarvis_location.save_device_location(*ISLAMABAD)
    assert (await jarvis_get_weather.get_weather(""))["city"] == "Islamabad"
    assert asked == ["Multan", "Islamabad"]


@pytest.mark.asyncio
async def test_jarvis_search_remote_never_calls_ipinfo(monkeypatch, location_cache, remote_mode):
    monkeypatch.delenv("USER_CITY", raising=False)
    asked = _fake_requests(monkeypatch)
    data = await jarvis_search.get_current_city_data()
    assert data["city"] == "Lahore"
    assert await jarvis_search.get_current_city() == "Lahore"
    assert not asked


@pytest.mark.asyncio
async def test_jarvis_search_uses_fresh_device_cache(monkeypatch, location_cache, remote_mode):
    jarvis_location.save_device_location(*ISLAMABAD)
    asked = _fake_requests(monkeypatch)
    data = await jarvis_search.get_current_city_data()
    assert (data["city"], data["lat"], data["lng"]) == ISLAMABAD
    assert not asked
