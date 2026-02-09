import os
import requests
import logging
from dotenv import load_dotenv
from livekit.agents import function_tool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_current_city():
    try:
        # Check if city is manually set in .env
        env_city = os.getenv("USER_CITY")
        if env_city:
            return env_city

        response = requests.get("https://ipinfo.io", timeout=5)
        data = response.json()
        detected_city = data.get("city", "Lahore")
        if detected_city.lower() in ["unknown", "", "none"]:
            return "Lahore"
        return detected_city
    except Exception as e:
        logger.error(f"Error getting current city: {e}")
        return os.getenv("USER_CITY", "Lahore")

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
        return "Environment variables mein OpenWeather API key nahi mili. WEATHER_API_KEY ya OPENWEATHER_API_KEY set karein."

    # If no city provided or empty, use Lahore as default
    if not city or city.strip() == "":
        city = "Lahore"
        logger.info("No city provided, using default: Lahore")
    
    logger.info(f"City ke liye weather fetch kiya ja raha hai: {city}")
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric"
    }

    try:
        response = requests.get(url, params=params)
        if response.status_code != 200:
            logger.error(f"OpenWeather API mein error aaya: {response.status_code} - {response.text}")
            return f"Error: {city} ke liye weather fetch nahi kar paaye. Kripaya city name check karein."

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

        logger.info(f"Weather result: \n{result}")
        return result

    except Exception as e:
        logger.exception(f"Weather fetch karte samay exception aaya: {e}")
        return "Weather fetch karte samay ek error aaya. Kripaya thodi der baad try karein."
    

