"""
Jarvis YouTube Downloader Module
Handles downloading videos and audio from YouTube using yt-dlp.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from shutil import which

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.automation.jarvis_youtube_automation import yt_bot
from services.utils import jarvis_delivery
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-YT-DOWNLOADER")

_PROGRESS_RE = re.compile(
    r"JARVIS_PROGRESS\s+(?P<pct>[\d.]+%?)\s*(?P<eta>.*)?", re.IGNORECASE
)
_JOBS_MODULE = "services.utils.jarvis_jobs"
PROGRESS_TEMPLATE = (
    "download:JARVIS_PROGRESS %(progress._percent_str)s %(progress._eta_str)s"
)


def _ffmpeg_dir_from(path: str | None) -> str | None:
    if not path:
        return None
    path = path.strip().strip('"')
    if os.path.isfile(path) and path.lower().endswith(("ffmpeg.exe", "ffmpeg")):
        return os.path.dirname(path)
    if os.path.isfile(os.path.join(path, "ffmpeg.exe")):
        return path
    if os.path.isfile(os.path.join(path, "ffmpeg")):
        return path
    return None


def _discover_ffmpeg() -> str | None:
    for candidate in (
        getattr(config, "ffmpeg_dir", None),
        os.environ.get("FFMPEG_DIR"),
        r"C:\ffmpeg\bin",
    ):
        found = _ffmpeg_dir_from(candidate)
        if found:
            return found
    system_ffmpeg = which("ffmpeg")
    if system_ffmpeg:
        return os.path.dirname(system_ffmpeg)
    try:
        import imageio_ffmpeg  # pylint: disable=import-outside-toplevel

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return os.path.dirname(exe)
    except ImportError:
        pass
    return None


def _ytdlp_js_flags() -> list[str]:
    flags: list[str] = []
    try:
        import yt_dlp_ejs  # noqa: F401  # pylint: disable=import-outside-toplevel, unused-import
    except ImportError:
        flags.extend(["--remote-components", "ejs:github"])
    if which("node"):
        flags.extend(["--js-runtimes", "node"])
    return flags


def _parse_progress_line(line: str) -> tuple[float | None, str]:
    match = _PROGRESS_RE.search(line)
    if not match:
        return None, ""
    pct_raw = (match.group("pct") or "").strip().rstrip("%")
    try:
        pct = float(pct_raw)
    except ValueError:
        pct = None
    eta = (match.group("eta") or "").strip()
    return pct, eta


def _start_job_available() -> bool:
    try:
        from services.utils import jarvis_jobs  # pylint: disable=import-outside-toplevel

        return callable(getattr(jarvis_jobs, "start_job", None))
    except ImportError:
        return False


class YouTubeDownloader:
    """Class to handle YouTube video and audio downloads."""

    def __init__(self):
        self.base_dir = os.path.join(config.shared_dir, "Downloads", "YouTube")
        os.makedirs(self.base_dir, exist_ok=True)
        self.ffmpeg_path = _discover_ffmpeg()
        if self.ffmpeg_path:
            logger.info("FFmpeg directory: %s", self.ffmpeg_path)
        else:
            logger.warning(
                "FFmpeg not found (config.ffmpeg_dir, FFMPEG_DIR, C:\\ffmpeg\\bin, PATH, imageio_ffmpeg)."
            )

    def is_valid_url(self, url: str) -> bool:
        """Simple check for YouTube URL."""
        yt_p = r"(https?://)?(www\.)?(youtube|youtu|music\.youtube)\.(com|be)/"
        id_p = r"(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})"
        return bool(re.match(yt_p + id_p, url))

    def resolve_target(self, url_or_query: str) -> str:
        """URLs pass through; plain text becomes a single-result yt-dlp search."""
        text = url_or_query.strip()
        if self.is_valid_url(text):
            return text
        return f"ytsearch1:{text}"

    def _get_command(self, target: str, download_type: str) -> list[str]:
        """Constructs the yt-dlp argv (python -m yt_dlp)."""
        output_template = os.path.join(self.base_dir, "%(title)s.%(ext)s")
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--newline",
            "--progress-template",
            PROGRESS_TEMPLATE,
            "--print",
            "after_move:filepath",
            "-o",
            output_template,
            *_ytdlp_js_flags(),
        ]

        if download_type == "video":
            cmd.extend(["-f", "best[height<=720]/best"])
        else:
            cmd.extend(["-x", "--audio-format", "mp3"])

        if self.ffmpeg_path:
            cmd.extend(["--ffmpeg-location", self.ffmpeg_path])
        cmd.append(target)
        return cmd

    async def _fallback_target_url(self, url_or_query: str) -> str:
        text = url_or_query.strip()
        if self.is_valid_url(text):
            return text
        found = await yt_bot.get_video_url(text)
        return found or text

    async def _run_ytdlp(
        self,
        cmd: list[str],
        *,
        ctx: object | None = None,
        auto_open: bool = True,
        url_or_query: str = "",
        download_type: str = "audio",
    ) -> dict:
        """Runs yt-dlp as a subprocess; optional JobContext for progress and cancel."""
        logger.info("Executing yt-dlp: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_lines: list[str] = []
        stderr_chunks: list[str] = []

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                if ctx is not None and getattr(ctx, "cancelled", False):
                    if proc.returncode is None:
                        proc.kill()
                    break
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip("\r\n")
                stderr_chunks.append(text)
                if ctx is not None and "JARVIS_PROGRESS" in text:
                    pct, eta = _parse_progress_line(text)
                    msg = f"ETA {eta}" if eta else "Download ho raha hai..."
                    await ctx.progress(pct, msg)

        async def read_stdout() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                stdout_lines.append(line.decode(errors="replace").rstrip("\r\n"))

        stderr_task = asyncio.create_task(read_stderr())
        stdout_task = asyncio.create_task(read_stdout())
        try:
            await asyncio.gather(stderr_task, stdout_task)
            returncode = await proc.wait()
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            if not stdout_task.done():
                stdout_task.cancel()

        stderr_text = "\n".join(stderr_chunks)
        if returncode != 0:
            err = subprocess.CalledProcessError(returncode, cmd, stderr=stderr_text)
            return await self._handle_download_error(
                err, url_or_query, download_type, auto_open=auto_open
            )

        final_path = ""
        for line in reversed(stdout_lines):
            candidate = line.strip()
            if candidate and os.path.exists(candidate):
                final_path = candidate
                break

        if not final_path:
            return await self._fallback_to_browser(
                await self._fallback_target_url(url_or_query),
                "❌ Download complete lekin file path nahi mila.",
            )

        if auto_open:
            open_target = final_path if os.path.exists(final_path) else self.base_dir
            await asyncio.to_thread(jarvis_delivery.open_artifact, open_target)

        type_str = "Video" if download_type == "video" else "Audio (MP3)"
        name = os.path.basename(final_path)
        return {
            "status": "success",
            "message": f"✅ {type_str} download ho gaya hai aur play kar diya hai.",
            "file_path": final_path,
            "artifact": {"kind": "file", "path": final_path, "name": name},
        }

    async def run_download_job(
        self,
        ctx: object,
        url_or_query: str,
        download_type: str = "audio",
        *,
        auto_open: bool = True,
    ) -> dict:
        target = self.resolve_target(url_or_query)
        cmd = self._get_command(target, download_type)
        return await self._run_ytdlp(
            cmd,
            ctx=ctx,
            auto_open=auto_open,
            url_or_query=url_or_query,
            download_type=download_type,
        )

    async def download(
        self,
        url_or_query: str,
        download_type: str = "audio",
        *,
        auto_open: bool = True,
    ) -> dict:
        """Synchronous path (no background job queue)."""
        url = url_or_query.strip()
        try:
            target = self.resolve_target(url_or_query)
            cmd = self._get_command(target, download_type)
            return await self._run_ytdlp(
                cmd,
                ctx=None,
                auto_open=auto_open,
                url_or_query=url_or_query,
                download_type=download_type,
            )
        except subprocess.CalledProcessError as e:
            return await self._handle_download_error(
                e, url, download_type, auto_open=auto_open
            )
        except (ValueError, OSError, RuntimeError) as e:
            logger.error("Unexpected error in downloader: %s", e)
            return await self._fallback_to_browser(
                await self._fallback_target_url(url),
                f"❌ Unexpected Error: {e!s}",
            )

    async def _handle_download_error(
        self,
        err: subprocess.CalledProcessError,
        url_or_query: str,
        download_type: str,
        *,
        auto_open: bool = True,
    ) -> dict:
        """Handles yt-dlp specific errors."""
        stderr = err.stderr or ""
        logger.error("yt-dlp error: %s", stderr)
        fallback_url = await self._fallback_target_url(url_or_query)
        if download_type == "audio" and "ffmpeg not found" in stderr.lower():
            return await self._try_audio_fallback(fallback_url, auto_open=auto_open)

        return await self._fallback_to_browser(
            fallback_url, f"❌ Download fail ho gaya: {stderr[:50]}"
        )

    async def _try_audio_fallback(self, url: str, *, auto_open: bool = True) -> dict:
        """Fallback for missing ffmpeg."""
        try:
            cmd = [
                sys.executable,
                "-m",
                "yt_dlp",
                "-f",
                "bestaudio",
                "-o",
                os.path.join(self.base_dir, "%(title)s.%(ext)s"),
                "--no-playlist",
                "--newline",
                "--progress-template",
                PROGRESS_TEMPLATE,
                *_ytdlp_js_flags(),
                url,
            ]
            result = await self._run_ytdlp(
                cmd, ctx=None, auto_open=False, url_or_query=url, download_type="audio"
            )
            if result.get("status") != "success":
                return result
            if auto_open:
                await asyncio.to_thread(jarvis_delivery.open_artifact, self.base_dir)
            result["message"] = "✅ Audio (Original) download ho gaya (ffmpeg missing tha)."
            return result
        except (subprocess.SubprocessError, OSError):
            return await self._fallback_to_browser(url, "❌ Fallback fail ho gaya.")

    async def _fallback_to_browser(self, url: str, reason: str) -> dict:
        """Opens YouTube URL in browser as final fallback."""
        logger.info("%s. Opening browser fallback...", reason)
        opened = await yt_bot.open_url_in_app(url)
        if not opened:
            return {
                "status": "error",
                "message": f"{reason} Browser fallback bhi fail ho gaya.",
                "error": "browser_fallback_failed",
            }
        return {
            "status": "success",
            "message": f"{reason} ✅ Browser mein play kar diya gaya hai.",
        }


yt_downloader = YouTubeDownloader()


@jarvis_tool
async def download_youtube_media(
    query: str,
    download_type: str = "audio",
    auto_open: bool = True,
) -> dict:
    """
    YouTube se video ya audio download karne ke liye use hota hai.
    Aap 'audio' (MP3) ya 'video' (MP4) download kar sakte hain.
    """
    url = query.strip()
    try:
        if _start_job_available():
            from services.utils import jarvis_jobs  # pylint: disable=import-outside-toplevel

            kind = "video" if download_type == "video" else "audio"
            title = f"YouTube {kind} download"

            async def factory(ctx: jarvis_jobs.JobContext) -> dict:
                return await yt_downloader.run_download_job(
                    ctx, query, download_type, auto_open=auto_open
                )

            job_id = jarvis_jobs.start_job(
                "download_youtube_media",
                title,
                factory,
                timeout=1800,
                announce=True,
            )
            return {
                "status": "started",
                "job_id": job_id,
                "message": "Download shuru ho gaya, progress dashboard par hai.",
            }

        return await yt_downloader.download(query, download_type, auto_open=auto_open)
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception("YouTube downloader error: %s", e)
        return await yt_downloader._fallback_to_browser(  # pylint: disable=protected-access
            await yt_downloader._fallback_target_url(url),  # pylint: disable=protected-access
            f"❌ Error in YouTube downloader: {e!s}",
        )
