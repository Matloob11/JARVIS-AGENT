"""
# jarvis_get_weather.py
Jarvis Weather Module

Retrieves current weather information for a specified city (or automatic detection).
"""

import asyncio
import os
from collections import Counter
from typing import Any

import requests

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.info import jarvis_search
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

# Setup logging
logger = setup_logger("JARVIS-WEATHER")

_INVALID_KEY_MSG = (
    "OpenWeather key invalid ya abhi active nahi (naye keys ~2 ghante mein active hote hain)"
)


async def _resolve_city(city: str) -> str:
    city = (city or "").strip()
    if city:
        return city
    city = (await jarvis_search.get_current_city()).strip()
    if city:
        return city
    city = (os.getenv("USER_CITY") or "").strip()
    return city or "Lahore"


def _summarize_forecast_24h(items: list[dict[str, Any]]) -> dict[str, Any]:
    temps: list[float] = []
    pops: list[float] = []
    rain_mm = 0.0
    descriptions: list[str] = []

    for item in items:
        main = item.get("main") or {}
        for key in ("temp", "temp_min", "temp_max"):
            val = main.get(key)
            if val is not None:
                temps.append(float(val))
        pop = item.get("pop")
        if pop is not None:
            pops.append(float(pop))
        rain = item.get("rain") or {}
        if isinstance(rain, dict):
            rain_mm += float(rain.get("3h") or 0)
        weather = item.get("weather") or []
        if weather:
            desc = (weather[0].get("description") or "").strip()
            if desc:
                descriptions.append(desc)

    min_temp = min(temps) if temps else None
    max_temp = max(temps) if temps else None
    max_pop_pct = round(max(pops) * 100) if pops else 0
    main_desc = (
        Counter(descriptions).most_common(1)[0][0].title()
        if descriptions
        else "Unknown"
    )

    return {
        "min_temp": min_temp,
        "max_temp": max_temp,
        "rain_probability_pct": max_pop_pct,
        "rain_mm": round(rain_mm, 1),
        "description": main_desc,
    }


def _forecast_message(city: str, block: dict[str, Any]) -> str:
    min_t = block.get("min_temp")
    max_t = block.get("max_temp")
    rain_pct = block.get("rain_probability_pct", 0)
    rain_mm = block.get("rain_mm", 0)
    desc = block.get("description", "")
    return (
        f"🌧️ {city} — agle 24 ghante ka mausam:\n"
        f"• Halat: {desc}\n"
        f"• Temperature: {min_t}°C se {max_t}°C\n"
        f"• Barish ki probability: {rain_pct}%\n"
        f"• Barish (andaza): {rain_mm} mm"
    )


@jarvis_tool(execution_timeout=25)
async def get_weather(city: str = "", forecast: bool = False) -> str | dict[str, Any]:
    """
    Gives current or 24-hour forecast weather for a city.

    Use this tool when the user asks about weather, rain, temperature, humidity, or wind.
    Leave city empty to auto-detect location (IP / USER_CITY).
    Set forecast=True when the user asks about rain later, tomorrow, or the next 24 hours.

    Example prompts:
    - "Aaj ka mausam kaisa hai?"
    - "Weather batao Bangalore ka"
    - "Kal Lahore mein barish hogi?"
    - "Agle 24 ghante Mumbai mein barish ka chance?"
    """

    api_key = os.getenv("OPENWEATHER_API_KEY") or os.getenv("WEATHER_API_KEY")

    if not api_key:
        logger.error("OpenWeather API key missing hai.")
        return {
            "status": "error",
            "error": "OpenWeather API key missing",
            "message": "Maazrat Sir! OpenWeather API key nahi mili. .env file check karein.",
        }

    city = await _resolve_city(city)
    logger.info("City ke liye weather fetch kiya ja raha hai: %s (forecast=%s)", city, forecast)

    if forecast:
        return await _get_forecast(city, api_key)

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric",
    }

    try:
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        if response.status_code == 401:
            logger.warning("OpenWeather 401 — invalid or inactive key.")
            return await get_weather_via_search(city, key_notice=_INVALID_KEY_MSG)
        if response.status_code != 200:
            logger.warning(
                "Weather API error %s. Falling back to search.", response.status_code)
            return await get_weather_via_search(city)

        data = response.json()
        weather = data["weather"][0]["description"].title()
        temperature = data["main"]["temp"]
        humidity = data["main"]["humidity"]
        wind_speed = data["wind"]["speed"]

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
            "message": result,
        }

    except (requests.exceptions.RequestException, ValueError, KeyError, RuntimeError) as e:
        logger.warning(
            "Weather API failure: %s. Attempting search fallback.", redact_secrets(str(e)))
        return await get_weather_via_search(city)


async def _get_forecast(city: str, api_key: str) -> dict[str, Any]:
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric",
        "cnt": 8,
    }

    try:
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        if response.status_code == 401:
            logger.warning("OpenWeather forecast 401 — invalid or inactive key.")
            return await get_weather_via_search(
                city, key_notice=_INVALID_KEY_MSG, forecast=True)
        if response.status_code != 200:
            logger.warning(
                "Forecast API error %s. Falling back to search.", response.status_code)
            return await get_weather_via_search(city, forecast=True)

        data = response.json()
        items = data.get("list") or []
        if not items:
            return await get_weather_via_search(city, forecast=True)

        forecast_block = _summarize_forecast_24h(items)
        message = _forecast_message(city, forecast_block)

        return {
            "status": "success",
            "city": city,
            "forecast": forecast_block,
            "message": message,
        }

    except (requests.exceptions.RequestException, ValueError, KeyError, RuntimeError) as e:
        logger.warning(
            "Forecast API failure: %s. Attempting search fallback.", redact_secrets(str(e)))
        return await get_weather_via_search(city, forecast=True)


async def get_weather_via_search(
    city: str,
    *,
    key_notice: str | None = None,
    forecast: bool = False,
) -> dict[str, Any]:
    """
    Fallback method to get weather via internet search.
    """
    logger.info("Attempting weather fallback via search for: %s", city)
    if forecast:
        query = f"weather forecast rain probability next 24 hours in {city}"
    else:
        query = f"current weather in {city} temperature condition"
    search_result = await jarvis_search.search_internet(query)

    if search_result.get("status") == "success":
        summary = search_result.get("message", "No details found.")
        prefix = f"{key_notice}\n\n" if key_notice else ""
        label = "forecast (via Search)" if forecast else "weather (via Search)"
        return {
            "status": "success",
            "city": city,
            "provider": "search_fallback",
            "message": f"{prefix}🌤️ {city} ka {label}:\n{summary}",
        }

    err_tail = redact_secrets(
        search_result.get("message") or "API and Search failed")
    base = f"Error: {city} ke liye weather fetch nahi kar paaye ({err_tail})."
    if key_notice:
        base = f"{key_notice}\n{base}"
    return {
        "status": "error",
        "message": base,
    }
