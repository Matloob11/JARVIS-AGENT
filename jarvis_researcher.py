"""
# jarvis_researcher.py
Autonomous Web Research Module for JARVIS.
Extracts and synthesizes information from multiple websites.
"""

import asyncio
from typing import List
import requests
from bs4 import BeautifulSoup
from livekit.agents import function_tool
from jarvis_search import GOOGLE_SEARCH_API_KEY, SEARCH_ENGINE_ID
from jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("JARVIS-RESEARCHER")


async def scrape_url(url: str, timeout: int = 10) -> str:
    """Extract clean text content from a URL."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
        }
        response = None
        for attempt in range(2):  # Simple retry
            try:
                # Use to_thread for the blocking requests call
                response = await asyncio.to_thread(
                    requests.get, url, headers=headers, timeout=timeout
                )
                response.raise_for_status()
                break
            except (requests.RequestException, asyncio.TimeoutError):
                if attempt == 1:
                    raise
                await asyncio.sleep(1)

        soup = BeautifulSoup(response.text, 'html.parser')

        # Remove script and style elements
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()

        # Get text and clean up whitespace
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip()
                  for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)

        # Limit to first 4000 characters to keep context manageable
        return text[:4000]
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error scraping %s: %s", url, e)
        return ""


async def get_search_urls(query: str, count: int = 5) -> List[str]:
    """Get top URLs from Google Search."""
    if not GOOGLE_SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        logger.error("Search API credentials missing")
        return []

    url = (
        f"https://www.googleapis.com/customsearch/v1"
        f"?key={GOOGLE_SEARCH_API_KEY}&cx={SEARCH_ENGINE_ID}&q={query}"
    )

    for attempt in range(2):
        try:
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return [
                item.get("link") for item in data.get("items", [])[:count]
                if item.get("link")
            ]
        except Exception as e:  # pylint: disable=broad-exception-caught
            if attempt == 1:
                logger.error("Error getting search URLs: %s", e)
                return []
            await asyncio.sleep(1)
    return []


@function_tool
async def perform_web_research(query: str) -> str:
    """
    Perform deep web research on a query by analyzing multiple websites.
    Returns a synthesized report based on the scraped content.
    """
    logger.info("Starting deep research for query: %s", query)

    urls = await get_search_urls(query)
    if not urls:
        return "Maaf Sir, main research ke liye links nahi dhoond paya. Search API check karein."

    reports = []
    tasks = [scrape_url(url) for url in urls]
    results = await asyncio.gather(*tasks)

    for i, content in enumerate(results):
        if content:
            reports.append(f"--- SOURCE {i+1}: {urls[i]} ---\n{content}\n")

    if not reports:
        return "Maaf Sir, links to mile magar contents extract nahi ho sakay."

    combined_context = "\n".join(reports)

    # Return the raw research data to the LLM.
    msg = (
        f"[RESEARCH DATA RETRIEVED]\nQuery: {query}\n\n{combined_context}\n\n"
        "[INSTRUCTION]: Sir ko is data ki base par ek detailed aur structured report dain "
        "(Natural Urdu main)."
    )
    return msg
