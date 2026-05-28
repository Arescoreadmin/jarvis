"""
Central tool registry.

Each tool registers itself with a name and a JSON schema.
The registry converts to Claude's tool_use format for injection into API calls.
"""
from typing import Any, Optional


class ToolBase:
    name: str = ""
    description: str = ""
    input_schema: dict = {}

    async def run(self, **kwargs) -> Any:
        raise NotImplementedError

    def to_claude_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolBase] = {}

    def register(self, tool: ToolBase) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolBase]:
        return self._tools.get(name)

    def all(self) -> list[ToolBase]:
        return list(self._tools.values())

    def to_claude_schema(self) -> list[dict]:
        return [t.to_claude_schema() for t in self._tools.values()]


def build_registry(config: dict) -> ToolRegistry:
    registry = ToolRegistry()

    from tools.web_search import WebSearchTool
    from tools.calendar import CalendarTool
    from tools.email import EmailTool
    from tools.tasks import TaskTool
    from tools.notes import NotesTool
    from tools.home import HomeControlTool
    from tools.finance import FinanceTool
    from tools.health import HealthTool
    from tools.research import ResearchTool

    registry.register(WebSearchTool())
    registry.register(CalendarTool(config.get("google_calendar_credentials", "")))
    registry.register(EmailTool(config.get("gmail_credentials", "")))
    registry.register(TaskTool())
    registry.register(NotesTool(config.get("notes_dir", "./data/notes")))
    registry.register(HomeControlTool(
        url=config.get("home_assistant_url", ""),
        token=config.get("home_assistant_token", ""),
    ))
    registry.register(FinanceTool(
        alpaca_key=config.get("alpaca_api_key", ""),
        alpaca_secret=config.get("alpaca_secret_key", ""),
    ))
    registry.register(HealthTool(oura_token=config.get("oura_personal_token", "")))
    registry.register(ResearchTool())

    return registry
