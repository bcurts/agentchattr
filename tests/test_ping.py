import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import app
from router import Router
from mcp_bridge import _serialize_messages
import json

class PingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._pending_pings.clear()
    def tearDown(self):
        app._pending_pings.clear()
    def test_targets_and_instruction(self):
        router = Router(['c1', 'c2'], default_mention='all', online_checker=lambda: {'c1', 'c2'})
        self.assertEqual(router.get_targets('Derek', '/ping'), [])
        self.assertEqual(router.get_targets('Derek', '/ping @unknown'), [])
        self.assertEqual(router.get_targets('Derek', '/ping @c1'), ['c1'])
        self.assertEqual(set(router.get_targets('Derek', '/ping @all')), {'c1', 'c2'})
        msg = {'id': 1, 'sender': 'Derek', 'text': '/ping @c1', 'type': 'chat', 'time': 'now', 'channel': 'ops'}
        self.assertIn('reply_to', json.loads(_serialize_messages([msg]))[0]['command_instruction'])
    async def test_only_matching_reply_suppresses_each_timeout(self):
        app._pending_pings[1] = ('ops', {'c1', 'c2'})
        for msg in [
            {'reply_to': 2, 'sender': 'c1', 'text': 'OK', 'channel': 'ops'},
            {'reply_to': 1, 'sender': 'c1', 'text': 'OK', 'channel': 'other'},
            {'reply_to': 1, 'sender': 'c2', 'text': 'not OK', 'channel': 'ops'},
            {'reply_to': 1, 'sender': 'stranger', 'text': 'OK', 'channel': 'ops'},
        ]:
            app._record_ping_reply(msg)
        self.assertEqual(app._pending_pings[1][1], {'c1', 'c2'})
        app._record_ping_reply({'reply_to': 1, 'sender': 'c1', 'text': 'OK', 'channel': 'ops'})
        with patch.object(app, 'store') as store:
            await app._ping_timeout(1, 0)
        store.add.assert_called_once_with('system', 'c2 is not available (no reply within 30 seconds).', msg_type='system', channel='ops', reply_to=1)
        self.assertNotIn(1, app._pending_pings)
        app._record_ping_reply({'reply_to': 1, 'sender': 'c2', 'text': 'OK', 'channel': 'ops'})
    async def test_command_dispatch_and_reply_use_the_live_handler(self):
        router = Router(['c1', 'c2'], default_mention='all', online_checker=lambda: {'c1', 'c2'})
        agents = MagicMock()
        agents.is_available.return_value = True
        agents.trigger = AsyncMock()
        with patch.object(app, 'router', router), patch.object(app, 'registry', None), \
             patch.object(app, 'config', {'agents': {'c1': {}, 'c2': {}}}), \
             patch.object(app, 'agents', agents), patch.object(app, 'session_engine', None), \
             patch.object(app, '_unarchive_for_activity', return_value=False), \
             patch.object(app, 'broadcast', AsyncMock()), patch.object(app, 'store') as store, \
             patch('mcp_bridge.is_online', return_value=True):
            await app._handle_new_message({'id': 42, 'sender': 'Derek', 'text': '/ping @all', 'type': 'chat', 'channel': 'ops'})
            self.assertEqual(app._pending_pings[42][1], {'c1', 'c2'})
            self.assertEqual(agents.trigger.await_count, 2)
            await app._handle_new_message({'id': 43, 'sender': 'c1', 'text': 'OK', 'type': 'chat', 'channel': 'ops', 'reply_to': 42})
            self.assertEqual(app._pending_pings[42][1], {'c2'})
            await app._ping_timeout(42, 0)
            store.add.assert_called_once_with('system', 'c2 is not available (no reply within 30 seconds).', msg_type='system', channel='ops', reply_to=42)

    async def test_all_responses_produce_no_timeout(self):
        app._pending_pings[1] = ('ops', {'c1'})
        app._record_ping_reply({'reply_to': 1, 'sender': 'c1', 'text': 'OK', 'channel': 'ops'})
        with patch.object(app, 'store') as store:
            await app._ping_timeout(1, 0)
        store.add.assert_not_called()
