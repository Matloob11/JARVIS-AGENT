"""
# services/info/jarvis_map.py
Jarvis Mapping & Geocoding Tool (Nominatim policy + map focus zoom)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from geopy.extra.rate_limiter import RateLimiter  # pylint: disable=import-error
from geopy.geocoders import Nominatim  # pylint: disable=import-error

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_bridge import notify_location
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MAP")

# Nominatim usage policy: identifying UA + at most 1 request/s
_geolocator = Nominatim(
    user_agent="jarvis_personal_assistant_v2 (JARVIS-AGENT)",
    timeout=10,
)
_geocode = RateLimiter(_geolocator.geocode, min_delay_seconds=1)


def _geocode_sync(location_name: str) -> Any:
    return _geocode(location_name)


@jarvis_tool
async def show_location_on_map(location_name: str, zoom: int = 13) -> dict[str, Any]:
    """
    Updates the JARVIS holographic map to show a specific location.
    Use this when the user asks to see a place or asks for its location.
    Args:
        location_name: Name of the city, country, address, or landmark.
        zoom: Map zoom level (default 13).
    """
    logger.info("Updating map to: %s (zoom=%s)", location_name, zoom)
    try:
        zoom_i = int(zoom) if zoom is not None else 13
        location = await asyncio.to_thread(_geocode_sync, location_name)

        if not location:
            logger.warning("Could not find location: %s", location_name)
            msg = (
                f"I couldn't find the coordinates for '{location_name}'. "
                "Please be more specific."
            )
            return {
                "status": "not_found",
                "address": "",
                "lat": None,
                "lng": None,
                "zoom": zoom_i,
                "message": msg,
            }

        payload = {
            "city": location_name.capitalize(),
            "label": location.address,
            "lat": location.latitude,
            "lng": location.longitude,
            "kind": "map",
            "source": "geocode",
            "zoom": zoom_i,
            "ts": time.time(),
        }
        await notify_location(payload)
        logger.info("Map sync successful for %s", location_name)
        msg = (
            f"Understood. I've updated the map to show {location.address}. "
            "All systems synced."
        )
        return {
            "status": "success",
            "address": location.address,
            "lat": location.latitude,
            "lng": location.longitude,
            "zoom": zoom_i,
            "message": msg,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Map sync error: %s", e)
        return {
            "status": "error",
            "address": "",
            "lat": None,
            "lng": None,
            "zoom": int(zoom) if zoom is not None else 13,
            "message": f"Error syncing map for {location_name}: {e!s}",
        }
