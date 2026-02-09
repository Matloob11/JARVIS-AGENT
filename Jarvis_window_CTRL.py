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
    "edge": r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    "microsoft edge": r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',

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
    "edge": "Microsoft Edge",
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
        "open", "opening", "opened", "run", "launch", "start",
        "kholo", "khol", "open", "opening", "karo", "chalao", "chalana",
        "aur", "usmein", "likh", "do", "hello", "jarvis", "what", "are", "you", "doing",
        "browser", "app", "application"
    ]
    text = text.lower()
    for w in REMOVE_WORDS:
        text = text.replace(w, "")
    
    # Return the full cleaned text instead of just the first word
    # This allows matching multi-word apps like "microsoft edge" or "control panel"
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

@function_tool
async def save_notepad(file_path: str = r"D:\jarvis_notes.txt") -> str:
    """
    Saves the content of an open Notepad window.
    """
    try:
        import pygetwindow as gw
        # Try to find Notepad window
        notepad_windows = [w for w in gw.getWindowsWithTitle('Notepad')]
        if not notepad_windows:
            return "❌ Notepad window nahi mili."
        
        notepad = notepad_windows[0]
        notepad.activate()
        await asyncio.sleep(1)
        
        # Ctrl+S
        pyautogui.hotkey('ctrl', 's')
        await asyncio.sleep(1.5) # Wait for Save As dialog
        
        # Type path
        pyautogui.write(file_path, interval=0.01)
        await asyncio.sleep(0.5)
        pyautogui.press('enter')
        
        # Overwrite check - ONLY if file exists
        if os.path.exists(file_path):
            await asyncio.sleep(1)
            pyautogui.press('left')
            pyautogui.press('enter')
        else:
            await asyncio.sleep(0.5)
        
        return f"💾 Notepad file ko '{file_path}' par save kar diya gaya hai."
    except Exception as e:
        logger.error(f"Save Notepad Error: {e}")
        return f"❌ Save karne mein error: {e}"

@function_tool
async def open_notepad_file(file_path: str) -> str:
    """
    Opens a specific text file in Notepad.
    """
    if not os.path.exists(file_path):
        return f"❌ File nahi mili: {file_path}"
    
    try:
        # Use full path to notepad if possible
        subprocess.Popen([r"C:\Windows\System32\notepad.exe", file_path])
        return f"📂 {file_path} ko Notepad mein open kar diya gaya hai."
    except Exception as e:
        return f"❌ File open karne mein error: {e}"

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

    # Fuzzy matching for common apps
    matched_key = fuzzy_match_app(window_name)
    search_title = FOCUS_TITLES.get(matched_key, window_name).lower()
    
    logger.info(f"CLOSE → target='{original_name}' search_title='{search_title}'")

    hwnds_to_close = []

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            # Specific logic for common apps to avoid broad matches
            is_match = False
            
            if matched_key == "notepad":
                # Notepad windows usually end with " - Notepad" or are just "Notepad"
                if title.endswith(" - notepad") or title == "notepad":
                    is_match = True
            elif matched_key == "edge":
                if "edge" in title or "msedge" in title:
                    is_match = True
            elif matched_key == "chrome":
                if "google chrome" in title:
                    is_match = True
            else:
                # Fallback to substring match for other apps
                if search_title in title:
                    is_match = True

            # Safeguard: Don't close the current script or the IDE if they just happen to have the name in the title
            # and it wasn't an exact match.
            if is_match:
                # If we matched "notepad" but the title also contains ".py", it's likely a script file open in an IDE
                # unless the search_title was very specific.
                if ".py" in title and search_title == "notepad":
                    is_match = False
                    
                if is_match:
                    hwnds_to_close.append(hwnd)

    win32gui.EnumWindows(enum_handler, None)

    if not hwnds_to_close:
        return f"❌ '{original_name}' naam ki koi window nahi mili."

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

    if still_open_count == 0:
        return f"🗑️ {original_name} band kar diya gaya hai aur maine verify kar liya hai."
    else:
        return f"⚠ {original_name} ko band karne ki command bhej di gayi hai, lekin {still_open_count} window(s) abhi bhi open lag rahi hain."

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
                return "📉 Active window minimize kar di gayi hai"
            return "❌ Koi active window nahi mili"
        
        # Search for window by name
        windows = gw.getWindowsWithTitle(window_name)
        if windows:
            windows[0].minimize()
            return f"📉 '{window_name}' minimize kar di gayi hai"
        return f"❌ '{window_name}' naam ki koi window nahi mili"
    except Exception as e:
        return f"❌ Window minimize nahi ho paayi: {e}"

@function_tool
async def maximize_window(window_name: str = "active") -> str:
    """
    Maximizes or restores a window. If window_name is 'active', it maximizes the currently focused window.
    """
    if not gw:
        return "❌ pygetwindow available nahi hai"
    
    try:
        if window_name.lower() == "active":
            window = gw.getActiveWindow()
            if window:
                window.maximize()
                return "📈 Active window maximize kar di gayi hai"
            return "❌ Koi active window nahi mili"
        
        # Search for window by name
        windows = gw.getWindowsWithTitle(window_name)
        if windows:
            windows[0].maximize()
            return f"📈 '{window_name}' maximize kar di gayi hai"
        return f"❌ '{window_name}' naam ki koi window nahi mili"
    except Exception as e:
        return f"❌ Window maximize nahi ho paayi: {e}"

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
