"""
Clarification Memory tool — exposes ClarificationMemory to Brain's tool-use loop.

Actions:
  add_clarification  — store a user-provided clarification permanently
  add_assumption     — manually log an assumption Jarvis made
  get_relevant       — retrieve clarifications relevant to a context string
  list_assumptions   — list open (unconfirmed) assumptions
  list_clarifications — list all stored clarifications
"""
from tools.registry import ToolBase, ToolSafety
from core.clarifications import ClarificationMemory


class ClarificationTool(ToolBase):
    name = "clarification_memory"
    description = (
        "Store and retrieve clarifications about how the user wants things interpreted. "
        "When the user says 'remember that X means Y' or corrects an assumption, "
        "store it so Jarvis never asks the same question twice. "
        "Also tracks open assumptions Jarvis has made that haven't been confirmed."
    )
    input_schema = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "add_clarification", "add_assumption",
                    "get_relevant", "list_assumptions", "list_clarifications",
                ],
            },
            "trigger": {"type": "string", "description": "The phrase or context the clarification applies to"},
            "clarification": {"type": "string", "description": "What the trigger actually means"},
            "scope": {
                "type": "string",
                "enum": ["session", "permanent"],
                "description": "How long to remember this clarification",
            },
            "context": {"type": "string", "description": "Assumption text or context for relevance lookup"},
            "confidence": {"type": "number", "description": "Confidence of the assumption, 0.0-1.0"},
        },
    }

    action_policies = {
        "get_relevant":        ToolSafety(action_type="read",  risk_level="low"),
        "list_assumptions":    ToolSafety(action_type="read",  risk_level="low"),
        "list_clarifications": ToolSafety(action_type="read",  risk_level="low"),
        "add_clarification":   ToolSafety(action_type="write", risk_level="low"),
        "add_assumption":      ToolSafety(action_type="write", risk_level="low"),
    }

    def __init__(self):
        self._memory = ClarificationMemory()

    async def run(self, action: str, **kwargs) -> str:
        if action == "add_clarification":
            cid = self._memory.add_clarification(
                trigger=kwargs["trigger"],
                clarification=kwargs["clarification"],
                scope=kwargs.get("scope", "permanent"),
            )
            return f"Clarification stored: '{kwargs['trigger']}' → {kwargs['clarification']} (id: {cid[:8]})"

        elif action == "add_assumption":
            aid = self._memory.add_assumption(
                context=kwargs.get("context", ""),
                assumption=kwargs.get("context", ""),
                confidence=kwargs.get("confidence", 0.8),
            )
            return f"Assumption logged (id: {aid[:8]})"

        elif action == "get_relevant":
            results = self._memory.get_relevant(kwargs.get("context", ""))
            if not results:
                return "No relevant clarifications found."
            lines = [f'• "{r["trigger"]}" → {r["clarification"]}' for r in results]
            return "\n".join(lines)

        elif action == "list_assumptions":
            rows = self._memory.list_open_assumptions()
            if not rows:
                return "No open assumptions."
            lines = [f'  [{r["id"][:8]}] {r["assumption"]} (confidence: {r["confidence"]})' for r in rows]
            return f"{len(rows)} open assumption(s):\n" + "\n".join(lines)

        elif action == "list_clarifications":
            rows = self._memory.list_clarifications()
            if not rows:
                return "No clarifications stored yet."
            lines = [f'• "{r["trigger"]}" → {r["clarification"]} [{r["scope"]}]' for r in rows]
            return "\n".join(lines)

        return f"Unknown action: {action}"
