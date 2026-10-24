"""Regression tests for the per-instance streamable-HTTP MCP proxy."""

import asyncio
import http.client
import json
import socket
import sys
import threading
import time
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

        response = self.server.responses.get(self.command, (405, [], b""))
        if callable(response):
            response = response(self.command, self.headers, body)
        if response == "DROP":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if response == "TRUNCATE":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0"')
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if response == "TRUNCATE_SSE":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: first-event\n\n")
            self.wfile.write(b'data: {"jsonrpc":"2.0","id":')
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if response is None:
            return
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
    do_GET = _handle


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


class ProxyRestartTests(unittest.TestCase):
    _stop_upstream = ProxyTransportTests._stop_upstream
    _request = ProxyTransportTests._request

    def setUp(self):
        ProxyTransportTests.setUp(self)
        self.proxy.stop()
        self.proxy = McpIdentityProxy(
            f"http://127.0.0.1:{self.upstream.server_address[1]}",
            "/mcp", "claude-test", "token", recovery_window=2,
            retry_interval=0.05,
        )
        self.assertTrue(self.proxy.start())
        self.generation = 1
        self.calls = []

        def post(_, headers, body):
            msg = json.loads(body)
            method = msg["method"]
            self.calls.append((self.generation, method, headers.get("Mcp-Session-Id")))
            if method == "initialize":
                return 200, [("Content-Type", "application/json"),
                             ("Mcp-Session-Id", f"session-{self.generation}")], json.dumps(
                    {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                ).encode()
            if headers.get("Mcp-Session-Id") != f"session-{self.generation}":
                return 404, [("Content-Type", "application/json")], json.dumps(
                    {"jsonrpc": "2.0", "id": "server-error",
                     "error": {"code": -32600, "message": "Session not found"}}
                ).encode()
            if method == "notifications/initialized":
                return 202, [], b""
            return 200, [("Content-Type", "application/json")], json.dumps(
                {"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}
            ).encode()

        self._post_response = post
        self.upstream.responses["POST"] = post
        self._request("POST", body=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')
        self._request("POST", body=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
                      headers={"Mcp-Session-Id": "session-1"})

    def test_idle_restart_remaps_session_and_replays_handshake(self):
        self.generation = 2
        status, _, body = self._request(
            "POST", body=b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
            headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual((status, json.loads(body)["result"]), (200, {"ok": True}))
        self.assertEqual(self.calls[-3:], [(2, "initialize", None),
                                           (2, "notifications/initialized", "session-2"),
                                           (2, "tools/list", "session-2")])

    def test_stale_session_tools_call_is_replayed_once_after_rejection(self):
        self.generation = 2
        status, _, body = self._request(
            "POST", body=b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"ping","arguments":{}}}',
            headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], {"ok": True})
        self.assertEqual(self.calls.count((2, "tools/call", "session-1")), 1)
        self.assertEqual(self.calls.count((2, "tools/call", "session-2")), 1)

    def test_reinitialize_waits_through_temporary_503(self):
        self.generation = 2
        attempts = []

        def warming(method, headers, body):
            if json.loads(body)["method"] == "initialize":
                attempts.append(1)
                if len(attempts) == 1:
                    return 503, [], b""
            return self._post_response(method, headers, body)

        self.upstream.responses["POST"] = warming
        status, _, body = self._request(
            "POST", body=b'{"jsonrpc":"2.0","id":10,"method":"tools/list"}',
            headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], {"ok": True})
        self.assertEqual(len(attempts), 2)

    def test_delete_uses_remapped_session_and_returns_client_id(self):
        self.generation = 2
        self._request("POST", body=b'{"jsonrpc":"2.0","id":8,"method":"tools/list"}',
                      headers={"Mcp-Session-Id": "session-1"})

        def delete(_, headers, body):
            self.assertEqual(headers.get("Mcp-Session-Id"), "session-2")
            return 200, [("Mcp-Session-Id", "session-2")], b""

        self.upstream.responses["DELETE"] = delete
        status, headers, _ = self._request("DELETE", headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual(status, 200)
        self.assertEqual([v for k, v in headers if k.lower() == "mcp-session-id"], ["session-1"])

    def test_upstream_refused_then_returns_within_window(self):
        self._stop_upstream()
        self.generation = 2
        results = []
        worker = threading.Thread(target=lambda: results.append(self._request(
            "POST", body=b'{"jsonrpc":"2.0","id":3,"method":"tools/list"}',
            headers={"Mcp-Session-Id": "session-1"})))
        worker.start()
        time.sleep(0.2)
        self.assertTrue(worker.is_alive())
        self.upstream = ThreadingHTTPServer(self.upstream.server_address, _StubUpstreamHandler)
        self.upstream.requests = []
        self.upstream.responses = {"POST": self._post_response}
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0][0], 200)

    def test_ambiguous_tools_call_is_never_replayed(self):
        def drop(_, headers, body):
            self.calls.append((self.generation, "tools/call", headers.get("Mcp-Session-Id")))
            return "DROP"

        self.upstream.responses["POST"] = drop
        status, _, body = self._request(
            "POST", body=b'{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"ping","arguments":{}}}',
            headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual(status, 200)
        self.assertIn("not replayed", json.loads(body)["error"]["message"])
        self.assertEqual([c for c in self.calls if c[1] == "tools/call"],
                         [(1, "tools/call", "session-1")])

    def test_partial_tools_call_response_returns_error_without_replay(self):
        def truncate(_, headers, body):
            self.calls.append((1, "tools/call", headers.get("Mcp-Session-Id")))
            return "TRUNCATE"

        self.upstream.responses["POST"] = truncate
        status, _, body = self._request(
            "POST", body=b'{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"ping","arguments":{}}}',
            headers={"Mcp-Session-Id": "session-1"})
        self.assertEqual(status, 200)
        self.assertIn("not replayed", json.loads(body)["error"]["message"])
        self.assertEqual(self.calls[-1], (1, "tools/call", "session-1"))

    def test_open_sse_stream_reconnects_after_restart(self):
        def get(_, headers, body):
            session = headers.get("Mcp-Session-Id")
            if session != f"session-{self.generation}":
                return 404, [("Content-Type", "application/json")], b'{"error":"unknown session"}'
            event = f"data: generation-{self.generation}\n\n".encode()
            if self.generation == 1:
                self.generation = 2
            return 200, [("Content-Type", "text/event-stream")], event

        self.upstream.responses["GET"] = get
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/mcp", headers={"Mcp-Session-Id": "session-1"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.readline(), b"data: generation-1\n")
        self.assertEqual(response.readline(), b"\n")
        self.assertEqual(response.readline(), b"data: generation-2\n")
        self.assertIn((2, "initialize", None), self.calls)

    def test_broken_sse_event_is_dropped_before_reconnect(self):
        first = []

        def get(_, headers, body):
            if not first:
                first.append(1)
                self.generation = 2
                return "TRUNCATE_SSE"
            if headers.get("Mcp-Session-Id") != "session-2":
                return 404, [("Content-Type", "application/json")], b'{"error":"unknown session"}'
            return 200, [("Content-Type", "text/event-stream")], b"data: second-event\n\n"

        self.upstream.responses["GET"] = get
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/mcp", headers={"Mcp-Session-Id": "session-1"})
        response = connection.getresponse()
        self.assertEqual(response.readline(), b"data: first-event\n")
        self.assertEqual(response.readline(), b"\n")
        self.assertEqual(response.readline(), b"data: second-event\n")


if __name__ == "__main__":
    unittest.main()
