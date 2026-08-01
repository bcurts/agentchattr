import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
from rules import MAX_ACTIVE_RULES, RuleStore
from store import MessageStore


class FakeRequest:
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class RuleProposalResolutionTests(unittest.TestCase):
    """Resolving a rule proposal must fail loudly when the RuleStore rejects
    the action (active limit reached, rule already gone) instead of stamping
    the message as resolved anyway."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.store = MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))
        self.rules = RuleStore(str(Path(self.tmp.name) / "rules.json"))

        app.store = self.store
        app.rules = self.rules

    def _propose(self, text="keep replies short"):
        rule = self.rules.propose(text, "claude")
        msg = self.store.add(
            "claude",
            text,
            msg_type="rule_proposal",
            metadata={"rule_id": rule["id"], "status": "pending", "text": text},
        )
        return rule, msg

    def test_activate_at_active_limit_returns_409_and_keeps_pending(self):
        for i in range(MAX_ACTIVE_RULES):
            filler = self.rules.propose(f"rule {i}", "claude")
            self.rules.activate(filler["id"])
        rule, msg = self._propose()

        resp = asyncio.run(
            app.resolve_rule_proposal(msg["id"], FakeRequest(body={"action": "activate"}))
        )

        self.assertEqual(resp.status_code, 409)
        current = self.store.get_by_id(msg["id"])
        self.assertEqual(current["metadata"]["status"], "pending")
        self.assertEqual(self.rules.get(rule["id"])["status"], "pending")

    def test_activate_unknown_rule_returns_409_and_keeps_pending(self):
        rule, msg = self._propose()
        self.rules.delete(rule["id"])

        resp = asyncio.run(
            app.resolve_rule_proposal(msg["id"], FakeRequest(body={"action": "activate"}))
        )

        self.assertEqual(resp.status_code, 409)
        current = self.store.get_by_id(msg["id"])
        self.assertEqual(current["metadata"]["status"], "pending")

    def test_activate_success_marks_message_activated(self):
        rule, msg = self._propose()

        result = asyncio.run(
            app.resolve_rule_proposal(msg["id"], FakeRequest(body={"action": "activate"}))
        )

        self.assertEqual(result["metadata"]["status"], "activated")
        current = self.store.get_by_id(msg["id"])
        self.assertEqual(current["metadata"]["status"], "activated")
        self.assertEqual(self.rules.get(rule["id"])["status"], "active")

    def test_demote_unknown_rule_returns_409_and_keeps_proposal(self):
        rule, msg = self._propose()
        self.rules.delete(rule["id"])

        resp = asyncio.run(app.demote_rule_proposal(msg["id"]))

        self.assertEqual(resp.status_code, 409)
        current = self.store.get_by_id(msg["id"])
        self.assertEqual(current["type"], "rule_proposal")
        self.assertEqual(current["metadata"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
