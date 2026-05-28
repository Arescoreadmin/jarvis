"""
Task management — local SQLite-backed task list.
Can integrate with Todoist or Linear if API keys are present.
"""
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.registry import ToolBase, ToolSafety

TASKS_DB = Path(__file__).parent.parent / "data" / "tasks.db"


def _init_tasks_db() -> sqlite3.Connection:
    TASKS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TASKS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            notes TEXT,
            project TEXT,
            due TEXT,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    conn.commit()
    return conn


class TaskTool(ToolBase):
    name = "task_create"
    description = (
        "Create, update, list, and complete tasks. "
        "Tasks support priority (high/medium/low), due dates, and project tags."
    )
    action_policies = {
        "create": ToolSafety(action_type="task_write", risk_level="low", requires_confirmation=False, reason="Creates a local task."),
        "list": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Lists local tasks."),
        "complete": ToolSafety(action_type="task_write", risk_level="low", requires_confirmation=False, reason="Marks a local task complete."),
        "update": ToolSafety(action_type="task_write", risk_level="medium", requires_confirmation=False, reason="Updates a local task."),
        "delete": ToolSafety(action_type="destructive_task_write", risk_level="medium", requires_confirmation=True, reason="Deletes a local task."),
    }

    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "complete", "update", "delete"],
                "description": "Task action",
            },
            "title": {"type": "string", "description": "Task title"},
            "notes": {"type": "string", "description": "Optional notes"},
            "project": {"type": "string", "description": "Project tag"},
            "due": {"type": "string", "description": "Due date (ISO or natural language)"},
            "priority": {"type": "string", "enum": ["high", "medium", "low"], "default": "medium"},
            "task_id": {"type": "string", "description": "Task ID for complete/update/delete"},
            "filter": {"type": "string", "description": "Filter for list: all | today | high | project:<name>"},
        },
        "required": ["action"],
    }

    def __init__(self):
        self._conn = _init_tasks_db()

    async def run(self, action: str, **kwargs) -> str:
        if action == "create":
            return self._create(**kwargs)
        if action == "list":
            return self._list(kwargs.get("filter", "all"))
        if action == "complete":
            return self._complete(kwargs.get("task_id", ""))
        if action == "update":
            return self._update(kwargs.get("task_id", ""), **kwargs)
        if action == "delete":
            return self._delete(kwargs.get("task_id", ""))
        return "Unknown task action"

    def _create(self, title: str = "", notes: str = "", project: str = "",
                 due: str = "", priority: str = "medium", **_) -> str:
        if not title:
            return "Title required"
        tid = str(uuid.uuid4())[:8]
        self._conn.execute(
            "INSERT INTO tasks (id, title, notes, project, due, priority, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
            (tid, title, notes, project, due, priority, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return f"Task created [{tid}]: {title}"

    def _list(self, filter_str: str = "all") -> str:
        if filter_str == "all" or not filter_str:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'open' ORDER BY priority DESC, due ASC NULLS LAST"
            ).fetchall()
        elif filter_str == "high":
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'open' AND priority = 'high'"
            ).fetchall()
        elif filter_str.startswith("project:"):
            proj = filter_str.split(":", 1)[1]
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'open' AND project = ?", (proj,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'open' ORDER BY created_at DESC"
            ).fetchall()

        if not rows:
            return "No open tasks."
        lines = []
        for r in rows:
            due = f" [due {r['due']}]" if r["due"] else ""
            proj = f" ({r['project']})" if r["project"] else ""
            priority_marker = "!" if r["priority"] == "high" else ""
            lines.append(f"[{r['id']}]{priority_marker} {r['title']}{proj}{due}")
        return "\n".join(lines)

    def _complete(self, task_id: str) -> str:
        if not task_id:
            return "task_id required"
        row = self._conn.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found"
        self._conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        self._conn.commit()
        return f"Done: {row['title']}"

    def _update(self, task_id: str, title: str = "", due: str = "",
                 priority: str = "", notes: str = "", **_) -> str:
        if not task_id:
            return "task_id required"
        updates = {}
        if title:
            updates["title"] = title
        if due:
            updates["due"] = due
        if priority:
            updates["priority"] = priority
        if notes:
            updates["notes"] = notes
        if not updates:
            return "Nothing to update"
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            (*updates.values(), task_id),
        )
        self._conn.commit()
        return f"Updated task {task_id}"

    def _delete(self, task_id: str) -> str:
        self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._conn.commit()
        return f"Deleted task {task_id}"

    async def get_pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE status = 'open'"
        ).fetchone()
        return row["c"] if row else 0
