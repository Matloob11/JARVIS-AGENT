"""
Jarvis WhatsApp Automation Module
Handles sending messages and automating WhatsApp Desktop.
"""
import os
import time
import asyncio
import pyautogui as pg
import pygetwindow as gw
from livekit.agents import function_tool
from keyboard_mouse_ctrl import type_text_tool
try:
    import win32gui
    import win32con
    import pywintypes
except ImportError:
    win32gui = None
    win32con = None
    pywintypes = None
from jarvis_logger import setup_logger

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
                # Find windows with "WhatsApp" in title
                windows = gw.getWindowsWithTitle('WhatsApp')

                # Filter strictly for windows that likely represent the app
                valid_windows = [
                    w for w in windows if "WhatsApp" in w.title
                ]

                if not valid_windows:
                    await asyncio.sleep(1.0)
                    continue

                # Get the first match
                whatsapp_win = valid_windows[0]

                if whatsapp_win.isMinimized:
                    whatsapp_win.restore()

                # Explicitly try to bring to front using Win32 if available
                if win32gui:
                    try:
                        # pylint: disable=protected-access
                        win32gui.ShowWindow(
                            whatsapp_win._hwnd, win32con.SW_RESTORE)
                        win32gui.SetForegroundWindow(whatsapp_win._hwnd)
                    except (pywintypes.error, AttributeError) as e:  # pylint: disable=no-member
                        logger.debug("Minor Win32 focus error: %s", e)

                # Try to activate
                try:
                    whatsapp_win.activate()
                except (pywintypes.error, AttributeError) as e:  # pylint: disable=no-member
                    logger.debug("Minor pygetwindow activate error: %s", e)

                # Polling for active state
                poll_start = time.time()
                while time.time() - poll_start < 3.0:  # Poll for max 3 seconds
                    if whatsapp_win.isActive:
                        logger.info("WhatsApp is active and focused.")
                        focused = True
                        break
                    await asyncio.sleep(0.1)

                if focused:
                    break

            except (OSError, AttributeError, RuntimeError, pywintypes.error) as e:  # pylint: disable=no-member
                logger.error("Error attempting to focus WhatsApp: %s", e)

            await asyncio.sleep(0.5)

        if not focused:
            logger.error("Timed out waiting for WhatsApp focus.")
        return focused

    async def open_whatsapp(self):
        """Opens WhatsApp Desktop application using the Store URI"""
        try:
            logger.info("Opening WhatsApp via URI: %s", WHATSAPP_URI)
            os.startfile(WHATSAPP_URI)  # nosec B606
            await asyncio.sleep(3.0)  # Initial wait for launch
            return True
        except (OSError, ValueError) as e:
            logger.error("Failed to open WhatsApp: %s", e)
            return False

    async def search_and_select_contact(self, contact_name: str):
        """Searches for a contact and selects it"""
        try:
            logger.info("Searching for contact: %s", contact_name)

            # Focus Search Bar (Ctrl + F is standard)
            await asyncio.sleep(2.0)
            pg.hotkey('ctrl', 'f')
            await asyncio.sleep(1.0)

            # Clear previous search
            pg.hotkey('ctrl', 'a')
            pg.press('backspace')
            await asyncio.sleep(0.5)

            # Type name slowly
            for char in contact_name:
                pg.write(char)
                await asyncio.sleep(0.05)

            # Polling for search results (Max 7 seconds)
            logger.info("Waiting for search results...")
            await asyncio.sleep(4.0)

            # Select result
            pg.press('down')
            await asyncio.sleep(0.5)
            pg.press('enter')

            # Wait for chat UI to load
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

            # Use the robust tool from keyboard_mouse_ctrl
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


@function_tool
async def automate_whatsapp(contact_name: str, message: str, close_after: bool = True) -> dict:
    """
    Automates WhatsApp Desktop to send a message.

    1. Opens WhatsApp
    2. Searches for the contact
    3. Types and sends the message
    4. Optionally closes WhatsApp

    Args:
        contact_name: The exact name of the contact as saved in phone.
        message: The message to send.
        close_after: Whether to close WhatsApp after sending (Default: True).
    """
    try:
        # 1. Open
        await whatsapp_bot.open_whatsapp()

        # 2. Wait for Focus (Give it time to load if cold start)
        is_focused = await whatsapp_bot.ensure_whatsapp_focus(timeout=15)
        if not is_focused:
            return {
                "status": "error",
                "message": "❌ Maazrat Sir, WhatsApp open ya focus nahi ho paaya. Kya ye installed hai?"
            }

        # 3. Search Contact
        await whatsapp_bot.search_and_select_contact(contact_name)

        # 4. Send Message
        await whatsapp_bot.send_text_message(message)

        # 5. Close
        if close_after:
            # wait a bit before closing to ensure send
            await asyncio.sleep(3.0)
            await whatsapp_bot.close_whatsapp()
            return {
                "status": "success",
                "contact": contact_name,
                "message": f"✅ Message sent to '{contact_name}' and WhatsApp closed."
            }
        return {
            "status": "success",
            "contact": contact_name,
            "message": f"✅ Message sent to '{contact_name}'. WhatsApp left open."
        }

    except (OSError, AttributeError, RuntimeError) as e:  # pylint: disable=broad-exception-caught
        logger.exception("WhatsApp automation error: %s", e)
        return {
            "status": "error",
            "message": f"❌ WhatsApp automation mein masla aaya: {str(e)}",
            "error": str(e)
        }
