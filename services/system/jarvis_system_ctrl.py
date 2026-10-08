"""
# services/system/jarvis_system_ctrl.py
System-level controls for JARVIS (Shutdown, Restart, Sleep, Recycle Bin).
Guarded wrappers: ConfirmFlow risk, non-blocking sleep, cancel_shutdown.
"""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
import threading
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import tool_result
from services.utils.jarvis_delivery import DESKTOP_ONLY
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SYSTEM")

# SHEmptyRecycleBinW flags: no confirm UI, no progress, no sound
_SHERB_FLAGS = 0x00000001 | 0x00000002 | 0x00000004  # 0x7
# HRESULTs that mean the bin was already empty / nothing to delete
_RECYCLE_ALREADY_EMPTY = {
    0x80270015,
    0x80070015,  # ERROR_NOT_READY (empty volume)
    0x80070002,  # ERROR_FILE_NOT_FOUND
}


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


def _desktop_only(tool: str) -> dict[str, Any]:
    return tool_result(
        "error",
        "Yeh command sirf Windows laptop par chalti hai.",
        tool=tool,
        error_code=DESKTOP_ONLY,
    )


def _query_recycle_bin() -> tuple[int, float]:
    """Return (item_count, size_mb) via SHQueryRecycleBinW."""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    hr = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    if hr != 0:
        return 0, 0.0
    items = int(info.i64NumItems)
    size_mb = float(info.i64Size) / (1024.0 * 1024.0)
    return items, size_mb


async def _recycle_confirm_summary(_args: dict[str, Any]) -> str:
    items, size_mb = await asyncio.to_thread(_query_recycle_bin)
    return f"{items} items, {size_mb:.1f} MB recycle bin se hamesha ke liye delete honge"


def _powercfg_has_s0() -> bool:
    try:
        proc = subprocess.run(
            ["powercfg", "/a"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        return "S0 Low Power Idle" in text
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("powercfg /a failed: %s", exc)
        return False


def _post_monitor_power_off() -> None:
    """Turn off the display (Modern Standby / screen-off path)."""
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore

        win32api.PostMessage(
            win32con.HWND_BROADCAST,
            win32con.WM_SYSCOMMAND,
            win32con.SC_MONITORPOWER,
            2,
        )
        return
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    # Fallback without pywin32
    HWND_BROADCAST = 0xFFFF
    WM_SYSCOMMAND = 0x0112
    SC_MONITORPOWER = 0xF170
    ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def _set_suspend_state() -> None:
    """True S3 sleep via powrprof.SetSuspendState(False, False, False)."""
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def _run_shutdown(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _hibernate_now() -> None:
    _run_shutdown(["shutdown", "/h"])


@jarvis_tool(
    risk="high",
    timeout=20,
    confirm_summary=(
        "System 30 second mein SHUTDOWN hoga; "
        "cancel ke liye bolein: Jarvis shutdown cancel karo"
    ),
)
async def shutdown_system() -> dict[str, Any]:
    """Safely shuts down the host operating system after a 30s delay."""
    logger.warning("SHUTDOWN command received.")
    if sys.platform != "win32":
        return _desktop_only("shutdown_system")
    try:
        cmd = ["shutdown", "/s", "/t", "30"]
        proc = await asyncio.to_thread(_run_shutdown, cmd)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            return tool_result(
                "error",
                f"Shutdown failed: {err}",
                tool="shutdown_system",
            )
        return tool_result(
            "success",
            "System is shutting down in 30 seconds, Sir.",
            tool="shutdown_system",
            data={"delay_s": 30, "command": " ".join(cmd)},
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Shutdown failed: %s", e)
        return tool_result("error", f"Shutdown failed: {e}", tool="shutdown_system")


@jarvis_tool(
    risk="high",
    timeout=20,
    confirm_summary=(
        "System 30 second mein RESTART hoga; "
        "cancel ke liye bolein: Jarvis shutdown cancel karo"
    ),
)
async def restart_system() -> dict[str, Any]:
    """Restarts the host operating system after a 30s delay."""
    logger.warning("RESTART command received.")
    if sys.platform != "win32":
        return _desktop_only("restart_system")
    try:
        cmd = ["shutdown", "/r", "/t", "30"]
        proc = await asyncio.to_thread(_run_shutdown, cmd)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            return tool_result(
                "error",
                f"Restart failed: {err}",
                tool="restart_system",
            )
        return tool_result(
            "success",
            "System is restarting in 30 seconds, Sir.",
            tool="restart_system",
            data={"delay_s": 30, "command": " ".join(cmd)},
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Restart failed: %s", e)
        return tool_result("error", f"Restart failed: {e}", tool="restart_system")


@jarvis_tool(
    risk="medium",
    timeout=20,
    confirm_summary="Laptop sleep / screen-off mode mein jayega",
)
async def sleep_system(mode: str = "sleep") -> dict[str, Any]:
    """
    Put the system to sleep (or hibernate).

    mode='sleep' uses Modern Standby screen-off when S0 is available, else S3
    SetSuspendState. mode='hibernate' schedules shutdown /h after 3 seconds.
    Returns immediately so the event loop is never blocked until resume.
    """
    logger.info("SLEEP command received (mode=%s).", mode)
    if sys.platform != "win32":
        return _desktop_only("sleep_system")

    normalized = (mode or "sleep").strip().lower()
    if normalized not in ("sleep", "hibernate"):
        return tool_result(
            "error",
            f"Unknown sleep mode '{mode}'. Use 'sleep' or 'hibernate'.",
            tool="sleep_system",
        )

    try:
        if normalized == "hibernate":
            threading.Timer(3.0, _hibernate_now).start()
            return tool_result(
                "success",
                "Hibernate 3 second mein start hoga, Sir.",
                tool="sleep_system",
                data={"mode": "hibernate", "method": "shutdown_/h"},
            )

        has_s0 = await asyncio.to_thread(_powercfg_has_s0)
        if has_s0:
            threading.Timer(3.0, _post_monitor_power_off).start()
            return tool_result(
                "success",
                "Screen 3 second mein off hogi (Modern Standby / S0).",
                tool="sleep_system",
                data={"mode": "sleep", "method": "S0_monitor_off"},
            )

        threading.Thread(target=_set_suspend_state, daemon=True).start()
        return tool_result(
            "success",
            "Putting the system to sleep, Sir.",
            tool="sleep_system",
            data={"mode": "sleep", "method": "S3_SetSuspendState"},
        )
    except (subprocess.SubprocessError, OSError, AttributeError) as e:
        logger.error("Sleep failed: %s", e)
        return tool_result("error", f"Sleep failed: {e}", tool="sleep_system")


@jarvis_tool(risk="low", timeout=10)
async def cancel_shutdown() -> dict[str, Any]:
    """Abort a pending Windows shutdown or restart (shutdown /a)."""
    logger.info("CANCEL_SHUTDOWN command received.")
    if sys.platform != "win32":
        return _desktop_only("cancel_shutdown")
    try:
        proc = await asyncio.to_thread(_run_shutdown, ["shutdown", "/a"])
        if proc.returncode == 1116:
            return tool_result(
                "not_found",
                "Koi shutdown/restart pending nahi",
                tool="cancel_shutdown",
                data={"exit_code": 1116},
            )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            return tool_result(
                "error",
                f"Cancel shutdown failed: {err}",
                tool="cancel_shutdown",
                data={"exit_code": proc.returncode},
            )
        return tool_result(
            "success",
            "Pending shutdown/restart cancel ho gaya, Sir.",
            tool="cancel_shutdown",
            data={"exit_code": 0},
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Cancel shutdown failed: %s", e)
        return tool_result("error", f"Cancel shutdown failed: {e}", tool="cancel_shutdown")


def _empty_recycle_bin_shell32() -> int:
    """Call SHEmptyRecycleBinW; return HRESULT (0 = success)."""
    return int(ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, _SHERB_FLAGS))


def _empty_recycle_bin_powershell() -> subprocess.CompletedProcess[str]:
    ps_cmd = "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
    return subprocess.run(
        ["powershell", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@jarvis_tool(
    risk="high",
    timeout=20,
    confirm_summary=_recycle_confirm_summary,
)
async def empty_recycle_bin() -> dict[str, Any]:
    """Clears the Windows recycle bin (non-blocking, ConfirmFlow high)."""
    if sys.platform != "win32":
        return _desktop_only("empty_recycle_bin")

    try:
        items_before, size_before = await asyncio.to_thread(_query_recycle_bin)
        hr = await asyncio.to_thread(_empty_recycle_bin_shell32)
        hr_u = hr & 0xFFFFFFFF

        if hr == 0:
            freed = f"{size_before:.1f} MB" if size_before else "contents"
            return tool_result(
                "success",
                f"Recycle bin emptied; {freed} free hue, Sir.",
                tool="empty_recycle_bin",
                data={"items": items_before, "size_mb": size_before, "method": "SHEmptyRecycleBinW"},
            )

        if hr_u in _RECYCLE_ALREADY_EMPTY or items_before == 0:
            return tool_result(
                "success",
                "Recycle bin pehle se khali hai",
                tool="empty_recycle_bin",
                data={"items": 0, "size_mb": 0.0, "hresult": hr_u},
            )

        # PowerShell fallback
        proc = await asyncio.to_thread(_empty_recycle_bin_powershell)
        if proc.returncode == 0:
            return tool_result(
                "success",
                "Recycle bin has been emptied successfully, Sir.",
                tool="empty_recycle_bin",
                data={"items": items_before, "size_mb": size_before, "method": "Clear-RecycleBin"},
            )
        logger.error("Empty Recycle Bin failed hr=0x%08X ps=%s", hr_u, proc.returncode)
        return tool_result(
            "error",
            "Recycle bin was already empty or could not be cleared.",
            tool="empty_recycle_bin",
            data={"hresult": hr_u, "ps_exit": proc.returncode},
        )
    except (subprocess.SubprocessError, OSError, AttributeError) as e:
        logger.error("Empty Recycle Bin failed: %s", e)
        return tool_result(
            "error",
            "Recycle bin was already empty or could not be cleared.",
            tool="empty_recycle_bin",
        )
