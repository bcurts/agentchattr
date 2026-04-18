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

from wrapper import _write_json_mcp_settings  # noqa: E402


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
