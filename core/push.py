"""
Push notification delivery.

Delivers alerts to your phone when JARVIS is running headless.
Provider chain: ntfy.sh (free, recommended) → Pushover → generic webhook → silent.

ntfy.sh setup:
  1. Install ntfy app (iOS / Android) — free
  2. Subscribe to your topic name
  3. Set NTFY_TOPIC (and optionally NTFY_URL for self-hosted) in .env

Priority → ntfy:
  urgent  → 5 (bypasses Do Not Disturb)
  high    → 4
  medium  → 3 (default)
  low     → 2 (no sound)
"""
import logging
import os

import httpx

log = logging.getLogger("jarvis.push")

_NTFY_PRIORITY = {"urgent": 5, "high": 4, "medium": 3, "low": 2}
_NTFY_TAGS = {
    "calendar": "calendar",
    "commitment": "white_check_mark",
    "email": "email",
    "health": "heart",
    "finance": "chart_with_upwards_trend",
    "home": "house",
    "watchlist": "eyes",
    "meeting": "busts_in_silhouette",
    "relationship": "handshake",
    "code": "computer",
    "business": "briefcase",
}


class PushNotifier:
    def __init__(self):
        self._ntfy_url = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
        self._ntfy_topic = os.environ.get("NTFY_TOPIC", "")
        self._po_token = os.environ.get("PUSHOVER_API_TOKEN", "")
        self._po_user = os.environ.get("PUSHOVER_USER_KEY", "")
        self._webhook = os.environ.get("PUSH_WEBHOOK_URL", "")

    @property
    def available(self) -> bool:
        return bool(self._ntfy_topic or (self._po_token and self._po_user) or self._webhook)

    async def send(
        self,
        title: str,
        message: str,
        priority: str = "medium",
        category: str = "",
        click_url: str = "",
    ) -> bool:
        if not self.available:
            return False
        try:
            if self._ntfy_topic:
                return await self._ntfy(title, message, priority, category, click_url)
            if self._po_token and self._po_user:
                return await self._pushover(title, message, priority)
            if self._webhook:
                return await self._webhook_send(title, message, priority, category)
        except Exception as e:
            log.debug("Push failed: %s", e)
        return False

    async def send_alert(self, alert) -> bool:
        return await self.send(
            title=f"JARVIS — {getattr(alert, 'category', 'alert').title()}",
            message=alert.message,
            priority=alert.priority,
            category=getattr(alert, "category", ""),
        )

    async def _ntfy(
        self, title: str, message: str, priority: str, category: str, click_url: str
    ) -> bool:
        headers = {
            "Title": title,
            "Priority": str(_NTFY_PRIORITY.get(priority, 3)),
            "Tags": _NTFY_TAGS.get(category, "bell"),
            "Content-Type": "text/plain",
        }
        if click_url:
            headers["Click"] = click_url
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(
                f"{self._ntfy_url}/{self._ntfy_topic}",
                content=message.encode(),
                headers=headers,
            )
            r.raise_for_status()
        return True

    async def _pushover(self, title: str, message: str, priority: str) -> bool:
        po_p = {"urgent": 1, "high": 0, "medium": 0, "low": -1}.get(priority, 0)
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": self._po_token,
                    "user": self._po_user,
                    "title": title,
                    "message": message,
                    "priority": po_p,
                },
            )
            r.raise_for_status()
        return True

    async def _webhook_send(
        self, title: str, message: str, priority: str, category: str
    ) -> bool:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(
                self._webhook,
                json={"title": title, "message": message, "priority": priority, "category": category},
            )
            r.raise_for_status()
        return True
