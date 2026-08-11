import asyncio
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from launcher_supervisor import Launcher, LogEvent, ManagedProcess, ServerProbe

    HAS_SERVER_PROBE = True
except ImportError:
    from launcher import Launcher, LogEvent, ManagedProcess

    ServerProbe = None
    HAS_SERVER_PROBE = False
from launcher_rules import process_actions, server_actions


TEST_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 8300,
        "data_dir": "./data",
    },
    "mcp": {
        "http_port": 8200,
        "sse_port": 8201,
    },
    "agents": {
        "codex": {
            "label": "Codex",
            "command": "codex",
            "cwd": "..",
            "color": "#10a37f",
        },
        "kimi": {
            "label": "Kimi",
            "command": "kimi",
            "cwd": "..",
            "color": "#111111",
        },
    },
}


class DummySubprocess:
    stdin = object()
    returncode = None


class DummyConsoleSubprocess:
    stdin = None
    returncode = None


class DummyStartedConsoleSubprocess:
    pid = 9876
    stdin = None
    stdout = None
    stderr = None
    returncode = None

    async def wait(self):
        await asyncio.Future()


class DummyExitedConsoleSubprocess:
    pid = 9876
    stdin = None
    stdout = None
    stderr = None
    returncode = 1

    async def wait(self):
        return self.returncode


class DummyWaitedConsoleSubprocess:
    pid = 9876
    stdin = None
    stdout = None
    stderr = None
    returncode = None

    async def wait(self):
        self.returncode = 0
        return self.returncode


def make_launcher():
    config = copy.deepcopy(TEST_CONFIG)
    config["server"]["data_dir"] = tempfile.mkdtemp(prefix="agentchattr-launcher-test-")
    try:
        return Launcher(
            config=config,
            registry_provider=None,
            role_setter=None,
        )
    except TypeError:
        return Launcher()


class LauncherStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_serializes_server_templates_and_managed_processes(self):
        launcher = make_launcher()
        launcher._processes["agent:codex:1"] = ManagedProcess(
            key="agent:codex:1",
            kind="agent",
            base="codex",
            assigned_name="codex",
            pid=1234,
            status="running",
            started_by_launcher=True,
            started_at=10.0,
            mode="normal",
            role="Builder",
        )
        launcher._subprocesses["agent:codex:1"] = DummySubprocess()

        if HAS_SERVER_PROBE:
            probe = ServerProbe(running=True, occupied=False, status="running")
            patch_target = patch.object(
                launcher, "_probe_server", AsyncMock(return_value=probe)
            )
        else:
            patch_target = patch.object(
                launcher, "_is_server_running", AsyncMock(return_value=True)
            )

        with (
            patch_target,
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value={})),
        ):
            status = await launcher.get_status()

        self.assertTrue(status["server"]["running"])
        self.assertFalse(status["server"]["managed_by_launcher"])
        self.assertIn("codex", status["templates"])
        self.assertEqual(status["processes"]["agent:codex:1"]["pid"], 1234)
        self.assertEqual(status["processes"]["agent:codex:1"]["role"], "Builder")
        self.assertTrue(status["processes"]["agent:codex:1"]["started_by_launcher"])
        self.assertTrue(status["processes"]["agent:codex:1"]["can_send_input"])
        self.assertEqual(
            status["processes"]["agent:codex:1"]["input_capability"],
            "stdin_pipe",
        )

    async def test_status_merges_runtime_busy_and_external_agents_from_server_status(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        launcher._processes["agent:kimi:1"] = ManagedProcess(
            key="agent:kimi:1",
            kind="agent",
            base="kimi",
            assigned_name="kimi",
            pid=4321,
            status="running",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
            role=None,
        )
        launcher._subprocesses["agent:kimi:1"] = DummySubprocess()

        probe = ServerProbe(running=True, occupied=False, status="running")
        runtime = {
            "kimi": {
                "available": True,
                "busy": True,
                "base": "kimi",
                "role": "Researcher",
                "label": "Kimi",
                "color": "#111111",
                "state": "active",
            },
            "codex": {
                "available": True,
                "busy": False,
                "base": "codex",
                "role": "Builder",
                "label": "Codex",
                "color": "#10a37f",
                "state": "active",
            },
        }

        with (
            patch.object(launcher, "_probe_server", AsyncMock(return_value=probe)),
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value=runtime)),
        ):
            status = await launcher.get_status()

        processes = status["processes"]
        self.assertEqual(processes["agent:kimi:1"]["status"], "working")
        self.assertTrue(processes["agent:kimi:1"]["busy"])
        self.assertEqual(processes["agent:kimi:1"]["role"], "Researcher")
        self.assertEqual(processes["external:codex"]["status"], "active")
        self.assertFalse(processes["external:codex"]["started_by_launcher"])

    async def test_status_marks_windows_console_agent_input_capability(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:kimi:console"
        launcher._processes[key] = ManagedProcess(
            key=key,
            kind="agent",
            base="kimi",
            assigned_name="kimi",
            pid=4321,
            status="running",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
            role=None,
        )
        launcher._subprocesses[key] = DummyConsoleSubprocess()

        with (
            patch.object(
                launcher,
                "_probe_server",
                AsyncMock(return_value=ServerProbe(running=False, status="stopped")),
            ),
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value={})),
            patch("launcher_supervisor.platform.system", return_value="Windows"),
        ):
            status = await launcher.get_status()

        process = status["processes"][key]
        self.assertFalse(process["can_send_input"])
        self.assertEqual(process["input_capability"], "windows_terminal")
        self.assertEqual(process["terminal_capability"], "windows_terminal")

    async def test_start_agent_defaults_to_windows_terminal_and_no_restart(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        create_proc = AsyncMock(return_value=DummyStartedConsoleSubprocess())

        with (
            patch.object(launcher, "_is_server_running", AsyncMock(return_value=True)),
            patch.object(launcher, "_probe_server", AsyncMock(return_value=ServerProbe(running=True, status="running"))),
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value={})),
            patch("launcher_supervisor.platform.system", return_value="Windows"),
            patch("launcher_supervisor.asyncio.create_subprocess_exec", create_proc),
        ):
            result = await launcher.start_agent("codex", cwd=str(ROOT))
            status = await launcher.get_status()

        self.assertEqual(result["status"], "starting")
        argv = list(create_proc.await_args.args)
        self.assertIn("--no-restart", argv)
        self.assertEqual(argv[argv.index("--workdir") + 1], str(ROOT.resolve()))
        self.assertEqual(create_proc.await_args.kwargs["stdin"], None)
        self.assertEqual(create_proc.await_args.kwargs["stdout"], None)
        self.assertEqual(create_proc.await_args.kwargs["stderr"], None)
        self.assertEqual(
            create_proc.await_args.kwargs["creationflags"],
            subprocess.CREATE_NEW_CONSOLE,
        )
        process = status["processes"][result["process_key"]]
        self.assertEqual(process["input_capability"], "windows_terminal")
        self.assertEqual(process["terminal_capability"], "windows_terminal")
        self.assertFalse(process["can_send_input"])
        self.assertTrue(process["started_by_launcher"])

    async def test_agent_console_exit_marks_agent_stopped_without_restart(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:codex:console"
        process = ManagedProcess(
            key=key,
            kind="agent",
            base="codex",
            assigned_name="codex",
            pid=9876,
            status="running",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
        )
        launcher._processes[key] = process
        proc = DummyExitedConsoleSubprocess()
        launcher._subprocesses[key] = proc

        await launcher._monitor_process(key, proc, process)

        self.assertEqual(launcher._processes[key].status, "stopped")
        self.assertIsNone(launcher._processes[key].last_error)
        self.assertNotIn(key, launcher._subprocesses)

    async def test_agent_console_exit_releases_identity(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:codex:console"
        process = ManagedProcess(
            key=key,
            kind="agent",
            base="codex",
            assigned_name="codex",
            pid=9876,
            status="running",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
        )
        launcher._processes[key] = process
        proc = DummyExitedConsoleSubprocess()
        launcher._subprocesses[key] = proc

        with patch.object(launcher, "_release_agent_identity", AsyncMock()) as release:
            await launcher._monitor_process(key, proc, process)

        release.assert_awaited_once_with("codex")

    async def test_windows_taskkill_is_hidden(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        completed = subprocess.CompletedProcess(
            ["taskkill", "/PID", "9876", "/T", "/F"],
            0,
            stdout="",
            stderr="",
        )

        with (
            patch("launcher_supervisor.platform.system", return_value="Windows"),
            patch("launcher_supervisor.subprocess.run", return_value=completed) as run,
        ):
            result = await launcher._terminate_windows_process_tree(9876)

        self.assertEqual(result["status"], "stopped")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertIsNotNone(kwargs["startupinfo"])
        self.assertTrue(kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, subprocess.SW_HIDE)

    async def test_stop_launcher_agent_releases_identity(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:kimi:console"
        launcher._processes[key] = ManagedProcess(
            key=key,
            kind="agent",
            base="kimi",
            assigned_name="kimi",
            pid=9876,
            status="running",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
            cwd=str(ROOT),
        )
        launcher._subprocesses[key] = DummyWaitedConsoleSubprocess()

        with (
            patch("launcher_supervisor.platform.system", return_value="Windows"),
            patch.object(
                launcher,
                "_terminate_windows_process_tree",
                AsyncMock(return_value={"pid": 9876, "status": "stopped"}),
            ),
            patch.object(launcher, "_release_agent_identity", AsyncMock()) as release,
        ):
            result = await launcher.stop_process(key)

        self.assertEqual(result["status"], "stopped")
        release.assert_awaited_once_with("kimi")

    async def test_pending_managed_agent_suppresses_duplicate_external_runtime(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        launcher._processes["agent:codex:pending"] = ManagedProcess(
            key="agent:codex:pending",
            kind="agent",
            base="codex",
            assigned_name=None,
            pid=9876,
            status="starting",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
        )
        launcher._subprocesses["agent:codex:pending"] = DummyConsoleSubprocess()
        runtime = {
            "codex": {
                "available": True,
                "busy": False,
                "base": "codex",
                "label": "Codex",
                "state": "active",
            }
        }

        with (
            patch.object(launcher, "_probe_server", AsyncMock(return_value=ServerProbe(running=True, status="running"))),
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value=runtime)),
        ):
            status = await launcher.get_status()

        self.assertIn("agent:codex:pending", status["processes"])
        self.assertNotIn("external:codex", status["processes"])
        self.assertEqual(
            status["processes"]["agent:codex:pending"]["assigned_name"],
            "codex",
        )

    async def test_stopped_launcher_agent_is_not_revived_by_stale_runtime(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:codex:stopped"
        launcher._processes[key] = ManagedProcess(
            key=key,
            kind="agent",
            base="codex",
            assigned_name="codex",
            pid=9876,
            status="stopped",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
            cwd=str(ROOT),
        )
        runtime = {
            "codex": {
                "available": True,
                "busy": False,
                "base": "codex",
                "label": "Codex",
                "state": "active",
            }
        }

        with (
            patch.object(launcher, "_probe_server", AsyncMock(return_value=ServerProbe(running=True, status="running"))),
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value=runtime)),
        ):
            status = await launcher.get_status()

        process = status["processes"][key]
        self.assertEqual(process["status"], "stopped")
        self.assertFalse(process["available"])
        self.assertFalse(process["busy"])
        self.assertNotIn("external:codex", status["processes"])

    async def test_external_registry_active_without_fresh_heartbeat_is_stopped(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        runtime = {
            "codex": {
                "available": False,
                "busy": False,
                "base": "codex",
                "label": "Codex",
                "state": "active",
            }
        }

        with (
            patch.object(launcher, "_probe_server", AsyncMock(return_value=ServerProbe(running=True, status="running"))),
            patch.object(launcher, "_server_runtime_status", AsyncMock(return_value=runtime)),
        ):
            status = await launcher.get_status()

        process = status["processes"]["external:codex"]
        self.assertEqual(process["status"], "stopped")
        self.assertFalse(process["available"])

    async def test_start_existing_agent_reuses_key_and_preferred_name(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:codex:reuse"
        launcher._processes[key] = ManagedProcess(
            key=key,
            kind="agent",
            base="codex",
            assigned_name="codex",
            pid=1111,
            status="stopped",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
            cwd=str(ROOT),
        )
        create_proc = AsyncMock(return_value=DummyStartedConsoleSubprocess())

        with (
            patch.object(launcher, "_is_server_running", AsyncMock(return_value=True)),
            patch.object(launcher, "_release_agent_identity", AsyncMock()) as release,
            patch("launcher_supervisor.platform.system", return_value="Windows"),
            patch("launcher_supervisor.asyncio.create_subprocess_exec", create_proc),
        ):
            result = await launcher.start_existing_agent(key)

        self.assertEqual(result["process_key"], key)
        self.assertEqual(result["assigned_name"], "codex")
        release.assert_awaited_once_with("codex")
        argv = list(create_proc.await_args.args)
        self.assertIn("--preferred-name", argv)
        self.assertEqual(argv[argv.index("--preferred-name") + 1], "codex")
        self.assertIn("--no-restart", argv)
        self.assertEqual(argv[argv.index("--workdir") + 1], str(ROOT.resolve()))
        self.assertEqual(create_proc.await_args.kwargs["cwd"], str(ROOT.resolve()))

    async def test_restart_agent_reuses_key_and_preferred_name(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        key = "agent:codex:reuse"
        launcher._processes[key] = ManagedProcess(
            key=key,
            kind="agent",
            base="codex",
            assigned_name="codex",
            pid=1111,
            status="stopped",
            started_by_launcher=True,
            started_at=20.0,
            mode="normal",
            cwd=str(ROOT),
        )
        create_proc = AsyncMock(return_value=DummyStartedConsoleSubprocess())

        with (
            patch.object(launcher, "_is_server_running", AsyncMock(return_value=True)),
            patch("launcher_supervisor.platform.system", return_value="Windows"),
            patch("launcher_supervisor.asyncio.create_subprocess_exec", create_proc),
        ):
            result = await launcher.restart_process(key)

        self.assertEqual(result["process_key"], key)
        argv = list(create_proc.await_args.args)
        self.assertIn("--preferred-name", argv)
        self.assertEqual(argv[argv.index("--preferred-name") + 1], "codex")
        self.assertEqual(argv[argv.index("--workdir") + 1], str(ROOT.resolve()))
        self.assertEqual(create_proc.await_args.kwargs["cwd"], str(ROOT.resolve()))

    async def test_status_marks_saved_live_server_as_launcher_managed(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        saved_state = {
            "pid": 2468,
            "root": str(ROOT.resolve()),
            "port": 8300,
            "launcher_token": "secret",
        }

        with (
            patch.object(
                launcher,
                "_probe_server",
                AsyncMock(return_value=ServerProbe(running=True, status="running")),
            ),
            patch.object(launcher, "_load_server_state", return_value=saved_state),
            patch.object(launcher, "_pid_is_alive", return_value=True),
        ):
            status = await launcher.get_status()

        self.assertTrue(status["server"]["managed_by_launcher"])
        self.assertEqual(status["server"]["pid"], 2468)

    async def test_stop_server_uses_saved_launcher_shutdown_token(self):
        if not HAS_SERVER_PROBE:
            self.skipTest("ServerProbe is unavailable")
        launcher = make_launcher()
        with (
            patch.object(
                launcher,
                "_probe_server",
                AsyncMock(return_value=ServerProbe(running=True, status="running")),
            ),
            patch.object(
                launcher,
                "_stop_server_with_saved_token",
                AsyncMock(return_value={"key": "server", "status": "stopped"}),
            ) as shutdown,
        ):
            result = await launcher.stop_server()

        self.assertEqual(result["status"], "stopped")
        shutdown.assert_awaited_once()

    async def test_stop_external_agent_is_rejected_without_subprocess_handle(self):
        launcher = make_launcher()

        result = await launcher.stop_process("external:codex")

        self.assertEqual(result["status"], "external")
        self.assertIn("cannot be stopped", result["error"])

    async def test_stop_known_process_without_handle_is_rejected_as_external(self):
        launcher = make_launcher()
        launcher._processes["agent:kimi:1"] = ManagedProcess(
            key="agent:kimi:1",
            kind="agent",
            base="kimi",
            assigned_name="kimi",
            pid=4321,
            status="running",
            started_by_launcher=False,
            started_at=20.0,
        )

        result = await launcher.stop_process("agent:kimi:1")

        self.assertEqual(result["status"], "external")
        self.assertIn("not managed", result["error"])


class LauncherLogTests(unittest.TestCase):
    def test_get_logs_returns_recent_events_in_order(self):
        launcher = make_launcher()
        launcher._logs["agent:codex:1"] = deque(
            [
                LogEvent("agent:codex:1", "stdout", "first", 1.0),
                LogEvent("agent:codex:1", "stderr", "second", 2.0),
                LogEvent("agent:codex:1", "stdout", "third", 3.0),
            ],
            maxlen=launcher.MAX_LOG_LINES,
        )

        logs = launcher.get_logs("agent:codex:1", limit=2)

        self.assertEqual([entry["text"] for entry in logs], ["second", "third"])
        self.assertEqual(logs[0]["stream"], "stderr")
        self.assertEqual(logs[1]["timestamp"], 3.0)

    def test_read_stream_captures_stdout_and_trims_newlines(self):
        launcher = make_launcher()

        async def run_read():
            reader = asyncio.StreamReader()
            reader.feed_data(b"hello\r\n")
            reader.feed_data("snowman: \u2603\n".encode("utf-8"))
            reader.feed_eof()
            await launcher._read_stream("server", reader, "stdout")

        asyncio.run(run_read())

        logs = launcher.get_logs("server", limit=10)
        self.assertEqual([entry["text"] for entry in logs], ["hello", "snowman: \u2603"])
        self.assertTrue(all(entry["stream"] == "stdout" for entry in logs))

    def test_read_stream_decodes_local_encoding_and_strips_ansi_controls(self):
        launcher = make_launcher()

        async def run_read():
            reader = asyncio.StreamReader()
            reader.feed_data("\x1b[31m中文\x1b[0m\x07\r进度\r\n".encode("gbk"))
            reader.feed_eof()
            with patch(
                "launcher_supervisor.locale.getpreferredencoding",
                return_value="gbk",
            ):
                await launcher._read_stream("agent:kimi:1", reader, "stderr")

        asyncio.run(run_read())

        logs = launcher.get_logs("agent:kimi:1", limit=10)
        self.assertEqual(logs[0]["text"], "中文进度")
        self.assertEqual(logs[0]["stream"], "stderr")

    def test_send_input_writes_utf8_line_to_subprocess_stdin(self):
        launcher = make_launcher()

        async def run_send():
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "line=sys.stdin.buffer.readline(); "
                    "sys.stdout.buffer.write(line); "
                    "sys.stdout.flush()"
                ),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            key = "agent:kimi:stdin"
            launcher._processes[key] = ManagedProcess(
                key=key,
                kind="agent",
                base="kimi",
                assigned_name="kimi",
                pid=proc.pid,
                status="running",
                started_by_launcher=True,
                started_at=1.0,
            )
            launcher._subprocesses[key] = proc

            result = await launcher.send_input(key, "中文")
            echoed = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
            await asyncio.wait_for(proc.wait(), timeout=2.0)
            return result, echoed

        result, echoed = asyncio.run(run_send())

        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["capability"], "stdin_pipe")
        self.assertEqual(echoed.decode("utf-8"), "中文\n")

    def test_send_input_rejects_external_process_without_pipe(self):
        launcher = make_launcher()

        async def run_send():
            return await launcher.send_input("external:kimi", "hello")

        result = asyncio.run(run_send())

        self.assertEqual(result["status"], "external")
        self.assertIn("no launcher stdin pipe", result["error"])


class LauncherRuleTests(unittest.TestCase):
    def test_server_actions_only_stop_launcher_managed_server(self):
        self.assertEqual(
            server_actions({"running": False, "managed_by_launcher": False}),
            {"can_start": True, "can_stop": False, "can_restart": False},
        )
        self.assertEqual(
            server_actions({"running": True, "managed_by_launcher": False}),
            {"can_start": False, "can_stop": False, "can_restart": False},
        )
        self.assertEqual(
            server_actions({"running": True, "managed_by_launcher": True}),
            {"can_start": False, "can_stop": True, "can_restart": True},
        )

    def test_process_actions_match_launcher_ownership_rules(self):
        managed_running = {
            "base": "codex",
            "status": "working",
            "started_by_launcher": True,
        }
        external_running = {
            "base": "codex",
            "status": "running",
            "started_by_launcher": False,
        }
        external_stopped = {
            "base": "codex",
            "status": "stopped",
            "started_by_launcher": False,
        }
        stopped_template = {
            "base": "kimi",
            "status": "stopped",
            "started_by_launcher": True,
        }

        self.assertEqual(
            process_actions(managed_running),
            {
                "can_start": False,
                "can_stop": True,
                "can_restart": True,
                "can_view_logs": True,
                "is_external": False,
            },
        )
        self.assertEqual(
            process_actions(external_running),
            {
                "can_start": False,
                "can_stop": False,
                "can_restart": False,
                "can_view_logs": False,
                "is_external": True,
            },
        )
        self.assertFalse(process_actions(external_stopped)["can_start"])
        self.assertTrue(process_actions(stopped_template)["can_start"])
        self.assertFalse(process_actions(stopped_template)["can_stop"])


class LauncherWorkdirTests(unittest.IsolatedAsyncioTestCase):
    async def test_workdir_flag_precedes_agent_pass_through_args(self):
        launcher = make_launcher()
        process = AsyncMock(return_value=DummyStartedConsoleSubprocess())
        with (
            patch.object(launcher, "_is_server_running", AsyncMock(return_value=True)),
            patch("launcher_supervisor.asyncio.create_subprocess_exec", process),
        ):
            result = await launcher.start_agent("codex", mode="yolo", cwd=str(ROOT))
        self.assertEqual(result["status"], "starting")
        argv = list(process.await_args.args)
        self.assertLess(argv.index("--workdir"), argv.index("--"))
        self.assertEqual(argv[argv.index("--workdir") + 1], str(ROOT.resolve()))

    async def test_start_agent_uses_absolute_existing_directory(self):
        launcher = make_launcher()
        process = AsyncMock(return_value=DummyStartedConsoleSubprocess())
        with (
            patch.object(launcher, "_is_server_running", AsyncMock(return_value=True)),
            patch("launcher_supervisor.asyncio.create_subprocess_exec", process),
        ):
            result = await launcher.start_agent("codex", cwd=".")
        self.assertEqual(result["status"], "starting")
        self.assertEqual(process.await_args.kwargs["cwd"], str(launcher.root.resolve()))

    async def test_start_agent_rejects_missing_non_directory_and_file_workdirs(self):
        launcher = make_launcher()
        self.assertIn("choose a working directory", (await launcher.start_agent("codex"))["error"])
        missing = await launcher.start_agent("codex", cwd=str(launcher.root / "does-not-exist"))
        self.assertIn("does not exist", missing["error"])
        with tempfile.NamedTemporaryFile() as file:
            result = await launcher.start_agent("codex", cwd=file.name)
        self.assertIn("not a directory", result["error"])

    async def test_kimi_rejects_windows_drive_root(self):
        launcher = make_launcher()
        with patch("launcher_supervisor.platform.system", return_value="Windows"):
            workdir, error = launcher._resolve_workdir("kimi", "C:\\")
        self.assertIsNone(workdir)
        self.assertIn("drive root", error or "")

    async def test_preferences_use_configured_data_dir_when_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = copy.deepcopy(TEST_CONFIG)
            config["server"]["data_dir"] = "./configured-data"
            preferences = root / "configured-data" / "launcher_preferences.json"
            preferences.parent.mkdir()
            preferences.write_text(
                json.dumps({"version": 1, "last_workdirs": {"codex": str(root)}}),
                encoding="utf-8",
            )
            with (
                patch("launcher_supervisor.ROOT", root),
                patch("launcher_supervisor.load_config", return_value=config),
            ):
                launcher = Launcher()
        self.assertEqual(launcher._last_workdirs, {"codex": str(root)})

    async def test_workdirs_persist_by_agent_type_and_recover_from_bad_file(self):
        launcher = make_launcher()
        launcher._remember_workdir("codex", ROOT.resolve())
        launcher._remember_workdir("kimi", launcher.root.resolve())
        reloaded = Launcher(config=launcher._config, registry_provider=None, role_setter=None)
        self.assertEqual(reloaded._last_workdirs["codex"], str(ROOT.resolve()))
        self.assertEqual(reloaded._last_workdirs["kimi"], str(launcher.root.resolve()))
        reloaded._preferences_path().write_text("not json", encoding="utf-8")
        recovered = Launcher(config=launcher._config, registry_provider=None, role_setter=None)
        self.assertEqual(recovered._last_workdirs, {})

    async def test_failed_subprocess_does_not_remember_workdir(self):
        launcher = make_launcher()
        with (
            patch.object(launcher, "_is_server_running", AsyncMock(return_value=True)),
            patch("launcher_supervisor.asyncio.create_subprocess_exec", AsyncMock(side_effect=OSError("nope"))),
        ):
            result = await launcher.start_agent("codex", cwd=str(ROOT))
        self.assertIn("Failed to start", result["error"])
        self.assertNotIn("codex", launcher._last_workdirs)


if __name__ == "__main__":
    unittest.main()
