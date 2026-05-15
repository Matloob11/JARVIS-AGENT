"""
Jarvis Notepad Automation Module
Handles creating, writing, and running code in Notepad.
"""
import asyncio
import os
import shlex
import subprocess
import time
from typing import Any, Optional

import pyautogui

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_bridge import notify_thinking, notify_tool_action
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_win32 import WIN32_ERRORS, pywintypes, win32con, win32gui

# Use WIN32_ERRORS tuple for stable generic exception catching in Win32 logic

# Setup logging
logger = setup_logger("JARVIS-NOTEPAD")

_bg_tasks: set[asyncio.Task[Any]] = set()

# Global Automation Instance
_notepad_automation = None

def get_notepad_mgr():
    global _notepad_automation
    if _notepad_automation is None:
        from services.automation.jarvis_notepad_automation import NotepadAutomation
        _notepad_automation = NotepadAutomation()
    return _notepad_automation

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

    async def ensure_notepad_focus(self, timeout: int = 10) -> bool:
        """
        Waits for Notepad to verify it is the active window and brings it to front.
        Handles minimized and non-focused states with high priority Win32 calls.
        """
        if gw is None:
            logger.warning("pygetwindow not available, falling back to simple delay.")
            await asyncio.sleep(2)
            return True

        logger.info("Ensuring Notepad is focused and visible...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Find windows with "Notepad" in title
                all_windows = gw.getWindowsWithTitle('Notepad')
                windows = []
                for w in all_windows:
                    title = w.title.lower()
                    # Filter for actual Notepad app
                    if (title.endswith(" - notepad") or title == "notepad") and ".py" not in title:
                        windows.append(w)

                if not windows:
                    await asyncio.sleep(0.5)
                    continue

                notepad = windows[0]
                
                # 1. Handle Minimized state
                if notepad.isMinimized:
                    logger.debug("Notepad is minimized, restoring...")
                    notepad.restore()
                    await asyncio.sleep(0.5)

                # 2. Use Win32 for hard focus
                if win32gui and pywintypes:
                    hwnd = notepad._hWnd
                    try:
                        # Ensure it's not hidden
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                        await asyncio.sleep(0.2)
                        # Bring to top
                        win32gui.SetForegroundWindow(hwnd)
                        await asyncio.sleep(0.2)
                        # Force focus
                        win32gui.SetFocus(hwnd)
                        # Bring to front for visibility
                        win32gui.BringWindowToTop(hwnd)
                    except WIN32_ERRORS as e:
                        logger.debug("Win32 focus attempt: %s", e)

                # 3. pygetwindow activate
                notepad.activate()
                await asyncio.sleep(0.8) # Increased wait for UI to catch up

                # 4. Verification
                if notepad.isActive or gw.getActiveWindow() == notepad:
                    logger.info("Notepad focus confirmed.")
                    # Move mouse to center of notepad to ensure click lands in text area
                    center_x = notepad.left + (notepad.width // 2)
                    center_y = notepad.top + (notepad.height // 2)
                    pyautogui.click(center_x, center_y)
                    await asyncio.sleep(0.2)
                    return True

            except (AttributeError, IndexError, gw.PyGetWindowException) as e:
                logger.error("Focus loop error: %s", e)
            
            await asyncio.sleep(0.5)

        logger.error("Timed out: Could not focus Notepad.")
        return False

    async def simulate_typing(self, text: str) -> bool:
        """Simulate typing text with visible animation (0.01s interval)"""
        try:
            lines = text.split("\n")
            for index, line in enumerate(lines):
                if line:
                    pyautogui.write(line, interval=0.01)
                if index < len(lines) - 1:
                    pyautogui.press("enter")
            return True
        except (pyautogui.FailSafeException, RuntimeError, AttributeError) as e:
            logger.exception("Typing simulation failed: %s", e)
            return False

    async def save_file_safely(self, content: str, filename: str, folder_path: Optional[str] = None) -> tuple[bool, str]:
        """Save content to a file safely using standard I/O."""
        try:
            # Set dedicated workspace folder as Jarvis_Outputs
            if not folder_path:
                folder_path = os.path.join(config.project_root, "Jarvis_Outputs")

            os.makedirs(folder_path, exist_ok=True)

            # CRITICAL: Enforce basename only to prevent "Save As" path errors in Notepad
            clean_filename = os.path.basename(filename)
            full_path = str(os.path.join(folder_path, clean_filename))

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            self.current_file_path = full_path
            logger.info("File successfully saved: %s", full_path)
            return True, full_path

        except OSError as e:
            logger.error("Error creating file: %s", e)
            return False, str(e)

    async def close_active_notepad(self, force: bool = True) -> bool:
        """Closes the currently active Notepad window gracefully first."""
        try:
            logger.info("Closing Notepad window...")
            closed_any = False
            
            # Find and close the window titled "Notepad" specifically
            if gw:
                windows = [w for w in gw.getWindowsWithTitle('Notepad') 
                          if (w.title.lower().endswith(" - notepad") or w.title.lower() == "notepad")]
                if windows:
                    for w in windows:
                        w.close() # Graceful close
                        closed_any = True
                        await asyncio.sleep(0.2)

            if not closed_any and win32gui:
                handles = []

                def _collect_notepad(hwnd, _param):
                    title = win32gui.GetWindowText(hwnd)
                    if win32gui.IsWindowVisible(hwnd) and title and "notepad" in title.lower():
                        handles.append(hwnd)

                win32gui.EnumWindows(_collect_notepad, None)
                for hwnd in handles:
                    win32gui.PostMessage(hwnd, getattr(win32con, "WM_CLOSE", 0x0010), 0, 0)
                    closed_any = True
                    await asyncio.sleep(0.2)

            if not closed_any and not force:
                pyautogui.hotkey('alt', 'f4')

            if force:
                # If still open, taskkill safely
                await asyncio.sleep(0.5)
                subprocess.run([r"C:\Windows\System32\taskkill.exe", "/f", "/im", "notepad.exe"],
                               check=False, capture_output=True)
                logger.info("Notepad force closed.")
                return True

            return True
        except Exception as e:
            logger.error("Error closing Notepad: %s", e)
            return False

    async def handle_save_as_dialog(self, full_path: str) -> bool:
        """Types the absolute path into the Windows Save As dialog if present."""
        try:
            logger.info("Checking for 'Save As' dialog...")
            await asyncio.sleep(1.0)

            # Use abspath with normal backslashes for Windows dialog
            clean_path = os.path.abspath(full_path).replace("/", "\\")

            if gw:
                save_as_wins = gw.getWindowsWithTitle('Save As')
                if save_as_wins:
                    logger.info("Found 'Save As' dialog. Typing path: %s", clean_path)
                    save_as_wins[0].activate()
                    await asyncio.sleep(0.5)
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
        """DEPRECATED: Background task to close browser - Removed to avoid killing user work."""
        logger.info("Auto-close browser disabled to prevent data loss.")
        return


# Global instance
notepad_automation = NotepadAutomation()

# Code templates
HTML_LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JARVIS Unified Interface</title>
    <style>
        body { background: #020617; color: #d1d5db; font-family: 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { padding: 40px; background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(10px); border: 1px solid #1e293b; border-radius: 20px; text-align: center; box-shadow: 0 0 50px rgba(56, 189, 248, 0.1); }
        h1 { color: #38bdf8; margin-bottom: 5px; }
        button { margin-top: 20px; padding: 10px 30px; background: #38bdf8; color: black; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; }
    </style>
</head>
<body>
    <div class="card">
        <h1>JARVIS LOGIN</h1>
        <p>Security Identity: Verified</p>
        <button id="mainBtn">ACTIVATE SYSTEM</button>
    </div>
    <script>
        document.getElementById('mainBtn').onclick = () => { alert('JARVIS: System is already at peak performance, Sir Matloob.'); };
    </script>
</body>
</html>'''

PYTHON_HELLO_TEMPLATE = '''# Simple Python Hello World Program
print("Hello World from JARVIS!")
print("=" * 40)
'''

HEART_ANIMATION_TEMPLATE = '''# heart animation Amazing Moment
import math
from turtle import *
def hearta(k): return 15 * math.sin(k) ** 3
def heartb(k): return 12 * math.cos(k) - 5 * math.cos(2 * k) - 2 * math.cos(3 * k) - math.cos(4 * k)
speed(0)
bgcolor("black")
for i in range(400):
    goto(hearta(i) * 20, heartb(i) * 20)
    color("red")
    dot()
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


async def _safe_notify(notify_func, *args, **kwargs):
    """Helper to run notifications in the background to prevent blocking critical logic."""
    try:
        # Don't await directly in the main flow if the bridge is laggy
        t = asyncio.create_task(notify_func(*args, **kwargs))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    except Exception:
        pass


async def _maybe_await(value):
    """Await async collaborators while tolerating sync mocks and fallbacks."""
    if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
        return await value
    return value


async def _process_notepad_automation(content: str, filename: str, auto_run: bool) -> dict[str, Any]:
    """Unified logic for writing and running code via Notepad."""
    try:
        # Step 1: Initialize File
        await _safe_notify(notify_thinking, "Sir, main file ki details prepare kar raha hoon...")
        success, full_path = await _maybe_await(notepad_automation.save_file_safely("", filename))
        if not success:
            return {"status": "error", "message": f"❌ File initialization failed: {full_path}"}

        await _safe_notify(notify_tool_action, "notepad", f"File '{filename}' initialized.")
        await asyncio.sleep(0.5)

        # Step 2: Open and Focus Notepad
        await _safe_notify(notify_thinking, "Sir, Notepad open karke focus set kar raha hoon...")
        subprocess.Popen([r"C:\Windows\System32\notepad.exe", full_path])

        try:
            if await _maybe_await(notepad_automation.ensure_notepad_focus()):
                await _safe_notify(notify_thinking, "Sir, aapka code Notepad mein type kar raha hoon...")
                typed = await _maybe_await(notepad_automation.simulate_typing(content))
                if not typed:
                    raise RuntimeError("Typing failed")

                await _safe_notify(notify_thinking, "Sir, code save karke Notepad close kar raha hoon...")
                pyautogui.hotkey('ctrl', 's')
                await _maybe_await(notepad_automation.handle_save_as_dialog(full_path))
                await asyncio.sleep(1.0)
                await _maybe_await(notepad_automation.close_active_notepad())
                msg = f"Code successfully written to '{filename}' using Notepad focus logic.\n"
            else:
                await _maybe_await(notepad_automation.save_file_safely(content, filename))
                msg = "Notepad focus failed. Focus failed. File saved programmatically.\n"
        except Exception as gui_error:
            logger.warning("GUI automation failed, saving directly: %s", gui_error)
            await _maybe_await(notepad_automation.save_file_safely(content, filename))
            msg = "GUI automation failed. File saved programmatically.\n"

        if auto_run:
            await _safe_notify(notify_thinking, f"Sir, ab main {filename} ko execute kar raha hoon...")
            if filename.endswith('.html'):
                import webbrowser
                webbrowser.open(full_path)
                msg += "HTML page browser mein open ho gayi hai."
            elif filename.endswith('.py'):
                subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', 'python', full_path])
                msg += "Python script run ho rahi hai."

        return {
            "status": "success",
            "filename": filename,
            "path": full_path,
            "message": msg,
        }
        
        if await notepad_automation.ensure_notepad_focus():
            # Step 3: Type Code (Visible)
            await _safe_notify(notify_thinking, "Sir, aapka code Notepad mein type kar raha hoon...")
            await notepad_automation.simulate_typing(content)
            
            # Step 4: Save and Close
            await _safe_notify(notify_thinking, "Sir, code save karke Notepad close kar raha hoon...")
            pyautogui.hotkey('ctrl', 's')
            await notepad_automation.handle_save_as_dialog(full_path)
            await asyncio.sleep(1.0)
            await notepad_automation.close_active_notepad()
            msg = f"✅ Code successfully written to '{filename}' using Notepad focus logic.\n"
        else:
            # Fallback
            await notepad_automation.save_file_safely(content, filename)
            msg = "⚠️ GUI focus failed. File saved programmatically.\n"

        # Step 5: Run Code
        if auto_run:
            await _safe_notify(notify_thinking, f"Sir, ab main {filename} ko execute kar raha hoon...")
            if filename.endswith('.html'):
                import webbrowser
                webbrowser.open(full_path)
                msg += "🌐 HTML page browser mein open ho gayi hai."
            elif filename.endswith('.py'):
                subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', 'python', full_path])
                msg += "🐍 Python script run ho rahi hai."

        return {
            "status": "success",
            "filename": filename,
            "path": full_path,
            "message": msg,
        }
    except Exception as e:
        logger.exception("Notepad process error: %s", e)
        return {"status": "error", "message": f"❌ Error: {e}"}


@jarvis_tool(execution_timeout=45.0)
async def create_template_code(code_type: str, filename: str = "", auto_run: bool = True) -> dict[str, Any]:
    """
    Sir, is tool ki madad se main template code (HTML/Python) Notepad mein likh kar automatically run kar deta hoon.
    Har step par main aapko inform karunga.
    """
    try:
        content, filename = get_template_content(code_type, filename)
    except Exception as e:
        logger.exception("create_template_code error: %s", e)
        return {"status": "error", "message": f"Error: {e}"}
    if not content:
        return {"status": "error", "message": "❌ Unsupported code type. Use 'html_login' or 'python_hello'."}
    
    return await _process_notepad_automation(content, filename, auto_run)


@jarvis_tool(execution_timeout=45.0)
async def write_custom_code(content: str, filename: str, auto_run: bool = True) -> dict[str, Any]:
    """
    Sir, aap jo bhi code kahenge, main use Notepad mein manually type karke run karunga.
    Aapko har action real-time mein nazar ayega.
    """
    if not filename:
        return {"status": "error", "message": "❌ Filename is required."}
    
    return await _process_notepad_automation(content, filename, auto_run)


@jarvis_tool
async def run_cmd_command(command: str) -> dict[str, Any]:
    """Execute a CMD command (Non-interactive) safely."""
    try:
        # Sanitize and run via list to prevent injection
        cmd_list = ["cmd", "/c", "start", "cmd", "/k"] + shlex.split(command)
        # Run in thread to prevent blocking
        await asyncio.to_thread(subprocess.Popen, cmd_list)
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
    def _launch():
        try:
            # Try absolute path first
            return subprocess.Popen([r"C:\Windows\System32\notepad.exe"])
        except FileNotFoundError:
            # Fallback to generic command
            return subprocess.Popen(["notepad"])

    try:
        # Run process launch in background thread to avoid blocking main loop
        await asyncio.to_thread(_launch)

        # Notify UI about Notepad open
        t = asyncio.create_task(notify_tool_action(
            "notepad", "Opened blank instance"))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
        return {
            "status": "success",
            "message": "✅ Notepad opened successfully"
        }
    except (subprocess.SubprocessError, OSError) as e:
        logger.exception("open_notepad_simple error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error: {e!s}",
        }
@jarvis_tool
async def list_notepad_files_tool() -> dict:
    """
    Lists all files in the Jarvis_Outputs directory created by JARVIS.
    Use this to 'remember' which text or code files were created.
    """
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        folder_path = os.path.join(project_root, "Jarvis_Outputs")
        
        if not os.path.exists(folder_path):
            return {"status": "empty", "message": "Sir, abhi tak koi Notepad files create nahi hui hain."}
            
        files = []
        for f in os.listdir(folder_path):
            full_path = os.path.join(folder_path, f)
            if os.path.isfile(full_path):
                mtime = os.path.getmtime(full_path)
                files.append({
                    "name": f,
                    "path": full_path,
                    "created": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)),
                    "timestamp": mtime
                })
        
        files.sort(key=lambda x: x["timestamp"], reverse=True)
        
        if not files:
            return {"status": "empty", "message": "Jarvis_Outputs folder khali hai."}
            
        msg = f"Sir, mujhe {len(files)} files mili hain:\n"
        for i, f in enumerate(files[:10], 1):
            msg += f"{i}. {f['name']} ({f['created']})\n"
            
        return {"status": "success", "files": files, "message": msg}
        
    except Exception as e:
        logger.error("Error listing notepad files: %s", e)
        return {"status": "error", "message": f"Files list karne mein error aaya: {e}"}


@jarvis_tool
async def edit_notepad_file_tool(filename: str, new_content: str) -> dict:
    """
    Opens an existing file in Notepad, REPLACES all text with new_content, and saves it.
    Use this when the user says "is file mein ye likh do" or "old text ko change kar do".
    """
    mgr = get_notepad_mgr()
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    folder_path = os.path.join(project_root, "Jarvis_Outputs")
    full_path = os.path.join(folder_path, filename)
    
    if not os.path.exists(full_path):
        return {"status": "error", "message": f"❌ File '{filename}' nahi mili."}

    logger.info("Opening file for editing: %s", full_path)
    subprocess.Popen(["notepad.exe", full_path])
    
    if await mgr.ensure_notepad_focus():
        # Select All and Delete
        pyautogui.hotkey('ctrl', 'a')
        pyautogui.press('backspace')
        
        # Type and Save
        await mgr.simulate_typing(new_content)
        pyautogui.hotkey('ctrl', 's')
        
        return {"status": "success", "message": f"✅ File '{filename}' successfully update ho gayi hai."}
    
    return {"status": "error", "message": "Notepad focus nahi ho saka."}


@jarvis_tool
async def append_notepad_file_tool(filename: str, append_text: str) -> dict:
    """
    Opens an existing file in Notepad, adds text at the bottom, and saves it.
    Use this when the user says "is file ke end mein ye add kar do".
    """
    mgr = get_notepad_mgr()
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    folder_path = os.path.join(project_root, "Jarvis_Outputs")
    full_path = os.path.join(folder_path, filename)
    
    if not os.path.exists(full_path):
        return {"status": "error", "message": f"❌ File '{filename}' nahi mili."}

    logger.info("Opening file for appending: %s", full_path)
    subprocess.Popen(["notepad.exe", full_path])
    
    if await mgr.ensure_notepad_focus():
        # Go to end
        pyautogui.hotkey('ctrl', 'end')
        pyautogui.press('enter')
        
        # Type and Save
        await mgr.simulate_typing(append_text)
        pyautogui.hotkey('ctrl', 's')
        
        return {"status": "success", "message": f"✅ Text successfully append kar diya gaya hai."}
    
    return {"status": "error", "message": "Notepad focus nahi ho saka."}


@jarvis_tool
async def close_notepad_tool() -> dict:
    """
    Closes the active Notepad window.
    """
    mgr = get_notepad_mgr()
    if await mgr.close_active_notepad(force=True):
        return {"status": "success", "message": "✅ Notepad closed."}
    return {"status": "error", "message": "Notepad close nahi ho saka."}
