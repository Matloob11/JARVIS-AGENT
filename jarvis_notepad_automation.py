import os
import time
import subprocess
import asyncio
import logging
import pyautogui
from datetime import datetime
from livekit.agents import function_tool

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-NOTEPAD")

# Configure pyautogui
pyautogui.FAILSAFE = True

# Try to import pygetwindow for window focus control
try:
    import pygetwindow as gw
except ImportError:
    gw = None
    logger.warning("pygetwindow not installed. Window focus verification will be limited.")

class NotepadAutomation:
    def __init__(self):
        self.current_file_path = None
        
    async def ensure_notepad_focus(self, timeout: int = 5):
        """
        Waits for Notepad to verify it is the active window.
        Returns True if Notepad is focused, False otherwise.
        """
        if gw is None:
            await asyncio.sleep(2) # Fallback
            return True

        logger.info("Waiting for Notepad to appear and gain focus...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Find windows with "Notepad" in title
                windows = gw.getWindowsWithTitle('Notepad')
                if not windows:
                    await asyncio.sleep(0.5)
                    continue
                
                # Get the mostly likely correct window (usually the last active or just first found)
                notepad = windows[0]
                
                if notepad.isMinimized:
                    notepad.restore()
                
                notepad.activate()
                await asyncio.sleep(0.2)
                
                if notepad.isActive:
                    logger.info("Notepad is active and focused.")
                    return True
                    
            except Exception as e:
                logger.error(f"Error attempting to focus Notepad: {e}")
            
            await asyncio.sleep(0.5)
            
        logger.error("Timed out waiting for Notepad focus.")
        return False

    async def simulate_typing(self, text: str, delay: float = 0.05):
        """Simulate typing text line by line for visual effect"""
        try:
            lines = text.split('\n')
            for line in lines:
                pyautogui.write(line)
                pyautogui.press('enter')
                await asyncio.sleep(delay)  # Wait a bit between lines
            return True
        except Exception as e:
            logger.error(f"Typing simulation failed: {e}")
            return False

        
    async def save_file_safely(self, content, filename, folder_path=None):
        """Save content to a file safely using standard I/O"""
        try:
            if not folder_path:
                # Create a dedicated output folder on Desktop
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
                folder_path = os.path.join(desktop, "JARVIS_Output")
            
            # Ensure folder exists
            os.makedirs(folder_path, exist_ok=True)
            
            full_path = os.path.join(folder_path, filename)
            
            # Write content to file
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
                
            self.current_file_path = full_path
            logger.info(f"File successfully saved: {full_path}")
            return True, full_path
            
        except Exception as e:
            logger.error(f"Error saving file: {e}")
            return False, str(e)
    
    async def open_file_with_app(self, file_path):
        """Open a file with the default associated application (Notepad/Browser/etc)"""
        try:
            if not os.path.exists(file_path):
                return False
            
            # Use os.startfile for Windows to open with default app
            os.startfile(file_path)
            await asyncio.sleep(1)
            return True
            
        except Exception as e:
            logger.error(f"Error opening file: {e}")
            return False
            
    async def close_active_notepad(self):
        """Closes the currently active Notepad window."""
        try:
            logger.info("Closing Notepad window...")
            # Close window using shortcut
            pyautogui.hotkey('alt', 'f4')
            await asyncio.sleep(0.5)
            logger.info("Notepad closed.")
            return True
        except Exception as e:
            logger.error(f"Error closing Notepad: {e}")
            return False

# Global instance
notepad_automation = NotepadAutomation()

# Code templates
HTML_LOGIN_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login Page</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Arial', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.1);
            width: 400px;
            text-align: center;
        }
        .login-container h2 { margin-bottom: 30px; color: #333; }
        .form-group { margin-bottom: 20px; text-align: left; }
        .form-group label { display: block; margin-bottom: 5px; color: #555; font-weight: bold; }
        .form-group input {
            width: 100%; padding: 12px; border: 2px solid #ddd; border-radius: 5px; font-size: 16px;
        }
        .login-btn {
            width: 100%; padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h2>Login</h2>
        <form>
            <div class="form-group">
                <label>Username:</label>
                <input type="text" required>
            </div>
            <div class="form-group">
                <label>Password:</label>
                <input type="password" required>
            </div>
            <button type="submit" class="login-btn">Login</button>
        </form>
    </div>
</body>
</html>'''

PYTHON_HELLO_TEMPLATE = '''# Simple Python Hello World Program
print("Hello World from JARVIS!")
print("=" * 40)
name = input("Enter your name: ")
print(f"Hello {name}! Welcome to Python programming!")
'''

@function_tool
async def create_template_code(code_type: str, filename: str = "", auto_run: bool = True) -> str:
    """
    Create code file, visually type it in Notepad, and optionally Run it.
    
    Args:
        code_type: "html_login", "python_hello", "custom"
        filename: Name for the file
        auto_run: Automatically run the file (Browser for HTML, CMD for Python)
    """
    try:
        logger.info(f"Creating {code_type} code...")
        
        # 1. Get content
        content = ""
        if code_type.lower() == "html_login":
            content = HTML_LOGIN_TEMPLATE
            if not filename: filename = f"login_{int(time.time())}.html"
        elif code_type.lower() == "python_hello":
            content = PYTHON_HELLO_TEMPLATE
            if not filename: filename = f"hello_{int(time.time())}.py"
        else:
            return "❌ Unsupported code type"
            
        # 2. Create EMPTY file initially to establish path
        success, full_path = await notepad_automation.save_file_safely("", filename)
        
        if not success:
            return f"❌ Failed to initialize file: {full_path}"
            
        msg = f"✅ File initialized: {filename}\n"
        
        # 3. Open File in Notepad and Type + Save
        try:
            # Force open in Notepad specifically
            subprocess.Popen(['notepad.exe', full_path])
            
            # Wait for focus verification
            is_ready = await notepad_automation.ensure_notepad_focus()
            
            if is_ready:
                # Type content
                await notepad_automation.simulate_typing(content)
                msg += "📝 Typed code in Notepad.\n"
                
                # SAVE THE FILE (Real Save)
                await asyncio.sleep(0.5)
                pyautogui.hotkey('ctrl', 's')
                logger.info("Sent Ctrl+S to save.")
                await asyncio.sleep(0.5)
                
                # Close Notepad
                await notepad_automation.close_active_notepad()
                
            else:
                msg += "⚠️ Notepad opened but couldn't verify focus. Writing content manually.\n"
                # Fallback: Write content programmatically if GUI interaction failed
                await notepad_automation.save_file_safely(content, filename)
            
        except Exception as e:
            logger.error(f"Failed to automate notepad: {e}")
            msg += "⚠️ GUI automation failed. File saved programmatically.\n"
            # Fallback
            await notepad_automation.save_file_safely(content, filename)
        
        # 4. Auto Run
        if auto_run:
            if filename.endswith('.html'):
                # Open in Browser
                os.startfile(full_path) 
                msg += "🌐 HTML file opened in Browser!"
            elif filename.endswith('.py'):
                # Run in new CMD window so user sees output
                subprocess.Popen(f'start cmd /k python "{full_path}"', shell=True)
                msg += "🐍 Python script running in CMD!"
            else:
                msg += "⚠️ Auto-run not supported for this file type."
                
        return msg
        
    except Exception as e:
        return f"❌ Error: {str(e)}"

@function_tool
async def write_custom_code(content: str, filename: str, auto_run: bool = True) -> str:
    """
    Writes COMPLETELY NEW custom code (Python, HTML, Text, etc.) based on user request.
    
    Use this tool when the user says:
    - "Write a python script to..."
    - "Ek HTML page banao jo..."
    - "Save a text file with..."
    
    Args:
        content: The full code or text content to write.
        filename: The filename with extension (e.g., 'calculator.py', 'index.html').
        auto_run: If True, it will run/open the file immediately after saving. Defaults to True.
    """
    try:
        if not filename:
            return "❌ Filename is required"
            
        # 1. Create EMPTY file
        success, full_path = await notepad_automation.save_file_safely("", filename)
        
        if not success:
            return f"❌ Failed to initialize file: {full_path}"
            
        msg = f"✅ File initialized: {filename}\n"
        
        # 2. Open in Notepad, Type, Save
        try:
            subprocess.Popen(['notepad.exe', full_path])
            
            # Wait for focus
            if await notepad_automation.ensure_notepad_focus():
                await notepad_automation.simulate_typing(content)
                msg += "📝 Typed code in Notepad.\n"
                
                # SAVE
                await asyncio.sleep(0.5)
                pyautogui.hotkey('ctrl', 's')
                await asyncio.sleep(0.5)
                
                # CLOSE
                await notepad_automation.close_active_notepad()
            else:
                 # Fallback
                await notepad_automation.save_file_safely(content, filename)
                msg += "⚠️ Could not safely type in Notepad (focus check failed). Saved programmatically.\n"
                
        except:
             # Fallback
            await notepad_automation.save_file_safely(content, filename)
            pass
        
        # Auto Run
        if auto_run:
            if filename.endswith('.html'):
                os.startfile(full_path)
                msg += "🌐 HTML opened in Browser!"
            elif filename.endswith('.py'):
                subprocess.Popen(f'start cmd /k python "{full_path}"', shell=True)
                msg += "🐍 Python running in CMD!"
                
        return msg
        
    except Exception as e:
        return f"❌ Error: {str(e)}"

@function_tool
async def run_cmd_command(command: str) -> str:
    """
    Execute a CMD command (Non-interactive)
    """
    try:
        subprocess.Popen(f'start cmd /k "{command}"', shell=True)
        return f"✅ Command sent to CMD: {command}"
    except Exception as e:
        return f"❌ Error running command: {str(e)}"

@function_tool
async def open_notepad_simple() -> str:
    """Open a blank Notepad instance"""
    try:
        subprocess.Popen(['notepad.exe'])
        return "✅ Notepad opened"
    except Exception as e:
        return f"❌ Error: {str(e)}"