"""
Nightly memory distillation.

Runs once per day (or on demand) to:
  1. Summarize the day's episodic memory into a single daily digest entry
  2. Extract behavioral patterns (preferences, habits, recurring topics)
  3. Update the semantic memory with distilled facts
  4. Prune raw episodes older than the retention window

The distilled behavioral model is stored as semantic memory entries
under the namespace "profile/" so Brain can surface them in context.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic

log = logging.getLogger("jarvis.distiller")


DISTILL_PROMPT = """\
You are analyzing a day's worth of JARVIS interaction logs to extract
durable behavioral facts about the user.

Today's interactions (most recent first):
{episodes}

Tasks:
1. Write a 2-3 sentence daily digest capturing the key themes of today.
2. Extract up to 10 behavioral facts in JSON format:
   [{{"key": "prefers_short_answers", "value": "true", "confidence": "high"}}, ...]
   Keys must be snake_case. Values must be strings. Confidence: high/medium/low.
   Focus on: communication preferences, recurring topics, working patterns,
   recurring concerns, tools used most, decision-making style.

Respond ONLY with this JSON structure:
{{
  "digest": "...",
  "facts": [{{"key": "...", "value": "...", "confidence": "..."}}]
}}
"""


class Distiller:
    MODEL = "claude-haiku-4-5-20251001"
    RETENTION_DAYS = 30

    def __init__(self, memory):
        self._memory = memory
        self._client = anthropic.AsyncAnthropic()
        self._last_run: Optional[datetime] = None

    async def run(self, force: bool = False) -> str:
        now = datetime.now(timezone.utc)

        # Skip if already ran today (unless forced)
        if not force and self._last_run:
            if self._last_run.date() == now.date():
                return "Distillation already ran today — skipping."

        log.info("Starting memory distillation")
        episodes = self._memory.get_recent_episodes(hours=24, limit=200)
        if not episodes:
            log.info("No episodes to distill")
            return "No episodes to distill."

        text_block = "\n---\n".join(
            f"[{e.get('role', '?')}] {e.get('content', '')}"
            for e in episodes
        )
        prompt = DISTILL_PROMPT.format(episodes=text_block[:12000])

        try:
            resp = await self._client.messages.create(
                model=self.MODEL,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()

            # Strip markdown code fence if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            data = json.loads(raw)
        except Exception as e:
            log.error("Distillation LLM call failed: %s", e)
            return f"Distillation failed: {e}"

        digest = data.get("digest", "")
        facts = data.get("facts", [])

        date_str = now.strftime("%Y-%m-%d")
        self._memory.set_semantic(f"digest/{date_str}", digest)

        updated = 0
        for fact in facts:
            key = fact.get("key", "").strip()
            value = fact.get("value", "").strip()
            conf = fact.get("confidence", "low")
            if key and value and conf in ("high", "medium"):
                self._memory.set_semantic(f"profile/{key}", value)
                updated += 1

        self._prune_old_episodes()

        self._last_run = now
        log.info("Distillation complete: %d facts extracted", updated)
        return f"Distilled {len(episodes)} episodes → {updated} behavioral facts updated. Digest: {digest[:100]}…"

    def _prune_old_episodes(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.RETENTION_DAYS)).isoformat()
        try:
            self._memory._conn.execute(
                "DELETE FROM episodic WHERE timestamp < ?", (cutoff,)
            )
            self._memory._conn.commit()
        except Exception as e:
            log.warning("Episode pruning failed: %s", e)

    def should_run_tonight(self) -> bool:
        now = datetime.now(timezone.utc)
        if self._last_run and self._last_run.date() == now.date():
            return False
        return 2 <= now.hour < 4  # run in the 2–4 AM window
