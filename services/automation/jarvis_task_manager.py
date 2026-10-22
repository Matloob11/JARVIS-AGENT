"""
# jarvis_task_manager.py
Advanced Persistent Task Scheduler for JARVIS.
Uses SQLite for state persistence across restarts.

X-32 (K-33 task_store_v2): the tasks table carries kind, action_args (JSON), result, attempts,
updated_at and confirm_id so a scheduled task can run a tool action (with its own arguments) and
report back a result; status moves through pending -> triggered -> running ->
awaiting_confirmation -> completed|failed, or cancelled at any point. parse_when() understands
English and Roman-Urdu relative durations, HH:MM, h:mm am/pm, 'YYYY-MM-DD HH:MM', ISO and
'kal'/'tomorrow HH:MM'. Every state change calls _publish() which best-effort broadcasts a
task_sync event (EXT DASH-U13 owns the bridge/UI side of this; here it is a safe no-op until a
bridge is actually reachable).

X-66: every connection to tasks.db goes through _connect(), which turns on WAL journalling and a 5 s
busy_timeout, so a second process's (or thread's) connection waits instead of raising "database is locked"
while another one holds a write. increment_attempts() reads and writes the counter in one atomic UPDATE
statement (attempts = attempts + 1), so two processes bumping the same task's attempts at the same time
never lose one of the increments.
"""

import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_plugin_manager import jarvis_tool

logger = setup_logger("JARVIS-TASKS")

DB_PATH = os.path.join(config.project_root, "conversations", "tasks.db")

_SQLITE_BUSY_MS = 5000
_DEFAULT_TZ = "Asia/Karachi"


def scheduling_now() -> datetime:
    """Naive local wall clock in TZ (default Asia/Karachi); stored isoformat stays naive."""
    tz_name = (os.getenv("TZ") or _DEFAULT_TZ).strip() or _DEFAULT_TZ
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pylint: disable=broad-exception-caught
        tz = ZoneInfo(_DEFAULT_TZ)
    return datetime.now(tz).replace(tzinfo=None)


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=_SQLITE_BUSY_MS / 1000.0)
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_MS}")
    return conn


# Terminal states that no longer accept further mutation through the public tools.
_TERMINAL_STATUSES = ("completed", "cancelled")

# K-33 result column cap (matches K-32's "result JSON <= 4000 chars").
MAX_RESULT_CHARS = 4000

# K-32 FORBIDDEN_SCHEDULED_ACTIONS: never allowed to be scheduled, confirmed or not.
_LOCAL_FORBIDDEN_ACTIONS = {"confirm_action", "schedule_task", "cancel_scheduled_task", "set_reminder"}

# Background publish tasks kept alive until they finish (K-13 _emit_tool_sync pattern).
_publish_tasks: "set[asyncio.Task[None]]" = set()
# First loop that ran publish — used from worker threads (C-125 B7 / C-86 routes).
_publish_loop: Optional[asyncio.AbstractEventLoop] = None


def _due_at_epoch_seconds(scheduled_time: Any) -> Optional[float]:
    """Convert stored scheduled_time (ISO naive in scheduling TZ) to epoch seconds."""
    if scheduled_time is None:
        return None
    if isinstance(scheduled_time, (int, float)):
        return float(scheduled_time)
    text = str(scheduled_time).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    tz_name = (os.getenv("TZ") or _DEFAULT_TZ).strip() or _DEFAULT_TZ
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pylint: disable=broad-exception-caught
        tz = ZoneInfo(_DEFAULT_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()

# X-66: busy_timeout (ms) every connection waits for a lock held by another process/thread before raising
# "database is locked", on top of WAL journalling (readers never block writers, and vice versa).
_BUSY_TIMEOUT_MS = 5000


def _connect() -> sqlite3.Connection:
    """A tasks.db connection with WAL journalling and a 5 s busy_timeout (X-66), so two processes or threads
    writing at the same time serialise instead of losing an update or raising "database is locked"."""
    conn = sqlite3.connect(DB_PATH, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _decode_action_args(raw: Any) -> Optional[dict]:
    """Best-effort JSON decode of the action_args column; never raises."""
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _encode_result(result: Any) -> Optional[str]:
    """Stores a result/error as text, capped at MAX_RESULT_CHARS (K-32)."""
    if result is None:
        return None
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(result)
    return text[:MAX_RESULT_CHARS]


def _short_text(value: Any, limit: int = 60) -> str:
    if not value:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


async def _post_task_sync(tasks: List[Dict[str, Any]]) -> None:
    """EXT DASH-U13: best-effort task_sync broadcast; failures are swallowed and logged quietly."""
    try:
        # pylint: disable=import-outside-toplevel
        from services.utils import jarvis_bridge
        payload = {
            "tasks": [
                {
                    "id": row.get("id"),
                    "description": row.get("description"),
                    "due_at": _due_at_epoch_seconds(row.get("scheduled_time")),
                    "source": "task",
                    "kind": row.get("kind") or "task",
                    "status": row.get("status"),
                }
                for row in tasks
            ]
        }
        await jarvis_bridge.notify_event("task_sync", payload)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("task_sync publish skipped: %s", e)


# ---------------------------------------------------------------------------
# parse_when: K-33 relative/absolute time parser (English + Roman Urdu)
# ---------------------------------------------------------------------------

_NUM_WORDS: Dict[str, int] = {
    "ek": 1, "aik": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4,
    "panch": 5, "paanch": 5, "chay": 6, "chhay": 6, "cheh": 6,
    "saat": 7, "saath": 7, "aath": 8, "nau": 9, "das": 10, "dus": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnees": 19, "bees": 20,
}

_MINUTE_UNITS = ("minutes", "minute", "mins", "min", "mints", "mint")
_HOUR_UNITS = ("hours", "hour", "ghanty", "ghante", "ghanta")
_SECOND_UNITS = ("seconds", "second", "secs", "sec")
_DAY_UNITS = ("days", "day", "din")

_UNIT_TO_KWARG: Dict[str, str] = {}
for _u in _MINUTE_UNITS:
    _UNIT_TO_KWARG[_u] = "minutes"
for _u in _HOUR_UNITS:
    _UNIT_TO_KWARG[_u] = "hours"
for _u in _SECOND_UNITS:
    _UNIT_TO_KWARG[_u] = "seconds"
for _u in _DAY_UNITS:
    _UNIT_TO_KWARG[_u] = "days"

_UNIT_PATTERN = "|".join(sorted(_UNIT_TO_KWARG, key=len, reverse=True))
_WORD_PATTERN = "|".join(sorted(_NUM_WORDS, key=len, reverse=True))

_RELATIVE_RE = re.compile(
    rf"\b(?P<num>\d+|{_WORD_PATTERN})\b\s*(?P<unit>{_UNIT_PATTERN})\b", re.IGNORECASE
)
_BARE_UNIT_RE = re.compile(rf"\b(?:{_UNIT_PATTERN})\b", re.IGNORECASE)

_HHMM_AMPM_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*([ap])\.?m?\.?\b", re.IGNORECASE)
_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_YMD_HM_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2}(?::\d{2})?)\s*$")
_TOMORROW_RE = re.compile(r"\b(kal|tomorrow)\b", re.IGNORECASE)


def _extract_clock(text: str) -> Optional[time]:
    """Pulls an explicit h:mm am/pm or HH:MM clock time out of free text."""
    ampm = _HHMM_AMPM_RE.search(text)
    if ampm:
        hour = int(ampm.group(1)) % 12
        minute = int(ampm.group(2))
        if minute > 59 or int(ampm.group(1)) > 12:
            return None
        if ampm.group(3).lower() == "p":
            hour += 12
        return time(hour=hour, minute=minute)
    hhmm = _HHMM_RE.match(text.strip())
    if hhmm:
        hour, minute = int(hhmm.group(1)), int(hhmm.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour=hour, minute=minute)
    return None


def parse_when(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """
    K-33 time parser used by schedule_task and set_reminder.

    Understands: 'in 10 minutes'/'das minute baad' (English + Roman-Urdu units and number
    words), 'HH:MM' (24h), 'h:mm am/pm', 'YYYY-MM-DD HH:MM', ISO 8601, and 'kal'/'tomorrow
    HH:MM'. Returns None when the text cannot be understood, including a bare unit with no
    number (e.g. 'minutes' alone), rather than guessing.
    """
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    base = now or scheduling_now()
    lowered = raw.lower()

    # 1. 'kal' / 'tomorrow', optionally followed by a clock time.
    tomorrow_match = _TOMORROW_RE.search(lowered)
    if tomorrow_match:
        remainder = lowered[: tomorrow_match.start()] + lowered[tomorrow_match.end():]
        target_date = (base + timedelta(days=1)).date()
        clock = _extract_clock(remainder)
        if clock is not None:
            return datetime.combine(target_date, clock)
        return datetime.combine(target_date, base.time())

    # 2. explicit 'YYYY-MM-DD HH:MM' (space or 'T' separated, seconds optional).
    ymd_match = _YMD_HM_RE.match(raw)
    if ymd_match:
        try:
            return datetime.fromisoformat(f"{ymd_match.group(1)}T{ymd_match.group(2)}")
        except ValueError:
            pass

    # 3. plain ISO 8601 (date required; datetime.fromisoformat rejects a bare time).
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass

    # 4. relative duration: 'in 10 minutes', 'das minute baad', '2 ghante', etc.
    rel_match = _RELATIVE_RE.search(lowered)
    if rel_match:
        num_text = rel_match.group("num")
        unit_text = rel_match.group("unit").lower()
        amount = int(num_text) if num_text.isdigit() else _NUM_WORDS.get(num_text.lower())
        if amount is not None:
            kwarg = _UNIT_TO_KWARG[unit_text]
            return base + timedelta(**{kwarg: amount})

    # A bare unit with no number in front of it is ambiguous: say so instead of guessing.
    if _BARE_UNIT_RE.search(lowered):
        return None

    # 5. 'h:mm am/pm'.
    ampm_match = _HHMM_AMPM_RE.search(lowered)
    if ampm_match and int(ampm_match.group(1)) <= 12:
        hour = int(ampm_match.group(1)) % 12
        minute = int(ampm_match.group(2))
        if minute <= 59:
            if ampm_match.group(3).lower() == "p":
                hour += 12
            candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate < base:
                candidate += timedelta(days=1)
            return candidate

    # 6. plain 'HH:MM' (24h, the whole string).
    hhmm_match = _HHMM_RE.match(raw)
    if hhmm_match:
        hour, minute = int(hhmm_match.group(1)), int(hhmm_match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate < base:
                candidate += timedelta(days=1)
            return candidate

    return None


# ---------------------------------------------------------------------------
# K-32 risk lookup used by schedule_task (decorator risk= and the forbidden guard)
# ---------------------------------------------------------------------------

_RISK_LEVELS = ("low", "medium", "high")


def _parse_action_args_json(raw: Any) -> dict:
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _dispatcher_action_risk(action: str, action_args: dict) -> Optional[str]:
    """Tries the real K-32 action_risk; None when the dispatcher is not importable/usable."""
    try:
        # pylint: disable=import-outside-toplevel
        from services.ai_core import scheduler_dispatcher
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    try:
        return scheduler_dispatcher.action_risk(action, action_args)
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _is_forbidden_action(action: str, action_args: dict) -> bool:
    """K-32 FORBIDDEN_SCHEDULED_ACTIONS, checked locally so it holds even without the dispatcher."""
    if not action:
        return False
    if action in _LOCAL_FORBIDDEN_ACTIONS:
        return True
    if action == "autonomous_self_repair" and bool(action_args.get("apply")):
        return True
    return _dispatcher_action_risk(action, action_args) == "forbidden"


def _scheduled_action_risk(args: Dict[str, Any]) -> str:
    """
    K-32 risk lookup used as schedule_task's decorator risk=. A forbidden action (or no action
    at all) resolves 'low' so the call runs immediately: schedule_task's own body then either
    stores a plain task or returns the forbidden-action error, neither of which needs Sir's
    approval. Any other named action is confirmed when scheduled: the real dispatcher's answer
    when it is importable, else 'high' (K-33 fallback, so an unknown risky-looking action is
    never scheduled without a confirmation).
    """
    action = str(args.get("action") or "").strip()
    if not action:
        return "low"
    action_args = _parse_action_args_json(args.get("action_args_json"))
    if _is_forbidden_action(action, action_args):
        return "low"
    dispatcher_level = _dispatcher_action_risk(action, action_args)
    if dispatcher_level in _RISK_LEVELS:
        return dispatcher_level
    return "high"


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------

class TaskManager:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _connect_db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    action TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
            """)
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            migrations = [
                ("kind", "TEXT DEFAULT 'task'"),
                ("action_args", "TEXT"),
                ("result", "TEXT"),
                ("attempts", "INTEGER DEFAULT 0"),
                ("updated_at", "TEXT"),
                ("confirm_id", "TEXT"),
            ]
            for col, decl in migrations:
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
            conn.commit()

    def _publish(self) -> None:
        """EXT DASH-U13: fire-and-forget task_sync; safe from worker threads (C-125 B7)."""
        global _publish_loop
        try:
            snapshot = self.list_pending_tasks()
        except Exception:  # pylint: disable=broad-exception-caught
            snapshot = []
        try:
            loop = asyncio.get_running_loop()
            _publish_loop = loop
            task = loop.create_task(_post_task_sync(snapshot))
            _publish_tasks.add(task)
            task.add_done_callback(_publish_tasks.discard)
            return
        except RuntimeError:
            pass
        loop = _publish_loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(_post_task_sync(snapshot), loop)

    async def publish_snapshot(self) -> None:
        """Async publish for agent-svc / agent_core: read SQLite off the event loop."""
        try:
            snapshot = await asyncio.to_thread(self.list_pending_tasks)
        except Exception:  # pylint: disable=broad-exception-caught
            snapshot = []
        await _post_task_sync(snapshot)

    def _set_status(self, task_id: int, status: str, **extra_cols: Any) -> None:
        now = scheduling_now().isoformat()
        cols = ["status = ?", "updated_at = ?"]
        params: List[Any] = [status, now]
        for col, val in extra_cols.items():
            cols.append(f"{col} = ?")
            params.append(val)
        params.append(task_id)
        with _connect_db() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE id = ?", params)
            conn.commit()
        self._publish()

    # -- writes ------------------------------------------------------------

    def add_task(self, description: str, scheduled_time: datetime, action: str = None,
                 action_args: Optional[dict] = None, kind: str = "task") -> int:
        args_json = json.dumps(action_args) if action_args is not None else None
        now = scheduling_now().isoformat()
        with _connect_db() as conn:
            cursor = conn.execute(
                """INSERT INTO tasks
                   (description, scheduled_time, action, action_args, kind, status,
                    created_at, updated_at, attempts)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, 0)""",
                (description, scheduled_time.isoformat(), action, args_json, kind, now, now),
            )
            conn.commit()
            task_id = cursor.lastrowid
        self._publish()
        return task_id

    def mark_running(self, task_id: int) -> None:
        self._set_status(task_id, "running")

    def mark_awaiting_confirmation(self, task_id: int, confirm_id: str) -> None:
        self._set_status(task_id, "awaiting_confirmation", confirm_id=confirm_id)

    def mark_completed(self, task_id: int, result: Any = None) -> None:
        """Old positional-only call form (mark_completed(id)) still works."""
        self._set_status(task_id, "completed", result=_encode_result(result))

    def mark_failed(self, task_id: int, error: Any = None) -> None:
        self._set_status(task_id, "failed", result=_encode_result(error))

    def increment_attempts(self, task_id: int) -> int:
        with _connect_db() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT attempts FROM tasks WHERE id = ?", (task_id,)).fetchone()
            current = row["attempts"] if row and row["attempts"] is not None else 0
            new_value = current + 1
            now = scheduling_now().isoformat()
            conn.execute(
                "UPDATE tasks SET attempts = ?, updated_at = ? WHERE id = ?",
                (new_value, now, task_id),
            )
            conn.commit()
        self._publish()
        return new_value

    def cancel_task(self, task_id: int) -> None:
        self._set_status(task_id, "cancelled")

    # -- reads ---------------------------------------------------------------

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        with _connect_db() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["action_args"] = _decode_action_args(data.get("action_args"))
        return data

    def list_pending_tasks(self, kind: Optional[str] = None) -> List[Dict]:
        query = "SELECT * FROM tasks WHERE status = 'pending'"
        params: List[Any] = []
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY scheduled_time ASC"
        with _connect_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["action_args"] = _decode_action_args(row.get("action_args"))
        return rows

    def list_triggered_tasks(self) -> List[Dict]:
        """VCR-19 voice review: tasks the due-loop has triggered but not yet resolved."""
        with _connect_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status = 'triggered' ORDER BY scheduled_time ASC"
            )
            rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["action_args"] = _decode_action_args(row.get("action_args"))
        return rows

    def get_due_tasks(self) -> List[Dict]:
        """Marks due pending rows 'triggered' atomically and returns them with action_args decoded."""
        now = scheduling_now().isoformat()
        with _connect_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending' AND scheduled_time <= ?", (now,)
            )
            tasks = [dict(row) for row in cursor.fetchall()]

            # Mark as triggered immediately to avoid double execution
            for t in tasks:
                conn.execute(
                    "UPDATE tasks SET status = 'triggered', updated_at = ? WHERE id = ?",
                    (now, t["id"]),
                )
            conn.commit()
        for t in tasks:
            t["action_args"] = _decode_action_args(t.get("action_args"))
        if tasks:
            self._publish()
        return tasks


# Global Instance
task_manager = TaskManager()


@jarvis_tool(
    arg_policies={"action_args_json": "raw", "description": "multiline"},
    risk=_scheduled_action_risk,
)
async def schedule_task(time_str: str, description: str, action: str = "",
                         action_args_json: str = "") -> dict:
    """
    Schedules an advanced task, optionally running a tool action when it is due.
    time_str: e.g. '2026-04-04 09:00', 'in 10 minutes', 'das minute baad', '9:00 pm', 'kal 14:00'.
    description: what needs to be done.
    action: optional specific tool to trigger when due (e.g. 'get_weather').
    action_args_json: a JSON object string of arguments for that action, e.g. '{"city":"Lahore"}'.
    """
    action = (action or "").strip()
    action_args_json = action_args_json or ""

    parsed_args = None
    if action_args_json.strip():
        try:
            parsed_args = json.loads(action_args_json)
        except (TypeError, ValueError) as e:
            return {
                "status": "error",
                "message": f"Sir, action_args_json valid JSON nahi hai: {e}",
            }
        if not isinstance(parsed_args, dict):
            return {
                "status": "error",
                "message": (
                    "Sir, action_args_json ek JSON object hona chahiye, "
                    'e.g. {"city": "Lahore"}.'
                ),
            }

    if action and _is_forbidden_action(action, parsed_args or {}):
        return {
            "status": "error",
            "message": f"Sir, '{action}' ko schedule karna allowed nahi hai.",
        }

    try:
        target_time = parse_when(time_str)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("parse_when failed for %r: %s", time_str, e)
        target_time = None

    if not target_time:
        return {"status": "error", "message": "Sir, main time format nahi samajh paya."}

    try:
        task_id = task_manager.add_task(
            description, target_time, action=action or None, action_args=parsed_args,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error scheduling task: %s", e)
        return {"status": "error", "message": str(e)}

    return {
        "status": "success",
        "task_id": task_id,
        "scheduled_for": target_time.strftime("%Y-%m-%d %I:%M %p"),
        "message": (
            f"✅ Task scheduled successfully, Sir Matloob. Main '{description}' "
            f"{target_time.strftime('%I:%M %p')} par dekhoon ga."
        ),
    }


@jarvis_tool
async def cancel_scheduled_task(task_id: int) -> dict:
    """Cancels a previously scheduled task by its id (from list_tasks)."""
    existing = task_manager.get_task(task_id)
    if existing is None:
        return {"status": "error", "message": f"Sir, task #{task_id} nahi mila."}
    if existing.get("status") in _TERMINAL_STATUSES:
        return {
            "status": "error",
            "message": f"Sir, task #{task_id} already {existing['status']} hai.",
        }
    task_manager.cancel_task(task_id)
    return {
        "status": "success",
        "task_id": task_id,
        "message": f"✅ Task #{task_id} cancel kar diya, Sir.",
    }


@jarvis_tool
async def list_tasks() -> dict:
    """Lists all pending tasks and scheduled actions."""
    tasks = task_manager.list_pending_tasks()
    if not tasks:
        return {"status": "empty", "message": "Sir, abhi koi pending tasks nahi hain."}

    lines = []
    for t in tasks:
        kind = t.get("kind") or "task"
        line = f"[{t['id']}] ({kind}/{t['status']}) {t['scheduled_time']} - {t['description']}"
        result_preview = _short_text(t.get("result"))
        if result_preview:
            line += f" -> {result_preview}"
        lines.append(line)
    summary = "\n".join(lines)

    return {
        "status": "success",
        "count": len(tasks),
        "tasks": tasks,
        "message": f"Sir, aapke pending tasks ye hain:\n{summary}"
    }
