"""
Authorized SIM lookup sources for JARVIS (contract K-34 v6).

Only NotConfiguredSource and AuthorizedApiSource are permitted. Leaked, scraped,
or unofficial 'SIM database' integrations must not be added here.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol, runtime_checkable

import httpx

from services.utils.jarvis_security import redact_secrets

_CNIC_RE = re.compile(r"\b(\d{13})\b")
_PHONE_RE = re.compile(r"\b(\d{10,12})\b")  # 92XXXXXXXXXX (12) or 03XXXXXXXXX (11)
_HEADER_RE = re.compile(r"^(?:\s*(?:Mobile|Name|CNIC|Address))+\s*", re.I)
# Known person fields inside each data[].fields object.
FIELD_MAP: dict[str, str] = {
    "full_name": "full_name",
    "cnic": "cnic",
    "phone": "phone",
    "address": "address",
}

PROVIDER_NOT_CONFIGURED = "not_configured"
PROVIDER_AUTHORIZED_API = "authorized_api"

NOT_CONFIGURED_HINT = "provider not configured"

_LOOKUP_TIMEOUT_S = 30.0  # v6: the provider doc asks for ~30 s

PHONE_PATH = "/api/v1/lookup/phone"
CNIC_PATH = "/api/v1/lookup/cnic"

# {"success": false, "error": <code>} -> user-facing (Roman Urdu); records stay empty.
_ERROR_MESSAGES: dict[str, str] = {
    "unauthorized": "provider ne key reject ki",
    "invalid_input": "Number/CNIC format sahi nahi lag raha.",
    "rate_limited": "Provider rate-limited hai. Thodi der baad try karein.",
    "upstream_unavailable": "SIM provider abhi unavailable hai.",
}

_PATTERN_HINT = "Number 11-digit (03xxxxxxxxx) ya CNIC 13-digit hona chahiye."


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


def _route(digits: str) -> tuple[str, dict[str, str]] | tuple[None, None]:
    """Pick the endpoint + body by pattern: phone = 11 digits starting 0; CNIC = 13 digits."""
    if len(digits) == 11 and digits.startswith("0"):
        return PHONE_PATH, {"phone": digits}
    if len(digits) == 13:
        return CNIC_PATH, {"cnic": digits}
    return None, None


def _looks_like_phone_or_cnic(key: str) -> bool:
    k = key.lower()
    return any(tok in k for tok in ("cnic", "nic", "phone", "mobile", "msisdn", "number"))


def _normalize_phone(digits: str) -> str:
    if len(digits) == 12 and digits.startswith("92"):
        return "0" + digits[2:]  # country-code form -> local 0…
    return digits


def _parse_raw_text(text: str) -> dict[str, str]:
    """pak-sim 'Mobile Name CNIC Address <mobile> <name> <cnic> <address>' -> structured fields."""
    out: dict[str, str] = {}
    cnic = _CNIC_RE.search(text)
    phone = _PHONE_RE.search(text)  # first 10–12 digit run; CNIC is 13 so it never matches here
    has_phone = phone is not None and (cnic is None or phone.start() < cnic.start())
    if has_phone:
        out["phone"] = _normalize_phone(phone.group(1))
    if cnic:
        out["cnic"] = cnic.group(1)
        start = phone.end() if has_phone else 0
        name = _HEADER_RE.sub("", text[start : cnic.start()]).strip()
        if name:
            out["full_name"] = name
        address = text[cnic.end() :].strip()
        if address:
            out["address"] = address
    return out


def _has_structured_identity(fields: dict[str, Any]) -> bool:
    return any(
        str(fields.get(key) or "").strip()
        for key in ("full_name", "name", "cnic", "phone")
    )


def _show_address_enabled() -> bool:
    """Address on by default for the owner dashboard; set SIM_LOOKUP_SHOW_ADDRESS=0 to hide."""
    raw = _env_str("SIM_LOOKUP_SHOW_ADDRESS").lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return True


def _record_from_fields(fields: dict[str, Any], *, source: str, show_address: bool) -> dict[str, Any]:
    """One UI lead record: cleartext identity for the owner dashboard + masked compat fields."""
    work = dict(fields)
    raw = work.get("raw_text")
    if isinstance(raw, str) and raw.strip() and not _has_structured_identity(work):
        parsed = _parse_raw_text(raw)
        work = {**parsed, "raw_text": raw}

    cnic = "".join(ch for ch in str(work.get(FIELD_MAP["cnic"]) or "") if ch.isdigit())
    phone_raw = str(work.get(FIELD_MAP["phone"]) or "").strip()
    phone_digits = "".join(ch for ch in phone_raw if ch.isdigit())
    phone = _normalize_phone(phone_digits) if phone_digits else ""
    address = str(work.get(FIELD_MAP["address"]) or "").strip()

    record: dict[str, Any] = {
        "source": source,
        "full_name": str(work.get(FIELD_MAP["full_name"]) or work.get("name") or "").strip(),
        # Owner-authenticated SIM panel (after K-45 Allow): full lead fields.
        "cnic": cnic,
        "phone": phone,
        # Compat / audit-friendly redactions (UI prefers cleartext when present).
        "cnic_masked": _mask_cnic_digits(cnic),
        "phone_masked": _mask_phone_digits(phone),
    }
    if show_address and address:
        record["address"] = address
    # Pass through remaining provider fields; keep raw_text readable for owner leads.
    extra: dict[str, Any] = {}
    known = set(FIELD_MAP.values()) | {"name"}
    for key, value in work.items():
        if key in known:
            continue
        text = str(value)
        if key == "raw_text":
            # Prefer structured fields above; still keep a readable blob for odd providers.
            text = text.strip()
        elif _looks_like_phone_or_cnic(key) and any(ch.isdigit() for ch in text):
            digits = "".join(ch for ch in text if ch.isdigit())
            text = digits if digits else text
        if text:
            extra[key] = text
    if extra:
        record["details"] = extra
    return record


class NotConfiguredSource:
    """Default: privacy-safe blocked flow until authorized API credentials exist."""

    provider = PROVIDER_NOT_CONFIGURED
    configured = False
    requires_purpose = False

    async def lookup(self, digits: str) -> tuple[list[dict[str, Any]], str | None]:
        del digits
        return [], NOT_CONFIGURED_HINT


class AuthorizedApiSource:
    """POST to {SIM_LOOKUP_API_URL}/api/v1/lookup/{phone|cnic} with Bearer SIM_LOOKUP_API_KEY."""

    provider = PROVIDER_AUTHORIZED_API
    configured = True
    requires_purpose = True

    def __init__(self, api_url: str, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base = api_url.rstrip("/")
        self._api_key = api_key
        self._client = client

    async def lookup(self, digits: str) -> tuple[list[dict[str, Any]], str | None]:
        path, body = _route(digits)
        if path is None:
            return [], _PATTERN_HINT
        show_address = _show_address_enabled()
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        url = self._base + path
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body, headers=headers, timeout=_LOOKUP_TIMEOUT_S)
            else:
                async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_S) as client:
                    response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            return [], redact_secrets(f"SIM provider request failed: {exc}")

        try:
            payload = response.json()
        except ValueError:
            return [], f"SIM provider ne invalid JSON bheja (HTTP {response.status_code})."

        if response.status_code >= 400 or not (isinstance(payload, dict) and payload.get("success")):
            code = payload.get("error") if isinstance(payload, dict) else None
            return [], _ERROR_MESSAGES.get(code, f"SIM provider error (HTTP {response.status_code}).")

        data = payload.get("data")
        records = [
            _record_from_fields(
                item.get("fields") if isinstance(item.get("fields"), dict) else {},
                source=str(item.get("source") or "SIM / CNIC Records"),
                show_address=show_address,
            )
            for item in (data or [])
            if isinstance(item, dict)
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
