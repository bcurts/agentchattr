"""Rules beyond the old caps persist, and proposal cards reflect actual results."""
import asyncio
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from rules import RuleStore
from store import MessageStore


def proposal(stores):
    rules, messages = stores
    rule = rules.propose('Example rule', 'claude')
    return rule, messages.add('claude', 'Rule proposal', msg_type='rule_proposal',
        metadata={'rule_id': rule['id'], 'text': rule['text'], 'status': 'pending'})


def resolve(message, action):
    request = Mock()
    async def body(): return {'action': action}
    request.json = body
    return asyncio.run(app.resolve_rule_proposal(message['id'], request))


class RuleProposalTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp_path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        rules = RuleStore(str(self.tmp_path / 'rules.json'))
        messages = MessageStore(str(self.tmp_path / 'messages.jsonl'))
        self.stack.enter_context(patch.object(app, 'rules', rules))
        self.stack.enter_context(patch.object(app, 'store', messages))
        self.stack.enter_context(patch.object(app, 'ws_clients', set()))
        self.stores = rules, messages

    def test_more_than_fifty_rules_activate_and_survive_reload(self):
        rules, messages = self.stores
        for i in range(55):
            rule = rules.propose(f'Rule {i}', 'Ben')
            assert rules.make_draft(rule['id'])['status'] == 'draft'
            assert rules.activate(rule['id'])['status'] == 'active'
        restored = RuleStore(str(self.tmp_path / 'rules.json'))
        assert len(restored.list_all()) == 55
        assert restored.active_list() == rules.active_list()
        assert restored.epoch == 55

    def test_proposal_activates_with_ten_already_active(self):
        rules, messages = self.stores
        for i in range(10):
            rules.activate(rules.propose(f'Rule {i}', 'Ben')['id'])
        rule, message = proposal(self.stores)
        result = resolve(message, 'activate')
        assert result['metadata']['status'] == 'activated'
        assert rules.get(rule['id'])['status'] == 'active'
        assert len(rules.active_list()['rules']) == 11

    def test_missing_rule_cannot_be_marked_successful(self):
        rules, messages = self.stores
        for action in ('activate', 'draft'):
            with self.subTest(action=action):
                rule, message = proposal(self.stores)
                rules.delete(rule['id'])
                result = resolve(message, action)
                assert result.status_code == 409
                assert messages.get_by_id(message['id'])['metadata']['status'] == 'pending'

    def test_obsolete_proposal_can_still_be_dismissed(self):
        rules, messages = self.stores
        for action in ('dismiss', 'demote'):
            with self.subTest(action=action):
                rule, message = proposal(self.stores)
                rules.delete(rule['id'])
                if action == 'dismiss':
                    assert resolve(message, action)['metadata']['status'] == 'dismissed'
                else:
                    result = asyncio.run(app.demote_rule_proposal(message['id']))
                    assert result['type'] == 'chat'
                    assert result['metadata'] == {}

    def test_agent_proposal_can_be_drafted_then_activated(self):
        rules, messages = self.stores
        rule, message = proposal(self.stores)
        assert resolve(message, 'draft')['metadata']['status'] == 'drafted'
        assert rules.get(rule['id'])['status'] == 'draft'
        assert rules.activate(rule['id'])['status'] == 'active'


if __name__ == '__main__':
    unittest.main()
