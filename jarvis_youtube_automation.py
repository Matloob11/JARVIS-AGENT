"""
Jarvis YouTube Automation Module
Handles searching and playing videos on YouTube.
"""
# pylint: disable=consider-using-with
import asyncio
import subprocess
import shutil
import os
import webbrowser
from livekit.agents import function_tool
from youtube_search import YoutubeSearch
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-YOUTUBE")


class YouTubeAutomation:
    """
    Automates YouTube interactions in the browser.
    """

    def __init__(self):
        pass

    async def get_video_url(self, query: str):
        """
        Searches for a YouTube video using youtube-search and returns the direct URL.
        """
        try:
            logger.info("Searching for video URL: %s", query)

            def perform_search():
                # YoutubeSearch is synchronous
                results = YoutubeSearch(query, max_results=1).to_dict()
                return results

            # Run in thread to allow async
            results = await asyncio.to_thread(perform_search)

            if not results:
                return None

            video_id = results[0].get('id')
            if video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
                logger.info("Found Video URL: %s", url)
                return url

            return None

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error finding video URL: %s", e)
            return None

    async def open_url_in_app(self, url: str):
        """Opens a URL in Edge App mode (PWA style) safely."""
        try:
            logger.info("Opening URL in App Mode: %s", url)

            # Determine browser command
            edge_path = shutil.which("msedge")

            # Common Edge paths on Windows if not in PATH
            if not edge_path and os.name == 'nt':
                common_paths = [
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                    os.path.expandvars(
                        r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe")
                ]
                for path in common_paths:
                    if os.path.exists(path):
                        edge_path = path
                        break

            if os.name == 'nt' and edge_path:
                logger.info("Launching Edge in App Mode on Windows")
                # Using --app flag for the "Desktop App" look
                subprocess.Popen([edge_path, f"--app={url}"], shell=False)
                return True

            # Fallback to default browser if Edge is not found or not on Windows
            logger.info(
                "Edge not found or non-Windows, falling back to default browser")
            webbrowser.open(url)
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to open URL: %s", e)
            return False


# Global Instance
yt_bot = YouTubeAutomation()


@function_tool
async def automate_youtube(action: str, query: str = "") -> dict:
    """
    Automates YouTube to play videos, search, or just open the homepage.

    Args:
        action: The action to perform: "play", "search", or "open".
        query: The video name or search term.
    """
    try:
        logger.info("YouTube Automation: Action=%s, Query=%s", action, query)

        # Normalize query
        query = query.strip() if query else ""

        # If action is search/play but query is just "youtube", treat it as "open"
        if action in ["search", "play"] and query.lower() == "youtube":
            action = "open"

        if action == "open":
            homepage_url = "https://www.youtube.com"
            await yt_bot.open_url_in_app(homepage_url)
            return {
                "status": "success",
                "action": "open",
                "message": "✅ YouTube homepage khol di gayi hai."
            }

        if not query:
            return {
                "status": "error",
                "message": "❌ Error: Query is required for play/search actions."
            }

        if action == "play":
            # 1. Find the direct video URL
            video_url = await yt_bot.get_video_url(query)

            if video_url:
                # 2. Open directly
                await yt_bot.open_url_in_app(video_url)
                return {
                    "status": "success",
                    "action": "play",
                    "query": query,
                    "url": video_url,
                    "message": f"✅ Playing '{query}' on YouTube Desktop."
                }
            return {
                "status": "error",
                "message": f"❌ Could not find a video for '{query}'."
            }

        if action == "search":
            search_url = f"https://www.youtube.com/results?search_query={query}"
            await yt_bot.open_url_in_app(search_url)
            return {
                "status": "success",
                "action": "search",
                "query": query,
                "url": search_url,
                "message": f"✅ Searching '{query}' on YouTube Desktop."
            }

        return {
            "status": "error",
            "message": f"❌ Unknown action: {action}. Use 'play', 'search', or 'open'."
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("YouTube automation error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error in YouTube automation: {str(e)}",
            "error": str(e)
        }
