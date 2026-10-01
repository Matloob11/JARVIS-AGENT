"""
Jarvis System Info Module
Retrieves laptop information such as battery and power status.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import psutil

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import tool_result
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SYSTEM-INFO")

_VITALS_HIGH_WATERMARK = 85.0


def collect_pc_vitals() -> dict[str, Any]:
    """
    Synchronous PC vitals snapshot (K-26). Safe for asyncio.to_thread every 5–10 s.
    """
    cpu = float(psutil.cpu_percent(interval=0.3))
    ram = float(psutil.virtual_memory().percent)
    disk_path = "C:\\" if sys.platform == "win32" else "/"
    disk = float(psutil.disk_usage(disk_path).percent)

    battery: int | None = None
    plugged: bool | None = None
    secs_left: int | None = None

    bat = psutil.sensors_battery()
    if bat is not None:
        battery = int(round(bat.percent))
        plugged = bool(bat.power_plugged)
        if bat.secsleft is not None and bat.secsleft >= 0:
            secs_left = int(bat.secsleft)

    return {
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "battery": battery,
        "plugged": plugged,
        "secs_left": secs_left,
    }


def _usage_sentence(cpu: float, ram: float) -> str:
    return f" CPU {cpu:.0f}% aur RAM {ram:.0f}% use ho rahi hai."


def _battery_reply(vitals: dict[str, Any]) -> str:
    battery = vitals.get("battery")
    plugged = vitals.get("plugged")
    if battery is None:
        return "Sir, is device par battery ki maloomat available nahi hai."

    status = "charging pe hai" if plugged else "battery par chal raha hai"
    reply = f"Sir, aapka laptop abhi {battery}% charged hai aur ye {status}."
    if not plugged and battery < 20:
        reply += " Kafi kam battery hai sir, please charger laga lein."
    return reply


@jarvis_tool
async def get_laptop_info(include_usage: bool = False) -> dict[str, Any]:
    """
    Retrieves real-time information about the laptop's battery, power, and system vitals.

    Args:
        include_usage: When True, include CPU/RAM usage in the spoken reply.

    Returns:
        ToolResult dict with vitals in ``data`` (cpu, ram, disk, battery, plugged, secs_left).
    """
    try:
        vitals = await asyncio.wait_for(
            asyncio.to_thread(collect_pc_vitals),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        logger.error("collect_pc_vitals timed out")
        return tool_result(
            "error",
            "❌ Laptop info collect karte waqt timeout ho gaya.",
            tool="get_laptop_info",
            error_code="TIMEOUT",
        )
    except (AttributeError, RuntimeError, OSError) as e:
        logger.exception("Error getting laptop info: %s", e)
        return tool_result(
            "error",
            f"❌ Error retrieving laptop info: {e}",
            tool="get_laptop_info",
            error_code="VITALS_ERROR",
        )

    reply = _battery_reply(vitals)
    cpu = float(vitals["cpu"])
    ram = float(vitals["ram"])
    if include_usage or cpu >= _VITALS_HIGH_WATERMARK or ram >= _VITALS_HIGH_WATERMARK:
        reply += _usage_sentence(cpu, ram)

    return tool_result(
        "success",
        reply,
        tool="get_laptop_info",
        data=vitals,
    )
