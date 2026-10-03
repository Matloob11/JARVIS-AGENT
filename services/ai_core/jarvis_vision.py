"""
# jarvis_vision.py
Jarvis Vision Module

Handles screen perception by capturing screenshots and camera frames and analyzing them through the
shared K-30 helper (services.ai_core.llm_text): Gemini 3.x first, then Groq, then OpenRouter (C-48).
Camera frames from the UI carry a timestamp; a frame older than JARVIS_FRAME_MAX_AGE_S (10 s) is never
analyzed (the local webcam is used instead, else the camera counts as off).
"""

import asyncio
import base64
import binascii
import contextvars
import importlib
import math
import os
import time
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Any

import openai
import requests  # noqa: F401  # pylint: disable=unused-import  # older callers and tests patch jarvis_vision.requests
from dotenv import load_dotenv
from google import genai
from openai import OpenAI  # Used for Groq's OpenAI-compatible API
from PIL import Image

from services.ai_core import llm_text
from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.ai_core.llm_text import LLMResult, LLMUnavailableError
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-VISION")

load_dotenv()

# Global clients (lazy initialized where needed)
_google_client = None

# Capture backends, imported on first use: importing this module has no GUI side effect, so a headless
# host (the VPS brain) can load it (K-72). pyautogui serves screenshots, cv2 the local webcam fallback.
pyautogui: Any = None
cv2: Any = None

DEFAULT_VISION_MODEL = llm_text.DEFAULT_VISION_MODEL  # gemini-3.8-flash (K-30 / K-35)
DEFAULT_FRAME_MAX_AGE_S = 10.0
MAX_IMAGE_WIDTH = 1600
VISION_JPEG_QUALITY = 80
# Per provider attempt. The tools get 60 s in total (C-48 step 7), so three tiers that each hang
# (3 x 18 s = 54 s) still end in a fallback answer or "Vision providers failed", not a tool timeout.
VISION_TIMEOUT_S = 18.0
CAMERA_OFF_MESSAGE = "Camera frame purana hai ya camera off hai"
PROVIDERS_FAILED_PREFIX = "Vision providers failed: "
# Error strings of the public analyze_via_groq / analyze_via_openrouter wrappers (and older stand-ins)
# that mean "this provider failed". A real provider answer is never judged by these prefixes, so a
# description such as "Error message box saying ..." is kept (see _is_failed_answer).
_FAILED_ANSWER_PREFIXES = ("Error", "OpenRouter Error", "Fallback failed")
_REASON_MAX_CHARS = 200
_JPEG_MAGIC = b"\xff\xd8\xff"
_MS_EPOCH_THRESHOLD = 1e11  # larger timestamps are epoch milliseconds (the UI sends Date.now())

# The LLMResult of the last provider call in this task, so analyze_image can log provider and model
# even when it reaches the provider through the public analyze_via_* methods.
_last_vision_result: contextvars.ContextVar[LLMResult | None] = contextvars.ContextVar(
    "jarvis_last_vision_result", default=None)
# (text, failed) of the last analysis string handed back in this task by analyze_content or
# VisionHandler.analyze_current_frame, so a caller can tell a failure string from an answer that merely
# starts with 'Error' (a description of an error dialog, for example). See analysis_failed().
_last_analysis: contextvars.ContextVar[tuple[str, bool] | None] = contextvars.ContextVar(
    "jarvis_last_vision_analysis", default=None)


def record_analysis(text: str, failed: bool) -> str:
    """Notes that text, about to be returned as a vision analysis, is a failure (or an answer); returns text."""
    _last_analysis.set((text, failed))
    return text


def analysis_failed(text: str) -> bool:
    """
    True when text, a string a vision analysis returned in this task, reports a failure. A string the
    analysis recorded (record_analysis) is judged by that record, so a real answer starting with 'Error'
    stays an answer; any other string (an older or patched caller) fails when it starts with 'Error'.
    """
    recorded = _last_analysis.get()
    if recorded is not None and recorded[0] == text:
        return recorded[1]
    return text.startswith("Error")


def frame_max_age_s() -> float:
    """JARVIS_FRAME_MAX_AGE_S (K-35, default 10): the oldest camera frame that may still be analyzed."""
    raw = (os.environ.get("JARVIS_FRAME_MAX_AGE_S") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_FRAME_MAX_AGE_S
    except ValueError:
        return DEFAULT_FRAME_MAX_AGE_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_FRAME_MAX_AGE_S


def frame_timestamp(ts: Any, received_at: float | None = None) -> float:
    """
    Epoch seconds of a frame. ts may be epoch ms (the UI's Date.now()), epoch seconds or a numeric
    string; anything else (None, a bool, garbage) means 'now'. A timestamp later than the moment the
    frame arrived is clamped to that moment, so a sender clock that runs ahead cannot keep a frame fresh.
    """
    now = time.time() if received_at is None else received_at
    if ts is None or isinstance(ts, bool):
        return now
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return now
    if not math.isfinite(value) or value <= 0:
        return now
    if value > _MS_EPOCH_THRESHOLD:
        value /= 1000.0
    return min(value, now)


def is_fresh(ts: float | None, max_age: float | None = None, now: float | None = None) -> bool:
    """True when a frame stamped ts (epoch seconds) is at most max_age (default JARVIS_FRAME_MAX_AGE_S) old."""
    if ts is None:
        return False
    limit = frame_max_age_s() if max_age is None else max_age
    current = time.time() if now is None else now
    return current - ts <= limit


def prepare_image(image: Image.Image | bytes, max_width: int = MAX_IMAGE_WIDTH,
                  quality: int = VISION_JPEG_QUALITY) -> bytes:
    """
    JPEG bytes for a vision request: an image wider than max_width is scaled down (aspect kept) and
    anything that is not already JPEG is re-encoded as RGB JPEG at quality; a JPEG within the width
    passes unchanged. Raises ValueError, TypeError or PIL's OSError for unusable input.
    """
    if isinstance(image, (bytes, bytearray, memoryview)):
        data = bytes(image)
        if not data:
            raise ValueError("image is empty")
        with Image.open(BytesIO(data)) as opened:
            if data.startswith(_JPEG_MAGIC) and opened.width <= max_width:
                return data
            return _encode_vision_jpeg(opened, max_width, quality)
    if isinstance(image, Image.Image):
        return _encode_vision_jpeg(image, max_width, quality)
    raise TypeError(f"image must be a PIL image or image bytes, not {type(image).__name__}")


def _encode_vision_jpeg(image: Image.Image, max_width: int, quality: int) -> bytes:
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    if rgb.width > max_width:
        height = max(1, round(rgb.height * max_width / rgb.width))
        rgb = rgb.resize((max_width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    rgb.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def providers_failed_message(error: LLMUnavailableError) -> str:
    """'Vision providers failed: <reasons>' (llm_text has already masked every reason)."""
    return PROVIDERS_FAILED_PREFIX + ("; ".join(error.reasons) or "no vision provider available")


class CaptureUnavailable(ImportError):
    """A capture backend cannot be imported on this host; the tools answer with status 'unavailable'."""


def _capture_backend(name: str) -> Any:
    """Imports pyautogui or cv2 once. Any import failure (ImportError, or KeyError 'DISPLAY' and
    NotImplementedError on a headless Linux host) raises CaptureUnavailable."""
    module = globals()[name]
    if module is None:
        try:
            module = importlib.import_module(name)
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise CaptureUnavailable(f"{name} is not available on this host ({type(e).__name__}: {e})") from e
        globals()[name] = module
    return module

def get_google_client() -> genai.Client | None:
    """Lazily initializes and returns the Google GenAI client (kept for callers; vision uses llm_text)."""
    global _google_client # pylint: disable=global-statement
    if _google_client is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("GOOGLE_API_KEY missing in environment.")
            return None
        _google_client = genai.Client(api_key=api_key)
    return _google_client

def get_groq_client() -> OpenAI | None:
    """Lazily initializes and returns the Groq OpenAI client (kept for callers; vision uses llm_text)."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )


class ScreenPerceiver:
    """
    Handles capturing and analyzing screen content.
    """

    def __init__(self, model_name: str | None = None):
        # C-48: the 404ing gemini-2.0-flash is gone; JARVIS_VISION_MODEL (K-35) or gemini-3.8-flash.
        self.model_name = (model_name or (os.getenv("JARVIS_VISION_MODEL") or "").strip()
                           or DEFAULT_VISION_MODEL)
        self._latest_frame: bytes | None = None  # JPEG bytes from the UI
        self.latest_frame_ts: float | None = None  # epoch seconds of that frame

    @property
    def latest_frame(self) -> bytes | None:
        """The last UI camera frame (JPEG bytes), whatever its age; capture_webcam checks the age."""
        return self._latest_frame

    @latest_frame.setter
    def latest_frame(self, frame: bytes | None) -> None:
        # A frame set directly (not through update_webcam_frame) counts as received now.
        self._latest_frame = frame
        self.latest_frame_ts = time.time() if frame else None

    def update_webcam_frame(self, frame_data: Any, ts: Any = None) -> None:
        """
        Updates the internal buffer with a new frame from the UI (a data URL, base64 or raw bytes).
        ts is the UI's capture time (epoch ms, K-05 'vision_frame' {frame, ts}); without one the frame
        is stamped with the time it arrived.
        """
        try:
            if not frame_data:
                return

            frame: bytes | None = None
            # If it's a data URL or base64 string
            if isinstance(frame_data, str):
                if "," in frame_data:
                    b64_str = frame_data.split(",")[1]
                else:
                    b64_str = frame_data
                frame = base64.b64decode(b64_str)
            elif isinstance(frame_data, (bytes, bytearray)):
                frame = bytes(frame_data)
            if not frame:
                return

            self._latest_frame = frame
            self.latest_frame_ts = frame_timestamp(ts)
            logger.debug("Vision system updated with new UI frame.")
        except (ValueError, TypeError, binascii.Error) as e:
            logger.error("Error decoding vision frame: %s", e)

    def fresh_ui_frame(self, max_age: float | None = None) -> bytes | None:
        """The UI frame while it is at most max_age (default JARVIS_FRAME_MAX_AGE_S) old, else None."""
        if self._latest_frame and is_fresh(self.latest_frame_ts, max_age):
            return self._latest_frame
        return None

    async def capture_screen(self) -> bytes:
        """
        Captures the current primary screen and returns it as PNG bytes.
        Raises CaptureUnavailable when pyautogui cannot be imported here.
        """
        gui = _capture_backend("pyautogui")
        try:
            # Capture using pyautogui in a thread to avoid blocking
            screenshot = await asyncio.to_thread(gui.screenshot)

            # Save to BytesIO to avoid disk I/O if possible,
            # but we need it in a format Gemini accepts (PIL or bytes)
            buffered = BytesIO()
            screenshot.save(buffered, format="PNG")
            return buffered.getvalue()
        except (gui.ImageNotFoundException, OSError) as e:
            logger.error("Error capturing screen: %s", e)
            raise

    async def capture_webcam(self) -> bytes:
        """
        Captures a frame from the UI-provided buffer or local webcam. Returns JPEG bytes.

        A UI frame is used only while it is at most JARVIS_FRAME_MAX_AGE_S old (C-48); otherwise the
        local cv2 camera is read. With a stale UI frame and no usable local camera it raises
        OSError('Camera frame purana hai ya camera off hai'), so an old frame is never described.
        Without any UI frame it raises CaptureUnavailable when OpenCV cannot be imported here.
        """
        # Priority 1: Use a fresh frame captured from the UI Bridge
        fresh = self.fresh_ui_frame()
        if fresh is not None:
            return fresh
        had_ui_frame = self._latest_frame is not None
        if had_ui_frame:
            logger.info("UI camera frame is older than %.0f s; trying the local camera.", frame_max_age_s())

        # Priority 2: Fallback to local webcam (OpenCV)
        try:
            opencv = _capture_backend("cv2")
        except CaptureUnavailable as e:
            if had_ui_frame:
                raise OSError(CAMERA_OFF_MESSAGE) from e
            raise

        # Run in thread to avoid blocking loop
        def do_capture() -> bytes | None:
            cap = opencv.VideoCapture(0)
            if not cap.isOpened():
                return None
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return None
            # Convert BGR to RGB
            rgb_frame = opencv.cvtColor(frame, opencv.COLOR_BGR2RGB)
            img = Image.fromarray(rgb_frame)
            buffered = BytesIO()
            img.save(buffered, format="JPEG")
            return buffered.getvalue()

        try:
            data = await asyncio.to_thread(do_capture)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("Error capturing webcam: %s", e)
            if had_ui_frame:
                raise OSError(CAMERA_OFF_MESSAGE) from e
            raise OSError(f"Camera error: {e!s}") from e
        if data is None:
            if had_ui_frame:
                raise OSError(CAMERA_OFF_MESSAGE)
            raise OSError(
                "Camera error: Sir, camera access nahi mil raha. "
                "Kripya check karein ke browser mein camera allowed hai ya nahi.",
            )
        return data

    async def _vision_tier(self, provider: str, prompt: str, image: Image.Image | bytes) -> LLMResult:
        """One K-30 tier (llm_text.generate_vision limited to provider); records the result for logging."""
        result = await llm_text.generate_vision(
            prompt, image, model=self.model_name if provider == "gemini" else None,
            timeout=VISION_TIMEOUT_S, providers=(provider,))
        _last_vision_result.set(result)
        return result

    async def analyze_via_google(self, prompt: str, image: Image.Image | bytes) -> str:
        """Gemini tier of llm_text (self.model_name first, then the Gemini fallbacks); raises on failure."""
        logger.info("Sending image to Gemini (%s)", self.model_name)
        try:
            return (await self._vision_tier("gemini", prompt, image)).text
        except Exception as e:
            logger.error("Google Vision API error: %s", e)
            raise

    async def analyze_via_openrouter(self, prompt: str, image: Image.Image | bytes) -> str:
        """OpenRouter tier of llm_text (JARVIS_OPENROUTER_MODEL; images never go to a ':free' model).
        Returns the answer, or a string starting with 'Error' when the tier fails."""
        logger.info("Attempting vision via OpenRouter...")
        if not (os.getenv("OPENROUTER_API_KEY") or "").strip():
            return "Error: OpenRouter API key missing for fallback."
        try:
            return (await self._vision_tier("openrouter", prompt, image)).text
        except (LLMUnavailableError, openai.APIError, OSError, RuntimeError, ValueError, TypeError) as e:
            logger.warning("OpenRouter vision failed: %s", e)
            return f"Error: OpenRouter failed: {e!s}"

    async def analyze_via_groq(self, prompt: str, image: Image.Image | bytes) -> str:
        """Groq tier of llm_text (JARVIS_GROQ_VISION_MODEL, default qwen/qwen3.8-27b).
        Returns the answer, or a string starting with 'Error' when the tier fails."""
        if not (os.getenv("GROQ_API_KEY") or "").strip():
            return "Error: Groq client not initialized."

        logger.info("Attempting vision via Groq...")
        try:
            return (await self._vision_tier("groq", prompt, image)).text
        # openai.APIError (NotFoundError for a retired model) used to escape here, so OpenRouter was never tried.
        except (LLMUnavailableError, openai.APIError, OSError, RuntimeError, ValueError, TypeError) as e:
            logger.error("Groq Vision API error: %s", e)
            return f"Error: Groq failed: {e!s}"

    async def analyze_image(self, prompt: str, image: Image.Image | bytes) -> str:
        """
        Answers prompt about image with the first K-30 tier that works: Gemini, then Groq, then
        OpenRouter (each through llm_text.generate_vision). The image is first downscaled to a JPEG at
        most 1600 px wide. Logs 'provider=<name> model=<id>' for the answer. Raises LLMUnavailableError,
        with one masked reason per failed attempt, when every tier fails.
        """
        jpeg = await asyncio.to_thread(prepare_image, image)
        tiers: tuple[tuple[str, Callable[[str, Any], Awaitable[str]]], ...] = (
            ("gemini", self.analyze_via_google),
            ("groq", self.analyze_via_groq),
            ("openrouter", self.analyze_via_openrouter),
        )
        reasons: list[str] = []
        started = time.monotonic()
        for provider, call in tiers:
            _last_vision_result.set(None)
            try:
                text = await call(prompt, jpeg)
            except LLMUnavailableError as e:
                reasons.extend(e.reasons or [f"{provider}: failed"])
                continue
            except Exception as e:  # pylint: disable=broad-exception-caught
                detail = " ".join(str(e).split())
                reasons.append(f"{provider}: {type(e).__name__}: {detail}"[:_REASON_MAX_CHARS])
                continue
            if not isinstance(text, str) or not text.strip():
                reasons.append(f"{provider}: empty response")
                continue
            result = _last_vision_result.get()
            if _is_failed_answer(provider, text, result):
                reasons.append(f"{provider}: {text}"[:_REASON_MAX_CHARS])
                continue
            model = result.model if result is not None and result.provider == provider else "unknown"
            logger.info("Vision answered: provider=%s model=%s in %.1fs", provider, model,
                        time.monotonic() - started)
            return text
        logger.warning("Vision: every provider failed (%d reasons)", len(reasons))
        raise LLMUnavailableError(reasons)

    async def analyze_content(self, prompt: str = "What is on my screen?") -> str:
        """
        Captures the screen, downscales it (at most 1600 px wide, JPEG quality 80) and analyzes it with
        analyze_image. Failures come back as a string starting with 'Error'; CaptureUnavailable passes.
        """
        try:
            logger.info("Capturing screen for analysis...")
            image_bytes = await self.capture_screen()
            jpeg = await asyncio.to_thread(prepare_image, image_bytes)
            return record_analysis(await self.analyze_image(prompt, jpeg), failed=False)
        except LLMUnavailableError as e:
            logger.error("Vision providers failed for the screen: %s", e)
            return record_analysis(f"Error: {providers_failed_message(e)}", failed=True)
        except (OSError, ValueError, TypeError) as e:
            logger.error("Error in vision system: %s", e)
            return record_analysis(f"Error: Vision analysis failed: {e!s}", failed=True)


def _is_failed_answer(provider: str, text: str, result: LLMResult | None) -> bool:
    """
    True when text is a tier wrapper's failure string rather than a provider answer. The Gemini tier
    raises when it fails, so its text is always an answer. A Groq or OpenRouter text that is exactly
    the answer llm_text returned for that provider is an answer too, even when it starts with 'Error'
    (for example a description of an error dialog); otherwise the 'Error...' strings of
    analyze_via_groq / analyze_via_openrouter mean the tier failed.
    """
    if provider == "gemini":
        return False
    if result is not None and result.provider == provider and result.text == text:
        return False
    return text.startswith(_FAILED_ANSWER_PREFIXES)


# Global Instance
vision_system = ScreenPerceiver()


def _providers_failed(result: str) -> str | None:
    """'Vision providers failed: ...' when result is analyze_content's provider failure, else None."""
    detail = result.removeprefix("Error: ")
    return detail if detail.startswith(PROVIDERS_FAILED_PREFIX) else None


@jarvis_tool(execution_timeout=60)
async def analyze_screen(query: str = "Describe what you see on my screen in detail.") -> dict:
    """
    Captures a screenshot of your primary monitor and uses AI to describe or analyze it.
    Use this to ask questions about currently open windows, visible text, or graphical content.
    """
    try:
        result = await vision_system.analyze_content(query)
        failed = analysis_failed(result)
        providers_failed = _providers_failed(result) if failed else None
        if providers_failed:
            return {"status": "error", "message": providers_failed}
        if failed:
            return {
                "status": "error",
                "message": f"👁️ Vision analysis failed: {result}",
            }
        return {
            "status": "success",
            "query": query,
            "message": f"👁️ Screen Analysis report taiyyar hai, Sir:\n{result}",
        }
    except CaptureUnavailable as e:
        logger.warning("Screen capture unavailable: %s", e)
        return {
            "status": "unavailable",
            "message": "👁️ Sir, is machine par screen capture available nahi hai.",
            "error": str(e),
        }
    except (OSError, RuntimeError) as e:
        logger.error("Vision tool error: %s", e)
        return {
            "status": "error",
            "message": f"👁️ Vision analysis failed: {e!s}",
            "error": str(e),
        }


@jarvis_tool(execution_timeout=60)
async def analyze_camera(query: str = "What do you see in the camera?") -> dict:
    """
    Captures a frame from your local webcam and uses AI to describe it.
    Use this to ask questions about your surroundings or objects held in front of the camera.
    """
    try:
        logger.info("Capturing webcam for analysis...")
        image_bytes = await vision_system.capture_webcam()
        result = await vision_system.analyze_image(query, image_bytes)
        return {
            "status": "success",
            "query": query,
            "message": f"📷 Camera view analysis, Sir:\n{result}",
        }
    except LLMUnavailableError as e:
        logger.error("Vision providers failed for the camera: %s", e)
        return {"status": "error", "message": providers_failed_message(e), "error": str(e)}
    except CaptureUnavailable as e:
        logger.warning("Webcam capture unavailable: %s", e)
        return {
            "status": "unavailable",
            "message": "📷 Sir, browser se camera frame nahi mila aur is machine par webcam capture available nahi hai.",
            "error": str(e),
        }
    except (OSError, RuntimeError, ValueError, TypeError) as e:
        logger.error("Camera tool error: %s", e)
        return {
            "status": "error",
            "message": (
                f"📷 Camera analysis failed, Sir. Report: {e!s}. "
                "Shayad camera permission ya technical error ki wajah se."
            ),
            "error": str(e),
        }
