"""
Central tool registry.

Each tool registers itself with a name and a JSON schema.
The registry converts to Claude's tool_use format for injection into API calls.
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ToolSafety:
    """Execution policy metadata for a specific tool invocation."""

    action_type: str = "read"
    risk_level: str = "low"
    requires_confirmation: bool = False
    reason: str = "Read-only or low-risk action"


class ToolBase:
    name: str = ""
    description: str = ""
    input_schema: dict = {}
    action_type: str = "read"
    risk_level: str = "low"
    requires_confirmation: bool = False
    confirmation_reason: str = "Read-only or low-risk action"
    action_policies: dict[str, ToolSafety] = {}

    async def run(self, **kwargs) -> Any:
        raise NotImplementedError

    def safety_for(self, kwargs: dict) -> ToolSafety:
        """Return the risk policy for this invocation.

        Tools that multiplex actions through an ``action`` argument can declare
        per-action policies in ``action_policies``. Tools without an action map
        fall back to their class-level defaults.
        """
        action = str(kwargs.get("action", "")).lower()
        if action and action in self.action_policies:
            return self.action_policies[action]
        return ToolSafety(
            action_type=self.action_type,
            risk_level=self.risk_level,
            requires_confirmation=self.requires_confirmation,
            reason=self.confirmation_reason,
        )

    def to_claude_schema(self) -> dict:
        safety = self.safety_for({})
        description = self.description
        if safety.requires_confirmation or any(
            policy.requires_confirmation for policy in self.action_policies.values()
        ):
            description = f"{description} Sensitive actions require user confirmation before execution."
        return {
            "name": self.name,
            "description": description,
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

    from tools.github import GitHubTool
    from tools.git_ops import GitOpsTool
    from tools.web_search import WebSearchTool
    from tools.strategic_planning import StrategicPlanningTool
    from tools.goal_manager import GoalManagerTool
    from tools.calendar import CalendarTool
    from tools.email import EmailTool
    from tools.tasks import TaskTool
    from tools.notes import NotesTool
    from tools.home import HomeControlTool
    from tools.finance import FinanceTool
    from tools.health import HealthTool
    from tools.research import ResearchTool
    from tools.code_assistant import CodeAssistantTool
    from tools.architect import ArchitectTool
    from tools.business import BusinessTool

    registry.register(GitHubTool(autonomous_mode=config.get("pr_orchestration", {}).get("autonomous_mode", True)))
    registry.register(GitOpsTool())
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
    registry.register(CodeAssistantTool())
    registry.register(ArchitectTool())
    registry.register(BusinessTool())
    registry.register(StrategicPlanningTool())
    registry.register(GoalManagerTool())

    return registry
