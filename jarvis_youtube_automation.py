"""
Jarvis YouTube Automation Module
Handles searching and playing videos on YouTube.
"""
import logging
import asyncio
import subprocess
import shutil
from livekit.agents import function_tool
from youtube_search import YoutubeSearch

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-YOUTUBE")


class YouTubeAutomation:
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
        """Opens a URL in Edge App mode (PWA style)"""
        try:
            logger.info("Opening URL in App Mode: %s", url)

            # Determine browser command
            # Edge is preferred on Windows
            browser_cmd = "msedge" if shutil.which("msedge") else "chrome"

            # Construct command
            # pylint: disable=consider-using-with
            subprocess.Popen(f'start {browser_cmd} --app="{url}"', shell=True)
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to open URL: %s", e)
            return False


# Global Instance
yt_bot = YouTubeAutomation()


@function_tool
async def automate_youtube(action: str, query: str = "") -> str:
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
            return "✅ YouTube homepage khol di gayi hai."

        if not query:
            return "❌ Error: Query is required for play/search actions."

        if action == "play":
            # 1. Find the direct video URL
            video_url = await yt_bot.get_video_url(query)

            if video_url:
                # 2. Open directly
                await yt_bot.open_url_in_app(video_url)
                return f"✅ Playing '{query}' on YouTube Desktop."
            return f"❌ Could not find a video for '{query}'."

        if action == "search":
            search_url = f"https://www.youtube.com/results?search_query={query}"
            await yt_bot.open_url_in_app(search_url)
            return f"✅ Searching '{query}' on YouTube Desktop."

        msg = f"❌ Unknown action: {action}. Use 'play', 'search', or 'open'."
        return msg

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error in YouTube automation: %s", e)
        return f"❌ Error in YouTube automation: {e}"
