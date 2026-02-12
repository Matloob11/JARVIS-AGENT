"""
# jarvis_search.py
Jarvis Search Module

Provides internet search functionality using Google Custom Search API
and utility functions for location and time detection.
"""

import asyncio
import logging
import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from livekit.agents import function_tool

# Setup logging for console output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] %(message)s"
)

load_dotenv()

# ✅ Correct way to get keys from environment variables
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")


async def get_current_city() -> str:
    """
    Detects the current city of the user based on IP address.
    Checks .env for USER_CITY first.
    """
    try:
        # Check if city is manually set in .env
        env_city = os.getenv("USER_CITY")
        if env_city:
            return env_city

        # Using asyncio.to_thread for blocking requests call
        response = await asyncio.to_thread(requests.get, "https://ipinfo.io", timeout=5)
        data = response.json()
        detected_city = data.get("city", "Lahore")

        if detected_city.lower() in ["unknown", "", "none"]:
            return "Lahore"
        return detected_city
    except requests.exceptions.RequestException as e:
        logging.error("Error getting current city: %s", e)
        return os.getenv("USER_CITY", "Lahore")


@function_tool
async def search_internet(query: str) -> str:
    """
    Perform a Google Custom Search for the given query and return the top 3 results.
    """
    if not GOOGLE_SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        logging.error("Google Search API credentials not found in .env")
        return "Google Search API credentials not found in .env"

    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?key={GOOGLE_SEARCH_API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}"
    )

    try:
        # Run blocking requests.get() safely in async mode
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        data = response.json()

        if "items" not in data:
            logging.warning("No results found for query: %s", query)
            return f"No results found for: {query}"

        results = []
        for item in data["items"][:3]:
            title = item.get("title", "No title")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            results.append(f"{title}\n{snippet}\n{link}\n")

        # Print results in console
        logging.info("Search results for '%s':\n%s", query, "\n".join(results))

        return "\n\n".join(results)

    except requests.exceptions.RequestException as e:
        logging.error("Error performing search: %s", e)
        return f"Error performing search: {e}"


@function_tool
async def get_formatted_datetime() -> str:
    """
    Get the current date and time in a human-readable formatted string.
    Example: "Thursday, November 13, 2025 - 07:25 PM"
    """
    now = datetime.now()
    formatted = now.strftime("%A, %B %d, %Y - %I:%M %p")

    return formatted
