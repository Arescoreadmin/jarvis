"""
Health & biometrics tool via Oura Ring API.
Surfaces sleep quality, HRV, readiness, and activity data.
Feeds proactive recommendations into the anticipator.
"""
import os
from datetime import datetime, timedelta, timezone

import httpx
from tools.registry import ToolBase


OURA_BASE = "https://api.ouraring.com/v2/usercollection"


class HealthTool(ToolBase):
    name = "health_read"
    description = (
        "Get health and biometric data: sleep quality, HRV, readiness score, activity summary. "
        "Use to inform daily recommendations and workload calibration."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "enum": ["summary", "sleep", "hrv", "readiness", "activity"],
                "description": "Which health metric to retrieve",
            },
            "days": {
                "type": "integer",
                "description": "Number of past days to include (default 1)",
                "default": 1,
            },
        },
        "required": ["metric"],
    }

    def __init__(self, oura_token: str = ""):
        self._token = oura_token or os.environ.get("OURA_PERSONAL_TOKEN", "")

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def run(self, metric: str = "summary", days: int = 1) -> str:
        if not self._token:
            return "[health] Oura token not configured (OURA_PERSONAL_TOKEN missing)"
        if metric == "summary":
            return await self._summary()
        if metric == "sleep":
            return await self._sleep(days)
        if metric == "hrv":
            return await self._hrv(days)
        if metric == "readiness":
            return await self._readiness(days)
        if metric == "activity":
            return await self._activity(days)
        return "Unknown metric"

    def _date_range(self, days: int) -> tuple[str, str]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        return str(start), str(end)

    async def _summary(self) -> str:
        readiness = await self._readiness(1)
        sleep = await self._sleep(1)
        hrv = await self._hrv(1)
        return f"Readiness: {readiness}\nSleep: {sleep}\nHRV: {hrv}"

    async def _readiness(self, days: int) -> str:
        start, end = self._date_range(days)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{OURA_BASE}/daily_readiness",
                headers=self._headers,
                params={"start_date": start, "end_date": end},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                r = data[-1]
                score = r.get("score", "?")
                return f"Score {score}/100 — {self._readiness_label(score)}"
        return "No readiness data"

    async def _sleep(self, days: int) -> str:
        start, end = self._date_range(days)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{OURA_BASE}/daily_sleep",
                headers=self._headers,
                params={"start_date": start, "end_date": end},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                s = data[-1]
                score = s.get("score", "?")
                total = s.get("contributors", {}).get("total_sleep", "?")
                return f"Score {score}/100 | {total}h total"
        return "No sleep data"

    async def _hrv(self, days: int) -> str:
        start, end = self._date_range(days)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{OURA_BASE}/daily_readiness",
                headers=self._headers,
                params={"start_date": start, "end_date": end},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                contributors = data[-1].get("contributors", {})
                hrv = contributors.get("hrv_balance", "?")
                return f"HRV balance: {hrv}/100"
        return "No HRV data"

    async def _activity(self, days: int) -> str:
        start, end = self._date_range(days)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{OURA_BASE}/daily_activity",
                headers=self._headers,
                params={"start_date": start, "end_date": end},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                a = data[-1]
                score = a.get("score", "?")
                steps = a.get("steps", "?")
                cal = a.get("active_calories", "?")
                return f"Activity {score}/100 | {steps:,} steps | {cal} active kcal"
        return "No activity data"

    async def get_daily_note(self) -> str:
        """Brief health note for context injection."""
        if not self._token:
            return ""
        try:
            readiness_str = await self._readiness(1)
            if "Score" in readiness_str:
                score_part = readiness_str.split("/")[0].replace("Score ", "")
                try:
                    score = int(score_part)
                    if score < 60:
                        return f"Readiness {score}/100 — consider lighter cognitive load today"
                    if score >= 85:
                        return f"Readiness {score}/100 — high-performance day"
                    return f"Readiness {score}/100"
                except ValueError:
                    pass
        except Exception:
            pass
        return ""

    async def get_alerts(self) -> list[dict]:
        """Called by anticipator for health-based alerts."""
        if not self._token:
            return []
        alerts = []
        try:
            readiness_str = await self._readiness(1)
            if "Score" in readiness_str:
                score_part = readiness_str.split("/")[0].replace("Score ", "")
                score = int(score_part)
                if score < 50:
                    alerts.append({
                        "type": "readiness",
                        "priority": "medium",
                        "message": f"Readiness is low ({score}/100) — heavy workload not recommended",
                        "suggestion": "Protect deep work time. Push non-critical tasks.",
                    })
        except Exception:
            pass
        return alerts

    def _readiness_label(self, score) -> str:
        try:
            s = int(score)
            if s >= 85:
                return "peak"
            if s >= 70:
                return "good"
            if s >= 55:
                return "moderate"
            return "low"
        except (ValueError, TypeError):
            return ""
