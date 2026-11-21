import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app
import mcp_bridge
import wrapper
from agents import AgentTrigger


class ActivityStatusTests(unittest.TestCase):
    def setUp(self):
        mcp_bridge._activity.clear()
        mcp_bridge._activity_known.clear()
        mcp_bridge._presence.clear()
        mcp_bridge._needs_input_ts.clear()
        self.addCleanup(mcp_bridge._activity.clear)
        self.addCleanup(mcp_bridge._activity_known.clear)
        self.addCleanup(mcp_bridge._presence.clear)
        self.addCleanup(mcp_bridge._needs_input_ts.clear)

    def test_unknown_heartbeat_is_not_coerced_to_idle(self):
        async def body():
            return {'active': None, 'activity_known': False, 'needs_input': False}
        request = SimpleNamespace(headers={}, json=body)
        with patch.object(app, 'registry', None), patch.object(app, '_resolve_authenticated_agent', return_value=None), \
                patch.object(app, '_extract_agent_token', return_value=None), \
                patch.object(app, 'broadcast_status', new=AsyncMock()):
            response = asyncio.run(app.heartbeat('example', request))
        self.assertEqual(response['ok'], True)
        self.assertFalse(mcp_bridge.is_activity_known('example'))
        registry = SimpleNamespace(get_all=lambda: {'example': {'label': 'Example', 'color': '#888'}})
        status = AgentTrigger(registry).get_status()['example']
        self.assertTrue(status['available'])
        self.assertFalse(status['activity_known'])
        self.assertIsNone(status['busy'])
        self.assertNotIn('example', mcp_bridge._activity)

    def test_pty_registration_is_unknown_before_any_heartbeat(self):
        async def body():
            return {'base': 'example', 'activity_known': False}
        registry = SimpleNamespace(register=lambda base, label: {'name': 'example', 'state': 'active'})
        with patch.object(app, 'registry', registry), patch.object(app, '_event_loop', None):
            response = asyncio.run(app.register_agent(SimpleNamespace(json=body)))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mcp_bridge.is_online('example'))
        self.assertFalse(mcp_bridge.is_activity_known('example'))

    def test_pty_wrapper_requests_unknown_on_registration_and_legacy_does_not(self):
        response = SimpleNamespace(read=lambda: b'{}')
        # A real context manager mock retains the Request for inspection.
        with patch('urllib.request.urlopen') as open_url:
            open_url.return_value.__enter__.return_value = response
            with patch.dict('os.environ', {'TERMGRID_BRIDGE_PORT': '1234'}, clear=True):
                wrapper._register_instance(8200, 'codex')
            self.assertIs(json.loads(open_url.call_args.args[0].data)['activity_known'], False)
            with patch.dict('os.environ', {}, clear=True):
                wrapper._register_instance(8200, 'codex')
            self.assertNotIn('activity_known', json.loads(open_url.call_args.args[0].data))

    def test_legacy_defaults_to_known_and_unknown_survives_rename(self):
        self.assertTrue(mcp_bridge.is_activity_known('old'))
        mcp_bridge.set_activity_known('old', False)
        with patch.object(mcp_bridge, '_save_cursors'):
            mcp_bridge.migrate_identity('old', 'new')
        self.assertFalse(mcp_bridge.is_activity_known('new'))
        with patch.object(mcp_bridge, '_save_cursors'):
            mcp_bridge.purge_identity('new')
        self.assertTrue(mcp_bridge.is_activity_known('new'))
