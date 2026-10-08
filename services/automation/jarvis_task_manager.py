"""
# jarvis_task_manager.py
TaskStore v2 (X-32 / K-33): SQLite tasks with action_args, parse_when, cancel tool.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-TASKS")

DB_PATH = os.path.join(config.project_root, "conversations", "tasks.db")

_NUMBER_WORDS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "panch": 5, "paanch": 5,
    "chhe": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnees": 19, "bees": 20,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "saath": 60,
}

_NEW_COLUMNS = (
    ("kind", "TEXT DEFAULT 'task'"),
    ("action_args", "TEXT"),
    ("result", "TEXT"),
    ("attempts", "INTEGER DEFAULT 0"),
    ("updated_at", "TEXT"),
    ("confirm_id", "TEXT"),
)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _publish_tasks() -> None:
    """EXT DASH-U13: best-effort task_sync after every store change."""
    try:
        from services.utils.jarvis_bridge import notify_event  # lazy
        import asyncio

        pending = task_manager.list_pending_tasks()
        payload = {"tasks": pending, "pending": pending}

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(notify_event("task_sync", payload))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("task publish skipped: %s", exc)


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """
    Parse English / Roman-Urdu relative and absolute times.
    Returns None when the unit has no number or the text is unrecognised.
    """
    if not text or not str(text).strip():
        return None
    now = now or datetime.now()
    raw = str(text).strip()
    lower = raw.lower().strip()

    # ISO / YYYY-MM-DD HH:MM
    for candidate in (raw, raw.replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt
        except ValueError:
            pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        pass

    # kal / tomorrow [HH:MM]
    m = re.match(r"^(kal|tomorrow)\s*(\d{1,2}:\d{2})?$", lower)
    if m:
        base = now.date() + timedelta(days=1)
        if m.group(2):
            hh, mm = m.group(2).split(":")
            return datetime.combine(base, datetime.strptime(f"{int(hh):02d}:{mm}", "%H:%M").time())
        return datetime.combine(base, now.time().replace(second=0, microsecond=0))

    # h:mm am/pm
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", lower)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ap = m.group(3)
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        target = datetime.combine(now.date(), datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M").time())
        if target < now:
            target += timedelta(days=1)
        return target

    # HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", lower)
    if m:
        try:
            parsed = datetime.strptime(f"{int(m.group(1)):02d}:{m.group(2)}", "%H:%M").time()
            target = datetime.combine(now.date(), parsed)
            if target < now:
                target += timedelta(days=1)
            return target
        except ValueError:
            pass

    def _extract_number(s: str) -> int | None:
        digits = re.search(r"(\d+)", s)
        if digits:
            return int(digits.group(1))
        for word, val in _NUMBER_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", s):
                return val
        return None

    # Relative units
    unit_patterns = [
        (r"(second|seconds|sec|secs)\b", "seconds"),
        (r"(minute|minutes|min|mins|mint)\b", "minutes"),
        (r"(hour|hours|ghanta|ghante)\b", "hours"),
        (r"(day|days|din)\b", "days"),
    ]
    # "in 10 minutes" / "das minute baad"
    if re.search(r"\b(in|baad|after)\b", lower) or any(
        re.search(p[0], lower) for p in unit_patterns
    ):
        for pat, unit in unit_patterns:
            if re.search(pat, lower):
                n = _extract_number(lower)
                if n is None:
                    return None
                if unit == "seconds":
                    return now + timedelta(seconds=n)
                if unit == "minutes":
                    return now + timedelta(minutes=n)
                if unit == "hours":
                    return now + timedelta(hours=n)
                if unit == "days":
                    return now + timedelta(days=n)

    return None


class TaskManager:
    def __init__(self) -> None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    description TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    action TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            for name, decl in _NEW_COLUMNS:
                if name not in cols:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
            conn.commit()

    def add_task(
        self,
        description: str,
        scheduled_time: datetime,
        action: str | None = None,
        action_args: dict | list | None = None,
        kind: str = "task",
    ) -> int:
        args_json = json.dumps(action_args) if action_args is not None else None
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks
                (description, scheduled_time, action, action_args, kind, status, created_at, updated_at, attempts)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, 0)
                """,
                (
                    description,
                    scheduled_time.isoformat(),
                    action,
                    args_json,
                    kind or "task",
                    _now_iso(),
                    _now_iso(),
                ),
            )
            conn.commit()
            task_id = int(cur.lastrowid)
        _publish_tasks()
        return task_id

    def _decode_row(self, row: sqlite3.Row | dict) -> dict:
        d = dict(row)
        raw = d.get("action_args")
        if isinstance(raw, str) and raw.strip():
            try:
                d["action_args"] = json.loads(raw)
            except json.JSONDecodeError:
                d["action_args"] = None
        return d

    def get_due_tasks(self) -> List[Dict]:
        now = _now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending' AND scheduled_time <= ?",
                (now,),
            ).fetchall()
            tasks = [self._decode_row(r) for r in rows]
            for t in tasks:
                conn.execute(
                    "UPDATE tasks SET status = 'triggered', updated_at = ? WHERE id = ? AND status = 'pending'",
                    (_now_iso(), t["id"]),
                )
                t["status"] = "triggered"
            conn.commit()
        if tasks:
            _publish_tasks()
        return tasks

    def list_triggered_tasks(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'triggered' ORDER BY scheduled_time ASC"
            ).fetchall()
            return [self._decode_row(r) for r in rows]

    def mark_running(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?",
                (_now_iso(), task_id),
            )
            conn.commit()
        _publish_tasks()

    def mark_awaiting_confirmation(self, task_id: int, confirm_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'awaiting_confirmation', confirm_id = ?, updated_at = ? WHERE id = ?",
                (confirm_id, _now_iso(), task_id),
            )
            conn.commit()
        _publish_tasks()

    def mark_completed(self, task_id: int, result: Any = None) -> None:
        result_s = None
        if result is not None:
            result_s = result if isinstance(result, str) else json.dumps(result)[:4000]
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'completed', result = ?, updated_at = ? WHERE id = ?",
                (result_s, _now_iso(), task_id),
            )
            conn.commit()
        _publish_tasks()

    def mark_failed(self, task_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'failed', result = ?, updated_at = ? WHERE id = ?",
                (str(error)[:4000], _now_iso(), task_id),
            )
            conn.commit()
        _publish_tasks()

    def increment_attempts(self, task_id: int) -> int:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET attempts = COALESCE(attempts, 0) + 1, updated_at = ? WHERE id = ?",
                (_now_iso(), task_id),
            )
            row = conn.execute("SELECT attempts FROM tasks WHERE id = ?", (task_id,)).fetchone()
            conn.commit()
        _publish_tasks()
        return int(row["attempts"]) if row else 0

    def get_task(self, task_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._decode_row(row) if row else None

    def list_pending_tasks(self, kind: str | None = None) -> List[Dict]:
        with self._connect() as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'pending' AND kind = ? ORDER BY scheduled_time ASC",
                    (kind,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'pending' ORDER BY scheduled_time ASC"
                ).fetchall()
            return [self._decode_row(r) for r in rows]

    def cancel_task(self, task_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE tasks SET status = 'cancelled', updated_at = ? WHERE id = ? AND status IN ('pending', 'triggered', 'awaiting_confirmation')",
                (_now_iso(), task_id),
            )
            conn.commit()
            ok = cur.rowcount > 0
        if ok:
            _publish_tasks()
        return ok


task_manager = TaskManager()


def _schedule_risk(arguments: dict[str, Any]) -> str:
    action = (arguments.get("action") or "").strip()
    if not action:
        return "low"
    args_raw = arguments.get("action_args_json") or "{}"
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
    except json.JSONDecodeError:
        args = {}
    try:
        from services.ai_core.scheduler_dispatcher import action_risk  # type: ignore

        return str(action_risk(action, args if isinstance(args, dict) else {}))
    except Exception:  # pylint: disable=broad-exception-caught
        return "high"


def _schedule_summary(arguments: dict[str, Any]) -> str:
    action = (arguments.get("action") or "").strip() or "(no action)"
    desc = (arguments.get("description") or "")[:80]
    return f"Schedule '{desc}' action={action}"


@jarvis_tool(
    timeout=20,
    arg_policies={"action_args_json": "raw", "description": "multiline"},
    risk=_schedule_risk,
    confirm_summary=_schedule_summary,
)
async def schedule_task(
    time_str: str,
    description: str,
    action: str = "",
    action_args_json: str = "",
) -> dict:
    """
    Schedule a task or tool action.
    time_str: e.g. 'in 10 minutes', 'das minute baad', '14:30', ISO.
    action_args_json: JSON object of tool kwargs (optional).
    """
    target = parse_when(time_str)
    if target is None:
        return {
            "status": "error",
            "message": "Sir, main time format nahi samajh paya. 'in 10 minutes' ya '14:30' try karein.",
        }

    action = (action or "").strip()
    args_obj: dict | None = None
    raw_args = (action_args_json or "").strip()
    if raw_args:
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "action_args_json valid JSON object hona chahiye.",
            }
        if not isinstance(parsed, dict):
            return {
                "status": "error",
                "message": "action_args_json ek JSON object hona chahiye (array nahi).",
            }
        args_obj = parsed

    if action:
        risk = _schedule_risk({"action": action, "action_args_json": raw_args or "{}"})
        if risk == "forbidden":
            return {
                "status": "error",
                "message": f"Action '{action}' schedule nahi ho sakti (forbidden).",
                "error_code": "FORBIDDEN_ACTION",
            }

    task_id = task_manager.add_task(
        description, target, action=action or None, action_args=args_obj, kind="task"
    )
    return {
        "status": "success",
        "task_id": task_id,
        "scheduled_for": target.strftime("%Y-%m-%d %I:%M %p"),
        "message": (
            f"✅ Task scheduled. Main '{description}' "
            f"{target.strftime('%I:%M %p')} par yaad dilaon ga."
        ),
    }


@jarvis_tool(timeout=10)
async def cancel_scheduled_task(task_id: int) -> dict:
    """Cancel a pending scheduled task by id."""
    ok = task_manager.cancel_task(int(task_id))
    if ok:
        return {
            "status": "success",
            "message": f"Task {task_id} cancel ho gaya.",
            "task_id": task_id,
        }
    return {
        "status": "not_found",
        "message": f"Task {task_id} pending nahi mila.",
        "task_id": task_id,
    }


@jarvis_tool
async def list_tasks() -> dict:
    """Lists all pending tasks and scheduled actions (kind, status, short result)."""
    tasks = task_manager.list_pending_tasks()
    if not tasks:
        return {"status": "empty", "message": "Sir, abhi koi pending tasks nahi hain."}

    lines = []
    for t in tasks:
        kind = t.get("kind") or "task"
        status = t.get("status") or "pending"
        result = (t.get("result") or "")[:60]
        extra = f" result={result}" if result else ""
        lines.append(
            f"[{t['id']}] ({kind}/{status}) {t.get('scheduled_time')} - {t.get('description')}{extra}"
        )
    summary = "\n".join(lines)
    return {
        "status": "success",
        "count": len(tasks),
        "tasks": tasks,
        "message": f"Sir, aapke pending tasks ye hain:\n{summary}",
    }
