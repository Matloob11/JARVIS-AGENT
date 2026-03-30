"""
Jarvis AI Bug Hunter
This module monitors logs for errors and uses AI to analyze tracebacks and suggest fixes.
"""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import vortex_guard

# Configure logging
logger = setup_logger("bug_hunter")

LOG_FILE: Path = Path("logs") / "jarvis_main.log"


class BugHunter:
    """Analyzes logs and suggests fixes."""

    def __init__(self) -> None:
        self.last_position: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        if LOG_FILE.exists():
            self.last_position = LOG_FILE.stat().st_size

    async def get_new_errors(self) -> list[str]:
        """Reads new lines from the error log with lock protection."""
        async with self._lock:
            if not os.path.exists(LOG_FILE):
                return []

            def _read_sync():
                current_size = os.path.getsize(LOG_FILE)
                if current_size < self.last_position:
                    # Log was rotated or cleared
                    self.last_position = 0

                lines = []
                with open(LOG_FILE, encoding="utf-8") as f:
                    f.seek(self.last_position)
                    lines = f.readlines()
                    self.last_position = f.tell()
                return lines

            return await asyncio.to_thread(_read_sync)

    async def analyze_error(self, error_text: str) -> str:
        """
        Uses the internal LLM context (via prompting) to analyze an error.
        In this implementation, it prepares a report for the agent's brain.
        """
        # This is a placeholder for actual LLM integration.
        # The agent.py will call this or we will push a notification to the agent.
        return f"Sir, ek naya error mila hai:\n\n{error_text[:500]}..."


async def monitor_logs(callback: Callable[[str], Any]) -> None:
    """Background task to watch logs."""
    hunter = BugHunter()
    logger.info("AI Bug Hunter monitoring started on %s", LOG_FILE)

    while True:
        try:
            new_lines: list[str] = await hunter.get_new_errors()
            if new_lines:
                error_block: str = "".join(new_lines)
                if "ERROR" in error_block or "TRACEBACK" in error_block.upper():
                    # Sanitize before callback to prevent injection propagation
                    sanitized_error = vortex_guard.sanitize_input(error_block)
                    await callback(sanitized_error)
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            logger.info("Bug Hunter monitoring stopping...")
            break
        except (OSError, ValueError) as e:
            logger.error("Bug Hunter loop error: %s", e)
            await asyncio.sleep(10)


@jarvis_tool
async def tool_investigate_recent_bugs() -> str:
    """Manual tool to check and analyze the last few errors."""
    if not LOG_FILE.exists():
        return "Sir, koi error logs nahi mile. Sab theek lag raha hai."

    def _read_recent() -> str:
        with LOG_FILE.open("r", encoding="utf-8") as f:
            # Read last 4KB
            f.seek(0, 2) # os.SEEK_END
            size = f.tell()
            f.seek(max(0, size - 4000))
            return f.read()

    recent_logs: str = await asyncio.to_thread(_read_recent)

    if not recent_logs.strip():
        return "Sir, recent logs khali hain. System stable hai."

    # Zero-trust: Sanitize logs before passing to agent prompt
    sanitized_logs: str = vortex_guard.sanitize_input(recent_logs)

    return (
        "Sir, maine recent logs analyze kiye hain. "
        "Yahan kuch patterns mile hain: \n\n"
        f"```\n{sanitized_logs}\n```\n\n"
        "Main inhein theek karne ke liye ready hoon."
    )
