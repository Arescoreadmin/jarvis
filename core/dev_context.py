"""
Developer activity tracker.

Receives events from Claude Code CLI and OpenAI Codex CLI hooks,
stores them in SQLite, and generates a dev context block for
injection into every JARVIS request.

This gives JARVIS awareness of:
  - What project you're currently working on
  - Which files you've been touching
  - What tools Claude Code / Codex have been calling
  - Session duration and intensity (tool call volume)
  - Patterns across sessions (repeated debugging in same file, etc.)
"""
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_from_cwd(cwd: str) -> str:
    if not cwd:
        return "unknown"
    return Path(cwd).name


DEV_SCHEMA = """
    CREATE TABLE IF NOT EXISTS dev_events (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        source TEXT NOT NULL,
        event_type TEXT NOT NULL,
        session_id TEXT,
        project TEXT,
        cwd TEXT,
        tool_name TEXT,
        file_path TEXT,
        summary TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_dev_events_ts ON dev_events(timestamp);
    CREATE INDEX IF NOT EXISTS idx_dev_events_session ON dev_events(session_id);
    CREATE INDEX IF NOT EXISTS idx_dev_events_project ON dev_events(project);

    CREATE TABLE IF NOT EXISTS dev_sessions (
        session_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        project TEXT,
        cwd TEXT,
        start_time TEXT NOT NULL,
        end_time TEXT,
        tool_call_count INTEGER DEFAULT 0,
        files_touched TEXT DEFAULT '[]',
        summary TEXT
    );
"""


class DevContextTracker:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        conn.executescript(DEV_SCHEMA)
        conn.commit()

    def ingest(self, payload: dict) -> None:
        source = payload.get("source", "unknown")
        event_type = payload.get("event_type", "unknown")
        session_id = payload.get("session_id") or payload.get("payload", {}).get("session_id", "")
        cwd = payload.get("cwd", "")
        project = _project_from_cwd(cwd)

        inner = payload.get("payload", {})
        tool_name = inner.get("tool_name", "") or payload.get("tool_name", "")
        tool_input = inner.get("tool_input", {})
        file_path = self._extract_file(tool_name, tool_input)
        summary = self._build_summary(event_type, tool_name, tool_input, payload)

        eid = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO dev_events
               (id, timestamp, source, event_type, session_id, project, cwd, tool_name, file_path, summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, _now(), source, event_type, session_id, project, cwd, tool_name, file_path, summary),
        )

        if session_id:
            self._upsert_session(session_id, source, project, cwd, event_type, file_path)

        self._conn.commit()

    def _upsert_session(
        self, session_id: str, source: str, project: str, cwd: str, event_type: str, file_path: str
    ) -> None:
        existing = self._conn.execute(
            "SELECT * FROM dev_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

        if not existing:
            self._conn.execute(
                """INSERT INTO dev_sessions
                   (session_id, source, project, cwd, start_time, tool_call_count, files_touched)
                   VALUES (?, ?, ?, ?, ?, 0, '[]')""",
                (session_id, source, project, cwd, _now()),
            )
            existing = self._conn.execute(
                "SELECT * FROM dev_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()

        files = json.loads(existing["files_touched"] or "[]")
        if file_path and file_path not in files:
            files.append(file_path)

        new_count = (existing["tool_call_count"] or 0) + (1 if event_type == "PostToolUse" else 0)
        end_time = _now() if event_type in ("Stop", "session_end") else existing["end_time"]

        self._conn.execute(
            """UPDATE dev_sessions
               SET tool_call_count = ?, files_touched = ?, end_time = ?
               WHERE session_id = ?""",
            (new_count, json.dumps(files[-20:]), end_time, session_id),
        )

    def _extract_file(self, tool_name: str, tool_input: dict) -> str:
        if not tool_input:
            return ""
        for key in ("file_path", "path", "filename"):
            if val := tool_input.get(key, ""):
                return str(val)
        if tool_name in ("Read", "Edit", "Write") and "file_path" in tool_input:
            return tool_input["file_path"]
        return ""

    def _build_summary(self, event_type: str, tool_name: str, tool_input: dict, payload: dict) -> str:
        if event_type == "PostToolUse":
            file_hint = self._extract_file(tool_name, tool_input)
            return f"{tool_name} {file_hint}".strip()
        if event_type in ("Stop", "session_end"):
            return payload.get("summary", "session ended")
        if event_type == "session_start":
            return f"started from {payload.get('cwd', '')}"
        return event_type

    def get_active_session(self) -> Optional[dict]:
        row = self._conn.execute(
            """SELECT * FROM dev_sessions
               WHERE end_time IS NULL
               ORDER BY start_time DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None

    def get_recent_sessions(self, hours: int = 24, limit: int = 5) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM dev_sessions
               WHERE start_time >= ?
               ORDER BY start_time DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_files(self, project: str, hours: int = 4) -> list[str]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self._conn.execute(
            """SELECT file_path FROM dev_events
               WHERE project = ? AND file_path != '' AND timestamp >= ?
               ORDER BY timestamp DESC LIMIT 30""",
            (project, cutoff),
        ).fetchall()
        seen = []
        for r in rows:
            if r["file_path"] not in seen:
                seen.append(r["file_path"])
        return seen[:10]

    def get_project_stats(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT project, source,
                      COUNT(*) as session_count,
                      SUM(tool_call_count) as total_calls
               FROM dev_sessions
               WHERE start_time >= ? AND project != 'unknown'
               GROUP BY project, source
               ORDER BY total_calls DESC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def build_context_block(self) -> str:
        lines = []

        active = self.get_active_session()
        if active:
            project = active.get("project", "?")
            source = active.get("source", "?")
            calls = active.get("tool_call_count", 0)
            files = json.loads(active.get("files_touched") or "[]")
            start = active.get("start_time", "")
            try:
                elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(start)
                mins = int(elapsed.total_seconds() / 60)
                duration = f"{mins}m" if mins < 60 else f"{mins // 60}h{mins % 60}m"
            except Exception:
                duration = "?"

            lines.append(f"Dev: [{source}] {project} — {duration}, {calls} tool calls")
            if files:
                lines.append(f"  Recent files: {', '.join(Path(f).name for f in files[:5])}")

        recent = self.get_recent_sessions(hours=8)
        projects_today = list({s["project"] for s in recent if s["project"] != "unknown"})
        if projects_today and not active:
            lines.append(f"Dev today: {', '.join(projects_today)}")

        stats = self.get_project_stats(days=7)
        if stats and not lines:
            top = stats[0]
            lines.append(f"Dev (7d): {top['project']} most active ({top['total_calls']} tool calls)")

        return "\n".join(lines)
