"""A choice is a question within a turn, not a completed turn or interruption."""
import asyncio
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from session_engine import SessionEngine
from session_store import SessionStore
from store import MessageStore


def setup_session(tmp_path, stack):
    sessions = SessionStore(str(tmp_path / 'session_runs.json'))
    sessions.save_custom_template({'id': 'test', 'name': 'Test', 'roles': ['asker', 'finisher'],
        'phases': [{'name': 'Work', 'participants': ['asker', 'finisher'], 'prompt': 'Test', 'is_output': True}]})
    messages = MessageStore(str(tmp_path / 'messages.jsonl'))
    registry = Mock()
    registry.is_registered.side_effect = lambda name: name in ('claude', 'codex')
    trigger = Mock()
    engine = SessionEngine(sessions, messages, trigger, registry)
    timers = []
    def timer(delay, callback, args):
        result = Mock()
        result.start.side_effect = lambda: timers.append(lambda: callback(*args))
        return result
    stack.enter_context(patch('session_engine.threading.Timer', timer))
    stack.enter_context(patch.object(app, 'store', messages))
    stack.enter_context(patch.object(app, 'room_settings', {'username': 'Ben'}))
    stack.enter_context(patch.object(app, 'ws_clients', set()))
    engine.start_session('test', 'general', {'asker': 'claude', 'finisher': 'codex'}, 'Ben')
    return sessions, messages, engine, trigger, timers


def ask(messages, sender='claude', channel='general'):
    return messages.add(sender, 'Which?', msg_type='decision', channel=channel,
                        metadata={'choices': ['Blue', 'Green'], 'resolved': False})


def answer(question):
    request = Mock()
    async def body(): return {'choice': 'Blue'}
    request.json = body
    return asyncio.run(app.resolve_decision(question['id'], request))


class SessionChoiceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp_path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.fixture = setup_session(self.tmp_path, self.stack)

    def test_choice_keeps_turn_then_response_advances_once(self):
        sessions, messages, engine, trigger, timers = self.fixture
        question = ask(messages)
        assert sessions.get(1)['choice_message_id'] == question['id']
        assert timers == []
        messages.add('claude', 'Please pick one.')
        assert timers == []
        assert answer(question)['ok']
        assert sessions.get(1)['state'] == 'waiting'
        assert sessions.get(1)['choice_message_id'] is None
        assert sessions.get(1)['current_turn'] == 0
        assert trigger.trigger_sync.call_count == 1  # reply is routed by app's normal @mention path
        messages.add('claude', 'I will use Blue.')
        messages.add('claude', 'Ready for review.')
        for timer in list(timers): timer()
        assert sessions.get(1)['current_turn'] == 1
        assert trigger.trigger_sync.call_count == 2
        assert trigger.trigger_sync.call_args.args == ('codex',)
        messages.add('codex', 'Done.')
        timers[-1]()
        assert sessions.get(1)['state'] == 'complete'

    def test_question_blocks_already_queued_advance(self):
        sessions, messages, engine, trigger, timers = self.fixture
        messages.add('claude', 'I need a choice.')
        ask(messages)
        timers[0]()
        assert sessions.get(1)['current_turn'] == 0
        assert trigger.trigger_sync.call_count == 1

    def test_typed_interruption_can_resume_without_clicking_choice(self):
        sessions, messages, engine, trigger, timers = self.fixture
        ask(messages)
        messages.add('Ben', '@claude Use red instead.')
        assert sessions.get(1)['state'] == 'paused'
        assert sessions.get(1)['choice_message_id'] is None
        messages.add('claude', 'Using red.')
        timers[0]()
        assert sessions.get(1)['current_turn'] == 1

    def test_duplicate_answer_is_rejected_without_second_reply(self):
        sessions, messages, engine, trigger, timers = self.fixture
        question = ask(messages)
        answer(question)
        result = answer(question)
        assert result.status_code == 400
        replies = [m for m in messages._messages if m.get('reply_to') == question['id']]
        assert len(replies) == 1
        assert sessions.get(1)['state'] == 'waiting'

    def test_old_choice_cannot_answer_current_question(self):
        sessions, messages, engine, trigger, timers = self.fixture
        old_question = ask(messages)
        current_question = ask(messages)
        answer(old_question)
        assert sessions.get(1)['state'] == 'waiting'
        assert sessions.get(1)['choice_message_id'] == current_question['id']
        messages.add('claude', 'Responding to the old answer.')
        assert timers == []
        assert sessions.get(1)['current_turn'] == 0
        assert trigger.trigger_sync.call_count == 1

    def test_question_in_other_channel_or_from_other_participant_is_ignored(self):
        sessions, messages, engine, trigger, timers = self.fixture
        ask(messages, channel='elsewhere')
        ask(messages, sender='codex')
        assert sessions.get(1).get('choice_message_id') is None
        assert timers == []

    def test_pending_choice_survives_restart(self):
        sessions, messages, engine, trigger, timers = self.fixture
        question = ask(messages)
        restored = SessionStore(str(self.tmp_path / 'session_runs.json'))
        engine._store = restored
        engine.resume_active_sessions()
        assert restored.get(1)['choice_message_id'] == question['id']
        assert trigger.trigger_sync.call_count == 1
        answer(question)
        assert restored.get(1)['state'] == 'waiting'
        assert restored.get(1)['choice_message_id'] is None

    def test_end_session_cancels_queued_response(self):
        sessions, messages, engine, trigger, timers = self.fixture
        messages.add('claude', 'Done.')
        engine.end_session(1)
        timers[0]()
        assert sessions.get(1)['state'] == 'interrupted'
        assert trigger.trigger_sync.call_count == 1

    def test_fast_choice_answer_does_not_complete_turn_with_pre_question_message(self):
        sessions, messages, engine, trigger, timers = self.fixture
        messages.add('claude', 'I need a choice.')
        question = ask(messages)
        answer(question)
        timers[0]()
        assert sessions.get(1)['current_turn'] == 0
        assert trigger.trigger_sync.call_count == 1
        messages.add('claude', 'Using Blue.')
        timers[-1]()
        assert sessions.get(1)['current_turn'] == 1


if __name__ == '__main__':
    unittest.main()
