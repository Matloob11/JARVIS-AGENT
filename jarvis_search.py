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
from urllib.parse import quote
from duckduckgo_search import DDGS
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SEARCH")

load_dotenv()

# ✅ Correct way to get keys from environment variables
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


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
    except Exception as e:
        logger.warning("Error getting current city: %s", e)
        return os.getenv("USER_CITY", "Lahore")


@function_tool
async def search_internet(query: str) -> dict:
    """
    Perform a high-quality internet search.
    Cascades through: Tavily -> Google Custom Search -> DuckDuckGo.
    """
    # 1. Try Tavily (Best for AI Agents)
    if TAVILY_API_KEY:
        tavily_result = await search_tavily(query)
        if tavily_result["status"] == "success":
            return tavily_result

    # 2. Fallback to Google Custom Search
    if GOOGLE_SEARCH_API_KEY and SEARCH_ENGINE_ID:
        google_result = await search_google(query)
        if google_result["status"] == "success":
            return google_result

    # 3. Last Resort: DuckDuckGo
    return await search_duckduckgo(query)


async def search_tavily(query: str) -> dict:
    """Uses Tavily API for smart research."""
    logger.info("Searching Tavily for: '%s'", query)
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 3
    }

    try:
        response = await asyncio.to_thread(requests.post, url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            return {"status": "not_found", "message": "No Tavily results."}

        summary = "\n\n".join(
            [f"{r['title']}\n{r['content']}\n{r['url']}" for r in results])
        return {
            "status": "success",
            "provider": "tavily",
            "results": results,
            "message": f"[TAVILY SEARCH]\n{summary}"
        }
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
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
            return {"status": "not_found", "message": "No Google results."}

        results = []
        for item in data["items"][:3]:
            results.append({
                "title": item.get("title", "No title"),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", "")
            })

        summary = "\n\n".join(
            [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in results])
        return {
            "status": "success",
            "provider": "google",
            "results": results,
            "message": f"[GOOGLE SEARCH]\n{summary}"
        }
    except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
        logger.warning("Google Search failed: %s", e)
        return {"status": "error", "message": str(e)}


async def search_duckduckgo(query: str) -> dict:
    """
    Fallback search using DuckDuckGo.
    """
    logger.info("Attempting DuckDuckGo fallback for: '%s'", query)
    try:
        def _ddgs_sync():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=3))

        results = await asyncio.to_thread(_ddgs_sync)
        if not results:
            return {"status": "not_found", "message": f"DuckDuckGo search returned no results for: {query}"}

        formatted_results = []
        for r in results:
            formatted_results.append({
                "title": r.get("title", "No Title"),
                "snippet": r.get("body", ""),
                "link": r.get("href", "")
            })

        summary = "\n\n".join(
            [f"{r['title']}\n{r['snippet']}\n{r['link']}" for r in formatted_results])
        return {
            "status": "success",
            "provider": "duckduckgo",
            "query": query,
            "message": f"[BACKUP SEARCH]\n{summary}"
        }
    except (RuntimeError, AttributeError, KeyError, ValueError) as e:
        logger.error("DuckDuckGo fallback also failed: %s", e)
        return {"status": "error", "message": "Search failed on both Google and DuckDuckGo."}


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
