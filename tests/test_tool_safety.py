import asyncio
import json
import sys
import types
import unittest

# Brain imports anthropic at module load. Unit tests exercise policy flow only,
# so provide a tiny stub instead of requiring external SDK credentials/dependency.
sys.modules.setdefault(
    "anthropic",
    types.SimpleNamespace(
        AsyncAnthropic=lambda *_, **__: None,
        RateLimitError=Exception,
        APIConnectionError=Exception,
    ),
)

from core.brain import Brain
from tools.email import EmailTool
from tools.registry import ToolBase, ToolRegistry, ToolSafety


class RecordingMemory:
    def __init__(self):
        self.pending_actions = []

    def add_pending_action(self, **kwargs):
        self.pending_actions.append(kwargs)
        return "approval-1"


class FakeRegistry:
    def __init__(self, tool):
        self.tool = tool

    def get(self, name):
        return self.tool if name == self.tool.name else None

    def to_claude_schema(self):
        return []


class DangerousTool(ToolBase):
    name = "dangerous_tool"
    description = "Dangerous test tool"
    action_policies = {
        "send": ToolSafety(
            action_type="external_communication",
            risk_level="high",
            requires_confirmation=True,
            reason="Sends a test message.",
        )
    }

    async def run(self, **kwargs):
        raise AssertionError("sensitive tool should not execute before approval")


class ToolSafetyTests(unittest.TestCase):
    def test_email_send_requires_confirmation(self):
        safety = EmailTool().safety_for({"action": "send"})

        self.assertTrue(safety.requires_confirmation)
        self.assertEqual(safety.action_type, "external_communication")
        self.assertEqual(safety.risk_level, "high")

    def test_low_risk_email_search_does_not_require_confirmation(self):
        safety = EmailTool().safety_for({"action": "search"})

        self.assertFalse(safety.requires_confirmation)
        self.assertEqual(safety.action_type, "read")

    def test_brain_queues_sensitive_tool_call_without_executing_it(self):
        memory = RecordingMemory()
        brain = object.__new__(Brain)
        brain._memory = memory
        brain._tools = FakeRegistry(DangerousTool())

        async def collect():
            chunks = []
            async for chunk in brain._execute_tool_and_continue(
                {
                    "id": "toolu_1",
                    "name": "dangerous_tool",
                    "input_json": json.dumps({"action": "send", "to": "user@example.com"}),
                },
                system="",
                messages=[],
                prior_response="",
            ):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(collect())

        self.assertEqual(len(memory.pending_actions), 1)
        self.assertIn("Confirmation required [approval-1]", "".join(chunks))
        self.assertEqual(memory.pending_actions[0]["tool_name"], "dangerous_tool")
        self.assertEqual(memory.pending_actions[0]["args"]["to"], "user@example.com")

    def test_registry_exposes_registered_tool_schema(self):
        registry = ToolRegistry()
        registry.register(DangerousTool())

        schemas = registry.to_claude_schema()

        self.assertEqual(schemas[0]["name"], "dangerous_tool")
        self.assertIn("input_schema", schemas[0])


if __name__ == "__main__":
    unittest.main()
