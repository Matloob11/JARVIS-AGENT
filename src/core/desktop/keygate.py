"""Desktop Node keystroke gate (plan task C-121; K-16, K-76 run time).

The run-time foreground rules (policy.guard_input_runs) are applied to the keys an input-lock call sends. The
gate sits in front of the two libraries the laptop tools type with:
- PyAutoGUI (Notepad, WhatsApp, the window tools) calls its module function failSafeCheck() before every key it
  presses (press, write per character, hotkey, keyDown/keyUp, the mouse actions); install() wraps that function.
- pynput (type_text_tool and the clipboard's Ctrl+V, which the Notepad, WhatsApp and open_app tools use too)
  sends every key through pynput.keyboard.Controller.press (type() calls it per character); install_keyboard()
  wraps that method. A refused press first releases the keys that controller still holds down, so a refused
  Ctrl+V never leaves Ctrl pressed. Releases are never refused.

Per call the gate can:
- block(call_id): every further gated key of that call raises InputBlocked before it reaches the OS;
- judge(call_id, fn): every gated key of that call asks fn() first (the policy's key-time foreground rules); a
  code refuses that key, records the code (tripped(call_id)) and blocks the call from then on. So a call is
  judged by the windows its keys really reach, not by whatever has the focus while it does something else.

Which call a key belongs to comes from executor.current_call_id(): the executor runs a call's coroutine and all of
its thread work (CountingThreadPool copies the context into the worker) with the call's id set. Keys of other
calls, and keys outside any call, are never blocked or judged. A blocked call id stays blocked: call ids are never
run twice, so it can never block a later call (the oldest ids are dropped past MAX_BLOCKED).
"""
from __future__ import annotations

import importlib
import logging
import sys
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from src.core.desktop.executor import current_call_id

logger = logging.getLogger("JARVIS-DESKTOP.keygate")

MAX_BLOCKED = 512
_GATED_MODULES = ("pyautogui",)          # gated through their module function failSafeCheck
_GATED_KEYBOARDS = ("pynput.keyboard",)  # gated through Controller.press
_JUDGE_FAILED = "DESKTOP_REJECTED"       # a judge that raises refuses the key (fails closed)


class InputBlocked(RuntimeError):
    """Raised instead of a key or mouse action of a call whose run was refused (K-76 run time)."""


class KeystrokeGate:
    """Blocks or judges the gated keys of a running call (see the module docstring)."""

    def __init__(self, *, current: Callable[[], str | None] = current_call_id, max_blocked: int = MAX_BLOCKED) -> None:
        self._current = current
        self._max = max(1, int(max_blocked))
        self._blocked: OrderedDict[str, None] = OrderedDict()
        self._judges: dict[str, Callable[[], str | None]] = {}
        self._tripped: OrderedDict[str, str] = OrderedDict()
        self._import_failed: set[str] = set()
        self._lock = threading.Lock()

    def block(self, call_id: str) -> None:
        """No further gated action of this call reaches the OS."""
        call_id = str(call_id or "")
        if not call_id:
            return
        with self._lock:
            self._judges.pop(call_id, None)
            self._blocked[call_id] = None
            self._blocked.move_to_end(call_id)
            while len(self._blocked) > self._max:
                self._blocked.popitem(last=False)
        self.install_loaded()  # a tool that imported PyAutoGUI after the run started is gated from now on

    def is_blocked(self, call_id: str | None) -> bool:
        if not call_id:
            return False
        with self._lock:
            return call_id in self._blocked

    def judge(self, call_id: str, judge: Callable[[], str | None]) -> None:
        """Every further gated key of this call asks judge() first: None sends it, a code refuses it."""
        call_id = str(call_id or "")
        if not call_id:
            return
        with self._lock:
            self._judges[call_id] = judge
            while len(self._judges) > self._max:
                self._judges.pop(next(iter(self._judges)))

    def forget(self, call_id: str) -> None:
        """The call ended: its judge is dropped (a code it tripped stays readable)."""
        with self._lock:
            self._judges.pop(str(call_id or ""), None)

    def tripped(self, call_id: str | None) -> str | None:
        """The code that refused a key of this call, if a key was refused at key time."""
        if not call_id:
            return None
        with self._lock:
            return self._tripped.get(call_id)

    def check(self) -> None:
        """Raises InputBlocked when the calling context belongs to a blocked call, or when the call's judge
        refuses this key (which blocks the call from then on); else returns at once."""
        call_id = self._current()
        if not call_id:
            return
        with self._lock:
            blocked = call_id in self._blocked
            judge = self._judges.get(call_id)
        if blocked:
            raise InputBlocked("desktop input stopped: the foreground changed while the call ran")
        if judge is None:
            return
        try:
            code = judge()
        except Exception:  # pylint: disable=broad-exception-caught  # a broken judge refuses the key
            logger.exception("Desktop key judge failed")
            code = _JUDGE_FAILED
        if code is None:
            return
        with self._lock:
            self._tripped.setdefault(call_id, str(code))
            self._tripped.move_to_end(call_id)
            while len(self._tripped) > self._max:
                self._tripped.popitem(last=False)
        self.block(call_id)
        raise InputBlocked("desktop input stopped: this key would reach a window the call may not type into")

    def install(self, module: Any) -> bool:
        """Wraps module.failSafeCheck with the gate (once; installing again is a no-op). False when the module
        has no such function."""
        original = getattr(module, "failSafeCheck", None)
        if not callable(original):
            return False
        if getattr(original, "_jarvis_keygate", None) is self:
            return True

        def failSafeCheck() -> Any:  # pylint: disable=invalid-name  # PyAutoGUI's own name, looked up per key
            self.check()
            return original()

        failSafeCheck._jarvis_keygate = self  # type: ignore[attr-defined]  # pylint: disable=protected-access
        failSafeCheck.__wrapped__ = original  # type: ignore[attr-defined]
        try:
            module.failSafeCheck = failSafeCheck
        except (AttributeError, TypeError):
            logger.warning("Keystroke gate could not be installed on %s", getattr(module, "__name__", "?"))
            return False
        return True

    def install_keyboard(self, module: Any) -> bool:
        """Wraps module.Controller.press (pynput) with the gate (once). A refused press first releases the keys
        that controller pressed and has not released. False when the module has no such class."""
        cls = getattr(module, "Controller", None)
        original = getattr(cls, "press", None) if cls is not None else None
        release = getattr(cls, "release", None) if cls is not None else None
        if not callable(original) or not callable(release):
            return False
        if getattr(original, "_jarvis_keygate", None) is self:
            return True
        down: weakref.WeakKeyDictionary[Any, list[Any]] = weakref.WeakKeyDictionary()
        down_lock = threading.Lock()
        gate = self

        def held_by(controller: Any) -> list[Any]:
            try:
                return down.setdefault(controller, [])
            except TypeError:  # not weak-referenceable: nothing is tracked
                return []

        def press(controller: Any, key: Any) -> Any:
            try:
                gate.check()
            except InputBlocked:
                with down_lock:
                    held = list(held_by(controller))
                    held_by(controller).clear()
                for key_down in reversed(held):
                    try:
                        release(controller, key_down)
                    except Exception:  # pylint: disable=broad-exception-caught  # best effort, never stuck
                        logger.warning("A held key could not be released after a refused key")
                raise
            result = original(controller, key)
            with down_lock:
                held_by(controller).append(key)
            return result

        def release_key(controller: Any, key: Any) -> Any:
            try:
                return release(controller, key)
            finally:
                with down_lock:
                    held = held_by(controller)
                    if key in held:
                        held.remove(key)

        press._jarvis_keygate = self  # type: ignore[attr-defined]  # pylint: disable=protected-access
        press.__wrapped__ = original  # type: ignore[attr-defined]
        release_key._jarvis_keygate = self  # type: ignore[attr-defined]  # pylint: disable=protected-access
        release_key.__wrapped__ = release  # type: ignore[attr-defined]
        try:
            cls.press = press
            cls.release = release_key
        except (AttributeError, TypeError):
            logger.warning("Keystroke gate could not be installed on %s", getattr(module, "__name__", "?"))
            return False
        return True

    def install_loaded(self) -> bool:
        """Installs the gate on every gated module this process has imported; True when one is gated. Never
        imports a module itself (importing PyAutoGUI needs a desktop session; load() does it on purpose)."""
        gated = False
        for name in _GATED_MODULES + _GATED_KEYBOARDS:
            module = sys.modules.get(name)
            if module is None:
                continue
            try:
                installer = self.install if name in _GATED_MODULES else self.install_keyboard
                gated = installer(module) or gated
            except Exception:  # pylint: disable=broad-exception-caught  # the cancel still stops the call
                logger.exception("Keystroke gate install failed")
        return gated

    def load(self) -> bool:
        """Imports the gated input libraries the laptop tools type with (each is tried once per process; one that
        cannot load cannot send a key either) and installs the gate on them; True when one is gated. Runs right
        before a call whose keys are judged, so a tool that imports PyAutoGUI lazily is gated from its first key.
        Blocking: call it off the event loop."""
        for name in _GATED_MODULES + _GATED_KEYBOARDS:
            if name in sys.modules or name in self._import_failed:
                continue
            try:
                importlib.import_module(name)
            except Exception:  # pylint: disable=broad-exception-caught  # no desktop session, not installed
                self._import_failed.add(name)
                logger.info("Gated input library %s is not available here", name)
        return self.install_loaded()


GATE = KeystrokeGate()
