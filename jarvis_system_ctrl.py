"""
# jarvis_system_ctrl.py
System-level controls for JARVIS (Shutdown, Restart, Sleep, Lock).
"""

import asyncio
import subprocess
from livekit.agents import function_tool
from jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SYSTEM")


@function_tool
async def shutdown_system() -> dict:
    """Shuts down the computer immediately."""
    try:
        await asyncio.to_thread(subprocess.run, ["shutdown", "/s", "/t", "0"], check=True)
        return {
            "status": "success",
            "message": "🔌 System shutdown ho raha hai, Sir. Allah Hafiz."
        }
    except subprocess.CalledProcessError as e:
        logger.error("Shutdown failed: %s", e)
        return {"status": "error", "message": f"❌ Shutdown fail ho gaya: {e}"}


@function_tool
async def restart_system() -> dict:
    """Restarts the computer immediately."""
    try:
        await asyncio.to_thread(subprocess.run, ["shutdown", "/r", "/t", "0"], check=True)
        return {
            "status": "success",
            "message": "🔄 System restart ho raha hai, Sir."
        }
    except subprocess.CalledProcessError as e:
        logger.error("Restart failed: %s", e)
        return {"status": "error", "message": f"❌ Restart fail ho gaya: {e}"}


@function_tool
async def sleep_system() -> dict:
    """Puts the computer to sleep."""
    try:
        await asyncio.to_thread(subprocess.run, ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True)
        return {
            "status": "success",
            "message": "😴 System sleep mode par jaa raha hai, Sir."
        }
    except subprocess.CalledProcessError as e:
        logger.error("Sleep failed: %s", e)
        return {"status": "error", "message": f"❌ Sleep command fail ho gayi: {e}"}


@function_tool
async def lock_screen() -> dict:
    """Locks the screen."""
    try:
        await asyncio.to_thread(subprocess.run, ["rundll32.exe", "user32.dll,LockWorkStation"], check=True)
        return {
            "status": "success",
            "message": "🔒 Screen lock kar di gayi hai, Sir."
        }
    except subprocess.CalledProcessError as e:
        logger.error("Lock failed: %s", e)
        return {"status": "error", "message": f"❌ Lock command fail ho gayi: {e}"}
