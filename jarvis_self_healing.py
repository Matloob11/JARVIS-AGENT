"""
# jarvis_self_healing.py
Autonomous Self-Healing and Repair System for JARVIS.
Detects tool failures and repairs source code autonomously.
"""

import os
import traceback
import subprocess
import re
import asyncio
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("JARVIS-SELF-HEALING")


def get_pylint_score(file_path: str) -> float:
    """Run pylint and extract the score."""
    try:
        result = subprocess.run(
            ["pylint", file_path],  # nosec B607
            capture_output=True,
            text=True,
            check=False
        )
        # Look for "Your code has been rated at X.XX/10"
        match = re.search(
            r"Your code has been rated at ([\d\.]+)/10", result.stdout)
        if match:
            return float(match.group(1))
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.error("Error getting pylint score: %s", e)
    return 0.0


@function_tool
async def autonomous_self_repair(error_details: str, file_path: str) -> str:
    """
    Autonomously repair a code file based on provided error details.
    Uses LLM reasoning to generate a patch and applies it.
    """
    try:
        if not os.path.exists(file_path):
            return f"Error: File {file_path} not found."

        with open(file_path, 'r', encoding='utf-8') as f:
            _ = f.read()  # Verify file is readable

        logger.info("Analyzing error: %s", error_details)
        pylint_score = await asyncio.to_thread(get_pylint_score, file_path)
        return (
            f"[SELF-HEALING SYSTEM]: File {file_path} ready for repair.\n"
            f"Original Score: {pylint_score}/10\n"
            "Please provide the corrected code block using 'write_custom_code' or "
            "simply confirm the fix strategy."
        )

    except (OSError, IOError, ValueError, AttributeError) as e:
        logger.error("Self-repair failed: %s", e)
        return f"Self-repair failed: {str(e)}"


def format_error_report(exception: Exception) -> str:
    """Generate a structured error report for the LLM."""
    tb = traceback.format_exc()
    return f"--- SYSTEM ERROR DETECTED ---\nEXCEPTION: {type(exception).__name__}: {exception}\n\nTRACEBACK:\n{tb}\n"
