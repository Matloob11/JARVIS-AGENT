"""
# Jarvis_window_CTRL.py
Jarvis Window Control Module

Handles opening, closing, and managing windows (minimize, maximize, restore).
Also provides system-level controls like shutdown, restart, and sleep.
"""
# pylint: disable=too-many-lines

from jarvis_system_ctrl import (
    shutdown_system, restart_system, sleep_system, lock_screen
)
import asyncio
import os
import re
import subprocess
import sys
import shlex

# Third-party imports
from fuzzywuzzy import process
import pygetwindow as gw
import pywintypes
import win32gui
import win32con
import pyperclip
import pyautogui as pg

# First-party imports
from jarvis_config import APP_MAPPINGS, FOCUS_TITLES
from jarvis_whatsapp_automation import whatsapp_bot
from keyboard_mouse_ctrl import type_text_tool
from jarvis_logger import setup_logger

try:
    from livekit.agents import function_tool
except ImportError:
    def function_tool(func):
        """
        Placeholder function_tool decorator if livekit is not installed.
        """
        return func

# ===================== LOGGER ===================== #
sys.stdout.reconfigure(encoding="utf-8")
logger = setup_logger("JARVIS-WINDOW")

get_windows = gw.getWindowsWithTitle


# ===================== UTIL ===================== #


def normalize_command(text: str) -> str:
    """
    Removes Hindi/English open keywords safely and extracts the app name.
    """
    remove_words = [
        "open", "opening", "opened", "run", "launch", "start",
        "kholo", "khol", "karo", "chalao", "chalana",
        "aur", "usmein", "likh", "do", "hello", "jarvis", "what", "are", "you", "doing",
        "browser", "app", "application", "please", "zara"
    ]
    text = text.lower()

    # Use regex with word boundaries to avoid partial word matches (e.g., 'what' in 'whatsapp')
    pattern = r'\b(' + '|'.join(map(re.escape, remove_words)) + r')\b'
    # Replace with space instead of empty to keep words separate
    text = re.sub(pattern, " ", text)

    return " ".join(text.split()).strip()


async def focus_window(target_title: str):
    """
    Activates and restores a window by its title.
    Uses Win32 API for more aggressive focus if needed.
    """
    if not gw:
        return False

    # Wait for app to settle
    await asyncio.sleep(1.2)

    target_lower = target_title.lower()
    for w in gw.getAllWindows():
        title_lower = w.title.lower()
        # Specific check for Notepad to be more accurate
        if target_lower == "notepad":
            is_match = title_lower.endswith(
                " - notepad") or title_lower == "notepad"
        else:
            is_match = target_lower in title_lower

        if is_match:
            try:
                if w.isMinimized:
                    w.restore()

                # Try pygetwindow activation
                w.activate()

                # Aggressive Win32 focus
                hwnd = w._hWnd  # pylint: disable=protected-access
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)

                await asyncio.sleep(0.5)
                logger.info("Focused window: %s", w.title)
                return True
            except (pywintypes.error, AttributeError) as e:  # pylint: disable=no-member
                logger.warning("Focus failed for %s: %s", target_title, e)
                continue
    return False


def fuzzy_match_app(app_name: str) -> str:
    """
    Performs fuzzy matching to find the closest app name in the mapping.
    """
    keys = list(APP_MAPPINGS.keys())
    match, score = process.extractOne(app_name, keys)
    if score >= 70:
        return match
    return app_name

# ===================== OPEN APP ===================== #


@function_tool
async def open_app(full_command: str) -> dict:  # pylint: disable=too-many-branches
    """
    Opens a desktop application or URL based on the user's voice command.
    Uses fuzzy matching to identify the best application from the APP_MAPPINGS list.
    """
    try:
        clean = normalize_command(full_command)
        matched_key = fuzzy_match_app(clean)
        app = APP_MAPPINGS.get(matched_key, matched_key)

        logger.info(
            "OPEN → raw='%s' clean='%s' match='%s'", full_command, clean, matched_key)

        # 🌐 URL → browser or desktop app
        if app.startswith("http"):
            # Use os.startfile for better handling of browser launches
            os.startfile(app)  # nosec B606
            await asyncio.sleep(3)  # Wait for browser to open
            if "youtube" in matched_key.lower():
                await focus_window("YouTube")
            elif "whatsapp" in matched_key.lower():
                await focus_window("WhatsApp")
        elif matched_key == "whatsapp":
            await whatsapp_bot.open_whatsapp()
            await whatsapp_bot.ensure_whatsapp_focus()
        elif app.startswith("whatsapp://"):
            if app:
                os.startfile(app)  # nosec B606
        else:
            # 🖥️ Generic Desktop App launch
            try:
                os.startfile(app)  # nosec B606
            except OSError as e:
                # Fallback to subprocess if startfile fails
                cmd_list = shlex.split(app)
                subprocess.Popen(cmd_list)  # nosec B603
                await asyncio.sleep(2)
                logger.warning(
                    "startfile failed, tried subprocess fallback safely: %s", e)

        # ✍️ Parse writing action
        write_text = None
        low_cmd = full_command.lower()

        # Priority parsing for "write" and "likh"
        if " write " in f" {low_cmd} ":
            write_text = low_cmd.split(" write ", 1)[1].strip()
        elif " likh " in f" {low_cmd} ":
            write_text = low_cmd.split(" likh ", 1)[1].strip()
            if write_text.startswith("do "):
                write_text = write_text[3:].strip()

        # Further clean write_text to remove trailing "kholo" etc if they were misplaced at the end
        if write_text:
            remove_trail = ["kholo", "karo", "please", "zara"]
            for w in remove_trail:
                if write_text.endswith(f" {w}"):
                    write_text = write_text[:-len(w)].strip()

        if write_text:
            return await _handle_write_after_open(matched_key, write_text)

        return {
            "status": "success",
            "app": matched_key,
            "message": f"🚀 {matched_key} khol diya gaya hai"
        }

    except (OSError, pywintypes.error, ValueError) as e:  # pylint: disable=no-member
        logger.error("App open error: %s", e)
        return {
            "status": "error",
            "message": f"❌ App open nahi ho paaya: {str(e)}",
            "error": str(e)
        }


async def _handle_write_after_open(matched_key: str, write_text: str) -> dict:
    """
    Helper to handle typing text after opening an app.
    Wait for the app to initialize before sending keystrokes.
    """
    await asyncio.sleep(2)  # Wait for app to open
    # Re-focus target app before typing
    await focus_window(matched_key)
    result = await type_text_tool(write_text)
    return {
        "status": "success",
        "app": matched_key,
        "message": f"🚀 {matched_key} khol diya gaya hai aur {result}"
    }


@function_tool
async def save_notepad(file_path: str = r"D:\jarvis_notes.txt") -> dict:
    """
    Saves the content of an open Notepad window.
    """
    try:
        # Try to find Notepad window
        notepad_windows = list(get_windows('Notepad'))
        if not notepad_windows:
            return {
                "status": "error",
                "message": "❌ Notepad window nahi mili."
            }

        notepad = notepad_windows[0]
        notepad.activate()
        await asyncio.sleep(1)

        # Ctrl+S
        pg.hotkey('ctrl', 's')
        await asyncio.sleep(1.5)  # Wait for Save As dialog

        # Type path using clipboard (safer than pg.write for long/special paths)
        pyperclip.copy(file_path)
        await asyncio.sleep(0.5)
        pg.hotkey('ctrl', 'v')
        await asyncio.sleep(0.5)
        pg.press('enter')

        # Overwrite check - ONLY if file exists
        if os.path.exists(file_path):
            await asyncio.sleep(1.2)
            # Find the "Confirm Save As" sub-window if possible, or just press Left/Enter as fallback
            pg.press('left')
            pg.press('enter')
        else:
            await asyncio.sleep(0.5)

        return {
            "status": "success",
            "file_path": file_path,
            "message": f"💾 Notepad file ko '{file_path}' par save kar diya gaya hai."
        }

    except (OSError, pywintypes.error, ValueError, AttributeError) as e:  # pylint: disable=no-member
        logger.error("Notepad save error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Notepad save karne mein error: {str(e)}",
            "error": str(e)
        }


@function_tool
async def open_notepad_file(file_path: str) -> str:
    """
    Opens a specific text file in Notepad.
    """
    if not os.path.exists(file_path):
        return f"❌ File nahi mili: {file_path}"

    try:
        # pylint: disable=consider-using-with
        subprocess.Popen(
            [r"C:\Windows\System32\notepad.exe", file_path])
        return {
            "status": "success",
            "file_path": file_path,
            "message": f"📂 {file_path} ko Notepad mein open kar diya gaya hai."
        }
    except OSError as e:
        logger.error("open_notepad_file error: %s", e)
        return {
            "status": "error",
            "message": f"❌ File open karne mein error: {str(e)}",
            "error": str(e)
        }

# ===================== CLOSE WINDOW ===================== #


@function_tool
async def close(window_name: str) -> str:
    """
    Closes a window by its name.
    """
    if not win32gui:
        return "❌ win32gui available nahi hai"

    original_name = window_name
    window_name = window_name.lower()

    if "whatsapp" in window_name:
        if await whatsapp_bot.close_whatsapp():
            return "🗑️ WhatsApp band kar diya gaya hai"

    if "notepad" in window_name:
        try:
            subprocess.run([r"C:\Windows\System32\taskkill.exe", "/f", "/im", "notepad.exe"],
                           check=False, capture_output=True)
            return "🗑️ Notepad force close kar diya gaya hai (Unsaved changes lost)."
        except (subprocess.SubprocessError, OSError) as e:
            logger.error("Error closing notepad: %s", e)
            return f"❌ Notepad band karne mein error aaya: {e}"

    # Fuzzy matching for common apps
    matched_key = fuzzy_match_app(window_name)
    search_title = FOCUS_TITLES.get(matched_key, window_name).lower()

    logger.info(
        "CLOSE → target='%s' search_title='%s'", original_name, search_title)

    hwnds_to_close = []

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            # Specific logic for common apps to avoid broad matches
            app_matchers = {
                "notepad": lambda t: t.endswith(" - notepad") or t == "notepad",
                "edge": lambda t: "edge" in t or "msedge" in t,
                "chrome": lambda t: "google chrome" in t
            }

            matcher = app_matchers.get(matched_key)
            if matcher:
                is_match = matcher(title)
            else:
                is_match = search_title in title

            # Safeguard: Don't close the current script or the IDE
            if is_match:
                if ".py" in title and search_title == "notepad":
                    is_match = False

                if is_match:
                    hwnds_to_close.append(hwnd)

    win32gui.EnumWindows(enum_handler, None)

    if not hwnds_to_close:
        return {
            "status": "error",
            "message": f"❌ '{original_name}' naam ki koi window nahi mili."
        }

    # Send close messages
    for hwnd in hwnds_to_close:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    # Verification loop
    await asyncio.sleep(2)

    still_open_count = 0

    def verify_handler(hwnd, _):
        nonlocal still_open_count
        if hwnd in hwnds_to_close and win32gui.IsWindow(hwnd):
            still_open_count += 1

    win32gui.EnumWindows(verify_handler, None)

    if not still_open_count:
        return {
            "status": "success",
            "window": original_name,
            "message": f"🗑️ {original_name} band kar diya gaya hai aur maine verify kar liya hai."
        }

    return {
        "status": "warning",
        "window": original_name,
        "still_open_count": still_open_count,
        "message": (f"⚠ {original_name} ko band karne ki command bhej di gayi hai, "
                    f"lekin {still_open_count} window(s) abhi bhi open lag rahi hain.")
    }


@function_tool
async def minimize_window(window_name: str = "active") -> str:
    """
    Minimizes a window. If window_name is 'active', it minimizes the currently focused window.
    """
    if not gw:
        return "❌ pygetwindow available nahi hai"

    try:
        if window_name.lower() == "active":
            window = gw.getActiveWindow()
            if window:
                window.minimize()
                return "📉 Active window minimize kar di gayi hai, Sir."
            return "❌ Koi active window nahi mili."

        # Search for window by name
        windows = gw.getWindowsWithTitle(window_name)
        if windows:
            windows[0].minimize()
            return f"📉 '{window_name}' minimize kar di gayi hai, Sir."
        return f"❌ '{window_name}' naam ki koi window nahi mili."
    except (pywintypes.error, AttributeError) as e:  # pylint: disable=no-member
        logger.error("Minimize Error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Window minimize nahi ho paayi: {str(e)}",
            "error": str(e)
        }


@function_tool
async def maximize_window(window_name: str = "active") -> str:
    """
    Maximizes or restores a window.
    If window_name is 'active', it maximizes the currently focused window.
    """
    if not gw:
        return "❌ pygetwindow available nahi hai"

    try:
        if window_name.lower() == "active":
            window = gw.getActiveWindow()
            if window:
                window.maximize()
                return "📈 Active window maximize kar di gayi hai, Sir."
            return "❌ Koi active window nahi mili."

        # Search for window by name
        windows = gw.getWindowsWithTitle(window_name)
        if windows:
            windows[0].maximize()
            return f"📈 '{window_name}' maximize kar di gayi hai, Sir."
        return f"❌ '{window_name}' naam ki koi window nahi mili."
    except (pywintypes.error, AttributeError) as e:  # pylint: disable=no-member
        logger.error("Maximize Error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Window maximize nahi ho paayi: {str(e)}",
            "error": str(e)
        }


@function_tool
async def folder_file(path: str) -> dict:
    """
    Opens a specified folder or file on the system.
    """
    try:
        if not path:
            return {"status": "error", "message": "❌ Path empty hai."}

        # Resolve path
        abs_path = os.path.abspath(path)
        if not await asyncio.to_thread(os.path.exists, abs_path):
            return {"status": "error", "message": f"❌ Path nahi mila: {path}"}

        # Open via startfile in thread
        await asyncio.to_thread(os.startfile, abs_path)
        return {
            "status": "success",
            "path": abs_path,
            "message": f"📂 '{os.path.basename(abs_path)}' ko open kar diya gaya hai."
        }
    except (OSError, ValueError, AttributeError) as e:
        logger.error("folder_file error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Open karne mein error: {str(e)}"
        }


# System control tools moved to jarvis_system_ctrl.py


@function_tool
async def create_folder(folder_name: str):
    """Creates a new folder on the Desktop (handles localized paths)."""
    try:
        # Robust Desktop path detection using environment variables or fallback
        desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        if not await asyncio.to_thread(os.path.exists, desktop):
            # Fallback to home folder if desktop is moved
            desktop = os.path.expanduser("~")

        path = os.path.join(desktop, folder_name)
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)
        return f"✅ Folder '{folder_name}' Desktop par create kar diya gaya hai."
    except OSError as e:
        logger.error("Create folder error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error creating folder: {str(e)}",
            "error": str(e)
        }


@function_tool
async def open_outputs_folder(subfolder: str = "") -> dict:
    """
    Opens the Jarvis_Outputs folder or a specific subfolder (e.g., 'QR_Codes', 'Generated_Images', 'Downloads').
    Use this when the user asks to open the generated files, downloads, or specific output folders.
    """
    try:
        base_dir = os.path.join(os.getcwd(), "Jarvis_Outputs")
        target_path = base_dir

        if subfolder:
            # Try to match subfolder name flexibly
            subfolders = {
                "qr": "QR_Codes",
                "image": "Generated_Images",
                "download": "Downloads"
            }
            for key, folder in subfolders.items():
                if key in subfolder.lower():
                    target_path = os.path.join(base_dir, folder)
                    break

        if not os.path.exists(target_path):
            os.makedirs(target_path, exist_ok=True)

        os.startfile(target_path)  # nosec B606
        folder_name = os.path.basename(
            target_path) if subfolder else "Jarvis_Outputs"
        return {
            "status": "success",
            "folder": folder_name,
            "message": f"📂 {folder_name} folder open kar diya gaya hai, Sir Matloob."
        }
    except OSError as e:
        logger.error("Error opening outputs folder: %s", e)
        return {
            "status": "error",
            "message": f"❌ Folder open karne mein error aaya: {str(e)}",
            "error": str(e)
        }
