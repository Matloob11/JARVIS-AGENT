"""
# confirm_policy.py
K-45 static risk list for the bridge (C-29 part 2).

The committed floor under every confirm_request's risk: the K-45 HIGH and MEDIUM tool lists of
docs/CONTRACTS.md plus every fixed 'medium' or 'high' risk in the committed tool metadata
(services/ai_core/desktop_manifest.json, K-71). The bridge may only RAISE a request's risk to the
floor, never lower it (K-45: a confirm may only get stricter); a tool in no list and with no fixed
manifest risk has no floor, because its risk can be dynamic (K-16) or raised per call (K-76), and
the agent's own level is trusted for it. The module has no project imports, so the bridge container
loads it without the agent's dependencies, and a missing or unreadable manifest only loses the
manifest-derived floors (one WARNING), never the K-45 lists.

Note (C-29 part 2): a confirm_request below 'medium' never reaches the clamp — the bridge rejects a
'low' or unknown risk as bad_payload first (K-45 v10) — so the MEDIUM floors only pin the table and
its tests; today only a HIGH floor can change a request (medium -> high).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger("UI-BRIDGE")

# Risk order (K-45): the floor may only raise a request within this scale.
RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

# K-45 HIGH (UI confirmation required), exactly the list in docs/CONTRACTS.md (the contract text is
# authoritative: sleep_system is HIGH here and in src/core/desktop/policy.py; K-16's 15/medium is a
# known follow-up for the owner, see NOTES).
K45_HIGH: frozenset[str] = frozenset({
    "shutdown_system", "restart_system", "sleep_system", "empty_recycle_bin", "run_cmd_command",
    "write_custom_code", "autonomous_self_repair", "send_email", "autonomous_research_and_email",
    "automate_whatsapp", "start_file_access_server", "zip_files", "lookup_sim_data",
    "guard_selftest"})
# K-45 MEDIUM (runs with a UI toast and an audit row; a confirm_request for one keeps at least medium).
K45_MEDIUM: frozenset[str] = frozenset({
    "type_text_tool", "press_hotkey_tool", "press_key_tool", "mouse_click_tool",
    "chrome_browser_perform_action", "automate_chrome_browser", "download_youtube_media",
    "download_images", "create_folder"})

# The committed tool metadata (K-71): <repo root>/services/ai_core/desktop_manifest.json.
MANIFEST_PATH: Path = Path(__file__).resolve().parents[2] / "services" / "ai_core" / "desktop_manifest.json"


def manifest_floors(manifest: Any) -> dict[str, str]:
    """
    The fixed per-tool floors of one parsed desktop manifest: every tools[] entry whose
    meta.risk is exactly 'medium' or 'high'. 'low', 'dynamic', a missing risk or an unusable
    entry adds no floor (a dynamic risk is decided per call, so the agent's level is trusted).
    """
    floors: dict[str, str] = {}
    tools = manifest.get("tools") if isinstance(manifest, Mapping) else None
    if not isinstance(tools, list):
        return floors
    for entry in tools:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("name")
        meta = entry.get("meta")
        risk = meta.get("risk") if isinstance(meta, Mapping) else None
        if isinstance(name, str) and name and risk in ("medium", "high"):
            floors[name] = risk
    return floors


def _load_manifest_floors(path: Path = MANIFEST_PATH) -> dict[str, str]:
    """manifest_floors of the committed manifest file; {} with one WARNING when it cannot be read."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return manifest_floors(json.load(handle))
    except (OSError, ValueError) as e:
        logger.warning("K-45 floors: desktop manifest not readable (%s); only the contract lists apply",
                       type(e).__name__)
        return {}


def build_static_floors(manifest_risks: Mapping[str, str] | None = None) -> dict[str, str]:
    """The static K-45 table: per tool the strictest of the contract lists and the manifest's fixed risk."""
    floors: dict[str, str] = {}
    sources: list[Mapping[str, str]] = [
        dict(manifest_risks or {}),
        {tool: "medium" for tool in K45_MEDIUM},
        {tool: "high" for tool in K45_HIGH},
    ]
    for source in sources:
        for tool, risk in source.items():
            if risk not in RISK_RANK:
                continue
            current = floors.get(tool)
            if current is None or RISK_RANK[risk] > RISK_RANK[current]:
                floors[tool] = risk
    return floors


# Built once at import; the manifest is committed, so a restart is the only way it changes (static list).
STATIC_FLOORS: dict[str, str] = build_static_floors(_load_manifest_floors())


def static_risk_floor(tool: Any) -> str | None:
    """The K-45 floor of one tool name ('medium'|'high'), or None when the static table has no entry."""
    if not isinstance(tool, str):
        return None
    return STATIC_FLOORS.get(tool)


def clamp_risk(tool: Any, risk: str) -> tuple[str, bool]:
    """
    (effective risk, clamped) of one confirm_request: the sent risk raised to the tool's K-45
    floor when it was below it, never lowered. An unknown risk label is returned unchanged.
    """
    floor = static_risk_floor(tool)
    if floor is None or risk not in RISK_RANK or RISK_RANK[risk] >= RISK_RANK[floor]:
        return risk, False
    return floor, True
