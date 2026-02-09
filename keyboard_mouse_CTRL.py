import pyautogui
import asyncio
import time
import os
from datetime import datetime
from pynput.keyboard import Key, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from typing import List
from livekit.agents import function_tool
# from langchain.tools import tool
import codecs

# ---------------------
# SafeController Class
# ---------------------
class SafeController:
    def __init__(self):
        self.active = False
        self.activation_time = None
        self.keyboard = KeyboardController()
        self.mouse = MouseController()
        self.valid_keys = set("abcdefghijklmnopqrstuvwxyz1234567890")
        self.special_keys = {
            "enter": Key.enter, "space": Key.space, "tab": Key.tab,
            "shift": Key.shift, "ctrl": Key.ctrl, "alt": Key.alt,
            "esc": Key.esc, "backspace": Key.backspace, "delete": Key.delete,
            "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
            "caps_lock": Key.caps_lock, "cmd": Key.cmd, "win": Key.cmd,
            "home": Key.home, "end": Key.end,
            "page_up": Key.page_up, "page_down": Key.page_down
        }

    async def _get_volume_interface(self):
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            from ctypes import cast, POINTER

            devices = AudioUtilities.GetSpeakers()
            if hasattr(devices, 'volume'):
                return devices.volume
            
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        except Exception:
            try:
                device_enumerator = AudioUtilities.GetDeviceEnumerator()
                default_device = device_enumerator.GetDefaultAudioEndpoint(0, 1)
                interface = default_device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                return cast(interface, POINTER(IAudioEndpointVolume))
            except Exception as e:
                self.log(f"Volume interface error: {e}")
                return None

    def resolve_key(self, key):
        return self.special_keys.get(key.lower(), key)

    def log(self, action: str):
        with open("control_log.txt", "a") as f:
            f.write(f"{datetime.now()}: {action}\n")

    def activate(self, token=None):
        env_token = os.getenv("CONTROLLER_TOKEN")
        if not env_token or token != env_token:
            self.log("Activation attempt failed.")
            return
        self.active = True
        self.activation_time = time.time()
        self.log("Controller auto-activated.")

    def deactivate(self):
        self.active = False
        self.log("Controller auto-deactivated.")

    def is_active(self):
        return self.active

    async def move_cursor(self, direction: str, distance: int = 100):
        if not self.is_active(): return "🛑 Controller is inactive."
        x, y = self.mouse.position
        if direction == "left": self.mouse.position = (x - distance, y)
        elif direction == "right": self.mouse.position = (x + distance, y)
        elif direction == "up": self.mouse.position = (x, y - distance)
        elif direction == "down": self.mouse.position = (x, y + distance)
        await asyncio.sleep(0.2)
        self.log(f"Mouse moved {direction}")
        return f"🖱️ Moved mouse {direction}."

    async def mouse_click(self, button: str = "left"):
        if not self.is_active(): return "🛑 Controller is inactive."
        if button == "left": self.mouse.click(Button.left, 1)
        elif button == "right": self.mouse.click(Button.right, 1)
        elif button == "double": self.mouse.click(Button.left, 2)
        await asyncio.sleep(0.2)
        self.log(f"Mouse clicked: {button}")
        return f"🖱️ {button.capitalize()} click."

    async def scroll_cursor(self, direction: str, amount: int = 10):
        if not self.is_active(): return "🛑 Controller is inactive."
        try:
            if direction == "up": self.mouse.scroll(0, amount)
            elif direction == "down": self.mouse.scroll(0, -amount)
        except:
            pyautogui.scroll(amount * 100)
        await asyncio.sleep(0.2)
        self.log(f"Mouse scrolled {direction}")
        return f"🖱️ Scrolled {direction}"

    async def type_text(self, text: str):
        if not self.is_active():
            return "🛑 Controller is inactive."

        # Fix: convert escaped sequences into real ones
        text = codecs.decode(text, "unicode_escape")

        for char in text:
            try:
                if char == "\n":
                    self.keyboard.press(Key.enter)
                    self.keyboard.release(Key.enter)
                elif char == "\t":
                    self.keyboard.press(Key.tab)
                    self.keyboard.release(Key.tab)
                elif char.isprintable():
                    self.keyboard.press(char)
                    self.keyboard.release(char)
                else:
                    continue

                await asyncio.sleep(0.05)

            except Exception:
                continue

        self.log(f"Typed text: {text}")
        return f"⌨️ Typed: {text}"

    async def press_key(self, key: str):
        if not self.is_active(): return "🛑 Controller is inactive."
        if key.lower() not in self.special_keys and key.lower() not in self.valid_keys:
            return f"❌ Invalid key: {key}"
        k = self.resolve_key(key)
        try:
            self.keyboard.press(k)
            self.keyboard.release(k)
        except Exception as e:
            return f"❌ Failed key: {key} — {e}"
        await asyncio.sleep(0.2)
        self.log(f"Pressed key: {key}")
        return f"⌨️ Key '{key}' pressed."

    async def press_hotkey(self, keys: List[str]):
        if not self.is_active(): return "🛑 Controller is inactive."
        resolved = []
        for k in keys:
            if k.lower() not in self.special_keys and k.lower() not in self.valid_keys:
                return f"❌ Invalid key in hotkey: {k}"
            resolved.append(self.resolve_key(k))

        for k in resolved: self.keyboard.press(k)
        for k in reversed(resolved): self.keyboard.release(k)
        await asyncio.sleep(0.3)
        self.log(f"Pressed hotkey: {' + '.join(keys)}")
        return f"⌨️ Hotkey {' + '.join(keys)} pressed."

    async def control_volume(self, action: str):
        if not self.is_active(): return "🛑 Controller is inactive."
        
        volume = await self._get_volume_interface()
        if volume:
            try:
                if action == "mute":
                    volume.SetMute(1, None)
                    self.log("Volume muted")
                    return "🔊 Volume mute kar diya gaya hai."
                elif action == "unmute":
                    volume.SetMute(0, None)
                    self.log("Volume unmuted")
                    return "🔊 Volume unmute kar diya gaya hai."
            except Exception as e:
                self.log(f"Volume action error: {e}")

        # Up/Down or fallback
        if action == "up": pyautogui.press("volumeup")
        elif action == "down": pyautogui.press("volumedown")
        elif action == "mute": pyautogui.press("volumemute")
        
        await asyncio.sleep(0.2)
        self.log(f"Volume control: {action}")
        return f"🔊 Volume {action}."

    async def set_volume_percentage(self, percentage: int):
        if not self.is_active(): return "🛑 Controller is inactive."
        
        volume = await self._get_volume_interface()
        if not volume:
            return "❌ Volume control interface nahi mila."

        try:
            percentage = max(0, min(100, percentage))
            volume.SetMasterVolumeLevelScalar(percentage / 100, None)
            self.log(f"Volume set to {percentage}%")
            return f"🔊 Volume {percentage} percent par set kar diya gaya hai."
        except Exception as e:
            self.log(f"Volume set error: {e}")
            return f"❌ Volume set nahi ho paaya: {e}"

    async def swipe_gesture(self, direction: str):
        if not self.is_active(): return "🛑 Controller is inactive."
        screen_width, screen_height = pyautogui.size()
        x, y = screen_width // 2, screen_height // 2
        try:
            if direction == "up": pyautogui.moveTo(x, y + 200); pyautogui.dragTo(x, y - 200, duration=0.5)
            elif direction == "down": pyautogui.moveTo(x, y - 200); pyautogui.dragTo(x, y + 200, duration=0.5)
            elif direction == "left": pyautogui.moveTo(x + 200, y); pyautogui.dragTo(x - 200, y, duration=0.5)
            elif direction == "right": pyautogui.moveTo(x - 200, y); pyautogui.dragTo(x + 200, y, duration=0.5)
        except Exception:
            pass
        await asyncio.sleep(0.5)
        self.log(f"Swipe gesture: {direction}")
        return f"🖱️ Swipe {direction} done."

controller = SafeController()

async def with_temporary_activation(fn, *args, **kwargs):
    print(f"🔍 TEMP ACTIVATION: {fn.__name__} | args: {args}")
    controller.activate(os.getenv("CONTROLLER_TOKEN"))
    result = await fn(*args, **kwargs)
    await asyncio.sleep(2)
    controller.deactivate()
    return result

@function_tool
async def move_cursor_tool(direction: str, distance: int = 100):

    """
    Temporarily activates the controller and moves the mouse cursor in a specified direction.

    Args:
        direction (str): Direction to move the cursor. Must be one of ["up", "down", "left", "right"].
        distance (int, optional): Number of pixels to move the cursor. Defaults to 100.

    Returns:
        str: A message describing the mouse movement action.

    Note:
        The controller is automatically activated before the action and deactivated afterward.
    """


    return await with_temporary_activation(controller.move_cursor, direction, distance)

@function_tool
async def mouse_click_tool(button: str = "left"):

    """
    Temporarily activates the controller and performs a mouse click.

    Simulates clicking behavior for automation or voice command triggers.

    Args:
        button (str, optional): Type of mouse click to perform.
            Must be one of ["left", "right", "double"]. Defaults to "left".

    Returns:
        str: A message indicating the type of mouse click performed.

    Notes:
        - "double" simulates a double left-click.
        - Useful for GUI automation or hands-free system interaction.
    """


    return await with_temporary_activation(controller.mouse_click, button)

@function_tool
async def scroll_cursor_tool(direction: str, amount: int = 10):

    """
    Scrolls the screen vertically in the specified direction.

    Useful for commands like "scroll down" or "upar karo".

    Args:
        direction (str): The scroll direction. Must be either "up" or "down".
        amount (int, optional): The scroll intensity or number of scroll steps. Defaults to 10.

    Returns:
        str: A message indicating the direction and magnitude of the scroll action.

    Notes:
        - Positive `amount` values scroll further; can be tuned for smooth or fast scrolling.
        - Designed for fuzzy natural language control.
    """


    return await with_temporary_activation(controller.scroll_cursor, direction, amount)

@function_tool
async def type_text_tool(text: str):

    """
    Simulates typing the given text character by character, as if entered manually from a keyboard.

    Useful for commands like "type hello world" or "hello likho".

    Args:
        text (str): The full string to type, including spaces, punctuation, and symbols.

    Returns:
        str: A message confirming the typed input.
    """
    return await with_temporary_activation(controller.type_text, text)

@function_tool
async def press_key_tool(key: str):

    """
    Simulates pressing a single key on the keyboard, like Enter, Esc, or any letter/number.

    Useful for commands like "Enter dabao", "Escape dabao", or "A press karo".

    Args:
        key (str): The name of the key to press (e.g., "enter", "a", "ctrl", "esc").

    Returns:
        str: A message confirming the key press or an error if the key is invalid.
    """


    return await with_temporary_activation(controller.press_key, key)

@function_tool
async def press_hotkey_tool(keys: List[str]):

    """
    Simulates pressing a keyboard shortcut like Ctrl+S, Alt+F4, etc.

    Use this when the user says something like "save karo", "window band karo", 
    or "refresh kar do".

    Args:
        keys (List[str]): List of key names to press together (e.g., ["ctrl", "s"]).

    Returns:
        str: A message indicating which hotkey combination was pressed.
    """


    return await with_temporary_activation(controller.press_hotkey, keys)

@function_tool
async def control_volume_tool(action: str):

    """
    Changes the system volume using keyboard emulation.

    Use this when the user says something like "volume badhao", "mute kar do", 
    or "lower the sound".

    Args:
        action (str): One of ["up", "down", "mute"].

    Returns:
        str: A message confirming the volume change.
    """


    return await with_temporary_activation(controller.control_volume, action)

@function_tool
async def set_volume_tool(percentage: int):
    """
    Sets the system volume to a specific percentage.
    
    Args:
        percentage (int): The volume level to set (0 to 100).
    """
    return await with_temporary_activation(controller.set_volume_percentage, percentage)

@function_tool
async def swipe_gesture_tool(direction: str):

    """
    Simulates a swipe gesture on the screen using the mouse.

    Use this when the user wants to swipe in a direction like up, down, left, or right — 
    for example: "neeche scroll karo", "left swipe karo", or "screen upar karo".

    Args:
        direction (str): One of ["up", "down", "left", "right"].

    Returns:
        str: A message describing the swipe action.
    """


    return await with_temporary_activation(controller.swipe_gesture, direction)

