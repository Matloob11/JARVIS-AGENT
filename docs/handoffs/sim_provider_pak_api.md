# Handoff: SIM provider → pak-sim-api (K-34 v6)

**For:** Cursor (owns `services/info/sim_providers.py` and `services/info/tests/test_cnic_lookup.py`).
**Why:** the owner connected a real authorized provider. The current `AuthorizedApiSource` posts `{"msisdn": digits}` to a single URL; the real API has **two endpoints** and a nested response, so lookups will not work until the provider client matches it. Claude has already set the credentials in the PC `.env` (`SIM_LOOKUP_API_URL`, `SIM_LOOKUP_API_KEY`), left `SIM_SOURCE=none`, and verified the key against both endpoints (phone + CNIC return `success:true`, wrong key → 401).

**Do NOT** put the base host, the key, or any raw phone/CNIC into a tracked file, a log, or a test fixture. `.env` is git-ignored; keep it that way. After this change lands and Claude flips `SIM_SOURCE=auto` + restarts, a real lookup can be tested.

## The API (confirmed live 2026-10-02)

Base = `SIM_LOOKUP_API_URL` (a base URL, no path). Auth header on every call: `Authorization: Bearer <SIM_LOOKUP_API_KEY>`, `Content-Type: application/json`. Timeout **30 s**.

- Phone: an 11-digit number starting with `0` (e.g. `03XXXXXXXXX`) → `POST {base}/api/v1/lookup/phone` body `{"phone": digits}`.
- CNIC: exactly 13 digits → `POST {base}/api/v1/lookup/cnic` body `{"cnic": digits}`.
- Any other length → a local validation error, **no HTTP call**.

Success body: `{"success": true, "query_type": "...", "hits": N, "data": [{"source": "...", "fields": {...}}]}`. The person's fields are nested under each `data[].fields` (not top-level).

Error body: `{"success": false, "error": "<code>"}` with HTTP 401 `unauthorized`, 400 `invalid_input`, 429 `rate_limited`, 502 `upstream_unavailable`.

## Proposed `services/info/sim_providers.py`

Drop-in replacement; keeps the K-34 protocol, the two source types, `resolve_sim_source`, `is_sim_lookup_configured`, `sim_source_types`, and the masking helpers. Verified logic (routing + parse) was proven against the live API.

```python
"""
Authorized SIM lookup sources for JARVIS (contract K-34 v6).

Only NotConfiguredSource and AuthorizedApiSource are permitted. Leaked, scraped,
or unofficial 'SIM database' integrations must not be added here.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

import httpx

from services.utils.jarvis_security import redact_secrets

# Known person fields inside each data[].fields object (pass the rest through, masked).
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


def _record_from_fields(fields: dict[str, Any], *, source: str, show_address: bool) -> dict[str, Any]:
    """One UI record from a data[].fields object; sensitive values masked, the rest kept."""
    record: dict[str, Any] = {
        "source": source,
        "full_name": str(fields.get(FIELD_MAP["full_name"]) or fields.get("name") or "").strip(),
        "cnic_masked": _mask_cnic_digits(str(fields.get(FIELD_MAP["cnic"]) or "")),
        "phone_masked": _mask_phone_digits(str(fields.get(FIELD_MAP["phone"]) or "")),
    }
    if show_address:
        address = str(fields.get(FIELD_MAP["address"]) or "").strip()
        if address:
            record["address"] = address
    # keep every other provider field, masking anything that looks like a CNIC/phone number
    extra: dict[str, Any] = {}
    known = set(FIELD_MAP.values()) | {"name"}
    for key, value in fields.items():
        if key in known:
            continue
        text = str(value)
        if _looks_like_phone_or_cnic(key) and any(ch.isdigit() for ch in text):
            digits = "".join(ch for ch in text if ch.isdigit())
            text = _mask_cnic_digits(digits) if len(digits) >= 13 else _mask_phone_digits(digits)
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
        show_address = _env_str("SIM_LOOKUP_SHOW_ADDRESS") in {"1", "true", "yes", "on"}
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
```

## Test notes (`services/info/tests/test_cnic_lookup.py`)

- The old test likely asserted the request body is `{"msisdn": ...}` and posts to the bare URL. Update it to assert the **phone** path/body (`/api/v1/lookup/phone`, `{"phone": "03001234567"}`) and the **cnic** path/body (`/api/v1/lookup/cnic`, `{"cnic": "1234567890123"}`), both with the Bearer header.
- Mock the httpx response with the new success shape `{"success": true, "query_type": "phone", "hits": 1, "data": [{"source": "...", "fields": {"full_name": "...", "cnic": "...", "phone": "...", "address": "..."}}]}` and assert `records[0]` has `full_name`, `cnic_masked` (last 4 only), `phone_masked`, and `details` for extra fields.
- Add error cases: 401/`unauthorized`, 429/`rate_limited`, 502/`upstream_unavailable` → `(records == [], error is the mapped Roman-Urdu message)`.
- `sim_source_types()` still returns exactly two types. Never put a real key or real number in the test.
- `lookup_sim_data` in `services/info/jarvis_sim_lookup.py` currently accepts lengths {10,11,12,13}; 10/12 will now fail routing with `_PATTERN_HINT`. Optional: tighten `_is_valid_lookup_query` to {11,13}.

When this lands, tell Claude in plan.md §11 so Claude flips `SIM_SOURCE` to `auto`, restarts the PC agent, and the owner tests a real number. For the VPS brain, these keys move to `.env.agent` (C-127), never the bridge.

## UPDATE (tested live 2026-10-03): the real response is a single `raw_text` field

SIM is already live (`SIM_SOURCE=auto`, agent restarted). A real lookup returns each record as ONE field, not the structured keys the draft assumed:

`data[0] = {"source": "SIM / CNIC Records", "fields": {"raw_text": "Mobile Name CNIC Address <11-digit-mobile> <NAME> <13-digit-CNIC> <ADDRESS>"}}`

— a header (`Mobile Name CNIC Address`) then the values: an 11-digit mobile starting 0, the name, a 13-digit CNIC, the address. So the current `_record_from_fields` (which looks for `full_name`/`cnic`/`phone` keys) returns blanks and only keeps the whole thing in `details.raw_text`, unmasked. Parse `raw_text` when the structured keys are absent, and mask the digits in the kept copy:

```python
import re

_PHONE_RE = re.compile(r"\b(0\d{10})\b")
_CNIC_RE = re.compile(r"\b(\d{13})\b")

def _parse_raw_text(text: str) -> dict[str, str]:
    """pak-sim 'Mobile Name CNIC Address <mobile> <name> <cnic> <address>' -> structured fields."""
    phone = _PHONE_RE.search(text)
    cnic = _CNIC_RE.search(text, phone.end() if phone else 0)
    out: dict[str, str] = {}
    if phone:
        out["phone"] = phone.group(1)
    if cnic:
        out["cnic"] = cnic.group(1)
        if phone:
            name = text[phone.end():cnic.start()].strip()
            if name:
                out["full_name"] = name
        address = text[cnic.end():].strip()
        if address:
            out["address"] = address
    return out
```

In `_record_from_fields`, when `fields` has no `full_name`/`name`/`cnic`/`phone` but has `raw_text`, do `fields = {**_parse_raw_text(fields["raw_text"]), "raw_text": fields["raw_text"]}` first, then build the record as before. Also mask every digit run in the kept `raw_text` (so `details.raw_text` is not a plaintext CNIC/phone). Add a test with a fake-digit `raw_text` sample asserting `full_name`, `cnic_masked` (last 4), `phone_masked`, and `address` populate, and that `details.raw_text` has no bare CNIC/phone. Never put a real number/CNIC or the key in a test or a log.
