"""
Jarvis System Info Module
Retrieves laptop information such as battery and charging status.
"""
import psutil
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SYSTEM-INFO")


@function_tool
async def get_laptop_info() -> str:
    """
    Retrieves real-time information about the laptop's battery and power status.

    Returns:
        str: A string containing the battery percentage and whether it is plugged in.
    """
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return "❌ Battery information is not available on this device."

        percent = battery.percent
        plugged_in = battery.power_plugged

        status = "charging pe hai" if plugged_in else "battery par chal raha hai"
        reply = f"Sir, aapka laptop abhi {percent}% charged hai aur ye {status}."

        if not plugged_in and percent < 20:
            reply += " Kafi kam battery hai sir, please charger laga lein."

        return reply

    except (AttributeError, RuntimeError, OSError) as e:
        logger.exception("Error getting laptop info: %s", e)
        return f"❌ Error retrieving laptop info: {e}"
