"""T3: /newtask resets the loop guard and restarts each agent's CLI session,
keeping the wrapper (identity, token, proxy) and its queued wake-ups."""

import asyncio
import json
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import wrapper
from agents import AgentTrigger
from fastapi import WebSocketDisconnect
from router import Router
from store import MessageStore


class NewTaskCommandTests(unittest.TestCase):
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
        self.agents.request_restart = Mock()
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

    def _trip_guard(self):
        self._agent_message("claude", "@codex one")
        self._agent_message("codex", "@claude two")
        self.assertTrue(self.router.is_paused("work"))
        self.agents.trigger.reset_mock()

    def _restarted(self):
        return sorted(c.args[0] for c in self.agents.request_restart.call_args_list)

    def _system_texts(self):
        return [m["text"] for m in self.messages.get_recent(100) if m["sender"] == "system"]

    def _websocket(self, sender, text):
        events = [json.dumps({"type": "message", "text": text,
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

    def test_http_newtask_restarts_every_agent_and_clears_guard(self):
        self._trip_guard()
        self._agent_message("user", "/newtask auth refactor")
        self.assertEqual(self._restarted(), ["claude", "codex"])
        self.assertFalse(self.router.is_paused("work"))
        self.assertEqual(self.router.continue_routing("work"), [],
                         "blocked messages from the old task must not replay")
        self.agents.trigger.assert_not_awaited()
        self.assertTrue(any("auth refactor" in t for t in self._system_texts()))

    def test_newtask_clears_the_guard_in_every_channel(self):
        self._trip_guard()  # #work
        self._agent_message("claude", "@codex one", channel="general")
        self._agent_message("codex", "@claude two", channel="general")
        self.assertTrue(self.router.is_paused("general"))
        self._agent_message("user", "/newtask", channel="work")
        for channel in ("work", "general"):
            self.assertFalse(self.router.is_paused(channel), channel)
            self.assertEqual(self.router.continue_routing(channel), [], channel)

    def test_websocket_newtask_restarts_every_agent(self):
        self._trip_guard()
        self._websocket("user", "/newtask")
        self.assertEqual(self._restarted(), ["claude", "codex"])
        self.assertFalse(self.router.is_paused("work"))
        self.assertTrue(any("new task" in t.lower() for t in self._system_texts()))

    def test_agent_cannot_newtask(self):
        self._agent_message("codex", "/newtask")
        self._websocket("claude", "/newtask")
        self.agents.request_restart.assert_not_called()


class RequestRestartTests(unittest.TestCase):
    def test_restart_request_is_a_queue_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            AgentTrigger(Mock(), data_dir=tmp).request_restart("claude")
            lines = (Path(tmp) / "claude_queue.jsonl").read_text("utf-8").splitlines()
        self.assertEqual([json.loads(l)["type"] for l in lines], ["restart"])


class _StopWatcher(BaseException):
    pass


class WatcherRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = Path(self.tmp.name) / "claude_queue.jsonl"
        self.state = Path(self.tmp.name) / "claude.session.json"
        self.session = ["cli-1"]
        self.events = []

    def append(self, **entry):
        with self.queue.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")

    def run_poll(self, restart_fn, activity=None):
        def sleep(seconds):
            if seconds == 1:
                raise _StopWatcher()

        with (patch.object(wrapper.time, "sleep", side_effect=sleep),
              patch.object(wrapper, "_fetch_role", return_value=""),
              patch.object(wrapper, "_fetch_active_rules", return_value=None)):
            with self.assertRaises(_StopWatcher):
                wrapper._queue_watcher(
                    lambda: ("claude", self.queue),
                    lambda prompt: self.events.append(("inject", prompt)),
                    session_state_path=self.state,
                    get_session_fn=lambda: self.session[0],
                    restart_fn=restart_fn, get_activity_fn=activity)

    def restart(self):
        self.events.append(("restart", None))
        self.session[0] = "cli-2"

    def test_restart_runs_first_and_queued_wakes_go_to_the_new_session(self):
        self.append(channel="general", text="m")
        self.append(type="restart")
        self.append(channel="work", text="m")
        self.run_poll(self.restart)
        kinds = [kind for kind, _ in self.events]
        self.assertEqual(kinds, ["restart", "inject", "inject"])
        self.assertIn("read #general", self.events[1][1])
        self.assertIn("NEW SESSION", self.events[1][1])
        self.assertIn("read #work", self.events[2][1])
        self.assertFalse(self.queue.with_suffix(".processing").exists())

    def test_failed_restart_keeps_the_whole_queue(self):
        self.append(type="restart")
        self.append(channel="work", text="m")

        def fail():
            raise RuntimeError("CLI would not stop")

        self.run_poll(fail)
        self.assertEqual(self.events, [])
        kept = self.queue.with_suffix(".processing").read_text("utf-8")
        self.assertIn('"restart"', kept)
        self.assertIn('"work"', kept)

    def test_restart_waits_while_the_agent_is_busy(self):
        self.append(type="restart")
        self.run_poll(self.restart, activity=lambda: {"active": True, "waiting_approval": False})
        self.assertEqual(self.events, [])
        self.run_poll(self.restart, activity=lambda: {"active": False, "waiting_approval": False})
        self.assertEqual(self.events, [("restart", None)])

    def test_repeated_restart_requests_collapse(self):
        self.append(type="restart")
        self.append(type="restart")
        self.run_poll(self.restart)
        self.assertEqual(self.events, [("restart", None)])


class RestartCliTests(unittest.TestCase):
    def test_restart_kills_the_cli_and_waits_for_the_relaunch(self):
        pid = [100]
        event = threading.Event()
        killed = []

        def kill(old):
            killed.append(old)
            pid[0] = 200  # the run loop relaunches the CLI

        with patch.object(wrapper, "_post_deregister") as dereg, \
             patch.object(wrapper, "_register_instance") as reg:
            wrapper._restart_cli(pid, event, kill, sleep=lambda s: None, boot_grace=0)
            dereg.assert_not_called()  # identity and token stay with the live wrapper
            reg.assert_not_called()
        self.assertEqual(killed, [100])
        self.assertTrue(event.is_set(), "tells the run loop to relaunch even with --no-restart")

    def test_restart_fails_when_the_cli_never_comes_back(self):
        pid = [100]
        with self.assertRaises(RuntimeError):
            wrapper._restart_cli(pid, threading.Event(), lambda old: pid.__setitem__(0, None),
                                 sleep=lambda s: None, timeout=1, boot_grace=0)


class RunLoopRestartTests(unittest.TestCase):
    def test_windows_loop_relaunches_on_request_despite_no_restart(self):
        import wrapper_windows
        event = threading.Event()
        procs = []

        def popen(*args, **kwargs):
            proc = Mock(pid=len(procs) + 1, returncode=0)
            if not procs:
                proc.wait.side_effect = event.set  # first CLI is killed by a restart request
            procs.append(proc)
            return proc

        with patch.object(wrapper_windows, "enable_vt_mode"), \
             patch.object(wrapper_windows, "_vt_keepalive_thread"), \
             patch.object(wrapper_windows.subprocess, "Popen", side_effect=popen), \
             patch.object(wrapper_windows.time, "sleep"):
            wrapper_windows.run_agent("claude", [], ".", {}, None, "claude", True,
                                      lambda fn: None, restart_event=event)
        self.assertEqual(len(procs), 2)
        self.assertFalse(event.is_set())

    def test_unix_loop_relaunches_on_request_despite_no_restart(self):
        import wrapper_unix
        event = threading.Event()
        attaches = []

        def run(cmd, *args, **kwargs):
            if cmd[:2] == ["tmux", "attach-session"]:
                attaches.append(cmd)
                if len(attaches) == 1:
                    event.set()
            return Mock(returncode=0)

        with patch.object(wrapper_unix, "_check_tmux"), \
             patch.object(wrapper_unix, "_session_exists", return_value=False), \
             patch.object(wrapper_unix.subprocess, "run", side_effect=run), \
             patch.object(wrapper_unix.time, "sleep"):
            wrapper_unix.run_agent("claude", [], ".", {}, None, "claude", True,
                                   lambda fn: None, restart_event=event)
        self.assertEqual(len(attaches), 2)


if __name__ == "__main__":
    unittest.main()
