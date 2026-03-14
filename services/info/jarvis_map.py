"""
# services/info/jarvis_map.py
Jarvis Mapping & Geocoding Tool
"""

import asyncio
from geopy.geocoders import Nominatim # pylint: disable=import-error
from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_bridge import notify_location
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MAP")

# Initialize Nominatim geocoder with a unique user_agent
geolocator = Nominatim(user_agent="jarvis_personal_assistant_v2")

@jarvis_tool
async def show_location_on_map(location_name: str) -> str:
    """
    Updates the JARVIS holographic map to show a specific location.
    Use this when the user asks to see a place or asks for its location.
    Args:
        location_name: Name of the city, country, address, or landmark.
    """
    logger.info("🗺️ Updating map to: %s", location_name)
    try:
        # Geocoding - Nominatim is free but we must respect their usage policy (1 request per sec)
        # We use asyncio.to_thread for blocking geopy calls
        location = await asyncio.to_thread(geolocator.geocode, location_name)

        if not location:
            logger.warning("❌ Could not find location: %s", location_name)
            return f"I couldn't find the coordinates for '{location_name}'. Please be more specific."

        payload = {
            "city": location_name.capitalize(),
            "lat": location.latitude,
            "lng": location.longitude
        }
        # Notify UI Through Bridge
        await notify_location(payload)
        logger.info("✅ Map sync successful for %s", location_name)
        return f"Understood. I've updated the map to show {location.address}. All systems synced."

    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Map sync error: %s", e)
        return f"Error syncing map for {location_name}: {str(e)}"
