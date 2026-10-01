"""
Shared desktop helpers for JARVIS tools (K-25 / X-03).

No import-time side effects: only constants and lazy optional imports.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

# Optional Win32 / input deps — never fail import of this module
try:
    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover
    win32api = None  # type: ignore[assignment]
    win32clipboard = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]
    win32gui = None  # type: ignore[assignment]
    win32process = None  # type: ignore[assignment]

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

try:
    from pynput.keyboard import Controller as _KbController
    from pynput.keyboard import Key as _KbKey
except ImportError:  # pragma: no cover
    _KbController = None  # type: ignore[assignment,misc]
    _KbKey = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXEC_EXTS: frozenset[str] = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".scr", ".lnk", ".msix", ".appx",
})

MEDIA_AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus",
})

MEDIA_VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".mpeg", ".mpg",
})

TEXT_EXTS: frozenset[str] = frozenset({
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".ini", ".log",
    ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".rs", ".go", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".sql", ".toml",
})

_PROTECTED_TITLES: frozenset[str] = frozenset({
    "JARVIS & ANNA",
    "JARVIS-Agent-Worker",
    "JARVIS-AGENT",
    "Program Manager",
})

_PROTECTED_PROCESSES: frozenset[str] = frozenset({
    "cursor.exe",
    "code.exe",
    "windowsterminal.exe",
    "openconsole.exe",
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
})

_BROWSER_PROCESSES: frozenset[str] = frozenset({
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
})

_DRIVE_REMOTE = 4  # DRIVE_REMOTE from winbase.h

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process_name: str


# ---------------------------------------------------------------------------
# ToolResult helper (K-12)
# ---------------------------------------------------------------------------


def tool_result(
    status: str,
    message: str,
    *,
    tool: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
    error_code: Optional[str] = None,
    hint: Optional[str] = None,
    confirm_id: Optional[str] = None,
    **legacy: Any,
) -> dict[str, Any]:
    """Build a standard tool return dict (legacy keys preserved at top level)."""
    out: dict[str, Any] = {"status": status, "message": message}
    if tool is not None:
        out["tool"] = tool
    if data is not None:
        out["data"] = data
    if error_code is not None:
        out["error_code"] = error_code
    if hint is not None:
        out["hint"] = hint
    if confirm_id is not None:
        out["confirm_id"] = confirm_id
    for key, value in legacy.items():
        if value is not None and key not in out:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Window discovery
# ---------------------------------------------------------------------------


def _process_name_for_pid(pid: int) -> str:
    if not psutil or pid <= 0:
        return ""
    try:
        return psutil.Process(pid).name() or ""
    except (psutil.Error, OSError, ValueError):
        return ""


def find_windows(
    title_contains: Optional[str] = None,
    *,
    title_suffix: Optional[str] = None,
    pid: Optional[int] = None,
    visible_only: bool = True,
) -> list[WindowInfo]:
    """Enumerate top-level windows matching optional filters."""
    if not win32gui or not win32process:
        return []

    needle = (title_contains or "").lower()
    suffix = (title_suffix or "").lower()
    results: list[WindowInfo] = []

    def _enum(hwnd: int, _extra: Any) -> None:
        try:
            if visible_only and not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if needle and needle not in title.lower():
                return
            if suffix and not title.lower().endswith(suffix):
                return
            _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if pid is not None and int(wpid) != int(pid):
                return
            results.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    title=title,
                    pid=int(wpid),
                    process_name=_process_name_for_pid(int(wpid)),
                )
            )
        except Exception:
            return

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return []
    return results


async def wait_for_window(
    *,
    pid: Optional[int] = None,
    title_contains: Optional[str] = None,
    title_suffix: Optional[str] = None,
    timeout_s: float = 10.0,
) -> Optional[WindowInfo]:
    """Poll every 0.25 s until a matching window appears or timeout."""
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_s)
    while True:
        matches = find_windows(
            title_contains,
            title_suffix=title_suffix,
            pid=pid,
            visible_only=True,
        )
        if matches:
            return matches[0]
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.25)


# ---------------------------------------------------------------------------
# Focus / close
# ---------------------------------------------------------------------------


def _alt_key_nudge() -> None:
    """Brief Alt tap to satisfy Windows focus-stealing rules."""
    if not win32api or not win32con:
        return
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def force_foreground_sync(hwnd: int) -> bool:
    """Bring hwnd to the foreground. Never raises."""
    if not win32gui or not win32con or not win32process or not win32api:
        return False
    try:
        hwnd = int(hwnd)
        if not win32gui.IsWindow(hwnd):
            return False
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        fg = win32gui.GetForegroundWindow()
        cur_tid = win32api.GetCurrentThreadId()
        fg_tid = 0
        if fg:
            fg_tid, _ = win32process.GetWindowThreadProcessId(fg)

        attached = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached = bool(win32process.AttachThreadInput(fg_tid, cur_tid, True))
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            try:
                _alt_key_nudge()
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(fg_tid, cur_tid, False)
                except Exception:
                    pass

        try:
            return int(win32gui.GetForegroundWindow()) == hwnd
        except Exception:
            return False
    except Exception:
        return False


async def force_foreground(hwnd: int) -> bool:
    return await asyncio.to_thread(force_foreground_sync, hwnd)


def close_windows_gracefully_sync(
    hwnds: Sequence[int],
    wait_s: float = 2.0,
) -> dict[str, list[int]]:
    """Post WM_CLOSE only; wait up to wait_s. Never force-kills."""
    closed: list[int] = []
    still_open: list[int] = []
    if not win32gui or not win32con:
        return {"closed": closed, "still_open": list(int(h) for h in hwnds)}

    targets = [int(h) for h in hwnds]
    for hwnd in targets:
        try:
            if win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            still_open.append(hwnd)

    import time as _time

    deadline = _time.monotonic() + max(0.0, wait_s)
    pending = set(targets)
    while pending and _time.monotonic() < deadline:
        done = [h for h in pending if not win32gui.IsWindow(h)]
        for h in done:
            pending.discard(h)
            closed.append(h)
        if pending:
            _time.sleep(0.05)

    still_open.extend(sorted(pending))
    return {"closed": closed, "still_open": still_open}


async def close_windows_gracefully(
    hwnds: Sequence[int],
    wait_s: float = 2.0,
) -> dict[str, list[int]]:
    return await asyncio.to_thread(close_windows_gracefully_sync, hwnds, wait_s)


# ---------------------------------------------------------------------------
# Window classification
# ---------------------------------------------------------------------------


def is_protected_window(info: WindowInfo) -> bool:
    title = (info.title or "").strip()
    if title in _PROTECTED_TITLES:
        return True
    try:
        own = os.getpid()
        parent = os.getppid()
        if info.pid in (own, parent):
            return True
    except Exception:
        pass
    pname = (info.process_name or "").lower()
    return pname in _PROTECTED_PROCESSES


def is_browser_window(info: WindowInfo) -> bool:
    pname = (info.process_name or "").lower()
    if pname in _BROWSER_PROCESSES:
        return True
    # Some launches report without .exe
    base = pname.replace(".exe", "")
    return base in {"chrome", "msedge", "firefox"}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_in_roots(path: str | os.PathLike[str], roots: Iterable[str | os.PathLike[str]]) -> Optional[Path]:
    """Resolve path and return it only if it stays under one of the roots."""
    try:
        candidate = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if is_network_path(candidate) or is_sensitive_path(candidate):
        return None
    for root in roots:
        try:
            base = Path(root).expanduser().resolve(strict=False)
            candidate.relative_to(base)
            return candidate
        except (ValueError, OSError, RuntimeError):
            continue
    return None


def safe_child_path(base: str | os.PathLike[str], name: str) -> Optional[Path]:
    """Join base/name rejecting absolute paths, drive letters and '..'."""
    raw = str(name or "")
    if not raw or raw.startswith(("/", "\\")):
        return None
    if re.match(r"^[A-Za-z]:", raw):
        return None
    parts = Path(raw).parts
    if any(p in ("..", "") for p in parts):
        return None
    try:
        base_p = Path(base).expanduser().resolve(strict=False)
        child = (base_p / raw).resolve(strict=False)
        child.relative_to(base_p)
        if is_sensitive_path(child) or is_network_path(child):
            return None
        return child
    except (OSError, RuntimeError, ValueError):
        return None


def is_sensitive_path(p: str | os.PathLike[str]) -> bool:
    try:
        path = Path(p)
    except (TypeError, ValueError):
        return True
    parts_lower = [x.lower() for x in path.parts]
    name = path.name.lower()
    # .env*
    if name.startswith(".env"):
        return True
    # .ssh directory segment
    if ".ssh" in parts_lower:
        return True
    # AppData
    if any(x.lower() == "appdata" for x in path.parts):
        return True
    # key material
    if name.endswith((".pem", ".key")):
        return True
    if name.startswith("id_rsa") or name.startswith("id_ed25519"):
        return True
    if name.startswith("credentials") and name.endswith(".json"):
        return True
    return False


def is_network_path(p: str | os.PathLike[str]) -> bool:
    s = str(p)
    if s.startswith("\\\\") or s.startswith("//"):
        return True
    if s.startswith("\\\\?\\") or s.startswith("\\\\.\\"):
        return True
    # NTFS alternate data stream: C:\path\file:stream
    if re.match(r"^[A-Za-z]:[\\/].*:", s):
        return True
    # Mapped remote drive
    if len(s) >= 2 and s[1] == ":" and win32api is not None:
        try:
            root = s[:2] + "\\"
            dtype = ctypes.windll.kernel32.GetDriveTypeW(root)
            if dtype == _DRIVE_REMOTE:
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Config / cancellable
# ---------------------------------------------------------------------------


def desktop_control_enabled() -> bool:
    env = os.environ.get("JARVIS_DESKTOP_CONTROL")
    if env is not None and env.strip() != "":
        return env.strip().lower() not in {"0", "false", "no", "off"}
    try:
        from services.utils.jarvis_config import config  # lazy

        return bool(getattr(config, "desktop_control_enabled", True))
    except Exception:
        return True


async def run_cancellable(fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
    """
    Run sync fn in a worker thread with cancel_event as the first positional arg.
    On CancelledError, set the event and wait up to 5 s for the worker.
    Shield the future so a cancel does not drop the worker mid-type.
    """
    cancel_event = threading.Event()
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, lambda: fn(cancel_event, *a, **kw))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        cancel_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
        raise


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_HEX_B64_RE = re.compile(r"\b[A-Za-z0-9+/=_\-]{24,}\b")
_LONG_DIGITS_RE = re.compile(r"\b\d{11,}\b")


def redact(text: str) -> str:
    if not text:
        return text
    out = _EMAIL_RE.sub("[email]", text)
    out = _HEX_B64_RE.sub("[redacted]", out)
    out = _LONG_DIGITS_RE.sub("[digits]", out)

    # User paths: forward slashes, single/doubled backslashes, %USERPROFILE%
    home = ""
    try:
        home = str(Path.home())
    except Exception:
        home = ""
    patterns: list[str] = [
        r"%USERPROFILE%[^\s\"']*",
        r"(?i)[A-Za-z]:/(?:Users|users)/[^\s\"']+",
        r"(?i)[A-Za-z]:\\(?:Users|users)\\[^\s\"']+",
        r"(?i)[A-Za-z]:\\\\(?:Users|users)\\\\[^\s\"']+",
    ]
    if home:
        esc = re.escape(home)
        patterns.append(esc)
        patterns.append(re.escape(home.replace("\\", "/")))
        patterns.append(re.escape(home.replace("\\", "\\\\")))
    for pat in patterns:
        out = re.sub(pat, "[user_path]", out)
    return out


# ---------------------------------------------------------------------------
# Clipboard paste
# ---------------------------------------------------------------------------


def _clipboard_open_retry(times: int = 5) -> None:
    assert win32clipboard is not None
    last: Exception | None = None
    for _ in range(times):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:
            last = exc
            import time as _time

            _time.sleep(0.05)
    if last:
        raise last


def _clipboard_get_unicode() -> tuple[bool, Optional[str]]:
    """Return (had_text, text_or_none)."""
    assert win32clipboard is not None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return True, data if isinstance(data, str) else str(data)
    except Exception:
        pass
    return False, None


def _clipboard_set_unicode(text: str) -> None:
    assert win32clipboard is not None and win32con is not None
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)


def _paste_ctrl_v() -> None:
    if _KbController is None or _KbKey is None:
        raise RuntimeError("pynput not available")
    kb = _KbController()
    kb.press(_KbKey.ctrl)
    kb.press("v")
    kb.release("v")
    kb.release(_KbKey.ctrl)


def _clipboard_paste_text_sync(text: str, restore: bool = True) -> bool:
    if win32clipboard is None or win32con is None:
        return False
    saved_was_text = False
    saved_text: Optional[str] = None
    try:
        _clipboard_open_retry(5)
        try:
            saved_was_text, saved_text = _clipboard_get_unicode()
            _clipboard_set_unicode(text)
        finally:
            win32clipboard.CloseClipboard()

        _paste_ctrl_v()

        import time as _time

        _time.sleep(0.6)
        # Allow up to 1.5 s total for paste consumers
        deadline = _time.monotonic() + 0.9
        while _time.monotonic() < deadline:
            _time.sleep(0.05)

        if restore and saved_was_text:
            try:
                _clipboard_open_retry(5)
                try:
                    still_ours, current = _clipboard_get_unicode()
                    if still_ours and current == text and saved_text is not None:
                        _clipboard_set_unicode(saved_text)
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass

        try:
            from services.automation import jarvis_clipboard as _jc

            note = getattr(_jc, "note_self_write", None)
            if callable(note):
                note(text)
        except Exception:
            pass
        return True
    except Exception:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return False


async def clipboard_paste_text(text: str, restore: bool = True) -> bool:
    return await asyncio.to_thread(_clipboard_paste_text_sync, text, restore)
