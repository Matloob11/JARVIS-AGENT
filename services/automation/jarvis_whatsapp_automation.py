"""
Jarvis WhatsApp Automation Module (X-41)
Sends messages through WhatsApp Desktop (Store app) with an install check, process-based window
matching (never a browser), a deep link for a known contact and a Ctrl+F clipboard search otherwise,
a FOCUS_LOST abort before anything is typed or sent, and a safe WM_CLOSE-only close.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
from typing import Any, Optional

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import (
    WindowInfo,
    clipboard_paste_text,
    close_windows_gracefully,
    desktop_control_enabled,
    find_windows,
    force_foreground,
    is_browser_window,
    tool_result,
)
from services.utils import jarvis_delivery
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_win32 import win32con, win32gui

try:
    import win32clipboard  # pylint: disable=import-error
except ImportError:  # pragma: no cover
    win32clipboard = None  # type: ignore[assignment]

# Setup logging
logger = setup_logger("JARVIS-WHATSAPP")

# WhatsApp Store app id (same default as services.utils.jarvis_config.config.whatsapp_app_id).
_DEFAULT_WHATSAPP_APP_ID = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"

# Windows this module will ever focus or close: the WhatsApp Desktop process itself, or the UWP
# frame host with the exact title 'WhatsApp'. chrome.exe / msedge.exe / firefox.exe (WhatsApp Web)
# are excluded by is_browser_window() below, never by title alone.
_WHATSAPP_PROCESS_NAMES = frozenset({"whatsapp.exe", "whatsapp.root.exe"})
_WHATSAPP_FRAME_HOST_PROCESS = "applicationframehost.exe"
_WHATSAPP_EXACT_TITLE = "whatsapp"

# is_whatsapp_installed() cache: 60 s only, Sir may install it mid-session.
_INSTALL_CACHE_TTL_S = 60.0
_install_cache: dict[str, Any] = {"ts": 0.0, "value": None}

_FUZZY_MATCH_THRESHOLD = 90

_pg = None


def _pg_mod():
    """Lazy pyautogui handle (no import-time side effects; X-65)."""
    global _pg  # pylint: disable=global-statement
    if _pg is None:
        try:
            import pyautogui as pg  # pylint: disable=import-outside-toplevel

            _pg = pg
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyautogui unavailable") from exc
    return _pg


# ---------------------------------------------------------------------------
# 1. is_whatsapp_installed(): %LOCALAPPDATA%/Packages/<family>, cached 60 s
# ---------------------------------------------------------------------------


def _whatsapp_package_family(app_id: str) -> str:
    return (app_id or "").split("!", 1)[0]


def is_whatsapp_installed(*, force_refresh: bool = False) -> bool:
    """
    True while the WhatsApp Desktop (Store) package folder exists under
    %LOCALAPPDATA%/Packages/<family of config.whatsapp_app_id>. Cached for 60 s so a fresh
    install during the session is picked up without restarting anything.
    """
    now = time.monotonic()
    cached = _install_cache.get("value")
    cached_ts = float(_install_cache.get("ts") or 0.0)
    if not force_refresh and cached is not None and (now - cached_ts) < _INSTALL_CACHE_TTL_S:
        return bool(cached)

    app_id = getattr(config, "whatsapp_app_id", None) or _DEFAULT_WHATSAPP_APP_ID
    family = _whatsapp_package_family(app_id)
    local_appdata = os.environ.get("LOCALAPPDATA") or ""
    installed = False
    if family and local_appdata:
        pkg_dir = os.path.join(local_appdata, "Packages", family)
        try:
            installed = os.path.isdir(pkg_dir)
        except OSError:
            installed = False

    _install_cache["ts"] = now
    _install_cache["value"] = installed
    return installed


def _reset_whatsapp_install_cache() -> None:
    """Test helper: clears the 60 s install-check cache."""
    _install_cache["ts"] = 0.0
    _install_cache["value"] = None


# ---------------------------------------------------------------------------
# 2. _find_whatsapp_windows(): process-based match, never a browser
# ---------------------------------------------------------------------------


def _find_whatsapp_windows() -> list[WindowInfo]:
    """
    Top-level windows that are WhatsApp Desktop itself: WhatsApp.exe / WhatsApp.Root.exe, or the
    UWP ApplicationFrameHost.exe with the exact title 'WhatsApp'. A browser window (chrome.exe,
    msedge.exe, firefox.exe — WhatsApp Web) is never returned, whatever its title says.
    """
    try:
        windows = find_windows(visible_only=True)
    except Exception:  # pylint: disable=broad-exception-caught
        return []

    matches: list[WindowInfo] = []
    for win in windows:
        if is_browser_window(win):
            continue
        pname = (win.process_name or "").lower()
        if pname in _WHATSAPP_PROCESS_NAMES:
            matches.append(win)
        elif pname == _WHATSAPP_FRAME_HOST_PROCESS and (win.title or "").strip().lower() == _WHATSAPP_EXACT_TITLE:
            matches.append(win)
    return matches


def _is_whatsapp_foreground() -> bool:
    """True while the foreground window is one of _find_whatsapp_windows()."""
    if win32gui is None:
        return True  # cannot verify (no pywin32): assume OK rather than block every send
    try:
        fg = win32gui.GetForegroundWindow()
    except Exception:  # pylint: disable=broad-exception-caught
        return True
    try:
        matches = _find_whatsapp_windows()
    except Exception:  # pylint: disable=broad-exception-caught
        return True
    return any(int(w.hwnd) == int(fg) for w in matches)


# ---------------------------------------------------------------------------
# Clipboard save/restore for the final message paste (restored only after send)
# ---------------------------------------------------------------------------


def _save_clipboard_text() -> Optional[str]:
    if win32clipboard is None or win32con is None:
        return None
    try:
        win32clipboard.OpenClipboard()
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return data if isinstance(data, str) else str(data)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return None


def _restore_clipboard_text(text: Optional[str]) -> None:
    if text is None or win32clipboard is None or win32con is None:
        return
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


# ---------------------------------------------------------------------------
# Contact targeting: config.whatsapp_contacts_file (name -> +E.164), fuzzy-matched (90+)
# ---------------------------------------------------------------------------


def _load_contacts_map() -> dict[str, str]:
    path = getattr(config, "whatsapp_contacts_file", None)
    if not path:
        return {}
    try:
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("Could not read whatsapp_contacts_file '%s': %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(k, str)}


def _match_contact_number(contact_name: str) -> Optional[str]:
    """A +E.164 number for contact_name from the contacts file (fuzzy, 90+), else None."""
    contacts = _load_contacts_map()
    if not contacts or not contact_name:
        return None
    try:
        from fuzzywuzzy import fuzz, process  # pylint: disable=import-outside-toplevel
    except ImportError:  # pragma: no cover
        return contacts.get(contact_name)
    match = process.extractOne(
        contact_name,
        list(contacts.keys()),
        scorer=fuzz.WRatio,
        score_cutoff=_FUZZY_MATCH_THRESHOLD,
    )
    if not match:
        return None
    return contacts.get(match[0])


def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _build_deep_link(phone_e164: str, message: str) -> str:
    digits = _digits_only(phone_e164)
    text = urllib.parse.quote(message or "", safe="")
    return f"whatsapp://send?phone={digits}&text={text}"


# ---------------------------------------------------------------------------
# WhatsAppAutomation
# ---------------------------------------------------------------------------


class WhatsAppAutomation:
    """
    Automates interactions with WhatsApp Desktop.
    open_whatsapp / ensure_whatsapp_focus / close_whatsapp keep their names and behaviour for
    services.system.jarvis_window_ctrl's open_app('whatsapp') / close('whatsapp') routing.
    """

    def __init__(self) -> None:
        self.last_error: Optional[str] = None

    @staticmethod
    async def _wait_for_whatsapp_window(timeout_s: float = 15.0) -> Optional[WindowInfo]:
        """Polls _find_whatsapp_windows() every 0.25 s until a match appears or timeout_s elapses."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max(0.0, timeout_s)
        while True:
            matches = _find_whatsapp_windows()
            if matches:
                return matches[0]
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(0.25)

    async def _is_focused(self) -> bool:
        return await asyncio.to_thread(_is_whatsapp_foreground)

    async def open_whatsapp(self) -> bool:
        """
        Launches WhatsApp Desktop via its Store app id (shell:AppsFolder), waits up to 15 s for its
        window (process-based match) and brings it to the foreground. False (with self.last_error
        set) when it is not installed, fails to launch, or no window appears in time.
        """
        if not is_whatsapp_installed():
            self.last_error = "WHATSAPP_NOT_INSTALLED"
            return False

        app_id = getattr(config, "whatsapp_app_id", None) or _DEFAULT_WHATSAPP_APP_ID
        uri = f"shell:AppsFolder\\{app_id}"
        try:
            await asyncio.to_thread(os.startfile, uri)  # pylint: disable=no-member
        except OSError as exc:
            logger.error("Failed to launch WhatsApp via %s: %s", uri, exc)
            self.last_error = "WHATSAPP_LAUNCH_FAILED"
            return False

        win = await self._wait_for_whatsapp_window(timeout_s=15.0)
        if win is None:
            logger.error("Timed out waiting for the WhatsApp window.")
            self.last_error = "WHATSAPP_WINDOW_TIMEOUT"
            return False

        await force_foreground(win.hwnd)
        return True

    async def ensure_whatsapp_focus(self, timeout: float = 15.0) -> bool:
        """Backward-compat helper: waits for and foregrounds the WhatsApp window."""
        win = await self._wait_for_whatsapp_window(timeout_s=timeout)
        if win is None:
            return False
        return await force_foreground(win.hwnd)

    async def close_whatsapp(self) -> bool:
        """WM_CLOSE only, to _find_whatsapp_windows() hwnds; never Alt+F4, never a browser window."""
        matches = _find_whatsapp_windows()
        if not matches:
            return True
        result = await close_windows_gracefully([w.hwnd for w in matches], wait_s=2.0)
        return not result.get("still_open")

    async def _send_via_deep_link(self, phone: str, message: str) -> dict[str, Any]:
        """Known contact: whatsapp://send?phone=<digits>&text=<urlencoded>, then Enter to send."""
        url = _build_deep_link(phone, message)
        try:
            await asyncio.to_thread(os.startfile, url)  # pylint: disable=no-member
        except OSError as exc:
            logger.error("WhatsApp deep link open failed: %s", exc)
            return tool_result(
                "error", f"WhatsApp deep link open nahi hua: {exc}", error_code="WHATSAPP_OPEN_FAILED"
            )

        win = await self._wait_for_whatsapp_window(timeout_s=15.0)
        if win is not None:
            await force_foreground(win.hwnd)
        await asyncio.sleep(1.0)

        if not await self._is_focused():
            return tool_result(
                "error",
                "WhatsApp window focus mein nahi hai; message nahi bheja gaya.",
                error_code="FOCUS_LOST",
            )

        _pg_mod().press("enter")
        await asyncio.sleep(0.5)
        return tool_result("success", "sent")

    async def _send_via_search(self, contact_name: str, message: str) -> dict[str, Any]:
        """No number on file: Ctrl+F, paste the contact name, Down, Enter, paste the message, Enter."""
        focus_lost = tool_result(
            "error",
            "WhatsApp window focus mein nahi hai; message nahi bheja gaya.",
            error_code="FOCUS_LOST",
        )

        if not await self._is_focused():
            return focus_lost

        pg = _pg_mod()
        pg.hotkey("ctrl", "f")
        await asyncio.sleep(0.5)
        await clipboard_paste_text(contact_name, restore=True)
        await asyncio.sleep(2.0)
        pg.press("down")
        await asyncio.sleep(0.3)

        if not await self._is_focused():
            return focus_lost
        pg.press("enter")  # open the selected chat
        await asyncio.sleep(1.0)

        if not await self._is_focused():
            return focus_lost

        saved_clip = await asyncio.to_thread(_save_clipboard_text)
        await clipboard_paste_text(message, restore=False)
        await asyncio.sleep(0.3)

        if not await self._is_focused():
            await asyncio.to_thread(_restore_clipboard_text, saved_clip)
            return focus_lost

        pg.press("enter")  # send
        await asyncio.sleep(0.5)
        await asyncio.to_thread(_restore_clipboard_text, saved_clip)  # restored only after the send
        return tool_result("success", "sent")

    async def send_message(self, contact_name: str, message: str) -> dict[str, Any]:
        """Deep link when contact_name resolves to a known number (90+ fuzzy match), else search."""
        number = _match_contact_number(contact_name)
        if number:
            return await self._send_via_deep_link(number, message)
        return await self._send_via_search(contact_name, message)


# Global Instance
whatsapp_bot = WhatsAppAutomation()


def _whatsapp_confirm_summary(arguments: dict[str, Any]) -> str:
    contact = str(arguments.get("contact_name") or "contact")
    message = str(arguments.get("message") or "")
    return f"WhatsApp par {contact} ko message bhejna hai: {message[:120]}"


@jarvis_tool(
    timeout=60,
    risk="medium",
    raw_args=("message",),
    exclusive="desktop_input",
    retry_attempts=0,
    confirm_summary=_whatsapp_confirm_summary,
)
async def automate_whatsapp(contact_name: str, message: str, close_after: bool = True) -> dict[str, Any]:
    """
    Sir, is tool se main WhatsApp Desktop par contact_name ko message bhejta hoon (install check,
    process-based focus — browser kabhi nahi, Unicode/emoji safe, FOCUS_LOST par kuch nahi bhejta).
    """
    if not desktop_control_enabled():
        return tool_result(
            "error",
            "Desktop control band hai; WhatsApp automation abhi use nahi ho sakta.",
            error_code="DESKTOP_CONTROL_DISABLED",
        )

    if not is_whatsapp_installed():
        try:
            jarvis_delivery.open_url("https://web.whatsapp.com")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Could not open the web.whatsapp.com fallback: %s", exc)
        return tool_result(
            "error",
            "Sir, WhatsApp Desktop install nahi hai. Microsoft Store se install karke phone se link "
            "kar dein; tab tak web.whatsapp.com khol raha hoon.",
            error_code="WHATSAPP_NOT_INSTALLED",
        )

    try:
        opened = await whatsapp_bot.open_whatsapp()
        if not opened:
            return tool_result(
                "error",
                "Maazrat Sir, WhatsApp Desktop open ya focus nahi ho paaya.",
                error_code="WHATSAPP_OPEN_FAILED",
            )

        result = await whatsapp_bot.send_message(contact_name, message)
        if result.get("status") != "success":
            return result

        if close_after:
            await whatsapp_bot.close_whatsapp()
            msg = f"Message '{contact_name}' ko bhej diya aur WhatsApp band kar diya."
        else:
            msg = f"Message '{contact_name}' ko bhej diya."
        return tool_result("success", msg, data={"contact": contact_name})

    except (OSError, AttributeError, RuntimeError) as exc:  # pylint: disable=broad-exception-caught
        logger.exception("WhatsApp automation error: %s", exc)
        return tool_result("error", f"WhatsApp automation mein masla aaya: {exc}")
