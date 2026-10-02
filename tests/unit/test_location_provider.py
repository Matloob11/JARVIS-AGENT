"""C-113 (contracts K-08, K-72, K-77): the location provider of the VPS brain, services/utils/jarvis_location.py.

The device location comes from conversations/location.json (agent-svc writes it from the Desktop Node's
location events) while it is at most 30 min old; otherwise USER_CITY with fixed coordinates. The cache
lives in tmp_path, and any attempt to resolve or connect to a host fails the test: the provider never
reaches the network (ipinfo would answer for the VPS data centre).
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import json
import socket
import time
from unittest.mock import MagicMock

import pytest
import requests

from services.utils import jarvis_location as loc

NOW = 1_790_000_000.0
ISLAMABAD = ("Islamabad", 33.6844, 73.0479)


def _refuse(*_args, **_kwargs):
    raise AssertionError("the location provider reached the network")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(requests, "get", _refuse)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    """conversations/location.json in tmp_path, USER_CITY unset, a fresh warning memory."""
    path = tmp_path / "conversations" / "location.json"
    monkeypatch.setattr(loc, "location_path", lambda: str(path))
    monkeypatch.setattr(loc, "_last_warning", None)
    monkeypatch.delenv("USER_CITY", raising=False)
    return path


def test_the_cache_lives_in_the_conversations_folder():
    assert loc.location_path().replace("\\", "/").endswith("/conversations/location.json")


@pytest.mark.asyncio
async def test_a_fresh_device_location_is_where_sir_is(cache):
    loc.save_device_location(*ISLAMABAD, ts=time.time() - 60)
    data = await loc.get_current_city_data()
    assert (data["city"], data["lat"], data["lng"], data["source"]) == (*ISLAMABAD, "device")
    assert await loc.get_current_city() == "Islamabad"


@pytest.mark.parametrize(("age", "fresh"), [
    (0, True), (1800, True), (1801, False), (86_400, False),
    (-120, True), (-121, False),  # a laptop clock a little ahead still counts; a future ts does not
])
def test_the_cache_counts_for_30_minutes(cache, age, fresh):
    loc.save_device_location(*ISLAMABAD, ts=NOW - age)
    assert (loc.get_device_location(now=NOW) is not None) is fresh


@pytest.mark.asyncio
async def test_without_a_fresh_device_location_user_city_answers_with_fixed_coordinates(cache, monkeypatch):
    monkeypatch.setenv("USER_CITY", "  Multan ")
    loc.save_device_location(*ISLAMABAD, ts=time.time() - 31 * 60)
    data = await loc.get_current_city_data()
    assert {key: data[key] for key in ("city", "lat", "lng", "source")} == {
        "city": "Multan", "lat": 31.5204, "lng": 74.3587, "source": "user_city"}
    assert abs(data["ts"] - time.time()) < 60
    assert await loc.get_current_city() == "Multan"


@pytest.mark.asyncio
async def test_without_a_cache_or_user_city_it_is_lahore(cache, monkeypatch):
    assert not cache.exists()
    assert await loc.get_current_city() == "Lahore"
    monkeypatch.setenv("USER_CITY", "   ")
    assert (await loc.get_current_city_data())["city"] == "Lahore"


UNUSABLE = {
    "not-json": "{not json",
    "list": "[]",
    "string": '"Islamabad"',
    "no-city": json.dumps({"lat": 33.6, "lng": 73.0, "ts": NOW}),
    "empty-city": json.dumps({"city": "", "lat": 33.6, "lng": 73.0, "ts": NOW}),
    "control-char": json.dumps({"city": "Isla\nmabad", "lat": 33.6, "lng": 73.0, "ts": NOW}),
    "long-city": json.dumps({"city": "x" * 101, "lat": 33.6, "lng": 73.0, "ts": NOW}),
    "lat-range": json.dumps({"city": "Islamabad", "lat": 90.5, "lng": 73.0, "ts": NOW}),
    "lng-range": json.dumps({"city": "Islamabad", "lat": 33.6, "lng": -180.5, "ts": NOW}),
    "lat-text": json.dumps({"city": "Islamabad", "lat": "33.6", "lng": 73.0, "ts": NOW}),
    "lat-bool": json.dumps({"city": "Islamabad", "lat": True, "lng": 73.0, "ts": NOW}),
    "lat-nan": '{"city": "Islamabad", "lat": NaN, "lng": 73.0, "ts": 1790000000}',
    "no-ts": json.dumps({"city": "Islamabad", "lat": 33.6, "lng": 73.0}),
    "zero-ts": json.dumps({"city": "Islamabad", "lat": 33.6, "lng": 73.0, "ts": 0}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("content", list(UNUSABLE.values()), ids=list(UNUSABLE))
async def test_an_unusable_cache_falls_back_to_user_city_with_one_warning(cache, monkeypatch, content):
    log = MagicMock()
    monkeypatch.setattr(loc, "logger", log)
    cache.parent.mkdir(parents=True)
    cache.write_text(content, encoding="utf-8")
    assert loc.get_device_location(now=NOW) is None
    assert (await loc.get_current_city_data())["source"] == "user_city"
    assert await loc.get_current_city() == "Lahore"
    assert log.warning.call_count == 1  # once per file version, not once per call
    assert "location.json" in str(log.warning.call_args)


def test_save_writes_the_whole_cache_through_a_replace(cache):
    first = loc.save_device_location(" Islamabad ", 33.6844, 73.0479, ts=NOW)
    assert first == {"city": "Islamabad", "lat": 33.6844, "lng": 73.0479, "source": "device", "ts": NOW}
    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "city": "Islamabad", "lat": 33.6844, "lng": 73.0479, "ts": NOW}
    loc.save_device_location("Karachi", 24.8607, 67.0011, ts=NOW + 600)
    assert loc.get_device_location(now=NOW + 600)["city"] == "Karachi"
    assert [path.name for path in cache.parent.iterdir()] == ["location.json"]  # no temporary file left


@pytest.mark.parametrize("bad", [
    ("", 33.6, 73.0), ("x" * 101, 33.6, 73.0), (None, 33.6, 73.0), ("Islamabad", 90.5, 73.0),
    ("Islamabad", 33.6, 180.5), ("Islamabad", float("nan"), 73.0), ("Islamabad", True, 73.0),
])
def test_save_refuses_an_invalid_location(cache, bad):
    with pytest.raises(ValueError, match="invalid device location"):
        loc.save_device_location(*bad, ts=NOW)
    assert not cache.exists()


def test_the_provider_holds_no_http_client():
    assert not any(name in vars(loc) for name in ("requests", "httpx", "aiohttp", "urllib3"))
