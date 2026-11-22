import json
import unittest

from mcp_bridge import _serialize_messages


class SummaryCommandTests(unittest.TestCase):
    def test_instruction_only_on_summary_chat_commands(self):
        def message(text, kind="chat"):
            return {"id": 1, "sender": "Derek", "text": text,
                    "type": kind, "time": "12:00", "channel": "general"}

        original = message("/summary @agent")
        result = json.loads(_serialize_messages([original]))[0]
        self.assertEqual(result["text"], "/summary @agent")
        self.assertIn("current work item", result["command_instruction"])
        self.assertIn("most recently completed work item", result["command_instruction"])
        self.assertIn("Working: doing XYZ", result["command_instruction"])
        self.assertIn("Idle: completed XYZ", result["command_instruction"])
        self.assertNotIn("command_instruction", original)
        for text, kind in [("hello /summary @agent", "chat"),
                           ("/summary-more", "chat"), ("/summary", "summary")]:
            self.assertNotIn("command_instruction", json.loads(_serialize_messages([message(text, kind)]))[0])
        self.assertIn("command_instruction", json.loads(_serialize_messages([message(" /SUMMARY ")]))[0])
        self.assertIn("command_instruction", json.loads(_serialize_messages([message("/summarise @agent")]))[0])


if __name__ == "__main__":
    unittest.main()
