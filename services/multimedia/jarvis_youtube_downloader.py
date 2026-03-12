"""
Jarvis YouTube Downloader Module
Handles downloading videos and audio from YouTube using yt-dlp.
"""
import asyncio
import os
import subprocess
import re
from typing import List, Dict, Union
from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger
from services.automation.jarvis_youtube_automation import yt_bot

# Setup logging
logger = setup_logger("JARVIS-YT-DOWNLOADER")


class YouTubeDownloader:
    """Class to handle YouTube video and audio downloads."""

    def __init__(self):
        # Centralized output directory
        self.base_dir = os.path.join(
            os.getcwd(), "Jarvis_Outputs", "Downloads", "YouTube")
        os.makedirs(self.base_dir, exist_ok=True)
        # 🛠️ Set FFmpeg path if it exists at C:\ffmpeg\bin or in system PATH
        self.ffmpeg_path = r"C:\ffmpeg\bin"
        if not os.path.exists(os.path.join(self.ffmpeg_path, "ffmpeg.exe")):
            # Try to find it in system path
            from shutil import which # pylint: disable=import-outside-toplevel
            system_ffmpeg = which("ffmpeg")
            if system_ffmpeg:
                self.ffmpeg_path = os.path.dirname(system_ffmpeg)
                logger.info("FFmpeg found in system path: %s", self.ffmpeg_path)
            else:
                self.ffmpeg_path = None
                logger.warning("FFmpeg not found at C:\\ffmpeg\\bin or in system PATH.")

    def is_valid_url(self, url: str) -> bool:
        """Simple check for YouTube URL."""
        # Split regex for line length
        yt_p = r'(https?://)?(www\.)?(youtube|youtu|music\.youtube)\.(com|be)/'
        id_p = r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
        return bool(re.match(yt_p + id_p, url))

    async def _prepare_url(self, url_or_query: str) -> Union[str, Dict]:
        """Resolves query to URL if needed."""
        url = url_or_query.strip()
        if not self.is_valid_url(url):
            logger.info("Query detected, searching for video: %s", url)
            found_url = await yt_bot.get_video_url(url)
            if not found_url:
                return {
                    "status": "error",
                    "message": f"❌ Error: '{url}' ke liye koi video nahi mili."
                }
            url = found_url
        return url

    def _get_command(self, url: str, download_type: str) -> List[str]:
        """Constructs the yt-dlp command."""
        output_template = os.path.join(self.base_dir, "%(title)s.%(ext)s")
        common_flags = ["--no-playlist", "--js-runtimes", "node"]

        if download_type == "video":
            cmd = [
                "yt-dlp", "-f", "best[height<=720]/best",
                "--print", "after_move:filepath", "-o", output_template,
                *common_flags, url
            ]
        else:
            cmd = [
                "yt-dlp", "-x", "--audio-format", "mp3",
                "--print", "after_move:filepath", "-o", output_template,
                *common_flags, url
            ]

        if self.ffmpeg_path:
            cmd.insert(1, "--ffmpeg-location")
            cmd.insert(2, self.ffmpeg_path)
        return cmd

    async def _execute_download(self, cmd: List[str]) -> str:
        """Runs yt-dlp in a thread and returns output path."""
        def run_proc():
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=True)
            return proc.stdout.strip()
        return await asyncio.to_thread(run_proc)

    async def download(self, url_or_query: str, download_type: str = "audio") -> Dict:
        """
        Downloads media from YouTube.
        """
        try:
            url_res = await self._prepare_url(url_or_query)
            if isinstance(url_res, dict):
                return url_res
            url = url_res

            cmd = self._get_command(url, download_type)
            logger.info("Executing yt-dlp: %s", " ".join(cmd))

            final_path = await self._execute_download(cmd)

            if os.path.exists(final_path):
                os.startfile(final_path)  # nosec B606
            else:
                os.startfile(self.base_dir)  # nosec B606

            type_str = "Video" if download_type == "video" else "Audio (MP3)"
            return {
                "status": "success",
                "message": f"✅ {type_str} download ho gaya hai aur play kar diya hai.",
                "file_path": final_path
            }

        except subprocess.CalledProcessError as e:
            return await self._handle_download_error(e, url, download_type)
        except (ValueError, OSError, RuntimeError) as e:
            logger.error("Unexpected error in downloader: %s", e)
            return await self._fallback_to_browser(url, f"❌ Unexpected Error: {str(e)}")

    async def _handle_download_error(self, err: subprocess.CalledProcessError,
                                     url: str, download_type: str) -> Dict:
        """Handles yt-dlp specific errors."""
        logger.error("yt-dlp error: %s", err.stderr)
        if download_type == "audio" and "ffmpeg not found" in err.stderr.lower():
            return await self._try_audio_fallback(url)

        return await self._fallback_to_browser(
            url, f"❌ Download fail ho gaya: {err.stderr[:50]}")

    async def _try_audio_fallback(self, url: str) -> Dict:
        """Fallback for missing ffmpeg."""
        try:
            cmd = [
                "yt-dlp", "-f", "bestaudio",
                "-o", os.path.join(self.base_dir, "%(title)s.%(ext)s"),
                "--no-playlist", "--js-runtimes", "node", url
            ]
            await asyncio.to_thread(lambda: subprocess.run(cmd, check=True))
            os.startfile(self.base_dir)  # nosec B606
            return {
                "status": "success",
                "message": "✅ Audio (Original) download ho gaya (ffmpeg missing tha)."
            }
        except (subprocess.SubprocessError, OSError):
            return await self._fallback_to_browser(url, "❌ Fallback fail ho gaya.")

    async def _fallback_to_browser(self, url: str, reason: str) -> Dict:
        """Opens YouTube URL in browser as final fallback."""
        logger.info("%s. Opening browser fallback...", reason)
        await yt_bot.open_url_in_app(url)
        return {
            "status": "success",
            "message": f"{reason} ✅ Browser mein play kar diya gaya hai."
        }


# Global Instance
yt_downloader = YouTubeDownloader()


@jarvis_tool
async def download_youtube_media(query: str, download_type: str = "audio") -> dict:
    """
    YouTube se video ya audio download karne ke liye use hota hai.
    Aap 'audio' (MP3) ya 'video' (MP4) download kar sakte hain.
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
