import tempfile
import unittest
from pathlib import Path

import core.memory as memory_module
from core.memory import Memory


class MemoryPendingActionTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = memory_module.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        memory_module.DB_PATH = Path(self._tmp.name) / "memory.db"

    def tearDown(self):
        memory_module.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def test_pending_action_round_trip_and_resolution(self):
        memory = Memory()
        try:
            action_id = memory.add_pending_action(
                tool_name="email_read",
                args={"action": "send", "to": "user@example.com"},
                action_type="external_communication",
                risk_level="high",
                reason="Sends an email.",
            )

            pending = memory.get_pending_actions()

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["id"], action_id)
            self.assertEqual(pending[0]["args"]["to"], "user@example.com")

            memory.resolve_pending_action(action_id, "rejected", "Rejected by test")

            self.assertEqual(memory.get_pending_actions(), [])
            resolved = memory.get_pending_action(action_id)
            self.assertEqual(resolved["status"], "rejected")
            self.assertEqual(resolved["result"], "Rejected by test")
        finally:
            memory.close()


if __name__ == "__main__":
    unittest.main()
