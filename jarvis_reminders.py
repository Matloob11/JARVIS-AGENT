"""
# jarvis_reminders.py
Proactive Reminder System for JARVIS.
Stores and manages scheduled tasks and notifications.
"""

import json
import os
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("JARVIS-REMINDERS")

REMINDERS_FILE = os.path.join("conversations", "reminders.json")
# Omega Global Lock
reminders_lock = asyncio.Lock()


def load_reminders() -> List[Dict]:
    """Load reminders with backup on corruption."""
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Omega Corruption Detected: %s. Backing up.", e)
            # Atomic backup of corrupted state
            if os.path.exists(REMINDERS_FILE):
                os.rename(
                    REMINDERS_FILE, f"{REMINDERS_FILE}.corrupted_{int(datetime.now().timestamp())}")
            return []
    return []


def save_reminders(reminders: List[Dict]):
    """Atomic write: writes to temp file then renames."""
    os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
    temp_file = f"{REMINDERS_FILE}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(reminders, f, indent=2, ensure_ascii=False)
        # Swap temp for real - OS level atomic rename
        os.replace(temp_file, REMINDERS_FILE)
    except IOError as e:
        logger.error("Error saving reminders: %s", e)
        if os.path.exists(temp_file):
            os.remove(temp_file)


@function_tool
async def set_reminder(time_str: str, message: str) -> str:
    """
    Schedule a reminder for a specific time or duration.
    time_str can be absolute (e.g., '2026-02-18 14:00') or relative (e.g., '10 minutes').
    message is the text JARVIS should say when the reminder triggers.
    """
    logger.info("Setting reminder: %s at %s", message, time_str)

    now = datetime.now()
    target_time = None

    try:
        # Try relative time first (e.g., "10 minutes")
        if "minute" in time_str.lower():
            minutes = int(''.join(filter(str.isdigit, time_str)))
            target_time = now + timedelta(minutes=minutes)
        elif "hour" in time_str.lower():
            hours = int(''.join(filter(str.isdigit, time_str)))
            target_time = now + timedelta(hours=hours)
        elif "second" in time_str.lower():
            seconds = int(''.join(filter(str.isdigit, time_str)))
            target_time = now + timedelta(seconds=seconds)
        else:
            # Try parsing absolute format (HH:MM or YYYY-MM-DD HH:MM)
            # LLM usually provides simple HH:MM
            try:
                # Try HH:MM
                parsed_time = datetime.strptime(time_str, "%H:%M").time()
                target_time = datetime.combine(now.date(), parsed_time)
                # If time has already passed today, assume it's for tomorrow
                if target_time < now:
                    target_time += timedelta(days=1)
            except ValueError:
                # Fallback for full timestamp
                target_time = datetime.fromisoformat(time_str)

        if not target_time:
            return (
                "Maaf Sir, main time nahi samajh paya. "
                "Format e.g. '10 minutes' ya '14:30' use karein."
            )

        async with reminders_lock:
            reminders = load_reminders()
            new_reminder = {
                "id": f"rem_{int(target_time.timestamp())}",
                "time": target_time.isoformat(),
                "message": message,
                "status": "pending",
                "created_at": now.isoformat()
            }
            reminders.append(new_reminder)
            save_reminders(reminders)

        # Replaced the original Hindi return with the English one,
        # and formatted it to fit within reasonable line length.
        return (
            f"Successfully set reminder for {time_str} "
            f"({target_time.strftime('%Y-%m-%d %H:%M:%S')}): {message}"
        )

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error in set_reminder: %s", e)
        return f"Error setting reminder: {str(e)}"


@function_tool
async def list_reminders() -> str:
    """List all pending reminders."""
    reminders = load_reminders()
    pending = [r for r in reminders if r.get("status") == "pending"]

    if not pending:
        return "Sir, abhi koi pending reminders nahi hain."

    lines = ["Aapke pending reminders yeh hain:"]
    for r in pending:
        t = datetime.fromisoformat(r["time"]).strftime("%I:%M %p")
        lines.append(f"- {t}: {r['message']}")

    return "\n".join(lines)


def check_due_reminders() -> List[Dict]:
    """Check for reminders that are due now."""
    # Since this is called from an async loop but is synchronous itself,
    # and we need the lock, we must handle the logic carefully.
    # We'll use a local lock check if needed, but the main agent loop
    # calls this frequently, so we should avoid blocking.

    # Actually, let's make it more robust by allowing it to return
    # an empty list if locked, or use a non-blocking check.
    if reminders_lock.locked():
        return []

    reminders = load_reminders()
    now = datetime.now()
    due = []
    updated = False

    for r in reminders:
        if r.get("status") == "pending":
            r_time = datetime.fromisoformat(r["time"])
            if r_time <= now:
                r["status"] = "triggered"
                due.append(r)
                updated = True

    if updated:
        save_reminders(reminders)

    return due
