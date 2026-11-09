"""Leave notices follow each agent's channel across presence and identity changes."""
import ast
import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from launcher_supervisor import Launcher

# The web launcher otherwise reads preferences from the user's real data folder.
with patch.object(Launcher, '_load_preferences', return_value={}):
    import app

import mcp_bridge
from fastapi import WebSocketDisconnect
from registry import RuntimeRegistry
from store import MessageStore
from jobs import JobStore
from schedules import ScheduleStore
from session_store import SessionStore
from summaries import SummaryStore
from router import Router
from channel_names import valid_channel_name


class AgentChannelTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.registry = RuntimeRegistry(str(self.root / 'registry'))
        self.registry.seed({name: {'label': name, 'color': '#222222'}
                            for name in ('claude', 'codex')})
        self.messages = MessageStore(str(self.root / 'messages.jsonl'))
        self.degraded, self.online, self.active, self.posted = set(), set(), set(), set()
        self.pid_alive = Mock(return_value=False)
        router = Mock()
        router.get_targets.return_value = []
        router.is_paused.return_value = False
        for name, value in {
            'registry': self.registry, 'store': self.messages, 'router': router,
            'agents': Mock(), 'session_engine': None, '_event_loop': None,
            'config': {'server': {'data_dir': str(self.root)}},
            'room_settings': {'channels': ['general', 'alpha', 'beta'], 'username': 'Ben'},
            '_agent_last_channel': {}, '_last_active_channel': 'general',
            'ws_clients': set(), 'session_token': 'test-token', 'launcher_shutdown_token': '',
            'agent_hats': {}, 'rules': SimpleNamespace(list_all=lambda: []),
            'jobs': SimpleNamespace(list_all=lambda: [], rename_channel=Mock()),
            'schedules': SimpleNamespace(list_all=lambda: [], rename_channel=Mock()),
        }.items():
            self.stack.enter_context(patch.object(app, name, value))
        for name in ('broadcast', 'broadcast_typing', 'broadcast_status',
                     'broadcast_agents', 'broadcast_settings'):
            self.stack.enter_context(patch.object(app, name, AsyncMock()))
        # All bridge identity state and persistence are isolated. No cursor or role
        # files are read/written when the real migration/purge functions run.
        for name in ('_presence', '_activity', '_activity_ts', '_cursors', '_roles',
                     '_last_read_channel', '_last_read_job_id', '_empty_read_count', '_channel_aliases'):
            self.stack.enter_context(patch.object(mcp_bridge, name, {}))
        self.stack.enter_context(patch.object(mcp_bridge, '_renamed_from', set()))
        self.stack.enter_context(patch.object(mcp_bridge, 'registry', None))
        for name in ('_save_cursors', '_save_roles'):
            self.stack.enter_context(patch.object(mcp_bridge, name, Mock()))

    def register(self, name, pid=0):
        result = self.registry.register(name, pid=pid)
        mcp_bridge._presence[result['name']] = 1000.0
        return result

    def message(self, sender, channel, msg_type='chat'):
        message = self.messages.add(sender, 'Working here.', channel=channel, msg_type=msg_type)
        asyncio.run(app._handle_new_message(message))
        return message

    def sweep(self, now, fallback=None):
        return app._presence_iteration(
            now=now, registry=self.registry, store=self.messages, mcp_bridge=mcp_bridge,
            pid_alive_fn=self.pid_alive, degraded=self.degraded, known_online=self.online,
            known_active=self.active, posted_leave=self.posted, last_channel=fallback)

    def leaves(self):
        return [m for m in self.messages._messages if m['type'] == 'leave']

    def socket_events(self, *events):
        socket = Mock()
        socket.query_params = {'token': 'test-token'}
        socket.accept = AsyncMock()
        socket.send_text = AsyncMock()
        socket.receive_text = AsyncMock(side_effect=[
            *(json.dumps(event) for event in events), WebSocketDisconnect()])
        asyncio.run(app.websocket_endpoint(socket))
        self.assertEqual(socket.receive_text.await_count, len(events) + 1,
                         'WebSocket handler failed before processing all events')
        return socket

    @staticmethod
    def request(body=None):
        return SimpleNamespace(json=AsyncMock(return_value=body or {}), headers={},
                               query_params={}, client=SimpleNamespace(host='127.0.0.1'))

    def test_mcp_agents_leave_in_their_own_channels_once(self):
        for name, channel in [('claude', 'alpha'), ('codex', 'beta')]:
            self.register(name)
            self.message(name, channel)
        self.sweep(1005)
        self.sweep(1011)
        self.sweep(1015)
        self.assertCountEqual([(m['sender'], m['channel']) for m in self.leaves()],
                              [('claude', 'alpha'), ('codex', 'beta')])

    def test_crashed_wrappers_leave_in_their_own_channels_once(self):
        for name, channel in [('claude', 'alpha'), ('codex', 'beta')]:
            self.register(name, pid=1234)
            self.message(name, channel)
        self.sweep(1005)
        self.sweep(1011)
        self.assertEqual(self.leaves(), [])  # stale wrapper is initially degraded
        self.sweep(1091)
        self.sweep(1095)
        self.assertCountEqual([(m['sender'], m['channel']) for m in self.leaves()],
                              [('claude', 'alpha'), ('codex', 'beta')])
        self.assertEqual(self.registry.get_all_names(), [])

    def test_pure_mcp_timeout_does_not_repeat_existing_leave(self):
        self.register('claude')
        self.message('claude', 'alpha')
        self.sweep(1005)
        self.sweep(1011)
        self.sweep(1091)
        self.sweep(1095)
        self.assertEqual(len(self.leaves()), 1)
        self.assertEqual(self.leaves()[0]['channel'], 'alpha')
        self.assertIsNone(self.registry.get_instance('claude'))

    def test_recovery_allows_next_offline_transition_to_emit_again(self):
        self.register('claude')
        self.message('claude', 'alpha')
        self.sweep(1011)
        mcp_bridge._presence['claude'] = 1020
        self.sweep(1021)
        self.message('claude', 'beta')
        self.sweep(1031)
        self.assertEqual([m['channel'] for m in self.leaves()], ['alpha', 'beta'])

    def test_unregistered_presence_client_keeps_its_last_channel(self):
        self.message('external', 'alpha')
        mcp_bridge._presence['external'] = 1000
        self.sweep(1005)
        self.message('Ben', 'beta')
        self.sweep(1011)
        self.sweep(1015)
        self.assertEqual([(m['sender'], m['channel']) for m in self.leaves()],
                         [('external', 'alpha')])

    def test_unseen_agent_uses_valid_global_or_explicit_fallback(self):
        self.register('claude')
        self.register('codex')
        self.message('Ben', 'beta')
        self.sweep(1011)
        self.assertEqual({m['channel'] for m in self.leaves()}, {'beta'})
        self.assertEqual(app._agent_channel_for('unseen', 'alpha'), 'alpha')
        self.assertEqual(app._agent_channel_for('unseen', 'deleted'), 'general')

    def test_live_wrapper_stays_degraded_without_wrong_channel_leave(self):
        self.register('claude', pid=1234)
        self.message('claude', 'alpha')
        self.message('Ben', 'beta')
        self.pid_alive.return_value = True
        self.sweep(1005)
        self.sweep(1011)
        self.sweep(1091)
        self.sweep(1200)
        self.assertEqual(self.leaves(), [])
        self.assertIn('claude', self.degraded)
        self.assertTrue(self.registry.is_registered('claude'))
        self.assertEqual(app._agent_last_channel['claude'], 'alpha')

    def test_system_and_presence_messages_cannot_pollute_channel_memory(self):
        self.message('claude', 'alpha')
        for sender, kind in [('claude', 'system'), ('claude', 'leave'),
                             ('claude', 'join'), ('system', 'chat'), ('system', 'session_phase')]:
            with self.subTest(sender=sender, kind=kind):
                self.message(sender, 'beta', kind)
                self.assertEqual(app._agent_last_channel, {'claude': 'alpha'})
                self.assertEqual(app._last_active_channel, 'alpha')

    def test_pending_name_event_migrates_memory_and_history(self):
        self.register('claude')
        pending = self.register('claude')['name']
        self.message(pending, 'alpha')
        self.socket_events({'type': 'name_pending', 'name': pending, 'label': 'Planner'})
        self.assertNotIn(pending, app._agent_last_channel)
        self.assertEqual(app._agent_last_channel['planner'], 'alpha')
        self.assertEqual(self.messages._messages[0]['sender'], 'planner')
        self.assertTrue(self.registry.is_registered('planner'))

    def test_label_change_keeps_identity_and_its_channel(self):
        self.register('claude')
        self.message('claude', 'alpha')
        response = asyncio.run(app.rename_agent_label('claude', self.request({'label': 'Planner'})))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app._agent_last_channel, {'claude': 'alpha'})
        self.assertTrue(self.registry.is_registered('claude'))

    def test_channel_rename_and_delete_events_update_all_remembered_agents(self):
        self.message('claude', 'alpha')
        self.message('codex', 'alpha')
        self.socket_events({'type': 'channel_rename', 'old_name': 'alpha', 'new_name': 'review'})
        self.assertEqual(app._agent_last_channel, {'claude': 'review', 'codex': 'review'})
        self.assertEqual(app._last_active_channel, 'review')
        self.socket_events({'type': 'channel_delete', 'name': 'review'})
        self.assertEqual(app._agent_last_channel, {})
        self.assertEqual(app._last_active_channel, 'general')
        self.assertEqual(app._agent_channel_for('claude'), 'general')
        self.assertNotIn('review', app.room_settings['channels'])

    def test_chinese_names_create_rename_and_restore_related_state(self):
        summary = SummaryStore(str(self.root / 'summaries.json'))
        summary.write('alpha', '历史摘要', 'codex')
        for filename, records in (
            ('jobs.json', [{'id': 1, 'channel': 'alpha'}]),
            ('schedules.json', [{'id': 1, 'channel': 'alpha'}]),
            ('sessions.json', [{'id': 1, 'channel': 'alpha', 'state': 'active'}]),
        ):
            (self.root / filename).write_text(json.dumps(records), encoding='utf-8')
        job_store = JobStore(str(self.root / 'jobs.json'))
        schedule_store = ScheduleStore(str(self.root / 'schedules.json'))
        session_store = SessionStore(str(self.root / 'sessions.json'))
        routing = Router(['codex'])
        routing._get_ch('alpha')['paused'] = True
        self.stack.enter_context(patch.object(app, 'summaries', summary))
        self.stack.enter_context(patch.object(app, 'jobs', job_store))
        self.stack.enter_context(patch.object(app, 'schedules', schedule_store))
        self.stack.enter_context(patch.object(app, 'session_store', session_store))
        self.stack.enter_context(patch.object(app, 'router', routing))
        self.messages.add('user', '历史消息', channel='alpha')
        self.socket_events({'type': 'channel_create', 'name': '  中文 讨论  '},
                           {'type': 'channel_rename', 'old_name': 'alpha', 'new_name': '项目 一'})
        self.assertIn('中文 讨论', app.room_settings['channels'])
        self.assertIn('项目 一', app.room_settings['channels'])
        self.assertEqual(self.messages.get_recent(channel='项目 一')[0]['text'], '历史消息')
        self.assertEqual(SummaryStore(str(self.root / 'summaries.json')).get('项目 一')['text'], '历史摘要')
        self.assertEqual(JobStore(str(self.root / 'jobs.json')).list_all(channel='项目 一')[0]['id'], 1)
        self.assertEqual(ScheduleStore(str(self.root / 'schedules.json')).list_all()[0]['channel'], '项目 一')
        self.assertEqual(SessionStore(str(self.root / 'sessions.json')).get_active('项目 一')['id'], 1)
        self.assertTrue(routing.is_paused('项目 一'))

    def test_invalid_names_do_not_create_channels_or_disconnect(self):
        self.socket_events(*({'type': 'channel_create', 'name': name} for name in
                             ['../路径', 'a/b', 'a\\b', '测试\n名字', 'a' * 61, 42]),
                           {'type': 'channel_create', 'name': '有效 名称'})
        self.assertEqual(app.room_settings['channels'], ['general', 'alpha', 'beta', '有效 名称'])
        self.assertTrue(valid_channel_name('中文 项目_2'))
        self.assertTrue(valid_channel_name('中' * 60))
        self.assertFalse(valid_channel_name(' 中文'))

    def test_mcp_inflight_and_remembered_channels_follow_rename_delete_and_reuse(self):
        self.stack.enter_context(patch.object(mcp_bridge, 'store', self.messages))
        self.messages.add('user', '原消息', channel='alpha')
        mcp_bridge.chat_read(sender='codex', channel='alpha')
        self.socket_events({'type': 'channel_rename', 'old_name': 'alpha', 'new_name': '项目 一'})
        self.assertEqual(mcp_bridge._last_read_channel['codex'], '项目 一')
        mcp_bridge.chat_send('codex', '省略频道')
        mcp_bridge.chat_send('codex', '在途回复', channel='alpha')
        self.assertEqual([m['text'] for m in self.messages.get_recent(channel='项目 一')],
                         ['原消息', '省略频道', '在途回复'])
        self.assertIn('原消息', mcp_bridge.chat_resync('codex', channel='alpha'))
        self.socket_events({'type': 'channel_rename', 'old_name': '项目 一', 'new_name': '项目 二'})
        self.assertEqual(mcp_bridge.resolve_channel('alpha'), '项目 二')
        self.socket_events({'type': 'channel_create', 'name': 'alpha'})
        self.assertEqual(mcp_bridge.resolve_channel('alpha'), 'alpha')
        mcp_bridge.chat_send('codex', '新频道消息', channel='alpha')
        self.assertEqual(self.messages.get_recent(channel='alpha')[0]['text'], '新频道消息')
        socket = self.socket_events({'type': 'channel_delete', 'name': '项目 二'})
        self.assertEqual(mcp_bridge._last_read_channel['codex'], 'general')
        mcp_bridge.chat_send('codex', '删除后的回复', channel='项目 一')
        self.assertEqual(self.messages.get_recent(channel='general')[-1]['text'], '删除后的回复')
        events = [json.loads(call.args[0]) for call in socket.send_text.await_args_list]
        self.assertIn({'type': 'channel_deleted', 'name': '项目 二'}, events)

    def test_stale_memory_never_routes_to_deleted_channel(self):
        self.register('claude')
        app._agent_last_channel['claude'] = 'deleted'
        app._last_active_channel = 'deleted'
        self.sweep(1011)
        self.assertEqual(self.leaves()[0]['channel'], 'general')

    def test_register_slot_migration_preserves_channel(self):
        self.message('claude', 'alpha')
        result = {'name': 'claude-2', '_renamed_slot1': {'old': 'claude', 'new': 'claude-1'}}
        with patch.object(self.registry, 'register', return_value=result):
            response = asyncio.run(app.register_agent(self.request({'base': 'claude'})))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(app._agent_last_channel, {'claude-1': 'alpha'})
        self.assertEqual(self.messages._messages[0]['sender'], 'claude-1')

    def test_deregister_and_launcher_release_migrate_survivor_channel(self):
        for endpoint in ('deregister', 'launcher'):
            with self.subTest(endpoint=endpoint):
                app._agent_last_channel.clear()
                self.message('claude-1', 'alpha')
                result = {'name': 'claude-2', '_renamed_back': {'old': 'claude-1', 'new': 'claude'}}
                with patch.object(self.registry, 'deregister', return_value=result), \
                        patch.object(self.registry, 'is_agent_family', return_value=False):
                    if endpoint == 'deregister':
                        response = asyncio.run(app.deregister_agent('claude-2', self.request()))
                    else:
                        response = asyncio.run(app.launcher_release_agent('claude-2', self.request()))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(app._agent_last_channel, {'claude': 'alpha'})

    def claim(self, identity, target, sender=None):
        """Exercise real authentication/claim/migration, with an in-memory event loop."""
        context = self.claim_context(identity)
        broadcast = AsyncMock()

        async def run():
            with patch.object(app, '_event_loop', asyncio.get_running_loop()):
                result = mcp_bridge.chat_claim(sender or identity['name'], target, ctx=context)
                # run_coroutine_threadsafe first schedules callbacks, then tasks.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                return result

        with patch.object(mcp_bridge, 'registry', self.registry), \
                patch.object(mcp_bridge, 'store', self.messages), \
                patch.object(app, '_broadcast', broadcast):
            result = asyncio.run(run())
        events = [json.loads(call.args[0]) for call in broadcast.await_args_list]
        return result, events

    @staticmethod
    def claim_context(identity):
        return SimpleNamespace(request_context=SimpleNamespace(
            request=SimpleNamespace(headers={'authorization': 'Bearer ' + identity['token']})))

    def test_authenticated_chat_claim_migrates_channel_history_presence_and_event(self):
        identity = self.register('claude')
        message = self.message('claude', 'alpha')
        self.message('Ben', 'beta')
        mcp_bridge._activity['claude'] = True
        mcp_bridge._activity_ts['claude'] = 1000
        mcp_bridge._cursors['claude'] = {'alpha': message['id']}
        mcp_bridge._last_read_channel['claude'] = 'alpha'
        mcp_bridge._empty_read_count['claude'] = 2
        result, events = self.claim(identity, 'Planner')
        self.assertEqual(json.loads(result)['confirmed_name'], 'planner')
        self.assertEqual(app._agent_channel_for('planner'), 'alpha')
        self.assertNotIn('claude', app._agent_last_channel)
        self.assertEqual(self.messages.get_by_id(message['id'])['sender'], 'planner')
        self.assertEqual(set(mcp_bridge._presence), {'planner'})
        self.assertEqual(mcp_bridge._activity, {'planner': True})
        self.assertEqual(mcp_bridge._cursors, {'planner': {'alpha': message['id']}})
        self.assertEqual(mcp_bridge._last_read_channel, {'planner': 'alpha'})
        self.assertEqual(mcp_bridge._empty_read_count, {'planner': 2})
        self.assertIn('claude', mcp_bridge._renamed_from)
        self.assertNotIn('planner', mcp_bridge._renamed_from)
        self.assertEqual(events, [{'type': 'agent_renamed', 'old_name': 'claude', 'new_name': 'planner'}])
        with patch.object(mcp_bridge, 'registry', self.registry), \
                patch.object(mcp_bridge, 'store', self.messages):
            result = mcp_bridge.chat_send('claude', 'Reply after rename', ctx=self.claim_context(identity))
        self.assertFalse(result.startswith('Error:'), result)
        sent = self.messages._messages[-1]
        self.assertEqual((sent['sender'], sent['channel']), ('planner', 'alpha'))

    def test_chat_claim_keeps_last_read_job_for_implicit_reply(self):
        identity = self.register('claude')
        jobs = JobStore(str(self.root / 'jobs.json'))
        job = jobs.create('Review', 'Read this task', 'alpha', 'Ben')
        with patch.object(mcp_bridge, 'registry', self.registry), \
                patch.object(mcp_bridge, 'store', self.messages), \
                patch.object(mcp_bridge, 'jobs', jobs), patch.object(mcp_bridge, 'router', None):
            mcp_bridge.chat_read('claude', job_id=job['id'], ctx=self.claim_context(identity))
            self.claim(identity, 'Planner')
            self.assertEqual(mcp_bridge._last_read_job_id, {'planner': job['id']})
            result = mcp_bridge.chat_send('claude', 'Task reply', ctx=self.claim_context(identity))
        self.assertIn(f"Sent to job #{job['id']}", result)
        self.assertEqual([(m['sender'], m['text']) for m in jobs.get_messages(job['id'])],
                         [('planner', 'Task reply')])
        self.assertEqual(self.messages._messages, [])

    def test_chat_claim_old_alias_uses_actual_current_name_for_migration(self):
        identity = self.register('claude')
        message = self.message('claude', 'alpha')
        self.claim(identity, 'Planner')
        self.message('Ben', 'beta')
        result, events = self.claim(identity, 'Reviewer', sender='claude')
        self.assertEqual(json.loads(result)['confirmed_name'], 'reviewer')
        self.assertEqual(app._agent_channel_for('reviewer'), 'alpha')
        self.assertNotIn('planner', app._agent_last_channel)
        self.assertEqual(self.messages.get_by_id(message['id'])['sender'], 'reviewer')
        self.assertEqual(set(mcp_bridge._presence), {'reviewer'})
        self.assertEqual(events, [{'type': 'agent_renamed', 'old_name': 'planner', 'new_name': 'reviewer'}])

    def test_chat_claim_chinese_label_does_not_migrate_stable_identity(self):
        identity = self.register('claude')
        self.message('claude', 'alpha')
        with patch.object(mcp_bridge, 'migrate_identity', wraps=mcp_bridge.migrate_identity) as migrate:
            result, events = self.claim(identity, '规划助手')
        self.assertEqual(json.loads(result)['confirmed_name'], 'claude')
        self.assertEqual(json.loads(result)['label'], '规划助手')
        self.assertEqual(app._agent_last_channel, {'claude': 'alpha'})
        self.assertEqual(set(mcp_bridge._presence), {'claude'})
        self.assertEqual(events, [])
        migrate.assert_not_called()

    def test_failed_chat_claim_never_migrates_channel_history_or_emits_rename(self):
        identity = self.register('claude')
        self.register('codex')
        message = self.message('claude', 'alpha')
        with patch.object(mcp_bridge, 'migrate_identity', wraps=mcp_bridge.migrate_identity) as migrate:
            result, events = self.claim(identity, 'codex')
        self.assertTrue(result.startswith('Error:'))
        self.assertEqual(app._agent_last_channel, {'claude': 'alpha'})
        self.assertEqual(self.messages.get_by_id(message['id'])['sender'], 'claude')
        self.assertEqual(set(mcp_bridge._presence), {'claude', 'codex'})
        self.assertEqual(events, [])
        migrate.assert_not_called()

    def test_every_history_identity_migration_also_migrates_channel_memory(self):
        # Some legacy rename branches are intentionally unreachable under the
        # current label-only UI. Audit all call sites so re-enabling one later
        # cannot silently reintroduce stale channel memory.
        tree = ast.parse((ROOT / 'app.py').read_text('utf-8-sig'))
        checked = 0
        for owner in ast.walk(tree):
            for _, statements in ast.iter_fields(owner):
                if not isinstance(statements, list):
                    continue
                for index, statement in enumerate(statements):
                    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                        continue
                    call = statement.value
                    if not (isinstance(call.func, ast.Attribute) and call.func.attr == 'rename_sender'
                            and isinstance(call.func.value, ast.Name) and call.func.value.id == 'store'):
                        continue
                    checked += 1
                    following = statements[index + 1] if index + 1 < len(statements) else None
                    self.assertIsInstance(following, ast.Expr, f'migration missing at line {call.lineno}')
                    migration = following.value
                    self.assertIsInstance(migration, ast.Call)
                    self.assertEqual(ast.unparse(migration.func), '_migrate_agent_last_channel',
                                     f'channel migration missing at line {call.lineno}')
                    self.assertEqual([ast.dump(arg) for arg in migration.args],
                                     [ast.dump(arg) for arg in call.args])
        self.assertGreaterEqual(checked, 8)


if __name__ == '__main__':
    unittest.main()
