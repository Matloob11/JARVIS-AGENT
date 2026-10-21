"""
# jarvis_reminders.py
Proactive Reminder System for JARVIS.
Stores and manages scheduled tasks and notifications.

X-66: every read-modify-write of reminders.json runs inside services.utils.jarvis_filelock.locked(REMINDERS_FILE),
so the live job and jarvis-agent-svc (or two job threads on the PC brain) never lose an update to each other
(fcntl/msvcrt sidecar lock, cross-process). "now" for setting a reminder and for checking what is due is the
wall-clock time in TZ (Asia/Karachi by default), not the host clock, so a UTC container still fires a reminder
at the right Pakistan time; the on-disk "time" field keeps the same naive-ISO format as before (today's format).
list_due_reminders() (read-only, no mutation) and mark_reminder_delivered(id) are for agent-svc: it may read
reminders.json at any time, but writes it only through mark_reminder_delivered, and only after a reminder was
actually announced, never before (so a crash between reading and announcing leaves it 'pending' for a retry
instead of silently dropping it). The PC brain's own loop keeps using check_due_reminders(), whose behaviour
(marking a due reminder 'triggered' and returning it) is unchanged from before X-66.
"""

import asyncio
import json
import os
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.automation.jarvis_task_manager import parse_when
from services.utils.jarvis_filelock import locked
from services.utils.jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("JARVIS-REMINDERS")

REMINDERS_FILE = os.path.join("conversations", "reminders.json")

# X-66: wall-clock timezone for setting/checking reminders (TZ env var, else Asia/Karachi). Read as a plain env
# var rather than through services.utils.jarvis_config, so this Cursor-lane module keeps no import from it.
_DEFAULT_TZ_NAME = "Asia/Karachi"


def _reminder_zone() -> ZoneInfo:
    """ZoneInfo(TZ) when TZ is set to a name tzdata knows, else Asia/Karachi."""
    name = (os.getenv("TZ") or "").strip() or _DEFAULT_TZ_NAME
    try:
        return ZoneInfo(name)
    except Exception:  # pylint: disable=broad-exception-caught  # unknown TZ value, or no tzdata for it
        return ZoneInfo(_DEFAULT_TZ_NAME)


def _now_local() -> datetime:
    """Current wall-clock time in _reminder_zone(), stripped back to naive: matches the on-disk 'time' format
    (no tzinfo), so a UTC host clock (the VPS container) still compares and stores exactly like a PKT host did
    before X-66, and a reminder fires at the right Pakistan time either way."""
    return datetime.now(_reminder_zone()).replace(tzinfo=None)


def load_reminders() -> list[dict]:
    """Load reminders with backup on corruption. A plain read; a caller that mutates wraps this call (and
    save_reminders) itself in `with locked(REMINDERS_FILE):` (X-66) for the whole cycle. save_reminders's
    os.replace() is already atomic, so an unlocked read here never sees a half-written file, only the
    previous or the next complete one."""
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Omega Corruption Detected: %s. Backing up.", e)
            # Atomic backup of corrupted state
            if os.path.exists(REMINDERS_FILE):
                os.rename(
                    REMINDERS_FILE, f"{REMINDERS_FILE}.corrupted_{int(datetime.now().timestamp())}")
            return []
    return []


def save_reminders(reminders: list[dict]):
    """Atomic write: writes to temp file then renames. Not itself cross-process safe; a caller that mutates
    holds the X-66 file lock for the whole load-mutate-save cycle (see load_reminders)."""
    os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)
    temp_file = f"{REMINDERS_FILE}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(reminders, f, indent=2, ensure_ascii=False)
        # Swap temp for real - OS level atomic rename
        os.replace(temp_file, REMINDERS_FILE)
    except OSError as e:
        logger.error("Error saving reminders: %s", e)
        if os.path.exists(temp_file):
            os.remove(temp_file)


def _append_reminder_locked(reminder: dict) -> None:
    """X-66: the whole load-append-save cycle for one new reminder, under the cross-process lock."""
    with locked(REMINDERS_FILE):
        reminders = load_reminders()
        reminders.append(reminder)
        save_reminders(reminders)


@jarvis_tool
async def set_reminder(time_str: str, message: str) -> str:
    """
    Schedule a reminder for a specific time or duration.
    time_str can be absolute (e.g., '2026-02-18 14:00', '14:30', '9:00 pm', 'kal 09:00') or
    relative (e.g., '10 minutes', 'das minute baad'); see jarvis_task_manager.parse_when.
    message is the text JARVIS should say when the reminder triggers.
    """
    # C-125 B6: never log reminder text at INFO (PII / chat content).
    logger.info("Setting reminder at %s", time_str)

    now = _now_local()

    try:
        target_time = parse_when(time_str, now=now)

        if not target_time:
            return (
                "Maaf Sir, main time nahi samajh paya. "
                "Format e.g. '10 minutes', 'das minute baad' ya '14:30' use karein."
            )

        new_reminder = {
            "id": f"rem_{int(target_time.timestamp())}_{secrets.token_hex(2)}",
            "time": target_time.isoformat(),
            "message": message,
            "status": "pending",
            "created_at": now.isoformat(),
        }
        await asyncio.to_thread(_append_reminder_locked, new_reminder)

        # Replaced the original Hindi return with the English one,
        # and formatted it to fit within reasonable line length.
        return (
            f"Successfully set reminder for {time_str} "
            f"({target_time.strftime('%Y-%m-%d %H:%M:%S')}): {message}"
        )

    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError, TimeoutError) as e:
        logger.error("Error in set_reminder: %s", e)
        return f"Error setting reminder: {e!s}"


@jarvis_tool
async def list_reminders() -> str:
    """List all pending reminders."""
    reminders = await asyncio.to_thread(load_reminders)
    pending = [r for r in reminders if r.get("status") == "pending"]

    if not pending:
        return "Sir, abhi koi pending reminders nahi hain."

    lines = ["Aapke pending reminders yeh hain:"]
    for r in pending:
        t = datetime.fromisoformat(r["time"]).strftime("%I:%M %p")
        lines.append(f"- {t}: {r['message']}")

    return "\n".join(lines)


def check_due_reminders() -> list[dict]:
    """PC brain: unchanged behaviour from before X-66 (marks each due reminder 'triggered' and returns it), now
    cross-process-safe (locked(REMINDERS_FILE)) and timezone-aware (_now_local()). Returns [] without blocking
    when another holder has the lock right now, the same as the old in-process asyncio.Lock check did; the
    frequent (30 s) caller just tries again next tick."""
    try:
        with locked(REMINDERS_FILE, timeout=0):
            reminders = load_reminders()
            now = _now_local()
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
    except TimeoutError:
        return []


def list_due_reminders() -> list[dict]:
    """Agent-svc (X-66): a read-only, oldest-first list of pending reminders whose time has arrived. Never
    mutates reminders.json (unlike check_due_reminders): the caller announces each one and only then calls
    mark_reminder_delivered(id), so a crash between the two leaves it 'pending' for a retry next tick instead
    of silently dropping it."""
    with locked(REMINDERS_FILE):
        reminders = load_reminders()
    now = _now_local()
    due = []
    for r in reminders:
        if r.get("status") != "pending":
            continue
        try:
            r_time = datetime.fromisoformat(r["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if r_time <= now:
            due.append(r)
    due.sort(key=lambda r: r["time"])
    return due


def mark_reminder_delivered(
    reminder_id: str,
    created_at: str | None = None,
    time: str | None = None,
) -> bool:
    """Agent-svc (X-66 / C-125 B6): marks one reminder delivered (status 'triggered', the same terminal status
    check_due_reminders uses, plus a 'delivered_at' timestamp), under the lock, only after it was actually
    announced. Optional created_at/time disambiguate when ids collide. True when a pending reminder with this
    id was found and changed; False for an unknown id or one that is no longer pending."""
    with locked(REMINDERS_FILE):
        reminders = load_reminders()
        changed = False
        for r in reminders:
            if r.get("id") != reminder_id or r.get("status") != "pending":
                continue
            if created_at is not None and r.get("created_at") != created_at:
                continue
            if time is not None and r.get("time") != time:
                continue
            r["status"] = "triggered"
            r["delivered_at"] = _now_local().isoformat()
            changed = True
            break
        if changed:
            save_reminders(reminders)
    return changed
