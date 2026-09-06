"""Tests for wrapper.py MCP config writers.

Focused on the shape of the JSON written to provider settings files — Gemini
needs "httpUrl", CodeBuddy needs "url", legacy paths still work.
"""

import json
import os
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wrapper import (  # noqa: E402
    GROK_MCP_TOKEN_ENV,
    _build_provider_launch,
    _write_grok_mcp_toml,
    _write_json_mcp_settings,
)


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


class GrokTomlMcpSettingsTests(unittest.TestCase):
    """Grok-native TOML writer: merge-only [mcp_servers.agentchattr], env-var auth."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / ".grok" / "config.toml"

    def test_writes_url_enabled_and_bearer_env_var(self):
        url = "http://127.0.0.1:8244/mcp"
        path = _write_grok_mcp_toml(self.target, url)
        text = path.read_text("utf-8")
        payload = tomllib.loads(text)
        server = payload["mcp_servers"]["agentchattr"]
        self.assertEqual(server["url"], url)
        self.assertTrue(server["enabled"])
        self.assertEqual(server["bearer_token_env_var"], GROK_MCP_TOKEN_ENV)
        self.assertNotIn("headers", server)
        self.assertNotIn("Bearer ", text)

    def test_merge_preserves_unrelated_mcp_servers(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(
            "# keep this comment and the other server\n"
            "[mcp_servers.linear]\n"
            'url = "https://mcp.linear.app/mcp"\n'
            "enabled = true\n",
            "utf-8",
        )
        _write_grok_mcp_toml(self.target, "http://127.0.0.1:8244/mcp")
        text = self.target.read_text("utf-8")
        self.assertIn("keep this comment", text)
        payload = tomllib.loads(text)
        self.assertEqual(
            payload["mcp_servers"]["linear"]["url"],
            "https://mcp.linear.app/mcp",
        )
        self.assertEqual(
            payload["mcp_servers"]["agentchattr"]["url"],
            "http://127.0.0.1:8244/mcp",
        )

    def test_rewrite_replaces_only_agentchattr_block(self):
        _write_grok_mcp_toml(self.target, "http://127.0.0.1:1111/mcp")
        _write_grok_mcp_toml(self.target, "http://127.0.0.1:2222/mcp")
        payload = tomllib.loads(self.target.read_text("utf-8"))
        server = payload["mcp_servers"]["agentchattr"]
        self.assertEqual(server["url"], "http://127.0.0.1:2222/mcp")
        self.assertEqual(server["bearer_token_env_var"], GROK_MCP_TOKEN_ENV)
        self.assertEqual(list(payload["mcp_servers"]), ["agentchattr"])

    def test_spaced_table_header_is_replaced_not_duplicated(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(
            "[ mcp_servers.agentchattr ]\n"
            'url = "http://127.0.0.1:1111/mcp"\n'
            "enabled = true\n",
            "utf-8",
        )
        _write_grok_mcp_toml(self.target, "http://127.0.0.1:2222/mcp")
        payload = tomllib.loads(self.target.read_text("utf-8"))
        self.assertEqual(
            payload["mcp_servers"]["agentchattr"]["url"],
            "http://127.0.0.1:2222/mcp",
        )
        self.assertEqual(list(payload["mcp_servers"]), ["agentchattr"])

    def test_invalid_existing_toml_is_not_overwritten(self):
        self.target.parent.mkdir(parents=True)
        garbage = "this is not toml [[[\n"
        self.target.write_text(garbage, "utf-8")
        with self.assertRaises(ValueError):
            _write_grok_mcp_toml(self.target, "http://127.0.0.1:2222/mcp")
        self.assertEqual(self.target.read_text("utf-8"), garbage)

    def test_non_table_mcp_servers_is_not_overwritten(self):
        self.target.parent.mkdir(parents=True)
        original = 'mcp_servers = "keep-me"\n'
        self.target.write_text(original, "utf-8")
        with self.assertRaises(ValueError) as cm:
            _write_grok_mcp_toml(self.target, "http://127.0.0.1:2222/mcp")
        self.assertIn("mcp_servers", str(cm.exception))
        self.assertEqual(self.target.read_text("utf-8"), original)

    def test_concurrent_writes_do_not_share_temp_file(self):
        self.target.parent.mkdir(parents=True)
        errors: list[BaseException] = []
        n = 8
        # Hold every writer at its first os.replace so unique temps exist
        # together. A shared config.toml.tmp then fails FileNotFoundError
        # instead of depending on a natural race.
        barrier = threading.Barrier(n)
        first_temps: list[str] = []
        lock = threading.Lock()
        waited = threading.local()
        real_replace = os.replace

        def gated_replace(src, dst, *args, **kwargs):
            if not getattr(waited, "done", False):
                waited.done = True
                with lock:
                    first_temps.append(os.path.normpath(str(src)))
                barrier.wait(timeout=5)
            return real_replace(src, dst, *args, **kwargs)

        def worker(i: int) -> None:
            try:
                _write_grok_mcp_toml(
                    self.target, f"http://127.0.0.1:{8100 + i}/mcp"
                )
            except Exception as exc:
                errors.append(exc)

        with mock.patch("wrapper.os.replace", gated_replace):
            threads = [
                threading.Thread(target=worker, args=(i,)) for i in range(n)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(first_temps), n)
        self.assertEqual(len(set(first_temps)), n, first_temps)
        payload = tomllib.loads(self.target.read_text("utf-8"))
        self.assertIn("agentchattr", payload["mcp_servers"])
        self.assertTrue(payload["mcp_servers"]["agentchattr"]["enabled"])

    def test_generic_settings_file_toml_is_not_grok_writer(self):
        """A custom settings_file path ending in .toml must stay JSON, not Grok TOML."""
        target = Path(self.tmp.name) / "custom.toml"
        token = "secret-token-not-for-disk-shape"
        _, _, _, settings_path = _build_provider_launch(
            agent="customcli",
            agent_cfg={
                "mcp_inject": "settings_file",
                "mcp_settings_path": str(target),
                "mcp_transport": "http",
                "mcp_http_key": "url",
            },
            instance_name="customcli-1",
            data_dir=Path(self.tmp.name),
            proxy_url=None,
            extra_args=[],
            env={},
            token=token,
            mcp_cfg={"http_port": 8244},
        )
        raw = settings_path.read_text("utf-8")
        data = json.loads(raw)
        self.assertIn("mcpServers", data)
        self.assertNotIn("[mcp_servers.agentchattr]", raw)
        self.assertNotIn("bearer_token_env_var", raw)

    def test_settings_file_grok_format_uses_native_toml_writer(self):
        """Explicit mcp_settings_format=grok_toml (settings_file entry) writes TOML."""
        target = Path(self.tmp.name) / "proj" / ".grok" / "config.toml"
        token = "tok-format-" + os.urandom(4).hex()
        _, _, inject_env, settings_path = _build_provider_launch(
            agent="customcli",
            agent_cfg={
                "mcp_inject": "settings_file",
                "mcp_settings_path": str(target),
                "mcp_settings_format": "grok_toml",
            },
            instance_name="customcli-1",
            data_dir=Path(self.tmp.name),
            proxy_url=None,
            extra_args=[],
            env={},
            token=token,
            mcp_cfg={"http_port": 8244},
        )
        text = settings_path.read_text("utf-8")
        self.assertNotIn(token, text)
        payload = tomllib.loads(text)
        server = payload["mcp_servers"]["agentchattr"]
        self.assertEqual(server["url"], "http://127.0.0.1:8244/mcp")
        self.assertEqual(server["bearer_token_env_var"], GROK_MCP_TOKEN_ENV)
        self.assertEqual(inject_env.get(GROK_MCP_TOKEN_ENV), token)

    def test_quoted_table_header_is_replaced_not_duplicated(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(
            '[mcp_servers."agentchattr"]\n'
            'url = "http://127.0.0.1:1111/mcp"\n'
            "enabled = true\n",
            "utf-8",
        )
        _write_grok_mcp_toml(self.target, "http://127.0.0.1:2222/mcp")
        payload = tomllib.loads(self.target.read_text("utf-8"))
        self.assertEqual(
            payload["mcp_servers"]["agentchattr"]["url"],
            "http://127.0.0.1:2222/mcp",
        )
        self.assertEqual(list(payload["mcp_servers"]), ["agentchattr"])

    def test_inline_table_on_other_server_is_preserved(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(
            "[mcp_servers.other]\n"
            'url = "http://127.0.0.1:9999/mcp"\n'
            'headers = { Authorization = "Bearer keep-me" }\n'
            "[mcp_servers.agentchattr]\n"
            'url = "http://127.0.0.1:1111/mcp"\n'
            'headers = { Authorization = "Bearer old-secret" }\n',
            "utf-8",
        )
        _write_grok_mcp_toml(self.target, "http://127.0.0.1:2222/mcp")
        payload = tomllib.loads(self.target.read_text("utf-8"))
        other = payload["mcp_servers"]["other"]
        self.assertEqual(other["headers"]["Authorization"], "Bearer keep-me")
        server = payload["mcp_servers"]["agentchattr"]
        self.assertEqual(server["url"], "http://127.0.0.1:2222/mcp")
        self.assertNotIn("headers", server)
        self.assertNotIn("old-secret", self.target.read_text("utf-8"))

    def test_unknown_settings_format_is_rejected(self):
        target = Path(self.tmp.name) / "settings.yaml"
        with self.assertRaises(ValueError) as cm:
            _build_provider_launch(
                agent="customcli",
                agent_cfg={
                    "mcp_inject": "settings_file",
                    "mcp_settings_path": str(target),
                    "mcp_settings_format": "yaml",
                },
                instance_name="customcli-1",
                data_dir=Path(self.tmp.name),
                proxy_url=None,
                extra_args=[],
                env={},
                token="tok",
                mcp_cfg={"http_port": 8244},
            )
        self.assertIn("mcp_settings_format", str(cm.exception))
        self.assertFalse(target.exists())

    def test_blank_settings_format_is_not_json_default(self):
        """Explicit empty/false format must not fall through to JSON."""
        for i, bad in enumerate(("", False)):
            with self.subTest(fmt=bad):
                target = Path(self.tmp.name) / f"blank-format-{i}.json"
                with self.assertRaises(ValueError) as cm:
                    _build_provider_launch(
                        agent="customcli",
                        agent_cfg={
                            "mcp_inject": "settings_file",
                            "mcp_settings_path": str(target),
                            "mcp_settings_format": bad,
                        },
                        instance_name="customcli-1",
                        data_dir=Path(self.tmp.name),
                        proxy_url=None,
                        extra_args=[],
                        env={},
                        token="tok",
                        mcp_cfg={"http_port": 8244},
                    )
                self.assertIn("mcp_settings_format", str(cm.exception))
                self.assertFalse(target.exists())

    def test_json_settings_file_keeps_url_key_and_env_path(self):
        project = Path(self.tmp.name) / "proj"
        project.mkdir()
        token = "json-tok-" + os.urandom(4).hex()
        _, _, inject_env, settings_path = _build_provider_launch(
            agent="customcli",
            agent_cfg={
                "mcp_inject": "settings_file",
                "mcp_settings_path": ".qwen/settings.json",
                "mcp_env_var": "MYCLI_MCP_SETTINGS",
                "mcp_transport": "http",
                "mcp_http_key": "url",
            },
            instance_name="customcli-1",
            data_dir=Path(self.tmp.name),
            proxy_url=None,
            extra_args=[],
            env={},
            token=token,
            mcp_cfg={"http_port": 8244},
            project_dir=project,
        )
        self.assertEqual(settings_path, project / ".qwen" / "settings.json")
        self.assertEqual(inject_env["MYCLI_MCP_SETTINGS"], str(settings_path))
        entry = json.loads(settings_path.read_text("utf-8"))["mcpServers"]["agentchattr"]
        self.assertEqual(entry["url"], "http://127.0.0.1:8244/mcp")
        self.assertNotIn("httpUrl", entry)
        self.assertEqual(entry["headers"]["Authorization"], f"Bearer {token}")

    def test_json_settings_file_expands_tilde_path(self):
        home = Path(self.tmp.name) / "home"

        def expanduser(p):
            p = os.fspath(p)
            if p.startswith("~"):
                return str(home / p[1:].lstrip("/\\"))
            return p

        with mock.patch("os.path.expanduser", side_effect=expanduser):
            _, _, inject_env, settings_path = _build_provider_launch(
                agent="customcli",
                agent_cfg={
                    "mcp_inject": "settings_file",
                    "mcp_settings_path": "~/.codebuddy/.mcp.json",
                    "mcp_env_var": "CODEBUDDY_MCP",
                    "mcp_http_key": "url",
                },
                instance_name="customcli-1",
                data_dir=Path(self.tmp.name),
                proxy_url=None,
                extra_args=[],
                env={},
                token="tilde-tok",
                mcp_cfg={"http_port": 8244},
            )
        self.assertEqual(settings_path, home / ".codebuddy" / ".mcp.json")
        self.assertTrue(settings_path.exists())
        self.assertEqual(inject_env["CODEBUDDY_MCP"], str(settings_path))


if __name__ == "__main__":
    unittest.main()
