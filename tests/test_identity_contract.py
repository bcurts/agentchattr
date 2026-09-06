import asyncio
import json
import os
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import config_loader
import mcp_bridge
import mcp_proxy
import wrapper
import wrapper_unix
if sys.platform == "win32":
    import wrapper_windows
from mcp.server.fastmcp import Context
from registry import RuntimeRegistry
from store import MessageStore


class FakeRequest:
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def auth_ctx(token: str) -> Context:
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    request_context = SimpleNamespace(request=request)
    return Context(request_context=request_context)


class RuntimeRegistryTests(unittest.TestCase):
    def test_registers_active_instances_and_resolves_current_name_from_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = RuntimeRegistry(data_dir=tmp)
            registry.seed({"claude": {"label": "Claude", "color": "#da7756"}})

            first = registry.register("claude")
            second = registry.register("claude")

            self.assertEqual(first["state"], "active")
            self.assertEqual(second["state"], "active")
            self.assertEqual(second["name"], "claude-2")

            resolved_first = registry.resolve_token(first["token"])
            self.assertIsNotNone(resolved_first)
            self.assertEqual(resolved_first["name"], "claude-1")
            self.assertEqual(resolved_first["identity_id"], first["identity_id"])
            self.assertEqual(resolved_first["epoch"], 1)


class McpBridgeAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.registry = RuntimeRegistry(data_dir=self.tmp.name)
        self.registry.seed(
            {
                "codex": {"label": "Codex", "color": "#10a37f"},
                "claude": {"label": "Claude", "color": "#da7756"},
            }
        )
        self.store = MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))

        mcp_bridge.store = self.store
        mcp_bridge.registry = self.registry
        mcp_bridge.decisions = None
        mcp_bridge.room_settings = {"channels": ["general"]}
        mcp_bridge.config = {"images": {"upload_dir": str(Path(self.tmp.name) / "uploads")}}
        mcp_bridge._presence.clear()
        mcp_bridge._activity.clear()
        mcp_bridge._cursors.clear()
        mcp_bridge._renamed_from.clear()

    def test_chat_send_stamps_authenticated_sender(self):
        self.registry.register("codex")
        second = self.registry.register("codex")

        result = mcp_bridge.chat_send(
            sender="claude",
            message="hello from the right identity",
            ctx=auth_ctx(second["token"]),
        )

        self.assertIn("Sent", result)
        recent = self.store.get_recent(1)
        self.assertEqual(recent[0]["sender"], second["name"])

    def test_chat_send_rejects_unauthenticated_agent_sender(self):
        self.registry.register("codex")

        result = mcp_bridge.chat_send(sender="codex", message="no auth")

        self.assertIn("authenticated agent session required", result)

    def test_stale_token_is_rejected_after_deregister(self):
        inst = self.registry.register("codex")
        self.registry.deregister(inst["name"])

        result = mcp_bridge.chat_send(
            sender=inst["name"],
            message="stale",
            ctx=auth_ctx(inst["token"]),
        )

        self.assertIn("stale or unknown authenticated agent session", result)


class AppAuthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.registry = RuntimeRegistry(data_dir=self.tmp.name)
        self.registry.seed({"claude": {"label": "Claude", "color": "#da7756"}})
        self.store = MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))

        app.registry = self.registry
        app.store = self.store
        mcp_bridge.store = self.store
        mcp_bridge.registry = self.registry
        mcp_bridge._presence.clear()
        mcp_bridge._activity.clear()
        mcp_bridge._cursors.clear()
        mcp_bridge._renamed_from.clear()

    def test_heartbeat_requires_valid_token_for_registered_agent(self):
        inst = self.registry.register("claude")

        ok = asyncio.run(
            app.heartbeat(
                "claude",
                FakeRequest(headers={"authorization": f"Bearer {inst['token']}"}),
            )
        )
        self.assertEqual(ok["name"], inst["name"])

        stale = asyncio.run(
            app.heartbeat(
                "claude",
                FakeRequest(headers={"authorization": "Bearer deadbeef"}),
            )
        )
        self.assertEqual(stale.status_code, 409)

    def test_deregister_uses_authenticated_identity_not_path_text(self):
        inst = self.registry.register("claude")

        resp = asyncio.run(
            app.deregister_agent(
                "wrong-name",
                FakeRequest(headers={"authorization": f"Bearer {inst['token']}"}),
            )
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.registry.get_instance(inst["name"]))


class WrapperLaunchTests(unittest.TestCase):
    def test_build_provider_launch_for_claude_uses_direct_server_auth(self):
        """Claude bypasses proxy — connects directly to MCP server with bearer token."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a fake project .mcp.json so _read_project_mcp_servers works
            project_dir = Path(tmp) / "project"
            project_dir.mkdir()
            (project_dir / ".mcp.json").write_text(json.dumps({
                "mcpServers": {
                    "unity-mcp": {"type": "http", "url": "http://127.0.0.1:8090/mcp"},
                    "agentchattr": {"type": "http", "url": "http://127.0.0.1:8200/mcp"},
                }
            }))

            args, env, inject_env, _ = wrapper._build_provider_launch(
                agent="claude",
                agent_cfg={},
                instance_name="claude-2",
                data_dir=Path(tmp),
                proxy_url=None,
                extra_args=["--debug"],
                env={"PATH": os.environ.get("PATH", "")},
                token="test_token_abc",
                mcp_cfg={"http_port": 8200},
                project_dir=project_dir,
            )

            self.assertEqual(args[0], "--mcp-config")
            config_path = Path(args[1])
            self.assertTrue(config_path.exists())
            payload = json.loads(config_path.read_text("utf-8"))
            # Points at real server, not proxy
            self.assertEqual(
                payload["mcpServers"]["agentchattr"]["url"],
                "http://127.0.0.1:8200/mcp",
            )
            # Bearer token in headers
            self.assertEqual(
                payload["mcpServers"]["agentchattr"]["headers"]["Authorization"],
                "Bearer test_token_abc",
            )
            # Project servers preserved (minus unauthenticated agentchattr)
            self.assertIn("unity-mcp", payload["mcpServers"])
            # Extra args preserved
            self.assertEqual(args[2], "--debug")
            self.assertEqual(env["PATH"], os.environ.get("PATH", ""))

    def test_build_provider_launch_for_gemini_uses_direct_server_auth(self):
        """Gemini bypasses proxy — connects directly to the streamable-http MCP
        server with a bearer token. Transport is http (not sse): SSE has blocking
        issues in Gemini 0.32.x, so the built-in default was switched to http."""
        with tempfile.TemporaryDirectory() as tmp:
            args, env, inject_env, _ = wrapper._build_provider_launch(
                agent="gemini",
                agent_cfg={},
                instance_name="gemini-2",
                data_dir=Path(tmp),
                proxy_url=None,
                extra_args=[],
                env={},
                token="gemini_token_xyz",
                mcp_cfg={"http_port": 8200},
            )

            self.assertEqual(args, [])
            # Settings path is in inject_env (propagated through tmux on Mac/Linux)
            self.assertIn("GEMINI_CLI_SYSTEM_SETTINGS_PATH", inject_env)
            self.assertNotIn("GEMINI_CLI_SYSTEM_SETTINGS_PATH", env)
            settings_path = Path(inject_env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
            self.assertTrue(settings_path.exists())
            payload = json.loads(settings_path.read_text("utf-8"))
            self.assertEqual(payload["mcpServers"]["agentchattr"]["type"], "http")
            # Points at the real streamable-http server, not the proxy. Gemini
            # expects the URL under "httpUrl" (not "url") for http transport.
            self.assertEqual(
                payload["mcpServers"]["agentchattr"]["httpUrl"],
                "http://127.0.0.1:8200/mcp",
            )
            # Bearer token in headers
            self.assertEqual(
                payload["mcpServers"]["agentchattr"]["headers"]["Authorization"],
                "Bearer gemini_token_xyz",
            )

    def test_build_provider_launch_for_codex_uses_config_override(self):
        args, env, inject_env, _ = wrapper._build_provider_launch(
            agent="codex",
            agent_cfg={},
            instance_name="codex-2",
            data_dir=Path(tempfile.gettempdir()),
            proxy_url="http://127.0.0.1:7777/mcp",
            extra_args=["--no-alt-screen"],
            env={"PATH": os.environ.get("PATH", "")},
        )

        self.assertEqual(args[0], "-c")
        self.assertIn('mcp_servers.agentchattr.url="http://127.0.0.1:7777/mcp"', args[1])
        self.assertEqual(args[2], "--no-alt-screen")
        self.assertIn("PATH", env)

    def test_build_provider_launch_for_grok_writes_native_toml_mcp(self):
        """Grok uses official [mcp_servers.agentchattr] TOML with env-var auth."""
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "project"
            grok_cfg = project_dir / ".grok" / "config.toml"
            grok_cfg.parent.mkdir(parents=True)
            grok_cfg.write_text(
                '[mcp_servers.other]\n'
                'url = "http://127.0.0.1:9999/mcp"\n'
                "enabled = true\n",
                "utf-8",
            )
            token = "grok_tok_" + os.urandom(8).hex()
            port = 8233
            args, env, inject_env, settings_path = wrapper._build_provider_launch(
                agent="grok",
                agent_cfg={},
                instance_name="grok-2",
                data_dir=Path(tmp),
                proxy_url=None,
                extra_args=["--debug"],
                env={"PATH": os.environ.get("PATH", "")},
                token=token,
                mcp_cfg={"http_port": port},
                project_dir=project_dir,
            )

            self.assertIsNotNone(settings_path)
            self.assertTrue(settings_path.exists())
            self.assertEqual(settings_path, grok_cfg)
            text = settings_path.read_text("utf-8")
            self.assertNotIn(token, text)
            payload = tomllib.loads(text)
            server = payload["mcp_servers"]["agentchattr"]
            self.assertEqual(server["url"], f"http://127.0.0.1:{port}/mcp")
            self.assertTrue(server["enabled"])
            env_var = server.get("bearer_token_env_var")
            self.assertEqual(env_var, wrapper.GROK_MCP_TOKEN_ENV)
            self.assertNotIn("headers", server)
            self.assertEqual(inject_env.get(env_var), token)
            self.assertNotEqual(env.get(env_var), token)
            self.assertEqual(
                payload["mcp_servers"]["other"]["url"],
                "http://127.0.0.1:9999/mcp",
            )
            self.assertEqual(args, ["--debug"])
            self.assertEqual(env["PATH"], os.environ.get("PATH", ""))

    def test_committed_config_registers_grok_cli(self):
        config = config_loader.load_config(ROOT)
        self.assertIn("grok", config["agents"])
        grok = config["agents"]["grok"]
        self.assertEqual(grok["command"], "grok")
        self.assertNotIn("mcp_inject", grok)
        self.assertNotIn("mcp_settings_path", grok)
        self.assertNotIn("mcp_settings_format", grok)
        self.assertNotIn("mcp_transport", grok)

    def test_mcp_identity_maps_grok_build_to_base_grok(self):
        text = mcp_bridge._MCP_INSTRUCTIONS
        self.assertIn("Grok Build", text)
        self.assertIn('base: "grok"', text)

    def test_grok_launch_does_not_write_trust_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / ".grok").mkdir(parents=True)
            trust = home / ".grok" / "trusted_folders.toml"
            original = "[folders.'C:\\\\already']\ntrusted = true\n"
            trust.write_text(original, "utf-8")
            project_dir = Path(tmp) / "project"
            project_dir.mkdir()
            with mock.patch.object(wrapper.Path, "home", return_value=home):
                wrapper._build_provider_launch(
                    agent="grok",
                    agent_cfg={},
                    instance_name="grok-2",
                    data_dir=Path(tmp) / "data",
                    proxy_url=None,
                    extra_args=[],
                    env={},
                    token="tok_" + os.urandom(4).hex(),
                    mcp_cfg={"http_port": 8233},
                    project_dir=project_dir,
                )
            self.assertEqual(trust.read_text("utf-8"), original)


class WrapperUnixLifecycleTests(unittest.TestCase):
    def test_detach_keeps_wrapper_alive_until_tmux_session_ends(self):
        commands = []
        sleep_calls = []
        has_session_states = iter([0, 0, 1])

        def fake_run(args, **kwargs):
            commands.append(args)
            if args[:2] == ["tmux", "has-session"]:
                return SimpleNamespace(returncode=next(has_session_states))
            return SimpleNamespace(returncode=0, stdout=b"")

        with (
            mock.patch.object(wrapper_unix, "_check_tmux"),
            mock.patch.object(wrapper_unix.subprocess, "run", side_effect=fake_run),
            mock.patch.object(
                wrapper_unix.time,
                "sleep",
                side_effect=lambda seconds: sleep_calls.append(seconds),
            ),
        ):
            wrapper_unix.run_agent(
                command="claude",
                extra_args=["--debug"],
                cwd=".",
                env={},
                queue_file=Path("queue.jsonl"),
                agent="claude",
                no_restart=False,
                start_watcher=lambda inject_fn: None,
                session_name="agentchattr-claude-1",
            )

        has_session_checks = [
            args for args in commands if args[:2] == ["tmux", "has-session"]
        ]
        self.assertEqual(len(has_session_checks), 3)
        self.assertEqual(sleep_calls, [1])


@unittest.skipUnless(sys.platform == "win32", "Windows-only wrapper compatibility test")
class WrapperWindowsCompatibilityTests(unittest.TestCase):
    def test_run_agent_accepts_session_name_kwarg(self):
        fake_proc = mock.Mock()
        fake_proc.pid = 1234
        fake_proc.returncode = 0
        fake_proc.wait.return_value = 0

        with mock.patch.object(wrapper_windows.subprocess, "Popen", return_value=fake_proc):
            wrapper_windows.run_agent(
                command="codex",
                extra_args=[],
                cwd=".",
                env={},
                queue_file=Path("queue.jsonl"),
                agent="codex",
                no_restart=True,
                start_watcher=lambda inject_fn: None,
                session_name=None,
            )


class ProxyHeaderForwardingTests(unittest.TestCase):
    def test_post_forwards_session_and_content_type_headers_case_insensitively(self):
        seen = {}

        class UpstreamHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                seen["authorization"] = self.headers.get("Authorization")
                seen["agent_token"] = self.headers.get("X-Agent-Token")
                body = b'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n\r\n'
                self.send_response(200)
                # Match the lowercase header names emitted by the upstream FastMCP app.
                self.send_header("content-type", "text/event-stream")
                self.send_header("mcp-session-id", "session-123")
                self.send_header("cache-control", "no-cache, no-transform")
                self.end_headers()
                self.wfile.write(body)

        upstream = HTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        proxy = mcp_proxy.McpIdentityProxy(
            upstream_base=f"http://127.0.0.1:{upstream.server_address[1]}",
            upstream_path="/mcp",
            agent_name="claude-7",
            instance_token="token-123",
        )
        self.assertTrue(proxy.start())

        try:
            req = Request(
                f"{proxy.url}/mcp",
                data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")
                self.assertEqual(resp.headers.get("Mcp-Session-Id"), "session-123")
                self.assertEqual(resp.headers.get("Cache-Control"), "no-cache, no-transform")
                self.assertIn('"ok":true', body)

            self.assertEqual(seen["authorization"], "Bearer token-123")
            self.assertEqual(seen["agent_token"], "token-123")
        finally:
            proxy.stop()
            upstream.shutdown()
            upstream.server_close()

    def test_get_forwards_upstream_http_errors_instead_of_hanging(self):
        seen = {}

        class UpstreamHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                seen["authorization"] = self.headers.get("Authorization")
                seen["agent_token"] = self.headers.get("X-Agent-Token")
                body = b'{"error":"missing discovery metadata"}'
                self.send_response(404)
                self.send_header("content-type", "application/json")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

        upstream = HTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        proxy = mcp_proxy.McpIdentityProxy(
            upstream_base=f"http://127.0.0.1:{upstream.server_address[1]}",
            upstream_path="/mcp",
            agent_name="claude",
            instance_token="token-456",
        )
        self.assertTrue(proxy.start())

        try:
            req = Request(
                f"{proxy.url}/.well-known/oauth-authorization-server",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with self.assertRaises(HTTPError) as cm:
                urlopen(req, timeout=10)

            err = cm.exception
            self.assertEqual(getattr(err, "code", None), 404)
            self.assertEqual(err.headers.get("Content-Type"), "application/json")
            self.assertEqual(err.headers.get("Cache-Control"), "no-cache")
            self.assertIn("missing discovery metadata", err.read().decode("utf-8"))
            self.assertEqual(seen["authorization"], "Bearer token-456")
            self.assertEqual(seen["agent_token"], "token-456")
        finally:
            proxy.stop()
            upstream.shutdown()
            upstream.server_close()


class ProxyDisconnectNoiseTests(unittest.TestCase):
    def test_benign_disconnects_are_suppressed(self):
        self.assertTrue(mcp_proxy._is_benign_client_disconnect(BrokenPipeError()))
        self.assertTrue(mcp_proxy._is_benign_client_disconnect(ConnectionResetError()))
        generic_os_error = OSError()
        self.assertFalse(mcp_proxy._is_benign_client_disconnect(generic_os_error))
        win_disconnect = OSError()
        win_disconnect.winerror = 10054
        self.assertTrue(
            mcp_proxy._is_benign_client_disconnect(win_disconnect)
        )
        self.assertFalse(mcp_proxy._is_benign_client_disconnect(ValueError()))


class AgentLastChannelRenameTests(unittest.TestCase):
    """Disconnect messages route to each agent's own last-active channel; that
    mapping must survive renames or the leave lands in the wrong channel."""

    def setUp(self):
        self._saved = dict(app._agent_last_channel)
        app._agent_last_channel.clear()

    def tearDown(self):
        app._agent_last_channel.clear()
        app._agent_last_channel.update(self._saved)

    def test_migrate_rekeys_old_to_new(self):
        app._agent_last_channel["claude"] = "bugfixing"
        app._migrate_agent_last_channel("claude", "claude-1")
        # New name inherits the channel; a later disconnect for "claude-1"
        # now resolves to #bugfixing instead of the global fallback.
        self.assertEqual(app._agent_last_channel.get("claude-1"), "bugfixing")
        self.assertNotIn("claude", app._agent_last_channel)

    def test_migrate_noop_when_no_prior_channel(self):
        # Renaming an agent that never spoke must not fabricate an entry.
        app._migrate_agent_last_channel("ghost", "ghost-1")
        self.assertNotIn("ghost", app._agent_last_channel)
        self.assertNotIn("ghost-1", app._agent_last_channel)

    def test_migrate_same_name_is_noop(self):
        app._agent_last_channel["codex"] = "general"
        app._migrate_agent_last_channel("codex", "codex")
        self.assertEqual(app._agent_last_channel.get("codex"), "general")

    def test_renamed_back_preserves_channel(self):
        # e.g. "claude-1" -> "claude" when the other instance leaves.
        app._agent_last_channel["claude-1"] = "design"
        app._migrate_agent_last_channel("claude-1", "claude")
        self.assertEqual(app._agent_last_channel.get("claude"), "design")
        self.assertNotIn("claude-1", app._agent_last_channel)


if __name__ == "__main__":
    unittest.main()
