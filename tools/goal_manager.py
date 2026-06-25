"""
Goal Manager tool — exposes GoalEngine to Brain's tool-use loop.

Actions:
  add           — create a new goal
  add_milestone — add a milestone to an existing goal
  complete      — mark a milestone complete
  list          — list active goals
  get_blocked   — goals with overdue milestones
  get           — full detail for one goal
  set_status    — manually set goal status (pause, complete, abandon)
  suggest       — AI-suggested milestones for a goal title
"""
from tools.registry import ToolBase, ToolSafety
from core.goals import GoalEngine


class GoalManagerTool(ToolBase):
    name = "goal_manager"
    description = (
        "Track personal and project goals with milestones. "
        "Add goals, break them into milestones, mark progress, "
        "and surface blocked goals with overdue steps. "
        "Links to strategic objectives when provided."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "add", "add_milestone", "complete",
                    "list", "get_blocked", "get", "set_status", "suggest",
                ],
            },
            "title": {"type": "string"},
            "description": {"type": "string"},
            "deadline": {"type": "string", "description": "ISO date, e.g. 2026-07-31"},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "linked_objective_id": {"type": "string", "description": "ID of a strategic objective"},
            "goal_id": {"type": "string"},
            "milestone_id": {"type": "string"},
            "due_date": {"type": "string", "description": "ISO date for milestone"},
            "status": {
                "type": "string",
                "enum": ["active", "complete", "paused", "abandoned"],
            },
        },
    }

    action_policies = {
        "list":          ToolSafety(action_type="read",  risk_level="low"),
        "get":           ToolSafety(action_type="read",  risk_level="low"),
        "get_blocked":   ToolSafety(action_type="read",  risk_level="low"),
        "suggest":       ToolSafety(action_type="read",  risk_level="low"),
        "add":           ToolSafety(action_type="write", risk_level="low"),
        "add_milestone": ToolSafety(action_type="write", risk_level="low"),
        "complete":      ToolSafety(action_type="write", risk_level="low"),
        "set_status":    ToolSafety(action_type="write", risk_level="low"),
    }

    def __init__(self):
        self._engine = GoalEngine()

    async def run(self, action: str, **kwargs) -> str:
        if action == "add":
            gid = self._engine.add_goal(
                title=kwargs["title"],
                description=kwargs.get("description", ""),
                deadline=kwargs.get("deadline", ""),
                priority=kwargs.get("priority", "medium"),
                linked_objective_id=kwargs.get("linked_objective_id", ""),
            )
            return f"Goal created: '{kwargs['title']}' (id: {gid[:8]})"

        elif action == "add_milestone":
            mid = self._engine.add_milestone(
                goal_id=kwargs["goal_id"],
                title=kwargs["title"],
                due_date=kwargs.get("due_date", ""),
            )
            return f"Milestone added: '{kwargs['title']}' (id: {mid[:8]})"

        elif action == "complete":
            result = self._engine.complete_milestone(kwargs["milestone_id"])
            return f"Milestone {kwargs['milestone_id'][:8]} marked complete."

        elif action == "list":
            goals = self._engine.get_active_goals()
            if not goals:
                return "No active goals."
            lines = []
            for g in goals:
                deadline = f" · due {g['deadline'][:10]}" if g.get("deadline") else ""
                lines.append(
                    f"[{g['priority'].upper()}] {g['title']}{deadline} "
                    f"— {g['pct_complete']}% ({g['milestones_done']}/{g['milestone_count']} milestones)"
                    f" (id: {g['id'][:8]})"
                )
                for m in g["milestones"]:
                    icon = "✓" if m["status"] == "complete" else "○"
                    lines.append(f"  {icon} {m['title']}")
            return "\n".join(lines)

        elif action == "get_blocked":
            blocked = self._engine.get_blocked()
            if not blocked:
                return "No blocked goals — all milestones are on track."
            lines = [f"{len(blocked)} blocked goal(s):"]
            for g in blocked:
                lines.append(f"  ⚠ {g['title']} ({g['pct_complete']}% complete)")
                for m in g["milestones"]:
                    if m["status"] == "pending" and m.get("due_date") and m["due_date"] < __import__("core.goals", fromlist=["_now"])._now():
                        lines.append(f"    OVERDUE: {m['title']} (was due {m['due_date'][:10]})")
            return "\n".join(lines)

        elif action == "get":
            g = self._engine.get_goal(kwargs["goal_id"])
            if not g:
                return "Goal not found."
            import json
            return json.dumps(g, indent=2, default=str)

        elif action == "set_status":
            self._engine.set_goal_status(kwargs["goal_id"], kwargs["status"])
            return f"Goal status set to: {kwargs['status']}"

        elif action == "suggest":
            suggestions = await self._engine.suggest_milestones(
                kwargs["title"], kwargs.get("description", "")
            )
            return "Suggested milestones:\n" + "\n".join(f"  • {s}" for s in suggestions)

        return f"Unknown action: {action}"
