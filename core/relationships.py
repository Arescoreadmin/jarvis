"""
Relationship Intelligence Engine.

Builds rich, longitudinal contact profiles by synthesizing email, calendar,
and conversation history. The longer JARVIS runs, the more it knows about
the people in your life — and the more it can help you maintain and leverage
those relationships.

Core capabilities:
  - Drift detection: surfaces contacts you haven't reached in too long
  - Pre-meeting briefs: who's attending, last interaction, open commitments
  - Interaction recording: email/calendar events auto-update contact history
  - Context injection: when a contact is mentioned, Brain gets their full context

This is the moat: commercial AI products deliberately don't persist
relationship context. JARVIS does — and it compounds over time.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("jarvis.relationships")

# Days of silence before a drift alert fires, by relationship type
DRIFT_THRESHOLDS = {
    "vip": 14,
    "investor": 21,
    "partner": 21,
    "close": 30,
    "professional": 45,
    "friend": 60,
    "acquaintance": 90,
}


class RelationshipEngine:
    def __init__(self, memory, tool_registry=None):
        self._memory = memory
        self._tools = tool_registry

    # ── Drift detection ───────────────────────────────────────────────────────

    def get_drift_alerts(self) -> list[dict]:
        """Contacts overdue for a touch — sorted by urgency."""
        contacts = self._memory.get_all_contacts()
        now = datetime.now(timezone.utc)
        alerts = []

        for c in contacts:
            rel = (c.get("relationship") or "").lower()
            threshold = DRIFT_THRESHOLDS.get(rel)
            if not threshold:
                continue

            last_raw = c.get("last_interaction")
            if not last_raw:
                days_ago = threshold + 1
            else:
                try:
                    last_dt = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
                    days_ago = (now - last_dt).days
                except ValueError:
                    continue

            if days_ago >= threshold:
                alerts.append({
                    "name": c["name"],
                    "relationship": rel,
                    "days_since": days_ago,
                    "threshold": threshold,
                    "notes": (c.get("notes") or "")[:200],
                })

        return sorted(alerts, key=lambda x: x["days_since"], reverse=True)

    # ── Contact context ───────────────────────────────────────────────────────

    def get_contact_context(self, name: str) -> str:
        """Return formatted context for Brain injection when a person is mentioned."""
        contact = self._memory.get_contact(name)
        if not contact:
            return ""

        lines = [f"Contact: {name}"]
        if contact.get("relationship"):
            lines.append(f"  Relationship: {contact['relationship']}")
        if contact.get("email"):
            lines.append(f"  Email: {contact['email']}")
        if contact.get("communication_style"):
            lines.append(f"  Communication style: {contact['communication_style']}")
        if contact.get("notes"):
            lines.append(f"  Notes: {contact['notes'][:300]}")
        if contact.get("last_interaction"):
            lines.append(f"  Last contact: {contact['last_interaction'][:10]}")

        # Open commitments to this person
        commitments = [
            c for c in self._memory.get_pending_commitments()
            if c.get("made_to", "").lower() == name.lower()
        ]
        if commitments:
            lines.append("  Open commitments to them:")
            for c in commitments[:3]:
                deadline = f" (due {c['deadline'][:10]})" if c.get("deadline") else ""
                lines.append(f"    - {c['description'][:100]}{deadline}")

        # Recent episodic interactions
        recent = self._memory.search_episodes(name, limit=5)
        if recent:
            lines.append("  Recent interactions:")
            for ep in recent[-3:]:
                snippet = (ep.get("content") or "")[:120].replace("\n", " ")
                ts = (ep.get("timestamp") or "")[:10]
                lines.append(f"    [{ts}] {snippet}")

        return "\n".join(lines)

    # ── Pre-meeting brief ─────────────────────────────────────────────────────

    def get_pre_meeting_brief(self, attendees: list[str], event_title: str) -> str:
        """Generate a structured brief for an upcoming meeting."""
        sections = []
        for name in attendees:
            ctx = self.get_contact_context(name)
            if ctx:
                sections.append(ctx)

        if not sections:
            return f"No relationship history found for '{event_title}' attendees."

        header = f"Pre-meeting brief — {event_title}"
        return header + "\n\n" + "\n\n".join(sections)

    # ── Interaction recording ─────────────────────────────────────────────────

    def record_interaction(
        self,
        person_name: str,
        interaction_type: str,
        summary: str,
    ) -> None:
        """Record an interaction; update last_interaction timestamp."""
        if not self._memory.get_contact(person_name):
            self._memory.upsert_contact(person_name)
        self._memory.update_last_interaction(person_name)
        self._memory.add_episode(
            "interaction",
            f"[{interaction_type}] {person_name}: {summary}",
            people=[person_name],
        )

    # ── Attendee extraction ───────────────────────────────────────────────────

    def extract_people_from_event(self, event: dict) -> list[str]:
        """Extract attendee names from a calendar event dict."""
        raw = event.get("attendees", [])
        names = []
        for a in raw:
            if isinstance(a, dict):
                name = a.get("name") or a.get("displayName") or ""
                if not name and a.get("email"):
                    name = a["email"].split("@")[0].replace(".", " ").title()
                if name:
                    names.append(name)
            elif isinstance(a, str):
                names.append(a.split("@")[0].replace(".", " ").title())
        return [n for n in names if n]
