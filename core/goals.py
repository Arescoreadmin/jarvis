"""
Goal Engine — weekly, personal, actionable goal tracking.

Separate from the OKR/Strategy layer (which is quarterly and business-level).
Goals are concrete, short-horizon, and milestone-driven. Jarvis tracks them,
surfaces blocked ones proactively, and links them back to strategic objectives.

Schema:
  goals       — individual goals with deadline + linked objective
  milestones  — ordered steps within a goal
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

DB_PATH = Path(__file__).parent.parent / "data" / "goals.db"

GOALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    deadline TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    linked_objective_id TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES goals(id),
    title TEXT NOT NULL,
    due_date TEXT,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_milestones_goal ON milestones(goal_id);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_deadline ON goals(deadline);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class GoalEngine:
    MODEL = "claude-sonnet-4-6"

    def __init__(self):
        self._conn = _connect()
        self._conn.executescript(GOALS_SCHEMA)
        self._conn.commit()
        self._client = anthropic.AsyncAnthropic()

    # ── Goals ─────────────────────────────────────────────────────────────────

    def add_goal(
        self,
        title: str,
        description: str = "",
        deadline: str = "",
        priority: str = "medium",
        linked_objective_id: str = "",
    ) -> str:
        gid = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """INSERT INTO goals
               (id, title, description, deadline, status, linked_objective_id, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
            (gid, title, description, deadline or None, linked_objective_id or None, priority, now, now),
        )
        self._conn.commit()
        return gid

    def add_milestone(
        self,
        goal_id: str,
        title: str,
        due_date: str = "",
    ) -> str:
        mid = str(uuid.uuid4())
        # Sort order = next available
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM milestones WHERE goal_id = ?", (goal_id,)
        ).fetchone()
        sort_order = (row["c"] or 0)
        self._conn.execute(
            """INSERT INTO milestones (id, goal_id, title, due_date, status, sort_order, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (mid, goal_id, title, due_date or None, sort_order, _now()),
        )
        self._conn.commit()
        return mid

    def complete_milestone(self, milestone_id: str) -> dict:
        self._conn.execute(
            "UPDATE milestones SET status = 'complete', completed_at = ? WHERE id = ?",
            (_now(), milestone_id),
        )
        self._conn.commit()

        # Check if all milestones for the parent goal are done
        row = self._conn.execute(
            "SELECT goal_id FROM milestones WHERE id = ?", (milestone_id,)
        ).fetchone()
        if row:
            self._refresh_goal_status(row["goal_id"])

        return {"milestone_id": milestone_id, "status": "complete"}

    def _refresh_goal_status(self, goal_id: str) -> None:
        rows = self._conn.execute(
            "SELECT status FROM milestones WHERE goal_id = ?", (goal_id,)
        ).fetchall()
        if not rows:
            return
        statuses = [r["status"] for r in rows]
        if all(s == "complete" for s in statuses):
            self._conn.execute(
                "UPDATE goals SET status = 'complete', updated_at = ? WHERE id = ?",
                (_now(), goal_id),
            )
            self._conn.commit()

    def set_goal_status(self, goal_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE goals SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), goal_id),
        )
        self._conn.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_active_goals(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM goals WHERE status = 'active'
               ORDER BY
                 CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 deadline ASC NULLS LAST"""
        ).fetchall()
        return [self._hydrate(dict(r)) for r in rows]

    def get_blocked(self) -> list[dict]:
        """Goals with at least one overdue milestone."""
        now = _now()
        rows = self._conn.execute(
            """SELECT DISTINCT g.* FROM goals g
               JOIN milestones m ON m.goal_id = g.id
               WHERE g.status = 'active'
                 AND m.status = 'pending'
                 AND m.due_date IS NOT NULL
                 AND m.due_date < ?
               ORDER BY g.deadline ASC NULLS LAST""",
            (now,),
        ).fetchall()
        return [self._hydrate(dict(r)) for r in rows]

    def get_goal(self, goal_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM goals WHERE id = ?", (goal_id,)
        ).fetchone()
        return self._hydrate(dict(row)) if row else None

    def _hydrate(self, goal: dict) -> dict:
        milestones = self._conn.execute(
            "SELECT * FROM milestones WHERE goal_id = ? ORDER BY sort_order",
            (goal["id"],),
        ).fetchall()
        ms = [dict(m) for m in milestones]
        total = len(ms)
        done = sum(1 for m in ms if m["status"] == "complete")
        goal["milestones"] = ms
        goal["milestone_count"] = total
        goal["milestones_done"] = done
        goal["pct_complete"] = round(done / total * 100, 1) if total else 0.0
        return goal

    # ── Context block ─────────────────────────────────────────────────────────

    def get_context_block(self) -> str:
        """Compact summary for Brain context injection."""
        active = self.get_active_goals()
        if not active:
            return ""

        blocked = self.get_blocked()
        blocked_ids = {g["id"] for g in blocked}

        lines = ["[ACTIVE GOALS]"]
        for g in active[:5]:
            flag = " ⚠ BLOCKED" if g["id"] in blocked_ids else ""
            deadline = f" (due {g['deadline'][:10]})" if g.get("deadline") else ""
            lines.append(
                f"• [{g['priority'].upper()}] {g['title']}{deadline}"
                f" — {g['pct_complete']}% complete{flag}"
            )
        if len(active) > 5:
            lines.append(f"  ...and {len(active) - 5} more")
        if blocked:
            lines.append(f"BLOCKED: {len(blocked)} goal(s) have overdue milestones")
        return "\n".join(lines)

    # ── AI helpers ────────────────────────────────────────────────────────────

    async def suggest_milestones(self, goal_title: str, goal_description: str = "") -> list[str]:
        """Ask Claude to break a goal into concrete milestones."""
        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=600,
            system=(
                "You are a productivity coach. Break goals into 3-6 concrete, "
                "actionable milestones. Output a JSON array of strings only — "
                "no explanation, no markdown."
            ),
            messages=[{
                "role": "user",
                "content": f"Goal: {goal_title}\n{goal_description}".strip(),
            }],
        )
        text = resp.content[0].text.strip()
        try:
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except (json.JSONDecodeError, IndexError):
            # Fall back: split by newline, strip bullets
            return [
                line.lstrip("•-0123456789. ").strip()
                for line in text.splitlines()
                if line.strip()
            ][:6]

    def close(self) -> None:
        self._conn.close()
