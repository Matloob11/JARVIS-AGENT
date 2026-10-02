"""
tests/exhaustive (Claude lane, plan C-61): no real desktop, process or network side effects.

block_real_side_effects is the K-27 guard from tests/tools/side_effect_guard.py (autouse): real
keyboard, mouse, clipboard, process, browser, mail and HTTP calls raise
AssertionError('REAL SIDE EFFECT: <name>'). block_exhaustive_desktop_gaps adds the entry points the
desktop tests here could still reach past K-27: the pynput mouse position setter (a real cursor
move), the pycaw speaker lookup (real mute and volume changes), win32clipboard Open/CloseClipboard
and the pyautogui input functions K-27 leaves out. Both stay off only with JARVIS_LIVE_GUI_TESTS=1.

Replace a guarded entry point (subprocess, pyautogui, webbrowser, pycaw, pynput, win32...) with
patch.object inside the test or in a requested fixture, never with monkeypatch.setattr: the
monkeypatch fixture is set up before the guard, so its undo would put the guard's mock back after
the guard has stopped and leak it into later tests.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import pytest

from tests.tools.side_effect_guard import block_real_side_effects  # noqa: F401  # pylint: disable=unused-import

_LIVE = os.environ.get("JARVIS_LIVE_GUI_TESTS", "").strip() == "1"

_PYAUTOGUI_EXTRA = ("mouseDown", "mouseUp", "doubleClick", "tripleClick", "rightClick",
                    "middleClick", "move", "moveRel", "drag", "dragRel", "hscroll", "vscroll")


def _blocked(name: str) -> Callable[..., Any]:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"REAL SIDE EFFECT: {name}")

    return _raise


def _try_import(name: str) -> Any:
    """The module, or None. Like K-27's _try_import, any error counts: on a headless Linux host
    'import pyautogui' raises KeyError('DISPLAY'), not ImportError."""
    try:
        return importlib.import_module(name)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _gap_patches() -> list[Any]:
    patches: list[Any] = []

    def add(target: Any, attr: str, name: str) -> None:
        if target is not None and hasattr(target, attr):
            patches.append(patch.object(target, attr, side_effect=_blocked(name)))

    # Controller.position = (x, y) calls _position_set, which K-27 does not block.
    mouse = _try_import("pynput.mouse")
    add(getattr(mouse, "Controller", None), "_position_set", "pynput.mouse.Controller.position")
    audio = getattr(_try_import("pycaw.pycaw"), "AudioUtilities", None)
    add(audio, "GetSpeakers", "pycaw.AudioUtilities.GetSpeakers")
    add(audio, "GetDeviceEnumerator", "pycaw.AudioUtilities.GetDeviceEnumerator")
    win32clipboard = _try_import("win32clipboard")
    add(win32clipboard, "OpenClipboard", "win32clipboard.OpenClipboard")
    add(win32clipboard, "CloseClipboard", "win32clipboard.CloseClipboard")
    pyautogui = _try_import("pyautogui")
    for attr in _PYAUTOGUI_EXTRA:
        add(pyautogui, attr, f"pyautogui.{attr}")
    return patches


@pytest.fixture(autouse=True)
def block_exhaustive_desktop_gaps(
        block_real_side_effects: None,  # pylint: disable=redefined-outer-name
) -> Iterator[None]:
    """Blocks the desktop entry points above after the K-27 guard is active."""
    if _LIVE:
        yield
        return
    patches = _gap_patches()
    for item in patches:
        item.start()
    try:
        yield
    finally:
        for item in reversed(patches):
            item.stop()


@pytest.fixture(name="instant_sleep")
def _instant_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncio.sleep returns at once but still yields to the event loop."""
    real_sleep = asyncio.sleep

    async def _sleep(_delay: float = 0, result: Any = None) -> Any:
        await real_sleep(0)
        return result

    monkeypatch.setattr(asyncio, "sleep", _sleep)
