"""
Google Calendar integration.
Reads events, creates/edits/deletes events, checks availability.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from tools.registry import ToolBase

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarTool(ToolBase):
    name = "calendar_read"
    description = (
        "Read and manage Google Calendar events. "
        "Get upcoming events, create events, check availability, find free slots."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upcoming", "create", "delete", "find_free_slot", "check_day"],
                "description": "Calendar action",
            },
            "count": {"type": "integer", "description": "Number of events for 'upcoming'", "default": 5},
            "title": {"type": "string", "description": "Event title for 'create'"},
            "start_time": {"type": "string", "description": "ISO datetime for event start"},
            "end_time": {"type": "string", "description": "ISO datetime for event end"},
            "description": {"type": "string", "description": "Event description"},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee emails"},
            "event_id": {"type": "string", "description": "Event ID for delete"},
            "date": {"type": "string", "description": "Date (YYYY-MM-DD) for check_day"},
        },
        "required": ["action"],
    }

    def __init__(self, credentials_path: str = ""):
        self._creds_path = credentials_path or os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "./data/google_creds.json")
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        if not GOOGLE_AVAILABLE:
            return None
        creds = None
        token_path = "./data/calendar_token.json"
        if Path(token_path).exists():
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif Path(self._creds_path).exists():
                flow = InstalledAppFlow.from_client_secrets_file(self._creds_path, SCOPES)
                creds = flow.run_local_server(port=0)
                Path(token_path).parent.mkdir(parents=True, exist_ok=True)
                Path(token_path).write_text(creds.to_json())
            else:
                return None
        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    async def run(self, action: str, **kwargs) -> str:
        svc = self._get_service()
        if not svc:
            return "[calendar] Google Calendar not configured (credentials missing)"

        if action == "upcoming":
            events = await self.get_upcoming(kwargs.get("count", 5))
            if not events:
                return "No upcoming events."
            lines = []
            for e in events:
                lines.append(f"{e.get('time', '?')} — {e.get('title', 'event')}")
            return "\n".join(lines)

        if action == "create":
            return self._create_event(
                svc,
                kwargs.get("title", ""),
                kwargs.get("start_time", ""),
                kwargs.get("end_time", ""),
                kwargs.get("description", ""),
                kwargs.get("attendees", []),
            )

        if action == "delete":
            return self._delete_event(svc, kwargs.get("event_id", ""))

        if action == "check_day":
            return self._check_day(svc, kwargs.get("date", ""))

        return "Unknown calendar action"

    async def get_upcoming(self, count: int = 5) -> list[dict]:
        svc = self._get_service()
        if not svc:
            return []
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = svc.events().list(
                calendarId="primary",
                timeMin=now,
                maxResults=count,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            events = []
            for item in result.get("items", []):
                start = item.get("start", {})
                dt = start.get("dateTime", start.get("date", ""))
                try:
                    parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    formatted = parsed.strftime("%a %b %d %I:%M%p")
                except (ValueError, AttributeError):
                    formatted = dt
                events.append({
                    "id": item.get("id", ""),
                    "title": item.get("summary", "Untitled"),
                    "time": formatted,
                    "start_time": dt,
                    "attendees": [a.get("email", "") for a in item.get("attendees", [])],
                    "description": item.get("description", ""),
                })
            return events
        except Exception:
            return []

    def _create_event(self, svc, title, start_time, end_time, description, attendees) -> str:
        if not title or not start_time:
            return "Title and start_time required"
        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_time, "timeZone": "UTC"},
            "end": {"dateTime": end_time or start_time, "timeZone": "UTC"},
        }
        if attendees:
            event["attendees"] = [{"email": e} for e in attendees]
        result = svc.events().insert(calendarId="primary", body=event).execute()
        return f"Created: {result.get('summary')} — {result.get('htmlLink', '')}"

    def _delete_event(self, svc, event_id: str) -> str:
        if not event_id:
            return "event_id required"
        svc.events().delete(calendarId="primary", eventId=event_id).execute()
        return f"Deleted event {event_id}"

    def _check_day(self, svc, date: str) -> str:
        if not date:
            return "date required"
        try:
            start = datetime.fromisoformat(date).replace(hour=0, minute=0, second=0)
            end = start + timedelta(days=1)
        except ValueError:
            return f"Invalid date: {date}"
        result = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return f"{date}: clear"
        lines = [f"{date}:"]
        for e in events:
            start_t = e.get("start", {}).get("dateTime", "")
            lines.append(f"  {start_t[11:16] if len(start_t) > 16 else '?'} — {e.get('summary', 'event')}")
        return "\n".join(lines)

    async def get_unread_count(self) -> int:
        events = await self.get_upcoming(10)
        return len(events)
