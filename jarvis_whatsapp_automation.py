"""
Jarvis WhatsApp Automation Module
Handles sending messages and automating WhatsApp Desktop.
"""
import time
import subprocess
import asyncio
import logging
import pyautogui
import pyperclip
from livekit.agents import function_tool

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-WHATSAPP")

# Configure pyautogui
pyautogui.FAILSAFE = True

# Try to import pygetwindow for window focus control
try:
    import pygetwindow as gw
except ImportError:
    gw = None
    logger.warning(
        "pygetwindow not installed. Window focus verification will be limited.")

# WhatsApp App URI (Store App)
WHATSAPP_URI = r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"


class WhatsAppAutomation:
    def __init__(self):
        pass

    async def ensure_whatsapp_focus(self, timeout: int = 10):
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
                    await asyncio.sleep(0.5)
                    continue

                # Get the first match
                whatsapp_win = valid_windows[0]

                if whatsapp_win.isMinimized:
                    whatsapp_win.restore()

                # Try to activate
                try:
                    whatsapp_win.activate()
                except Exception:  # pylint: disable=broad-exception-caught
                    # sometimes activate fails if another window is aggressively on top
                    pass

                await asyncio.sleep(0.2)

                if whatsapp_win.isActive:
                    logger.info("WhatsApp is active and focused.")
                    focused = True
                    break

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error attempting to focus WhatsApp: %s", e)

            await asyncio.sleep(0.5)

        if not focused:
            logger.error("Timed out waiting for WhatsApp focus.")
        return focused

    async def open_whatsapp(self):
        """Opens WhatsApp Desktop application"""
        try:
            logger.info("Opening WhatsApp...")
            # Use subprocess to start the store app
            # pylint: disable=consider-using-with
            subprocess.Popen('start whatsapp://', shell=True)
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to open WhatsApp: %s", e)
            return False

    async def search_and_select_contact(self, contact_name: str):
        """Searches for a contact and selects it"""
        try:
            logger.info("Searching for contact: %s", contact_name)

            # Focus Search Bar (Ctrl + F is standard)
            # Sometimes WhatsApp needs a moment to catch input after focus
            await asyncio.sleep(1.0)
            pyautogui.hotkey('ctrl', 'f')
            await asyncio.sleep(1.0)

            # Clear previous search if any (Ctrl+A, Backspace)
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.press('backspace')
            await asyncio.sleep(0.5)

            # Type name
            pyautogui.write(contact_name, interval=0.1)
            # Wait for search results (Increased significantly)
            await asyncio.sleep(3.0)

            # Select first result
            # Sometimes 'down' doesn't land correctly on first result if there's focus delay
            pyautogui.press('down')
            await asyncio.sleep(0.8)
            pyautogui.press('enter')
            await asyncio.sleep(3.0)  # Wait for chat to open

            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error searching contact: %s", e)
            return False

    async def send_text_message(self, message: str):
        """Types and sends a message"""
        try:
            logger.info("Sending message: %s", message)
            if not message:
                return False

            # Type message
            # Handling newlines by splitting or just raw write?
            # pyautogui.write handles \n as enter usually, but let's be safe.
            # WhatsApp sends on Enter by default.

            # Use clipboard copy-paste for Unicode support
            pyperclip.copy(message)
            await asyncio.sleep(0.5)
            pyautogui.hotkey('ctrl', 'v')
            await asyncio.sleep(1.0)  # Wait for paste

            pyautogui.press('enter')  # Send
            logger.info("Message sent.")
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error sending message: %s", e)
            return False

    async def close_whatsapp(self):
        """Closes the active WhatsApp window"""
        try:
            logger.info("Closing WhatsApp...")
            pyautogui.hotkey('alt', 'f4')
            await asyncio.sleep(0.5)
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error closing WhatsApp: %s", e)
            return False


# Global Instance
whatsapp_bot = WhatsAppAutomation()


@function_tool
async def automate_whatsapp(contact_name: str, message: str, close_after: bool = True) -> str:
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
            return "❌ Failed to open or focus WhatsApp. Is it installed?"

        # 3. Search Contact
        await whatsapp_bot.search_and_select_contact(contact_name)

        # 4. Send Message
        await whatsapp_bot.send_text_message(message)

        # 5. Close
        if close_after:
            # wait a bit before closing to ensure send
            await asyncio.sleep(3.0)
            await whatsapp_bot.close_whatsapp()
            return f"✅ Message sent to '{contact_name}' and WhatsApp closed."
        return f"✅ Message sent to '{contact_name}'. WhatsApp left open."

    except Exception as e:  # pylint: disable=broad-exception-caught
        return f"❌ Error in WhatsApp automation: {str(e)}"
