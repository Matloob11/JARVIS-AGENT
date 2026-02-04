import os
import subprocess
import logging
import sys
import asyncio
import webbrowser
from fuzzywuzzy import process

try:
    from livekit.agents import function_tool
except ImportError:
    def function_tool(func):
        return func

try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None
    win32con = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

try:
    import pyautogui
except Exception:
    pyautogui = None

from keyboard_mouse_CTRL import type_text_tool
from jarvis_whatsapp_automation import whatsapp_bot

# ===================== LOGGER ===================== #
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-WINDOW")

# ===================== APP MAP ===================== #
APP_MAPPINGS = {
    # System & Common
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "command prompt": "cmd",
    "cmd": "cmd",
    "paint": "mspaint",
    "camera": "camera",
    "settings": "ms-settings:",
    "explorer": "explorer",
    
    # Browsers
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",

    # Store Apps (Found on System)
    "whatsapp": "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "tiktok": "shell:AppsFolder\\BytedancePte.Ltd.TikTok_6yccndn6064se!App",
    "instagram": "shell:AppsFolder\\Facebook.InstagramBeta_8xx8rvfyw5nnt!App",
    "facebook": "shell:AppsFolder\\FACEBOOK.FACEBOOK_8xx8rvfyw5nnt!App",
    "media player": "shell:AppsFolder\\Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
    "films": "shell:AppsFolder\\Microsoft.ZuneVideo_8wekyb3d8bbwe!Microsoft.ZuneVideo",
    "movies": "shell:AppsFolder\\Microsoft.ZuneVideo_8wekyb3d8bbwe!Microsoft.ZuneVideo",
    "store": "shell:AppsFolder\\Microsoft.WindowsStore_8wekyb3d8bbwe!App",
    "clock": "shell:AppsFolder\\Microsoft.WindowsAlarms_8wekyb3d8bbwe!App",
    "alarms": "shell:AppsFolder\\Microsoft.WindowsAlarms_8wekyb3d8bbwe!App",

    # Installed Desktop Apps
    "vlc": "vlc",
    "obs": "obs64", 
    "obs studio": "obs64",

    # URLs
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",

    # Hindi / Varieties
    "youtub": "https://www.youtube.com", 
    "watsapp": "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "insta": "shell:AppsFolder\\Facebook.InstagramBeta_8xx8rvfyw5nnt!App",
    "face book": "shell:AppsFolder\\FACEBOOK.FACEBOOK_8xx8rvfyw5nnt!App",
}

FOCUS_TITLES = {
    "notepad": "Notepad",
    "calc": "Calculator",
    "chrome": "Google Chrome",
    "edge": "Edge",
    "vlc": "VLC",
    "cmd": "Command Prompt",
    "youtube": "YouTube",
    "whatsapp": "WhatsApp",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "obs": "OBS",
    "store": "Microsoft Store",
    "settings": "Settings",
}

# ===================== UTIL ===================== #
def normalize_command(text: str) -> str:
    """
    Removes Hindi/English open keywords safely and extracts the app name
    """
    REMOVE_WORDS = [
        "open", "opening", "opened",
        "kholo", "khol", "open", "opening", "karo",
        "aur", "usmein", "likh", "do", "hello", "jarvis", "what", "are", "you", "doing"
    ]
    text = text.lower()
    for w in REMOVE_WORDS:
        text = text.replace(w, "")
    # Take the first meaningful word
    words = text.strip().split()
    if words:
        return words[0]
    return text.strip()

async def focus_window(title: str):
    if not gw:
        return False
    await asyncio.sleep(1.2)
    for w in gw.getAllWindows():
        if title.lower() in w.title.lower():
            if w.isMinimized:
                w.restore()
            w.activate()
            return True
    return False

def fuzzy_match_app(app_name: str) -> str:
    keys = list(APP_MAPPINGS.keys())
    match, score = process.extractOne(app_name, keys)
    if score >= 70:
        return match
    return app_name

# ===================== OPEN APP ===================== #
@function_tool
async def open(full_command: str) -> str:
    try:
        clean = normalize_command(full_command)
        matched_key = fuzzy_match_app(clean)
        app = APP_MAPPINGS.get(matched_key, matched_key)

        logger.info(f"OPEN → raw='{full_command}' clean='{clean}' match='{matched_key}'")

        # 🌐 URL → browser or desktop app
        if app.startswith("http"):
            webbrowser.open(app)
            await asyncio.sleep(3)  # Wait for browser to open
            if "youtube" in matched_key.lower():
                await focus_window("YouTube")
            elif "whatsapp" in matched_key.lower():
                # For web WhatsApp, ensure focus
                await focus_window("WhatsApp")
        elif matched_key == "whatsapp":
            await whatsapp_bot.open_whatsapp()
            await whatsapp_bot.ensure_whatsapp_focus()
        elif app.startswith("whatsapp://"):
            try:
                # Try to open desktop app via URI scheme
                subprocess.Popen([app], shell=True) # Use shell=True for URI schemes on Windows
                await asyncio.sleep(5)  # Give time for app to open
                await focus_window("WhatsApp")
            except Exception as uri_e:
                logger.warning(f"Failed to open WhatsApp desktop app via URI: {uri_e}. Falling back to web.")
                webbrowser.open("https://web.whatsapp.com")
                await asyncio.sleep(5)
                await focus_window("WhatsApp")
        else:
            # 🪟 Try Start Menu (non-blocking)
            if pyautogui:
                try:
                    await asyncio.to_thread(pyautogui.press, "win")
                    await asyncio.sleep(0.5)
                    await asyncio.to_thread(pyautogui.write, matched_key, 0.05)
                    await asyncio.sleep(0.4)
                    await asyncio.to_thread(pyautogui.press, "enter")
                except Exception:
                    pass

            # 🧨 FINAL fallback (NO TIMEOUT)
            subprocess.Popen(app, shell=True)

            # 🎯 Focus
            title = FOCUS_TITLES.get(matched_key)
            if title:
                await focus_window(title)

        # ✍️ Check for writing
        write_text = None
        if "write" in full_command.lower():
            parts = full_command.lower().split("write", 1)
            if len(parts) > 1:
                write_text = parts[1].strip()
        elif "likh" in full_command:
            parts = full_command.split("likh", 1)
            if len(parts) > 1:
                write_text = parts[1].strip()
                # Remove "do" if present
                if write_text.startswith("do "):
                    write_text = write_text[3:].strip()

        if write_text:
            await asyncio.sleep(2)  # Wait for app to open
            result = await type_text_tool(write_text)
            return f"🚀 {matched_key} khol diya gaya hai aur {result}"

        return f"🚀 {matched_key} khol diya gaya hai"

    except Exception as e:
        logger.error(e)
        return f"❌ App open nahi ho paaya: {e}"

# ===================== CLOSE WINDOW ===================== #
@function_tool
async def close(window_name: str) -> str:
    if not win32gui:
        return "❌ win32gui available nahi hai"

    window_name = window_name.lower()

    if "whatsapp" in window_name:
        if await whatsapp_bot.close_whatsapp():
            return "🗑️ WhatsApp band kar diya gaya hai"

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if window_name in title:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    win32gui.EnumWindows(enum_handler, None)
    return f"🗑️ {window_name} band kar diya gaya hai"

@function_tool
async def folder_file(path: str) -> str:
    return "❌ folder_file tool not implemented"





# ===================== SYSTEM CONTROL ===================== #
@function_tool
async def shutdown_system():
    """Shuts down the computer immediately."""
    os.system("shutdown /s /t 0")
    return "🔌 System shutting down..."

@function_tool
async def restart_system():
    """Restarts the computer immediately."""
    os.system("shutdown /r /t 0")
    return "🔄 System restarting..."

@function_tool
async def sleep_system():
    """Puts the computer to sleep."""
    os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
    return "😴 System going to sleep..."

@function_tool
async def lock_screen():
    """Locks the screen."""
    os.system("rundll32.exe user32.dll,LockWorkStation")
    return "🔒 Screen locked."

@function_tool
async def create_folder(folder_name: str):
    """Creates a new folder on the Desktop."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    path = os.path.join(desktop, folder_name)
    try:
        os.makedirs(path, exist_ok=True)
        return f"Cc Folder created: {folder_name}"
    except Exception as e:
        return f"❌ Error creating folder: {e}"
