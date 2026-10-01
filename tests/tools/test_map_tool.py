"""Unit tests for jarvis_map (geocoder mocked — no live Nominatim)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.info import jarvis_map as jarvis_map_mod


class _FakeLocation:
    def __init__(self, address: str, lat: float, lng: float) -> None:
        self.address = address
        self.latitude = lat
        self.longitude = lng


@pytest.mark.asyncio
async def test_show_location_kind_map_and_zoom() -> None:
    fake = _FakeLocation("Faisal Mosque, Islamabad", 33.7295, 73.0383)
    notify = AsyncMock()
    with (
        patch.object(jarvis_map_mod, "_geocode_sync", return_value=fake),
        patch.object(jarvis_map_mod, "notify_location", notify),
    ):
        result = await jarvis_map_mod.show_location_on_map("Faisal Mosque", zoom=15)

    assert result["status"] == "success"
    assert result["zoom"] == 15
    assert result["lat"] == 33.7295
    assert result["lng"] == 73.0383
    notify.assert_awaited_once()
    payload = notify.await_args.args[0]
    assert payload["kind"] == "map"
    assert payload["source"] == "geocode"
    assert payload["zoom"] == 15
    assert "Faisal Mosque" in payload["label"]


@pytest.mark.asyncio
async def test_geocode_rate_at_least_one_second() -> None:
    """Two RateLimiter-wrapped calls should be spaced >= 1 s (real limiter, fake geocode)."""
    calls: list[float] = []

    def fake_geocode(_query: str, **_kw: object) -> _FakeLocation:
        calls.append(time.monotonic())
        return _FakeLocation("Somewhere", 1.0, 2.0)

    geolocator = MagicMock()
    geolocator.geocode = fake_geocode
    from geopy.extra.rate_limiter import RateLimiter

    limited = RateLimiter(geolocator.geocode, min_delay_seconds=1)
    with (
        patch.object(jarvis_map_mod, "_geocode_sync", side_effect=lambda q: limited(q)),
        patch.object(jarvis_map_mod, "notify_location", AsyncMock()),
    ):
        t0 = time.monotonic()
        await jarvis_map_mod.show_location_on_map("A")
        await jarvis_map_mod.show_location_on_map("B")
        elapsed = time.monotonic() - t0

    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.95
    assert elapsed >= 0.95
