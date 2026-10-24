"""An approval prompt must remain visible as a distinct agent state."""

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
import mcp_bridge
import wrapper
from agents import AgentTrigger
from registry import RuntimeRegistry
from store import MessageStore

if sys.platform == "win32":
    import wrapper_windows


CLAUDE_PROMPT = (
    "Bash command\nThis command requires approval\n"
    "Do you want to proceed?\n> 1. Yes\n  2. Yes, and don't ask again\n  3. No"
)
CODEX_PROMPT = (
    "Would you like to run the following command?\n"
    "> 1. Yes, proceed\n  2. No, and tell Codex what to do differently"
)
CLAUDE_GLYPH_PROMPT = CLAUDE_PROMPT.replace("> 1. Yes", "❯ 1. Yes")


@unittest.skipUnless(sys.platform == "win32", "Windows console screen test")
class WindowsApprovalCheckerTests(unittest.TestCase):
    def test_static_prompt_stays_waiting_then_clears(self):
        checker = wrapper_windows.get_activity_checker([123], agent_name="claude")
        screens = [CLAUDE_PROMPT] * 7 + ["Task complete"] * 6
        snapshots = [(s.encode("utf-16-le"), s) for s in screens]
        with patch.object(wrapper_windows, "_read_console_snapshot", side_effect=snapshots):
            reports = [wrapper._build_activity_report(checker) for _ in screens]
        self.assertFalse(reports[0]["waiting_approval"])
        self.assertTrue(all(r["waiting_approval"] for r in reports[1:7]))
        self.assertTrue(all(not r["active"] for r in reports[1:7]))
        self.assertTrue(reports[7]["waiting_approval"], "one changed frame must not clear wait")
        self.assertFalse(reports[-1]["waiting_approval"])

    def test_known_codex_prompt_and_plain_prose(self):
        self.assertTrue(wrapper_windows._approval_prompt_visible(CODEX_PROMPT))
        self.assertTrue(wrapper_windows._approval_prompt_visible(CLAUDE_GLYPH_PROMPT))
        self.assertFalse(wrapper_windows._approval_prompt_visible(
            "The docs say: Do you want to proceed?\nI chose yes yesterday."
        ))
        self.assertFalse(wrapper_windows._approval_prompt_visible(
            "Claude: Here are options. Do you want to proceed?\n"
            "1. Yes, refactor now\n2. No, keep it"
        ))

    def test_one_torn_frame_does_not_post_a_transition(self):
        checker = wrapper_windows.get_activity_checker([123], agent_name="claude")
        screens = [CLAUDE_GLYPH_PROMPT, CLAUDE_GLYPH_PROMPT,
                   "", CLAUDE_GLYPH_PROMPT, CLAUDE_GLYPH_PROMPT]
        snapshots = [(s.encode("utf-16-le"), s) for s in screens]
        with patch.object(wrapper_windows, "_read_console_snapshot", side_effect=snapshots):
            states = [wrapper._build_activity_report(checker)["waiting_approval"]
                      for _ in screens]
        self.assertEqual(states, [False, True, True, True, True])


class ApprovalHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        registry = RuntimeRegistry(data_dir=str(root))
        registry.seed({"claude": {"label": "Claude", "color": "#da7756"}})
        store = MessageStore(str(root / "messages.jsonl"))
        agents = AgentTrigger(registry, str(root))
        for module, values in (
            (app, {"registry": registry, "store": store, "agents": agents,
                   "router": Mock(is_paused=Mock(return_value=False)),
                   "room_settings": {"channels": ["general", "work"]},
                   "_agent_last_channel": {"claude": "work"},
                   "broadcast_status": AsyncMock()}),
            (mcp_bridge, {"registry": registry, "store": store,
                          "_presence": {}, "_activity": {}, "_activity_ts": {},
                          "_approval_wait": {}}),
        ):
            for name, value in values.items():
                self.stack.enter_context(patch.object(module, name, value))
        self.instance = registry.register("claude")

    def heartbeat(self, body):
        request = Mock()
        request.headers = {"authorization": f"Bearer {self.instance['token']}"}
        request.json = AsyncMock(return_value=body)
        return asyncio.run(app.heartbeat("claude", request))

    @unittest.skipUnless(sys.platform == "win32", "Windows console screen test")
    def test_console_screen_reaches_status_and_chat(self):
        checker = wrapper_windows.get_activity_checker([123], agent_name="claude")
        with patch.object(wrapper_windows, "_read_console_snapshot",
                          return_value=(CLAUDE_PROMPT.encode("utf-16-le"), CLAUDE_PROMPT)):
            wrapper._build_activity_report(checker)
            report = wrapper._build_activity_report(checker)
        self.assertEqual(report, {"active": False, "waiting_approval": True})
        self.heartbeat(json.loads(json.dumps(report)))
        self.assertTrue(asyncio.run(app.get_status())["claude"]["waiting_approval"])
        self.assertIn("waiting for approval", app.store.get_recent(1)[0]["text"])

    def test_approval_transition_survives_refresh_and_clears(self):
        waiting = {"active": False, "waiting_approval": True}
        self.heartbeat(waiting)
        self.assertTrue(asyncio.run(app.get_status())["claude"]["waiting_approval"])
        self.heartbeat(waiting)
        messages = [m for m in app.store.get_recent(20) if m["type"] == "system"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["channel"], "work")
        self.assertIn("waiting for approval", messages[0]["text"].lower())
        self.heartbeat({"active": False, "waiting_approval": False})
        self.assertFalse(asyncio.run(app.get_status())["claude"]["waiting_approval"])
        self.heartbeat({"active": False, "waiting_approval": False})
        messages = [m for m in app.store.get_recent(20) if m["type"] == "system"]
        self.assertEqual(len(messages), 2)
        self.assertIn("no longer waiting", messages[1]["text"].lower())
        self.assertEqual(app.broadcast_status.await_count, 2)

    def test_plain_heartbeat_does_not_clear_wait(self):
        self.heartbeat({"waiting_approval": True})
        self.heartbeat({})
        self.assertTrue(asyncio.run(app.get_status())["claude"]["waiting_approval"])
        self.assertEqual(len(app.store.get_recent(20)), 1)


@unittest.skipUnless(shutil.which("node"), "Node is needed for pill DOM test")
class ApprovalPillTests(unittest.TestCase):
    def test_pill_transitions_waiting_to_available(self):
        source = (ROOT / "static" / "chat.js").read_text(encoding="utf-8")
        fn = source.split("function updateStatus(data) {", 1)[1].split(
            "\nfunction updateTyping(", 1)[0]
        code = "function updateStatus(data) {" + fn + "\n"
        harness = r"""
const vm = require('node:vm');
const classes = new Set(['status-pill']);
const pill = {
  classList: {add: x => classes.add(x), remove: (...xs) => xs.forEach(x => classes.delete(x)), contains: x => classes.has(x)},
  style: {setProperty() {}}, title: '@claude'
};
const ctx = {document: {getElementById: () => pill}, _agentRoles: {}, _syncBubbleRolePills() {}};
vm.createContext(ctx);
vm.runInContext(process.argv[1], ctx);
ctx.updateStatus({claude: {available: true, busy: false, waiting_approval: true}});
if (!classes.has('waiting-approval') || pill.title !== '@claude — Waiting for approval') process.exit(1);
ctx.updateStatus({claude: {available: true, busy: false, waiting_approval: false}});
if (classes.has('waiting-approval') || !classes.has('available')) process.exit(2);
"""
        result = subprocess.run(["node", "-e", harness, code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
