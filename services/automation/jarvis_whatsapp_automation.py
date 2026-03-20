"""
Jarvis WhatsApp Automation Module
Handles sending messages and automating WhatsApp Desktop.
"""
import os
import time
import asyncio
import pyautogui as pg
import pygetwindow as gw
from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.automation.keyboard_mouse_ctrl import type_text_tool
from services.utils.jarvis_win32 import win32gui, win32con, pywintypes
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-WHATSAPP")

# WhatsApp App URI (Store App)
WHATSAPP_URI = r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"


class WhatsAppAutomation:
    """
    Automates interactions with the WhatsApp Desktop application.
    """

    def __init__(self):
        """Initialize the WhatsApp automation controller."""

    async def ensure_whatsapp_focus(self, timeout: int = 15):
        """
        Waits for WhatsApp to verify it is the active window.
        Returns True if WhatsApp is focused, False otherwise.
        """
        if gw is None:
            await asyncio.sleep(3)  # Fallback wait
            return True

        logger.info("Waiting for WhatsApp to appear and gain focus...")
        start_time = time.time()
        focused = False

        while time.time() - start_time < timeout:
            try:
                whatsapp_win = self._get_whatsapp_window()
                if not whatsapp_win:
                    await asyncio.sleep(1.0)
                    continue

                if whatsapp_win.isMinimized:
                    whatsapp_win.restore()

                self._apply_win32_focus(whatsapp_win)

                # Try to activate
                try:
                    whatsapp_win.activate()
                except (pywintypes.error, AttributeError) as e: # pylint: disable=no-member
                    logger.debug("Minor pygetwindow activate error: %s", e)

                await asyncio.sleep(1.0)

                if await self._poll_activation(whatsapp_win):
                    focused = True
                    break

            except (OSError, AttributeError, RuntimeError) as e:
                logger.error("Error attempting to focus WhatsApp: %s", e)

            await asyncio.sleep(0.5)

        if not focused:
            logger.error("Timed out waiting for WhatsApp focus.")
        return focused

    def _get_whatsapp_window(self):
        """Helper to find WhatsApp window."""
        windows = gw.getWindowsWithTitle('WhatsApp')
        valid_windows = [w for w in windows if "WhatsApp" in w.title]
        return valid_windows[0] if valid_windows else None

    def _apply_win32_focus(self, whatsapp_win):
        """Helper to apply Win32 focus."""
        if win32gui is not None:
            try:
                pg.press('alt')
                hwnd = getattr(whatsapp_win, '_hwnd', None)
                if hwnd:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
            except (pywintypes.error, AttributeError) as e: # pylint: disable=no-member
                logger.debug("Minor Win32 focus error: %s", e)

    async def _poll_activation(self, whatsapp_win):
        """Polls for activation state."""
        poll_start = time.time()
        while time.time() - poll_start < 5.0:
            if whatsapp_win.isActive:
                return True
            if win32gui is not None:
                try:
                    hwnd = getattr(whatsapp_win, '_hwnd', None)
                    if hwnd:
                        win32gui.SetForegroundWindow(hwnd)
                except (pywintypes.error, AttributeError): # pylint: disable=no-member
                    pass
            await asyncio.sleep(0.5)
        return False

    async def open_whatsapp(self):
        """Opens WhatsApp Desktop application using the Store URI"""
        try:
            # Check if WhatsApp Store App exists (rudimentary check via path possibility)
            # This is a bit tricky for 'shell:AppsFolder', so we try-except the startfile
            logger.info("Opening WhatsApp via URI: %s", WHATSAPP_URI)
            os.startfile(WHATSAPP_URI)  # nosec B606
            await asyncio.sleep(3.0)  # Initial wait for launch
            return True
        except (OSError, ValueError) as e:
            logger.error("Failed to open WhatsApp (Is it installed?): %s", e)
            return False

    async def search_and_select_contact(self, contact_name: str):
        """Searches for a contact and selects it"""
        try:
            logger.info("Searching for contact: %s", contact_name)

            # Open search bar once, clear it, then type
            pg.hotkey('ctrl', 'f')
            await asyncio.sleep(1.0)
            pg.hotkey('ctrl', 'a')
            pg.press('backspace')
            await asyncio.sleep(0.3)

            logger.info("Typing contact name...")
            for char in contact_name:
                pg.write(char)
                await asyncio.sleep(0.1)

            logger.info("Waiting for search results...")
            await asyncio.sleep(3.0)

            pg.press('down')
            await asyncio.sleep(0.5)
            pg.press('enter')
            await asyncio.sleep(1.5)
            return True
        except (pg.FailSafeException, AttributeError, OSError) as e:
            logger.error("Error searching contact: %s", e)
            return False

    async def send_text_message(self, message: str):
        """Types and sends a message using the standardized type_text_tool."""
        try:
            if not message:
                return False
            logger.info("Sending message via type_text_tool: %s",
                        message[:20] + "...")

            await type_text_tool(message)
            await asyncio.sleep(0.8)
            pg.press('enter')
            logger.info("Message sent.")
            return True
        except (pg.FailSafeException, AttributeError, OSError, ImportError) as e:
            logger.error("Error sending message: %s", e)
            return False

    async def close_whatsapp(self):
        """Closes the active WhatsApp window"""
        try:
            logger.info("Closing WhatsApp...")
            pg.hotkey('alt', 'f4')
            await asyncio.sleep(0.5)
            return True
        except (OSError, AttributeError) as e:
            logger.error("Error closing WhatsApp: %s", e)
            return False


# Global Instance
whatsapp_bot = WhatsAppAutomation()


@jarvis_tool
async def automate_whatsapp(contact_name: str, message: str, close_after: bool = True) -> dict:
    """
    Automates WhatsApp Desktop to send a message.
    """
    try:
        await whatsapp_bot.open_whatsapp()

        is_focused = await whatsapp_bot.ensure_whatsapp_focus(timeout=15)
        if not is_focused:
            msg = "Maazrat Sir, WhatsApp open ya focus nahi ho paaya. Kya ye installed hai?"
            return {"status": "error", "message": msg}

        await whatsapp_bot.search_and_select_contact(contact_name)
        await whatsapp_bot.send_text_message(message)

        if close_after:
            await asyncio.sleep(3.0)
            await whatsapp_bot.close_whatsapp()
            msg = f"Message sent to '{contact_name}' and WhatsApp closed."
            return {"status": "success", "contact": contact_name, "message": msg}

        msg = f"Message sent to '{contact_name}'. WhatsApp left open."
        return {"status": "success", "contact": contact_name, "message": msg}

    except (OSError, AttributeError, RuntimeError) as e: # pylint: disable=broad-exception-caught
        logger.exception("WhatsApp automation error: %s", e)
        return {
            "status": "error",
            "message": f"WhatsApp automation mein masla aaya: {str(e)}",
            "error": str(e)
        }
