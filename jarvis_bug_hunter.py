"""
Jarvis AI Bug Hunter
This module monitors logs for errors and uses AI to analyze tracebacks and suggest fixes.
"""

from livekit.agents import function_tool
import os
import asyncio
from typing import List
from jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("bug_hunter")

LOG_FILE = "jarvis_errors.log"


class BugHunter:
    """Analyzes logs and suggests fixes."""

    def __init__(self):
        self.last_position = 0
        if os.path.exists(LOG_FILE):
            self.last_position = os.path.getsize(LOG_FILE)

    def get_new_errors(self) -> List[str]:
        """Reads new lines from the error log."""
        if not os.path.exists(LOG_FILE):
            return []

        current_size = os.path.getsize(LOG_FILE)
        if current_size < self.last_position:
            # Log was rotated or cleared
            self.last_position = 0

        new_content = []
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            f.seek(self.last_position)
            new_content = f.readlines()
            self.last_position = f.tell()

        return new_content

    async def analyze_error(self, error_text: str) -> str:
        """
        Uses the internal LLM context (via prompting) to analyze an error.
        In this implementation, it prepares a report for the agent's brain.
        """
        # This is a placeholder for actual LLM integration.
        # The agent.py will call this or we will push a notification to the agent.
        summary = f"Sir, ek naya error mila hai:\n\n{error_text[:500]}..."
        return summary


async def monitor_logs(callback):
    """Background task to watch logs."""
    hunter = BugHunter()
    logger.info("AI Bug Hunter monitoring started on %s", LOG_FILE)

    while True:
        try:
            new_lines = hunter.get_new_errors()
            if new_lines:
                error_block = "".join(new_lines)
                if "ERROR" in error_block or "TRACEBACK" in error_block.upper():
                    await callback(error_block)
            await asyncio.sleep(10)
        except (IOError, ValueError, asyncio.CancelledError) as e:
            logger.error("Bug Hunter loop error: %s", e)
            await asyncio.sleep(30)


@function_tool
async def tool_investigate_recent_bugs() -> str:
    """Manual tool to check and analyze the last few errors."""
    if not os.path.exists(LOG_FILE):
        return "Sir, koi error logs nahi mile. Sab theek lag raha hai."

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        # Read last 2KB
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 2000))
        recent_logs = f.read()

    if not recent_logs.strip():
        return "Sir, recent logs khali hain. System stable hai."

    analysis_prompt = (
        "Sir, maine recent logs analyze kiye hain. "
        "Yahan kuch patterns mile hain: \n\n"
        f"```\n{recent_logs}\n```\n\n"
        "Main inhein theek karne ke liye ready hoon."
    )
    return analysis_prompt
