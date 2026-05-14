"""
Safe SIM lookup workflow for JARVIS.

This module intentionally does not call public personal-data APIs. It provides
the product flow, UI activation, validation, and audit trail needed for an
authorized lookup system without exposing CNIC/address data from third parties.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_bridge import notify_event, notify_sim_data, notify_sim_loading
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SIM-LOOKUP")

AUDIT_FILE = Path(config.project_root) / "Jarvis_Outputs" / "sim_lookup_history.jsonl"

SAFE_MESSAGE = (
    "SIM lookup panel ready hai. Privacy-safe mode active hai, is liye CNIC, "
    "address, ya kisi third-party personal record ko public API se fetch nahi kiya jayega. "
    "Authorized dataset connect karne ke baad yahi flow real records show karega."
)


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


def _write_audit_entry(entry: dict[str, Any]) -> None:
    """Append a privacy-preserving lookup audit row to Jarvis_Outputs."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_FILE.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def _save_lookup_audit(
    query: str,
    status: str,
    result_count: int = 0,
    reason: str = "",
) -> None:
    """Persist a masked audit event without storing raw CNIC/phone data."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "query_masked": _mask_query(query),
        "status": status,
        "result_count": result_count,
        "reason": reason,
    }
    await asyncio.to_thread(_write_audit_entry, entry)


@jarvis_tool
async def open_sim_lookup_panel() -> dict[str, str]:
    """
    Opens the SIM lookup panel in the UI.

    Use this when the user asks for "sim info", "sim details", or "number info".
    """
    await notify_event("sim_panel_open", {"message": SAFE_MESSAGE})
    return {
        "status": "success",
        "message": "Sir, SIM lookup panel open kar diya hai. Aap number enter kar dein.",
    }


@jarvis_tool
async def lookup_sim_data(phone_number: str) -> dict[str, Any]:
    """
    Starts the SIM lookup workflow and records a safe audit event.

    This product-safe version does not fetch CNIC/address/name from public APIs.
    """
    clean_number = _normalize_query(phone_number)

    masked_number = _mask_query(clean_number)
    await notify_sim_loading({"query_masked": masked_number})

    if not _is_valid_lookup_query(clean_number):
        message = (
            "Input valid phone/CNIC pattern jaisa nahi lag raha. "
            "Please number dobara check kar ke enter karein."
        )
        await _save_lookup_audit(clean_number, "validation_error", reason=message)
        await notify_sim_data([], masked_number, status="validation_error", message=message)
        return {
            "status": "validation_error",
            "message": message,
            "records": [],
        }

    logger.info("Privacy-safe SIM lookup requested for %s", masked_number)

    await _save_lookup_audit(
        clean_number,
        "blocked_requires_authorized_source",
        reason="Public personal-data API lookup disabled.",
    )
    await notify_sim_data(
        [],
        masked_number,
        status="blocked",
        message=SAFE_MESSAGE,
    )

    return {
        "status": "blocked",
        "records": [],
        "message": SAFE_MESSAGE,
        "audit_path": str(AUDIT_FILE),
    }
