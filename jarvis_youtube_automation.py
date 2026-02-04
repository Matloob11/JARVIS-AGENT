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
            logger.info(f"Searching for video URL: {query}")
            
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
                logger.info(f"Found Video URL: {url}")
                return url
                
            return None
            
        except Exception as e:
            logger.error(f"Error finding video URL: {e}")
            return None

    async def open_url_in_app(self, url: str):
        """Opens a URL in Edge App mode (PWA style)"""
        try:
            logger.info(f"Opening URL in App Mode: {url}")
            
            # Determine browser command
            # Edge is preferred on Windows
            browser_cmd = "msedge" if shutil.which("msedge") else "chrome"
            
            # Construct command
            # --app=URL launches in independent window without address bar
            subprocess.Popen(f'start {browser_cmd} --app={url}', shell=True)
            return True
        except Exception as e:
            logger.error(f"Failed to open URL: {e}")
            return False

# Global Instance
yt_bot = YouTubeAutomation()

@function_tool
async def automate_youtube(action: str, query: str = "") -> str:
    """
    Automates YouTube Desktop App to play videos or search.
    
    Args:
        action: The action to perform. Options: "play", "search".
        query: The video name or search term.
    """
    try:
        logger.info(f"YouTube Automation: Action={action}, Query={query}")
        
        if not query:
            return "❌ Error: Query is required."

        if action == "play":
            # 1. Find the direct video URL
            video_url = await yt_bot.get_video_url(query)
            
            if video_url:
                # 2. Open directly
                await yt_bot.open_url_in_app(video_url)
                return f"✅ Playing '{query}' on YouTube Desktop."
            else:
                return f"❌ Could not find a video for '{query}'."

        elif action == "search":
            # Just open the search results page
            search_url = f"https://www.youtube.com/results?search_query={query}"
            await yt_bot.open_url_in_app(search_url)
            return f"✅ Searching '{query}' on YouTube Desktop."
        
        else:
            return f"❌ Unknown action: {action}."

    except Exception as e:
        logger.error(f"Error in YouTube automation: {e}")
        return f"❌ Error in YouTube automation: {str(e)}"
