"""Tests for MessageStore reactions."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from store import MessageStore  # noqa: E402


def test_toggle_reaction_adds_and_removes_sender(tmp_path):
    store = MessageStore(str(tmp_path / "chat.jsonl"))
    msg = store.add("user", "hello")

    reactions = store.toggle_reaction(msg["id"], "👍", "alice")
    assert reactions == {"👍": ["alice"]}

    reactions = store.toggle_reaction(msg["id"], "👍", "alice")
    assert reactions == {}


def test_get_recent_includes_reactions(tmp_path):
    store = MessageStore(str(tmp_path / "chat.jsonl"))
    msg = store.add("user", "hello")
    store.toggle_reaction(msg["id"], "🎉", "alice")
    store.toggle_reaction(msg["id"], "🎉", "bob")

    recent = store.get_recent(1)

    assert recent[0]["reactions"] == {"🎉": ["alice", "bob"]}
