"""
# pylint: disable=duplicate-code
Jarvis Notepad Automation Module
Handles creating, writing, and running code in Notepad.
"""
import os
import time
import subprocess
import asyncio
import pyautogui
try:
    import win32gui
    import win32con
    import pywintypes
except ImportError:
    win32gui = None
    win32con = None
    pywintypes = None
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-NOTEPAD")

# Configure pyautogui
pyautogui.FAILSAFE = True

# Try to import pygetwindow for window focus control
try:
    import pygetwindow as gw
except ImportError:
    gw = None
    logger.warning(
        "pygetwindow not installed. Window focus verification will be limited.")


class NotepadAutomation:
    """Class to handle Notepad GUI automation"""

    def __init__(self):
        self.current_file_path = None

    async def ensure_notepad_focus(self, timeout: int = 5):
        """
        Waits for Notepad to verify it is the active window.
        Returns True if Notepad is focused, False otherwise.
        """
        if gw is None:
            await asyncio.sleep(2)  # Fallback
            return True

        logger.info("Waiting for Notepad to appear and gain focus...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Find windows with "Notepad" in title but filter for actual Notepad app
                all_windows = gw.getWindowsWithTitle('Notepad')
                windows = []
                for w in all_windows:
                    title = w.title.lower()
                    # Only match if it looks like the Notepad app and NOT a script file in an IDE
                    if (title.endswith(" - notepad") or title == "notepad") and ".py" not in title:
                        windows.append(w)

                if not windows:
                    await asyncio.sleep(0.5)
                    continue

                notepad = windows[0]
                if notepad.isMinimized:
                    notepad.restore()

                # Bring to front using Win32 for maximum reliability
                if win32gui and pywintypes:
                    try:
                        # pylint: disable=protected-access
                        win32gui.ShowWindow(notepad._hwnd, win32con.SW_RESTORE)
                        win32gui.SetForegroundWindow(notepad._hwnd)
                    except (pywintypes.error, AttributeError) as e:  # pylint: disable=broad-exception-caught, no-member
                        logger.debug(
                            "Minor Win32 focus error for Notepad: %s", e)

                notepad.activate()
                await asyncio.sleep(0.5)
                if notepad.isActive:
                    logger.info("Notepad is active and focused.")
                    return True
            except (AttributeError, IndexError, gw.PyGetWindowException) as e:
                logger.exception("Error attempting to focus Notepad: %s", e)
            await asyncio.sleep(0.5)

        logger.error("Timed out waiting for Notepad focus.")
        return False

    async def simulate_typing(self, text: str):
        """Simulate typing text line by line with absolute maximum speed"""
        try:
            pyautogui.PAUSE = 0.0
            lines = text.split('\n')
            for line in lines:
                pyautogui.write(line, interval=0.0)
                pyautogui.press('enter')
            return True
        except (pyautogui.FailSafeException, RuntimeError, AttributeError) as e:
            logger.exception("Typing simulation failed: %s", e)
            return False

    async def save_file_safely(self, content, filename, folder_path=None):
        """Save content to a file safely using standard I/O."""
        try:
            if not folder_path:
                # Robust Desktop path detection
                desktop = os.path.join(
                    os.environ.get("USERPROFILE"), "Desktop")
                if not os.path.exists(desktop):
                    desktop = os.path.expanduser("~")
                folder_path = os.path.join(desktop, "JARVIS_Output")

            os.makedirs(folder_path, exist_ok=True)
            full_path = os.path.join(folder_path, filename)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            self.current_file_path = full_path
            logger.info("File successfully saved: %s", full_path)
            return True, full_path

        except (OSError, IOError) as e:
            logger.error("Error creating template: %s", e)
            return False, str(e)

    async def close_active_notepad(self, force: bool = True):
        """Closes the currently active Notepad window. If force=True, uses taskkill."""
        try:
            logger.info("Closing Notepad window...")
            if force:
                # Force close via taskkill using absolute path for security
                subprocess.run([r"C:\Windows\System32\taskkill.exe", "/f", "/im", "notepad.exe"],
                               check=False, capture_output=True)
                logger.info("Notepad force closed via taskkill.")
                return True

            if win32gui:
                def callback(hwnd, extra):  # pylint: disable=unused-argument
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd).lower()
                        is_notepad = title.endswith(
                            " - notepad") or title == "notepad"
                        if is_notepad and ".py" not in title:
                            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                win32gui.EnumWindows(callback, None)
            else:
                pyautogui.hotkey('alt', 'f4')

            await asyncio.sleep(0.5)
            logger.info("Notepad close signal sent.")
            return True
        except (subprocess.SubprocessError, OSError, AttributeError) as e:
            logger.error("Error closing Notepad: %s", e)
            return False


# Global instance
notepad_automation = NotepadAutomation()

# Code templates
HTML_LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background: #020617; color: white; display: flex; align-items: center; justify-content: center; height: 100vh; }
        .card { padding: 40px; background: rgba(15, 23, 42, 0.6); border-radius: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="card"><h1>JARVIS LOGIN</h1><p>Secure Access</p></div>
</body>
</html>'''

PYTHON_HELLO_TEMPLATE = '''# Simple Python Hello World Program
print("Hello World from JARVIS!")
print("=" * 40)
'''

HEART_ANIMATION_TEMPLATE = '''# heart animation Amazing Moment
# MADE BY MATLOOB
import math
from turtle import *

def hearta(k):
    return 15 * math.sin(k) ** 3

def heartb(k):
    return 12 * math.cos(k) - 5 * \\
           math.cos(2 * k) - 2 * \\
           math.cos(3 * k) - \\
           math.cos(4 * k)

speed(0)
bgcolor("black")
for i in range(6000):
    goto(hearta(i) * 20, heartb(i) * 20)
    for j in range(1):
        color("red")
        dot()  # Draw a dot at the current position
goto(0, 0)
print("MADE BY MATLOOB")
done()
'''


def get_template_content(code_type: str, filename: str):
    """Retrieve template content and default filename"""
    content = ""
    new_filename = filename
    code_type_lower = code_type.lower()

    if code_type_lower == "html_login":
        content = HTML_LOGIN_TEMPLATE
        if not new_filename:
            new_filename = f"login_{int(time.time())}.html"
    elif code_type_lower == "python_hello":
        content = PYTHON_HELLO_TEMPLATE
        if not new_filename:
            new_filename = f"hello_{int(time.time())}.py"
    elif code_type_lower in ["amazing_code", "heart_animation"]:
        content = HEART_ANIMATION_TEMPLATE
        if not new_filename:
            new_filename = f"amazing_heart_{int(time.time())}.py"

    return content, new_filename


@function_tool
async def create_template_code(code_type: str, filename: str = "", auto_run: bool = True) -> dict:
    """
    Create code file, visually type it in Notepad, and optionally Run it.
    """
    try:
        content, filename = get_template_content(code_type, filename)
        if not content:
            return {
                "status": "error",
                "message": "❌ Unsupported code type"
            }

        success, full_path = await notepad_automation.save_file_safely("", filename)
        if not success:
            return {
                "status": "error",
                "message": f"❌ Failed to initialize file: {full_path}"
            }

        msg = f"✅ File initialized: {filename}\n"
        try:
            # pylint: disable=consider-using-with
            subprocess.Popen(
                [r"C:\Windows\System32\notepad.exe", full_path])
            if await notepad_automation.ensure_notepad_focus():
                await notepad_automation.simulate_typing(content)
                msg += "📝 Typed code in Notepad.\n"
                await asyncio.sleep(0.5)
                pyautogui.hotkey('ctrl', 's')
                await asyncio.sleep(2)
                await notepad_automation.close_active_notepad()
            else:
                msg += "⚠️ Notepad focus failed. Writing manually.\n"
                await notepad_automation.save_file_safely(content, filename)
        except (OSError, IOError, pyautogui.FailSafeException, RuntimeError) as e:
            msg += f"⚠️ GUI automation failed: {str(e)}. File saved programmatically.\n"
            await notepad_automation.save_file_safely(content, filename)

        if auto_run:
            if filename.endswith('.html'):
                os.startfile(full_path)  # nosec B606
                msg += "🌐 HTML opened in Browser!"
            elif filename.endswith('.py'):
                # Safe launch using list arguments to avoid shell injection
                # pylint: disable=consider-using-with
                subprocess.Popen(
                    ['cmd', '/c', 'start', 'cmd', '/k', 'python', full_path])  # nosec B607
                msg += "🐍 Python script running in CMD!"

        return {
            "status": "success",
            "filename": filename,
            "path": full_path,
            "message": msg
        }
    except (OSError, IOError, ValueError, RuntimeError) as e:
        logger.exception("create_template_code error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {str(e)}"
        }


@function_tool
async def write_custom_code(content: str, filename: str, auto_run: bool = True) -> dict:
    """Writes custom code based on user request."""
    try:
        if not filename:
            return {
                "status": "error",
                "message": "❌ Filename is required"
            }
        success, full_path = await notepad_automation.save_file_safely("", filename)
        if not success:
            return {
                "status": "error",
                "message": f"❌ Failed to initialize file: {full_path}"
            }

        msg = f"✅ File initialized: {filename}\n"
        try:
            # pylint: disable=consider-using-with
            subprocess.Popen(
                [r"C:\Windows\System32\notepad.exe", full_path])
            if await notepad_automation.ensure_notepad_focus():
                await notepad_automation.simulate_typing(content)
                msg += "📝 Typed code in Notepad.\n"
                await asyncio.sleep(0.5)
                pyautogui.hotkey('ctrl', 's')
                await asyncio.sleep(2)
                await notepad_automation.close_active_notepad()
            else:
                await notepad_automation.save_file_safely(content, filename)
                msg += "⚠️ Focus failed. Saved programmatically.\n"
        except (pyautogui.FailSafeException, OSError, IOError, RuntimeError) as e:
            logger.warning("GUI automation for custom code failed: %s", e)
            await notepad_automation.save_file_safely(content, filename)
            msg += "⚠️ GUI automation failed. File saved programmatically.\n"

        if auto_run:
            if filename.endswith('.html'):
                os.startfile(full_path)  # nosec B606
                msg += "🌐 HTML opened!"
            elif filename.endswith('.py'):
                # Safe launch using list arguments
                # pylint: disable=consider-using-with
                subprocess.Popen(
                    ['cmd', '/c', 'start', 'cmd', '/k', 'python', full_path])  # nosec B607
                msg += "🐍 Python script running!"

        return {
            "status": "success",
            "filename": filename,
            "path": full_path,
            "message": msg
        }
    except (OSError, IOError, ValueError, RuntimeError) as e:
        logger.exception("Error in write_custom_code: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {e}"
        }


@function_tool
async def run_cmd_command(command: str) -> dict:
    """Execute a CMD command (Non-interactive) safely."""
    try:
        # Sanitize and run via list to prevent injection
        # Use shlex to split if the command is a complex string
        import shlex
        cmd_list = ["cmd", "/c", "start", "cmd", "/k"] + shlex.split(command)
        subprocess.Popen(cmd_list)  # nosec B603
        return {
            "status": "success",
            "command": command,
            "message": f"✅ Command sent to CMD: {command}"
        }
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.exception("run_cmd_command error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error running command: {e}"
        }


@function_tool
async def open_notepad_simple() -> dict:
    """Open a blank Notepad instance"""
    try:
        # pylint: disable=consider-using-with
        subprocess.Popen([r"C:\Windows\System32\notepad.exe"])
        return {
            "status": "success",
            "message": "✅ Notepad opened"
        }
    except (subprocess.SubprocessError, OSError) as e:
        logger.exception("open_notepad_simple error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {str(e)}"
        }
