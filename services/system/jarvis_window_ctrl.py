"""
# Jarvis_window_CTRL.py
Jarvis Window Control Module

Handles opening, closing, and managing windows (minimize, maximize, restore).
Also provides system-level controls like shutdown, restart, and sleep.
"""
# pylint: disable=too-many-lines

import asyncio
import io
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from typing import Any, Iterable

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

# Third-party imports
from fuzzywuzzy import process

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import (
    EXEC_EXTS,
    WindowInfo,
    close_windows_gracefully,
    find_windows,
    force_foreground,
    is_browser_window,
    is_protected_window,
    tool_result,
)
from services.utils import jarvis_delivery
from services.automation.jarvis_whatsapp_automation import whatsapp_bot
from services.automation.keyboard_mouse_ctrl import type_text_tool
from services.utils.jarvis_config import APP_MAPPINGS, FOCUS_TITLES, config
from services.utils.jarvis_logger import setup_logger

# First-party imports
from services.utils.jarvis_win32 import WIN32_ERRORS, pywintypes, win32con, win32gui

try:
    import win32process
except ImportError:  # pragma: no cover
    win32process = None  # type: ignore[assignment]

TASKKILL_EXE = r"C:\Windows\System32\taskkill.exe"

# ===================== LOGGER ===================== #
# Safe stdout reconfigure — only if supported (avoids crash in piped/test environments)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation):
        pass
logger = setup_logger("JARVIS-WINDOW")

pg = None
gw = None


def _load_pg():
    global pg
    if pg is None:
        try:
            import pyautogui as _pg  # pylint: disable=import-outside-toplevel

            pg = _pg
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pyautogui unavailable") from exc
    return pg


def _load_gw():
    global gw
    if gw is None:
        try:
            import pygetwindow as _gw  # pylint: disable=import-outside-toplevel

            gw = _gw
        except Exception:  # pragma: no cover
            gw = False  # type: ignore[assignment]
    return gw if gw is not False else None


def get_windows(title: str):
    mod = _load_gw()
    return mod.getWindowsWithTitle(title) if mod else []


_APP_INDEX_TTL_S = 600.0
_FUZZY_OPEN_MIN = 75
_OFFICE_KEYS = frozenset({"excel", "word", "powerpoint"})
_OFFICE_WEB_SLUG = {"excel": "excel", "word": "word", "powerpoint": "powerpoint"}
_OFFICE_EXE = {"excel": "excel.exe", "word": "winword.exe", "powerpoint": "powerpnt.exe"}
_WHATSAPP_WEB = "https://web.whatsapp.com"
_WHATSAPP_INSTALL_HINT = (
    "WhatsApp Desktop install nahi mila; web.whatsapp.com khol diya. "
    "Microsoft Store se WhatsApp install kar sakte hain."
)
_VLC_MISSING_HINT = "VLC install nahi mila; Windows ka default media player khol diya."
_OFFICE_MISSING_HINT = "Office install nahi mila; browser mein Office on the web khol diya."

_app_index_cache: tuple[float, list["_IndexedApp"]] | None = None
_app_index_lock = threading.Lock()


@dataclass(frozen=True)
class _IndexedApp:
    label: str
    canonical: str
    target: str
    method: str


def _start_menu_program_dirs() -> list[str]:
    dirs: list[str] = []
    for env_key in ("ProgramData", "APPDATA"):
        base = os.environ.get(env_key, "")
        if base:
            dirs.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    return dirs


def _read_app_paths_registry() -> list[_IndexedApp]:
    if winreg is None:
        return []
    out: list[_IndexedApp] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as root:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, idx)
                        idx += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, sub_name) as sub:
                            exe_path, _ = winreg.QueryValueEx(sub, None)
                    except OSError:
                        continue
                    if not exe_path or not isinstance(exe_path, str):
                        continue
                    label = Path(sub_name).stem.lower()
                    if label.endswith(".exe"):
                        label = label[:-4]
                    out.append(_IndexedApp(label, label, exe_path, "app_path"))
        except OSError:
            continue
    return out


def _read_start_apps_json() -> list[_IndexedApp]:
    cmd = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-StartApps | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    raw = (proc.stdout or "").strip()
    if proc.returncode != 0 or not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    out: list[_IndexedApp] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        app_id = str(row.get("AppID") or "").strip()
        if not name or not app_id:
            continue
        canonical = name.lower().strip()
        out.append(_IndexedApp(name, canonical, app_id, "uwp"))
    return out


def _scan_start_menu_lnks() -> list[_IndexedApp]:
    out: list[_IndexedApp] = []
    for root in _start_menu_program_dirs():
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for fname in files:
                if not fname.lower().endswith(".lnk"):
                    continue
                path = os.path.join(dirpath, fname)
                label = os.path.splitext(fname)[0]
                canonical = label.lower().strip()
                out.append(_IndexedApp(label, canonical, path, "lnk"))
    return out


def _build_app_index_sync() -> list[_IndexedApp]:
    seen: set[tuple[str, str, str]] = set()
    entries: list[_IndexedApp] = []

    def _add(entry: _IndexedApp) -> None:
        key = (entry.label.lower(), entry.target, entry.method)
        if key in seen:
            return
        seen.add(key)
        entries.append(entry)

    for map_key, target in APP_MAPPINGS.items():
        _add(_IndexedApp(map_key, map_key, target, "mapping"))

    for chunk in (
        _scan_start_menu_lnks(),
        _read_start_apps_json(),
        _read_app_paths_registry(),
    ):
        for entry in chunk:
            _add(entry)
    return entries


async def _get_app_index() -> list[_IndexedApp]:
    global _app_index_cache
    now = time.monotonic()
    with _app_index_lock:
        if _app_index_cache and now - _app_index_cache[0] < _APP_INDEX_TTL_S:
            return list(_app_index_cache[1])
    built = await asyncio.to_thread(_build_app_index_sync)
    with _app_index_lock:
        _app_index_cache = (now, built)
    return list(built)


def _resolve_mapping_key(clean: str) -> str | None:
    if not clean:
        return None
    keys = list(APP_MAPPINGS.keys())
    match, score = process.extractOne(clean, keys)
    if score >= _FUZZY_OPEN_MIN:
        return match
    return None


def _fuzzy_index_match(clean: str, index: list[_IndexedApp]) -> tuple[_IndexedApp | None, list[str]]:
    if not clean:
        return None, []
    labels = [e.label for e in index]
    if not labels:
        return None, []
    best = process.extractOne(clean, labels)
    if not best:
        return None, []
    best_label, score = best[0], best[1]
    if score < _FUZZY_OPEN_MIN:
        suggestions = [m[0] for m in process.extract(clean, labels, limit=3)]
        return None, suggestions
    for entry in index:
        if entry.label == best_label:
            return entry, []
    return None, []


def _launch_sync(target: str, method: str) -> None:
    """Non-blocking launch in a worker thread (never use 'with Popen')."""
    if method == "uwp":
        subprocess.Popen(  # nosec B603
            ["explorer.exe", f"shell:AppsFolder\\{target}"],
            close_fds=True,
        )
        return
    if method == "lnk" or target.lower().endswith(".lnk"):
        os.startfile(target)  # pylint: disable=no-member
        return
    if target.startswith("http://") or target.startswith("https://"):
        os.startfile(target)  # pylint: disable=no-member
        return
    if os.path.isfile(target):
        subprocess.Popen([target], close_fds=True)  # nosec B603
        return
    subprocess.Popen(shlex.split(target), close_fds=True)  # nosec B603


async def _launch_entry(target: str, method: str) -> None:
    await asyncio.to_thread(_launch_sync, target, method)


def _office_installed(office_key: str, index: list[_IndexedApp]) -> bool:
    want = _OFFICE_EXE.get(office_key, "").lower()
    if not want:
        return False
    for entry in index:
        if entry.method == "app_path" and want in entry.target.lower():
            if os.path.isfile(entry.target):
                return True
        if entry.canonical == office_key and os.path.isfile(entry.target):
            return True
    return False


def _vlc_installed(index: list[_IndexedApp]) -> bool:
    for entry in index:
        if entry.canonical == "vlc":
            return True
        if "vlc" in entry.label.lower() and entry.method in ("app_path", "lnk", "mapping"):
            if entry.method == "mapping":
                return True
            if os.path.isfile(entry.target):
                return True
    return False


def _parse_write_text(full_command: str) -> str | None:
    low_cmd = full_command.lower()
    write_text: str | None = None
    if " write " in f" {low_cmd} ":
        write_text = low_cmd.split(" write ", 1)[1].strip()
    elif " likh " in f" {low_cmd} ":
        write_text = low_cmd.split(" likh ", 1)[1].strip()
        if write_text.startswith("do "):
            write_text = write_text[3:].strip()
    if write_text:
        for w in ("kholo", "karo", "please", "zara"):
            if write_text.endswith(f" {w}"):
                write_text = write_text[: -len(w)].strip()
    return write_text or None


# ===================== UTIL ===================== #


def normalize_command(text: str) -> str:
    """
    Removes Hindi/English open keywords safely and extracts the app name.
    """
    remove_words = [
        "open", "opening", "opened", "run", "launch", "start",
        "kholo", "khol", "karo", "chalao", "chalana",
        "aur", "usmein", "likh", "do", "hello", "jarvis", "what", "are", "you", "doing",
        "browser", "app", "application", "please", "zara",
    ]
    text = text.lower()

    # Use regex with word boundaries to avoid partial word matches
    pattern = r'\b(' + '|'.join(map(re.escape, remove_words)) + r')\b'
    # Replace with space instead of empty to keep words separate
    text = re.sub(pattern, " ", text)

    return " ".join(text.split()).strip()


async def focus_window(target_title: str) -> bool:
    """
    Activates and restores a window by its title via find_windows + force_foreground.
    """
    target = (target_title or "").strip()
    if not target:
        return False

    await asyncio.sleep(1.2)

    for win in _match_windows_by_name(target):
        if is_protected_window(win):
            continue
        try:
            if await force_foreground(win.hwnd):
                await asyncio.sleep(0.5)
                logger.info("Focused window: %s", win.title)
                return True
        except (pywintypes.error, AttributeError, OSError) as exc:  # pylint: disable=no-member
            logger.warning("Focus failed for %s: %s", target_title, exc)
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


def _open_app_risk(arguments: dict[str, Any]) -> str:
    cmd = str(arguments.get("full_command") or "")
    clean = normalize_command(cmd)
    matched_key = _resolve_mapping_key(clean)
    if not matched_key or matched_key not in APP_MAPPINGS:
        return "high"
    app = str(APP_MAPPINGS.get(matched_key, matched_key))
    probe = f"{app} {clean}".lower()
    for ext in EXEC_EXTS:
        if probe.endswith(ext) or ext in clean.lower():
            return "high"
    return "low"


# ===================== OPEN APP ===================== #


@jarvis_tool(timeout=30, exclusive="desktop_input", risk=_open_app_risk)
async def open_app(full_command: str) -> dict[str, Any]:  # pylint: disable=too-many-branches,too-many-return-statements
    """
    Opens a desktop application or URL based on the user's voice command.
    """
    from services.automation.jarvis_youtube_automation import _handle_open as _youtube_handle_open  # pylint: disable=import-outside-toplevel

    try:
        clean = normalize_command(full_command)
        mapping_key = _resolve_mapping_key(clean)
        write_text = _parse_write_text(full_command)
        index = await _get_app_index()
        index_entry, suggestions = _fuzzy_index_match(clean, index)

        logger.info(
            "OPEN → raw='%s' clean='%s' map='%s' index='%s'",
            full_command,
            clean,
            mapping_key,
            index_entry.label if index_entry else None,
        )

        app_key = mapping_key or (index_entry.canonical if index_entry else clean)

        async def _finish(status: str, message: str, launch_method: str) -> dict[str, Any]:
            if write_text:
                return await _handle_write_after_open(app_key, write_text, launch_method)
            return tool_result(
                status,
                message,
                tool="open_app",
                data={"app": app_key, "method": launch_method},
            )

        if mapping_key == "chrome":
            chrome = getattr(config, "chrome_path", None)
            if not chrome:
                return tool_result(
                    "error",
                    "❌ Chrome path set nahi hai (JARVIS_CHROME_PATH).",
                    tool="open_app",
                    data={"app": "chrome", "method": "chrome_path"},
                )
            await asyncio.to_thread(_launch_sync, chrome, "chrome_path")
            return await _finish("success", "🚀 Google Chrome khol diya gaya hai.", "chrome_path")

        if mapping_key == "youtube":
            yt_result = await _youtube_handle_open()
            yt_msg = yt_result.get("message", "YouTube khol diya.") if isinstance(yt_result, dict) else str(yt_result)
            if write_text:
                return await _handle_write_after_open("youtube", write_text, "youtube_handler")
            return tool_result(
                yt_result.get("status", "success") if isinstance(yt_result, dict) else "success",
                yt_msg,
                tool="open_app",
                data={"app": "youtube", "method": "youtube_handler"},
            )

        if mapping_key == "whatsapp":
            opened = await whatsapp_bot.open_whatsapp()
            if opened:
                await whatsapp_bot.ensure_whatsapp_focus()
                return await _finish("success", "🚀 WhatsApp khol diya gaya hai.", "whatsapp_native")
            await _launch_entry(_WHATSAPP_WEB, "url")
            return tool_result(
                "degraded",
                f"⚠ {_WHATSAPP_INSTALL_HINT}",
                tool="open_app",
                hint=_WHATSAPP_INSTALL_HINT,
                data={"app": "whatsapp", "method": "whatsapp_web"},
            )

        if mapping_key in _OFFICE_KEYS and not _office_installed(mapping_key, index):
            slug = _OFFICE_WEB_SLUG[mapping_key]
            url = f"https://www.office.com/launch/{slug}"
            await _launch_entry(url, "url")
            msg = f"⚠ {_OFFICE_MISSING_HINT}"
            return tool_result(
                "degraded",
                msg,
                tool="open_app",
                hint=_OFFICE_MISSING_HINT,
                data={"app": mapping_key, "method": "office_web"},
            )

        if mapping_key == "vlc" and not _vlc_installed(index):
            await asyncio.to_thread(
                _launch_sync,
                r"C:\Program Files\Windows Media Player\wmplayer.exe",
                "exe",
            )
            msg = f"⚠ {_VLC_MISSING_HINT}"
            return tool_result(
                "degraded",
                msg,
                tool="open_app",
                hint=_VLC_MISSING_HINT,
                data={"app": "vlc", "method": "media_default"},
            )

        if index_entry:
            await _launch_entry(index_entry.target, index_entry.method)
            label = index_entry.label
            return await _finish("success", f"🚀 {label} khol diya gaya hai.", index_entry.method)

        if mapping_key and mapping_key in APP_MAPPINGS:
            target = APP_MAPPINGS[mapping_key]
            launch_method = "url" if target.startswith("http") else "mapping"
            await _launch_entry(target, launch_method)
            return await _finish("success", f"🚀 {mapping_key} khol diya gaya hai.", launch_method)

        top3 = suggestions or [m[0] for m in process.extract(clean, [e.label for e in index], limit=3)]
        hint = f"Shayad yeh chahiye tha: {', '.join(top3[:3])}" if top3 else None
        return tool_result(
            "not_found",
            f"❌ '{clean}' ke liye koi app nahi mili.",
            tool="open_app",
            hint=hint,
            data={"app": clean, "method": "none", "suggestions": top3[:3]},
        )

    except WIN32_ERRORS as e:  # pylint: disable=no-member
        logger.error("App open error: %s", e)
        return tool_result(
            "error",
            f"❌ App open nahi ho paaya: {e!s}",
            tool="open_app",
            error=str(e),
            data={"app": full_command, "method": "error"},
        )


async def _handle_write_after_open(app_key: str, write_text: str, launch_method: str) -> dict[str, Any]:
    """Type into the app after launch; read type_text_tool message when dict."""
    await asyncio.sleep(2)
    await focus_window(app_key)
    result = await type_text_tool(write_text)
    if isinstance(result, dict):
        typed = str(result.get("message") or result)
    else:
        typed = str(result)
    return tool_result(
        "success",
        f"🚀 {app_key} khol diya gaya hai aur {typed}",
        tool="open_app",
        data={"app": app_key, "method": launch_method},
    )


@jarvis_tool
async def save_notepad(filename: str = "jarvis_notes.txt") -> dict:
    """
    Saves the content of an open Notepad window into the Jarvis_Outputs folder.
    """
    try:
        folder_path = config.outputs_dir
        os.makedirs(folder_path, exist_ok=True)

        file_path = os.path.join(folder_path, os.path.basename(filename))

        # Try to find Notepad window
        notepad_windows = list(get_windows('Notepad'))
        if not notepad_windows:
            return {
                "status": "error",
                "message": "❌ Sir, maazrat! Notepad window nahi mili.",
            }

        notepad = notepad_windows[0]
        notepad.activate()
        await asyncio.sleep(1)

        # Ctrl+S to trigger Save As
        pyg = _load_pg()
        pyg.hotkey('ctrl', 's')
        await asyncio.sleep(1.5)

        # Type absolute path directly for reliability
        pyg.write(file_path)
        await asyncio.sleep(0.5)
        pyg.press('enter')

        # Handle Overwrite
        await asyncio.sleep(1.0)
        confirm_wins = get_windows('Confirm Save As')
        if confirm_wins:
            pyg.press('y')

        return {
            "status": "success",
            "file_path": file_path,
            "message": f"💾 Notepad file ko '{os.path.basename(file_path)}' ke naam se save kar diya gaya hai, Sir.",
        }

    except WIN32_ERRORS as e:
        logger.error("Notepad save error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Notepad save karne mein masla aaya: {e!s}",
        }


@jarvis_tool
async def open_notepad_file(file_path: str) -> dict:
    """
    Opens a specific text file in Notepad.
    """
    if not os.path.exists(file_path):
        return {
            "status": "error",
            "message": f"❌ Maazrat Sir, file nahi mili: {file_path}",
        }

    try:
        # pylint: disable=consider-using-with
        subprocess.Popen(
            [r"C:\Windows\System32\notepad.exe", file_path])
        return {
            "status": "success",
            "file_path": file_path,
            "message": f"📂 {file_path} ko Notepad mein open kar diya gaya hai.",
        }
    except OSError as e:
        logger.error("open_notepad_file error: %s", e)
        return {
            "status": "error",
            "message": f"❌ File open karne mein error: {e!s}",
            "error": str(e),
        }

# ===================== CLOSE WINDOW ===================== #


def _process_name_for_pid(pid: int) -> str:
    try:
        import psutil  # pylint: disable=import-outside-toplevel

        return psutil.Process(pid).name() or ""
    except Exception:
        return ""


def _hwnd_to_window_info(hwnd: int) -> WindowInfo | None:
    if not win32gui or not win32process or not hwnd:
        return None
    try:
        if not win32gui.IsWindow(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        pid_i = int(pid)
        return WindowInfo(int(hwnd), title, pid_i, _process_name_for_pid(pid_i))
    except Exception:
        return None


def _match_windows_by_name(window_name: str) -> list[WindowInfo]:
    """Resolve visible windows via FOCUS_TITLES + fuzzy app keys + title substring."""
    raw = (window_name or "").strip()
    if not raw:
        return []
    needle = raw.lower()
    matched_key = fuzzy_match_app(needle)
    seen: dict[int, WindowInfo] = {}

    def _take(windows: Iterable[WindowInfo]) -> None:
        for win in windows:
            seen.setdefault(win.hwnd, win)

    if matched_key == "notepad":
        for win in find_windows(title_suffix=" - notepad"):
            if ".py" not in win.title.lower():
                _take([win])
        _take(find_windows("notepad"))
    else:
        focus_title = FOCUS_TITLES.get(matched_key)
        if focus_title:
            _take(find_windows(focus_title))
        if matched_key == "edge":
            _take(find_windows("edge"))

    _take(find_windows(raw))
    if matched_key != needle:
        _take(find_windows(matched_key))
    return list(seen.values())


def _safe_window_matches(window_name: str) -> list[WindowInfo]:
    return [w for w in _match_windows_by_name(window_name) if not is_protected_window(w)]


def _close_window_risk(arguments: dict[str, Any]) -> str:
    if arguments.get("force"):
        return "medium"
    name = str(arguments.get("window_name") or "")
    if "whatsapp" in name.lower():
        return "low"
    matches = _safe_window_matches(name)
    if len(matches) > 1:
        return "medium"
    if any(is_browser_window(w) for w in matches):
        return "medium"
    return "low"


async def _force_kill_still_open(
    still_hwnds: list[int],
    by_hwnd: dict[int, WindowInfo],
) -> list[int]:
    """taskkill /im only for non-protected processes when force=True."""
    remaining: list[int] = []
    killed_im: set[str] = set()
    for hwnd in still_hwnds:
        info = by_hwnd.get(hwnd) or _hwnd_to_window_info(hwnd)
        if info and is_protected_window(info):
            if win32gui and win32gui.IsWindow(hwnd):
                remaining.append(hwnd)
            continue
        exe = (info.process_name if info else "").strip()
        if exe and exe.lower() not in killed_im:
            im = exe if exe.lower().endswith(".exe") else f"{exe}.exe"
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    [TASKKILL_EXE, "/f", "/im", im],
                    check=False,
                    capture_output=True,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.error("taskkill failed for %s: %s", im, exc)
            killed_im.add(exe.lower())
        if win32gui and win32gui.IsWindow(hwnd):
            remaining.append(hwnd)
    return remaining


@jarvis_tool(timeout=15, risk=_close_window_risk)
async def close_window(window_name: str, force: bool = False) -> dict[str, Any]:
    """
    Closes an application WINDOW on the Windows desktop by name (calculator, chrome, notepad, vlc...).
    For the JARVIS automation browser use chrome_browser_close; for Notepad-only use close_notepad_tool.
    """
    original = (window_name or "").strip()
    if not original:
        return tool_result(
            "error",
            "Window ka naam khali hai.",
            tool="close_window",
            data={"closed_titles": [], "still_open": []},
        )

    if "whatsapp" in original.lower():
        closed = await whatsapp_bot.close_whatsapp()
        data = {"closed_titles": ["WhatsApp"] if closed else [], "still_open": [] if closed else ["WhatsApp"]}
        if closed:
            return tool_result("success", "WhatsApp band kar diya gaya hai.", tool="close_window", data=data)
        return tool_result("error", "WhatsApp band nahi ho saka.", tool="close_window", data=data)

    matches = _safe_window_matches(original)
    if not matches:
        return tool_result(
            "not_found",
            f"'{original}' naam ki koi safe window nahi mili.",
            tool="close_window",
            data={"closed_titles": [], "still_open": []},
        )

    by_hwnd = {w.hwnd: w for w in matches}
    close_result = await close_windows_gracefully([w.hwnd for w in matches], wait_s=2.0)
    closed_hwnds = set(close_result.get("closed") or [])
    still_hwnds = list(close_result.get("still_open") or [])

    if force and still_hwnds:
        still_hwnds = await _force_kill_still_open(still_hwnds, by_hwnd)

    closed_titles = [by_hwnd[h].title for h in closed_hwnds if h in by_hwnd]
    still_open = [by_hwnd[h].title for h in still_hwnds if h in by_hwnd]

    data = {"closed_titles": closed_titles, "still_open": still_open}
    if closed_titles and not still_open:
        msg = f"🗑️ {len(closed_titles)} window(s) band kar di: {', '.join(closed_titles[:5])}"
        return tool_result("success", msg, tool="close_window", data=data)
    if closed_titles:
        msg = (
            f"⚠ {original} par close bheja; {len(still_open)} ab bhi open: "
            f"{', '.join(still_open[:5])}"
        )
        return tool_result("warning", msg, tool="close_window", data=data)
    return tool_result(
        "error",
        f"'{original}' ki matching window(s) band nahi ho sakin.",
        tool="close_window",
        data=data,
    )


@jarvis_tool(timeout=15, risk=_close_window_risk)
async def close(window_name: str) -> dict[str, Any]:
    """Same as close_window(window_name): closes a desktop application window by name."""
    return await close_window(window_name)


def _pygetwindow_for_hwnd(hwnd: int) -> Any:
    mod = _load_gw()
    if not mod:
        return None
    try:
        return mod.Win32Window(int(hwnd))
    except Exception:
        return None


async def _resolve_window_target(window_name: str) -> WindowInfo | None:
    name = (window_name or "").strip()
    if not name:
        return None
    if name.lower() == "active":
        if not win32gui:
            return None
        return _hwnd_to_window_info(win32gui.GetForegroundWindow())
    safe = _safe_window_matches(name)
    return safe[0] if safe else None


@jarvis_tool(timeout=10, risk="low")
async def minimize_window(window_name: str = "active") -> dict[str, Any]:
    """Minimizes a window by name, or the foreground window when window_name is 'active'."""
    target = await _resolve_window_target(window_name)
    if target is None:
        return tool_result(
            "not_found",
            "❌ Minimize ke liye koi safe window nahi mili.",
            tool="minimize_window",
        )
    if is_protected_window(target):
        return tool_result(
            "error",
            "❌ Protected window minimize allowed nahi.",
            tool="minimize_window",
        )
    win = _pygetwindow_for_hwnd(target.hwnd)
    if win is None:
        return tool_result(
            "error",
            "❌ pygetwindow available nahi hai.",
            tool="minimize_window",
        )
    try:
        win.minimize()
        label = target.title or window_name
        return tool_result(
            "success",
            f"📉 '{label}' minimize kar di gayi hai, Sir.",
            tool="minimize_window",
            data={"title": target.title, "hwnd": target.hwnd},
        )
    except (pywintypes.error, AttributeError, OSError) as exc:  # pylint: disable=no-member
        logger.error("Minimize Error: %s", exc)
        return tool_result(
            "error",
            f"❌ Window minimize nahi ho paayi: {exc!s}",
            tool="minimize_window",
            error=str(exc),
        )


@jarvis_tool(timeout=10, risk="low")
async def maximize_window(window_name: str = "active") -> dict[str, Any]:
    """Maximizes a window by name, or the foreground window when window_name is 'active'."""
    target = await _resolve_window_target(window_name)
    if target is None:
        return tool_result(
            "not_found",
            "❌ Maximize ke liye koi safe window nahi mili.",
            tool="maximize_window",
        )
    if is_protected_window(target):
        return tool_result(
            "error",
            "❌ Protected window maximize allowed nahi.",
            tool="maximize_window",
        )
    win = _pygetwindow_for_hwnd(target.hwnd)
    if win is None:
        return tool_result(
            "error",
            "❌ pygetwindow available nahi hai.",
            tool="maximize_window",
        )
    try:
        win.maximize()
        label = target.title or window_name
        return tool_result(
            "success",
            f"📈 '{label}' maximize kar di gayi hai, Sir.",
            tool="maximize_window",
            data={"title": target.title, "hwnd": target.hwnd},
        )
    except (pywintypes.error, AttributeError, OSError) as exc:  # pylint: disable=no-member
        logger.error("Maximize Error: %s", exc)
        return tool_result(
            "error",
            f"❌ Window maximize nahi ho paayi: {exc!s}",
            tool="maximize_window",
            error=str(exc),
        )


@jarvis_tool
async def folder_file(path: str) -> dict:
    """
    Opens a specified folder or file on the system.
    """
    try:
        if not path:
            return {"status": "error", "message": "❌ Path empty hai."}

        # Resolve path
        abs_path = os.path.abspath(path)

        low_path = abs_path.lower()
        blocked_keywords = [
            "\\windows\\", "\\system32\\", "\\config\\",
            "pagefile.sys", "ntuser.dat", "desktop.ini",
        ]

        # Drive validation
        if not (low_path.startswith("c:\\") or low_path.startswith("d:\\")):
            return {"status": "error",
                    "message": "❌ Access Denied: Sir, sirf C: aur D: available hain."}

        # System folder validation
        for keyword in blocked_keywords:
            if keyword in low_path:
                logger.warning("🚫 BLOCKED ACCESS ATTEMPT: %s", abs_path)
                return {"status": "error",
                        "message": "❌ Access Denied: System files block hain."}

        if not await asyncio.to_thread(os.path.exists, abs_path):
            return {"status": "error", "message": f"❌ Path nahi mila: {path}"}

        # Open via startfile in thread
        await asyncio.to_thread(os.startfile, abs_path)
        return {
            "status": "success",
            "path": abs_path,
            "message": f"📂 '{os.path.basename(abs_path)}' open ho gaya hai.",
        }
    except (OSError, ValueError, AttributeError) as e:
        logger.error("folder_file error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Open karne mein error: {e!s}",
        }


@jarvis_tool
async def create_folder(folder_name: str):
    """Creates a new folder on the Desktop."""
    try:
        desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        if not await asyncio.to_thread(os.path.exists, desktop):
            desktop = os.path.expanduser("~")

        path = os.path.join(desktop, folder_name)
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)
        return f"✅ Folder '{folder_name}' Desktop par create kar diya gaya hai."
    except OSError as e:
        logger.error("Create folder error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error creating folder: {e!s}",
            "error": str(e),
        }


@jarvis_tool
async def open_outputs_folder(subfolder: str = "") -> dict:
    """
    Opens the Jarvis_Outputs folder or a specific subfolder.
    """
    try:
        base_dir = os.path.join(config.project_root, "Jarvis_Outputs")
        target_path = base_dir

        if subfolder:
            subfolders = {
                "qr": "QR_Codes",
                "image": "Generated_Images",
                "download": "Downloads",
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
            "message": f"📂 {folder_name} folder open kar diya gaya hai.",
        }
    except OSError as e:
        logger.error("Error opening outputs folder: %s", e)
        return {
            "status": "error",
            "message": f"❌ Folder open karne mein error: {e!s}",
            "error": str(e),
        }


@jarvis_tool(timeout=5, risk="low")
async def get_active_window_context() -> dict:
    """
    Retrieves metadata about the currently focused window on the system.
    """
    mod = _load_gw()
    if not mod and not win32gui:
        return {"status": "error", "message": "pygetwindow not available"}

    try:
        active_window = mod.getActiveWindow() if mod else None
        info: WindowInfo | None = None
        if win32gui:
            info = _hwnd_to_window_info(win32gui.GetForegroundWindow())
        if active_window:
            return {
                "status": "success",
                "title": active_window.title,
                "is_maximized": active_window.isMaximized,
                "is_minimized": active_window.isMinimized,
                "pid": info.pid if info else 0,
                "process_name": info.process_name if info else "",
                "message": f"User is focused on: {active_window.title}",
            }
        if info and info.title:
            return {
                "status": "success",
                "title": info.title,
                "is_maximized": False,
                "is_minimized": False,
                "pid": info.pid,
                "process_name": info.process_name,
                "message": f"User is focused on: {info.title}",
            }
        return {"status": "not_found", "message": "No active window detected."}
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error getting window context: %s", e)
        return {"status": "error", "message": str(e)}
