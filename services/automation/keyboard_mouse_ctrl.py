"""
# keyboard_mouse_ctrl.py
Keyboard and mouse control tools for the JARVIS agent.
"""

# pylint: disable=no-member, protected-access, broad-exception-caught

from __future__ import annotations

import asyncio
import os
import re
import secrets
import time
from ctypes import POINTER, cast
from datetime import datetime
from typing import Any, Callable, Optional

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import (
    WindowInfo,
    clipboard_paste_text,
    desktop_control_enabled,
    is_browser_window,
    is_protected_window,
    tool_result,
)
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-KEYBOARD-MOUSE")

_bg_tasks: set[asyncio.Task[Any]] = set()

_TOKEN_WARNING_EMITTED = False

_WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)")

_CLOSE_COMBO_CORES = (
    frozenset({"alt", "f4"}),
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "q"}),
    frozenset({"ctrl", "shift", "w"}),
)

pyautogui = None
pythoncom = None
pywintypes = None
CLSCTX_ALL = None
AudioUtilities = None
IAudioEndpointVolume = None
KeyboardController = None
MouseController = None
Key = None
Button = None


def _load_pyautogui():
    global pyautogui
    if pyautogui is None:
        try:
            import pyautogui as pg  # pylint: disable=import-outside-toplevel

            pyautogui = pg
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyautogui unavailable") from exc
    return pyautogui


def _load_input_controllers():
    global KeyboardController, MouseController, Key, Button
    if KeyboardController is None:
        try:
            from pynput.keyboard import Controller as _Kb  # pylint: disable=import-outside-toplevel
            from pynput.keyboard import Key as _Key
            from pynput.mouse import Button as _Button
            from pynput.mouse import Controller as _Mb

            KeyboardController = _Kb
            MouseController = _Mb
            Key = _Key
            Button = _Button
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pynput unavailable") from exc


def _load_volume_stack():
    global pythoncom, pywintypes, CLSCTX_ALL, AudioUtilities, IAudioEndpointVolume
    if AudioUtilities is None:
        try:
            import pythoncom as _pythoncom  # pylint: disable=import-outside-toplevel
            import pywintypes as _pywintypes
            from comtypes import CLSCTX_ALL as _CLS
            from pycaw.pycaw import AudioUtilities as _AU
            from pycaw.pycaw import IAudioEndpointVolume as _IA

            pythoncom = _pythoncom
            pywintypes = _pywintypes
            CLSCTX_ALL = _CLS
            AudioUtilities = _AU
            IAudioEndpointVolume = _IA
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("volume stack unavailable") from exc

_KEY_ALIASES = {
    "control": "ctrl",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "pgup": "page_up",
    "pgdn": "page_down",
    "windows": "win",
}


def _init_default_token() -> str:
    global _TOKEN_WARNING_EMITTED
    tok = getattr(config, "controller_token", None) or os.getenv("CONTROLLER_TOKEN")
    if tok:
        return str(tok)
    if not _TOKEN_WARNING_EMITTED:
        logger.warning("CONTROLLER_TOKEN missing; using per-process token")
        _TOKEN_WARNING_EMITTED = True
    return secrets.token_urlsafe(32)


DEFAULT_TOKEN = _init_default_token()


def _normalize_key_name(key: str) -> str:
    k = (key or "").lower().strip()
    return _KEY_ALIASES.get(k, k)


def _is_valid_key_token(key: str) -> bool:
    k = _normalize_key_name(key)
    if len(key) == 1 and key.isprintable():
        return True
    return k in SafeController.BASE_SPECIAL_KEYS or k in SafeController.LETTER_KEYS


def _is_close_combo(keys: list[str]) -> bool:
    norm = frozenset(_normalize_key_name(k) for k in keys)
    return any(core <= norm for core in _CLOSE_COMBO_CORES)


def _maybe_unescape_literals(text: str) -> str:
    if "\n" in text or "\r" in text:
        return text
    if _WINDOWS_PATH_RE.search(text):
        return text
    if "\\n" in text or "\\t" in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t")
    return text


def _process_name_for_pid(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name() or ""
    except Exception:
        return ""


def _foreground_window_info() -> Optional[WindowInfo]:
    try:
        import win32gui
        import win32process
    except Exception:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return WindowInfo(
            hwnd=int(hwnd),
            title=title,
            pid=int(pid),
            process_name=_process_name_for_pid(int(pid)),
        )
    except Exception:
        return None


def _press_hotkey_risk(args: dict[str, Any]) -> str:
    keys = args.get("keys")
    if isinstance(keys, list) and _is_close_combo(keys):
        fg = _foreground_window_info()
        if fg and is_browser_window(fg):
            return "medium"
    return "low"


def _tool_out(tool: str, raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and raw.get("status"):
        if raw.get("tool") is None:
            raw = {**raw, "tool": tool}
        return raw
    if isinstance(raw, tuple) and len(raw) == 2:
        msg, data = raw
        return tool_result("success", str(msg), tool=tool, data=data if isinstance(data, dict) else None)
    if isinstance(raw, str):
        if raw.startswith("❌") or "Invalid key" in raw or "Failed key" in raw:
            return tool_result("error", raw.replace("❌", "").strip(), tool=tool)
        if raw.startswith("🛑"):
            return tool_result(
                "error",
                "Controller abhi active nahi hai.",
                tool=tool,
                error_code="DESKTOP_CONTROL_DISABLED",
            )
        return tool_result("success", raw, tool=tool)
    return tool_result("success", str(raw), tool=tool)


def _close_combo_guard(keys: list[str], tool: str) -> Optional[dict[str, Any]]:
    if not _is_close_combo(keys):
        return None
    fg = _foreground_window_info()
    if fg and is_protected_window(fg):
        return tool_result(
            "error",
            "Yeh shortcut protected window par allowed nahi hai.",
            tool=tool,
            error_code="DESKTOP_CONTROL_DISABLED",
            hint="JARVIS UI ya dev tool focus mein close shortcut refuse hota hai.",
        )
    return None


# ---------------------
# SafeController Class
# ---------------------


class SafeController:
    """
    Controller for safe keyboard and mouse interactions.
    Requires activation token for security.
    """

    LETTER_KEYS = set("abcdefghijklmnopqrstuvwxyz1234567890")

    BASE_SPECIAL_KEYS = {
        "enter",
        "space",
        "tab",
        "shift",
        "ctrl",
        "alt",
        "esc",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
        "caps_lock",
        "cmd",
        "win",
        "home",
        "end",
        "page_up",
        "page_down",
        "insert",
        "menu",
        "print_screen",
        "pause",
        "num_lock",
        "scroll_lock",
        "media_play_pause",
        "media_volume_mute",
        "media_volume_down",
        "media_volume_up",
        "media_next",
        "media_previous",
    }

    def __init__(self):
        self.active = False
        self.activation_time = None
        self.keyboard = None
        self.mouse = None
        self.special_keys: dict[str, Any] = {}
        self._input_ready = False

    def _ensure_input(self) -> None:
        if self._input_ready:
            return
        _load_input_controllers()
        assert KeyboardController is not None and MouseController is not None and Key is not None
        self.keyboard = KeyboardController()
        self.mouse = MouseController()
        self.special_keys = {
            "enter": Key.enter,
            "space": Key.space,
            "tab": Key.tab,
            "shift": Key.shift,
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "esc": Key.esc,
            "backspace": Key.backspace,
            "delete": Key.delete,
            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,
            "caps_lock": Key.caps_lock,
            "cmd": Key.cmd,
            "win": Key.cmd,
            "home": Key.home,
            "end": Key.end,
            "page_up": Key.page_up,
            "page_down": Key.page_down,
            "insert": Key.insert,
            "menu": Key.menu,
            "print_screen": Key.print_screen,
            "pause": Key.pause,
            "num_lock": Key.num_lock,
            "scroll_lock": Key.scroll_lock,
            "media_play_pause": Key.media_play_pause,
            "media_volume_mute": Key.media_volume_mute,
            "media_volume_down": Key.media_volume_down,
            "media_volume_up": Key.media_volume_up,
            "media_next": Key.media_next,
            "media_previous": Key.media_previous,
            "control": Key.ctrl,
            "escape": Key.esc,
            "return": Key.enter,
            "del": Key.delete,
            "pgup": Key.page_up,
            "pgdn": Key.page_down,
            "windows": Key.cmd,
        }
        for i in range(1, 13):
            name = f"f{i}"
            self.special_keys[name] = getattr(Key, name)
            self.BASE_SPECIAL_KEYS.add(name)
        self._input_ready = True

    async def _get_volume_interface(self):
        """
        Retrieves the Windows IAudioEndpointVolume interface for low-level volume control.
        Ensures COM is initialized properly.
        """
        try:
            if AudioUtilities is None:
                _load_volume_stack()
            if pythoncom is None or AudioUtilities is None:
                return None
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass

            devices = AudioUtilities.GetSpeakers()
            endpoint = getattr(devices, "EndpointVolume", None)
            if endpoint is not None:
                return endpoint

            if hasattr(devices, "volume"):
                return devices.volume

            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        except (AttributeError, OSError, Exception) as e:
            logger.warning("Volume interface error: %s", e)
            try:
                device_enumerator = AudioUtilities.GetDeviceEnumerator()
                default_device = device_enumerator.GetDefaultAudioEndpoint(0, 1)
                interface = default_device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                return cast(interface, POINTER(IAudioEndpointVolume))
            except (AttributeError, OSError, Exception) as final_e:
                logger.warning("Volume interface fallback error: %s", final_e)
                return None

    def resolve_key(self, key: str):
        """
        Resolves a string key name to a pynput Key object or returns the original character.
        """
        return self.special_keys.get(_normalize_key_name(key), key)

    def log(self, action: str):
        """
        Logs a controller action (names and lengths only — never typed text or tokens).
        """
        logger.info("Controller Action: %s", action)
        log_dir = os.path.join(getattr(config, "project_root", os.getcwd()), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "control_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now()}: {action}\n")

    def activate(self, token=None):
        """
        Activates the controller if the provided token matches the environment token.
        """
        if token != DEFAULT_TOKEN:
            self.log("activation refused")
            return
        self.active = True
        self.activation_time = time.time()
        self.log("Controller auto-activated.")

    def deactivate(self):
        """
        Deactivates the controller, preventing further inputs.
        """
        self.active = False
        self.log("Controller auto-deactivated.")

    def is_active(self):
        """
        Returns True if the controller is currently active.
        """
        return self.active

    async def move_cursor(self, direction: str, distance: int = 100):
        if not self.is_active():
            return "🛑 Controller is inactive."
        self._ensure_input()
        assert self.mouse is not None
        pg = _load_pyautogui()
        x, y = self.mouse.position
        screen_width, screen_height = pg.size()
        target_x, target_y = x, y

        if direction == "left":
            target_x = max(0, x - distance)
        elif direction == "right":
            target_x = min(screen_width - 1, x + distance)
        elif direction == "up":
            target_y = max(0, y - distance)
        elif direction == "down":
            target_y = min(screen_height - 1, y + distance)

        self.mouse.position = (target_x, target_y)
        await asyncio.sleep(0.2)
        self.log(f"Mouse moved {direction}")
        return f"Mouse {direction} move ho gaya."

    async def mouse_click(self, button: str = "left"):
        if not self.is_active():
            return "🛑 Controller is inactive."
        self._ensure_input()
        assert self.mouse is not None and Button is not None
        if button == "left":
            self.mouse.click(Button.left, 1)
        elif button == "right":
            self.mouse.click(Button.right, 1)
        elif button == "double":
            self.mouse.click(Button.left, 2)
        await asyncio.sleep(0.2)
        self.log(f"Mouse clicked: {button}")
        return f"{button.capitalize()} click ho gaya."

    async def scroll_cursor(self, direction: str, amount: int = 10):
        if not self.is_active():
            return "🛑 Controller is inactive."
        self._ensure_input()
        assert self.mouse is not None
        pg = _load_pyautogui()
        try:
            if direction == "up":
                self.mouse.scroll(0, amount)
            elif direction == "down":
                self.mouse.scroll(0, -amount)
        except (AttributeError, OSError, RuntimeError) as e:
            logger.debug("pynput scroll failed, trying pyautogui: %s", e)
            pg.scroll(amount * 100)
        await asyncio.sleep(0.2)
        self.log(f"Mouse scrolled {direction}")
        return f"Scroll {direction} ho gaya."

    async def type_text(self, text: str, interval: float = 0.0, force_typing: bool = False):
        if not self.is_active():
            return "🛑 Controller is inactive."

        text = _maybe_unescape_literals(text)
        is_unicode = any(ord(char) > 127 for char in text)

        if not force_typing and (len(text) > 50 or is_unicode):
            pasted = await clipboard_paste_text(text, restore=True)
            if pasted:
                reason = "unicode" if is_unicode else "length"
                self.log(f"type_text clipboard ({reason}) len={len(text)}")
                return f"Text paste ho gaya ({len(text)} characters)."
            logger.warning("Clipboard paste failed; falling back to per-char typing")

        self._ensure_input()
        assert self.keyboard is not None and Key is not None
        actual_interval = interval if interval > 0 else 0.02
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
                if actual_interval > 0:
                    await asyncio.sleep(actual_interval)
            except (ValueError, KeyError, AttributeError):
                continue

        self.log(f"type_text typed len={len(text)}")
        return f"Text type ho gaya ({len(text)} characters)."

    async def type_code_animation(self, code: str, interval: float = 0.015):
        if not self.is_active():
            return "🛑 Controller is inactive."
        code = (code or "")[:6000]
        head = code[:1500]
        tail = code[1500:]
        await self.type_text(head, interval=interval, force_typing=True)
        if tail:
            await self.type_text(tail, force_typing=False)
        self.log(f"type_code_animation total len={len(code)}")
        return f"Code animation complete ({len(code)} characters)."

    async def press_key(self, key: str):
        if not self.is_active():
            return "🛑 Controller is inactive."
        self._ensure_input()
        assert self.keyboard is not None
        if not _is_valid_key_token(key):
            return f"❌ Invalid key: {key}"
        k = self.resolve_key(key)
        try:
            self.keyboard.press(k)
            self.keyboard.release(k)
        except (ValueError, KeyError, AttributeError) as e:
            return f"❌ Failed key: {key} — {e}"
        await asyncio.sleep(0.2)
        self.log(f"Pressed key: {_normalize_key_name(key)}")
        return f"Key '{key}' press ho gaya."

    async def press_hotkey(self, keys: list[str]):
        if not self.is_active():
            return "🛑 Controller is inactive."
        self._ensure_input()
        assert self.keyboard is not None
        resolved = []
        for k in keys:
            if not _is_valid_key_token(k):
                return f"❌ Invalid key in hotkey: {k}"
            resolved.append(self.resolve_key(k))

        for k in resolved:
            self.keyboard.press(k)
        for k in reversed(resolved):
            self.keyboard.release(k)
        await asyncio.sleep(0.3)
        combo = " + ".join(_normalize_key_name(k) for k in keys)
        self.log(f"Pressed hotkey: {combo}")
        return f"Hotkey {combo} press ho gaya."

    async def control_volume(self, action: str):
        if not self.is_active():
            return "🛑 Controller is inactive."

        action = (action or "").lower().strip()
        volume = await self._get_volume_interface()
        data: dict[str, Any] = {}

        if volume and action in {"mute", "unmute", "up", "down"}:
            try:
                if action == "mute":
                    volume.SetMute(1, None)
                    self.log("Volume muted")
                    return "Volume mute ho gaya.", data
                if action == "unmute":
                    volume.SetMute(0, None)
                    self.log("Volume unmuted")
                    return "Volume unmute ho gaya.", data
                if action in {"up", "down"}:
                    current = float(volume.GetMasterVolumeLevelScalar())
                    delta = 0.10 if action == "up" else -0.10
                    new_val = max(0.0, min(1.0, current + delta))
                    volume.SetMasterVolumeLevelScalar(new_val, None)
                    pct = int(round(new_val * 100))
                    data["percent"] = pct
                    self.log(f"Volume {action} to {pct}%")
                    return f"Volume ab {pct} percent hai.", data
            except Exception as e:
                if pywintypes is not None and isinstance(e, pywintypes.error):
                    logger.debug("Volume scalar path failed: %s", e)
                else:
                    logger.debug("Volume scalar path failed: %s", e)

        pg = _load_pyautogui()
        if action == "up":
            pg.press("volumeup")
        elif action == "down":
            pg.press("volumedown")
        elif action == "mute":
            pg.press("volumemute")
        else:
            return f"❌ Unknown volume action: {action}", data

        await asyncio.sleep(0.2)
        self.log(f"Volume control fallback: {action}")
        return f"Volume {action} (media key fallback).", data

    async def set_volume_percentage(self, percentage: int):
        if not self.is_active():
            return "🛑 Controller is inactive."

        try:
            _load_volume_stack()
            assert pythoncom is not None
            pythoncom.CoInitialize()
        except Exception:
            pass

        try:
            volume = await self._get_volume_interface()
            if not volume:
                return "❌ Volume control interface nahi mila.", {}

            percentage = max(0, min(100, int(percentage)))
            volume.SetMasterVolumeLevelScalar(percentage / 100, None)
            self.log(f"Volume set to {percentage}%")
            return f"Volume {percentage} percent par set ho gaya.", {"percent": percentage}
        except (AttributeError, ValueError, OSError) as set_e:
            self.log(f"Volume set error: {set_e!s}")
            return f"❌ Volume set nahi ho paaya: {set_e}", {}

    async def swipe_gesture(self, direction: str):
        if not self.is_active():
            return "🛑 Controller is inactive."
        pg = _load_pyautogui()
        screen_width, screen_height = pg.size()
        x, y = screen_width // 2, screen_height // 2
        try:
            if direction == "up":
                pg.moveTo(x, y + 200)
                pg.dragTo(x, y - 200, duration=0.5)
            elif direction == "down":
                pg.moveTo(x, y - 200)
                pg.dragTo(x, y + 200, duration=0.5)
            elif direction == "left":
                pg.moveTo(x + 200, y)
                pg.dragTo(x - 200, y, duration=0.5)
            elif direction == "right":
                pg.moveTo(x - 200, y)
                pg.dragTo(x + 200, y, duration=0.5)
        except (AttributeError, OSError) as e:
            logger.warning("Swipe gesture failed or aborted: %s", e)
        await asyncio.sleep(0.5)
        self.log(f"Swipe gesture: {direction}")
        return f"Swipe {direction} complete ho gaya."


controller = SafeController()


async def _safe_notify(notify_func, *args, **kwargs):
    """Helper to run notifications in the background to prevent blocking critical logic."""
    try:
        t = asyncio.create_task(notify_func(*args, **kwargs))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    except Exception:
        pass


async def with_temporary_activation(
    fn: Callable[..., Any],
    *args: Any,
    tool: str = "",
    **kwargs: Any,
) -> Any:
    """
    Activates the controller temporarily for the duration of a single function call.
    """
    if not desktop_control_enabled():
        return tool_result(
            "error",
            "Desktop control band hai; keyboard/mouse abhi use nahi ho sakta.",
            tool=tool or getattr(fn, "__name__", "desktop_tool"),
            error_code="DESKTOP_CONTROL_DISABLED",
        )

    from services.utils.jarvis_bridge import notify_thinking

    logger.debug(fn.__name__)
    await _safe_notify(notify_thinking, f"Sir, main controller action perform kar raha hoon: {fn.__name__}")
    controller.activate(DEFAULT_TOKEN)
    try:
        return await fn(*args, **kwargs)
    finally:
        controller.deactivate()


@jarvis_tool(timeout=10, risk="low", exclusive="desktop_input")
async def move_cursor_tool(direction: str, distance: int = 100):
    raw = await with_temporary_activation(
        controller.move_cursor, direction, distance, tool="move_cursor_tool"
    )
    return _tool_out("move_cursor_tool", raw)


@jarvis_tool(timeout=10, risk="low", exclusive="desktop_input")
async def mouse_click_tool(button: str = "left"):
    raw = await with_temporary_activation(controller.mouse_click, button, tool="mouse_click_tool")
    return _tool_out("mouse_click_tool", raw)


@jarvis_tool(timeout=10, risk="low", exclusive="desktop_input")
async def scroll_cursor_tool(direction: str, amount: int = 10):
    raw = await with_temporary_activation(
        controller.scroll_cursor, direction, amount, tool="scroll_cursor_tool"
    )
    return _tool_out("scroll_cursor_tool", raw)


@jarvis_tool(timeout=60, risk="low", raw_args=("text",), exclusive="desktop_input")
async def type_text_tool(text: str, interval: float = 0.0, force_typing: bool = False):
    raw = await with_temporary_activation(
        controller.type_text, text, interval, force_typing, tool="type_text_tool"
    )
    return _tool_out("type_text_tool", raw)


@jarvis_tool(timeout=180, risk="low", raw_args=("code",), exclusive="desktop_input")
async def type_code_animation_tool(code: str):
    raw = await with_temporary_activation(
        controller.type_code_animation, code, tool="type_code_animation_tool"
    )
    return _tool_out("type_code_animation_tool", raw)


@jarvis_tool(timeout=10, risk="low", exclusive="desktop_input")
async def press_key_tool(key: str):
    blocked = _close_combo_guard([key], "press_key_tool")
    if blocked:
        return blocked
    raw = await with_temporary_activation(controller.press_key, key, tool="press_key_tool")
    return _tool_out("press_key_tool", raw)


@jarvis_tool(timeout=10, risk=_press_hotkey_risk, exclusive="desktop_input")
async def press_hotkey_tool(keys: list[str]):
    blocked = _close_combo_guard(keys, "press_hotkey_tool")
    if blocked:
        return blocked
    raw = await with_temporary_activation(controller.press_hotkey, keys, tool="press_hotkey_tool")
    return _tool_out("press_hotkey_tool", raw)


@jarvis_tool(timeout=10, risk="low")
async def control_volume_tool(action: str):
    raw = await with_temporary_activation(controller.control_volume, action, tool="control_volume_tool")
    return _tool_out("control_volume_tool", raw)


@jarvis_tool(timeout=10, risk="low")
async def set_volume_tool(percentage: int):
    raw = await with_temporary_activation(
        controller.set_volume_percentage, percentage, tool="set_volume_tool"
    )
    return _tool_out("set_volume_tool", raw)


@jarvis_tool(timeout=10, risk="low", exclusive="desktop_input")
async def swipe_gesture_tool(direction: str):
    raw = await with_temporary_activation(controller.swipe_gesture, direction, tool="swipe_gesture_tool")
    return _tool_out("swipe_gesture_tool", raw)
