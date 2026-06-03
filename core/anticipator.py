"""
Proactive intelligence engine.

Runs as a background asyncio task. Monitors multiple data sources on a cadence
and builds a priority queue of things the user should know. Surfaces them when
the active mode allows interruption.

Priority levels:
  urgent  — surface immediately regardless of mode (except DEEP WORK lock)
  high    — surface at next natural break
  medium  — surface in morning/evening briefing
  low     — log only, available on request
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("anticipator")


@dataclass
class Alert:
    priority: str           # urgent | high | medium | low
    category: str           # calendar | commitment | email | health | finance | home
    message: str
    action_hint: str = ""   # suggested follow-up
    expires_at: Optional[str] = None
    surfaced: bool = False


class Anticipator:
    CHECK_INTERVAL = 300  # 5 minutes between full scans
    URGENT_INTERVAL = 60  # 1 minute for urgent-only scan

    def __init__(
        self,
        memory,
        mode_manager,
        context_aggregator,
        tool_registry,
        on_alert=None,
        push_notifier=None,
        relationship_engine=None,
    ):
        self._memory = memory
        self._modes = mode_manager
        self._context = context_aggregator
        self._tools = tool_registry
        self._on_alert = on_alert
        self._push = push_notifier
        self._relationships = relationship_engine
        self._queue: list[Alert] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            log.info("Anticipator started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    def get_pending_alerts(self, min_priority: str = "medium") -> list[Alert]:
        order = ["low", "medium", "high", "urgent"]
        threshold = order.index(min_priority)
        return [a for a in self._queue if not a.surfaced and order.index(a.priority) >= threshold]

    def mark_surfaced(self, alert: Alert) -> None:
        alert.surfaced = True

    def drain_surfaceable(self) -> list[Alert]:
        surfaceable = []
        for alert in self._queue:
            if not alert.surfaced and self._modes.should_interrupt(alert.priority):
                surfaceable.append(alert)
                alert.surfaced = True
        return surfaceable

    async def _loop(self) -> None:
        urgent_tick = 0
        full_tick = 0

        while self._running:
            try:
                await asyncio.sleep(self.URGENT_INTERVAL)
                urgent_tick += self.URGENT_INTERVAL
                full_tick += self.URGENT_INTERVAL

                await self._check_commitments()

                if full_tick >= self.CHECK_INTERVAL:
                    full_tick = 0
                    await asyncio.gather(
                        self._check_calendar(),
                        self._check_email(),
                        self._check_health(),
                        self._check_finance(),
                        self._check_home(),
                        self._check_watchlist(),
                        self._check_relationship_drift(),
                        return_exceptions=True,
                    )
                    self._prune_expired()

                surfaceable = self.drain_surfaceable()
                for alert in surfaceable:
                    if self._on_alert:
                        try:
                            await self._on_alert(alert)
                        except Exception as e:
                            log.error("Alert callback failed: %s", e)
                    if self._push and self._push.available:
                        try:
                            await self._push.send_alert(alert)
                        except Exception as e:
                            log.debug("Push delivery failed: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Anticipator loop error: %s", e)
                await asyncio.sleep(30)

    async def _check_commitments(self) -> None:
        overdue = self._memory.get_overdue_commitments()
        for c in overdue:
            if not self._already_queued("commitment", c["id"]):
                self._enqueue(Alert(
                    priority="urgent",
                    category="commitment",
                    message=f"Overdue: '{c['description']}' (to {c['made_to']})",
                    action_hint=f"Handle or defer this commitment to {c['made_to']}",
                ))

        due_soon = self._memory.get_commitments_due_soon(days=2)
        for c in due_soon:
            if not self._already_queued("commitment", c["id"]):
                deadline = c.get("deadline", "soon")
                self._enqueue(Alert(
                    priority="high",
                    category="commitment",
                    message=f"Due soon: '{c['description']}' (to {c['made_to']}) by {deadline}",
                    action_hint="Complete or defer this commitment",
                ))

    async def _check_calendar(self) -> None:
        cal = self._tools.get("calendar_read")
        if not cal:
            return
        try:
            events = await cal.get_upcoming(count=5)
            now = datetime.now(timezone.utc)

            for event in events:
                start_str = event.get("start_time", "")
                if not start_str:
                    continue
                try:
                    start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                except ValueError:
                    continue

                delta = start - now
                minutes = delta.total_seconds() / 60

                if 15 <= minutes <= 40:
                    title = event.get("title", "event")
                    if not self._already_queued("calendar", title):
                        action_hint = "Check prep notes or brief the user"
                        if self._relationships:
                            try:
                                attendees = self._relationships.extract_people_from_event(event)
                                if attendees:
                                    brief = self._relationships.get_pre_meeting_brief(attendees, title)
                                    action_hint = brief[:400]
                            except Exception:
                                pass
                        self._enqueue(Alert(
                            priority="urgent",
                            category="calendar",
                            message=f"{title} in {int(minutes)} minutes",
                            action_hint=action_hint,
                        ))

                elif 60 <= minutes <= 90:
                    title = event.get("title", "event")
                    if not self._already_queued("calendar", title + "_1h"):
                        self._enqueue(Alert(
                            priority="high",
                            category="calendar",
                            message=f"{title} in ~{int(minutes // 60)}h — prep time?",
                            action_hint="Pull relevant context for this meeting",
                        ))
        except Exception as e:
            log.warning("Calendar check failed: %s", e)

    async def _check_email(self) -> None:
        email = self._tools.get("email_read")
        if not email:
            return
        try:
            urgent = await email.get_urgent_unread()
            for msg in urgent[:3]:
                sender = msg.get("from", "unknown")
                subject = msg.get("subject", "")
                if not self._already_queued("email", subject):
                    self._enqueue(Alert(
                        priority="high",
                        category="email",
                        message=f"Urgent email from {sender}: {subject}",
                        action_hint="Review and respond",
                    ))
        except Exception as e:
            log.warning("Email check failed: %s", e)

    async def _check_health(self) -> None:
        health = self._tools.get("health_read")
        if not health:
            return
        try:
            alerts = await health.get_alerts()
            for a in alerts:
                if not self._already_queued("health", a.get("type", "")):
                    self._enqueue(Alert(
                        priority=a.get("priority", "medium"),
                        category="health",
                        message=a.get("message", ""),
                        action_hint=a.get("suggestion", ""),
                    ))
        except Exception as e:
            log.warning("Health check failed: %s", e)

    async def _check_finance(self) -> None:
        finance = self._tools.get("finance_read")
        if not finance:
            return
        try:
            alerts = await finance.get_alerts()
            for a in alerts:
                if not self._already_queued("finance", a.get("symbol", a.get("type", ""))):
                    self._enqueue(Alert(
                        priority=a.get("priority", "medium"),
                        category="finance",
                        message=a.get("message", ""),
                        action_hint=a.get("action", ""),
                    ))
        except Exception as e:
            log.warning("Finance check failed: %s", e)

    async def _check_home(self) -> None:
        home = self._tools.get("home_control")
        if not home:
            return
        try:
            anomalies = await home.get_anomalies()
            for a in anomalies:
                if not self._already_queued("home", a.get("device", "")):
                    self._enqueue(Alert(
                        priority=a.get("priority", "medium"),
                        category="home",
                        message=a.get("message", ""),
                        action_hint=a.get("action", ""),
                    ))
        except Exception as e:
            log.warning("Home check failed: %s", e)

    async def _check_relationship_drift(self) -> None:
        if not self._relationships:
            return
        try:
            drifted = self._relationships.get_drift_alerts()
            for d in drifted[:3]:
                key = f"drift:{d['name']}"
                if not self._already_queued("relationship", key):
                    self._enqueue(Alert(
                        priority="medium",
                        category="relationship",
                        message=(
                            f"Haven't connected with {d['name']} in {d['days_since']} days "
                            f"({d['relationship']})"
                        ),
                        action_hint=f"Reach out to {d['name']} — a quick message keeps the relationship warm.",
                    ))
        except Exception as e:
            log.warning("Relationship drift check failed: %s", e)

    async def _check_calendar(self) -> None:
        watches = self._memory.watchlist.get_active()
        if not watches:
            return
        for watch in watches:
            tool = self._tools.get(watch["tool_name"])
            if not tool:
                continue
            try:
                result = str(await tool.run(**watch["tool_args"]))
                changed = self._memory.watchlist.has_changed(watch, result)
                if changed:
                    key = f"watch:{watch['id']}"
                    if not self._already_queued("watchlist", key):
                        self._enqueue(Alert(
                            priority=watch.get("priority", "medium"),
                            category="watchlist",
                            message=f"Watch update — {watch['description']}: {result[:200]}",
                            action_hint="Review the change and take action if needed.",
                        ))
                        self._memory.watchlist.update_result(watch["id"], result, fired=True)
                        if not watch.get("recur"):
                            self._memory.watchlist.deactivate(watch["id"])
                else:
                    self._memory.watchlist.update_result(watch["id"], result, fired=False)
            except Exception as e:
                log.warning("Watchlist check failed for '%s': %s", watch["description"], e)

    def _enqueue(self, alert: Alert) -> None:
        self._queue.append(alert)
        if len(self._queue) > 200:
            self._queue = [a for a in self._queue if not a.surfaced][-100:]

    def _already_queued(self, category: str, key: str) -> bool:
        return any(
            a.category == category and key in a.message and not a.surfaced
            for a in self._queue
        )

    def _prune_expired(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._queue = [
            a for a in self._queue
            if not (a.expires_at and a.expires_at < now and not a.surfaced)
        ]

    def morning_brief(self) -> list[Alert]:
        """All medium+ alerts for morning summary."""
        return self.get_pending_alerts(min_priority="medium")
