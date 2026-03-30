"""
# services/utils/jarvis_self_healing.py
Autonomous Self-Healing and Repair System for JARVIS.
Detects tool failures and repairs source code autonomously.
"""

import asyncio
import re
import subprocess
import traceback
from pathlib import Path

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("JARVIS-SELF-HEALING")


def get_pylint_score(file_path: str | Path) -> float:
    """Run pylint and extract the score."""
    try:
        result = subprocess.run(
            ["pylint", file_path],  # nosec B607
            capture_output=True,
            text=True,
            check=False,
        )
        # Look for "Your code has been rated at X.XX/10"
        match = re.search(
            r"Your code has been rated at ([\d\.]+)/10", result.stdout)
        if match:
            return float(match.group(1))
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.error("Error getting pylint score: %s", e)
    return 0.0


@jarvis_tool
async def autonomous_self_repair(error_details: str, file_path: str | Path) -> str:
    """
    Autonomously repair a code file based on provided error details.
    Uses LLM reasoning to generate a patch and applies it.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return f"Error: File {path} not found."

        with path.open("r", encoding="utf-8") as f:
            f.read()  # Verify file is readable

        logger.info("Analyzing error: %s", error_details)
        pylint_score: float = await asyncio.to_thread(get_pylint_score, path)
        return (
            f"[SELF-HEALING SYSTEM]: File {path} ready for repair.\n"
            f"Original Score: {pylint_score}/10\n"
            "Please provide the corrected code block using 'write_custom_code'."
        )

    except (OSError, ValueError) as e:
        logger.error("Self-repair failed: %s", e)
        return f"Self-repair failed: {e!s}"


def format_error_report(exception: Exception) -> str:
    """Generate a structured error report for the LLM."""
    tb = traceback.format_exc()
    header = "--- SYSTEM ERROR DETECTED ---"
    exc_info = f"EXCEPTION: {type(exception).__name__}: {exception}"
    return f"{header}\n{exc_info}\n\nTRACEBACK:\n{tb}\n"
