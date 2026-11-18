"""`@agent //cmd args` from a human becomes the raw slash command `/cmd args`."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from router import slash_passthrough  # noqa: E402


class SlashPassthroughTests(unittest.TestCase):
    def test_command_with_args(self):
        self.assertEqual(slash_passthrough("@claude //goal fix the login bug"), "/goal fix the login bug")

    def test_bare_and_namespaced_commands(self):
        self.assertEqual(slash_passthrough("@claude //compact"), "/compact")
        self.assertEqual(slash_passthrough("//plugin:skill go"), "/plugin:skill go")

    def test_urls_and_plain_text_are_not_commands(self):
        for text in ("@claude look at https://example.com/x", "@claude hi", "", None, "a//b"):
            self.assertEqual(slash_passthrough(text), "")


if __name__ == "__main__":
    unittest.main()
