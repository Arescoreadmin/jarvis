"""
Multi-tier persistent memory.

Tiers:
  working    — current session (in-memory)
  episodic   — time-indexed past interactions (SQLite)
  semantic   — structured user knowledge (SQLite)
  procedural — learned workflows and preferences (SQLite)
  relational — people graph with relationship metadata (SQLite)
  commitment — tracked promises with deadlines (SQLite)
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import chromadb
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False


DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS episodic (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            topics TEXT,
            people TEXT,
            session_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_episodic_ts ON episodic(timestamp);
        CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic(session_id);

        CREATE TABLE IF NOT EXISTS semantic (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS procedural (
            trigger TEXT PRIMARY KEY,
            expansion TEXT NOT NULL,
            use_count INTEGER DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            relationship TEXT,
            communication_style TEXT,
            notes TEXT,
            last_interaction TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS commitments (
            id TEXT PRIMARY KEY,
            made_to TEXT NOT NULL,
            description TEXT NOT NULL,
            deadline TEXT,
            made_on TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            priority TEXT DEFAULT 'medium',
            context TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);
        CREATE INDEX IF NOT EXISTS idx_commitments_deadline ON commitments(deadline);

        CREATE TABLE IF NOT EXISTS pending_actions (
            id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            action TEXT,
            args_json TEXT NOT NULL,
            action_type TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            decided_at TEXT,
            result TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pending_actions_status ON pending_actions(status);
        CREATE INDEX IF NOT EXISTS idx_pending_actions_created ON pending_actions(created_at);
    """)
    conn.commit()


class Memory:
    def __init__(self):
        self._conn = _connect()
        _init_schema(self._conn)
        from core.dev_context import DevContextTracker, DEV_SCHEMA
        self._conn.executescript(DEV_SCHEMA)
        self._conn.commit()
        self.dev = DevContextTracker(self._conn)
        self._working: dict[str, Any] = {}
        self._session_id = str(uuid.uuid4())

        self._chroma = None
        self._collection = None
        if CHROMA_AVAILABLE:
            try:
                vectors_path = str(DB_PATH.parent / "vectors")
                self._chroma = chromadb.PersistentClient(path=vectors_path)
                self._collection = self._chroma.get_or_create_collection("episodic")
            except Exception:
                pass

    # ── Working memory ──────────────────────────────────────────────────────

    def set_working(self, key: str, value: Any) -> None:
        self._working[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        return self._working.get(key, default)

    def clear_working(self) -> None:
        self._working.clear()

    # ── Semantic memory ──────────────────────────────────────────────────────

    def set_semantic(self, key: str, value: Any) -> None:
        serialized = json.dumps(value) if not isinstance(value, str) else value
        self._conn.execute(
            "INSERT OR REPLACE INTO semantic (key, value, updated_at) VALUES (?, ?, ?)",
            (key, serialized, _now()),
        )
        self._conn.commit()

    def get_semantic(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM semantic WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def get_user_profile(self) -> dict:
        return self.get_semantic("user_profile") or {
            "name": "",
            "preferred_name": "",
            "location": {"home": "", "work": ""},
            "timezone": "UTC",
            "wake_time": "07:00",
            "preferences": {},
        }

    def update_user_profile(self, updates: dict) -> None:
        profile = self.get_user_profile()
        profile.update(updates)
        self.set_semantic("user_profile", profile)

    # ── Episodic memory ───────────────────────────────────────────────────────

    def add_episode(
        self,
        role: str,
        content: str,
        topics: list[str] | None = None,
        people: list[str] | None = None,
    ) -> str:
        eid = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO episodic (id, timestamp, role, content, topics, people, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                eid,
                _now(),
                role,
                content,
                json.dumps(topics or []),
                json.dumps(people or []),
                self._session_id,
            ),
        )
        self._conn.commit()

        if self._collection:
            try:
                self._collection.add(
                    ids=[eid],
                    documents=[content],
                    metadatas=[{"role": role, "timestamp": _now()}],
                )
            except Exception:
                pass

        return eid

    def search_episodes(self, query: str, limit: int = 5) -> list[dict]:
        if self._collection:
            try:
                results = self._collection.query(query_texts=[query], n_results=limit)
                ids = results["ids"][0] if results["ids"] else []
                if ids:
                    placeholders = ",".join("?" * len(ids))
                    rows = self._conn.execute(
                        f"SELECT * FROM episodic WHERE id IN ({placeholders})", ids
                    ).fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                pass

        rows = self._conn.execute(
            "SELECT * FROM episodic WHERE content LIKE ? ORDER BY timestamp DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_episodes(self, n: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM episodic ORDER BY timestamp DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_session_history(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT role, content FROM episodic WHERE session_id = ? ORDER BY timestamp",
            (self._session_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    # ── Procedural memory ─────────────────────────────────────────────────────

    def add_procedure(self, trigger: str, expansion: str) -> None:
        self._conn.execute(
            """INSERT INTO procedural (trigger, expansion, use_count, updated_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(trigger) DO UPDATE SET
                 expansion=excluded.expansion,
                 use_count=use_count+1,
                 updated_at=excluded.updated_at""",
            (trigger, expansion, _now()),
        )
        self._conn.commit()

    def expand_procedure(self, trigger: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT expansion FROM procedural WHERE trigger = ?", (trigger,)
        ).fetchone()
        return row["expansion"] if row else None

    def get_all_procedures(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT trigger, expansion, use_count FROM procedural ORDER BY use_count DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Relational memory ─────────────────────────────────────────────────────

    def upsert_contact(self, name: str, **fields) -> str:
        existing = self._conn.execute(
            "SELECT id FROM contacts WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        cid = existing["id"] if existing else str(uuid.uuid4())
        fields.update({"name": name, "updated_at": _now()})
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        updates = ", ".join(f"{k}=excluded.{k}" for k in fields if k != "id")
        self._conn.execute(
            f"""INSERT INTO contacts (id, {cols}) VALUES (?, {placeholders})
                ON CONFLICT(id) DO UPDATE SET {updates}""",
            (cid, *fields.values()),
        )
        self._conn.commit()
        return cid

    def get_contact(self, name: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM contacts WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_contacts(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM contacts ORDER BY last_interaction DESC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_last_interaction(self, name: str) -> None:
        self._conn.execute(
            "UPDATE contacts SET last_interaction = ?, updated_at = ? WHERE name = ? COLLATE NOCASE",
            (_now(), _now(), name),
        )
        self._conn.commit()

    # ── Commitment tracking ────────────────────────────────────────────────────

    def add_commitment(
        self,
        made_to: str,
        description: str,
        deadline: Optional[str] = None,
        priority: str = "medium",
        context: str = "",
    ) -> str:
        cid = str(uuid.uuid4())
        self._conn.execute(
            """INSERT INTO commitments (id, made_to, description, deadline, made_on, status, priority, context)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (cid, made_to, description, deadline, _now(), priority, context),
        )
        self._conn.commit()
        return cid

    def complete_commitment(self, commitment_id: str) -> None:
        self._conn.execute(
            "UPDATE commitments SET status = 'completed' WHERE id = ?",
            (commitment_id,),
        )
        self._conn.commit()

    def get_pending_commitments(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM commitments WHERE status = 'pending' ORDER BY deadline ASC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_commitments_due_soon(self, days: int = 7) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM commitments
               WHERE status = 'pending' AND deadline IS NOT NULL AND deadline <= ?
               ORDER BY deadline ASC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_overdue_commitments(self) -> list[dict]:
        now = _now()
        rows = self._conn.execute(
            """SELECT * FROM commitments
               WHERE status = 'pending' AND deadline IS NOT NULL AND deadline < ?
               ORDER BY deadline ASC""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


    # ── Pending action approvals ───────────────────────────────────────────────

    def add_pending_action(
        self,
        tool_name: str,
        args: dict,
        action_type: str,
        risk_level: str,
        reason: str = "",
    ) -> str:
        action_id = str(uuid.uuid4())
        action = str(args.get("action", "")) if isinstance(args, dict) else ""
        self._conn.execute(
            """INSERT INTO pending_actions
               (id, tool_name, action, args_json, action_type, risk_level, reason, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                action_id,
                tool_name,
                action,
                json.dumps(args, sort_keys=True),
                action_type,
                risk_level,
                reason,
                _now(),
            ),
        )
        self._conn.commit()
        return action_id

    def get_pending_actions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM pending_actions WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
        return [self._decode_pending_action(r) for r in rows]

    def get_pending_action(self, action_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        return self._decode_pending_action(row) if row else None

    def resolve_pending_action(self, action_id: str, status: str, result: str = "") -> None:
        self._conn.execute(
            "UPDATE pending_actions SET status = ?, decided_at = ?, result = ? WHERE id = ?",
            (status, _now(), result, action_id),
        )
        self._conn.commit()

    def _decode_pending_action(self, row) -> dict:
        data = dict(row)
        try:
            data["args"] = json.loads(data.pop("args_json"))
        except (TypeError, json.JSONDecodeError):
            data["args"] = {}
        return data

    def close(self) -> None:
        self._conn.close()
