"""
K-27 / X-11: Autouse guard that blocks real desktop, network, and process side effects.

Import this module from conftest so the fixture registers. Skip with JARVIS_LIVE_GUI_TESTS=1.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable, Iterator, Optional
from unittest.mock import patch

import pytest

_LIVE = os.environ.get("JARVIS_LIVE_GUI_TESTS", "").strip() == "1"


def _raise(name: str) -> Callable[..., Any]:
    def _blocked(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError(f"REAL SIDE EFFECT: {name}")

    return _blocked


def _try_import(name: str) -> Optional[Any]:
    try:
        return __import__(name)
    except Exception:
        return None


def _patch_attr(target: Any, attr: str, name: str, patches: list[Any]) -> None:
    if target is None or not hasattr(target, attr):
        return
    patches.append(patch.object(target, attr, side_effect=_raise(name)))


def _remove_ui_bridge_handlers() -> None:
    from services.utils.jarvis_logger import UIBridgeHandler

    manager = logging.Logger.manager
    loggers: list[logging.Logger] = [logging.getLogger()]
    loggers.extend(
        logging.getLogger(name)
        for name in list(manager.loggerDict.keys())
        if isinstance(logging.getLogger(name), logging.Logger)
    )
    for logger in loggers:
        for handler in list(logger.handlers):
            if isinstance(handler, UIBridgeHandler):
                logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def block_real_side_effects() -> Iterator[None]:
    """Block real GUI/process/network side effects unless JARVIS_LIVE_GUI_TESTS=1."""
    if _LIVE:
        yield
        return

    patches: list[Any] = []

    # --- pyautogui ---
    pyautogui = _try_import("pyautogui")
    for attr in (
        "write",
        "typewrite",
        "press",
        "hotkey",
        "click",
        "moveTo",
        "dragTo",
        "scroll",
        "keyDown",
        "keyUp",
    ):
        _patch_attr(pyautogui, attr, f"pyautogui.{attr}", patches)

    # --- pynput Controllers ---
    try:
        from pynput.keyboard import Controller as KbController
        from pynput.mouse import Controller as MouseController

        for cls, methods in (
            (KbController, ("press", "release", "type", "tap")),
            (MouseController, ("press", "release", "click", "move", "scroll")),
        ):
            for method in methods:
                if hasattr(cls, method):
                    patches.append(
                        patch.object(cls, method, side_effect=_raise(f"pynput.{cls.__module__}.{method}"))
                    )
    except Exception:
        pass

    # --- os / subprocess / asyncio / webbrowser ---
    import asyncio
    import os as _os
    import subprocess
    import webbrowser

    for attr in ("startfile", "system"):
        if hasattr(_os, attr):
            patches.append(patch.object(_os, attr, side_effect=_raise(f"os.{attr}")))

    for attr in ("Popen", "run", "call"):
        patches.append(patch.object(subprocess, attr, side_effect=_raise(f"subprocess.{attr}")))

    for attr in ("create_subprocess_exec", "create_subprocess_shell"):
        patches.append(patch.object(asyncio, attr, side_effect=_raise(f"asyncio.{attr}")))

    patches.append(patch.object(webbrowser, "open", side_effect=_raise("webbrowser.open")))

    # --- clipboard ---
    pyperclip = _try_import("pyperclip")
    _patch_attr(pyperclip, "copy", "pyperclip.copy", patches)
    _patch_attr(pyperclip, "paste", "pyperclip.paste", patches)

    win32clipboard = _try_import("win32clipboard")
    for attr in ("SetClipboardText", "GetClipboardData", "SetClipboardData", "EmptyClipboard"):
        _patch_attr(win32clipboard, attr, f"win32clipboard.{attr}", patches)

    # --- win32gui / win32api ---
    win32gui = _try_import("win32gui")
    for attr in ("PostMessage", "SetForegroundWindow", "ShowWindow", "BringWindowToTop"):
        _patch_attr(win32gui, attr, f"win32gui.{attr}", patches)

    win32api = _try_import("win32api")
    for attr in ("keybd_event", "PostMessage"):
        _patch_attr(win32api, attr, f"win32api.{attr}", patches)

    # --- ctypes windll ---
    try:
        import ctypes

        user32 = getattr(ctypes.windll, "user32", None)
        shell32 = getattr(ctypes.windll, "shell32", None)
        _patch_attr(user32, "keybd_event", "ctypes.windll.user32.keybd_event", patches)
        _patch_attr(user32, "SendInput", "ctypes.windll.user32.SendInput", patches)
        _patch_attr(shell32, "SHEmptyRecycleBinW", "ctypes.windll.shell32.SHEmptyRecycleBinW", patches)
    except Exception:
        pass

    # --- network / mail ---
    smtplib = _try_import("smtplib")
    _patch_attr(smtplib, "SMTP", "smtplib.SMTP", patches)

    requests = _try_import("requests")
    _patch_attr(requests, "get", "requests.get", patches)
    _patch_attr(requests, "post", "requests.post", patches)

    httpx = _try_import("httpx")
    if httpx is not None:
        _patch_attr(httpx, "get", "httpx.get", patches)
        _patch_attr(httpx, "post", "httpx.post", patches)
        if hasattr(httpx, "AsyncClient"):
            patches.append(
                patch.object(httpx.AsyncClient, "get", side_effect=_raise("httpx.AsyncClient.get"))
            )
            patches.append(
                patch.object(httpx.AsyncClient, "post", side_effect=_raise("httpx.AsyncClient.post"))
            )

    # Activate all patches
    for p in patches:
        p.start()

    # Point bridge at a closed local port and strip UI bridge log handlers
    try:
        from services.utils.jarvis_config import config

        config.bridge_url = "http://127.0.0.1:9"
        if hasattr(config, "ui_bridge_url"):
            config.ui_bridge_url = "http://127.0.0.1:9"
    except Exception:
        pass
    os.environ["UI_BRIDGE_URL"] = "http://127.0.0.1:9"
    try:
        _remove_ui_bridge_handlers()
    except Exception:
        pass

    try:
        yield
    finally:
        for p in reversed(patches):
            try:
                p.stop()
            except Exception:
                pass


# Ensure project root is importable when this module is loaded early
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
