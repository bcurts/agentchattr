"""Both /continue paths must re-deliver blocked messages in their own channel (F6)."""
import asyncio
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from fastapi import WebSocketDisconnect
from router import Router
from store import MessageStore


class ContinueReplayTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.messages = MessageStore(str(root / "messages.jsonl"))
        self.router = Router(["claude", "codex"], default_mention="none", max_hops=1)
        self.agents = Mock()
        self.agents.get_status.return_value = {}
        self.agents.is_available.return_value = True
        self.agents.trigger = AsyncMock()
        empty = Mock()
        empty.list_all.return_value = []
        for name, value in {"store": self.messages, "router": self.router,
                            "agents": self.agents, "registry": None,
                            "session_engine": None, "rules": empty, "jobs": empty,
                            "schedules": empty, "agent_hats": {},
                            "config": {"agents": {"claude": {}, "codex": {}}},
                            "room_settings": {"channels": ["general", "work"],
                                              "history_limit": "all"},
                            "session_token": "test", "ws_clients": set()}.items():
            self.stack.enter_context(patch.object(app, name, value))
        self.stack.enter_context(patch("mcp_bridge.is_online", return_value=True))

    def _agent_message(self, sender, text, channel="work"):
        msg = self.messages.add(sender, text, channel=channel)
        asyncio.run(app._handle_new_message(msg))
        return msg

    def _trip_guard(self, channel="work"):
        self._agent_message("claude", "@codex one", channel)
        blocked = self._agent_message("codex", "@claude two", channel)
        self.assertTrue(self.router.is_paused(channel))
        self.agents.trigger.reset_mock()
        return blocked

    def _triggered(self):
        return [(c.args[0], c.kwargs.get("channel")) for c in self.agents.trigger.await_args_list]

    def test_http_continue_replays_blocked_message(self):
        self._trip_guard()
        count_before = len(self.messages.get_recent(100))
        continue_msg = self.messages.add("user", "/continue", channel="work")
        asyncio.run(app._handle_new_message(continue_msg))
        self.assertEqual(self._triggered(), [("claude", "work")])
        self.assertFalse(self.router.is_paused("work"))
        # Replay re-triggers; it must not copy the blocked chat message.
        added = self.messages.get_recent(100)[count_before:]
        self.assertTrue(all(m["sender"] in ("user", "system") for m in added))

    def _prompts(self):
        return [c.kwargs.get("prompt", "") for c in self.agents.trigger.await_args_list]

    def test_replay_prompt_says_a_human_resumed_the_chain(self):
        self._trip_guard()
        self._continue_http()
        [prompt] = self._prompts()
        # The agent will see the earlier 'Loop guard' system message in history;
        # the wake prompt must tell it that pause is over.
        self.assertIn("#work", prompt)
        self.assertIn("human", prompt.lower())
        self.assertIn("resumed", prompt.lower())
        self.assertIn("no longer paused", prompt.lower())

    def test_ordinary_mention_keeps_default_prompt(self):
        self._agent_message("user", "@claude hi")
        self.assertEqual(self._prompts(), [""])

    def _websocket_continue(self, sender):
        events = [json.dumps({"type": "message", "text": "/continue",
                              "sender": sender, "channel": "work"})]

        class Socket:
            query_params = {"token": "test"}
            async def accept(self): pass
            async def send_text(self, raw): pass
            async def receive_text(self):
                if events:
                    return events.pop(0)
                raise WebSocketDisconnect()

        asyncio.run(app.websocket_endpoint(Socket()))

    def test_websocket_continue_resumes_its_own_channel_and_replays(self):
        self._trip_guard("work")
        self._websocket_continue("user")
        self.assertFalse(self.router.is_paused("work"))
        self.assertEqual(self._triggered(), [("claude", "work")])

    def test_websocket_continue_from_agent_is_refused(self):
        blocked = self._trip_guard("work")
        self._websocket_continue("codex")
        self.assertTrue(self.router.is_paused("work"))
        self.agents.trigger.assert_not_awaited()
        self.assertEqual([b["msg_id"] for b in self.router.continue_routing("work")],
                         [blocked["id"]])

    # --- job-thread mentions blocked by the guard ---

    def _use_job(self, job_id=7):
        jobs = Mock()
        jobs.get.return_value = {"id": job_id, "channel": "work"}
        jobs.add_message.side_effect = lambda jid, sender, text, **kw: {
            "id": 500, "sender": sender, "text": text}
        self.stack.enter_context(patch.object(app, "jobs", jobs))
        return jobs

    def _continue_http(self):
        continue_msg = self.messages.add("user", "/continue", channel="work")
        asyncio.run(app._handle_new_message(continue_msg))

    def _job_triggers(self):
        return [(c.args[0], c.kwargs.get("channel"), c.kwargs.get("job_id"))
                for c in self.agents.trigger.await_args_list if c.kwargs.get("job_id")]

    def test_job_message_blocked_via_http_is_replayed_into_job(self):
        self._use_job()
        self._agent_message("claude", "@codex one")
        self.agents.trigger.reset_mock()
        request = Mock()
        request.json = AsyncMock(return_value={"text": "@claude job ping", "sender": "codex"})
        asyncio.run(app.post_job_message(7, request))  # trips the guard
        self.assertTrue(self.router.is_paused("work"))
        self.agents.trigger.assert_not_awaited()
        self._continue_http()
        self.assertEqual(self._job_triggers(), [("claude", "work", 7)])
        [prompt] = self._prompts()
        self.assertIn("job_id=7", prompt)
        self.assertIn("no longer paused", prompt.lower())

    def test_job_message_blocked_via_mcp_is_replayed_into_job(self):
        import mcp_bridge
        jobs = self._use_job()
        for name, value in {"jobs": jobs, "router": self.router, "agents": self.agents,
                            "registry": None}.items():
            self.stack.enter_context(patch.object(mcp_bridge, name, value))
        self._agent_message("claude", "@codex one")
        self.agents.trigger.reset_mock()
        mcp_bridge.chat_send("codex", "@claude job ping", choices=[], job_id=7)  # trips the guard
        self.assertTrue(self.router.is_paused("work"))
        self.agents.trigger_sync.assert_not_called()
        self._continue_http()
        self.assertEqual(self._job_triggers(), [("claude", "work", 7)])


if __name__ == "__main__":
    unittest.main()
