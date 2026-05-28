import os
import httpx
from tools.registry import ToolBase


class WebSearchTool(ToolBase):
    name = "web_search"
    description = "Search the web for current information, news, facts, or any topic."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "num_results": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
        },
        "required": ["query"],
    }

    async def run(self, query: str, num_results: int = 5) -> str:
        api_key = os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("SERPER_API_KEY")
        if not api_key:
            return f"[web_search] No search API key configured. Query was: {query}"

        if os.environ.get("BRAVE_SEARCH_API_KEY"):
            return await self._brave(query, num_results)
        return await self._serper(query, num_results)

    async def _brave(self, query: str, num: int) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]},
                params={"q": query, "count": num},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("web", {}).get("results", [])
            lines = []
            for r in results[:num]:
                lines.append(f"**{r.get('title', '')}**\n{r.get('description', '')}\n{r.get('url', '')}")
            return "\n\n".join(lines) or "No results found."

    async def _serper(self, query: str, num: int) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
                json={"q": query, "num": num},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("organic", [])
            lines = []
            for r in results[:num]:
                lines.append(f"**{r.get('title', '')}**\n{r.get('snippet', '')}\n{r.get('link', '')}")
            return "\n\n".join(lines) or "No results found."
