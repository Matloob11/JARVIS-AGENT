"""
# jarvis_get_weather.py
Jarvis Weather Module

Retrieves current weather information for a specified city (or automatic detection).
"""

import asyncio
import os
import requests
from dotenv import load_dotenv
from livekit.agents import function_tool
from jarvis_logger import setup_logger

from jarvis_search import get_current_city

load_dotenv()

# Setup logging
logger = setup_logger("JARVIS-WEATHER")


@function_tool
async def get_weather(city: str = "Lahore") -> str:
    """
    Gives current weather information for a given city.

    Use this tool when the user asks about weather, rain, temperature, humidity, or wind.
    If no city is given, detect city automatically.

    Example prompts:
    - "Aaj ka mausam kaisa hai?"
    - "Weather batao Bangalore ka"
    - "Kya barish hogi Mumbai mein?"
    """

    # Get API key from environment - check both possible names
    api_key = os.getenv("OPENWEATHER_API_KEY") or os.getenv("WEATHER_API_KEY")

    if not api_key:
        logger.error("OpenWeather API key missing hai.")
        msg = ("Environment variables mein OpenWeather API key nahi mili. "
               "WEATHER_API_KEY ya OPENWEATHER_API_KEY set karein.")
        return msg

    # If no city provided or empty, detect city
    if not city or not city.strip():
        city = await get_current_city()
        logger.info("No city provided, detected city: %s", city)

    if not city:
        city = "Lahore"
        logger.info("City detection failed, using fallback: Lahore")

    logger.info("City ke liye weather fetch kiya ja raha hai: %s", city)
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric"
    }

    try:
        # Move blocking requests.get to a thread
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        if response.status_code != 200:
            logger.error(
                "Weather error: %s - %s", response.status_code, response.text)
            return f"Error: {city} ke liye weather fetch nahi kar paaye."

        data = response.json()
        weather = data["weather"][0]["description"].title()
        temperature = data["main"]["temp"]
        humidity = data["main"]["humidity"]
        wind_speed = data["wind"]["speed"]

        # Hinglish response
        result = (f"🌤️ {city} ka weather:\n"
                  f"• Mausam: {weather}\n"
                  f"• Temperature: {temperature}°C\n"
                  f"• Humidity: {humidity}%\n"
                  f"• Hawa ki speed: {wind_speed} m/s")

        return {
            "status": "success",
            "city": city,
            "weather": weather,
            "temperature": temperature,
            "humidity": humidity,
            "wind_speed": wind_speed,
            "message": result
        }

    except requests.exceptions.RequestException as e:
        logger.exception("Weather API request failed: %s", e)
        return {
            "status": "error",
            "message": f"Error: {city} ke liye weather fetch nahi kar paaye. Network issue.",
            "error": str(e)
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Weather fetch karte samay exception aaya: %s", e)
        return {
            "status": "error",
            "message": "Weather fetch karte samay ek error aaya. Kripaya thodi der baad try karein.",
            "error": str(e)
        }
