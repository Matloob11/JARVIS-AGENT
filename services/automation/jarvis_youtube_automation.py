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

import yt_dlp

try:
    from youtube_search import YoutubeSearch
except ImportError:
    YoutubeSearch = None

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import tool_result
from services.utils import jarvis_delivery
from services.utils.jarvis_config import config
from services.utils.jarvis_delivery import DeliveryError
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-YOUTUBE")

_YT_DLP_SEARCH_OPTS: dict[str, Any] = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": "in_playlist",
    "noplaylist": True,
    "socket_timeout": 10,
}


class YouTubeAutomation:
    """
    Automates YouTube interactions in the browser.
    """

    def __init__(self):
        pass

    @staticmethod
    def _watch_url_from_id(video_id: str, title: str = "") -> dict[str, str]:
        return {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "video_id": video_id,
            "title": title or "",
        }

    def _yt_dlp_search_sync(self, query: str) -> dict[str, str] | None:
        with yt_dlp.YoutubeDL(_YT_DLP_SEARCH_OPTS) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entries = (info or {}).get("entries") or []
        if not entries:
            return None
        entry = entries[0] or {}
        video_id = entry.get("id")
        if not video_id:
            return None
        return self._watch_url_from_id(str(video_id), str(entry.get("title") or ""))

    @staticmethod
    def _youtube_search_pkg_sync(query: str) -> dict[str, str] | None:
        if YoutubeSearch is None:
            return None
        results: Any = YoutubeSearch(query, max_results=1).to_dict()
        if not results:
            return None
        video_id = results[0].get("id")
        if not video_id:
            return None
        title = str(results[0].get("title") or results[0].get("titleSnippet") or "")
        return YouTubeAutomation._watch_url_from_id(str(video_id), title)

    @staticmethod
    def _youtubesearchpython_sync(query: str) -> dict[str, str] | None:
        try:
            from youtubesearchpython import VideosSearch
        except ImportError:
            return None
        payload = VideosSearch(query, limit=1).result()
        hits = (payload or {}).get("result") or []
        if not hits:
            return None
        video_id = hits[0].get("id")
        if not video_id:
            return None
        title = str(hits[0].get("title") or "")
        return YouTubeAutomation._watch_url_from_id(str(video_id), title)

    async def resolve_video(self, query: str) -> dict[str, str] | None:
        """Resolve query to a canonical watch URL and metadata (never a search page)."""
        logger.info("Searching for video URL: %s", query)
        searchers = (
            self._yt_dlp_search_sync,
            self._youtube_search_pkg_sync,
            self._youtubesearchpython_sync,
        )
        for search in searchers:
            try:
                meta = await asyncio.to_thread(search, query)
            except Exception as e:  # noqa: BLE001 — optional backends may fail (network, httpx, yt-dlp)
                logger.warning("YouTube search backend failed (%s): %s", search.__name__, e)
                continue
            if meta and meta.get("url") and "/watch?v=" in meta["url"]:
                logger.info("Found YouTube URL: %s", meta["url"])
                return meta
        return None

    async def get_video_url(self, query: str) -> str | None:
        """
        Searches for a YouTube video and returns the canonical watch URL (K-22).
        Never returns a search-results URL.
        """
        meta = await self.resolve_video(query)
        return meta["url"] if meta else None

    async def open_url_in_app(self, url: str):
        """Opens a URL in Browser App mode for a dedicated, app-like experience."""
        try:
            logger.info("Attempting to open URL in App Mode: %s", url)
            browser_path = await asyncio.to_thread(self._get_browser_path)

            if os.name == "nt" and browser_path:
                logger.info("Launching browser in App Mode: %s", browser_path)
                await asyncio.to_thread(
                    subprocess.Popen,
                    [
                        browser_path,
                        f"--app={url}",
                        "--autoplay-policy=no-user-gesture-required",
                    ],
                )
                return True

            logger.info("No browser path found. Falling back to delivery open_url.")
            await asyncio.to_thread(jarvis_delivery.open_url, url)
            return True

        except (subprocess.SubprocessError, OSError, Exception) as e:
            logger.error("Browser App Mode failed: %s. Trying open_url fallback.", e)
            try:
                await asyncio.to_thread(jarvis_delivery.open_url, url)
                return True
            except (DeliveryError, Exception) as fallback_err:
                logger.error("All browser open methods failed: %s", fallback_err)
                return False

    def _get_browser_path(self):
        """Finds MS Edge or Chrome path for App Mode (config first, then PATH / known paths)."""
        edge = getattr(config, "edge_path", None)
        if edge and os.path.isfile(edge):
            return edge

        chrome = getattr(config, "chrome_path", None)
        if chrome and os.path.isfile(chrome):
            return chrome

        edge_which = shutil.which("msedge")
        if edge_which:
            return edge_which

        chrome_which = shutil.which("chrome") or shutil.which("google-chrome")
        if chrome_which:
            return chrome_which

        if os.name == "nt":
            common_paths = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
            for path in common_paths:
                if os.path.exists(path):
                    return path
        return None


# Global Instance
yt_bot = YouTubeAutomation()


@jarvis_tool(execution_timeout=30.0)
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
    meta = await yt_bot.resolve_video(query)
    if meta:
        await yt_bot.open_url_in_app(meta["url"])
        msg = f"✅ YouTube par '{query}' play kar raha hoon, Sir."
        return tool_result(
            "success",
            msg,
            data={
                "url": meta["url"],
                "video_id": meta["video_id"],
                "title": meta.get("title") or "",
            },
            action="play",
            query=query,
            url=meta["url"],
        )
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
