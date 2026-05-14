"""
# jarvis_task_manager.py
Advanced Persistent Task Scheduler for JARVIS.
Uses SQLite for state persistence across restarts.
"""

import asyncio
import sqlite3
import os
from datetime import datetime
from typing import Any, List, Dict
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_plugin_manager import jarvis_tool

logger = setup_logger("JARVIS-TASKS")

DB_PATH = os.path.join(config.project_root, "conversations", "tasks.db")

class TaskManager:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
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
            conn.commit()

    def add_task(self, description: str, scheduled_time: datetime, action: str = None) -> int:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """INSERT INTO tasks (description, scheduled_time, action, created_at) 
                   VALUES (?, ?, ?, ?)""",
                (description, scheduled_time.isoformat(), action, datetime.now().isoformat())
            )
            return cursor.lastrowid

    def list_pending_tasks(self) -> List[Dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM tasks WHERE status = 'pending' ORDER BY scheduled_time ASC")
            return [dict(row) for row in cursor.fetchall()]

    def get_due_tasks(self) -> List[Dict]:
        now = datetime.now().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending' AND scheduled_time <= ?", (now,)
            )
            tasks = [dict(row) for row in cursor.fetchall()]
            
            # Mark as triggered immediately to avoid double execution
            for t in tasks:
                conn.execute("UPDATE tasks SET status = 'triggered' WHERE id = ?", (t['id'],))
            conn.commit()
            return tasks

    def mark_completed(self, task_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
            conn.commit()

    def cancel_task(self, task_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
            conn.commit()

# Global Instance
task_manager = TaskManager()

@jarvis_tool
async def schedule_task(time_str: str, description: str, action: str = None) -> dict:
    """
    Schedules an advanced task or reminder.
    time_str: e.g., '2026-04-04 09:00' or 'in 10 minutes'.
    description: What needs to be done.
    action: Optional specific tool/action to trigger (e.g., 'check_email', 'summary').
    """
    from datetime import timedelta
    now = datetime.now()
    target_time = None

    try:
        # Simple relative time parsing
        if "minute" in time_str.lower():
            mins = int(''.join(filter(str.isdigit, time_str)))
            target_time = now + timedelta(minutes=mins)
        elif "hour" in time_str.lower():
            hrs = int(''.join(filter(str.isdigit, time_str)))
            target_time = now + timedelta(hours=hrs)
        else:
            # Try absolute formats
            try:
                # Try HH:MM
                parsed = datetime.strptime(time_str.strip(), "%H:%M").time()
                target_time = datetime.combine(now.date(), parsed)
                if target_time < now: target_time += timedelta(days=1)
            except ValueError:
                # Try YYYY-MM-DD HH:MM
                target_time = datetime.fromisoformat(time_str.replace(" ", "T"))

        if not target_time:
            return {"status": "error", "message": "Sir, main time format nahi samajh paya."}

        task_id = task_manager.add_task(description, target_time, action)
        
        return {
            "status": "success",
            "task_id": task_id,
            "scheduled_for": target_time.strftime("%Y-%m-%d %I:%M %p"),
            "message": f"✅ Task scheduled successfully, Sir Matloob. Main '{description}' {target_time.strftime('%I:%M %p')} par yaad dilaon ga."
        }
    except Exception as e:
        logger.error("Error scheduling task: %s", e)
        return {"status": "error", "message": str(e)}

@jarvis_tool
async def list_tasks() -> dict:
    """Lists all pending tasks and scheduled actions."""
    tasks = task_manager.list_pending_tasks()
    if not tasks:
        return {"status": "empty", "message": "Sir, abhi koi pending tasks nahi hain."}
    
    summary = "\n".join([f"[{t['id']}] {t['scheduled_time']} - {t['description']}" for t in tasks])
    return {
        "status": "success",
        "count": len(tasks),
        "tasks": tasks,
        "message": f"Sir, aapke pending tasks ye hain:\n{summary}"
    }
