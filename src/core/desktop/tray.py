"""System-tray control for the Desktop Node (plan task C-121 step 9; K-76).

A tray icon with three choices: Off, Read and Full. Off and Read tighten the node; Full raises it and
also clears a panic latch or a remote stop (the only place loosening happens, D3/K-76). The tray runs
on the laptop in the node process.

pystray and Pillow are optional: they are imported lazily and guarded, so this module imports with
neither installed (Linux/CI, or a laptop before Claude installs them with the node stopped). When the
tray cannot start it logs and the node keeps running headless; the menu callbacks are plain methods the
tests exercise without any real tray.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

logger = logging.getLogger("JARVIS-DESKTOP.tray")


class TrayState(Protocol):
    """What the tray drives: the three mode choices (PersistentState implements them)."""

    def tray_off(self) -> None: ...
    def tray_read(self) -> None: ...
    def tray_full(self) -> None: ...

    def current(self) -> Any: ...


class Tray:
    """The optional pystray tray. start() builds the icon on its own thread when pystray is available.

    The Off/Read/Full methods are the menu actions and are safe to call directly (tests, or another
    thread). available() says whether a real tray could be built.
    """

    TITLE = "JARVIS Desktop"

    def __init__(self, state: TrayState) -> None:
        self._state = state
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    # --- the menu actions ------------------------------------------------------------------------------------

    def choose_off(self) -> None:
        logger.info("Tray: Off")
        self._state.tray_off()
        self._refresh()

    def choose_read(self) -> None:
        logger.info("Tray: Read")
        self._state.tray_read()
        self._refresh()

    def choose_full(self) -> None:
        logger.info("Tray: Full (clears panic / remote stop)")
        self._state.tray_full()
        self._refresh()

    # --- lifecycle -------------------------------------------------------------------------------------------

    @staticmethod
    def available() -> bool:
        try:
            import pystray  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import

            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    def start(self) -> Tray:
        if self._thread is not None:
            return self
        if not self.available():
            logger.info("Tray not available (pystray not installed); the node runs headless")
            return self
        self._thread = threading.Thread(target=self._run, name="desktop-tray", daemon=True)
        self._thread.start()
        return self

    def _refresh(self) -> None:
        """Redraw the icon tooltip after a mode change; a no-op when no real tray is running."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = f"{self.TITLE} ({self._state.current().mode})"
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        self._thread = None

    def _run(self) -> None:  # pragma: no cover - requires a real display/tray
        try:
            import pystray  # pylint: disable=import-outside-toplevel

            menu = pystray.Menu(
                pystray.MenuItem("Full (resume control)", lambda *_: self.choose_full()),
                pystray.MenuItem("Read only", lambda *_: self.choose_read()),
                pystray.MenuItem("Off", lambda *_: self.choose_off()),
                pystray.MenuItem("Quit", lambda icon, *_: icon.stop()),
            )
            self._icon = pystray.Icon("jarvis-desktop", self._icon_image(), self.TITLE, menu)
            self._icon.run()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Tray stopped")

    def _icon_image(self) -> Any:  # pragma: no cover - requires Pillow
        try:
            from PIL import Image, ImageDraw  # pylint: disable=import-outside-toplevel

            image = Image.new("RGB", (64, 64), (16, 24, 40))
            draw = ImageDraw.Draw(image)
            draw.ellipse((12, 12, 52, 52), fill=(64, 160, 255))
            return image
        except Exception:  # pylint: disable=broad-exception-caught
            return None
