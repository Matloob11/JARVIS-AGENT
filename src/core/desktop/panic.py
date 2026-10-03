"""Panic hotkey for the Desktop Node (plan task C-121 step 9; K-76).

Ctrl+Alt+Shift+F12, pressed as a genuine keystroke (no LLKHF_INJECTED), means 'stop now': the node
goes to mode off, cancels every in-flight call and latches the panic in run/desktop_state.json. The
latch survives a reboot or logon and is cleared only at the laptop (the tray Full choice), never from
the UI, the bridge or the agent.

This module holds only the combo state machine: it consumes the genuine key events that presence.py's
low-level keyboard hook already produces (vk, injected, down) and calls a callback once per full press.
No windll here, so it imports anywhere and the tests feed it key events directly, with no real hook.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger("JARVIS-DESKTOP.panic")

# Virtual-key codes (winuser.h). The low-level hook reports the specific left/right modifier keys, which
# _CANON folds onto the generic modifier so either side of the keyboard arms the combo.
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_F12 = 0x7B

_CANON = {
    VK_LSHIFT: VK_SHIFT, VK_RSHIFT: VK_SHIFT,
    VK_LCONTROL: VK_CONTROL, VK_RCONTROL: VK_CONTROL,
    VK_LMENU: VK_MENU, VK_RMENU: VK_MENU,
}
_MODIFIERS = frozenset({VK_SHIFT, VK_CONTROL, VK_MENU})
PANIC_COMBO = "Ctrl+Alt+Shift+F12"


class PanicHotkey:
    """Recognises the genuine Ctrl+Alt+Shift+F12 press and fires on_panic once per press.

    on_key(vk, injected, down) is wired to presence.add_key_listener. Injected events are ignored, so a
    tool that presses F12 itself (DevTools) can never trip the panic latch. The callback fires on the
    F12 key-down while all three modifiers are held down, and re-arms only after F12 is released, so a
    held combo fires once.
    """

    def __init__(self, on_panic: Callable[[], None]) -> None:
        self._on_panic = on_panic
        self._down: set[int] = set()
        self._armed = True

    def on_key(self, vk: int, injected: bool, down: bool) -> None:
        if injected:
            return
        key = _CANON.get(vk, vk)
        if down:
            self._down.add(key)
        else:
            self._down.discard(key)
            if key == VK_F12:
                self._armed = True
            return
        if key == VK_F12 and self._armed and _MODIFIERS <= self._down:
            self._armed = False
            self._fire()

    def reset(self) -> None:
        self._down.clear()
        self._armed = True

    def _fire(self) -> None:
        logger.warning("Panic hotkey %s pressed: desktop off and cancel all", PANIC_COMBO)
        try:
            self._on_panic()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Panic handler raised")
