"""
Real-time context aggregator.

Builds a ContextSnapshot before every LLM request, injecting:
  - current time, day, timezone
  - upcoming calendar events
  - unread email count
  - pending tasks
  - active projects
  - health snapshot
  - financial pulse
  - active mode
  - commitments due soon
  - any pending proactive alerts

Cached for 60 seconds to avoid hammering APIs on every token.
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class ContextSnapshot:
    timestamp: str = ""
    day_of_week: str = ""
    time_of_day: str = ""          # morning | afternoon | evening | night
    active_mode: str = "executive"
    upcoming_events: list[dict] = field(default_factory=list)
    unread_email_count: int = 0
    pending_task_count: int = 0
    active_projects: list[str] = field(default_factory=list)
    commitments_due_soon: list[dict] = field(default_factory=list)
    overdue_commitments: list[dict] = field(default_factory=list)
    health_note: str = ""          # e.g. "HRV low today — consider lighter cognitive load"
    financial_alert: str = ""      # e.g. "AAPL -4.2% today"
    dev_context: str = ""          # e.g. "Claude Code / jarvis — 42m, 31 tool calls"
    proactive_alerts: list[dict] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = [
            f"[CONTEXT SNAPSHOT — {self.timestamp}]",
            f"Day: {self.day_of_week} | Period: {self.time_of_day} | Mode: {self.active_mode.upper()}",
        ]

        if self.overdue_commitments:
            lines.append(f"⚠ OVERDUE COMMITMENTS: {len(self.overdue_commitments)}")
            for c in self.overdue_commitments[:3]:
                lines.append(f"  - {c['description']} (to {c['made_to']}, was due {c['deadline']})")

        if self.upcoming_events:
            lines.append("Upcoming:")
            for e in self.upcoming_events[:3]:
                lines.append(f"  - {e.get('time', '?')} {e.get('title', 'event')}")

        if self.commitments_due_soon:
            lines.append(f"Commitments due in 7 days: {len(self.commitments_due_soon)}")
            for c in self.commitments_due_soon[:2]:
                lines.append(f"  - {c['description']} (to {c['made_to']}) by {c['deadline']}")

        if self.unread_email_count:
            lines.append(f"Unread email: {self.unread_email_count}")

        if self.pending_task_count:
            lines.append(f"Open tasks: {self.pending_task_count}")

        if self.active_projects:
            lines.append(f"Active projects: {', '.join(self.active_projects)}")

        if self.health_note:
            lines.append(f"Health: {self.health_note}")

        if self.financial_alert:
            lines.append(f"Finance: {self.financial_alert}")

        if self.dev_context:
            lines.append(self.dev_context)

        if self.proactive_alerts:
            lines.append("Alerts:")
            for a in self.proactive_alerts:
                lines.append(f"  [{a['priority'].upper()}] {a['message']}")

        return "\n".join(lines)


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


class ContextAggregator:
    CACHE_TTL = 60  # seconds

    def __init__(self, memory, mode_manager, tool_registry):
        self._memory = memory
        self._modes = mode_manager
        self._tools = tool_registry
        self._cache: Optional[ContextSnapshot] = None
        self._cache_at: float = 0

    async def get(self, force: bool = False) -> ContextSnapshot:
        if not force and self._cache and (time.monotonic() - self._cache_at < self.CACHE_TTL):
            return self._cache

        snap = await self._build()
        self._cache = snap
        self._cache_at = time.monotonic()
        return snap

    def invalidate(self) -> None:
        self._cache = None

    async def _build(self) -> ContextSnapshot:
        now = datetime.now(timezone.utc)
        snap = ContextSnapshot(
            timestamp=now.strftime("%Y-%m-%d %H:%M %Z"),
            day_of_week=now.strftime("%A"),
            time_of_day=_time_of_day(now.hour),
            active_mode=self._modes.current.value,
        )

        snap.commitments_due_soon = self._memory.get_commitments_due_soon(days=7)
        snap.overdue_commitments = self._memory.get_overdue_commitments()

        snap.active_projects = self._memory.get_semantic("active_projects") or []

        results = await asyncio.gather(
            self._fetch_calendar(),
            self._fetch_email_count(),
            self._fetch_task_count(),
            self._fetch_health(),
            self._fetch_finance(),
            return_exceptions=True,
        )

        calendar_events, email_count, task_count, health_note, finance_alert = results

        try:
            snap.dev_context = self._memory.dev.build_context_block()
        except Exception:
            pass

        if not isinstance(calendar_events, Exception):
            snap.upcoming_events = calendar_events or []
        if not isinstance(email_count, Exception):
            snap.unread_email_count = email_count or 0
        if not isinstance(task_count, Exception):
            snap.pending_task_count = task_count or 0
        if not isinstance(health_note, Exception):
            snap.health_note = health_note or ""
        if not isinstance(finance_alert, Exception):
            snap.financial_alert = finance_alert or ""

        return snap

    async def _fetch_calendar(self) -> list[dict]:
        try:
            cal = self._tools.get("calendar_read")
            if cal:
                return await cal.get_upcoming(count=5)
        except Exception:
            pass
        return []

    async def _fetch_email_count(self) -> int:
        try:
            email = self._tools.get("email_read")
            if email:
                return await email.get_unread_count()
        except Exception:
            pass
        return 0

    async def _fetch_task_count(self) -> int:
        try:
            tasks = self._tools.get("task_read")
            if tasks:
                return await tasks.get_pending_count()
        except Exception:
            pass
        return 0

    async def _fetch_health(self) -> str:
        try:
            health = self._tools.get("health_read")
            if health:
                return await health.get_daily_note()
        except Exception:
            pass
        return ""

    async def _fetch_finance(self) -> str:
        try:
            finance = self._tools.get("finance_read")
            if finance:
                return await finance.get_pulse()
        except Exception:
            pass
        return ""
