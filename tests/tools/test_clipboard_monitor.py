"""X-25: ClipboardMonitor structured suggestions (K-19)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from services.automation import jarvis_clipboard as jc
from services.automation.jarvis_clipboard import ClipboardMonitor, note_self_write

_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "C:\\Users\\secretuser\\test.py", line 1\n'
    "ValueError: token abcdefghijklmnopqrstuvwxyz0123456789 user@example.com\n"
)

_ETH_ADDRESS = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _reset_clipboard_module_state() -> None:
    jc._last_self_write_hash = None
    jc._callback_error_logged.clear()
    yield
    jc._last_self_write_hash = None
    jc._callback_error_logged.clear()


def test_ethereum_address_not_technical_error() -> None:
    monitor = ClipboardMonitor()
    assert monitor.is_technical_error(_ETH_ADDRESS) is False


def test_traceback_is_technical_error() -> None:
    monitor = ClipboardMonitor()
    assert monitor.is_technical_error(_TRACEBACK) is True


def test_self_write_ignored_in_loop() -> None:
    note_self_write(_TRACEBACK)
    assert jc._is_self_write(_TRACEBACK) is True


@pytest.mark.asyncio
async def test_traceback_triggers_one_redacted_callback() -> None:
    monitor = ClipboardMonitor(check_interval=0.02)
    seen: list[dict] = []

    async def on_detect(payload: dict) -> None:
        seen.append(payload)

    paste_count = 0

    def fake_paste() -> str:
        nonlocal paste_count
        paste_count += 1
        return _TRACEBACK

    mock_ddgs = MagicMock()
    mock_ddgs.__enter__.return_value.text.return_value = [
        {"body": "Try fixing the value.", "href": "https://example.com/fix"},
    ]

    with patch("services.automation.jarvis_clipboard.pyperclip.paste", fake_paste):
        with patch("services.automation.jarvis_clipboard.DDGS", return_value=mock_ddgs):
            task = asyncio.create_task(monitor.start(on_detect))
            await asyncio.sleep(0.12)
            monitor.stop()
            task.cancel()
            await asyncio.wait([task], timeout=2.0)

    assert len(seen) == 1
    payload = seen[0]
    assert set(payload.keys()) == {
        "id",
        "error_excerpt",
        "query",
        "solution",
        "source_url",
        "ts",
    }
    assert "user@example.com" not in payload["error_excerpt"]
    assert "[email]" in payload["error_excerpt"] or "[email]" in payload["query"]
    assert len(payload["error_excerpt"]) <= 200
    assert len(payload["solution"]) <= 500
    assert payload["source_url"] == "https://example.com/fix"


@pytest.mark.asyncio
async def test_disabled_flag_skips_detection() -> None:
    monitor = ClipboardMonitor(check_interval=0.02)
    seen: list[dict] = []

    async def on_detect(payload: dict) -> None:
        seen.append(payload)

    cfg = MagicMock()
    cfg.clipboard_helper_enabled = False

    with patch("services.automation.jarvis_clipboard.pyperclip.paste", return_value=_TRACEBACK):
        with patch("services.automation.jarvis_clipboard.config", cfg):
            task = asyncio.create_task(monitor.start(on_detect))
            await asyncio.sleep(0.08)
            monitor.stop()
            task.cancel()
            await asyncio.wait([task], timeout=2.0)

    assert seen == []


@pytest.mark.asyncio
async def test_self_write_skips_callback() -> None:
    note_self_write(_TRACEBACK)
    monitor = ClipboardMonitor(check_interval=0.02)
    seen: list[dict] = []

    async def on_detect(payload: dict) -> None:
        seen.append(payload)

    with patch("services.automation.jarvis_clipboard.pyperclip.paste", return_value=_TRACEBACK):
        task = asyncio.create_task(monitor.start(on_detect))
        await asyncio.sleep(0.08)
        monitor.stop()
        task.cancel()
        await asyncio.wait([task], timeout=2.0)

    assert seen == []


@pytest.mark.asyncio
async def test_callback_exception_does_not_stop_loop() -> None:
    monitor = ClipboardMonitor(check_interval=0.02)
    paste_calls: list[int] = []

    async def on_detect(_payload: dict) -> None:
        raise RuntimeError("callback failed")

    def fake_paste() -> str:
        paste_calls.append(1)
        return _TRACEBACK

    mock_ddgs = MagicMock()
    mock_ddgs.__enter__.return_value.text.return_value = [
        {"body": "hint", "href": "https://example.com"},
    ]

    with patch("services.automation.jarvis_clipboard.pyperclip.paste", fake_paste):
        with patch("services.automation.jarvis_clipboard.DDGS", return_value=mock_ddgs):
            task = asyncio.create_task(monitor.start(on_detect))
            await asyncio.sleep(0.15)
            monitor.stop()
            task.cancel()
            await asyncio.wait([task], timeout=2.0)

    assert len(paste_calls) >= 2


@pytest.mark.asyncio
async def test_query_redacts_sensitive_bits() -> None:
    monitor = ClipboardMonitor(check_interval=0.02)
    captured_query: list[str] = []

    async def on_detect(_payload: dict) -> None:
        pass

    long_token = "x" * 30
    win_user = os.environ.get("USERNAME", "secretuser")
    rich_trace = (
        "Traceback (most recent call last):\n"
        f"ValueError: {long_token} {win_user}@example.com\n"
    )

    mock_ddgs = MagicMock()

    def _text(query: str, max_results: int = 3) -> list[dict[str, str]]:
        captured_query.append(query)
        return [{"body": "ok", "href": "https://example.com"}]

    mock_ddgs.__enter__.return_value.text.side_effect = _text

    with patch("services.automation.jarvis_clipboard.pyperclip.paste", return_value=rich_trace):
        with patch("services.automation.jarvis_clipboard.DDGS", return_value=mock_ddgs):
            task = asyncio.create_task(monitor.start(on_detect))
            await asyncio.sleep(0.08)
            monitor.stop()
            task.cancel()
            await asyncio.wait([task], timeout=2.0)

    assert captured_query
    q = captured_query[0]
    assert "@example.com" not in q
    assert long_token not in q
    assert win_user not in q or "[email]" in q
