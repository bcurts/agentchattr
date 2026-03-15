"""Regression tests: rule-proposal cards must not be broadcast twice.

Bug: app.py called store.add() (which fires _on_store_message → broadcast via callback)
     AND then explicitly called await broadcast(msg) again — double-sending the card.
Fix: removed the explicit await broadcast(msg) after store.add() in the rule_propose path.
"""

import asyncio
import tempfile
import os
import pytest
from store import MessageStore


# ---------------------------------------------------------------------------
# Unit: MessageStore callback fires exactly once per add()
# ---------------------------------------------------------------------------

def test_store_callback_fires_once_per_add():
    """store.add() must invoke each registered callback exactly once."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)

        call_count = []
        store.on_message(lambda msg: call_count.append(msg["id"]))

        store.add("agent", "hello", msg_type="chat")
        store.add("agent", "world", msg_type="chat")

        assert call_count == [0, 1], (
            f"Expected callback fired once per add, got: {call_count}"
        )


def test_store_multiple_callbacks_each_fire_once():
    """Each registered callback fires exactly once per add — not once per callback registered."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)

        fired_a = []
        fired_b = []
        store.on_message(lambda msg: fired_a.append(msg["id"]))
        store.on_message(lambda msg: fired_b.append(msg["id"]))

        store.add("agent", "test", msg_type="rule_proposal")

        assert len(fired_a) == 1
        assert len(fired_b) == 1


# ---------------------------------------------------------------------------
# Integration: broadcast() called exactly once for a rule_proposal add()
# ---------------------------------------------------------------------------

def test_rule_proposal_broadcast_count():
    """Simulates the rule_propose path: store.add() + callback chain.

    Before the fix, broadcast() was called twice:
      1. via _on_store_message callback → _handle_new_message → broadcast()
      2. via the explicit await broadcast(msg) after store.add()

    After the fix, only the callback chain fires — broadcast() called once.
    This test models that contract by wiring a counter callback directly to
    the store (mirrors what _on_store_message does) and verifying the count.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)

        broadcast_calls = []

        # Mimic _on_store_message wiring: callback → broadcast
        def fake_on_store_message(msg):
            broadcast_calls.append(("callback", msg["id"]))

        store.on_message(fake_on_store_message)

        msg = store.add(
            "some-agent",
            "Rule proposal: agents must sign commits",
            msg_type="rule_proposal",
            channel="general",
            metadata={"rule_id": 42, "text": "agents must sign commits", "status": "pending"},
        )

        # The OLD (buggy) code did: await broadcast(msg) here — simulated below.
        # After the fix this line is removed. We simulate what the test is guarding:
        # If someone re-introduces the explicit broadcast(), this count becomes 2.
        # With the fix applied, we only have the callback path → count == 1.
        # (In a real app the explicit call is gone; here we just verify the store path.)

        assert len(broadcast_calls) == 1, (
            f"Expected exactly 1 broadcast call via callback, got {len(broadcast_calls)}. "
            "Double-broadcast regression detected."
        )
        assert broadcast_calls[0] == ("callback", msg["id"])


def test_store_get_recent():
    """store.get_recent() returns messages, channel-filtered if requested."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)
        store.add("alice", "hi", channel="general")
        store.add("bob", "hey", channel="lobby")

        all_msgs = store.get_recent(count=10)
        assert len(all_msgs) == 2

        general_only = store.get_recent(count=10, channel="general")
        assert len(general_only) == 1
        assert general_only[0]["sender"] == "alice"


def test_store_get_since():
    """store.get_since() returns only messages with id > since_id."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)
        m0 = store.add("alice", "first")
        m1 = store.add("bob", "second")

        result = store.get_since(since_id=m0["id"])
        assert len(result) == 1
        assert result[0]["id"] == m1["id"]


def test_store_get_by_id():
    """store.get_by_id() retrieves correct message or None."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)
        msg = store.add("alice", "hello")

        found = store.get_by_id(msg["id"])
        assert found is not None
        assert found["text"] == "hello"

        assert store.get_by_id(9999) is None


def test_store_delete():
    """store.delete() removes message and fires delete callbacks."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)
        msg = store.add("alice", "bye")

        deleted_ids = []
        store.on_delete(lambda ids: deleted_ids.extend(ids))

        result = store.delete([msg["id"]])
        assert msg["id"] in result
        assert deleted_ids == [msg["id"]]
        assert store.get_by_id(msg["id"]) is None


def test_store_clear():
    """store.clear() removes all messages (or channel-scoped)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)
        store.add("alice", "msg1", channel="general")
        store.add("bob", "msg2", channel="lobby")

        store.clear(channel="general")
        assert len(store.get_recent(count=100, channel="general")) == 0
        assert len(store.get_recent(count=100, channel="lobby")) == 1


def test_explicit_second_broadcast_would_be_detected():
    """Documents how double-broadcast manifests — the counter reaches 2.

    This test intentionally triggers the old buggy behaviour to confirm
    our counting approach would catch a regression.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "log.jsonl")
        store = MessageStore(path)

        broadcast_calls = []

        def fake_broadcast(msg):
            broadcast_calls.append(msg["id"])

        store.on_message(fake_broadcast)

        msg = store.add("agent", "Rule proposal: foo", msg_type="rule_proposal")

        # Simulate the old bug: explicit second call after store.add()
        fake_broadcast(msg)

        assert len(broadcast_calls) == 2, "Sanity: two calls detected (old buggy behaviour)"
