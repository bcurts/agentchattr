"""Quick test for typing indicator wiring changes."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TypingIndicatorTests(unittest.IsolatedAsyncioTestCase):

    async def test_heartbeat_broadcasts_typing_on_activity_change(self):
        """When wrapper reports active state change, broadcast_typing should be called."""
        # Import app and patch globals
        import app as _app

        with patch.object(_app, "broadcast_status", new_callable=AsyncMock) as mock_status, \
             patch.object(_app, "broadcast_typing", new_callable=AsyncMock) as mock_typing, \
             patch.object(_app, "registry", create=True) as mock_registry:

            mock_registry.is_agent_family.return_value = False
            mock_registry.resolve_name.return_value = "kimi-3"

            # Patch mcp_bridge presence/activity
            import mcp_bridge
            orig_presence = mcp_bridge._presence.get("kimi-3")
            orig_activity = mcp_bridge._activity.get("kimi-3")
            try:
                mcp_bridge._presence["kimi-3"] = 999999
                mcp_bridge._activity["kimi-3"] = False

                # Build a mock request with active=True
                class MockRequest:
                    headers = {}
                    async def json(self):
                        return {"active": True}
                    @property
                    def client(self):
                        return MagicMock(host="127.0.0.1")

                await _app.heartbeat("kimi-3", MockRequest())

                mock_status.assert_awaited_once()
                mock_typing.assert_awaited_once_with("kimi-3", True)
            finally:
                if orig_presence is not None:
                    mcp_bridge._presence["kimi-3"] = orig_presence
                else:
                    mcp_bridge._presence.pop("kimi-3", None)
                if orig_activity is not None:
                    mcp_bridge._activity["kimi-3"] = orig_activity
                else:
                    mcp_bridge._activity.pop("kimi-3", None)

    async def test_message_broadcast_clears_typing_for_agents(self):
        """When an agent message is broadcast, typing should be cleared."""
        import app as _app

        with patch.object(_app, "broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch.object(_app, "broadcast_typing", new_callable=AsyncMock) as mock_typing, \
             patch.object(_app, "registry", create=True) as mock_registry, \
             patch.object(_app, "router", create=True) as mock_router:

            mock_registry.get_all_names.return_value = ["kimi-3"]
            mock_registry.is_registered.return_value = True
            mock_router.is_paused.return_value = False
            _app.config = {"agents": {}}

            msg = {
                "sender": "kimi-3",
                "text": "Hello",
                "type": "chat",
                "channel": "general",
            }

            await _app._handle_new_message(msg)

            mock_broadcast.assert_awaited_once()
            mock_typing.assert_awaited_once_with("kimi-3", False)

    async def test_human_message_does_not_clear_typing(self):
        """Human messages should not trigger typing clear."""
        import app as _app

        with patch.object(_app, "broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch.object(_app, "broadcast_typing", new_callable=AsyncMock) as mock_typing, \
             patch.object(_app, "registry", create=True) as mock_registry, \
             patch.object(_app, "router", create=True) as mock_router:

            mock_registry.get_all_names.return_value = ["kimi-3"]
            mock_router.is_paused.return_value = False
            _app.config = {"agents": {}}

            msg = {
                "sender": "user",
                "text": "Hello",
                "type": "chat",
                "channel": "general",
            }

            await _app._handle_new_message(msg)

            mock_broadcast.assert_awaited_once()
            mock_typing.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
