"""
Clarification Memory — tracks assumptions Jarvis makes and clarifications the user provides.

When Jarvis assumes something ("I'll assume X means Y"), it's logged as an assumption.
When the user corrects or clarifies ("remember that X means Y"), it's stored as a
permanent clarification. Before each Brain call, relevant clarifications are injected
into the system prompt so Jarvis never asks the same question twice.

Schema:
  assumptions    — things Jarvis assumed without confirmation
  clarifications — user-provided corrections with optional scope
"""
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "clarifications.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS assumptions (
    id TEXT PRIMARY KEY,
    context TEXT NOT NULL,
    assumption TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    confirmed INTEGER DEFAULT 0,
    denied INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clarifications (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    clarification TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'permanent',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assumptions_created ON assumptions(created_at);
CREATE INDEX IF NOT EXISTS idx_clarifications_trigger ON clarifications(trigger);
"""

ASSUMPTION_PATTERNS = re.compile(
    r"(?:i'?ll assume|i'?m assuming|i'?m treating|i'?m interpreting|"
    r"assuming you mean|i'?ll treat|i'?ll interpret)[^.!?]*[.!?]?",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _simple_overlap(a: str, b: str) -> float:
    """Word-overlap similarity in [0, 1]."""
    wa = set(re.findall(r"\w+", a.lower()))
    wb = set(re.findall(r"\w+", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


class ClarificationMemory:
    SIMILARITY_THRESHOLD = 0.15

    def __init__(self):
        self._conn = _connect()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def add_assumption(
        self,
        context: str,
        assumption: str,
        confidence: float = 0.8,
    ) -> str:
        aid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO assumptions (id, context, assumption, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
            (aid, context[:500], assumption[:500], confidence, _now()),
        )
        self._conn.commit()
        return aid

    def add_clarification(
        self,
        trigger: str,
        clarification: str,
        scope: str = "permanent",
    ) -> str:
        cid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO clarifications (id, trigger, clarification, scope, created_at) VALUES (?, ?, ?, ?, ?)",
            (cid, trigger[:500], clarification[:1000], scope, _now()),
        )
        self._conn.commit()
        # Mark any open assumptions that this clarifies as confirmed
        self._confirm_related(trigger)
        return cid

    def _confirm_related(self, trigger: str) -> None:
        rows = self._conn.execute(
            "SELECT id, assumption FROM assumptions WHERE confirmed = 0 AND denied = 0"
        ).fetchall()
        for row in rows:
            if _simple_overlap(trigger, row["assumption"]) >= self.SIMILARITY_THRESHOLD:
                self._conn.execute(
                    "UPDATE assumptions SET confirmed = 1 WHERE id = ?", (row["id"],)
                )
        self._conn.commit()

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_relevant(self, context_text: str, limit: int = 5) -> list[dict]:
        """Return clarifications most relevant to the given context text."""
        rows = self._conn.execute(
            "SELECT * FROM clarifications ORDER BY created_at DESC"
        ).fetchall()
        scored = []
        for row in rows:
            score = _simple_overlap(context_text, row["trigger"] + " " + row["clarification"])
            if score >= self.SIMILARITY_THRESHOLD:
                scored.append((score, dict(row)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:limit]]

    def list_open_assumptions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM assumptions WHERE confirmed = 0 AND denied = 0 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_clarifications(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM clarifications ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Context block ─────────────────────────────────────────────────────────

    def get_context_block(self, context_text: str = "") -> str:
        relevant = self.get_relevant(context_text) if context_text else self.list_clarifications()
        if not relevant:
            return ""
        lines = ["[CLARIFICATIONS — apply these always]"]
        for c in relevant[:8]:
            lines.append(f'• When "{c["trigger"]}" → {c["clarification"]}')
        return "\n".join(lines)

    # ── Auto-extraction ────────────────────────────────────────────────────────

    def extract_assumptions_from_response(self, response: str, context: str) -> int:
        """Detect assumption language in a Brain response and store each one."""
        matches = ASSUMPTION_PATTERNS.findall(response)
        stored = 0
        for match in matches[:5]:
            text = match.strip()
            if len(text) > 15:
                self.add_assumption(context=context[:300], assumption=text)
                stored += 1
        return stored

    def close(self) -> None:
        self._conn.close()
