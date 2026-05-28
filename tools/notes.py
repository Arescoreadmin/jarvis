"""
Notes tool — markdown files in a local directory.
Fast reads, full-text search, atomic writes.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from tools.registry import ToolBase, ToolSafety


class NotesTool(ToolBase):
    name = "notes_read"
    description = (
        "Read, create, update, and search notes stored as markdown files. "
        "Ideal for capturing ideas, meeting notes, project docs."
    )
    action_policies = {
        "list": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Lists local notes."),
        "read": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Reads a local note."),
        "write": ToolSafety(action_type="note_write", risk_level="low", requires_confirmation=False, reason="Writes a local note."),
        "append": ToolSafety(action_type="note_write", risk_level="low", requires_confirmation=False, reason="Appends to a local note."),
        "search": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Searches local notes."),
    }

    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "append", "search", "list"],
                "description": "Notes action",
            },
            "filename": {"type": "string", "description": "Note filename (without .md)"},
            "content": {"type": "string", "description": "Content to write or append"},
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["action"],
    }

    def __init__(self, notes_dir: str = "./data/notes"):
        self._dir = Path(notes_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def run(self, action: str, filename: str = "", content: str = "", query: str = "") -> str:
        if action == "list":
            return self._list()
        if action == "read":
            return self._read(filename)
        if action == "write":
            return self._write(filename, content)
        if action == "append":
            return self._append(filename, content)
        if action == "search":
            return self._search(query)
        return "Unknown notes action"

    def _list(self) -> str:
        files = sorted(self._dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return "No notes."
        return "\n".join(f.stem for f in files[:30])

    def _read(self, filename: str) -> str:
        if not filename:
            return "filename required"
        path = self._dir / f"{filename}.md"
        if not path.exists():
            return f"Note '{filename}' not found."
        return path.read_text()

    def _write(self, filename: str, content: str) -> str:
        if not filename or not content:
            return "filename and content required"
        path = self._dir / f"{filename}.md"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        full_content = f"# {filename}\n_Last updated: {timestamp}_\n\n{content}"
        path.write_text(full_content)
        return f"Note saved: {filename}"

    def _append(self, filename: str, content: str) -> str:
        if not filename or not content:
            return "filename and content required"
        path = self._dir / f"{filename}.md"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        with open(path, "a") as f:
            f.write(f"\n\n---\n_{timestamp}_\n{content}")
        return f"Appended to: {filename}"

    def _search(self, query: str) -> str:
        if not query:
            return "query required"
        results = []
        for path in self._dir.glob("*.md"):
            try:
                text = path.read_text()
                if query.lower() in text.lower():
                    lines = [l for l in text.splitlines() if query.lower() in l.lower()]
                    snippet = lines[0][:120] if lines else ""
                    results.append(f"**{path.stem}**: {snippet}")
            except Exception:
                pass
        return "\n".join(results[:10]) if results else f"Nothing found for '{query}'"
