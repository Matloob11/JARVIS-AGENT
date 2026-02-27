"""
Jarvis YouTube Downloader Module
Handles downloading videos and audio from YouTube using yt-dlp.
"""
import asyncio
import os
import subprocess
import re
from livekit.agents import function_tool
from jarvis_logger import setup_logger
from jarvis_youtube_automation import yt_bot

# Setup logging
logger = setup_logger("JARVIS-YT-DOWNLOADER")


class YouTubeDownloader:
    """Class to handle YouTube video and audio downloads."""

    def __init__(self):
        # Centralized output directory
        self.base_dir = os.path.join(
            os.getcwd(), "Jarvis_Outputs", "Downloads", "YouTube")
        os.makedirs(self.base_dir, exist_ok=True)
        # 🛠️ Set FFmpeg path if it exists at C:\ffmpeg\bin
        self.ffmpeg_path = r"C:\ffmpeg\bin"
        if not os.path.exists(os.path.join(self.ffmpeg_path, "ffmpeg.exe")):
            self.ffmpeg_path = None

    def is_valid_url(self, url: str) -> bool:
        """Simple check for YouTube URL."""
        # Split regex for line length
        yt_p = r'(https?://)?(www\.)?(youtube|youtu|music\.youtube)\.(com|be)/'
        id_p = r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
        return bool(re.match(yt_p + id_p, url))

    async def download(self, url_or_query: str, download_type: str = "audio") -> str:
        """
        Downloads media from YouTube.
        Args:
            url_or_query: The YouTube URL or a search query.
            download_type: "audio" (default) or "video".
        """
        try:
            url = url_or_query.strip()

            # 1. Get URL if it's a query
            if not self.is_valid_url(url):
                logger.info("Query detected, searching for video: %s", url)
                found_url = await yt_bot.get_video_url(url)
                if not found_url:
                    return {
                        "status": "error",
                        "message": f"❌ Error: '{url}' ke liye koi video nahi mili."
                    }
                url = found_url

            # 2. Prepare yt-dlp command
            # Centralized location
            output_template = os.path.join(self.base_dir, "%(title)s.%(ext)s")

            # Common flags
            common_flags = [
                "--no-playlist",
                "--js-runtimes", "node"  # Use available node runtime
            ]

            if download_type == "video":
                # Download best video up to 720p (for speed and compatibility)
                cmd = [
                    "yt-dlp",
                    "-f", "best[height<=720]/best",
                    "--print", "after_move:filepath",
                    "-o", output_template,
                    *common_flags,
                    url
                ]
                if self.ffmpeg_path:
                    cmd.insert(1, "--ffmpeg-location")
                    cmd.insert(2, self.ffmpeg_path)
            else:
                # Download best audio and convert to mp3
                cmd = [
                    "yt-dlp",
                    "-x",
                    "--audio-format", "mp3",
                    "--print", "after_move:filepath",
                    "-o", output_template,
                    *common_flags,
                    url
                ]
                if self.ffmpeg_path:
                    cmd.insert(1, "--ffmpeg-location")
                    cmd.insert(2, self.ffmpeg_path)

            logger.info("Executing yt-dlp command: %s", " ".join(cmd))

            # 3. Run download
            def run_yt_dlp():
                """Helper function to run yt-dlp in a separate thread."""
                # Get the actual filename after download if possible, or just open the folder
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, check=True)
                return proc.stdout

            final_path = await asyncio.to_thread(run_yt_dlp)
            final_path = final_path.strip()

            # 4. Open the specific file directly
            if os.path.exists(final_path):
                os.startfile(final_path)
            else:
                os.startfile(self.base_dir)

            type_str = "Video" if download_type == "video" else "Audio (MP3)"
            msg = (f"✅ {type_str} download ho gaya hai aur play kar diya hai. "
                   f"Path: {final_path}")
            return {
                "status": "success",
                "message": msg,
                "file_path": final_path
            }

        except subprocess.CalledProcessError as e:
            logger.error("yt-dlp error: %s", e.stderr)

            # Check for ffmpeg error in audio download
            if download_type == "audio" and "ffmpeg not found" in e.stderr.lower():
                logger.warning(
                    "ffmpeg missing, trying audio fallback (original format)...")
                try:
                    fallback_cmd = [
                        "yt-dlp",
                        "-f", "bestaudio",
                        "-o", os.path.join(self.base_dir, "%(title)s.%(ext)s"),
                        "--no-playlist",
                        "--js-runtimes", "node",
                        url
                    ]
                    await asyncio.to_thread(
                        lambda: subprocess.run(
                            fallback_cmd, capture_output=True, text=True, check=True)
                    )
                    os.startfile(self.base_dir)
                    msg = ("✅ Audio (Original Format) download kar liya gaya hai kyunki 'ffmpeg' missing tha. "
                           "MP3 conversion possible nahi thi.")
                    return {
                        "status": "success",
                        "message": msg
                    }
                except (subprocess.SubprocessError, OSError, ValueError) as ex:
                    logger.error("Fallback failed: %s", ex)
                    return {
                        "status": "error",
                        "message": f"❌ Fallback bhi fail ho gaya: {str(ex)[:100]}"
                    }

            msg = f"❌ Download fail ho gaya: {e.stderr[:100]}. Browser mein play karne ki koshish kar raha hoon..."
            logger.info(msg)
            await yt_bot.open_url_in_app(url)
            return {
                "status": "success",
                "message": msg + " ✅ Browser mein play kar diya gaya hai."
            }
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.error("Unexpected error in downloader: %s", e)
            msg = f"❌ Unexpected Error: {str(e)}. Browser mein play karne ki koshish kar raha hoon..."
            logger.error(msg)
            if 'url' in locals():
                await yt_bot.open_url_in_app(url)
            return {
                "status": "success",
                "message": f"{msg} ✅ Browser mein play kar diya gaya hai."
            }


# Global Instance
yt_downloader = YouTubeDownloader()


@function_tool
async def download_youtube_media(query: str, download_type: str = "audio") -> dict:
    """
    YouTube se video ya audio download karne ke liye use hota hai. 
    Aap 'audio' (MP3) ya 'video' (MP4) download kar sakte hain.

    Args:
        query: Video ka naam, keyword ya direct YouTube URL.
        download_type: Use 'audio' for MP3/music and 'video' for MP4/video files. Default is 'audio'.
    """
    try:
        return await yt_downloader.download(query, download_type)
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception("YouTube downloader error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error in YouTube downloader: {str(e)}",
            "error": str(e)
        }
