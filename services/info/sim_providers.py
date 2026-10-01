"""
Authorized SIM lookup sources for JARVIS (contract K-34).

Only NotConfiguredSource and AuthorizedApiSource are permitted. Leaked, scraped,
or unofficial 'SIM database' integrations must not be added here.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

import httpx

from services.utils.jarvis_security import redact_secrets

# Confirm against the provider API doc when SIM_LOOKUP_API_KEY is available.
FIELD_MAP: dict[str, str] = {
    "full_name": "full_name",
    "cnic": "cnic",
    "phone": "phone",
    "address": "address",
}

PROVIDER_NOT_CONFIGURED = "not_configured"
PROVIDER_AUTHORIZED_API = "authorized_api"

NOT_CONFIGURED_HINT = "provider not configured"

_LOOKUP_TIMEOUT_S = 10.0


@runtime_checkable
class AuthorizedSimSource(Protocol):
    """EXT DASH-U18: one active SIM lookup backend."""

    provider: str
    configured: bool
    requires_purpose: bool

    async def lookup(self, digits: str) -> tuple[list[dict[str, Any]], str | None]:
        """Return (records, error_message). error_message is user-facing when records is empty."""


def _env_str(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _mask_cnic_digits(digits: str) -> str:
    clean = "".join(ch for ch in digits if ch.isdigit())
    if len(clean) <= 4:
        return "*" * len(clean)
    return ("*" * (len(clean) - 4)) + clean[-4:]


def _mask_phone_digits(digits: str) -> str:
    clean = "".join(ch for ch in digits if ch.isdigit())
    if len(clean) <= 3:
        return "*" * len(clean)
    return f"{clean[:2]}{'*' * (len(clean) - 3)}{clean[-1:]}"


def _record_from_payload(item: dict[str, Any], *, show_address: bool) -> dict[str, Any]:
    name_key = FIELD_MAP["full_name"]
    cnic_key = FIELD_MAP["cnic"]
    phone_key = FIELD_MAP["phone"]
    address_key = FIELD_MAP["address"]

    record: dict[str, Any] = {
        "full_name": str(item.get(name_key) or item.get("name") or "").strip(),
        "cnic_masked": _mask_cnic_digits(str(item.get(cnic_key) or "")),
        "phone_masked": _mask_phone_digits(str(item.get(phone_key) or "")),
    }
    if show_address:
        address = str(item.get(address_key) or "").strip()
        if address:
            record["address"] = address
    return record


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("records", "data", "results", "items"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
    return [payload]


class NotConfiguredSource:
    """Default: privacy-safe blocked flow until authorized API credentials exist."""

    provider = PROVIDER_NOT_CONFIGURED
    configured = False
    requires_purpose = False

    async def lookup(self, digits: str) -> tuple[list[dict[str, Any]], str | None]:
        del digits
        return [], NOT_CONFIGURED_HINT


class AuthorizedApiSource:
    """POST msisdn to SIM_LOOKUP_API_URL with Bearer SIM_LOOKUP_API_KEY."""

    provider = PROVIDER_AUTHORIZED_API
    configured = True
    requires_purpose = True

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._client = client

    async def lookup(self, digits: str) -> tuple[list[dict[str, Any]], str | None]:
        show_address = _env_str("SIM_LOOKUP_SHOW_ADDRESS") in {"1", "true", "yes", "on"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        body = {"msisdn": digits}
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._api_url,
                    json=body,
                    headers=headers,
                    timeout=_LOOKUP_TIMEOUT_S,
                )
            else:
                async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_S) as client:
                    response = await client.post(self._api_url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            return [], redact_secrets(f"SIM provider request failed: {exc}")

        if response.status_code in {401, 403}:
            return [], "provider ne key reject ki"

        if response.status_code >= 400:
            detail = response.text[:200] if response.text else f"HTTP {response.status_code}"
            return [], redact_secrets(f"SIM provider error: {detail}")

        try:
            payload = response.json()
        except ValueError:
            return [], "SIM provider ne invalid JSON bheja."

        records = [
            _record_from_payload(item, show_address=show_address)
            for item in _extract_items(payload)
        ]
        return records, None


def sim_source_types() -> tuple[type, ...]:
    """Exactly two concrete source implementations (K-34 test assertion)."""
    return (NotConfiguredSource, AuthorizedApiSource)


def resolve_sim_source() -> AuthorizedSimSource:
    """SIM_SOURCE=none forces off; auto uses AuthorizedApiSource when URL+KEY are set."""
    mode = _env_str("SIM_SOURCE").lower()
    if mode == "none":
        return NotConfiguredSource()

    api_url = _env_str("SIM_LOOKUP_API_URL")
    api_key = _env_str("SIM_LOOKUP_API_KEY")
    if api_url and api_key:
        return AuthorizedApiSource(api_url, api_key)
    return NotConfiguredSource()


def is_sim_lookup_configured() -> bool:
    return resolve_sim_source().configured
