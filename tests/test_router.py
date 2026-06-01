import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry import RuntimeRegistry
from router import Router

try:
    import mcp_bridge
except ModuleNotFoundError:
    mcp_bridge = None


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

    def test_all_matches_mixed_case_online_names(self):
        router = Router(
            ["planner", "kimijs"],
            default_mention="none",
            online_checker=lambda: {"Planner"},
        )

        self.assertEqual(set(router.parse_mentions("@all check")), {"planner"})


class RegistryResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_resolve_mixed_case_claim_from_lowercase_mention(self):
        registry = RuntimeRegistry(self.tmp.name)
        registry.seed({"codex": {"label": "Codex", "color": "#10a37f"}})
        registry.register("codex")

        result = registry.claim("codex", "Planner")

        self.assertIsInstance(result, dict)
        self.assertEqual(result["name"], "planner")
        self.assertEqual(result["label"], "Planner")
        self.assertEqual(registry.resolve_to_instances("planner"), ["planner"])
        self.assertEqual(registry.resolve_to_instances("Planner"), ["planner"])

    def test_resolve_base_family_returns_mixed_case_claim(self):
        registry = RuntimeRegistry(self.tmp.name)
        registry.seed({"codex": {"label": "Codex", "color": "#10a37f"}})
        registry.register("codex")
        registry.claim("codex", "Planner")

        self.assertEqual(registry.resolve_to_instances("codex"), ["planner"])

    def test_pure_chinese_claim_updates_label_only(self):
        registry = RuntimeRegistry(self.tmp.name)
        registry.seed({"codex": {"label": "Codex", "color": "#10a37f"}})
        registry.register("codex")
        label = "\u4e3b\u7a0b\u5e8f\u5458"

        result = registry.claim("codex", label)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["name"], "codex")
        self.assertEqual(result["label"], label)

    def test_polluted_rename_file_is_cleaned_to_single_canonical_mapping(self):
        renames = Path(self.tmp.name) / "renames.json"
        renames.write_text('{"codex":"Planner","planner":"codex"}', encoding="utf-8")
        registry = RuntimeRegistry(self.tmp.name)
        registry.seed({"codex": {"label": "Codex", "color": "#10a37f"}})

        self.assertEqual(registry.resolve_name("codex"), "planner")
        self.assertEqual(registry.resolve_name("planner"), "planner")
        self.assertEqual(renames.read_text(encoding="utf-8"), '{"codex": "planner"}')


@unittest.skipIf(mcp_bridge is None, "mcp package not installed")
class PresenceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed({"codex": {"label": "Codex", "color": "#10a37f"}})
        self.registry.register("codex")
        self.registry.claim("codex", "Planner")
        self.old_registry = mcp_bridge.registry
        self.old_presence = dict(mcp_bridge._presence)
        mcp_bridge.registry = self.registry
        mcp_bridge._presence.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        mcp_bridge.registry = self.old_registry
        mcp_bridge._presence.clear()
        mcp_bridge._presence.update(self.old_presence)

    def test_touch_presence_resolves_case_and_base_aliases(self):
        mcp_bridge._touch_presence("Planner")

        self.assertTrue(mcp_bridge.is_online("planner"))
        self.assertTrue(mcp_bridge.is_online("Planner"))
        self.assertTrue(mcp_bridge.is_online("codex"))

    def test_presence_timeout_remains_short_status_window(self):
        mcp_bridge._touch_presence("planner")
        mcp_bridge._presence["planner"] = time.time() - (mcp_bridge.PRESENCE_TIMEOUT + 1)

        self.assertFalse(mcp_bridge.is_online("planner"))


if __name__ == "__main__":
    unittest.main()
