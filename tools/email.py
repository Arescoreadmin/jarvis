"""
Gmail integration — read, search, draft, send.
Always confirm before sending.
"""
import base64
import os
from email.mime.text import MIMEText
from pathlib import Path

from tools.registry import ToolBase, ToolSafety

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

URGENT_SENDERS: list[str] = []  # populate from config or semantic memory


class EmailTool(ToolBase):
    name = "email_read"
    description = (
        "Read, search, draft, and send email via Gmail. "
        "Can search by sender, subject, date. Always confirms before sending."
    )
    action_policies = {
        "search": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Searches mailbox metadata and snippets."),
        "read": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Reads a selected email message."),
        "draft": ToolSafety(action_type="external_communication_draft", risk_level="medium", requires_confirmation=False, reason="Creates a draft but does not send it."),
        "send": ToolSafety(action_type="external_communication", risk_level="high", requires_confirmation=True, reason="Sends an email to an external recipient."),
        "unread_count": ToolSafety(action_type="read", risk_level="low", requires_confirmation=False, reason="Counts unread email."),
    }

    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read", "draft", "send", "unread_count"],
                "description": "Email action",
            },
            "query": {"type": "string", "description": "Search query (Gmail syntax supported)"},
            "message_id": {"type": "string", "description": "Message ID for 'read'"},
            "to": {"type": "string", "description": "Recipient for draft/send"},
            "subject": {"type": "string", "description": "Subject line"},
            "body": {"type": "string", "description": "Email body"},
            "count": {"type": "integer", "description": "Number of results", "default": 5},
        },
        "required": ["action"],
    }

    def __init__(self, credentials_path: str = ""):
        self._creds_path = credentials_path or os.environ.get("GMAIL_CREDENTIALS", "./data/gmail_creds.json")
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        if not GOOGLE_AVAILABLE:
            return None
        creds = None
        token_path = "./data/gmail_token.json"
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
        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    async def run(self, action: str, **kwargs) -> str:
        svc = self._get_service()
        if not svc:
            return "[email] Gmail not configured (credentials missing)"

        if action == "search":
            return self._search(svc, kwargs.get("query", ""), kwargs.get("count", 5))
        if action == "read":
            return self._read(svc, kwargs.get("message_id", ""))
        if action == "draft":
            return self._draft(svc, kwargs.get("to", ""), kwargs.get("subject", ""), kwargs.get("body", ""))
        if action == "send":
            return self._send(svc, kwargs.get("to", ""), kwargs.get("subject", ""), kwargs.get("body", ""))
        if action == "unread_count":
            count = await self.get_unread_count()
            return str(count)
        return "Unknown email action"

    def _search(self, svc, query: str, count: int) -> str:
        result = svc.users().messages().list(userId="me", q=query, maxResults=count).execute()
        messages = result.get("messages", [])
        if not messages:
            return "No messages found."
        lines = []
        for m in messages[:count]:
            meta = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            lines.append(
                f"[{m['id']}] {headers.get('Date', '')[:16]} | "
                f"From: {headers.get('From', '?')} | "
                f"Subject: {headers.get('Subject', '(no subject)')}"
            )
        return "\n".join(lines)

    def _read(self, svc, message_id: str) -> str:
        if not message_id:
            return "message_id required"
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = self._extract_body(msg)
        return (
            f"From: {headers.get('From', '?')}\n"
            f"Subject: {headers.get('Subject', '?')}\n"
            f"Date: {headers.get('Date', '?')}\n\n"
            f"{body[:2000]}"
        )

    def _extract_body(self, msg: dict) -> str:
        payload = msg.get("payload", {})
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return "(no text body)"

    def _draft(self, svc, to: str, subject: str, body: str) -> str:
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return f"Draft saved (ID: {draft['id']}) — review before sending."

    def _send(self, svc, to: str, subject: str, body: str) -> str:
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Sent to {to}: {subject}"

    async def get_unread_count(self) -> int:
        svc = self._get_service()
        if not svc:
            return 0
        try:
            result = svc.users().messages().list(
                userId="me", q="is:unread", maxResults=1
            ).execute()
            return result.get("resultSizeEstimate", 0)
        except Exception:
            return 0

    async def get_urgent_unread(self) -> list[dict]:
        svc = self._get_service()
        if not svc:
            return []
        try:
            result = svc.users().messages().list(
                userId="me", q="is:unread is:important", maxResults=5
            ).execute()
            messages = []
            for m in result.get("messages", [])[:5]:
                meta = svc.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "Subject"]
                ).execute()
                headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
                messages.append({
                    "id": m["id"],
                    "from": headers.get("From", "?"),
                    "subject": headers.get("Subject", "(no subject)"),
                })
            return messages
        except Exception:
            return []
