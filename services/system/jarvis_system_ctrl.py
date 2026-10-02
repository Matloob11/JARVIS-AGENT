"""
# services/system/jarvis_system_ctrl.py
System-level controls for JARVIS (Shutdown, Restart, Sleep).
Provides safe wrappers for OS commands.
"""

import os
import subprocess
import sys
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import tool_result
from services.utils.jarvis_delivery import DESKTOP_ONLY
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SYSTEM")


def _desktop_only(tool: str) -> dict[str, Any]:
    return tool_result(
        "error",
        "Yeh command sirf Windows laptop par chalti hai.",
        tool=tool,
        error_code=DESKTOP_ONLY,
    )


@jarvis_tool
async def shutdown_system() -> str | dict[str, Any]:
    """Safely shuts down the host operating system."""
    logger.warning("SHUTDOWN command received.")
    if sys.platform != "win32":
        return _desktop_only("shutdown_system")
    try:
        subprocess.run(["shutdown", "/s", "/t", "30"], check=True)
        return "System is shutting down in 30 seconds, Sir."
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Shutdown failed: %s", e)
        return f"Shutdown failed: {e}"


@jarvis_tool
async def restart_system() -> str | dict[str, Any]:
    """Restarts the host operating system."""
    logger.warning("RESTART command received.")
    if sys.platform != "win32":
        return _desktop_only("restart_system")
    try:
        subprocess.run(["shutdown", "/r", "/t", "30"], check=True)
        return "System is restarting in 30 seconds, Sir."
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Restart failed: %s", e)
        return f"Restart failed: {e}"


@jarvis_tool
async def sleep_system() -> str | dict[str, Any]:
    """Puts the system to sleep/standby mode."""
    logger.info("SLEEP command received.")
    if sys.platform != "win32":
        return _desktop_only("sleep_system")
    try:
        cmd = "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
        os.system(cmd)  # nosec B605
        return "Putting the system to sleep, Sir."
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Sleep failed: %s", e)
        return f"Sleep failed: {e}"


@jarvis_tool
async def empty_recycle_bin() -> str:
    """Clears the Windows recycle bin."""
    if sys.platform != "win32":
        return "Recycle bin clearing only supported on Windows."

    try:
        # Pylint fix: Split long PowerShell command for readability
        ps_cmd = (
            "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(["powershell", "-Command", ps_cmd], check=True)
        return "Recycle bin has been emptied successfully, Sir."
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("Empty Recycle Bin failed: %s", e)
        return "Recycle bin was already empty or could not be cleared."
