"""Tests for wrapper.py MCP config writers.

Focused on the shape of the JSON written to provider settings files — Gemini
needs "httpUrl", CodeBuddy needs "url", legacy paths still work.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wrapper import _build_provider_launch, _write_json_mcp_settings  # noqa: E402


class JsonMcpSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "settings.json"

    def _read(self):
        return json.loads(self.target.read_text("utf-8"))

    def test_default_http_uses_httpUrl_key(self):
        # Backward compat: no http_key override → "httpUrl" (Gemini-style)
        _write_json_mcp_settings(self.target, "http://127.0.0.1:8200/mcp",
                                 transport="http")
        data = self._read()
        entry = data["mcpServers"]["agentchattr"]
        self.assertEqual(entry["type"], "http")
        self.assertEqual(entry["httpUrl"], "http://127.0.0.1:8200/mcp")
        self.assertNotIn("url", entry)

    def test_http_key_override_writes_url_key(self):
        # CodeBuddy-style: http_key="url" → MCP-standard "url" key
        _write_json_mcp_settings(self.target, "http://127.0.0.1:8200/mcp",
                                 transport="http", http_key="url")
        data = self._read()
        entry = data["mcpServers"]["agentchattr"]
        self.assertEqual(entry["type"], "http")
        self.assertEqual(entry["url"], "http://127.0.0.1:8200/mcp")
        self.assertNotIn("httpUrl", entry)

    def test_sse_transport_always_uses_url(self):
        # SSE doesn't use httpUrl regardless of http_key setting
        _write_json_mcp_settings(self.target, "http://127.0.0.1:8201/sse",
                                 transport="sse")
        data = self._read()
        entry = data["mcpServers"]["agentchattr"]
        self.assertEqual(entry["type"], "sse")
        self.assertEqual(entry["url"], "http://127.0.0.1:8201/sse")

    def test_bearer_token_written_as_authorization_header(self):
        _write_json_mcp_settings(self.target, "http://127.0.0.1:8200/mcp",
                                 transport="http", token="secret-token-123",
                                 http_key="url")
        entry = self._read()["mcpServers"]["agentchattr"]
        self.assertEqual(entry["headers"]["Authorization"], "Bearer secret-token-123")

    def test_bearer_token_env_var_replaces_static_authorization_header(self):
        _write_json_mcp_settings(
            self.target, "http://127.0.0.1:8200/mcp",
            transport="http", token="secret-token-123", http_key="url",
            bearer_token_env="AGENTCHATTR_MCP_TOKEN",
        )
        entry = self._read()["mcpServers"]["agentchattr"]
        self.assertEqual(entry["bearerTokenEnvVar"], "AGENTCHATTR_MCP_TOKEN")
        self.assertNotIn("headers", entry)
        self.assertNotIn("secret-token-123", self.target.read_text("utf-8"))

    def test_existing_servers_preserved(self):
        # Write a pre-existing settings file with an unrelated server
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(json.dumps({
            "mcpServers": {"some-other-server": {"type": "http", "url": "http://elsewhere"}}
        }))
        _write_json_mcp_settings(self.target, "http://127.0.0.1:8200/mcp",
                                 transport="http", http_key="url")
        data = self._read()
        self.assertIn("some-other-server", data["mcpServers"])
        self.assertIn("agentchattr", data["mcpServers"])


class KimiBearerTokenEnvLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project_dir = self.root / "project"
        self.data_dir = self.root / "data"
        self.settings_file = self.project_dir / ".kimi-code" / "mcp.json"

    def _launch_kimi(self, instance_name: str, token: str):
        return _build_provider_launch(
            agent="kimi",
            agent_cfg={},
            instance_name=instance_name,
            data_dir=self.data_dir,
            proxy_url=None,
            extra_args=["--yolo"],
            env={},
            token=token,
            mcp_cfg={"http_port": 18200},
            project_dir=self.project_dir,
        )

    def test_kimi_instances_keep_tokens_in_process_env_not_shared_mcp_json(self):
        launch_1 = self._launch_kimi("kimi-1", "token-a")
        launch_2 = self._launch_kimi("kimi-2", "token-b")

        args_1, _env_1, inject_env_1, settings_path_1 = launch_1
        args_2, _env_2, inject_env_2, settings_path_2 = launch_2

        self.assertEqual(args_1, ["--yolo"])
        self.assertEqual(args_2, ["--yolo"])
        self.assertNotIn("--mcp-config-file", args_1 + args_2)
        self.assertEqual(inject_env_1["AGENTCHATTR_MCP_TOKEN"], "token-a")
        self.assertEqual(inject_env_2["AGENTCHATTR_MCP_TOKEN"], "token-b")
        self.assertEqual(settings_path_1, self.settings_file)
        self.assertEqual(settings_path_2, self.settings_file)

        text = self.settings_file.read_text("utf-8")
        data = json.loads(text)
        entry = data["mcpServers"]["agentchattr"]
        self.assertEqual(entry["url"], "http://127.0.0.1:18200/mcp")
        self.assertEqual(entry["bearerTokenEnvVar"], "AGENTCHATTR_MCP_TOKEN")
        self.assertNotIn("headers", entry)
        self.assertNotIn("token-a", text)
        self.assertNotIn("token-b", text)


class ExpanduserPathTests(unittest.TestCase):
    """Verify the _build_provider_launch path expansion logic.

    Unit-testing _build_provider_launch directly would require too much
    scaffolding (registry, token, etc.). Instead we verify Path behavior
    matches our expectations — the wrapper code uses Path(...).expanduser()
    at a single well-defined spot.
    """

    def test_tilde_prefix_expands_to_home(self):
        raw = "~/.codebuddy/.mcp.json"
        expanded = Path(raw).expanduser()
        self.assertTrue(expanded.is_absolute())
        # Must no longer contain a literal ~
        self.assertNotIn("~", str(expanded))
        # Sanity: should land under the user's home dir
        self.assertTrue(str(expanded).startswith(str(Path.home())))

    def test_absolute_path_unchanged_by_expanduser(self):
        raw = str(Path("/tmp/literal-abs").resolve())
        expanded = Path(raw).expanduser()
        self.assertEqual(str(expanded), raw)

    def test_relative_path_stays_relative_after_expanduser(self):
        # Relative paths without ~ aren't made absolute by expanduser alone —
        # that's handled by the subsequent `base / target` join in wrapper.py.
        raw = ".qwen/settings.json"
        expanded = Path(raw).expanduser()
        self.assertFalse(expanded.is_absolute())


if __name__ == "__main__":
    unittest.main()
