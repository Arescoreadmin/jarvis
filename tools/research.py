"""
Deep research tool — multi-source synthesis.

Unlike web_search (which returns raw results), research:
  1. Runs 3-5 parallel searches across different query angles
  2. Synthesizes the results into a coherent briefing
  3. Cites sources
  4. Flags contradictions or uncertainty

Used when the user asks for a brief, a summary, or research on a topic.
"""
import asyncio
import os
from typing import Optional

import anthropic
import httpx
from tools.registry import ToolBase


class ResearchTool(ToolBase):
    name = "research"
    description = (
        "Conduct deep multi-source research on a topic and return a synthesized briefing. "
        "Use for: briefings before meetings, background on a person or company, "
        "market analysis, decision support, competitive intelligence."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The topic, person, company, or question to research",
            },
            "depth": {
                "type": "string",
                "enum": ["quick", "standard", "deep"],
                "description": "quick=1-2 min, standard=3-5 min, deep=comprehensive",
                "default": "standard",
            },
            "focus": {
                "type": "string",
                "description": "Optional focus: recent_news | background | financial | competitive | technical",
            },
        },
        "required": ["topic"],
    }

    async def run(self, topic: str, depth: str = "standard", focus: Optional[str] = None) -> str:
        queries = self._build_queries(topic, depth, focus)
        search_results = await asyncio.gather(
            *[self._search(q) for q in queries],
            return_exceptions=True,
        )

        valid_results = [
            (queries[i], r) for i, r in enumerate(search_results)
            if not isinstance(r, Exception) and r
        ]

        if not valid_results:
            return f"Could not retrieve research on '{topic}' — search APIs may not be configured."

        combined = "\n\n---\n\n".join(
            f"Query: {q}\n\n{result}" for q, result in valid_results
        )

        return await self._synthesize(topic, combined, depth)

    def _build_queries(self, topic: str, depth: str, focus: Optional[str]) -> list[str]:
        base_queries = [topic]

        if depth in ("standard", "deep"):
            if focus == "recent_news":
                base_queries += [f"{topic} news 2025", f"{topic} latest developments"]
            elif focus == "financial":
                base_queries += [f"{topic} financial performance revenue", f"{topic} market position"]
            elif focus == "competitive":
                base_queries += [f"{topic} competitors market share", f"{topic} competitive analysis"]
            elif focus == "technical":
                base_queries += [f"{topic} technical details how it works", f"{topic} documentation"]
            else:
                base_queries += [f"{topic} overview background", f"{topic} recent news 2025"]

        if depth == "deep":
            base_queries += [f"{topic} analysis expert opinion", f"{topic} risks challenges"]

        return base_queries[:5]

    async def _search(self, query: str) -> str:
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("SERPER_API_KEY")
        if not api_key:
            return ""

        try:
            if os.environ.get("BRAVE_SEARCH_API_KEY"):
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        "https://api.search.brave.com/res/v1/web/search",
                        headers={
                            "Accept": "application/json",
                            "X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"],
                        },
                        params={"q": query, "count": 5},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    results = data.get("web", {}).get("results", [])
                    return "\n".join(
                        f"{r.get('title', '')}: {r.get('description', '')} [{r.get('url', '')}]"
                        for r in results
                    )
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        "https://google.serper.dev/search",
                        headers={"X-API-KEY": os.environ["SERPER_API_KEY"]},
                        json={"q": query, "num": 5},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    results = data.get("organic", [])
                    return "\n".join(
                        f"{r.get('title', '')}: {r.get('snippet', '')} [{r.get('link', '')}]"
                        for r in results
                    )
        except Exception:
            return ""

    async def _synthesize(self, topic: str, raw: str, depth: str) -> str:
        length_guide = {
            "quick": "3-4 sentences",
            "standard": "150-250 words with bullet points for key facts",
            "deep": "300-500 words with sections: Overview, Key Facts, Recent Developments, Risks/Unknowns",
        }

        prompt = (
            f"Synthesize the following research into a briefing on: {topic}\n\n"
            f"Format: {length_guide.get(depth, '150-250 words')}\n"
            f"Requirements: factual, cite specific data points, flag anything uncertain. "
            f"JARVIS voice — no fluff.\n\n"
            f"RAW RESEARCH:\n{raw[:8000]}"
        )

        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else "Synthesis failed."
