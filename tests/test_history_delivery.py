"""Saved history must stay distinguishable from live chat and presence updates."""
import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from fastapi import WebSocketDisconnect
from store import MessageStore


class HistoryDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.messages = MessageStore(str(root / 'messages.jsonl'))
        empty = Mock()
        empty.list_all.return_value = []
        agents = Mock()
        agents.get_status.return_value = {}
        router = Mock()
        router.is_paused.return_value = False
        self.settings = {'channels': ['general', 'work'], 'history_limit': 'all'}
        for name, value in {'store': self.messages, 'rules': empty, 'jobs': empty,
                            'schedules': empty, 'registry': None, 'config': {},
                            'agent_hats': {}, 'agents': agents, 'router': router,
                            'room_settings': self.settings, 'session_token': 'test',
                            'ws_clients': set()}.items():
            self.stack.enter_context(patch.object(app, name, value))

    def read_connection(self, interrupt=False):
        frames = []
        class Socket:
            query_params = {'token': 'test'}
            interrupted = False
            async def accept(self): pass
            async def receive_text(self): raise WebSocketDisconnect()
            async def send_text(socket, raw):
                event = json.loads(raw)
                frames.append(event)
                if interrupt and event['type'] == 'history' and not socket.interrupted:
                    socket.interrupted = True
                    # A normal presence update and new message arrive mid-replay.
                    await app.broadcast_status()
                    live = self.messages.add('Ben', 'Live message', channel='work')
                    await app.broadcast(live)
        asyncio.run(app.websocket_endpoint(Socket()))
        self.assertFalse(app.ws_clients)
        return frames

    def test_live_message_and_status_do_not_change_history_boundary(self):
        saved = [self.messages.add('Ben', f'Message {i}', channel='work' if i % 2 else 'general',
                                  timestamp=100 + i, _bulk=True) for i in range(205)]
        frames = self.read_connection(interrupt=True)
        batches = [f['messages'] for f in frames if f['type'] == 'history']
        self.assertEqual([len(b) for b in batches], [100, 100, 5])
        self.assertEqual([m for batch in batches for m in batch], saved)
        end = next(i for i, f in enumerate(frames) if f['type'] == 'history_complete')
        self.assertTrue(any(f['type'] == 'status' for f in frames[:end]))
        live = [f['data'] for f in frames if f['type'] == 'message']
        self.assertEqual([m['text'] for m in live], ['Live message'])
        self.assertEqual(sum(f['type'] == 'history_complete' for f in frames), 1)
        self.assertEqual(len(self.messages.get_recent(1000)), 206)

    def test_empty_history_still_completes(self):
        frames = self.read_connection()
        self.assertFalse(any(f['type'] == 'history' for f in frames))
        self.assertEqual(sum(f['type'] == 'history_complete' for f in frames), 1)

    def test_history_setting_is_applied_per_channel_without_deleting_messages(self):
        self.settings['history_limit'] = '3'
        for i in range(12):
            self.messages.add('Ben', f'Message {i}', channel='work' if i % 2 else 'general',
                              timestamp=100 + i, _bulk=True)
        frames = self.read_connection()
        saved = [m for f in frames if f['type'] == 'history' for m in f['messages']]
        self.assertEqual([m['id'] for m in saved], list(range(6, 12)))
        self.assertEqual(len(self.messages.get_recent(1000)), 12)


if __name__ == '__main__':
    unittest.main()
