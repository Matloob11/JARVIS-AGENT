"""
Safe SIM lookup workflow for JARVIS.

Uses only authorized API credentials (K-34). Without SIM_LOOKUP_API_URL and
SIM_LOOKUP_API_KEY the flow stays in a clear provider-not-configured mode.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.info.sim_providers import NOT_CONFIGURED_HINT, resolve_sim_source
from services.utils.jarvis_bridge import notify_event, notify_sim_loading
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SIM-LOOKUP")

AUDIT_FILE = Path(config.project_root) / "Jarvis_Outputs" / "sim_lookup_history.jsonl"

SAFE_MESSAGE = (
    "SIM lookup panel ready hai. Privacy-safe mode active hai, is liye CNIC, "
    "address, ya kisi third-party personal record ko public API se fetch nahi kiya jayega. "
    "Authorized dataset connect karne ke baad yahi flow real records show karega."
)

_BLOCKED_MESSAGE = f"{SAFE_MESSAGE} {NOT_CONFIGURED_HINT}"


def _lookup_sim_risk(arguments: dict[str, Any]) -> str:
    del arguments
    return "high" if resolve_sim_source().configured else "low"


def _normalize_query(query: str) -> str:
    """Keep only digits from a phone/CNIC style query."""
    return "".join(filter(str.isdigit, query or ""))


def _mask_query(query: str) -> str:
    """Mask phone/CNIC values before storing or showing them."""
    digits = _normalize_query(query)
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:3]}{'*' * max(len(digits) - 6, 3)}{digits[-3:]}"


def _is_valid_lookup_query(query: str) -> bool:
    """Allow local workflow for Pakistan mobile-like numbers or CNIC-like input."""
    digits = _normalize_query(query)
    return len(digits) in {10, 11, 12, 13}


def _purpose_sha256(purpose: str) -> str:
    return hashlib.sha256(purpose.encode("utf-8")).hexdigest()


def _write_audit_entry(entry: dict[str, Any]) -> None:
    """Append a privacy-preserving lookup audit row to Jarvis_Outputs."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_FILE.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_audit_entries() -> list[dict[str, Any]]:
    if not AUDIT_FILE.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with AUDIT_FILE.open(encoding="utf-8") as audit_file:
        for line in audit_file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                entries.append(row)
    return entries


def _today_utc() -> str:
    return datetime.now(UTC).date().isoformat()


def _count_successful_lookups_today(provider: str) -> int:
    day = _today_utc()
    count = 0
    for row in _read_audit_entries():
        ts = str(row.get("ts") or row.get("timestamp") or "")
        if not ts.startswith(day):
            continue
        if row.get("provider") != provider:
            continue
        if row.get("status") == "success":
            count += 1
    return count


def _daily_limit() -> int:
    import os

    value = (os.getenv("SIM_LOOKUP_DAILY_LIMIT") or "").strip()
    if value.isdigit():
        return max(1, int(value))
    return 20


async def _save_lookup_audit(
    query: str,
    *,
    provider: str,
    status: str,
    result_count: int = 0,
    reason: str = "",
    purpose: str = "",
) -> None:
    """Persist a masked audit event without storing raw CNIC/phone or purpose text."""
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "provider": provider,
        "query_masked": _mask_query(query),
        "status": status,
        "count": result_count,
    }
    if purpose:
        entry["purpose_sha256"] = _purpose_sha256(purpose)
    if reason:
        entry["reason"] = reason
    await asyncio.to_thread(_write_audit_entry, entry)


async def _emit_sim_data(
    records: list[Any],
    phone_masked: str,
    *,
    status: str,
    message: str,
    provider: str,
) -> None:
    await notify_event(
        "sim_data_result",
        {
            "records": records,
            "phone": phone_masked,
            "status": status,
            "message": message,
            "provider": provider,
            "timestamp": time.time(),
        },
    )


@jarvis_tool
async def open_sim_lookup_panel() -> dict[str, str]:
    """
    Opens the SIM lookup panel in the UI.

    Use this when the user asks for "sim info", "sim details", or "number info".
    """
    source = resolve_sim_source()
    panel_message = SAFE_MESSAGE if source.configured else _BLOCKED_MESSAGE
    await notify_event(
        "sim_panel_open",
        {
            "message": panel_message,
            "provider": source.provider,
            "configured": source.configured,
            "requires_purpose": source.requires_purpose,
        },
    )
    return {
        "status": "success",
        "message": "Sir, SIM lookup panel open kar diya hai. Aap number enter kar dein.",
    }


@jarvis_tool(risk=_lookup_sim_risk, timeout=35)
async def lookup_sim_data(phone_number: str, purpose: str = "") -> dict[str, Any]:
    """
    Starts the SIM lookup workflow and records a privacy-preserving audit event.

    With authorized API credentials, purpose (>= 5 chars) and a daily limit apply.
    """
    source = resolve_sim_source()
    clean_number = _normalize_query(phone_number)
    masked_number = _mask_query(clean_number)
    purpose_clean = (purpose or "").strip()

    await notify_sim_loading({"query_masked": masked_number})

    if not _is_valid_lookup_query(clean_number):
        message = (
            "Input valid phone/CNIC pattern jaisa nahi lag raha. "
            "Please number dobara check kar ke enter karein."
        )
        await _save_lookup_audit(
            clean_number,
            provider=source.provider,
            status="validation_error",
            reason=message,
        )
        await _emit_sim_data(
            [],
            masked_number,
            status="validation_error",
            message=message,
            provider=source.provider,
        )
        return {
            "status": "validation_error",
            "message": message,
            "records": [],
        }

    if not source.configured:
        logger.info("Privacy-safe SIM lookup requested for %s (provider not configured)", masked_number)
        await _save_lookup_audit(
            clean_number,
            provider=source.provider,
            status="blocked",
            reason=NOT_CONFIGURED_HINT,
        )
        await _emit_sim_data(
            [],
            masked_number,
            status="blocked",
            message=_BLOCKED_MESSAGE,
            provider=source.provider,
        )
        return {
            "status": "blocked",
            "records": [],
            "message": _BLOCKED_MESSAGE,
            "audit_path": str(AUDIT_FILE),
        }

    if len(purpose_clean) < 5:
        message = "Authorized SIM lookup ke liye purpose kam az kam 5 characters ka hona chahiye."
        await _save_lookup_audit(
            clean_number,
            provider=source.provider,
            status="validation_error",
            reason=message,
            purpose=purpose_clean,
        )
        await _emit_sim_data(
            [],
            masked_number,
            status="validation_error",
            message=message,
            provider=source.provider,
        )
        return {
            "status": "validation_error",
            "message": message,
            "records": [],
        }

    limit = _daily_limit()
    if _count_successful_lookups_today(source.provider) >= limit:
        message = f"Daily SIM lookup limit ({limit}) poori ho chuki hai. Kal dobara try karein."
        await _save_lookup_audit(
            clean_number,
            provider=source.provider,
            status="rate_limited",
            reason=message,
            purpose=purpose_clean,
        )
        await _emit_sim_data(
            [],
            masked_number,
            status="rate_limited",
            message=message,
            provider=source.provider,
        )
        return {
            "status": "rate_limited",
            "message": message,
            "records": [],
        }

    logger.info("Authorized SIM lookup for %s via %s", masked_number, source.provider)
    records, error_message = await source.lookup(clean_number)

    if error_message:
        await _save_lookup_audit(
            clean_number,
            provider=source.provider,
            status="provider_error",
            reason=error_message,
            purpose=purpose_clean,
        )
        await _emit_sim_data(
            [],
            masked_number,
            status="provider_error",
            message=error_message,
            provider=source.provider,
        )
        return {
            "status": "provider_error",
            "message": error_message,
            "records": [],
        }

    await _save_lookup_audit(
        clean_number,
        provider=source.provider,
        status="success",
        result_count=len(records),
        purpose=purpose_clean,
    )
    success_message = (
        f"{len(records)} record(s) milay."
        if records
        else "Is number ke liye koi record nahi mila."
    )
    await _emit_sim_data(
        records,
        masked_number,
        status="success",
        message=success_message,
        provider=source.provider,
    )
    return {
        "status": "success",
        "records": records,
        "message": success_message,
        "audit_path": str(AUDIT_FILE),
    }
