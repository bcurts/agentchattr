"""Reactions stay outside agent reads and long reads remain recoverable."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import mcp_bridge
from store import MessageStore


class ReactionAndReadLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))
        self.saved = (mcp_bridge.store, mcp_bridge.registry, mcp_bridge.config,
                      mcp_bridge.router, mcp_bridge._CURSORS_FILE,
                      dict(mcp_bridge._cursors))
        mcp_bridge.store = self.store
        mcp_bridge.registry = None
        mcp_bridge.config = None
        mcp_bridge.router = None
        mcp_bridge._CURSORS_FILE = None
        mcp_bridge._cursors.clear()

    def tearDown(self):
        (mcp_bridge.store, mcp_bridge.registry, mcp_bridge.config,
         mcp_bridge.router, mcp_bridge._CURSORS_FILE, cursors) = self.saved
        mcp_bridge._cursors.clear()
        mcp_bridge._cursors.update(cursors)
        self.tmp.cleanup()

    def test_react_persists_deduplicates_and_does_not_change_read(self):
        msg = self.store.add("user", "hello", channel="general")
        before = mcp_bridge.chat_read(sender="codex", channel="general")
        changes = []
        self.store.on_reaction(lambda mid, reactions: changes.append((mid, reactions)))
        self.assertIn("Reacted", mcp_bridge.chat_react(msg["id"], "✅", "codex"))
        self.assertIn("Reacted", mcp_bridge.chat_react(msg["id"], "✅", "codex"))
        self.assertEqual(self.store.get_by_id(msg["id"])["metadata"]["reactions"], {"✅": ["codex"]})
        self.assertEqual(len(changes), 1)
        self.assertEqual(json.loads(mcp_bridge.chat_read(sender="", channel="general")), json.loads(before))
        reloaded = MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))
        self.assertEqual(reloaded.get_by_id(msg["id"])["metadata"]["reactions"], {"✅": ["codex"]})

    def test_react_never_routes_or_adds_a_message(self):
        msg = self.store.add("user", "hello")
        mcp_bridge.router = Mock()
        before = len(self.store.get_recent())
        self.assertIn("Reacted", mcp_bridge.chat_react(msg["id"], "✅", "codex"))
        self.assertEqual(len(self.store.get_recent()), before)
        mcp_bridge.router.assert_not_called()
        self.assertFalse(list(Path(self.tmp.name).glob("*_queue.jsonl")))

    def test_long_text_truncates_and_full_read_recovers_it(self):
        text = "x" * 2010
        msg = self.store.add("user", text)
        short = json.loads(mcp_bridge.chat_read(sender="", channel="general"))[0]
        self.assertTrue(short["text"].startswith("x" * 2000))
        self.assertIn("10 more chars", short["text"])
        self.assertIn(f"since_id={msg['id'] - 1}", short["text"])
        full = json.loads(mcp_bridge.chat_read(sender="", channel="general",
                                               since_id=msg["id"] - 1, limit=1, full=True))[0]
        self.assertEqual(full["text"], text)

    def test_short_text_unchanged_and_limit_configurable(self):
        self.store.add("user", "hello")
        self.assertEqual(json.loads(mcp_bridge.chat_read(sender="", channel="general"))[0]["text"], "hello")
        mcp_bridge.config = {"chat": {"max_read_chars": 3}}
        clipped = json.loads(mcp_bridge.chat_read(sender="", channel="general"))[0]["text"]
        self.assertTrue(clipped.startswith("hel"))
        self.assertIn("2 more chars", clipped)


if __name__ == "__main__":
    unittest.main()
