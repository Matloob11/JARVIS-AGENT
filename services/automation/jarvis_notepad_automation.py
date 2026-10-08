"""
Jarvis Notepad Automation Module
Handles creating, writing, and running code in Notepad (X-27).
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import (
    WindowInfo,
    _clipboard_paste_text_sync,
    close_windows_gracefully,
    desktop_control_enabled,
    force_foreground,
    run_cancellable,
    safe_child_path,
    tool_result,
    wait_for_window,
)
from services.utils.jarvis_bridge import notify_thinking, notify_tool_action
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-NOTEPAD")

_bg_tasks: set[asyncio.Task[Any]] = set()

NOTEPAD_EXE = r"C:\Windows\System32\notepad.exe"
TASKKILL_EXE = r"C:\Windows\System32\taskkill.exe"

NOTEPAD_ALLOWED_EXTS: frozenset[str] = frozenset({
    ".py", ".html", ".htm", ".txt", ".js", ".css", ".md", ".json", ".bat", ".ps1",
})

AUTO_RUN_MEDIUM_EXTS: frozenset[str] = frozenset({".py", ".bat", ".cmd", ".ps1", ".js"})

_pyautogui = None


def _pyautogui_mod():
    global _pyautogui
    if _pyautogui is None:
        try:
            import pyautogui as pg  # pylint: disable=import-outside-toplevel

            pg.FAILSAFE = True
            _pyautogui = pg
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyautogui is not available on this host") from exc
    return _pyautogui


def _pyautogui_failsafe() -> tuple[type, ...]:
    pg = _pyautogui
    fs = getattr(pg, "FailSafeException", None) if pg is not None else None
    return (fs,) if isinstance(fs, type) else ()


def _outputs_dir() -> str:
    root = getattr(config, "project_root", os.getcwd())
    return str(getattr(config, "outputs_dir", None) or os.path.join(root, "Jarvis_Outputs"))


def _normalize_saved_text(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n")


def _resolve_output_file(filename: str) -> tuple[Optional[Path], Optional[dict[str, Any]]]:
    name = (filename or "").strip()
    if not name:
        return None, tool_result(
            "error",
            "Filename is required.",
            error_code="PATH_NOT_ALLOWED",
        )
    if os.path.basename(name) != name or name in (".", ".."):
        return None, tool_result(
            "error",
            "Sirf basename allowed hai; sub-folder path accept nahi hoti.",
            error_code="PATH_NOT_ALLOWED",
        )
    ext = os.path.splitext(name)[1].lower()
    if ext not in NOTEPAD_ALLOWED_EXTS:
        return None, tool_result(
            "error",
            f"File type {ext!r} is not allowed for Notepad output.",
            error_code="PATH_NOT_ALLOWED",
        )
    child = safe_child_path(_outputs_dir(), name)
    if child is None:
        return None, tool_result(
            "error",
            "Path is not allowed.",
            error_code="PATH_NOT_ALLOWED",
        )
    return child, None


def _type_text(cancel_event: Any, text: str, visible_limit: int = 1500) -> None:
    """Blocking typing worker (run via run_cancellable)."""
    pyautogui = _pyautogui_mod()
    old_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0.02
    try:
        visible = text[:visible_limit]
        remainder = text[visible_limit:]
        lines = visible.split("\n")
        for idx, line in enumerate(lines):
            if cancel_event.is_set():
                return
            if line:
                if line.isascii():
                    pyautogui.write(line, interval=0.005)
                else:
                    _clipboard_paste_text_sync(line)
            if idx < len(lines) - 1:
                pyautogui.press("enter")
        if remainder and not cancel_event.is_set():
            _clipboard_paste_text_sync(remainder)
    finally:
        pyautogui.PAUSE = old_pause


def _write_custom_code_risk(arguments: dict[str, Any]) -> str:
    auto_run = arguments.get("auto_run", True)
    filename = str(arguments.get("filename") or "")
    ext = os.path.splitext(filename)[1].lower()
    if auto_run and ext in AUTO_RUN_MEDIUM_EXTS:
        return "medium"
    return "low"


def _write_custom_code_summary(arguments: dict[str, Any]) -> str:
    filename = str(arguments.get("filename") or "file")
    content = arguments.get("content") or ""
    return f"{filename} Notepad mein likh kar RUN karna hai ({len(content)} chars)"


def _close_notepad_risk(arguments: dict[str, Any]) -> str:
    return "medium" if arguments.get("force", False) else "low"


# --- X-31: run_cmd_command risk + capture helpers ------------------------------------------

# Any of these characters (or a control character) in the raw command disqualifies it from the
# READ_ONLY allow-list, even if the tokens otherwise look safe (e.g. 'echo %GOOGLE_API_KEY%').
_RUN_CMD_FORBIDDEN_CHARS: frozenset[str] = frozenset("%!^&|<>()")

# READ_ONLY allow-list (X-31 Step 2): matched against the *normalized* command (tokens rejoined
# with single spaces), case-insensitively. Only these ever get risk='low' (no confirmation).
_RUN_CMD_READ_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^ipconfig(\s+/all)?$", re.IGNORECASE),
    re.compile(r"^ping\s+\S+$", re.IGNORECASE),
    re.compile(r"^tracert\s+\S+$", re.IGNORECASE),
    re.compile(r"^nslookup\s+\S+$", re.IGNORECASE),
    re.compile(r"^systeminfo$", re.IGNORECASE),
    re.compile(r"^tasklist$", re.IGNORECASE),
    re.compile(r"^whoami$", re.IGNORECASE),
    re.compile(r"^hostname$", re.IGNORECASE),
    re.compile(r"^ver$", re.IGNORECASE),
    re.compile(r"^getmac$", re.IGNORECASE),
    re.compile(r"^netstat\s+-ano$", re.IGNORECASE),
    re.compile(r"^dir\s+\S+$", re.IGNORECASE),
    re.compile(r"^where\s+\S+$", re.IGNORECASE),
    re.compile(r"^python\s+--version$", re.IGNORECASE),
    re.compile(r"^node\s+--version$", re.IGNORECASE),
    # git status|log|diff|branch, optionally with safe (dash-prefixed) flags only.
    re.compile(r"^git\s+(status|log|diff|branch)(\s+-{1,2}\S+)*$", re.IGNORECASE),
)

# Env vars kept for a captured low-risk run; everything else (API keys, tokens, ...) is scrubbed
# so no captured output can ever echo a secret from the agent's own environment.
_RUN_CMD_ENV_KEYS: tuple[str, ...] = ("PATH", "SystemRoot", "COMSPEC", "USERPROFILE", "TEMP", "TMP")

_RUN_CMD_OUTPUT_LIMIT = 4000


def _run_cmd_has_forbidden_chars(command: str) -> bool:
    return any(ch in _RUN_CMD_FORBIDDEN_CHARS or ord(ch) < 32 for ch in command)


# SMB/NTLM leak risk: \\host or //host anywhere in the command (e.g. dir/where args).
_RUN_CMD_UNC_RE = re.compile(r"(\\\\|//)[^\s\\/#]+", re.IGNORECASE)


def _run_cmd_has_unc_path(command: str) -> bool:
    return _RUN_CMD_UNC_RE.search(command) is not None


def _run_cmd_tokens(command: str) -> list[str] | None:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return None


def _run_cmd_token_has_equals(command: str) -> bool:
    """Any token with '=' can redirect/write (e.g. git diff --output=file)."""
    tokens = _run_cmd_tokens(command)
    if tokens is None:
        return True
    return any("=" in token for token in tokens)


def _run_cmd_is_read_only(command: str) -> bool:
    tokens = _run_cmd_tokens(command)
    if tokens is None:
        return False
    normalized = " ".join(tokens)
    return any(pattern.match(normalized) for pattern in _RUN_CMD_READ_ONLY_PATTERNS)


def _run_cmd_risk(arguments: dict[str, Any]) -> str:
    """X-31 Step 2: 'low' only for an exact READ_ONLY allow-list match with no shell metachars."""
    command = str(arguments.get("command") or "").strip()
    if not command:
        return "high"
    if _run_cmd_has_forbidden_chars(command):
        return "high"
    if _run_cmd_has_unc_path(command):
        return "high"
    if _run_cmd_token_has_equals(command):
        return "high"
    if _run_cmd_is_read_only(command):
        return "low"
    return "high"


def _run_cmd_summary(arguments: dict[str, Any]) -> str:
    """X-31 Step 2: the confirmation summary carries the exact command, nothing paraphrased."""
    return str(arguments.get("command") or "")


def _scrubbed_cmd_env() -> dict[str, str]:
    return {key: os.environ[key] for key in _RUN_CMD_ENV_KEYS if key in os.environ}


def _run_cmd_sync(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, cmd.exe does its own parsing
        ["cmd", "/c", command],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=str(Path.home()),
        env=_scrubbed_cmd_env(),
        check=False,
    )


async def _run_cmd_captured(command: str) -> dict[str, Any]:
    """X-31 Step 3: run in a thread with a scrubbed env; stdout+stderr capped at 4000 chars."""
    completed = await asyncio.to_thread(_run_cmd_sync, command)
    output = completed.stdout or ""
    if completed.stderr:
        output = f"{output}\n{completed.stderr}" if output else completed.stderr
    return {"output": output[:_RUN_CMD_OUTPUT_LIMIT], "returncode": completed.returncode}


async def _safe_notify(notify_func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        t = asyncio.create_task(notify_func(*args, **kwargs))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    except Exception:
        pass


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value) or hasattr(value, "__await__"):
        return await value
    return value


def _desktop_disabled(tool: str) -> dict[str, Any]:
    return tool_result(
        "error",
        "Desktop control band hai; Notepad automation abhi use nahi ho sakti.",
        tool=tool,
        error_code="DESKTOP_CONTROL_DISABLED",
    )


class NotepadAutomation:
    """PID-bound Notepad GUI automation."""

    def __init__(self) -> None:
        self.current_file_path: Optional[str] = None
        self._session_hwnd: Optional[int] = None
        self._session_pid: Optional[int] = None

    def clear_session(self) -> None:
        self._session_hwnd = None
        self._session_pid = None

    async def ensure_notepad_focus(
        self,
        *,
        hwnd: Optional[int] = None,
        pid: Optional[int] = None,
        timeout: float = 10.0,
    ) -> bool:
        """Focus only the given Notepad window; never picks an arbitrary Notepad."""
        if hwnd is None and pid is None:
            logger.warning("ensure_notepad_focus refused: no pid/hwnd (X-27)")
            return False

        deadline = time.monotonic() + max(0.0, timeout)
        target_hwnd = int(hwnd) if hwnd is not None else None

        while time.monotonic() < deadline:
            if target_hwnd is None and pid is not None:
                matches = await wait_for_window(pid=int(pid), timeout_s=0.25)
                if matches:
                    target_hwnd = matches.hwnd
            if target_hwnd is not None:
                if await force_foreground(target_hwnd):
                    self._session_hwnd = target_hwnd
                    if pid is not None:
                        self._session_pid = int(pid)
                    return True
            await asyncio.sleep(0.25)

        logger.error("Timed out focusing Notepad hwnd=%s pid=%s", hwnd, pid)
        return False

    async def launch_notepad_for_file(self, full_path: str) -> tuple[bool, Optional[WindowInfo], Optional[int]]:
        """Launch Notepad with a file and bind focus to that PID/window."""
        proc = subprocess.Popen([NOTEPAD_EXE, full_path])
        pid = proc.pid
        basename = os.path.basename(full_path)
        win = await wait_for_window(pid=pid, timeout_s=10.0)
        if win is None:
            win = await wait_for_window(title_suffix=f"{basename} - Notepad", timeout_s=5.0)
        if win is None:
            logger.error("Notepad window not found for pid=%s path=%s", pid, full_path)
            return False, None, pid

        self._session_hwnd = win.hwnd
        self._session_pid = pid
        await force_foreground(win.hwnd)
        focused = await self.ensure_notepad_focus(hwnd=win.hwnd, pid=pid, timeout=5.0)
        return focused, win, pid

    async def simulate_typing(self, text: str) -> bool:
        try:
            await run_cancellable(_type_text, text)
            return True
        except (RuntimeError, AttributeError, asyncio.CancelledError, *_pyautogui_failsafe()) as e:
            logger.exception("Typing simulation failed: %s", e)
            return False

    async def save_file_safely(
        self,
        content: str,
        filename: str,
        folder_path: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            folder = folder_path or _outputs_dir()
            os.makedirs(folder, exist_ok=True)
            child = safe_child_path(folder, os.path.basename(filename))
            if child is None:
                return False, "PATH_NOT_ALLOWED"
            full_path = str(child)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.current_file_path = full_path
            logger.info("File saved: %s", full_path)
            return True, full_path
        except OSError as e:
            logger.error("Error creating file: %s", e)
            return False, str(e)

    async def verify_saved_content(self, full_path: str, expected: str) -> tuple[bool, str]:
        await asyncio.sleep(0.4)
        try:
            with open(full_path, encoding="utf-8") as f:
                disk = f.read()
        except OSError:
            disk = ""
        if _normalize_saved_text(disk) == _normalize_saved_text(expected):
            return True, "verified"
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(expected)
        except OSError as e:
            logger.error("Fallback save failed: %s", e)
            return False, "fallback_failed"
        return True, "fallback"

    async def close_active_notepad(
        self,
        *,
        hwnd: Optional[int] = None,
        pid: Optional[int] = None,
        force_kill_pid: bool = False,
        kill_all_im: bool = False,
    ) -> bool:
        """Close only our Notepad window; kill_all_im is for confirmed close_notepad_tool(force=True)."""
        target_hwnd = hwnd if hwnd is not None else self._session_hwnd
        target_pid = pid if pid is not None else self._session_pid
        try:
            if kill_all_im:
                subprocess.run(
                    [TASKKILL_EXE, "/F", "/IM", "notepad.exe"],
                    check=False,
                    capture_output=True,
                )
                self.clear_session()
                return True

            if target_hwnd is not None:
                await close_windows_gracefully([int(target_hwnd)], wait_s=2.0)

            if force_kill_pid and target_pid is not None:
                await asyncio.sleep(3.0)
                subprocess.run(
                    [TASKKILL_EXE, "/PID", str(int(target_pid)), "/F"],
                    check=False,
                    capture_output=True,
                )

            self.clear_session()
            return target_hwnd is not None or target_pid is not None
        except Exception as e:
            logger.error("Error closing Notepad: %s", e)
            return False

    async def handle_save_as_dialog(self, full_path: str) -> bool:
        """Legacy helper if Save As appears (rare when opening with path)."""
        await asyncio.sleep(0.5)
        return os.path.isfile(full_path)


notepad_automation = NotepadAutomation()


def get_notepad_mgr() -> NotepadAutomation:
    return notepad_automation


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


async def _process_notepad_automation(content: str, filename: str, auto_run: bool) -> dict[str, Any]:
    if not desktop_control_enabled():
        return _desktop_disabled("write_custom_code")

    resolved, err = _resolve_output_file(filename)
    if err is not None:
        return err
    assert resolved is not None
    full_path = str(resolved)
    basename = resolved.name

    try:
        await _safe_notify(notify_thinking, "Sir, main file ki details prepare kar raha hoon...")
        success, init_path = await _maybe_await(notepad_automation.save_file_safely("", basename))
        if not success:
            return tool_result("error", f"File initialization failed: {init_path}")

        await _safe_notify(notify_tool_action, "notepad", f"File '{basename}' initialized.")
        await asyncio.sleep(0.3)

        await _safe_notify(notify_thinking, "Sir, Notepad open karke focus set kar raha hoon...")
        focused, _win, pid = await notepad_automation.launch_notepad_for_file(full_path)
        save_mode = "verified"
        msg = ""

        if focused:
            await _safe_notify(notify_thinking, "Sir, aapka code Notepad mein type kar raha hoon...")
            typed = await _maybe_await(notepad_automation.simulate_typing(content))
            if not typed:
                raise RuntimeError("Typing failed")

            await _safe_notify(notify_thinking, "Sir, code save karke Notepad close kar raha hoon...")
            _pyautogui_mod().hotkey("ctrl", "s")
            await asyncio.sleep(0.5)
            ok, save_mode = await notepad_automation.verify_saved_content(full_path, content)
            if not ok:
                await _maybe_await(notepad_automation.save_file_safely(content, basename))
                save_mode = "fallback"
            await _maybe_await(
                notepad_automation.close_active_notepad(
                    pid=pid,
                    force_kill_pid=True,
                )
            )
            msg = f"Code written to '{basename}' (save: {save_mode}).\n"
        else:
            await _maybe_await(notepad_automation.save_file_safely(content, basename))
            msg = "Notepad focus failed. File saved programmatically.\n"
            save_mode = "programmatic"

        if auto_run:
            await _safe_notify(notify_thinking, f"Sir, ab main {basename} ko execute kar raha hoon...")
            if basename.lower().endswith((".html", ".htm")):
                import webbrowser

                webbrowser.open(Path(full_path).as_uri())
                msg += "HTML page browser mein open ho gayi hai."
            elif basename.lower().endswith(".py"):
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", sys.executable, full_path])
                msg += "Python script run ho rahi hai."

        return tool_result(
            "success",
            msg.strip(),
            data={"filename": basename, "path": full_path, "save_mode": save_mode},
        )
    except Exception as e:
        logger.exception("Notepad process error: %s", e)
        return tool_result("error", f"Error: {e}")


@jarvis_tool(timeout=90, exclusive="desktop_input")
async def create_template_code(code_type: str, filename: str = "", auto_run: bool = True) -> dict[str, Any]:
    """
    Sir, is tool ki madad se main template code (HTML/Python) Notepad mein likh kar automatically run kar deta hoon.
    """
    try:
        content, filename = get_template_content(code_type, filename)
    except Exception as e:
        logger.exception("create_template_code error: %s", e)
        return tool_result("error", f"Error: {e}")
    if not content:
        return tool_result("error", "Unsupported code type. Use 'html_login' or 'python_hello'.")
    _, err = _resolve_output_file(filename)
    if err is not None:
        return err
    return await _process_notepad_automation(content, filename, auto_run)


@jarvis_tool(
    timeout=120,
    raw_args=("content",),
    exclusive="desktop_input",
    risk=_write_custom_code_risk,
    confirm_summary=_write_custom_code_summary,
)
async def write_custom_code(content: str, filename: str, auto_run: bool = True) -> dict[str, Any]:
    """
    Sir, aap jo bhi code kahenge, main use Notepad mein manually type karke run karunga.
    """
    if not filename:
        return tool_result("error", "Filename is required.", error_code="PATH_NOT_ALLOWED")
    return await _process_notepad_automation(content, filename, auto_run)


@jarvis_tool(
    timeout=30,
    raw_args=("command",),
    risk=_run_cmd_risk,
    confirm_summary=_run_cmd_summary,
)
async def run_cmd_command(command: str, capture: bool = False) -> dict[str, Any]:
    """Sir, main CMD command chalata hoon: READ_ONLY allow-list bina confirmation ke, baaqi sab
    sirf Sir ke Allow ke baad (K-45 ConfirmFlow). Captured output ka environment scrub kiya jaata
    hai taake agent ke .env ki koi value kabhi wapas na aaye."""
    if not command or not command.strip():
        return tool_result("error", "Command khali nahi ho sakta.", error_code="EMPTY_COMMAND")

    risk_level = _run_cmd_risk({"command": command})
    try:
        if risk_level == "low" or capture:
            captured = await _run_cmd_captured(command)
            data: dict[str, Any] = {
                "command": command,
                "output": captured["output"],
                "returncode": captured["returncode"],
            }
            if risk_level != "low":
                # Confirmed high-risk command: still open the visible 'cmd /k' window as today,
                # in addition to the captured copy (X-31 Step 4).
                await asyncio.to_thread(
                    subprocess.Popen, ["cmd", "/c", "start", "cmd", "/k", command]
                )
                return tool_result(
                    "success",
                    f"Command CMD window mein chal gaya aur output capture ho gaya: {command}",
                    data=data,
                )
            return tool_result("success", f"Command run ho gaya: {command}", data=data)

        # High risk, capture not requested: open the visible interactive window (today's
        # behaviour). The confirmation itself already happened via ConfirmFlow before this
        # function runs.
        await asyncio.to_thread(subprocess.Popen, ["cmd", "/c", "start", "cmd", "/k", command])
        return tool_result(
            "success", f"Command CMD window mein chal gaya: {command}", data={"command": command}
        )
    except subprocess.TimeoutExpired:
        logger.warning("run_cmd_command timed out: %s", command)
        return tool_result("error", "Command 20 seconds mein complete nahi hua (timeout).", error_code="TIMEOUT")
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.exception("run_cmd_command error: %s", e)
        return tool_result("error", f"Error running command: {e}")


@jarvis_tool(timeout=10, risk="low")
async def open_notepad_simple() -> dict[str, Any]:
    """Launch a blank Notepad window on the laptop for quick manual notes."""

    def _launch() -> subprocess.Popen[Any]:
        try:
            return subprocess.Popen([NOTEPAD_EXE])
        except FileNotFoundError:
            return subprocess.Popen(["notepad"])

    try:
        await asyncio.to_thread(_launch)
        t = asyncio.create_task(notify_tool_action("notepad", "Opened blank instance"))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
        return tool_result("success", "Notepad opened successfully")
    except (subprocess.SubprocessError, OSError) as e:
        logger.exception("open_notepad_simple error: %s", e)
        return tool_result("error", f"Error: {e!s}")


@jarvis_tool(timeout=10, risk="low")
async def list_notepad_files_tool() -> dict[str, Any]:
    """Lists files in Jarvis_Outputs created by JARVIS."""
    try:
        folder_path = _outputs_dir()
        if not os.path.exists(folder_path):
            return tool_result("empty", "Sir, abhi tak koi Notepad files create nahi hui hain.")

        files: list[dict[str, Any]] = []
        for f in os.listdir(folder_path):
            full_path = os.path.join(folder_path, f)
            if os.path.isfile(full_path):
                mtime = os.path.getmtime(full_path)
                files.append({
                    "name": f,
                    "path": full_path,
                    "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                    "timestamp": mtime,
                })

        files.sort(key=lambda x: x["timestamp"], reverse=True)
        if not files:
            return tool_result("empty", "Jarvis_Outputs folder khali hai.")

        msg = f"Sir, mujhe {len(files)} files mili hain:\n"
        for i, f in enumerate(files[:10], 1):
            msg += f"{i}. {f['name']} ({f['created']})\n"

        return tool_result("success", msg.strip(), data={"files": files})
    except Exception as e:
        logger.error("Error listing notepad files: %s", e)
        return tool_result("error", f"Files list karne mein error aaya: {e}")


@jarvis_tool(timeout=90, raw_args=("new_content",), exclusive="desktop_input", risk="low")
async def edit_notepad_file_tool(filename: str, new_content: str) -> dict[str, Any]:
    """Replace all text in an existing Jarvis_Outputs file via Notepad."""
    if not desktop_control_enabled():
        return _desktop_disabled("edit_notepad_file_tool")

    resolved, err = _resolve_output_file(filename)
    if err is not None:
        return err
    assert resolved is not None
    full_path = str(resolved)
    if not os.path.exists(full_path):
        return tool_result("error", f"File '{resolved.name}' nahi mili.")

    bak_path = full_path + ".bak"
    try:
        shutil.copy2(full_path, bak_path)
    except OSError as e:
        return tool_result("error", f"Backup failed: {e}")

    focused, _win, pid = await get_notepad_mgr().launch_notepad_for_file(full_path)
    if not focused:
        return tool_result("error", "Notepad focus nahi ho saka.")

    pg = _pyautogui_mod()
    pg.hotkey("ctrl", "a")
    _clipboard_paste_text_sync(new_content)
    pg.hotkey("ctrl", "s")
    mgr = get_notepad_mgr()
    await mgr.verify_saved_content(full_path, new_content)
    await mgr.close_active_notepad(pid=pid, force_kill_pid=True)
    return tool_result(
        "success",
        f"File '{resolved.name}' successfully update ho gayi hai.",
        data={"backup": bak_path},
    )


@jarvis_tool(timeout=90, raw_args=("append_text",), exclusive="desktop_input", risk="low")
async def append_notepad_file_tool(filename: str, append_text: str) -> dict[str, Any]:
    """Append text at the end of a Jarvis_Outputs file via Notepad."""
    if not desktop_control_enabled():
        return _desktop_disabled("append_notepad_file_tool")

    resolved, err = _resolve_output_file(filename)
    if err is not None:
        return err
    assert resolved is not None
    full_path = str(resolved)
    if not os.path.exists(full_path):
        return tool_result("error", f"File '{resolved.name}' nahi mili.")

    mgr = get_notepad_mgr()
    focused, _win, pid = await mgr.launch_notepad_for_file(full_path)
    if not focused:
        return tool_result("error", "Notepad focus nahi ho saka.")

    pg = _pyautogui_mod()
    pg.hotkey("ctrl", "end")
    pg.press("enter")
    await mgr.simulate_typing(append_text)
    pg.hotkey("ctrl", "s")

    try:
        with open(full_path, encoding="utf-8") as f:
            disk = f.read()
    except OSError:
        disk = ""
    if append_text not in disk:
        disk = disk + append_text
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(disk)
    await mgr.close_active_notepad(pid=pid, force_kill_pid=True)
    return tool_result("success", "Text successfully append kar diya gaya hai.")


@jarvis_tool(timeout=15, risk=_close_notepad_risk)
async def close_notepad_tool(force: bool = False) -> dict[str, Any]:
    """Close JARVIS Notepad session gracefully; force=True kills all Notepad (confirmed)."""
    mgr = get_notepad_mgr()
    if force:
        ok = await mgr.close_active_notepad(kill_all_im=True)
    else:
        ok = await mgr.close_active_notepad(force_kill_pid=False)
    if ok:
        return tool_result("success", "Notepad closed.")
    return tool_result("error", "Notepad close nahi ho saka.")
