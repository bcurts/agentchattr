"""Regression coverage for update failures and duplicate-safe Windows startup."""
import contextlib
import io
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

from windows import start_team as team


COMMANDS = {"codex": "C:/bin/codex.exe", "claude": "C:/bin/claude.exe",
            "antigravity": "C:/bin/agy.exe"}


class TeamLauncherTests(unittest.TestCase):
    def test_process_detection_covers_wrappers_native_and_node_shims(self):
        processes = [
            {"Name": "python.exe", "CommandLine": 'python "C:/a path/wrapper.py" codex'},
            {"Name": "node.exe", "CommandLine": 'node "C:/npm/@anthropic-ai/claude-code/cli.js" --mcp-config "C:/agentchattr/data/provider-config/claude-mcp.json"'},
            {"Name": "python.exe", "CommandLine": 'python wrapper.py antigravity'},
        ]
        self.assertEqual(team.running_agents(processes, COMMANDS), set(COMMANDS))
        processes.append({"Name": "claude.exe"})
        self.assertEqual(team.running_agents(processes, COMMANDS), set(COMMANDS))

    def test_node_codex_and_claude_entry_points(self):
        processes = [{"Name": "node.exe", "CommandLine": 'node C:/npm/@openai/codex/bin/codex.js -c mcp_servers.agentchattr.url="http://localhost:8200/mcp"'},
                     {"Name": "node.exe", "CommandLine": 'node C:/npm/claude.js --mcp-config C:/agentchattr/data/claude.json'}]
        self.assertEqual(team.running_agents(processes, COMMANDS), {"codex", "claude"})

    def test_desktop_ide_and_unrelated_clis_do_not_block_team(self):
        processes = [
            {"Name": "Claude.exe", "CommandLine": 'C:/Programs/Claude/Claude.exe'},
            {"Name": "claude.exe", "CommandLine": 'claude.exe --ide --mcp-config C:/other/mcp.json'},
            {"Name": "codex.exe", "CommandLine": 'C:/Programs/OpenAI/Codex/bin/codex.exe'},
            {"Name": "codex.exe", "CommandLine": 'codex.exe -c mcp_servers.other.url=http://localhost:1234'},
            {"Name": "agy.exe", "CommandLine": None},
            {"Name": "node.exe", "CommandLine": 'node C:/npm/@anthropic-ai/claude-code/cli.js'},
            {"Name": "node.exe", "CommandLine": 'node C:/npm/@openai/codex/bin/codex.js'},
        ]
        unrelated = set()
        self.assertEqual(team.running_agents(processes, COMMANDS, unrelated), set())
        self.assertEqual(unrelated, set(COMMANDS))

    def test_native_clis_require_explicit_chat_mcp_arguments(self):
        processes = [
            {"Name": "claude.exe", "CommandLine": 'claude.exe --mcp-config "C:\\a path\\agentchattr\\data\\provider-config\\claude-mcp.json"'},
            {"Name": "codex.exe", "CommandLine": 'codex.exe -c mcp_servers.agentchattr.url="http://127.0.0.1:1234/mcp"'},
            {"Name": "agy.exe", "CommandLine": 'agy.exe'},
        ]
        self.assertEqual(team.running_agents(processes, COMMANDS), {"claude", "codex"})

    def test_unrelated_warning_is_once_per_family_and_suppressed_for_team_child(self):
        processes = [{"Name": "claude.exe"}, {"Name": "Claude.exe"},
                     {"Name": "agy.exe"},
                     {"Name": "python.exe", "CommandLine": 'python wrapper.py antigravity'}]
        with patch.object(team, "process_inventory", return_value=processes), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(team.inspect_agents(COMMANDS), {"antigravity"})
        self.assertEqual(output.getvalue().count("will not block"), 1)
        self.assertIn("Claude", output.getvalue())
        self.assertNotIn("Agy", output.getvalue())

    def test_unrelated_sessions_allow_all_missing_team_wrappers_to_start(self):
        processes = [{"Name": "Claude.exe"}, {"Name": "codex.exe"}, {"Name": "agy.exe"}]
        started = []

        def launch(command, **kwargs):
            name = team.AGENTS[len(started)]
            started.append(name)
            processes.append({"Name": "python.exe", "CommandLine": f'python wrapper.py {name}'})
            return Mock(poll=lambda: None)

        with patch.object(team, "process_inventory", side_effect=lambda: list(processes)), \
             patch.object(team, "server_ready", return_value=True), \
             patch.object(team.subprocess, "Popen", side_effect=launch), \
             patch.object(team.webbrowser, "open"), contextlib.redirect_stdout(io.StringIO()):
            team.start_team("http://127.0.0.1:8300", {}, COMMANDS)
            # A second run should reuse the wrappers, despite unrelated apps still running.
            team.start_team("http://127.0.0.1:8300", {}, COMMANDS)
        self.assertEqual(started, list(team.AGENTS))

    def test_ignores_shell_history_and_similar_agent_names(self):
        processes = [{"Name": "cmd.exe", "CommandLine": 'cmd /k python wrapper.py codex'},
                     {"Name": "python.exe", "CommandLine": 'python wrapper.py codex2'},
                     {"Name": "python.exe", "CommandLine": 'python notwrapper.py claude'}]
        self.assertEqual(team.running_agents(processes, COMMANDS), set())

    def test_codex_background_service_is_not_an_interactive_session(self):
        processes = [{"Name": "codex.exe", "CommandLine": '"C:/bin/codex.exe" app-server --port 100'},
                     {"Name": "codex.exe", "CommandLine": 'C:/packages/app-server-daemon/release/codex.exe'}]
        self.assertEqual(team.running_agents(processes, COMMANDS), set())

    def test_failed_child_stops_startup_instead_of_launching_more(self):
        with patch.object(team, "process_inventory", return_value=[]), \
             patch.object(team, "running_agents", return_value=set()), \
             patch.object(team, "server_ready", return_value=True), \
             patch.object(team.subprocess, "Popen", return_value=Mock(poll=lambda: 1)) as popen, \
             patch.object(team.webbrowser, "open") as browser:
            with self.assertRaisesRegex(RuntimeError, "Cannot confirm Codex startup"):
                team.start_team("http://127.0.0.1:8300", {}, COMMANDS)
        self.assertEqual(popen.call_count, 1)
        browser.assert_not_called()

    def test_inventory_failure_blocks_startup(self):
        with patch.object(team.subprocess, "run", side_effect=subprocess.TimeoutExpired("cim", 20)):
            with self.assertRaisesRegex(RuntimeError, "refusing"):
                team.process_inventory()

    def test_single_process_inventory_is_normalized(self):
        with patch.object(team.subprocess, "run", return_value=Mock(stdout='{"Name":"agy.exe"}')):
            self.assertEqual(team.process_inventory(), [{"Name": "agy.exe"}])

    def test_update_available_and_newer_installed_version(self):
        output = io.StringIO()
        with patch.object(team.subprocess, "run", return_value=Mock(stdout="codex-cli 1.2.3")), \
             patch.object(team.platform, "machine", return_value="AMD64"), \
             patch.object(team, "read_json", side_effect=[{"tag_name": "rust-v1.3.0"}, {"tag_name": "v1.0.0"}]), \
             contextlib.redirect_stdout(output):
            team.check_updates(dict(list(COMMANDS.items())[:2]), {})
        self.assertIn("update available", output.getvalue())
        self.assertIn("up to date or newer", output.getvalue())

    def test_network_and_cli_failures_are_nonfatal(self):
        with patch.object(team.subprocess, "run", side_effect=[Mock(stdout="1.2.3"),
                subprocess.TimeoutExpired("claude", 10), Mock(stdout="1.2.3")]), \
             patch.object(team.platform, "machine", return_value="AMD64"), \
             patch.object(team, "read_json", side_effect=[OSError("offline"), {"version": "bad"}]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            team.check_updates(COMMANDS, {})
        self.assertEqual(output.getvalue().count("continuing"), 3)

    def test_repeated_launch_skips_all_live_agents(self):
        with patch.object(team, "process_inventory", return_value=[]), \
             patch.object(team, "running_agents", return_value=set(COMMANDS)), \
             patch.object(team, "server_ready", return_value=True), \
             patch.object(team.subprocess, "Popen") as popen, \
             patch.object(team.webbrowser, "open"), contextlib.redirect_stdout(io.StringIO()):
            team.start_team("http://127.0.0.1:8300", {}, COMMANDS)
        popen.assert_not_called()

    def test_launches_missing_agent_and_waits_for_visibility(self):
        # Codex appears only after the first poll, before releasing the startup lock.
        active = [{"claude", "antigravity"}] * 3 + [set(COMMANDS)] * 3
        with patch.object(team, "process_inventory", return_value=[]), \
             patch.object(team, "running_agents", side_effect=active), \
             patch.object(team, "server_ready", return_value=True), \
             patch.object(team.subprocess, "Popen", return_value=Mock(poll=lambda: None)) as popen, \
             patch.object(team.time, "sleep"), patch.object(team.webbrowser, "open"), \
             contextlib.redirect_stdout(io.StringIO()):
            team.start_team("http://127.0.0.1:8300", {}, COMMANDS)
        self.assertEqual(popen.call_count, 1)
        self.assertIn("wrapper.py", popen.call_args.args[0])

    def test_startup_lock_rejects_overlap_and_releases(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(team.tempfile, "gettempdir", return_value=folder):
            with team.startup_lock():
                with self.assertRaisesRegex(RuntimeError, "Another team launcher"):
                    with team.startup_lock():
                        self.fail("overlapping launcher acquired lock")
            with team.startup_lock():
                pass


if __name__ == "__main__":
    unittest.main()
