"""Regression tests for the per-instance streamable-HTTP MCP proxy."""

import asyncio
import http.client
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_proxy import McpIdentityProxy  # noqa: E402


class _StubUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.server.requests.append((self.command, self.headers, body))

        response = self.server.responses[self.command]
        if callable(response):
            response = response(self.command, self.headers, body)
        status, headers, response_body = response
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)

    do_POST = _handle
    do_DELETE = _handle


class ProxyTransportTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _StubUpstreamHandler)
        self.upstream.requests = []
        self.upstream.responses = {}
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever,
            daemon=True,
        )
        self.upstream_thread.start()
        self.addCleanup(self._stop_upstream)

        upstream_url = f"http://127.0.0.1:{self.upstream.server_address[1]}"
        self.proxy = McpIdentityProxy(
            upstream_url,
            "/mcp",
            "claude-test",
            "nonsecret-test-token",
        )
        self.assertTrue(self.proxy.start())
        self.addCleanup(self.proxy.stop)

    def _stop_upstream(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)

    def _request(self, method, *, body=b"", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=2)
        self.addCleanup(connection.close)
        connection.request(method, "/mcp", body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheaders(), response.read()

    def test_initialize_response_has_one_case_insensitive_session_header(self):
        self.upstream.responses["POST"] = (
            200,
            [
                ("Content-Type", "application/json"),
                ("Mcp-Session-Id", "session-123"),
            ],
            b'{"jsonrpc":"2.0","id":1,"result":{}}',
        )

        _, headers, _ = self._request(
            "POST",
            body=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
            headers={"Content-Type": "application/json"},
        )

        session_headers = [
            value for name, value in headers if name.lower() == "mcp-session-id"
        ]
        self.assertEqual(session_headers, ["session-123"])

    def test_follow_up_post_preserves_client_session_id(self):
        self.upstream.responses["POST"] = (
            202,
            [("Content-Type", "application/json")],
            b"",
        )

        self._request(
            "POST",
            body=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
            headers={
                "Content-Type": "application/json",
                "Mcp-Session-Id": "session-123",
            },
        )

        _, upstream_headers, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_headers.get("Mcp-Session-Id"), "session-123")

    def test_delete_relays_upstream_success_body_and_session_header(self):
        self.upstream.responses["DELETE"] = (
            200,
            [
                ("Content-Type", "application/json"),
                ("Mcp-Session-Id", "session-123"),
            ],
            b'{"closed":true}',
        )

        status, headers, body = self._request(
            "DELETE",
            headers={"Mcp-Session-Id": "session-123"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"closed":true}')
        self.assertEqual(
            [value for name, value in headers if name.lower() == "mcp-session-id"],
            ["session-123"],
        )
        _, upstream_headers, _ = self.upstream.requests[-1]
        self.assertEqual(upstream_headers.get("Mcp-Session-Id"), "session-123")
        self.assertEqual(
            upstream_headers.get("Authorization"),
            "Bearer nonsecret-test-token",
        )
        self.assertEqual(
            upstream_headers.get("X-Agent-Token"),
            "nonsecret-test-token",
        )

    def test_delete_relays_upstream_http_error(self):
        self.upstream.responses["DELETE"] = (
            404,
            [
                ("Content-Type", "application/json"),
                ("Mcp-Session-Id", "session-123"),
            ],
            b'{"error":"unknown session"}',
        )

        status, headers, body = self._request(
            "DELETE",
            headers={"Mcp-Session-Id": "session-123"},
        )

        self.assertEqual(status, 404)
        self.assertEqual(body, b'{"error":"unknown session"}')
        self.assertEqual(
            [value for name, value in headers if name.lower() == "mcp-session-id"],
            ["session-123"],
        )

    def test_sdk_initialize_notification_and_tools_list_through_proxy(self):
        def respond_to_post(_, headers, body):
            request = json.loads(body)
            method = request.get("method")
            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "serverInfo": {"name": "stub-upstream", "version": "1.0"},
                    },
                }
                return (
                    200,
                    [
                        ("Content-Type", "application/json"),
                        ("Mcp-Session-Id", "session-123"),
                    ],
                    json.dumps(response).encode(),
                )
            if method == "notifications/initialized":
                return 202, [], b""
            if method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "ping",
                                "description": "Return pong.",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    },
                }
                return 200, [("Content-Type", "application/json")], json.dumps(response).encode()
            raise AssertionError(f"Unexpected SDK request: {method}")

        self.upstream.responses["POST"] = respond_to_post
        self.upstream.responses["DELETE"] = (200, [], b"")

        async def exercise_lifecycle():
            async with streamable_http_client(
                f"{self.proxy.url}/mcp",
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return [tool.name for tool in tools.tools]

        self.assertEqual(asyncio.run(exercise_lifecycle()), ["ping"])
        post_requests = [
            (json.loads(body).get("method"), headers.get("Mcp-Session-Id"))
            for method, headers, body in self.upstream.requests
            if method == "POST"
        ]
        self.assertEqual(
            post_requests,
            [
                ("initialize", None),
                ("notifications/initialized", "session-123"),
                ("tools/list", "session-123"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
