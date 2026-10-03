"""Desktop Node presence and session-lock tracking (plan task C-121 step 1; K-76).

Presence = genuine local input. Low-level keyboard and mouse hooks (WH_KEYBOARD_LL, WH_MOUSE_LL)
record an input event only when its LLKHF_INJECTED / LLMHF_INJECTED flag is clear, so the synthetic
keystrokes and clicks the node's own tools inject never count as the user being present. Lock, unlock
and sleep come from WTS session-change notifications on a hidden message window. Presence never leaves
the node (K-76): it is read here by the policy and is never put in a hello, a notice or desktop_status.

Windows-only pieces (the prototyped user32/kernel32/wtsapi32 calls of _win_api(), the hook thread, the message
loop, WTSRegisterSessionNotification, GetForegroundWindow) are guarded, so this module imports on Linux/CI and
in a plain unit test with no hooks installed. The hook callbacks only record (note_input, the lock flag) and
enqueue the event: Windows removes a WH_KEYBOARD_LL / WH_MOUSE_LL hook without notice when a callback takes longer
than LowLevelHooksTimeout, so the key and lock listeners (the panic hotkey and its state write, the lock pause)
run on a separate dispatcher thread, in arrival order. Tests drive note_input / set_locked / _emit_key directly
(synchronous) or _hook_key / _hook_lock (the hook path), and foreground_title(), foreground_class(),
foreground_process() and foreground_handle() can be pinned for a test. Nothing runs at import.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
import time
import types
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger("JARVIS-DESKTOP.presence")

# Hook event flags (winuser.h): a set bit means the event was injected by SendInput, not a real device.
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01


# The low-level hook structs (winuser.h). They are declared with fixed-width ctypes fields (not wintypes,
# which only imports on Windows) so the layout is byte-for-byte identical wherever this module imports, and
# the two extraction helpers below can be unit-tested with a real struct on Linux/CI without any windll.
# MSLLHOOKSTRUCT begins with a POINT (two 32-bit LONGs), so its `flags` DWORD is the 4th field, NOT the 3rd:
# reading the 3rd field would read `mouseData`, which is 0 for ordinary moves/clicks and would make injected
# detection always False, letting the node's own synthetic input spoof presence.
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_uint32),
        ("scanCode", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt_x", ctypes.c_int32),
        ("pt_y", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _keyboard_hook_event(lparam: int) -> tuple[int, bool]:
    """(virtual-key, injected) read from the KBDLLHOOKSTRUCT a WH_KEYBOARD_LL hook is handed."""
    info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT))[0]
    return int(info.vkCode), bool(info.flags & LLKHF_INJECTED)


def _mouse_hook_injected(lparam: int) -> bool:
    """True when a WH_MOUSE_LL event was injected (SendInput), read from the correct `flags` field."""
    info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT))[0]
    return bool(info.flags & LLMHF_INJECTED)

# Key listeners get the normalised virtual-key, whether the event was injected, and whether it is a press.
KeyListener = Callable[[int, bool, bool], None]
LockListener = Callable[[bool], None]

_WH_KEYBOARD_LL = 13
_WH_MOUSE_LL = 14
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_KEYUP = 0x0101
_WM_SYSKEYUP = 0x0105
_HC_ACTION = 0
# WTS session-change (winuser.h): lock, unlock, logon and logoff.
_WM_WTSSESSION_CHANGE = 0x02B1
_WM_DESTROY = 0x0002
_WTS_SESSION_LOCK = 0x7
_WTS_SESSION_UNLOCK = 0x8
_NOTIFY_FOR_THIS_SESSION = 0
_WM_QUIT = 0x0012
_GWLP_WNDPROC = -4
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_WIN_API: Any = None
_WIN_API_LOCK = threading.Lock()


def _win_api() -> Any:
    """The user32/kernel32/wtsapi32 calls this module makes, with argtypes and restype declared (None off Windows).

    Private WinDLL instances, so these prototypes never change ctypes.windll for other code in the process, and
    pointer-sized values (the module handle, HHOOK, HWND, WPARAM/LPARAM, LRESULT) are never truncated to a C int:
    without them SetWindowsHookExW gets a truncated module handle on 64-bit Windows and CallNextHookEx or
    DefWindowProcW raise on a 64-bit lParam, so the hooks (presence and the panic hotkey) would not work.
    Loading this only declares prototypes; it installs nothing.
    """
    global _WIN_API  # pylint: disable=global-statement
    if sys.platform != "win32":
        return None
    with _WIN_API_LOCK:
        if _WIN_API is not None:
            return _WIN_API
        from ctypes import wintypes  # pylint: disable=import-outside-toplevel

        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        try:
            wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)  # type: ignore[attr-defined]
        except OSError:
            wtsapi32 = None
        lresult = ctypes.c_ssize_t
        hookproc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)  # type: ignore
        wndproc = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,  # type: ignore
                                     wintypes.LPARAM)
        hwnd, dword, boolean = wintypes.HWND, wintypes.DWORD, wintypes.BOOL
        msg_p = ctypes.POINTER(wintypes.MSG)

        def proto(func: Any, argtypes: list[Any], restype: Any) -> None:
            func.argtypes, func.restype = argtypes, restype

        proto(user32.SetWindowsHookExW, [ctypes.c_int, hookproc, wintypes.HINSTANCE, dword], wintypes.HHOOK)
        proto(user32.CallNextHookEx, [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM], lresult)
        proto(user32.UnhookWindowsHookEx, [wintypes.HHOOK], boolean)
        proto(user32.DefWindowProcW, [hwnd, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], lresult)
        proto(user32.CreateWindowExW, [dword, wintypes.LPCWSTR, wintypes.LPCWSTR, dword, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, hwnd, wintypes.HMENU, wintypes.HINSTANCE,
                                       wintypes.LPVOID], hwnd)
        proto(user32.DestroyWindow, [hwnd], boolean)
        # SetWindowLongPtrW exists only in 64-bit user32; 32-bit Windows has SetWindowLongW with the same role.
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        proto(set_long, [hwnd, ctypes.c_int, ctypes.c_void_p], ctypes.c_void_p)
        proto(user32.GetMessageW, [msg_p, hwnd, wintypes.UINT, wintypes.UINT], ctypes.c_int)
        proto(user32.TranslateMessage, [msg_p], boolean)
        proto(user32.DispatchMessageW, [msg_p], lresult)
        proto(user32.PostThreadMessageW, [dword, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], boolean)
        proto(user32.GetForegroundWindow, [], hwnd)
        proto(user32.GetWindowTextLengthW, [hwnd], ctypes.c_int)
        proto(user32.GetWindowTextW, [hwnd, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int)
        proto(user32.GetClassNameW, [hwnd, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int)
        proto(user32.GetWindowThreadProcessId, [hwnd, ctypes.POINTER(dword)], dword)
        proto(kernel32.GetCurrentThreadId, [], dword)
        proto(kernel32.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HMODULE)
        proto(kernel32.OpenProcess, [dword, boolean, dword], wintypes.HANDLE)
        proto(kernel32.QueryFullProcessImageNameW, [wintypes.HANDLE, dword, wintypes.LPWSTR,
                                                    ctypes.POINTER(dword)], boolean)
        proto(kernel32.CloseHandle, [wintypes.HANDLE], boolean)
        if wtsapi32 is not None:
            proto(wtsapi32.WTSRegisterSessionNotification, [hwnd, dword], boolean)
            proto(wtsapi32.WTSUnRegisterSessionNotification, [hwnd], boolean)
        _WIN_API = types.SimpleNamespace(user32=user32, kernel32=kernel32, wtsapi32=wtsapi32, set_long=set_long,
                                         HOOKPROC=hookproc, WNDPROC=wndproc, MSG=wintypes.MSG, DWORD=dword)
        return _WIN_API


class Presence(Protocol):
    """What the policy reads (C-121). No method ever blocks or touches the network."""

    def present_within(self, window_s: float) -> bool:
        """True when genuine local input happened within the last window_s seconds."""

    def is_locked(self) -> bool:
        """True while the Windows session is locked (WTS notifications)."""

    def foreground_title(self) -> str:
        """The current foreground window title (''  when unknown); never logged at INFO."""

    def foreground_class(self) -> str:
        """The foreground window's class name ('' when unknown); used for launch-surface detection."""

    def foreground_process(self) -> str:
        """The foreground window's process image name, lower case (e.g. 'cmd.exe'; '' when unknown)."""

    def foreground_handle(self) -> int:
        """The foreground window handle (HWND as an int; 0 when unknown). Optional: the policy reads it with
        getattr, and a source without it compares the foreground by its protection class only."""


class PresenceMonitor:
    """The live presence source on the laptop; inert and safe to construct anywhere.

    On Windows start() installs the low-level hooks and the WTS listener on a dedicated thread with its
    own message loop. Everywhere else (and if a hook cannot be installed) it stays inert: no genuine
    input is ever seen, so present_within() is False and captures that need presence are refused, which
    fails closed. note_input(), set_locked() and set_foreground_title() let the tests and the hook
    callbacks feed it without any real hook.
    """

    def __init__(self, *, mono: Callable[[], float] = time.monotonic) -> None:
        self._mono = mono
        self._lock = threading.Lock()
        self._last_input: float = 0.0
        self._locked: bool = False
        self._title_override: str | None = None
        self._class_override: str | None = None
        self._process_override: str | None = None
        self._handle_override: int | None = None
        self._key_listeners: list[KeyListener] = []
        self._lock_listeners: list[LockListener] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._hwnd: int = 0
        self._thread_id: int = 0
        self._hooks: list[int] = []
        # ctypes callbacks kept alive on self while the hooks and the WTS window use them
        self._keyboard_cb: Any = None
        self._mouse_cb: Any = None
        self._wndproc: Any = None
        # hook events waiting for the listeners; drained by the dispatcher thread (never the hook thread)
        self._events: queue.SimpleQueue[tuple[Any, ...] | None] = queue.SimpleQueue()
        self._dispatcher: threading.Thread | None = None
        self._dispatcher_guard = threading.Lock()

    # --- the policy-facing reads -----------------------------------------------------------------------------

    def present_within(self, window_s: float) -> bool:
        with self._lock:
            last = self._last_input
        return last > 0.0 and (self._mono() - last) <= max(0.0, float(window_s))

    def last_input_age_s(self) -> float | None:
        with self._lock:
            last = self._last_input
        return None if last <= 0.0 else max(0.0, self._mono() - last)

    def is_locked(self) -> bool:
        with self._lock:
            return self._locked

    def foreground_title(self) -> str:
        with self._lock:
            override = self._title_override
        if override is not None:
            return override
        return _foreground_title_windows()

    def foreground_class(self) -> str:
        with self._lock:
            override = self._class_override
        if override is not None:
            return override
        return _foreground_class_windows()

    def foreground_process(self) -> str:
        with self._lock:
            override = self._process_override
        if override is not None:
            return override
        return _foreground_process_windows()

    def foreground_handle(self) -> int:
        with self._lock:
            override = self._handle_override
        if override is not None:
            return override
        return _foreground_handle_windows()

    # --- fed by the hooks (and by the tests) -----------------------------------------------------------------

    def note_input(self, injected: bool) -> None:
        """A keyboard or mouse event: only a genuine (not injected) one counts as presence."""
        if injected:
            return
        with self._lock:
            self._last_input = self._mono()

    def set_locked(self, locked: bool) -> None:
        with self._lock:
            if self._locked == bool(locked):
                return
            self._locked = bool(locked)
        for callback in list(self._lock_listeners):
            _safe(callback, locked)

    def set_foreground_title(self, title: str | None) -> None:
        """Pins the foreground title (tests); None restores the live Windows query."""
        with self._lock:
            self._title_override = None if title is None else str(title)

    def set_foreground_class(self, win_class: str | None) -> None:
        """Pins the foreground window class (tests); None restores the live Windows query."""
        with self._lock:
            self._class_override = None if win_class is None else str(win_class)

    def set_foreground_process(self, process: str | None) -> None:
        """Pins the foreground process image name (tests); None restores the live Windows query."""
        with self._lock:
            self._process_override = None if process is None else str(process).lower()

    def set_foreground_handle(self, handle: int | None) -> None:
        """Pins the foreground window handle (tests); None restores the live Windows query."""
        with self._lock:
            self._handle_override = None if handle is None else int(handle)

    def add_key_listener(self, callback: KeyListener) -> None:
        """A genuine-key listener (the panic hotkey). Injected events are passed with injected=True."""
        self._key_listeners.append(callback)

    def add_lock_listener(self, callback: LockListener) -> None:
        self._lock_listeners.append(callback)

    def _emit_key(self, vk: int, injected: bool, down: bool) -> None:
        """A key event delivered synchronously to the listeners (the tests; the hook uses _hook_key)."""
        if not injected:
            self.note_input(False)
        self._dispatch_key(vk, injected, down)

    def _dispatch_key(self, vk: int, injected: bool, down: bool) -> None:
        for callback in list(self._key_listeners):
            _safe(callback, vk, injected, down)

    # --- the hook path: record and enqueue only, never a listener, a file or a log write ----------------------

    def _hook_key(self, vk: int, injected: bool, down: bool) -> None:
        """What the WH_KEYBOARD_LL callback calls: presence is recorded and the event queued; the key listeners
        (the panic hotkey, its state write and cancel-all) run on the dispatcher thread."""
        if not injected:
            self.note_input(False)
        self._enqueue(("key", int(vk), bool(injected), bool(down)))

    def _hook_lock(self, locked: bool) -> None:
        """What the WTS window procedure (on the hook thread) calls: the lock flag is recorded at once, so
        is_locked() is right immediately; the lock listeners run on the dispatcher thread."""
        with self._lock:
            if self._locked == bool(locked):
                return
            self._locked = bool(locked)
        self._enqueue(("lock", bool(locked)))

    def _enqueue(self, event: tuple[Any, ...]) -> None:
        self._events.put(event)
        dispatcher = self._dispatcher
        if dispatcher is None or not dispatcher.is_alive():
            self._start_dispatcher()  # normally started by start(); lazily here for a monitor never started

    def _start_dispatcher(self) -> None:
        with self._dispatcher_guard:
            if self._dispatcher is not None and self._dispatcher.is_alive():
                return
            self._dispatcher = threading.Thread(target=self._dispatch_loop, name="desktop-presence-events",
                                                daemon=True)
            self._dispatcher.start()

    def _dispatch_loop(self) -> None:
        while True:
            event = self._events.get()
            if event is None:
                return
            try:
                if event[0] == "key":
                    self._dispatch_key(event[1], event[2], event[3])
                elif event[0] == "lock":
                    for callback in list(self._lock_listeners):
                        _safe(callback, event[1])
            except Exception:  # pylint: disable=broad-exception-caught  # the dispatcher never dies
                logger.exception("Presence event dispatch raised")

    def _stop_dispatcher(self) -> None:
        with self._dispatcher_guard:
            dispatcher, self._dispatcher = self._dispatcher, None
        if dispatcher is None:
            return
        self._events.put(None)
        dispatcher.join(timeout=2.0)

    # --- lifecycle -------------------------------------------------------------------------------------------

    def start(self) -> PresenceMonitor:
        """Installs the Windows hooks on their own thread; a no-op (inert monitor) elsewhere."""
        if sys.platform != "win32" or self._thread is not None:
            if sys.platform != "win32":
                logger.info("PresenceMonitor inert (not Windows); presence reads as absent")
            return self
        self._stop.clear()
        self._start_dispatcher()  # before the hooks: their callbacks only enqueue
        self._thread = threading.Thread(target=self._run_windows, name="desktop-presence", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is None:
            self._stop_dispatcher()
            return
        try:  # post a quit to the hook thread's message loop
            api = _win_api()
            if api is not None and self._thread_id:
                api.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        thread.join(timeout=2.0)
        self._stop_dispatcher()

    # --- the Windows hook thread (guarded; never raises out) -------------------------------------------------

    def _run_windows(self) -> None:  # pragma: no cover - requires a real Windows message loop
        try:
            self._install_windows_hooks()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("PresenceMonitor could not install hooks; presence reads as absent")
            return
        try:
            self._message_loop()
        finally:
            self._uninstall_windows_hooks()

    def _install_windows_hooks(self) -> None:  # pragma: no cover
        api = _win_api()
        if api is None:
            raise OSError("not Windows")
        user32, kernel32 = api.user32, api.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())

        def keyboard_proc(code: int, wparam: int, lparam: int) -> int:
            if code == _HC_ACTION:
                try:
                    vk, injected = _keyboard_hook_event(lparam)
                    down = wparam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
                    if down or wparam in (_WM_KEYUP, _WM_SYSKEYUP):
                        self._hook_key(vk, injected, down)  # record and enqueue only (LowLevelHooksTimeout)
                except Exception:  # pylint: disable=broad-exception-caught  # never break the input chain
                    logger.exception("Keyboard hook handler raised")
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def mouse_proc(code: int, wparam: int, lparam: int) -> int:
            if code == _HC_ACTION:
                try:
                    self.note_input(_mouse_hook_injected(lparam))
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Mouse hook handler raised")
            return user32.CallNextHookEx(None, code, wparam, lparam)

        self._keyboard_cb = api.HOOKPROC(keyboard_proc)  # kept alive on self while the hooks are installed
        self._mouse_cb = api.HOOKPROC(mouse_proc)
        module = kernel32.GetModuleHandleW(None)
        for hook_id, callback in ((_WH_KEYBOARD_LL, self._keyboard_cb), (_WH_MOUSE_LL, self._mouse_cb)):
            handle = user32.SetWindowsHookExW(hook_id, callback, module, 0)
            if not handle:
                raise OSError(f"SetWindowsHookExW failed ({ctypes.get_last_error()})")  # type: ignore[attr-defined]
            self._hooks.append(handle)
        self._install_wts_window(api)

    def _install_wts_window(self, api: Any) -> None:  # pragma: no cover
        user32 = api.user32

        def wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == _WM_WTSSESSION_CHANGE:  # this window lives on the hook thread: record and enqueue only
                if wparam == _WTS_SESSION_LOCK:
                    self._hook_lock(True)
                elif wparam == _WTS_SESSION_UNLOCK:
                    self._hook_lock(False)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = api.WNDPROC(wndproc)
        hwnd = user32.CreateWindowExW(0, "STATIC", "JARVIS-Desktop-Presence", 0, 0, 0, 0, 0,
                                      None, None, None, None)
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        self._hwnd = int(hwnd)
        # subclass the static window so it receives the WTS session notifications
        api.set_long(hwnd, _GWLP_WNDPROC, ctypes.cast(self._wndproc, ctypes.c_void_p))
        if api.wtsapi32 is None or not api.wtsapi32.WTSRegisterSessionNotification(hwnd, _NOTIFY_FOR_THIS_SESSION):
            logger.warning("WTSRegisterSessionNotification failed; lock/unlock will not be seen")

    def _message_loop(self) -> None:  # pragma: no cover
        api = _win_api()
        msg = api.MSG()
        while not self._stop.is_set():
            result = api.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            api.user32.TranslateMessage(ctypes.byref(msg))
            api.user32.DispatchMessageW(ctypes.byref(msg))

    def _uninstall_windows_hooks(self) -> None:  # pragma: no cover
        try:
            api = _win_api()
            for handle in self._hooks:
                api.user32.UnhookWindowsHookEx(handle)
            self._hooks.clear()
            if self._hwnd:
                if api.wtsapi32 is not None:
                    api.wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)
                api.user32.DestroyWindow(self._hwnd)
                self._hwnd = 0
        except Exception:  # pylint: disable=broad-exception-caught
            pass


class ManualPresence:
    """A fully controllable Presence for tests and for a headless run; no hooks, no OS calls."""

    def __init__(self, *, present: bool = False, locked: bool = False, title: str = "",
                 win_class: str = "", process: str = "", handle: int = 0) -> None:
        self._present = present
        self._locked = locked
        self._title = title
        self._class = win_class
        self._process = process.lower()
        self._handle = int(handle)

    def present_within(self, window_s: float) -> bool:  # noqa: D401 - protocol method
        return self._present

    def is_locked(self) -> bool:
        return self._locked

    def foreground_title(self) -> str:
        return self._title

    def foreground_class(self) -> str:
        return self._class

    def foreground_process(self) -> str:
        return self._process

    def foreground_handle(self) -> int:
        return self._handle

    # test knobs
    def set_present(self, present: bool) -> None:
        self._present = bool(present)

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)

    def set_foreground_title(self, title: str) -> None:
        self._title = str(title)

    def set_foreground_class(self, win_class: str) -> None:
        self._class = str(win_class)

    def set_foreground_process(self, process: str) -> None:
        self._process = str(process).lower()

    def set_foreground_handle(self, handle: int) -> None:
        self._handle = int(handle)


def _foreground_hwnd(api: Any) -> Any:
    return api.user32.GetForegroundWindow() if api is not None else None


def _foreground_handle_windows() -> int:
    """The foreground window handle as an int (0 when unknown). Never raises."""
    try:
        hwnd = _foreground_hwnd(_win_api())
        return int(hwnd or 0)
    except Exception:  # pylint: disable=broad-exception-caught
        return 0


def _foreground_title_windows() -> str:
    """The foreground window title ('' when unknown). Never raises."""
    try:
        api = _win_api()
        hwnd = _foreground_hwnd(api)
        if not hwnd:
            return ""
        length = api.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        api.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or ""
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _foreground_class_windows() -> str:
    """The foreground window class name ('' when unknown). Never raises."""
    try:
        api = _win_api()
        hwnd = _foreground_hwnd(api)
        if not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(256)
        api.user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value or ""
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _foreground_process_windows() -> str:
    """The foreground window's process image name in lower case ('' when unknown). Never raises."""
    try:
        api = _win_api()
        hwnd = _foreground_hwnd(api)
        if not hwnd:
            return ""
        pid = api.DWORD(0)
        api.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = api.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = api.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not api.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            path = buffer.value or ""
        finally:
            api.kernel32.CloseHandle(handle)
        return path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _safe(callback: Callable[..., None], *args: object) -> None:
    try:
        callback(*args)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Presence listener raised")
