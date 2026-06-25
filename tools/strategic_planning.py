"""
Strategic Planning tool — exposes StrategyEngine to Brain's tool-use loop.

Actions:
  set_objective        — create a new objective
  add_key_result       — add a KR to an objective
  update_kr            — log a progress update on a KR
  list_objectives      — list all objectives (filtered by horizon/status)
  get_objective        — full detail for one objective
  get_at_risk          — objectives that are at_risk or off_track
  generate_weekly_review — Claude-written strategic week-in-review
  draft_okrs           — generate OKR suggestions from a plain-English description
  set_status           — manually set objective status
"""
from tools.registry import ToolBase, ToolSafety
from core.strategy import StrategyEngine


class StrategicPlanningTool(ToolBase):
    name = "strategic_planning"
    description = (
        "Manage the strategic plan: set objectives and key results (OKRs), "
        "track progress, identify what's at risk, generate weekly reviews, "
        "and draft new OKRs from a description. "
        "Horizons: weekly | monthly | quarterly | annual."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "set_objective",
                    "add_key_result",
                    "update_kr",
                    "list_objectives",
                    "get_objective",
                    "get_at_risk",
                    "generate_weekly_review",
                    "draft_okrs",
                    "set_status",
                ],
            },
            "title": {"type": "string"},
            "description": {"type": "string"},
            "horizon": {
                "type": "string",
                "enum": ["weekly", "monthly", "quarterly", "annual"],
            },
            "objective_id": {"type": "string"},
            "kr_id": {"type": "string"},
            "target_value": {"type": "number"},
            "current_value": {"type": "number"},
            "baseline_value": {"type": "number"},
            "unit": {"type": "string", "description": "e.g. '$', '%', 'users', 'calls'"},
            "note": {"type": "string", "description": "Context note for a progress update"},
            "status": {
                "type": "string",
                "enum": ["on_track", "at_risk", "off_track", "complete", "paused"],
            },
            "owner": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "filter_horizon": {"type": "string"},
            "filter_status": {"type": "string"},
        },
    }

    action_policies = {
        "list_objectives":       ToolSafety(action_type="read",   risk_level="low"),
        "get_objective":         ToolSafety(action_type="read",   risk_level="low"),
        "get_at_risk":           ToolSafety(action_type="read",   risk_level="low"),
        "generate_weekly_review":ToolSafety(action_type="read",   risk_level="low"),
        "draft_okrs":            ToolSafety(action_type="read",   risk_level="low"),
        "set_objective":         ToolSafety(action_type="write",  risk_level="low"),
        "add_key_result":        ToolSafety(action_type="write",  risk_level="low"),
        "update_kr":             ToolSafety(action_type="write",  risk_level="low"),
        "set_status":            ToolSafety(action_type="write",  risk_level="low"),
    }

    def __init__(self):
        self._engine = StrategyEngine()

    async def run(self, action: str, **kwargs) -> str:
        if action == "set_objective":
            oid = self._engine.add_objective(
                title=kwargs["title"],
                description=kwargs.get("description", ""),
                horizon=kwargs.get("horizon", "quarterly"),
                owner=kwargs.get("owner", ""),
                start_date=kwargs.get("start_date", ""),
                end_date=kwargs.get("end_date", ""),
            )
            return f"Objective created: {kwargs['title']} (id: {oid[:8]})"

        elif action == "add_key_result":
            krid = self._engine.add_key_result(
                objective_id=kwargs["objective_id"],
                title=kwargs["title"],
                target_value=float(kwargs["target_value"]),
                baseline_value=float(kwargs.get("baseline_value", 0)),
                unit=kwargs.get("unit", ""),
            )
            return f"Key result added: {kwargs['title']} (id: {krid[:8]})"

        elif action == "update_kr":
            result = self._engine.update_progress(
                kr_id=kwargs["kr_id"],
                current_value=float(kwargs["current_value"]),
                note=kwargs.get("note", ""),
            )
            return (
                f"Progress updated: {result['pct_complete']}% complete "
                f"(status: {result['status']})"
            )

        elif action == "list_objectives":
            import json
            objs = self._engine.list_objectives(
                horizon=kwargs.get("filter_horizon"),
                status=kwargs.get("filter_status"),
            )
            if not objs:
                return "No objectives found."
            lines = []
            for o in objs:
                lines.append(f"[{o['horizon'].upper()}] {o['title']} — {o['status']} (id: {o['id'][:8]})")
                for kr in o["key_results"]:
                    lines.append(
                        f"  • {kr['title']}: {kr['current_value']}/{kr['target_value']}"
                        f"{' ' + kr['unit'] if kr['unit'] else ''} ({kr['pct_complete']}%)"
                    )
            return "\n".join(lines)

        elif action == "get_objective":
            obj = self._engine.get_objective(kwargs["objective_id"])
            if not obj:
                return "Objective not found."
            import json
            return json.dumps(obj, indent=2, default=str)

        elif action == "get_at_risk":
            at_risk = self._engine.get_at_risk()
            if not at_risk:
                return "All objectives are on track."
            lines = [f"{len(at_risk)} objective(s) need attention:"]
            for o in at_risk:
                lines.append(f"  ⚠ {o['title']} ({o['status']}, {o['horizon']})")
                for kr in o["key_results"]:
                    if kr["status"] in ("at_risk", "off_track"):
                        lines.append(f"    - {kr['title']}: {kr['pct_complete']}% — {kr['status']}")
            return "\n".join(lines)

        elif action == "generate_weekly_review":
            return await self._engine.generate_weekly_review()

        elif action == "draft_okrs":
            import json
            draft = await self._engine.generate_okrs_from_description(kwargs.get("description", ""))
            return json.dumps(draft, indent=2)

        elif action == "set_status":
            self._engine.set_objective_status(kwargs["objective_id"], kwargs["status"])
            return f"Objective status set to: {kwargs['status']}"

        return f"Unknown action: {action}"
