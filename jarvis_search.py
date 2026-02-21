"""
# jarvis_search.py
Jarvis Search Module

Provides internet search functionality using Google Custom Search API
and utility functions for location and time detection.
"""

import asyncio
import os
from datetime import datetime
import requests  # type: ignore
from dotenv import load_dotenv
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SEARCH")

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
    except (requests.exceptions.RequestException, asyncio.TimeoutError) as e:
        logger.exception("Error getting current city: %s", e)
        return os.getenv("USER_CITY", "Lahore")


@function_tool
async def search_internet(query: str) -> dict:
    """
    Perform a Google Custom Search for the given query and return the top 3 results.
    """
    if not GOOGLE_SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        logger.error("Google Search API credentials not found in .env")
        return {"status": "error", "message": "Google Search API credentials not found in .env"}

    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?key={GOOGLE_SEARCH_API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}"
    )

    for attempt in range(2):
        try:
            # Run blocking requests.get() safely in async mode
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if "items" not in data:
                logger.warning("No results found for query: %s", query)
                return {"status": "not_found", "message": f"No results found for: {query}"}

            results = []
            for item in data["items"][:3]:
                results.append({
                    "title": item.get("title", "No title"),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", "")
                })

            logger.info(
                "Search results for '%s' returned %d items", query, len(results))

            # Create a user-readable summary for the voice interface
            summary = "\n\n".join(
                [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in results])

            return {
                "status": "success",
                "query": query,
                "results": results,
                "message": summary
            }

        except Exception as e:  # pylint: disable=broad-exception-caught
            if attempt == 1:
                logger.exception("Error performing search: %s", e)
                return {
                    "status": "error",
                    "message": f"Maaf Sir, search fail ho gaye (Network issue): {e}",
                    "error": str(e)
                }
            await asyncio.sleep(1)
    return {"status": "error", "message": "Error performing search after retries."}


@function_tool
async def get_formatted_datetime() -> dict:
    """
    Get the current date and time in a human-readable formatted string.
    Example: "Thursday, November 13, 2025 - 07:25 PM"
    """
    now = datetime.now()
    return {
        "formatted": now.strftime("%A, %B %d, %Y - %I:%M %p"),
        "day": now.strftime("%A"),
        "date": now.strftime("%B %d, %Y"),
        "time": now.strftime("%I:%M %p")
    }
