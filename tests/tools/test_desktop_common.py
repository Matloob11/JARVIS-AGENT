"""X-11 / TD-08: helpers from services.system.jarvis_desktop_common + guard self-test."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.system.jarvis_desktop_common import (
    WindowInfo,
    clipboard_paste_text,
    force_foreground,
    is_browser_window,
    is_network_path,
    is_protected_window,
    is_sensitive_path,
    resolve_in_roots,
    run_cancellable,
    safe_child_path,
    tool_result,
)


def test_path_traversal_and_system_paths_rejected(tmp_path: Path) -> None:
    roots = [tmp_path]
    assert resolve_in_roots(r"..\..\Windows", roots) is None
    assert resolve_in_roots("C:/Windows/win.ini", roots) is None
    assert resolve_in_roots(r"\\server\share\file.txt", roots) is None
    assert safe_child_path(tmp_path, "a.txt:x") is None
    assert is_network_path(r"\\unc\share") is True
    assert is_network_path("C:/Windows/win.ini:stream") is True


def test_sensitive_paths_rejected() -> None:
    # Avoid pytest's AppData temp roots (is_sensitive_path treats AppData as sensitive).
    with tempfile.TemporaryDirectory(dir=str(Path.home() / "Desktop")) as raw:
        root = Path(raw)
        assert is_sensitive_path(root / ".env") is True
        assert is_sensitive_path(root / ".env.local") is True
        assert is_sensitive_path(Path.home() / ".ssh" / "id_rsa") is True
        assert resolve_in_roots(root / ".env", [root]) is None
        ok = root / "notes.txt"
        ok.write_text("hi", encoding="utf-8")
        assert resolve_in_roots(ok, [root]) == ok.resolve()


def test_protected_and_browser_window_detection() -> None:
    assert is_protected_window(WindowInfo(1, "JARVIS & ANNA", 99999, "notepad.exe")) is True
    assert is_protected_window(WindowInfo(1, "Code", 99999, "Code.exe")) is True
    assert is_protected_window(WindowInfo(1, "Notes", 99999, "notepad.exe")) is False
    assert is_browser_window(WindowInfo(1, "Google", 1, "chrome.exe")) is True
    assert is_browser_window(WindowInfo(1, "Edge", 1, "msedge.exe")) is True
    assert is_browser_window(WindowInfo(1, "Docs", 1, "notepad.exe")) is False


def test_tool_result_shape() -> None:
    r = tool_result(
        "ok",
        "ho gaya",
        tool="demo",
        data={"n": 1},
        error_code=None,
        hint="tip",
        confirm_id="c1",
        legacy_flag=True,
    )
    assert r["status"] == "ok"
    assert r["message"] == "ho gaya"
    assert r["tool"] == "demo"
    assert r["data"] == {"n": 1}
    assert r["hint"] == "tip"
    assert r["confirm_id"] == "c1"
    assert r["legacy_flag"] is True
    assert "error_code" not in r


def test_force_foreground_returns_false_on_set_fail() -> None:
    async def _run() -> bool:
        with patch("services.system.jarvis_desktop_common.win32gui") as gui, patch(
            "services.system.jarvis_desktop_common.win32con", MagicMock()
        ), patch("services.system.jarvis_desktop_common.win32process", MagicMock()), patch(
            "services.system.jarvis_desktop_common.win32api", MagicMock()
        ):
            gui.IsWindow.return_value = True
            gui.IsIconic.return_value = False
            gui.GetForegroundWindow.return_value = 42
            gui.SetForegroundWindow.side_effect = OSError("denied")
            gui.BringWindowToTop.side_effect = OSError("denied")
            return await force_foreground(99)

    assert asyncio.run(_run()) is False


def test_run_cancellable_sets_event_and_waits() -> None:
    seen: dict[str, object] = {}

    def worker(cancel_event: threading.Event) -> str:
        seen["got"] = cancel_event
        while not cancel_event.wait(0.05):
            pass
        time.sleep(0.05)
        return "done"

    async def run_and_cancel() -> None:
        task = asyncio.create_task(run_cancellable(worker))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())
    assert isinstance(seen.get("got"), threading.Event)
    assert seen["got"].is_set()  # type: ignore[union-attr]


def test_clipboard_restore_skipped_when_changed() -> None:
    """If clipboard no longer holds our paste text, do not restore the prior value."""
    calls: list[str] = []

    class FakeClip:
        def __init__(self) -> None:
            self._data = "ORIGINAL"
            self._set_count = 0

        def OpenClipboard(self) -> None:
            return None

        def CloseClipboard(self) -> None:
            return None

        def IsClipboardFormatAvailable(self, _fmt: int) -> bool:
            return True

        def GetClipboardData(self, _fmt: int) -> str:
            if self._set_count >= 1:
                return "CHANGED_BY_USER"
            return self._data

        def EmptyClipboard(self) -> None:
            return None

        def SetClipboardData(self, _fmt: int, text: str) -> None:
            calls.append(text)
            self._data = text
            self._set_count += 1

    fake = FakeClip()

    async def _run() -> bool:
        with patch("services.system.jarvis_desktop_common.win32clipboard", fake), patch(
            "services.system.jarvis_desktop_common.win32con", MagicMock(CF_UNICODETEXT=13)
        ), patch("services.system.jarvis_desktop_common._paste_ctrl_v", lambda: None), patch(
            "services.system.jarvis_desktop_common._clipboard_open_retry", lambda *_a, **_k: None
        ), patch("time.sleep", lambda *_a, **_k: None):
            return await clipboard_paste_text("PASTED", restore=True)

    assert asyncio.run(_run()) is True
    assert calls == ["PASTED"]


def test_guard_blocks_pyautogui_subprocess_httpx() -> None:
    import httpx
    import pyautogui

    with pytest.raises(AssertionError, match="REAL SIDE EFFECT"):
        pyautogui.write("hello")

    with pytest.raises(AssertionError, match="REAL SIDE EFFECT"):
        subprocess.run(["echo", "hi"], check=False)

    async def _post() -> None:
        client = httpx.AsyncClient()
        try:
            with pytest.raises(AssertionError, match="REAL SIDE EFFECT"):
                await client.post("http://example.com/", json={})
        finally:
            await client.aclose()

    asyncio.run(_post())


def test_bridge_url_is_local() -> None:
    from services.utils.jarvis_config import config
    from services.utils.jarvis_logger import UIBridgeHandler
    import logging

    assert "127.0.0.1" in config.bridge_url or "localhost" in config.bridge_url
    for name in list(logging.Logger.manager.loggerDict.keys()) + [""]:
        logger = logging.getLogger(name)
        assert not any(isinstance(h, UIBridgeHandler) for h in logger.handlers)
