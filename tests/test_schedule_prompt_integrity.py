"""Accepted scheduled instructions must reach readers without losing their tail."""

import asyncio
import datetime
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from schedules import ScheduleStore
from store import MessageStore


class PromptPersistenceTests(unittest.TestCase):
    def test_prompt_survives_create_reload_and_due_at_and_beyond_old_boundary(self):
        prompts = ["x" * length for length in (499, 500, 501, 1911)]
        prompts.append("Keep café, 東京 and 🙂 intact.\n" * 80 + "FINAL: do not deploy.")
        for prompt in prompts:
            with self.subTest(length=len(prompt)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "schedules.json"
                with patch("time.time", return_value=2000000000):
                    store = ScheduleStore(str(path))
                    created = store.create(
                        prompt=" \n" + prompt + "\n ", targets=["codex"],
                        interval_seconds=60,
                    )
                self.assertEqual(created["prompt"], prompt)
                self.assertEqual(json.loads(path.read_text("utf-8"))[0]["prompt"], prompt)
                restored = ScheduleStore(str(path))
                self.assertEqual(restored.get(created["id"])["prompt"], prompt)
                with patch("time.time", return_value=2000000060):
                    self.assertEqual(restored.run_due()[0]["prompt"], prompt)


class PromptDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # configure() registers the real runner. Capture its target instead of
        # starting any background thread, and restore every global it replaces.
        for name in (
            "store", "rules", "summaries", "jobs", "schedules", "router", "agents",
            "registry", "session_store", "session_engine", "config", "_event_loop",
            "_last_active_channel",
        ):
            self.stack.enter_context(patch.object(app, name, getattr(app, name)))
        self.stack.enter_context(patch.object(app, "_event_loop", None))
        self.stack.enter_context(patch.object(app, "_agent_last_channel", {}))
        with patch.object(app, "_install_security_middleware"), \
             patch.object(app, "_load_settings"), patch.object(app, "_load_hats"), \
             patch.object(app.threading, "Thread") as threads:
            app.configure({"server": {"data_dir": str(self.root)},
                           "agents": {"codex": {}}})
        self.runner = next(
            call.kwargs["target"] for call in threads.call_args_list
            if call.kwargs["target"].__name__ == "_schedule_runner"
        )
        self.socket = Mock(send_text=AsyncMock())
        self.stack.enter_context(patch.object(app, "ws_clients", {self.socket}))
        self.stack.enter_context(patch.object(app, "agents", Mock(
            is_available=Mock(return_value=True), trigger=AsyncMock(),
        )))
        self.stack.enter_context(patch("mcp_bridge.is_online", return_value=True))

    def _assert_delivery(self, **mode):
        prompt = "Preserve the whole instruction: café 東京 🙂\n" * 80
        prompt += "FINAL CONSTRAINT: no push, restart or deployment."
        now = 2000000000
        payload = {"prompt": prompt, "targets": ["codex"], "channel": "work",
                   "created_by": "Max", **mode}
        request = Mock(json=AsyncMock(return_value=payload))
        with patch("time.time", return_value=now):
            response = asyncio.run(app.create_schedule(request))
        self.assertEqual(response.status_code, 200)
        created = json.loads(response.body)
        # Restart-equivalent reload before the runner reads the accepted record.
        app.schedules = ScheduleStore(str(self.root / "schedules.json"))
        due_at = created["next_run"]

        async def deliver():
            app._event_loop = asyncio.get_running_loop()
            # A second sleep stops the infinite loop after exactly one tick.
            # sleep is outside the runner's catch-all, so this cannot look like
            # a swallowed delivery error or leave a background worker running.
            with patch("time.time", return_value=due_at), \
                 patch("time.sleep", side_effect=[None, StopIteration]):
                with self.assertRaises(StopIteration):
                    self.runner()
            await asyncio.sleep(0)  # run the real store callback and routing task

        asyncio.run(deliver())
        expected = "@codex " + prompt
        messages = MessageStore(str(self.root / "agentchattr_log.jsonl")).get_recent(10)
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["text"], expected)
        self.assertEqual(message["sender"], "Max")
        self.assertEqual(message["channel"], "work")
        frames = [json.loads(call.args[0]) for call in self.socket.send_text.await_args_list]
        self.assertEqual([frame["data"] for frame in frames if frame["type"] == "message"],
                         [message])
        app.agents.trigger.assert_awaited_once_with(
            "codex", message="Max: " + expected, channel="work", prompt="",
        )
        restored = ScheduleStore(str(self.root / "schedules.json"))
        if mode.get("one_shot"):
            self.assertEqual(restored.list_all(), [])
        else:
            recurring = restored.get(created["id"])
            self.assertEqual(recurring["prompt"], prompt)
            self.assertGreater(recurring["next_run"], due_at)

    def test_relative_one_shot_delivers_full_prompt(self):
        self._assert_delivery(spec="in 2h", one_shot=True, send_at=2000007200)

    def test_calendar_one_shot_delivers_full_prompt(self):
        # The current browser resolves a calendar selection to an epoch too.
        self._assert_delivery(spec="daily at 09:00", one_shot=True, send_at=2000086400)

    def test_legacy_calendar_date_delivers_full_prompt(self):
        future = datetime.datetime.fromtimestamp(2000086400)
        self._assert_delivery(spec=f"daily at {future:%H:%M}", one_shot=True,
                              send_at_date=future.date().isoformat())

    def test_interval_recurring_delivers_and_retains_full_prompt(self):
        self._assert_delivery(spec="every 2h")

    def test_daily_recurring_delivers_and_retains_full_prompt(self):
        self._assert_delivery(spec="daily at 09:00")


if __name__ == "__main__":
    unittest.main()
