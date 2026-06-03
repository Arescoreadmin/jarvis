#!/usr/bin/env python3
"""
Claude Code CLI hook script.

Registered in ~/.claude/settings.json for PostToolUse and Stop events.
Reads the hook payload from stdin, POSTs to JARVIS API.

RULES:
  - Never raise an exception that would surface to Claude Code
  - Never block for more than 2 seconds
  - Use stdlib only (no pip installs required)
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


JARVIS_URL = os.environ.get("JARVIS_URL", "http://localhost:8765")
JARVIS_TOKEN = os.environ.get("JARVIS_TOKEN", "")
TIMEOUT = 2  # seconds — must never slow down the CLI


def main() -> None:
    event_type = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    data = {
        "source": "claude-code",
        "event_type": event_type,
        "session_id": payload.get("session_id", os.environ.get("CLAUDE_SESSION_ID", "")),
        "cwd": os.getcwd(),
        "payload": {
            "tool_name": payload.get("tool_name", ""),
            "tool_input": payload.get("tool_input", {}),
            "tool_response": _truncate(str(payload.get("tool_response", "")), 200),
            "session_id": payload.get("session_id", ""),
        },
    }

    _post(data)


def _post(data: dict) -> None:
    if not JARVIS_TOKEN:
        return
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{JARVIS_URL}/events/dev",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {JARVIS_TOKEN}",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception:
        pass  # Never surface errors to the CLI


def _truncate(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


if __name__ == "__main__":
    main()
