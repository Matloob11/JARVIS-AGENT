"""Unit tests for authorized SIM lookup sources (K-34 / X-38)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from services.info import jarvis_sim_lookup as sim_mod
from services.info import sim_providers as providers_mod
from services.info.sim_providers import (
    AuthorizedApiSource,
    NotConfiguredSource,
    sim_source_types,
)


@pytest.fixture(autouse=True)
def _clear_sim_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SIM_SOURCE",
        "SIM_LOOKUP_API_URL",
        "SIM_LOOKUP_API_KEY",
        "SIM_LOOKUP_DAILY_LIMIT",
        "SIM_LOOKUP_SHOW_ADDRESS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sim_lookup_history.jsonl"
    monkeypatch.setattr(sim_mod, "AUDIT_FILE", path)
    return path


def test_only_two_sim_source_types_exist() -> None:
    assert sim_source_types() == (NotConfiguredSource, AuthorizedApiSource)


@pytest.mark.asyncio
async def test_not_configured_returns_blocked(audit_path: Path) -> None:
    result = await sim_mod.lookup_sim_data("03336678955")
    assert result["status"] == "blocked"
    assert providers_mod.NOT_CONFIGURED_HINT in result["message"]
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["provider"] == providers_mod.PROVIDER_NOT_CONFIGURED
    assert "purpose_sha256" not in row
    assert "03336678955" not in json.dumps(row)


@pytest.mark.asyncio
async def test_configured_lookup_masks_record(
    monkeypatch: pytest.MonkeyPatch,
    audit_path: Path,
) -> None:
    monkeypatch.setenv("SIM_LOOKUP_API_URL", "https://sim.example/lookup")
    monkeypatch.setenv("SIM_LOOKUP_API_KEY", "test-secret-key-12345")

    async def fake_post(
        url: str,
        *,
        json: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        assert url == "https://sim.example/lookup/api/v1/lookup/phone"
        assert headers["Authorization"] == "Bearer test-secret-key-12345"
        assert json == {"phone": "03336678955"}
        assert timeout == 30.0
        return httpx.Response(
            200,
            json={
                "success": True,
                "query_type": "phone",
                "hits": 1,
                "data": [
                    {
                        "source": "pak-sim-api",
                        "fields": {
                            "full_name": "Test User",
                            "cnic": "3310012345678",
                            "phone": "03336678955",
                            "address": "Secret Street",
                        },
                    }
                ],
            },
        )

    mock_client = AsyncMock()
    mock_client.post = fake_post
    source = AuthorizedApiSource(
        "https://sim.example/lookup",
        "test-secret-key-12345",
        client=mock_client,
    )

    with patch.object(sim_mod, "resolve_sim_source", return_value=source):
        result = await sim_mod.lookup_sim_data("03336678955", purpose="fraud check")

    assert result["status"] == "success"
    assert len(result["records"]) == 1
    record = result["records"][0]
    # K-34 v14: owner UI records carry cleartext + masked compat; audit stays masked.
    assert record["full_name"] == "Test User"
    assert record["cnic"] == "3310012345678"
    assert record["phone"] == "03336678955"
    assert record["address"] == "Secret Street"
    assert record["cnic_masked"].endswith("5678")
    assert "3310012345678" not in record["cnic_masked"]
    assert "03336678955" not in record["phone_masked"]

    row = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["provider"] == providers_mod.PROVIDER_AUTHORIZED_API
    assert row["status"] == "success"
    assert "purpose_sha256" in row
    assert "fraud check" not in json.dumps(row)
    assert "03336678955" not in json.dumps(row)


@pytest.mark.asyncio
async def test_configured_without_purpose_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    audit_path: Path,
) -> None:
    monkeypatch.setenv("SIM_LOOKUP_API_URL", "https://sim.example/lookup")
    monkeypatch.setenv("SIM_LOOKUP_API_KEY", "test-secret-key-12345")

    result = await sim_mod.lookup_sim_data("03336678955", purpose="")
    assert result["status"] == "validation_error"
    assert "purpose" in result["message"].lower()


@pytest.mark.asyncio
async def test_rate_limit_on_21st_successful_lookup(
    monkeypatch: pytest.MonkeyPatch,
    audit_path: Path,
) -> None:
    monkeypatch.setenv("SIM_LOOKUP_API_URL", "https://sim.example/lookup")
    monkeypatch.setenv("SIM_LOOKUP_API_KEY", "test-secret-key-12345")
    monkeypatch.setenv("SIM_LOOKUP_DAILY_LIMIT", "20")

    today = datetime.now(UTC).isoformat()
    with audit_path.open("w", encoding="utf-8") as audit_file:
        for _ in range(20):
            audit_file.write(
                json.dumps(
                    {
                        "ts": today,
                        "provider": providers_mod.PROVIDER_AUTHORIZED_API,
                        "query_masked": "033*****955",
                        "status": "success",
                        "count": 1,
                        "purpose_sha256": "abc",
                    }
                )
                + "\n"
            )

    notify_lookup = AsyncMock(return_value=([], None))
    with patch.object(providers_mod, "resolve_sim_source") as resolve_mock:
        source = AuthorizedApiSource("https://sim.example/lookup", "key")
        source.lookup = notify_lookup  # type: ignore[method-assign]
        resolve_mock.return_value = source

        result = await sim_mod.lookup_sim_data("03336678955", purpose="audit trail")

    assert result["status"] == "rate_limited"
    notify_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_panel_includes_provider_metadata() -> None:
    notify = AsyncMock()
    with patch.object(sim_mod, "notify_event", notify):
        await sim_mod.open_sim_lookup_panel()
    event_name, payload = notify.await_args.args
    assert event_name == "sim_panel_open"
    assert payload["provider"] == providers_mod.PROVIDER_NOT_CONFIGURED
    assert payload["configured"] is False
    assert payload["requires_purpose"] is False
