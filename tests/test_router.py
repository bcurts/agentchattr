import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from router import Router


class RouterMentionTests(unittest.TestCase):
    def test_hyphenated_agent_name_is_parsed_as_full_mention(self):
        router = Router(["telegram-bridge"], default_mention="none")

        self.assertEqual(
            set(router.parse_mentions("please ask @telegram-bridge to check")),
            {"telegram-bridge"},
        )

    def test_shorter_agent_name_does_not_match_prefix_of_hyphenated_unknown(self):
        router = Router(["telegram"], default_mention="none")

        self.assertEqual(router.parse_mentions("@telegram-bridge check"), [])
        self.assertEqual(router.get_targets("ben", "@telegram-bridge check"), [])

    def test_longest_hyphenated_name_wins_when_prefix_agent_also_exists(self):
        router = Router(["telegram", "telegram-bridge"], default_mention="none")

        self.assertEqual(
            set(router.parse_mentions("@telegram-bridge check")),
            {"telegram-bridge"},
        )

    def test_unknown_exact_handle_still_does_not_route(self):
        router = Router(["telegram-bridge"], default_mention="none")

        self.assertEqual(router.parse_mentions("@telegram-bot check"), [])
        self.assertEqual(router.get_targets("ben", "@telegram-bot check"), [])


if __name__ == "__main__":
    unittest.main()


class RouterLoopGuardReplayTests(unittest.TestCase):
    """/continue must re-deliver messages the loop guard blocked (F6)."""

    def setUp(self):
        self.router = Router(["claude", "codex"], default_mention="none", max_hops=2)

    def _agent_says(self, sender, text, msg_id, channel="general"):
        origin = {"sender": sender, "text": text, "msg_id": msg_id}
        return self.router.get_targets(sender, text, channel, origin=origin)

    def _trip_guard(self, channel="general"):
        self._agent_says("claude", "@codex one", 1, channel)
        self._agent_says("codex", "@claude two", 2, channel)
        self.assertEqual(self._agent_says("claude", "@codex three", 3, channel), [])
        self.assertTrue(self.router.is_paused(channel))

    def test_message_that_trips_guard_is_returned_by_continue(self):
        self._trip_guard()
        blocked = self.router.continue_routing("general")
        self.assertEqual([b["msg_id"] for b in blocked], [3])
        self.assertFalse(self.router.is_paused("general"))

    def test_messages_blocked_while_paused_are_kept_in_order_without_cap(self):
        self._trip_guard()
        for i in range(4, 14):
            self._agent_says("codex", f"@claude more {i}", i)
        blocked = self.router.continue_routing("general")
        self.assertEqual([b["msg_id"] for b in blocked], list(range(3, 14)))
        self.assertEqual(self.router.continue_routing("general"), [])

    def test_blocked_message_without_mentions_is_not_kept(self):
        self._trip_guard()
        self._agent_says("codex", "no mention here", 4)
        blocked = self.router.continue_routing("general")
        self.assertEqual([b["msg_id"] for b in blocked], [3])

    def test_human_message_clears_blocked_list(self):
        self._trip_guard()
        self.router.get_targets("user", "@claude new direction", "general")
        self.assertEqual(self.router.continue_routing("general"), [])

    def test_blocked_list_is_per_channel(self):
        self._trip_guard("work")
        self.assertEqual(self.router.continue_routing("general"), [])
        self.assertEqual([b["msg_id"] for b in self.router.continue_routing("work")], [3])

    def test_replayed_message_routes_after_continue(self):
        self._trip_guard()
        [entry] = self.router.continue_routing("general")
        self.assertEqual(
            self.router.get_targets(entry["sender"], entry["text"], "general", origin=entry),
            ["codex"],
        )
