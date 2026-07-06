"""
# jarvis_search.py
Jarvis Search Module

Provides internet search functionality using Google Custom Search API
and utility functions for location and time detection.
"""

import asyncio
import os
from datetime import datetime
from urllib.parse import quote

import requests  # type: ignore
from dotenv import load_dotenv
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SEARCH")

load_dotenv()

# ✅ Correct way to get keys from environment variables
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


async def get_current_city_data() -> dict:
    """
    Detects the current location of the user based on IP address.
    Returns: {"city": str, "lat": float, "lng": float}
    """
    default_data = {
        "city": os.getenv("USER_CITY", "Lahore"),
        "lat": 31.5204,
        "lng": 74.3587,
    }
    try:
        # Using asyncio.to_thread for blocking requests call
        response = await asyncio.to_thread(requests.get, "https://ipinfo.io/json", timeout=5)
        data = response.json()

        loc = data.get("loc", "").split(",")
        lat = float(loc[0]) if len(loc) == 2 else default_data["lat"]
        lng = float(loc[1]) if len(loc) == 2 else default_data["lng"]

        detected_city = data.get("city", default_data["city"])

        if detected_city.lower() in ["unknown", "", "none"]:
            detected_city = default_data["city"]

        return {
            "city": detected_city,
            "lat": lat,
            "lng": lng,
        }
    except (requests.RequestException, ValueError, KeyError, OSError, RuntimeError) as e:
        logger.warning("Error getting current location: %s", e)
        return default_data


async def get_current_city() -> str:
    """Wrapper for legacy code expecting only string."""
    data = await get_current_city_data()
    return data["city"]


@jarvis_tool
async def search_internet(query: str) -> dict:
    """
    Perform a high-quality internet search.
    Cascades through: Tavily -> Google Custom Search -> DuckDuckGo.
    This ensures reliability even if one provider is rate-limited.
    """
    error_messages = []

    # 1. Try Tavily (Best for AI Agents - Highly reliable)
    if TAVILY_API_KEY:
        try:
            tavily_result = await search_tavily(query)
            if tavily_result["status"] == "success":
                return tavily_result
            error_messages.append(f"Tavily: {tavily_result.get('message')}")
        except Exception as e:
            error_messages.append(f"Tavily Exception: {e}")

    # 2. Fallback to Google Custom Search (Excellent quality)
    if GOOGLE_SEARCH_API_KEY and SEARCH_ENGINE_ID:
        try:
            google_result = await search_google(query)
            if google_result["status"] == "success":
                return google_result
            error_messages.append(f"Google: {google_result.get('message')}")
        except Exception as e:
            error_messages.append(f"Google Exception: {e}")

    # 3. Last Resort: DuckDuckGo (Free but prone to rate limits)
    ddg_result = await search_duckduckgo(query)
    if ddg_result["status"] == "success":
        return ddg_result
    
    error_messages.append(f"DuckDuckGo: {ddg_result.get('message')}")
    
    # If all failed, return a consolidated error
    return {
        "status": "error",
        "message": "❌ Sir, tamam search engines (Tavily, Google, DDG) failed ho gaye hain. "
                   "Shayad internet connection ya API limits ka masla hai.",
        "details": "; ".join(error_messages)
    }


async def search_tavily(query: str) -> dict:
    """Uses Tavily API for smart research."""
    logger.info("Searching Tavily for: '%s'", query)
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 5,
    }

    try:
        response = await asyncio.to_thread(requests.post, url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            return {"status": "not_found", "message": "No results found on Tavily."}

        summary = "\n\n".join(
            [f"[{i+1}] {r['title']}\n{r['content']}\nSource: {r['url']}" for i, r in enumerate(results)])
        return {
            "status": "success",
            "provider": "tavily",
            "results": results,
            "message": f"[TAVILY INTELLIGENCE]\n{summary}",
        }
    except Exception as e:
        logger.warning("Tavily Search failed: %s", e)
        return {"status": "error", "message": str(e)}


async def search_google(query: str) -> dict:
    """Perform a Google Custom Search."""
    logger.info("Searching Google for: '%s'", query)
    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?key={GOOGLE_SEARCH_API_KEY}&cx={SEARCH_ENGINE_ID}&q={quote(query)}"
    )

    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "items" not in data:
            return {"status": "not_found", "message": "No Google results found."}

        results = []
        for item in data["items"][:5]:
            results.append({
                "title": item.get("title", "No title"),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            })

        summary = "\n\n".join(
            [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in results])
        return {
            "status": "success",
            "provider": "google",
            "results": results,
            "message": f"[GOOGLE SEARCH]\n{summary}",
        }
    except Exception as e:
        logger.warning("Google Search failed: %s", e)
        return {"status": "error", "message": str(e)}


async def search_duckduckgo(query: str) -> dict:
    """
    Fallback search using DuckDuckGo.
    Includes robust error handling for common provider issues.
    """
    logger.info("Attempting DuckDuckGo fallback for: '%s'", query)
    try:
        def _ddgs_sync():
            try:
                with DDGS() as ddgs:
                    # Specific exception handling for DuckDuckGo
                    return list(ddgs.text(query, max_results=5))
            except Exception as inner_e:
                if "403" in str(inner_e) or "Ratelimit" in str(inner_e):
                    logger.warning("DuckDuckGo Rate Limit (403) detected.")
                raise inner_e

        results = await asyncio.to_thread(_ddgs_sync)
        if not results:
            return {"status": "not_found", "message": "DuckDuckGo returned no results."}

        formatted_results = []
        for r in results:
            formatted_results.append({
                "title": r.get("title", "No Title"),
                "snippet": r.get("body", ""),
                "link": r.get("href", ""),
            })

        summary = "\n\n".join(
            [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in formatted_results])
        return {
            "status": "success",
            "provider": "duckduckgo",
            "query": query,
            "message": f"[DUCKDUCKGO BACKUP]\n{summary}",
        }
    except Exception as e:
        logger.error("DuckDuckGo failed: %s", e)
        return {"status": "error", "message": f"DDG error: {str(e)[:100]}"}


@jarvis_tool
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
        "time": now.strftime("%I:%M %p"),
    }
