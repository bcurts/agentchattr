import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app
import mcp_bridge
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

    def test_legacy_defaults_to_known_and_unknown_survives_rename(self):
        self.assertTrue(mcp_bridge.is_activity_known('old'))
        mcp_bridge.set_activity_known('old', False)
        with patch.object(mcp_bridge, '_save_cursors'):
            mcp_bridge.migrate_identity('old', 'new')
        self.assertFalse(mcp_bridge.is_activity_known('new'))
        with patch.object(mcp_bridge, '_save_cursors'):
            mcp_bridge.purge_identity('new')
        self.assertTrue(mcp_bridge.is_activity_known('new'))
