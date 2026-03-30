"""
Jarvis YouTube Automation Module
Handles searching and playing videos on YouTube.
"""
# pylint: disable=consider-using-with
import asyncio
import os
import shutil
import subprocess
import webbrowser
from typing import Any
from urllib.parse import quote

try:
    from youtube_search import YoutubeSearch
    _HAS_YT_SEARCH = True
except ImportError:
    _HAS_YT_SEARCH = False
    YoutubeSearch = None

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-YOUTUBE")


class YouTubeAutomation:
    """
    Automates YouTube interactions in the browser.
    """

    def __init__(self):
        pass

    async def get_video_url(self, query: str) -> str | None:
        """
        Searches for a YouTube video and returns a direct embed URL for ad-minimal playback.
        Falls back to a YouTube search URL if youtube_search package is not installed.
        """
        try:
            logger.info("Searching for video URL: %s", query)

            if _HAS_YT_SEARCH and YoutubeSearch:
                from typing import cast
                def perform_search() -> list[dict[str, Any]]:
                    results: Any = YoutubeSearch(query, max_results=1).to_dict()
                    return cast(list[dict[str, Any]], results)

                results: list[dict[str, Any]] = await asyncio.to_thread(perform_search)

                if results:
                    video_id: Any = results[0].get('id')
                    if video_id:
                        url: str = f"https://www.youtube.com/watch?v={video_id}&autoplay=1&rel=0&modestbranding=1"
                        logger.info("Found High-Compatibility URL: %s", url)
                        return url
            else:
                # Fallback: open YouTube search page directly
                logger.info("youtube_search not installed, falling back to search URL")
                return f"https://www.youtube.com/results?search_query={quote(query)}"

            return None

        except (RuntimeError, ValueError, KeyError, AttributeError) as e:
            logger.error("Error finding video URL: %s", e)
            return None

    async def open_url_in_app(self, url: str):
        """Opens a URL in Browser App mode for a dedicated, app-like experience."""
        try:
            logger.info("Attempting to open URL in App Mode: %s", url)
            browser_path = await asyncio.to_thread(self._get_browser_path)

            if os.name == 'nt' and browser_path:
                logger.info("Launching browser in App Mode: %s", browser_path)
                # App mode provides a borderless, dedicated window
                await asyncio.create_subprocess_exec(browser_path, f"--app={url}")
                return True

            # Fallback to default browser
            logger.info("Falling back to default browser protocol.")
            await asyncio.to_thread(webbrowser.open, url)
            return True
        except (subprocess.SubprocessError, OSError) as e:
            logger.error("Failed to open URL: %s", e)
            return False

    def _get_browser_path(self):
        """Finds MS Edge or Chrome path for App Mode."""
        # Check for Edge first (usually better on Windows)
        edge = shutil.which("msedge")
        if edge:
            return edge

        # Check for Chrome
        chrome = shutil.which("chrome") or shutil.which("google-chrome")
        if chrome:
            return chrome

        if os.name == 'nt':
            common_paths = [
                # Edge
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                # Chrome
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
            for path in common_paths:
                if os.path.exists(path):
                    return path
        return None


# Global Instance
yt_bot = YouTubeAutomation()


@jarvis_tool
async def automate_youtube(action: str, query: str = "") -> dict[str, Any]:
    """
    Automates YouTube to play videos, search, or just open the homepage.
    """
    try:
        logger.info("YouTube Automation: Action=%s, Query=%s", action, query)
        query = query.strip() if query else ""

        if action in ["search", "play"] and query.lower() == "youtube":
            action = "open"

        if action == "open":
            return await _handle_open()

        if not query:
            return {"status": "error", "message": "❌ Error: Query is required."}

        if action == "play":
            return await _handle_play(query)

        if action == "search":
            return await _handle_search(query)

        return {"status": "error", "message": f"❌ Unknown action: {action}."}

    except (AttributeError, KeyError, RuntimeError, ValueError) as e:
        logger.exception("YouTube automation error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error in YouTube automation: {e!s}",
            "error": str(e),
        }


async def _handle_open() -> dict[str, Any]:
    """Helper for 'open' action."""
    homepage_url = "https://www.youtube.com"
    await yt_bot.open_url_in_app(homepage_url)
    msg = "✅ YouTube homepage khol di gayi hai."
    return {"status": "success", "action": "open", "message": msg}


async def _handle_play(query: str) -> dict[str, Any]:
    """Helper for 'play' action."""
    video_url = await yt_bot.get_video_url(query)
    if video_url:
        await yt_bot.open_url_in_app(video_url)
        msg = f"✅ YouTube par '{query}' play kar raha hoon, Sir."
        return {
            "status": "success",
            "action": "play",
            "query": query,
            "url": video_url,
            "message": msg,
        }
    fail_msg = f"❌ Maazrat Sir, '{query}' ke liye koi video nahi mili."
    return {"status": "error", "message": fail_msg}


async def _handle_search(query: str) -> dict[str, Any]:
    """Helper for 'search' action."""
    search_url = f"https://www.youtube.com/results?search_query={quote(query)}"
    await yt_bot.open_url_in_app(search_url)
    msg = f"✅ YouTube par '{query}' search kar raha hoon, Sir."
    return {
        "status": "success",
        "action": "search",
        "query": query,
        "url": search_url,
        "message": msg,
    }
