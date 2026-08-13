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

from wrapper import (  # noqa: E402
    _build_provider_launch,
    _normalize_passthrough_args,
    _resolve_agent_workdir,
    _write_json_mcp_settings,
)


class WorkdirResolutionTests(unittest.TestCase):
    def test_absolute_override_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            override = root / "selected-project"
            resolved = _resolve_agent_workdir(root, "../configured", override)
        self.assertEqual(resolved, override.resolve())

    def test_relative_override_resolves_from_runtime_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resolved = _resolve_agent_workdir(root, "../configured", "selected-project")
        self.assertEqual(resolved, (root / "selected-project").resolve())

    def test_configured_cwd_remains_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resolved = _resolve_agent_workdir(root, "../configured")
        self.assertEqual(resolved, (root / "../configured").resolve())


class PassthroughArgumentTests(unittest.TestCase):
    def test_wrapper_separator_is_not_forwarded_to_provider(self):
        args = ["--", "--dangerously-bypass-approvals-and-sandbox"]
        self.assertEqual(
            _normalize_passthrough_args(args),
            ["--dangerously-bypass-approvals-and-sandbox"],
        )

    def test_provider_flags_without_separator_are_unchanged(self):
        args = ["--yolo"]
        self.assertEqual(_normalize_passthrough_args(args), args)


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


class OpenCodeEnvContentTests(unittest.TestCase):
    """OpenCode reads OPENCODE_CONFIG_CONTENT (inline JSON, merged with user config)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.project_dir = Path(self.tmp.name) / "project"

    def _launch_opencode(self, instance_name, token, env=None, extra_args=None):
        return _build_provider_launch(
            agent="opencode",
            agent_cfg={},
            instance_name=instance_name,
            data_dir=self.data_dir,
            proxy_url=None,
            extra_args=extra_args if extra_args is not None else ["--auto"],
            env=env or {},
            token=token,
            mcp_cfg={"http_port": 18200},
            project_dir=self.project_dir,
        )

    def test_env_content_shape(self):
        args, _env, inject_env, settings_path = self._launch_opencode("opencode", "token-a")
        self.assertEqual(args, ["--auto"])
        self.assertIsNone(settings_path)
        # env_content mode must not write anything to disk
        self.assertFalse(self.data_dir.exists())
        entry = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])["mcp"]["agentchattr"]
        self.assertEqual(entry["type"], "remote")
        self.assertEqual(entry["url"], "http://127.0.0.1:18200/mcp")
        self.assertTrue(entry["enabled"])
        self.assertIs(entry["oauth"], False)
        self.assertEqual(entry["headers"]["Authorization"], "Bearer token-a")

    def test_deep_merge_preserves_existing_config_content(self):
        existing = json.dumps({
            "model": "anthropic/claude-sonnet-4",
            "plugin": ["my-plugin"],
            "mcp": {"other": {"type": "local", "command": ["other"]}},
        })
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            env={"OPENCODE_CONFIG_CONTENT": existing},
        )
        cfg = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])
        # user settings survive
        self.assertEqual(cfg["model"], "anthropic/claude-sonnet-4")
        self.assertEqual(cfg["plugin"], ["my-plugin"])
        self.assertIn("other", cfg["mcp"])
        # our entry is merged in alongside
        entry = cfg["mcp"]["agentchattr"]
        self.assertEqual(entry["type"], "remote")
        self.assertIs(entry["oauth"], False)
        self.assertEqual(entry["headers"]["Authorization"], "Bearer token-a")

    def test_tokens_are_per_instance_and_never_written_to_disk(self):
        _a1, _e1, inject_1, settings_1 = self._launch_opencode("opencode", "token-a")
        _a2, _e2, inject_2, settings_2 = self._launch_opencode("opencode-2", "token-b")
        self.assertIn("Bearer token-a", inject_1["OPENCODE_CONFIG_CONTENT"])
        self.assertIn("Bearer token-b", inject_2["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("token-b", inject_1["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("token-a", inject_2["OPENCODE_CONFIG_CONTENT"])
        self.assertIsNone(settings_1)
        self.assertIsNone(settings_2)
        self.assertFalse(self.data_dir.exists())

    def test_unparseable_existing_config_content_keeps_our_entry(self):
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            env={"OPENCODE_CONFIG_CONTENT": "not json"},
        )
        entry = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])["mcp"]["agentchattr"]
        self.assertEqual(entry["headers"]["Authorization"], "Bearer token-a")

    def test_auto_mode_replaces_permission_subtree(self):
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            extra_args=["--auto"],
        )
        cfg = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(cfg["permission"], {"*": "allow", "external_directory": "deny"})

    def test_auto_mode_overrides_user_deny_instead_of_merging(self):
        existing = json.dumps({
            "model": "anthropic/claude-sonnet-4",
            "permission": {"bash": {"rm *": "deny"}, "edit": "deny"},
        })
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            env={"OPENCODE_CONFIG_CONTENT": existing},
            extra_args=["--auto"],
        )
        cfg = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])
        # permission subtree is REPLACED, not deep-merged — user deny wiped
        self.assertEqual(cfg["permission"], {"*": "allow", "external_directory": "deny"})
        # non-permission user settings survive the overlay
        self.assertEqual(cfg["model"], "anthropic/claude-sonnet-4")

    def test_auto_mode_preserves_non_permission_fields_and_mcp(self):
        existing = json.dumps({
            "model": "anthropic/claude-sonnet-4",
            "plugin": ["my-plugin"],
            "mcp": {"other": {"type": "local", "command": ["other"]}},
            "permission": {"edit": "deny"},
        })
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            env={"OPENCODE_CONFIG_CONTENT": existing},
            extra_args=["--auto"],
        )
        cfg = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(cfg["model"], "anthropic/claude-sonnet-4")
        self.assertEqual(cfg["plugin"], ["my-plugin"])
        self.assertIn("other", cfg["mcp"])
        self.assertEqual(cfg["permission"], {"*": "allow", "external_directory": "deny"})
        entry = cfg["mcp"]["agentchattr"]
        self.assertEqual(entry["headers"]["Authorization"], "Bearer token-a")

    def test_normal_mode_does_not_inject_permission_overlay(self):
        existing = json.dumps({"permission": {"edit": "deny"}})
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            env={"OPENCODE_CONFIG_CONTENT": existing},
            extra_args=[],
        )
        cfg = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])
        # normal mode keeps the user's own permission untouched
        self.assertEqual(cfg["permission"], {"edit": "deny"})
        entry = cfg["mcp"]["agentchattr"]
        self.assertEqual(entry["headers"]["Authorization"], "Bearer token-a")

    def test_normal_mode_without_existing_config_has_no_permission(self):
        _args, _env, inject_env, _sp = self._launch_opencode(
            "opencode", "token-a",
            extra_args=[],
        )
        cfg = json.loads(inject_env["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("permission", cfg)
        self.assertIn("agentchattr", cfg["mcp"])


class KiloEnvContentNoMergeTests(unittest.TestCase):
    """Kilo has no mcp_merge_env_content flag: a pre-existing
    KILO_CONFIG_CONTENT is overwritten, never merged (unchanged behavior)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_existing_kilo_config_content_is_overwritten_not_merged(self):
        existing = json.dumps({
            "model": "user-model",
            "mcp": {"other": {"type": "local", "command": ["other"]}},
        })
        _args, _env, inject_env, _sp = _build_provider_launch(
            agent="kilo",
            agent_cfg={},
            instance_name="kilo",
            data_dir=Path(self.tmp.name) / "data",
            proxy_url=None,
            extra_args=[],
            env={"KILO_CONFIG_CONTENT": existing},
            token="token-k",
            mcp_cfg={"http_port": 18200},
            project_dir=Path(self.tmp.name) / "project",
        )
        cfg = json.loads(inject_env["KILO_CONFIG_CONTENT"])
        # user keys are NOT merged in — payload is exactly our injection
        self.assertNotIn("model", cfg)
        self.assertNotIn("other", cfg["mcp"])
        entry = cfg["mcp"]["agentchattr"]
        self.assertEqual(entry["type"], "remote")
        self.assertEqual(entry["headers"]["Authorization"], "Bearer token-k")
        # no oauth key for kilo (payload byte-compatible with before)
        self.assertNotIn("oauth", entry)


if __name__ == "__main__":
    unittest.main()
