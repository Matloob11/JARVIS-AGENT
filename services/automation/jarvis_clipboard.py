"""
# jarvis_clipboard.py
Proactive Clipboard Monitor Module
Detects errors and technical terms in the clipboard and suggests solutions.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from typing import Any, Callable

import pyperclip

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from services.system.jarvis_desktop_common import redact
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-CLIPBOARD")

_MAX_CLIPBOARD_CHARS = 20_000
_RATE_LIMIT_SEC = 60.0
_DEDUPE_SEC = 600.0
_DDGS_TIMEOUT_SEC = 8.0
_EXCERPT_MAX = 200
_SOLUTION_MAX = 500

_last_self_write_hash: str | None = None
_callback_error_logged: set[str] = set()


def note_self_write(text: str) -> None:
    """Mark clipboard text JARVIS wrote so the monitor ignores the next matching paste."""
    global _last_self_write_hash
    _last_self_write_hash = _sha256_text(text)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _is_self_write(text: str) -> bool:
    if _last_self_write_hash is None:
        return False
    return _sha256_text(text) == _last_self_write_hash


_CLIPBOARD_RETRY_ATTEMPTS = 4
_CLIPBOARD_RETRY_BASE_SEC = 0.05


def _is_clipboard_contention(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "openclipboard" in msg or ("clipboard" in msg and "access" in msg):
        return True
    winerror = getattr(exc, "winerror", None)
    return winerror in (0, 5)


def _read_clipboard_sync() -> str:
    last_exc: BaseException | None = None
    for attempt in range(_CLIPBOARD_RETRY_ATTEMPTS):
        try:
            return pyperclip.paste()
        except (OSError, RuntimeError) as exc:
            last_exc = exc
            if not _is_clipboard_contention(exc):
                raise
            delay = _CLIPBOARD_RETRY_BASE_SEC * (attempt + 1)
            if attempt + 1 >= _CLIPBOARD_RETRY_ATTEMPTS:
                logger.warning(
                    "Clipboard read failed after %s attempts: %s",
                    _CLIPBOARD_RETRY_ATTEMPTS,
                    exc,
                )
            else:
                logger.debug(
                    "Clipboard contention (attempt %s/%s): %s",
                    attempt + 1,
                    _CLIPBOARD_RETRY_ATTEMPTS,
                    exc,
                )
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    return ""


def _last_error_line(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        if re.search(r"\w+Error:", line, re.IGNORECASE):
            return line
        if re.search(r"\bException\b", line, re.IGNORECASE):
            return line
    return lines[-1]


class ClipboardMonitor:
    """
    Monitors the system clipboard for technical errors or terms.
    """

    def __init__(self, check_interval: float = 2.0):
        self.check_interval = check_interval
        self.last_content = ""
        self.is_running = False
        self.error_patterns = [
            r"Traceback \(most recent call last\):",
            r"\w+Error: .+",
            r"Exception in thread .+",
            r"failed with exit code \d+",
            r"(error|exception|code)[^\n]{0,40}0x[0-9a-fA-F]{8}",
            r"syntax error",
            r"segmentation fault",
            r"access violation",
        ]
        self._last_suggestion_mono: float = 0.0
        self._dedupe_line: str = ""
        self._dedupe_mono: float = 0.0

    def is_technical_error(self, text: str) -> bool:
        """Check if the text matches common technical error patterns."""
        if not text or len(text.strip()) < 10:
            return False
        if len(text) > _MAX_CLIPBOARD_CHARS:
            return False

        for pattern in self.error_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _should_suggest(self, error_line: str) -> bool:
        now = time.monotonic()
        if now - self._last_suggestion_mono < _RATE_LIMIT_SEC:
            return False
        if error_line == self._dedupe_line and now - self._dedupe_mono < _DEDUPE_SEC:
            return False
        return True

    def _mark_suggested(self, error_line: str) -> None:
        now = time.monotonic()
        self._last_suggestion_mono = now
        self._dedupe_line = error_line
        self._dedupe_mono = now

    async def _ddgs_search(self, query: str) -> list[dict[str, str]]:
        def _run() -> list[dict[str, str]]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=3))

        return await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=_DDGS_TIMEOUT_SEC,
        )

    async def _build_suggestion_payload(self, clipboard_text: str) -> dict[str, Any]:
        error_line = _last_error_line(clipboard_text)
        query = f"{redact(error_line)} solution"
        excerpt = redact(clipboard_text)[:_EXCERPT_MAX]

        solution = ""
        source_url: str | None = None
        try:
            results = await self._ddgs_search(query)
            if results:
                solution = (results[0].get("body") or "")[:_SOLUTION_MAX]
                href = results[0].get("href")
                source_url = str(href) if href else None
        except asyncio.TimeoutError:
            logger.warning("Clipboard DDGS search timed out after %ss", _DDGS_TIMEOUT_SEC)
        except (TimeoutError, ConnectionError) as e:
            logger.error("Error searching for clipboard solution: %s", e)
        except (AttributeError, ValueError, KeyError, RuntimeError) as e:
            logger.error("Data parsing error in clipboard solution: %s", e)

        return {
            "id": str(uuid.uuid4()),
            "error_excerpt": excerpt,
            "query": query,
            "solution": solution,
            "source_url": source_url,
            "ts": time.time(),
        }

    async def _invoke_callback(
        self,
        on_detection_callback: Callable[..., Any],
        payload: dict[str, Any],
    ) -> None:
        try:
            await on_detection_callback(payload)
        except Exception as e:
            key = type(e).__name__
            if key not in _callback_error_logged:
                logger.error("Clipboard callback error (%s): %s", key, e)
                _callback_error_logged.add(key)

    async def start(self, on_detection_callback: Any) -> None:
        """
        Starts the monitoring loop.
        Calls on_detection_callback(payload) when an error is found (K-19 ClipboardSuggestion).
        """
        self.is_running = True
        logger.info(
            "Clipboard Monitoring started (Interval: %s s)",
            self.check_interval,
        )

        while self.is_running:
            try:
                if not self.is_running:
                    break
                current_paste: str = await asyncio.to_thread(_read_clipboard_sync)

                if current_paste != self.last_content:
                    self.last_content = current_paste

                    helper_on = getattr(config, "clipboard_helper_enabled", True)
                    if not helper_on:
                        await asyncio.sleep(self.check_interval)
                        continue

                    if _is_self_write(current_paste):
                        await asyncio.sleep(self.check_interval)
                        continue

                    if len(current_paste) > _MAX_CLIPBOARD_CHARS:
                        await asyncio.sleep(self.check_interval)
                        continue

                    if self.is_technical_error(current_paste):
                        error_line = _last_error_line(current_paste)
                        if not self._should_suggest(error_line):
                            await asyncio.sleep(self.check_interval)
                            continue

                        logger.info("Technical content detected in clipboard!")
                        payload = await self._build_suggestion_payload(current_paste)
                        self._mark_suggested(error_line)
                        await self._invoke_callback(on_detection_callback, payload)

                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                logger.info("Clipboard monitoring loop cancelled.")
                self.is_running = False
                break
            except RuntimeError as e:
                if "Executor shutdown" in str(e):
                    logger.info(
                        "Clipboard executor shutdown detected. Stopping loop.",
                    )
                    self.is_running = False
                    break
                if _is_clipboard_contention(e):
                    logger.debug("Clipboard loop contention: %s", e)
                    await asyncio.sleep(_CLIPBOARD_RETRY_BASE_SEC * 2)
                    continue
                logger.warning("Clipboard loop runtime error: %s", e)
                await asyncio.sleep(5)
            except AttributeError as e:
                logger.error("Clipboard loop attribute error: %s", e)
                await asyncio.sleep(5)
            except OSError as e:
                if _is_clipboard_contention(e):
                    logger.debug("Clipboard loop IO contention: %s", e)
                    await asyncio.sleep(_CLIPBOARD_RETRY_BASE_SEC * 2)
                    continue
                logger.warning("Clipboard loop IO error: %s", e)
                await asyncio.sleep(10)

    def stop(self) -> None:
        """Stops the monitoring loop."""
        self.is_running = False
