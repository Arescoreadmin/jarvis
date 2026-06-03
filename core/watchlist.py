"""
Persistent monitoring engine.

Lets the user say "watch AAPL and alert me when it drops below 170"
or "watch my front door and alert me if it's open after midnight".
Watches are stored in SQLite, evaluated by the anticipator on its
normal check cycle, and fire one-shot or recurring alerts.

Schema:
    id          TEXT PRIMARY KEY
    description TEXT            — natural language of what's being watched
    tool_name   TEXT            — which tool to call for evaluation
    tool_args   JSON            — args to pass to the tool
    condition   TEXT            — natural language condition to evaluate
    priority    TEXT            — urgent/high/medium/low
    recur       INTEGER         — 0=fire once then deactivate, 1=keep watching
    active      INTEGER         — 1=active, 0=deactivated
    last_result TEXT            — last tool output (for change detection)
    last_fired  TEXT            — ISO timestamp of last alert
    created_at  TEXT
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional


class WatchlistManager:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id          TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                tool_name   TEXT NOT NULL,
                tool_args   TEXT NOT NULL DEFAULT '{}',
                condition   TEXT NOT NULL,
                priority    TEXT NOT NULL DEFAULT 'medium',
                recur       INTEGER NOT NULL DEFAULT 1,
                active      INTEGER NOT NULL DEFAULT 1,
                last_result TEXT,
                last_fired  TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def add(
        self,
        description: str,
        tool_name: str,
        tool_args: dict,
        condition: str,
        priority: str = "medium",
        recur: bool = True,
    ) -> str:
        watch_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO watchlist
                (id, description, tool_name, tool_args, condition, priority, recur, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                watch_id,
                description,
                tool_name,
                json.dumps(tool_args),
                condition,
                priority,
                1 if recur else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return watch_id

    def remove(self, watch_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE watchlist SET active=0 WHERE id=?", (watch_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_active(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM watchlist WHERE active=1 ORDER BY created_at"
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        out = []
        for row in rows:
            item = dict(zip(cols, row))
            item["tool_args"] = json.loads(item["tool_args"] or "{}")
            out.append(item)
        return out

    def get_all(self, include_inactive: bool = False) -> list[dict]:
        query = "SELECT * FROM watchlist ORDER BY created_at DESC"
        if not include_inactive:
            query = "SELECT * FROM watchlist WHERE active=1 ORDER BY created_at DESC"
        cur = self._conn.execute(query)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        out = []
        for row in rows:
            item = dict(zip(cols, row))
            item["tool_args"] = json.loads(item["tool_args"] or "{}")
            out.append(item)
        return out

    def update_result(
        self, watch_id: str, result: str, fired: bool = False
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if fired:
            self._conn.execute(
                """
                UPDATE watchlist
                SET last_result=?, last_fired=?
                WHERE id=?
                """,
                (result, now, watch_id),
            )
        else:
            self._conn.execute(
                "UPDATE watchlist SET last_result=? WHERE id=?",
                (result, watch_id),
            )
        self._conn.commit()

    def deactivate(self, watch_id: str) -> None:
        self._conn.execute(
            "UPDATE watchlist SET active=0 WHERE id=?", (watch_id,)
        )
        self._conn.commit()

    def has_changed(self, watch: dict, new_result: str) -> bool:
        return watch.get("last_result") != new_result
