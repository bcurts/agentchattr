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

from wrapper import _write_json_mcp_settings, _apply_mcp_inject  # noqa: E402


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


class ProxyFileInjectTests(unittest.TestCase):
    """proxy_file mode: route Claude through the identity proxy (token rotates
    live, no /mcp reconnect) WHILE preserving project-MCP merge (Gate 3)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.project_dir = Path(self.tmp.name) / "proj"
        self.project_dir.mkdir(parents=True)
        # A non-agentchattr project MCP server that MUST survive the mode switch.
        (self.project_dir / ".mcp.json").write_text(json.dumps({
            "mcpServers": {"unity-mcp": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}}
        }))

    def test_proxy_file_uses_proxy_url_no_token_and_preserves_merge(self):
        proxy_url = "http://127.0.0.1:50423/mcp"
        inject_cfg = {"mcp_inject": "proxy_file", "mcp_merge_project": True}
        args, env, settings_path = _apply_mcp_inject(
            inject_cfg, "claude", self.data_dir, proxy_url,
            token="SHOULD-NOT-BE-BAKED-IN", mcp_cfg={"http_port": 8200},
            project_dir=self.project_dir,
        )
        # Pass the proxy URL to Claude via a --mcp-config FILE.
        self.assertEqual(len(args), 2, "proxy_file mode not handled")
        self.assertEqual(args[0], "--mcp-config")
        servers = json.loads(Path(args[1]).read_text("utf-8"))["mcpServers"]
        # agentchattr points at the PROXY, with NO baked token (the proxy injects
        # the live token, so the file never goes stale on re-register).
        self.assertEqual(servers["agentchattr"]["url"], proxy_url)
        self.assertNotIn("headers", servers["agentchattr"])
        # Gate 3: the non-agentchattr project server survives.
        self.assertIn("unity-mcp", servers)

    def test_proxy_file_without_proxy_url_raises(self):
        # proxy_file REQUIRES a running proxy; a missing proxy_url must fail loud,
        # not silently write a token-less direct-server config.
        with self.assertRaises(ValueError):
            _apply_mcp_inject(
                {"mcp_inject": "proxy_file", "mcp_merge_project": True},
                "claude", self.data_dir, None,  # proxy_url missing
                token="x", mcp_cfg={"http_port": 8200}, project_dir=self.project_dir,
            )


if __name__ == "__main__":
    unittest.main()
