"""Unit tests for pak-sim-api shaped SIM lookup (K-34 v6)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from services.info.sim_providers import (
    CNIC_PATH,
    PHONE_PATH,
    AuthorizedApiSource,
    NotConfiguredSource,
    sim_source_types,
)

_FAKE_BASE = "https://sim.test.example"
_FAKE_KEY = "test-secret-key-not-real"
_PHONE = "03001234567"
_CNIC = "1234567890123"


@pytest.fixture(autouse=True)
def _clear_sim_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIM_LOOKUP_SHOW_ADDRESS", raising=False)


def test_sim_source_types_returns_exactly_two() -> None:
    assert sim_source_types() == (NotConfiguredSource, AuthorizedApiSource)


@pytest.mark.asyncio
async def test_phone_lookup_posts_correct_path_and_body() -> None:
    captured: dict[str, object] = {}

    async def fake_post(
        url: str,
        *,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(
            200,
            json={
                "success": True,
                "query_type": "phone",
                "hits": 1,
                "data": [
                    {
                        "source": "Test Registry",
                        "fields": {
                            "full_name": "Test User",
                            "cnic": "3310012345678",
                            "phone": _PHONE,
                            "address": "Secret Street",
                            "operator": "ExampleTel",
                        },
                    }
                ],
            },
        )

    mock_client = AsyncMock()
    mock_client.post = fake_post
    source = AuthorizedApiSource(_FAKE_BASE, _FAKE_KEY, client=mock_client)

    records, error = await source.lookup(_PHONE)

    assert error is None
    assert captured["url"] == f"{_FAKE_BASE}{PHONE_PATH}"
    assert captured["json"] == {"phone": _PHONE}
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == f"Bearer {_FAKE_KEY}"
    assert headers["Content-Type"] == "application/json"
    assert captured["timeout"] == 30.0

    assert len(records) == 1
    record = records[0]
    assert record["full_name"] == "Test User"
    assert record["source"] == "Test Registry"
    assert record["cnic_masked"].endswith("5678")
    assert "3310012345678" not in record["cnic_masked"]
    assert _PHONE not in record["phone_masked"]
    assert "address" not in record
    assert record["details"] == {"operator": "ExampleTel"}


@pytest.mark.asyncio
async def test_cnic_lookup_posts_correct_path_and_body() -> None:
    captured: dict[str, object] = {}

    async def fake_post(
        url: str,
        *,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del headers, timeout
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(
            200,
            json={
                "success": True,
                "query_type": "cnic",
                "hits": 1,
                "data": [
                    {
                        "source": "CNIC Records",
                        "fields": {
                            "full_name": "CNIC Holder",
                            "cnic": _CNIC,
                            "phone": "03009998877",
                        },
                    }
                ],
            },
        )

    mock_client = AsyncMock()
    mock_client.post = fake_post
    source = AuthorizedApiSource(_FAKE_BASE, _FAKE_KEY, client=mock_client)

    records, error = await source.lookup(_CNIC)

    assert error is None
    assert captured["url"] == f"{_FAKE_BASE}{CNIC_PATH}"
    assert captured["json"] == {"cnic": _CNIC}
    assert len(records) == 1
    assert records[0]["full_name"] == "CNIC Holder"
    assert records[0]["cnic_masked"].endswith("0123")


@pytest.mark.asyncio
async def test_raw_text_fields_are_parsed_and_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live pak-sim returns one raw_text blob; 92… mobile normalizes to 03…; fake digits only."""
    monkeypatch.setenv("SIM_LOOKUP_SHOW_ADDRESS", "1")
    # Fake digits only (not a real Pakistani number/CNIC). Live API uses 12-digit 92… mobiles.
    fake_mobile_cc = "921111222333"
    fake_phone_local = "01111222333"
    fake_cnic = "1111222233334"
    fake_name = "Sample Holder"
    fake_address = "Example Block Chak 12 Sector"
    raw = f"Mobile Name CNIC Address {fake_mobile_cc} {fake_name} {fake_cnic} {fake_address}"

    async def fake_post(
        url: str,
        *,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del url, json, headers, timeout
        return httpx.Response(
            200,
            json={
                "success": True,
                "query_type": "phone",
                "hits": 1,
                "data": [
                    {
                        "source": "SIM / CNIC Records",
                        "fields": {"raw_text": raw},
                    }
                ],
            },
        )

    mock_client = AsyncMock()
    mock_client.post = fake_post
    source = AuthorizedApiSource(_FAKE_BASE, _FAKE_KEY, client=mock_client)

    records, error = await source.lookup(fake_phone_local)

    assert error is None
    assert len(records) == 1
    record = records[0]
    assert record["full_name"] == fake_name
    assert record["cnic_masked"].endswith("3334")
    assert fake_cnic not in record["cnic_masked"]
    assert fake_mobile_cc not in record["phone_masked"]
    assert fake_phone_local not in record["phone_masked"]
    assert record["address"] == fake_address
    details_raw = record["details"]["raw_text"]
    assert fake_cnic not in details_raw
    assert fake_mobile_cc not in details_raw
    assert "Sample Holder" in details_raw


@pytest.mark.asyncio
async def test_invalid_length_skips_http() -> None:
    mock_client = AsyncMock()
    source = AuthorizedApiSource(_FAKE_BASE, _FAKE_KEY, client=mock_client)

    records, error = await source.lookup("12345")

    assert records == []
    assert error is not None
    assert "11-digit" in error
    mock_client.post.assert_not_awaited()


@pytest.mark.parametrize(
    ("status_code", "error_code", "expected_message"),
    [
        (401, "unauthorized", "provider ne key reject ki"),
        (429, "rate_limited", "Provider rate-limited hai. Thodi der baad try karein."),
        (502, "upstream_unavailable", "SIM provider abhi unavailable hai."),
    ],
)
@pytest.mark.asyncio
async def test_api_error_codes_map_to_user_messages(
    status_code: int,
    error_code: str,
    expected_message: str,
) -> None:
    async def fake_post(
        url: str,
        *,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del url, json, headers, timeout
        return httpx.Response(status_code, json={"success": False, "error": error_code})

    mock_client = AsyncMock()
    mock_client.post = fake_post
    source = AuthorizedApiSource(_FAKE_BASE, _FAKE_KEY, client=mock_client)

    records, error = await source.lookup(_PHONE)

    assert records == []
    assert error == expected_message
