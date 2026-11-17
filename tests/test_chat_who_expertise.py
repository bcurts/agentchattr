"""chat_who lists configured expertise so agents can find who to @mention."""

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_bridge  # noqa: E402


class ChatWhoExpertiseTests(unittest.TestCase):
    def setUp(self):
        self._config = mcp_bridge.config
        self.addCleanup(setattr, mcp_bridge, "config", self._config)
        self.addCleanup(mcp_bridge._presence.clear)
        mcp_bridge._presence.clear()

    def test_without_expertise_keeps_original_output(self):
        mcp_bridge.config = {"agents": {"claude": {"command": "claude"}}}
        mcp_bridge._presence["claude"] = time.time()
        self.assertEqual(mcp_bridge.chat_who(), "Online: claude")
        mcp_bridge._presence.clear()
        self.assertEqual(mcp_bridge.chat_who(), "Nobody online.")

    def test_lists_experts_online_and_offline(self):
        mcp_bridge.config = {"agents": {
            "ui": {"expertise": "frontend repo"},
            "api": {"expertise": "backend API"},
            "claude": {"command": "claude"},
        }}
        mcp_bridge._presence["ui"] = time.time()
        mcp_bridge._presence["claude"] = time.time()
        out = mcp_bridge.chat_who().splitlines()
        self.assertIn("@ui [online] frontend repo", out)
        self.assertIn("@api [offline] backend API", out)
        self.assertIn("Also online: claude", out)


if __name__ == "__main__":
    unittest.main()
