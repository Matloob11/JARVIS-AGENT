"""
Jarvis Notepad Automation Module
Handles creating, writing, and running code in Notepad.
"""
import asyncio
import os
import shlex
import subprocess
import time
import time
from typing import Any, Optional

import pyautogui

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_bridge import notify_tool_action
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_win32 import WIN32_ERRORS, pywintypes, win32con, win32gui

# Use WIN32_ERRORS tuple for stable generic exception catching in Win32 logic

# Setup logging
logger = setup_logger("JARVIS-NOTEPAD")

_bg_tasks: set[asyncio.Task[Any]] = set()

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

    def __init__(self) -> None:
        self.current_file_path: Optional[str] = None

    async def ensure_notepad_focus(self, timeout: int = 5) -> bool:
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
                    except WIN32_ERRORS as e:  # pylint: disable=broad-exception-caught, no-member
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

    async def simulate_typing(self, text: str) -> bool:
        """Simulate typing text with rapid visual animation (3x speed)"""
        try:
            # 0.005 is extremely fast (3x+ feel) as requested
            pyautogui.write(text, interval=0.005)
            return True
        except (pyautogui.FailSafeException, RuntimeError, AttributeError) as e:
            logger.exception("Typing simulation failed: %s", e)
            return False

    async def save_file_safely(self, content: str, filename: str, folder_path: Optional[str] = None) -> tuple[bool, str]:
        """Save content to a file safely using standard I/O."""
        try:
            # Set dedicated workspace folder as Jarvis_Outputs (as requested/noted in failures)
            if not folder_path:
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                folder_path = os.path.join(project_root, "Jarvis_Outputs")

            os.makedirs(folder_path, exist_ok=True)

            # CRITICAL: Enforce basename only to prevent "Save As" path errors in Notepad
            clean_filename = os.path.basename(filename)
            full_path = str(os.path.join(folder_path, clean_filename))

            # Ensure the directory exists before saving
            file_dir = os.path.dirname(full_path)
            if not os.path.exists(file_dir):
                os.makedirs(file_dir, exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            self.current_file_path = full_path
            logger.info("File successfully saved: %s", full_path)
            return True, full_path

        except OSError as e:
            logger.error("Error creating template: %s", e)
            return False, str(e)

    async def close_active_notepad(self, force: bool = True) -> bool:
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
                def callback(hwnd: Any, extra: Any) -> None:  # pylint: disable=unused-argument
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

    async def handle_save_as_dialog(self, full_path: str) -> bool:
        """Types the absolute path into the Windows Save As dialog if present."""
        try:
            logger.info("Checking for 'Save As' dialog...")
            await asyncio.sleep(1.0)

            # Use abspath with normal backslashes for Windows dialog
            clean_path = os.path.abspath(full_path).replace("/", "\\")

            # Try to find the Save As window
            if gw:
                save_as_wins = gw.getWindowsWithTitle('Save As')
                if save_as_wins:
                    logger.info("Found 'Save As' dialog. Typing path: %s", clean_path)
                    save_as_wins[0].activate()
                    await asyncio.sleep(0.5)
                    # Type the full path into the 'File name' box
                    pyautogui.typewrite(clean_path)
                    await asyncio.sleep(0.5)
                    pyautogui.press('enter')
                    await asyncio.sleep(1.5)

                    # If it says 'Overwrite?', confirmed it
                    confirm_wins = gw.getWindowsWithTitle('Confirm Save As')
                    if confirm_wins:
                        pyautogui.press('y')
                    return True
            return False
        except Exception as e:
            logger.warning("Error in handle_save_as_dialog: %s", e)
            return False

    async def auto_close_browser(self, delay: int = 30) -> None:
        """Background task to close browser after delay"""
        try:
            logger.info("Browser auto-close timer started: %s seconds", delay)
            await asyncio.sleep(delay)
            # Try to kill common browser processes
            browsers = ["msedge.exe", "chrome.exe", "firefox.exe"]
            for browser in browsers:
                subprocess.run([r"C:\Windows\System32\taskkill.exe", "/f", "/im", browser],
                               check=False, capture_output=True)
            logger.info("Browser windows closed via auto-timer.")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Auto-close minor error: %s", e)


# Global instance
notepad_automation = NotepadAutomation()

# Code templates
HTML_LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JARVIS Unified Interface</title>
    <!-- CSS Internal -->
    <style>
        body { 
            background: #020617; 
            color: #d1d5db; 
            font-family: 'Segoe UI', sans-serif;
            display: flex; 
            align-items: center; 
            justify-content: center; 
            height: 100vh; 
            margin: 0;
            overflow: hidden;
        }
        .card { 
            padding: 40px; 
            background: rgba(15, 23, 42, 0.8); 
            backdrop-filter: blur(10px);
            border: 1px solid #1e293b;
            border-radius: 20px; 
            text-align: center;
            box-shadow: 0 0 50px rgba(56, 189, 248, 0.1);
            transition: transform 0.3s;
        }
        .card:hover { transform: scale(1.02); }
        h1 { color: #38bdf8; margin-bottom: 5px; letter-spacing: 2px; }
        button { 
            margin-top: 20px; padding: 10px 30px; 
            background: #38bdf8; color: black; border: none; 
            border-radius: 8px; cursor: pointer; font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="card">
        <h1>JARVIS CORE</h1>
        <p>Security Identity: Verified</p>
        <button id="mainBtn">ACTIVATE SYSTEM</button>
    </div>

    <!-- JS Internal -->
    <script>
        document.getElementById('mainBtn').onclick = () => {
            alert('JARVIS: System is already at peak performance, Sir Matloob.');
        };
        console.log("JARVIS Unified File Loaded.");
    </script>
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


def get_template_content(code_type: str, filename: str) -> tuple[str, str]:
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


@jarvis_tool
async def create_template_code(code_type: str, filename: str = "", auto_run: bool = True) -> dict[str, Any]:
    """
    Create code file, visually type it in Notepad, and optionally Run it.
    """
    try:
        content, filename = get_template_content(code_type, filename)
        if not content:
            return {
                "status": "error",
                "message": "❌ Unsupported code type",
            }

        success, full_path = await notepad_automation.save_file_safely("", filename)
        if not success:
            return {
                "status": "error",
                "message": f"❌ Failed to initialize file: {full_path}",
            }

        msg = f"✅ File initialized: {filename}\n"
        try:
            # pylint: disable=consider-using-with
            subprocess.Popen([r"C:\Windows\System32\notepad.exe", full_path])
            if await notepad_automation.ensure_notepad_focus():
                await notepad_automation.simulate_typing(content)
                msg += "📝 Typed code in Notepad.\n"
                await asyncio.sleep(0.5)
                pyautogui.hotkey('ctrl', 's')

                # Check if Save As dialog appeared (happens if it's a new or untitled file)
                dialog_handled = await notepad_automation.handle_save_as_dialog(full_path)
                if dialog_handled:
                    msg += "📂 Handled Save As dialog.\n"

                await asyncio.sleep(1.5)
                await notepad_automation.close_active_notepad()
            else:
                msg += "⚠️ Notepad focus failed. Writing manually.\n"
                await notepad_automation.save_file_safely(content, filename)
        except (OSError, pyautogui.FailSafeException, RuntimeError) as e:
            msg += f"⚠️ GUI automation failed: {e!s}. File saved programmatically.\n"
            await notepad_automation.save_file_safely(content, filename)

        if auto_run:
            if filename.endswith('.html'):
                os.startfile(full_path)  # nosec B606
                msg += "🌐 HTML opened (Auto-close set for 30s)!"
                # Schedule auto-close in background
                task = asyncio.create_task(notepad_automation.auto_close_browser(30))
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)
            elif filename.endswith('.py'):
                subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', 'python', full_path])  # nosec B607
                msg += "🐍 Python script running!"

        return {
            "status": "success",
            "filename": filename,
            "path": full_path,
            "message": msg,
        }
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception("create_template_code error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {e!s}",
        }


@jarvis_tool
async def write_custom_code(content: str, filename: str, auto_run: bool = True) -> dict[str, Any]:
    """Writes custom code based on user request."""
    try:
        if not filename:
            return {
                "status": "error",
                "message": "❌ Filename is required",
            }
        success, full_path = await notepad_automation.save_file_safely("", filename)
        if not success:
            return {
                "status": "error",
                "message": f"❌ Failed to initialize file: {full_path}",
            }

        msg = f"✅ File initialized: {filename}\n"
        try:
            # pylint: disable=consider-using-with
            subprocess.Popen([r"C:\Windows\System32\notepad.exe", full_path])
            if await notepad_automation.ensure_notepad_focus():
                await notepad_automation.simulate_typing(content)
                msg += "📝 Typed code in Notepad.\n"
                await asyncio.sleep(0.5)
                pyautogui.hotkey('ctrl', 's')

                # Handle Save As dialog
                await notepad_automation.handle_save_as_dialog(full_path)

                await asyncio.sleep(1.5)
                await notepad_automation.close_active_notepad()
            else:
                await notepad_automation.save_file_safely(content, filename)
                msg += "⚠️ Focus failed. Saved programmatically.\n"
        except (pyautogui.FailSafeException, OSError, RuntimeError) as e:
            logger.warning("GUI automation for custom code failed: %s", e)
            await notepad_automation.save_file_safely(content, filename)
            msg += "⚠️ GUI automation failed. File saved programmatically.\n"

        if auto_run:
            if filename.endswith('.html'):
                os.startfile(full_path)  # nosec B606
                msg += "🌐 HTML opened (Auto-close in 30s)!"
                t = asyncio.create_task(notepad_automation.auto_close_browser(30))
                _bg_tasks.add(t)
                t.add_done_callback(_bg_tasks.discard)
            elif filename.endswith('.py'):
                subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', 'python', full_path])  # nosec B607
                msg += "🐍 Python script running!"

        return {
            "status": "success",
            "filename": filename,
            "path": full_path,
            "message": msg,
        }
    except (OSError, ValueError, RuntimeError) as e:
        logger.exception("Error in write_custom_code: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {e}",
        }


@jarvis_tool
async def run_cmd_command(command: str) -> dict[str, Any]:
    """Execute a CMD command (Non-interactive) safely."""
    try:
        # Sanitize and run via list to prevent injection
        cmd_list = ["cmd", "/c", "start", "cmd", "/k"] + shlex.split(command)
        # pylint: disable=consider-using-with
        subprocess.Popen(cmd_list)  # nosec B603
        return {
            "status": "success",
            "command": command,
            "message": f"✅ Command sent to CMD: {command}",
        }
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.exception("run_cmd_command error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error running command: {e}",
        }


@jarvis_tool
async def open_notepad_simple() -> dict[str, Any]:
    """Open a blank Notepad instance"""
    try:
        # pylint: disable=consider-using-with
        subprocess.Popen([r"C:\Windows\System32\notepad.exe"])
        # Notify UI about Notepad open
        t = asyncio.create_task(notify_tool_action(
            "notepad", "Opened blank instance"))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
        return {
            "status": "success",
            "message": "✅ Notepad opened",
        }
    except (subprocess.SubprocessError, OSError) as e:
        logger.exception("open_notepad_simple error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {e!s}",
        }
