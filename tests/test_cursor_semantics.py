"""Read-cursor semantics for chat_send / chat_read (trial finding F1).

An agent's own send must never skip messages that others posted after its
last read, and a backlog larger than `limit` must be delivered across
several reads instead of dropping the oldest unread.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_bridge
from store import MessageStore


def _ids(result: str) -> list[int]:
    """Message ids from a chat_read result (ignores trailing hint lines)."""
    if not result or result.startswith("No new messages"):
        return []
    return [m["id"] for m in json.loads(result.splitlines()[0])]


class CursorSemanticsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = {
            "store": mcp_bridge.store,
            "registry": mcp_bridge.registry,
            "jobs": mcp_bridge.jobs,
            "file": mcp_bridge._CURSORS_FILE,
        }
        self._saved_cursors = json.loads(json.dumps(mcp_bridge._cursors))
        mcp_bridge.store = MessageStore(str(Path(self._tmp) / "messages.jsonl"))
        mcp_bridge.registry = None
        mcp_bridge.jobs = None
        mcp_bridge._CURSORS_FILE = None
        mcp_bridge._cursors.clear()
        self.store = mcp_bridge.store

    def tearDown(self):
        mcp_bridge.store = self._saved["store"]
        mcp_bridge.registry = self._saved["registry"]
        mcp_bridge.jobs = self._saved["jobs"]
        mcp_bridge._CURSORS_FILE = self._saved["file"]
        mcp_bridge._cursors.clear()
        mcp_bridge._cursors.update(self._saved_cursors)
        mcp_bridge._empty_read_count.pop("agent", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _post(self, sender: str, text: str, channel: str = "general") -> int:
        return self.store.add(sender, text, channel=channel)["id"]

    def _read(self, **kw) -> str:
        kw.setdefault("sender", "agent")
        kw.setdefault("channel", "general")
        return mcp_bridge.chat_read(**kw)

    def _send(self, text: str, channel: str = "general") -> int:
        result = mcp_bridge.chat_send("agent", text, choices=[], channel=channel)
        return int(result.split("id=")[1].rstrip(")"))

    # --- own send interleaving with others' messages ---

    def test_send_does_not_skip_messages_that_arrived_since_last_read(self):
        self._post("user", "hello")
        self._read()
        m1 = self._post("user", "@agent first")
        m2 = self._post("user", "@agent second")
        own = self._send("working on it")
        self.assertEqual(_ids(self._read()), [m1, m2, own])
        self.assertEqual(_ids(self._read()), [])

    def test_send_with_nothing_unread_advances_cursor_past_own_message(self):
        self._post("user", "hello")
        self._read()
        self._send("reply")
        self.assertEqual(_ids(self._read()), [])

    def test_send_without_existing_cursor_does_not_create_one(self):
        m0 = self._post("user", "@agent before you ever read")
        own = self._send("hi")
        self.assertEqual(_ids(self._read()), [m0, own])

    def test_message_in_other_channel_does_not_block_advance(self):
        self._post("user", "hello")
        self._read()
        self._post("user", "elsewhere", channel="random")
        self._send("reply")
        self.assertEqual(_ids(self._read()), [])

    def test_message_arriving_right_after_send_is_delivered(self):
        self._post("user", "hello")
        self._read()
        self._send("reply")
        later = self._post("user", "@agent follow-up")
        self.assertEqual(_ids(self._read()), [later])

    def test_send_does_not_skip_unread_for_all_channels_cursor(self):
        self._post("user", "hello")
        self._read(channel="")
        m1 = self._post("user", "@agent in general")
        m2 = self._post("user", "@agent in random", channel="random")
        own = self._send("reply")
        self.assertEqual(_ids(self._read(channel="")), [m1, m2, own])

    def test_propose_job_does_not_skip_unread(self):
        self._post("user", "hello")
        self._read()
        m1 = self._post("user", "@agent please")
        mcp_bridge.chat_propose_job("agent", "Do the thing", channel="general")
        self.assertIn(m1, _ids(self._read()))

    # --- backlog larger than limit ---

    def test_backlog_larger_than_limit_is_paged_oldest_first(self):
        self._post("user", "hello")
        self._read()
        backlog = [self._post("user", f"msg {i}") for i in range(25)]
        first = self._read(limit=20)
        self.assertEqual(_ids(first), backlog[:20])
        self.assertIn("5 more unread", first)
        second = self._read(limit=20)
        self.assertEqual(_ids(second), backlog[20:])
        self.assertNotIn("more unread", second)

    def test_first_read_without_cursor_returns_most_recent(self):
        ids = [self._post("user", f"msg {i}") for i in range(25)]
        self.assertEqual(_ids(self._read(limit=20)), ids[-20:])

    def test_explicit_older_since_id_does_not_rewind_cursor(self):
        self._post("user", "zero")  # since_id=0 means "not given"
        early = self._post("user", "one")
        self._post("user", "two")
        self._read()
        self.assertEqual(len(_ids(self._read(since_id=early))), 1)
        self.assertEqual(_ids(self._read()), [])

    def test_explicit_since_id_pages_follow_advertised_instruction(self):
        self._post("user", "zero")
        start = self._post("user", "start")
        rest = [self._post("user", f"msg {i}") for i in range(25)]
        self._read()  # default cursor now at the newest message
        seen = []
        since = start
        for _ in range(5):
            result = self._read(since_id=since, limit=10)
            page = _ids(result)
            seen.extend(page)
            if "more unread" not in result:
                break
            self.assertIn(f"since_id={page[-1]}", result)
            since = page[-1]
        self.assertEqual(seen, rest)
        # Explicit paging must not have rewound the default cursor.
        self.assertEqual(_ids(self._read()), [])

    def test_since_id_paging_without_sender(self):
        self._post("user", "zero")
        start = self._post("user", "start")
        rest = [self._post("user", f"msg {i}") for i in range(12)]
        first = self._read(sender="", since_id=start, limit=10)
        self.assertEqual(_ids(first), rest[:10])
        self.assertIn(f"since_id={rest[9]}", first)

    def test_non_positive_limit_is_rejected(self):
        self._post("user", "hello")
        self._read()
        self._post("user", "unread")
        for bad in (0, -5):
            self.assertTrue(self._read(limit=bad).startswith("Error: limit"))
        # The rejected reads did not consume the unread message.
        self.assertEqual(len(_ids(self._read())), 1)


if __name__ == "__main__":
    unittest.main()
