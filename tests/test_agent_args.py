"""Configured agent arguments reach provider launch without shell splitting."""

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402
import wrapper  # noqa: E402

if sys.platform == "win32":
    import wrapper_windows


class AgentArgsTests(unittest.TestCase):
    def launch(self, config, extra=(), command=None):
        with mock.patch.object(wrapper, "_resolve_mcp_inject", return_value={}):
            with mock.patch.object(wrapper, "_apply_mcp_inject", return_value=(["--mcp", "url"], {}, None)):
                return wrapper._build_provider_launch(
                    agent="codex", agent_cfg=config, instance_name="codex", data_dir=ROOT,
                    proxy_url=None, extra_args=list(extra), env={}, resolved_command=command,
                )[0]

    def test_toml_args_and_passthrough_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.toml").write_text(
                '[agents.codex]\ncommand = "codex"\nargs = ["--model", "model name", "a&b"]\n',
                encoding="utf-8",
            )
            config = config_loader.load_config(root)["agents"]["codex"]
        self.assertEqual(self.launch(config, ["--debug"]),
                         ["--mcp", "url", "--model", "model name", "a&b", "--debug"])

    def test_absent_and_invalid_args(self):
        self.assertEqual(self.launch({}, ["--debug"]), ["--mcp", "url", "--debug"])
        for value in ("--model foo", ["--model", 7], None, {"model": "x"}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "list of strings"):
                self.launch({"args": value})

    @unittest.skipUnless(sys.platform == "win32", "Windows launch path")
    def test_windows_process_receives_exact_argument_vector(self):
        launch_args = self.launch({"args": ["--model", "model name", "a&b"]}, ["--debug"])
        process = mock.Mock()
        with (mock.patch.object(wrapper_windows, "enable_vt_mode"),
              mock.patch.object(wrapper_windows, "_vt_keepalive_thread"),
              mock.patch.object(wrapper_windows.subprocess, "Popen", return_value=process) as popen):
            wrapper_windows.run_agent(command="codex", extra_args=launch_args, cwd=".", env={},
                                      queue_file=ROOT / "unused", agent="codex", no_restart=True,
                                      start_watcher=lambda _inject: None)
        self.assertEqual(popen.call_args.args[0],
                         ["codex", "--mcp", "url", "--model", "model name", "a&b", "--debug"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch launcher")
    def test_batch_shim_rejects_metacharacters_and_runs_plain_args(self):
        with tempfile.TemporaryDirectory() as directory:
            shim = Path(directory) / "shim.cmd"
            shim.write_text("@echo off\necho %~1\necho %~2\n", encoding="ascii")
            for arg in ("a&b", "%PATH%", "!name!", 'x"y', "x|y", "(x)", "bad\nline", "a,b", "a;b", "a=b"):
                with self.subTest(arg=arg), self.assertRaisesRegex(ValueError, "unsafe characters"):
                    self.launch({"args": [arg]}, command=str(shim))
            args = self.launch({"args": ["plain", "model name"]}, command=str(shim))
            # The stub MCP pair is also sent through the same launcher.
            completed = subprocess.run([str(shim), *args[2:]], capture_output=True, text=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=True)
            self.assertEqual(completed.stdout.splitlines(), ["plain", "model name"])


if __name__ == "__main__":
    unittest.main()
