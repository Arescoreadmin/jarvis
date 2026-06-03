"""
Business management and entrepreneurship intelligence.

Persistent OKR tracking, KPI dashboards, financial runway modeling,
investor update generation, competitive intelligence, and strategic
planning — all integrated with JARVIS's memory and context.

Data stored in SQLite (same DB as memory, separate tables).
AI synthesis via Claude for narrative generation and strategic analysis.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

from tools.registry import ToolBase, ToolSafety

DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS okrs (
    id          TEXT PRIMARY KEY,
    period      TEXT NOT NULL,
    objective   TEXT NOT NULL,
    status      TEXT DEFAULT 'active',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS key_results (
    id          TEXT PRIMARY KEY,
    okr_id      TEXT NOT NULL REFERENCES okrs(id),
    description TEXT NOT NULL,
    target      REAL,
    current     REAL DEFAULT 0,
    unit        TEXT DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kpis (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT DEFAULT '',
    timestamp   TEXT NOT NULL,
    notes       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kpis_name ON kpis(name, timestamp);
"""

INVESTOR_SYSTEM = """\
You are a founder writing a crisp investor update. Rules:
- Lead with the number that proves growth
- One sentence on biggest win, one on biggest challenge (honest)
- What you need from investors (be specific — intros, advice, capital)
- 3 key metrics table: Metric | Last Period | This Period | Target
- Total length: 300-400 words
No fluff. Investors read dozens of these. Make yours worth reading.
"""

STRATEGY_SYSTEM = """\
You are a strategic advisor with experience scaling companies from 0→1 and 1→10.
You think in leverage: what's the one move that unlocks 10 other moves?
Give a direct recommendation, the key risk, and the first concrete action to take this week.
"""

COMPETITIVE_SYSTEM = """\
You are a competitive intelligence analyst. Synthesize available information into:
1. Competitor's likely strategy and positioning
2. Their key advantages and weaknesses
3. Where they're probably heading next (signal: recent hires, product updates, pricing)
4. Asymmetric angle: what can you do that they structurally can't?
Be specific. Don't describe what's publicly known — analyze what it means.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


class BusinessTool(ToolBase):
    name = "business"
    description = (
        "Business management: track OKRs and KPIs, model financial runway, "
        "generate investor updates, analyze competitors, and get strategic recommendations. "
        "Data persists across sessions so your metrics compound over time."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "add_okr", "update_kr", "get_okrs",
                    "add_kpi", "get_kpis", "kpi_trend",
                    "runway", "investor_update",
                    "competitive", "strategy",
                ],
            },
            "period": {"type": "string", "description": "e.g. 'Q3 2026' or '2026'"},
            "objective": {"type": "string", "description": "The O in OKR"},
            "key_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "target": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                },
                "description": "List of KRs for this objective",
            },
            "okr_id": {"type": "string", "description": "OKR ID to update"},
            "kr_id": {"type": "string", "description": "Key result ID to update"},
            "current_value": {"type": "number", "description": "Current KR value"},
            "kpi_name": {"type": "string", "description": "e.g. 'MRR', 'DAU', 'NPS'"},
            "kpi_value": {"type": "number"},
            "kpi_unit": {"type": "string", "description": "e.g. 'USD', 'users', 'score'"},
            "kpi_notes": {"type": "string"},
            "capital": {"type": "number", "description": "Cash on hand (USD)"},
            "monthly_burn": {"type": "number", "description": "Monthly burn rate (USD)"},
            "monthly_growth": {"type": "number", "description": "Monthly revenue growth %"},
            "context": {"type": "string", "description": "Additional context for AI analysis"},
            "competitor": {"type": "string", "description": "Competitor name"},
            "question": {"type": "string", "description": "Strategic question to explore"},
        },
    }

    action_policies = {
        "add_okr": ToolSafety(action_type="write", risk_level="low"),
        "update_kr": ToolSafety(action_type="write", risk_level="low"),
        "get_okrs": ToolSafety(action_type="read", risk_level="low"),
        "add_kpi": ToolSafety(action_type="write", risk_level="low"),
        "get_kpis": ToolSafety(action_type="read", risk_level="low"),
        "kpi_trend": ToolSafety(action_type="read", risk_level="low"),
        "runway": ToolSafety(action_type="analysis", risk_level="low"),
        "investor_update": ToolSafety(action_type="generate", risk_level="low"),
        "competitive": ToolSafety(action_type="analysis", risk_level="low"),
        "strategy": ToolSafety(action_type="analysis", risk_level="low"),
    }

    def __init__(self):
        self._client = anthropic.AsyncAnthropic()
        self._conn = _get_conn()

    async def run(self, action: str, **kwargs) -> str:
        if action == "add_okr":
            return self._add_okr(
                kwargs.get("period", ""),
                kwargs.get("objective", ""),
                kwargs.get("key_results", []),
            )
        elif action == "update_kr":
            return self._update_kr(kwargs.get("kr_id", ""), kwargs.get("current_value", 0))
        elif action == "get_okrs":
            return self._get_okrs(kwargs.get("period"))
        elif action == "add_kpi":
            return self._add_kpi(
                kwargs.get("kpi_name", ""),
                kwargs.get("kpi_value", 0),
                kwargs.get("kpi_unit", ""),
                kwargs.get("kpi_notes", ""),
            )
        elif action == "get_kpis":
            return self._get_kpis()
        elif action == "kpi_trend":
            return self._kpi_trend(kwargs.get("kpi_name", ""))
        elif action == "runway":
            return self._runway(
                kwargs.get("capital", 0),
                kwargs.get("monthly_burn", 0),
                kwargs.get("monthly_growth", 0),
            )
        elif action == "investor_update":
            return await self._investor_update(kwargs.get("context", ""))
        elif action == "competitive":
            return await self._competitive(
                kwargs.get("competitor", ""), kwargs.get("context", "")
            )
        elif action == "strategy":
            return await self._strategy(
                kwargs.get("question", ""), kwargs.get("context", "")
            )
        return f"Unknown action: {action}"

    # ── OKRs ──────────────────────────────────────────────────────────────────

    def _add_okr(self, period: str, objective: str, key_results: list) -> str:
        oid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO okrs (id, period, objective, status, created_at) VALUES (?,?,?,?,?)",
            (oid, period, objective, "active", _now()),
        )
        kr_ids = []
        for kr in key_results:
            krid = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO key_results (id, okr_id, description, target, current, unit, updated_at) VALUES (?,?,?,?,?,?,?)",
                (krid, oid, kr.get("description", ""), kr.get("target"), 0, kr.get("unit", ""), _now()),
            )
            kr_ids.append(krid)
        self._conn.commit()
        return f"OKR created (id: {oid[:8]}) with {len(kr_ids)} key results for {period}: {objective}"

    def _update_kr(self, kr_id: str, value: float) -> str:
        self._conn.execute(
            "UPDATE key_results SET current=?, updated_at=? WHERE id LIKE ?",
            (value, _now(), f"{kr_id}%"),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT description, current, target, unit FROM key_results WHERE id LIKE ?",
            (f"{kr_id}%",),
        ).fetchone()
        if row:
            pct = round(row["current"] / row["target"] * 100, 1) if row["target"] else 0
            return f"KR updated: {row['description']} — {row['current']}{row['unit']} / {row['target']}{row['unit']} ({pct}%)"
        return "KR updated."

    def _get_okrs(self, period: Optional[str]) -> str:
        if period:
            rows = self._conn.execute(
                "SELECT * FROM okrs WHERE period=? AND status='active' ORDER BY created_at",
                (period,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM okrs WHERE status='active' ORDER BY period DESC, created_at"
            ).fetchall()
        if not rows:
            return "No active OKRs found."
        lines = []
        for o in rows:
            lines.append(f"\n[{o['period']}] {o['objective']} (id: {o['id'][:8]})")
            krs = self._conn.execute(
                "SELECT * FROM key_results WHERE okr_id=? ORDER BY rowid", (o["id"],)
            ).fetchall()
            for kr in krs:
                pct = round(kr["current"] / kr["target"] * 100, 1) if kr["target"] else 0
                bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
                lines.append(f"  {bar} {pct}%  {kr['description']} ({kr['current']}/{kr['target']}{kr['unit']}) [{kr['id'][:8]}]")
        return "\n".join(lines)

    # ── KPIs ──────────────────────────────────────────────────────────────────

    def _add_kpi(self, name: str, value: float, unit: str, notes: str) -> str:
        self._conn.execute(
            "INSERT INTO kpis (id, name, value, unit, timestamp, notes) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), name, value, unit, _now(), notes),
        )
        self._conn.commit()
        return f"KPI logged: {name} = {value}{unit}" + (f" — {notes}" if notes else "")

    def _get_kpis(self) -> str:
        rows = self._conn.execute(
            """SELECT name, value, unit, timestamp, notes
               FROM kpis
               WHERE (name, timestamp) IN (
                   SELECT name, MAX(timestamp) FROM kpis GROUP BY name
               )
               ORDER BY name""",
        ).fetchall()
        if not rows:
            return "No KPIs tracked yet."
        lines = ["Latest KPI values:"]
        for r in rows:
            ts = r["timestamp"][:10]
            note = f" — {r['notes']}" if r["notes"] else ""
            lines.append(f"  {r['name']}: {r['value']}{r['unit']}  [{ts}]{note}")
        return "\n".join(lines)

    def _kpi_trend(self, name: str) -> str:
        rows = self._conn.execute(
            "SELECT value, unit, timestamp FROM kpis WHERE name=? ORDER BY timestamp DESC LIMIT 10",
            (name,),
        ).fetchall()
        if not rows:
            return f"No data for KPI '{name}'."
        lines = [f"Trend for {name}:"]
        for r in rows:
            lines.append(f"  {r['timestamp'][:10]}  {r['value']}{r['unit']}")
        if len(rows) >= 2:
            latest = rows[0]["value"]
            oldest = rows[-1]["value"]
            if oldest != 0:
                chg = round((latest - oldest) / abs(oldest) * 100, 1)
                arrow = "↑" if chg > 0 else "↓"
                lines.append(f"  Change over {len(rows)} readings: {arrow}{abs(chg)}%")
        return "\n".join(lines)

    # ── Runway ────────────────────────────────────────────────────────────────

    def _runway(self, capital: float, monthly_burn: float, monthly_growth: float) -> str:
        if monthly_burn <= 0:
            return "Monthly burn must be > 0 to calculate runway."
        months = capital / monthly_burn
        from datetime import date, timedelta
        cutoff = date.today() + timedelta(days=int(months * 30.4))

        lines = [
            f"Runway: {months:.1f} months  (runs out ~{cutoff.strftime('%B %Y')})",
            f"  Capital: ${capital:,.0f}",
            f"  Monthly burn: ${monthly_burn:,.0f}",
        ]
        if monthly_growth > 0:
            # Estimate when revenue covers burn (simplified)
            # Assumes current revenue = 0 (conservative)
            lines.append(f"  Revenue growth: {monthly_growth}%/mo — factor this into net burn projections")

        # Scenarios
        lines.append("\nScenarios:")
        for reduction, label in [(0, "current burn"), (0.8, "20% cut"), (0.6, "40% cut")]:
            adj_burn = monthly_burn * (1 - reduction) if reduction else monthly_burn
            r = capital / adj_burn if adj_burn else float("inf")
            lines.append(f"  {label}: {r:.1f} months")

        lines.append(f"\n{'⚠ ' if months < 12 else ''}Fundraise target: raise before {(months - 6):.0f} months to maintain 6-month buffer.")
        return "\n".join(lines)

    # ── AI-powered analysis ───────────────────────────────────────────────────

    async def _investor_update(self, context: str) -> str:
        kpis = self._get_kpis()
        okrs = self._get_okrs(None)
        prompt = (
            f"Write an investor update.\n\nKPIs:\n{kpis}\n\nOKRs:\n{okrs}\n"
            f"\nAdditional context:\n{context or 'none provided'}"
        )
        return await self._ask(INVESTOR_SYSTEM, prompt, 1000)

    async def _competitive(self, competitor: str, context: str) -> str:
        prompt = (
            f"Competitive analysis of: {competitor}\n"
            f"Context about our product/position: {context or 'startup, early stage'}"
        )
        return await self._ask(COMPETITIVE_SYSTEM, prompt, 1500)

    async def _strategy(self, question: str, context: str) -> str:
        prompt = (
            f"Strategic question: {question}\n"
            f"Context: {context or 'early-stage startup, small team, capital constrained'}"
        )
        return await self._ask(STRATEGY_SYSTEM, prompt, 1200)

    async def _ask(self, system: str, prompt: str, max_tokens: int = 1000) -> str:
        resp = await self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
