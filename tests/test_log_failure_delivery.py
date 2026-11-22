import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import mcp_bridge
from store import MessageStore


class LogFailureDeliveryTests(unittest.TestCase):
    def test_broadcast_sends_current_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(str(Path(directory) / "messages.jsonl"))
            with patch("builtins.open", side_effect=PermissionError("locked")):
                store.add("codex", "hello")
            sent = []
            async def broadcast(raw):
                sent.append(json.loads(raw))
            with patch.object(app, "store", store), patch.object(app, "_broadcast", broadcast):
                asyncio.run(app._broadcast_log_status())
            self.assertEqual(sent[0]["type"], "log_status")
            self.assertEqual(sent[0]["data"], store.log_status())

    def test_log_status_reports_full_path_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.jsonl"
            store = MessageStore(str(path))
            statuses = []
            store.on_log_status(lambda: statuses.append(store.log_status()))
            with patch("builtins.open", side_effect=PermissionError(13, "Permission denied", str(path))):
                store.add("codex", "first")
            self.assertEqual(statuses[0]["path"], str(path.resolve()))
            self.assertIn("Permission denied", statuses[0]["error"])
            self.assertEqual(store.log_status(), statuses[0])
            store.add("codex", "second")
            self.assertIsNone(statuses[-1]["error"])
            self.assertIsNone(store.log_status()["error"])
            self.assertEqual(len(statuses), 2)

    def test_send_delivers_warns_and_recovers_log_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.jsonl"
            store = MessageStore(str(path))
            delivered = []
            store.on_message(delivered.append)
            with patch.object(mcp_bridge, "store", store), \
                 patch.object(mcp_bridge, "registry", None), \
                 patch.object(mcp_bridge, "_resolve_tool_identity", return_value=("codex", None)), \
                 patch.object(mcp_bridge, "_update_cursor"), \
                 patch("builtins.open", side_effect=PermissionError(13, "Permission denied", str(path))):
                result = mcp_bridge.chat_send(sender="codex", message="hello", channel="general")
            self.assertIn("Sent (id=0)", result)
            self.assertIn("Warning: message log write failed", result)
            self.assertIn("Permission denied", result)
            self.assertIn("may be lost", result)
            self.assertEqual([m["text"] for m in delivered], ["hello"])
            self.assertNotIn("persistence_error", delivered[0])
            store.add("codex", "after recovery")
            persisted = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([m["text"] for m in persisted], ["hello", "after recovery"])
            self.assertEqual([m["id"] for m in persisted], [0, 1])


if __name__ == "__main__":
    unittest.main()
