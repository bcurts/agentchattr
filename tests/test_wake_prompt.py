"""T1: prescriptive wake prompts, a first-trigger marker that survives restarts,
and standing MCP instructions without routine join/rules/summary calls."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_bridge
import wrapper


class _StopWatcher(BaseException):
    pass


class WakePromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.queue = root / "claude_queue.jsonl"
        self.state = root / "claude.session.json"
        self.session = ["cli-100"]
        self.rules = None

    def append(self, channel="general", **extra):
        with self.queue.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"channel": channel, "text": "m", **extra}) + "\n")

    def wake(self, inject=None, restart_fn=None):
        """Queue one mention and run a fresh watcher (a watcher restart) for one poll."""
        self.append()
        prompts = []

        def sleep(seconds):
            if seconds == 1:
                raise _StopWatcher()

        with (patch.object(wrapper.time, "sleep", side_effect=sleep),
              patch.object(wrapper, "_fetch_role", return_value=""),
              patch.object(wrapper, "_fetch_active_rules", return_value=self.rules),
              patch.object(wrapper, "_report_rule_sync")):
            with self.assertRaises(_StopWatcher):
                wrapper._queue_watcher(
                    lambda: ("claude", self.queue), inject or prompts.append,
                    session_state_path=self.state,
                    get_session_fn=lambda: self.session[0],
                    restart_fn=restart_fn)
        return prompts

    def test_later_trigger_is_prescriptive_and_not_a_new_session(self):
        self.wake()
        [prompt] = self.wake()
        self.assertIn("read #general", prompt)
        self.assertIn("chat_read once", prompt)
        self.assertIn("END YOUR TURN", prompt)
        self.assertIn("Never wait by sleeping or re-reading", prompt)
        self.assertIn("chat_react(msg_id=", prompt)
        self.assertIn("sender=", prompt.split("chat_react(")[1].split(")")[0])
        self.assertIn("@mention another agent only when they must act", prompt)
        self.assertNotIn("NEW SESSION", prompt)
        self.assertNotIn("chat_join", prompt)

    def test_first_trigger_asks_for_join_and_summary(self):
        [prompt] = self.wake()
        self.assertIn("NEW SESSION", prompt)
        self.assertIn("chat_join(name=", prompt)
        self.assertIn("chat_summary(action='read', sender=", prompt)
        self.assertIn("channel='general'", prompt)
        self.assertIn("END YOUR TURN", prompt)

    def test_marker_survives_watcher_restart(self):
        self.wake()
        # Each wake() call is a brand-new watcher, as after _watcher_monitor restarts it.
        [second] = self.wake()
        [third] = self.wake()
        self.assertNotIn("NEW SESSION", second)
        self.assertNotIn("NEW SESSION", third)

    def test_new_cli_session_gets_join_and_rules_again(self):
        self.rules = {"epoch": 3, "rules": ["be brief"], "refresh_interval": 0}
        [first] = self.wake()
        [same] = self.wake()
        self.session[0] = "cli-200"  # the CLI process was relaunched
        [fresh] = self.wake()
        self.assertIn("RULES: be brief", first)
        self.assertIn("NEW SESSION", fresh)
        self.assertIn("RULES: be brief", fresh, "a new session has not seen the rules")
        self.assertNotIn("NEW SESSION", same)

    def test_failed_injection_does_not_consume_the_first_trigger(self):
        self.wake(inject=lambda _prompt: False)
        prompts = self.wake()
        self.assertTrue(prompts)
        self.assertIn("NEW SESSION", prompts[0])

    def test_custom_prompt_also_carries_turn_discipline(self):
        self.wake()
        self.append(prompt="custom instruction")
        [prompt] = [p for p in self.wake() if "custom instruction" in p]
        self.assertIn("END YOUR TURN", prompt)


class McpInstructionsTests(unittest.TestCase):
    text = mcp_bridge._MCP_INSTRUCTIONS

    def test_chat_join_only_on_a_new_session(self):
        self.assertNotIn("chat_join when you start a session", self.text)
        for sentence in re.split(r"(?<=[.:])\s", self.text):
            if "chat_join" in sentence and "do not call" not in sentence.lower():
                self.assertIn("new session", sentence.lower(), sentence)

    def test_no_routine_rules_or_summary_reads(self):
        self.assertNotIn("At session start, call chat_rules", self.text)
        self.assertNotIn("chat_summary(action='read') at session start", self.text)

    def test_identity_rules_stated_once(self):
        self.assertEqual(self.text.count('"codex"'), 1)
        self.assertEqual(self.text.count("chat_claim(sender="), 2)  # reject + /resume
        self.assertNotIn("CRITICAL — Identity:", self.text)
        self.assertNotIn("Multi-instance support:", self.text)

    def test_turn_discipline_present(self):
        self.assertIn("end your turn", self.text.lower())
        self.assertIn("chat_react(msg_id=", self.text)
        self.assertIn("sender=", self.text.split("chat_react(")[1].split(")")[0])
        self.assertIn("@mention", self.text)


if __name__ == "__main__":
    unittest.main()
