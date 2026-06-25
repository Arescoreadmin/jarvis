"""
Strategic Planning Layer.

Tracks the business's high-level objectives and key results (OKRs) so every
Jarvis decision, recommendation, and autonomous action stays aligned to what
actually matters. Injected into Brain's system prompt on every call.

Horizons: weekly | monthly | quarterly | annual
Status:   on_track | at_risk | off_track | complete | paused
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

DB_PATH = Path(__file__).parent.parent / "data" / "strategy.db"

STRATEGY_SCHEMA = """
CREATE TABLE IF NOT EXISTS objectives (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    horizon TEXT NOT NULL DEFAULT 'quarterly',
    status TEXT NOT NULL DEFAULT 'on_track',
    owner TEXT,
    start_date TEXT,
    end_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS key_results (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL REFERENCES objectives(id),
    title TEXT NOT NULL,
    metric_type TEXT NOT NULL DEFAULT 'numeric',
    unit TEXT,
    target_value REAL,
    current_value REAL DEFAULT 0,
    baseline_value REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'on_track',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS progress_log (
    id TEXT PRIMARY KEY,
    kr_id TEXT NOT NULL REFERENCES key_results(id),
    value REAL,
    note TEXT,
    logged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kr_objective ON key_results(objective_id);
CREATE INDEX IF NOT EXISTS idx_progress_kr ON progress_log(kr_id);
CREATE INDEX IF NOT EXISTS idx_objectives_status ON objectives(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _pct(current: float, baseline: float, target: float) -> float:
    span = target - baseline
    if span == 0:
        return 100.0 if current >= target else 0.0
    return min(100.0, max(0.0, (current - baseline) / span * 100))


class StrategyEngine:
    MODEL = "claude-sonnet-4-6"

    def __init__(self):
        self._conn = _connect()
        self._conn.executescript(STRATEGY_SCHEMA)
        self._conn.commit()
        self._client = anthropic.AsyncAnthropic()

    # ── Objectives ────────────────────────────────────────────────────────────

    def add_objective(
        self,
        title: str,
        description: str = "",
        horizon: str = "quarterly",
        owner: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> str:
        oid = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            """INSERT INTO objectives
               (id, title, description, horizon, status, owner, start_date, end_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'on_track', ?, ?, ?, ?, ?)""",
            (oid, title, description, horizon, owner, start_date, end_date, now, now),
        )
        self._conn.commit()
        return oid

    def add_key_result(
        self,
        objective_id: str,
        title: str,
        target_value: float,
        baseline_value: float = 0,
        unit: str = "",
        metric_type: str = "numeric",
    ) -> str:
        krid = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO key_results
               (id, objective_id, title, metric_type, unit, target_value, current_value, baseline_value, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'on_track', ?)""",
            (krid, objective_id, title, metric_type, unit, target_value, baseline_value, baseline_value, _now()),
        )
        self._conn.commit()
        return krid

    def update_progress(
        self,
        kr_id: str,
        current_value: float,
        note: str = "",
    ) -> dict:
        row = self._conn.execute(
            "SELECT * FROM key_results WHERE id = ?", (kr_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Key result not found: {kr_id}")

        pct = _pct(current_value, row["baseline_value"], row["target_value"])
        status = "on_track" if pct >= 70 else ("at_risk" if pct >= 40 else "off_track")

        self._conn.execute(
            "UPDATE key_results SET current_value = ?, status = ?, updated_at = ? WHERE id = ?",
            (current_value, status, _now(), kr_id),
        )
        self._conn.execute(
            """INSERT INTO progress_log (id, kr_id, value, note, logged_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), kr_id, current_value, note, _now()),
        )
        self._conn.commit()

        # Roll up objective status
        self._refresh_objective_status(row["objective_id"])

        return {"kr_id": kr_id, "current": current_value, "pct_complete": round(pct, 1), "status": status}

    def _refresh_objective_status(self, objective_id: str) -> None:
        krs = self._conn.execute(
            "SELECT status FROM key_results WHERE objective_id = ?", (objective_id,)
        ).fetchall()
        if not krs:
            return
        statuses = [r["status"] for r in krs]
        if all(s == "complete" for s in statuses):
            obj_status = "complete"
        elif any(s == "off_track" for s in statuses):
            obj_status = "off_track"
        elif any(s == "at_risk" for s in statuses):
            obj_status = "at_risk"
        else:
            obj_status = "on_track"
        self._conn.execute(
            "UPDATE objectives SET status = ?, updated_at = ? WHERE id = ?",
            (obj_status, _now(), objective_id),
        )
        self._conn.commit()

    def set_objective_status(self, objective_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE objectives SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), objective_id),
        )
        self._conn.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    def list_objectives(
        self,
        horizon: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        query = "SELECT * FROM objectives WHERE 1=1"
        params: list = []
        if horizon:
            query += " AND horizon = ?"
            params.append(horizon)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            obj = dict(row)
            obj["key_results"] = self._get_krs(obj["id"])
            result.append(obj)
        return result

    def _get_krs(self, objective_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM key_results WHERE objective_id = ? ORDER BY rowid",
            (objective_id,),
        ).fetchall()
        krs = []
        for row in rows:
            kr = dict(row)
            kr["pct_complete"] = round(
                _pct(kr["current_value"] or 0, kr["baseline_value"] or 0, kr["target_value"] or 1),
                1,
            )
            krs.append(kr)
        return krs

    def get_objective(self, objective_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM objectives WHERE id = ?", (objective_id,)
        ).fetchone()
        if not row:
            return None
        obj = dict(row)
        obj["key_results"] = self._get_krs(obj["id"])
        return obj

    def get_at_risk(self) -> list[dict]:
        """Objectives that are at_risk or off_track."""
        rows = self._conn.execute(
            "SELECT * FROM objectives WHERE status IN ('at_risk', 'off_track') ORDER BY updated_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            obj = dict(row)
            obj["key_results"] = self._get_krs(obj["id"])
            result.append(obj)
        return result

    def get_progress_history(self, kr_id: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM progress_log WHERE kr_id = ? ORDER BY logged_at DESC LIMIT ?",
            (kr_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Context injection ─────────────────────────────────────────────────────

    def get_active_plan(self) -> str:
        """Returns a concise prompt block summarising active objectives for Brain injection."""
        objectives = self.list_objectives(status=None)
        active = [o for o in objectives if o["status"] not in ("complete", "paused")]
        if not active:
            return ""

        lines = ["[STRATEGIC PLAN]"]
        for obj in active[:5]:
            status_icon = {"on_track": "✓", "at_risk": "⚠", "off_track": "✗"}.get(obj["status"], "·")
            lines.append(f"{status_icon} [{obj['horizon'].upper()}] {obj['title']} ({obj['status']})")
            for kr in obj["key_results"][:3]:
                bar = "▓" * int(kr["pct_complete"] / 10) + "░" * (10 - int(kr["pct_complete"] / 10))
                val = f"{kr['current_value']}/{kr['target_value']}{' ' + kr['unit'] if kr['unit'] else ''}"
                lines.append(f"  {bar} {kr['pct_complete']}% — {kr['title']} ({val})")

        at_risk = self.get_at_risk()
        if at_risk:
            lines.append(f"AT RISK: {len(at_risk)} objective(s) need attention")

        return "\n".join(lines)

    # ── AI-assisted generation ────────────────────────────────────────────────

    async def generate_weekly_review(self) -> str:
        objectives = self.list_objectives()
        if not objectives:
            return "No objectives defined yet. Add some with 'add objective'."

        summary = json.dumps(objectives, indent=2, default=str)
        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=800,
            system=(
                "You are a strategic advisor writing a crisp weekly review. "
                "Be direct. Lead with what's at risk. End with the one thing that matters most this week. "
                "No filler. Max 250 words."
            ),
            messages=[{
                "role": "user",
                "content": f"Write a weekly strategic review based on these OKRs:\n\n{summary}",
            }],
        )
        return resp.content[0].text

    async def generate_okrs_from_description(self, description: str) -> dict:
        """Given a natural-language business description, draft OKRs."""
        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=1200,
            system="You are an OKR coach. Generate clear, measurable OKRs.",
            tools=[{
                "name": "create_okrs",
                "description": "Create structured OKRs from a business description",
                "input_schema": {
                    "type": "object",
                    "required": ["objectives"],
                    "properties": {
                        "objectives": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["title", "horizon", "key_results"],
                                "properties": {
                                    "title": {"type": "string"},
                                    "description": {"type": "string"},
                                    "horizon": {"type": "string", "enum": ["weekly", "monthly", "quarterly", "annual"]},
                                    "key_results": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["title", "target_value", "unit"],
                                            "properties": {
                                                "title": {"type": "string"},
                                                "target_value": {"type": "number"},
                                                "baseline_value": {"type": "number"},
                                                "unit": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        }
                    },
                },
            }],
            tool_choice={"type": "tool", "name": "create_okrs"},
            messages=[{"role": "user", "content": f"Create OKRs for: {description}"}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "create_okrs":
                return block.input
        return {"objectives": []}

    def close(self) -> None:
        self._conn.close()
