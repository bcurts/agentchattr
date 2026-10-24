"""Per-instance MCP identity proxy.

Sits between an agent CLI and the real agentchattr MCP server.
Intercepts tool calls and stamps the `sender`/`name` argument
from the agent's registered identity while forwarding the
server-issued bearer token, so agents never need to know
their own name or auth material.

Supports both transports:
  - streamable-http (Claude, Codex, Qwen): POST /mcp, GET /mcp, DELETE /mcp
  - SSE (Gemini): GET /sse → event stream, POST /messages/ → tool calls

Usage (from wrapper.py):
    proxy = McpIdentityProxy(
        upstream_base="http://127.0.0.1:8200",
        upstream_path="/mcp",
        agent_name="claude-prime",
        instance_token="abc123...",
    )
    proxy.start()          # non-blocking — runs in a daemon thread
    proxy_url = proxy.url  # e.g. "http://127.0.0.1:54321"
    ...
    proxy.stop()
"""

import json
import errno
from http.client import HTTPException
import re
import threading
import logging
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

log = logging.getLogger(__name__)

# MCP tools and which parameter carries the agent identity
_SENDER_PARAMS = {
    "chat_send": "sender",
    "chat_read": "sender",
    "chat_resync": "sender",
    "chat_join": "name",
    "chat_who": None,          # no sender param
    "chat_decision": "sender",
    "chat_channels": None,
    "chat_set_hat": "sender",
    "chat_claim": "sender",
}


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each request in a new thread.
    Required for SSE: GET holds the stream open while POSTs arrive concurrently."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if _is_benign_client_disconnect(exc):
            return
        super().handle_error(request, client_address)


def _is_benign_client_disconnect(exc: BaseException | None) -> bool:
    """Return True for normal client disconnects that should not spam stderr."""
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "winerror", None) in {64, 995, 10053, 10054}
    return False


def _connection_refused(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return (isinstance(reason, ConnectionRefusedError)
            or getattr(reason, "errno", None) == errno.ECONNREFUSED
            or getattr(reason, "winerror", None) == 10061)


def _session_not_found(status: int, body: bytes) -> bool:
    """The MCP server rejects unknown sessions before dispatching requests."""
    if status != 404:
        return False
    try:
        error = json.loads(body).get("error", {})
    except (ValueError, AttributeError):
        return False
    return (isinstance(error, dict)
            and error.get("code") in (-32600, -32001)
            and error.get("message") == "Session not found")


class McpIdentityProxy:
    """Local HTTP proxy that stamps agent identity on MCP tool calls.

    Args:
        upstream_base: Base URL without path, e.g. "http://127.0.0.1:8200"
        upstream_path: Path prefix for the transport, e.g. "/mcp" or "/sse"
        agent_name: Current canonical name for this instance
        instance_token: Server-issued token (forwarded as Authorization: Bearer)
    """

    def __init__(self, upstream_base: str, upstream_path: str,
                 agent_name: str, instance_token: str, port: int = 0,
                 recovery_window: float = 45, retry_interval: float = 0.2):
        self._upstream_base = upstream_base.rstrip("/")
        self._upstream_path = upstream_path
        self._agent_name = agent_name
        self._token = instance_token
        self._port = port  # 0 = OS-assigned (legacy), >0 = fixed
        self._lock = threading.Lock()
        self._session_lock = threading.RLock()
        self._sessions = {}  # client session -> upstream session and original handshake
        self._recovery_window = recovery_window
        self._retry_interval = retry_interval
        self._server: _ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server:
            return self._server.server_address[1]
        return 0

    @property
    def url(self) -> str:
        """Base URL of the proxy (no path — clients add /mcp or /sse themselves)."""
        return f"http://127.0.0.1:{self.port}"

    @property
    def agent_name(self) -> str:
        with self._lock:
            return self._agent_name

    @agent_name.setter
    def agent_name(self, name: str):
        with self._lock:
            self._agent_name = name

    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    @token.setter
    def token(self, value: str):
        with self._lock:
            self._token = value

    def start(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # silence request logs

            def _upstream_url(self, path: str | None = None) -> str:
                """Build upstream URL, preserving the request path."""
                p = path if path else self.path
                return f"{proxy._upstream_base}{p}"

            def _send_response_headers(self, headers, client_session=None):
                for key in (
                    "Content-Type",
                    "Mcp-Session-Id",
                    "Cache-Control",
                    "X-Accel-Buffering",
                ):
                    val = client_session if key == "Mcp-Session-Id" and client_session else headers.get(key)
                    if val:
                        self.send_header(key, val)

            def _upstream_request(self, method, body=b"", headers=None, timeout=30,
                                  session=None):
                req = Request(self._upstream_url(), data=body if method == "POST" else None,
                              method=method)
                for hdr, val in (headers or {}).items():
                    if hdr.lower() not in ("content-length", "host", "authorization",
                                           "x-agent-token", "mcp-session-id", "connection"):
                        req.add_header(hdr, val)
                if session:
                    req.add_header("Mcp-Session-Id", session)
                req.add_header("Authorization", f"Bearer {proxy.token}")
                req.add_header("X-Agent-Token", proxy.token)
                try:
                    return urlopen(req, timeout=timeout)
                except HTTPError as e:
                    return e

            def _retry_refused(self, operation, deadline):
                delay = proxy._retry_interval
                while True:
                    try:
                        return operation()
                    except (URLError, OSError) as exc:
                        if not _connection_refused(exc) or time.monotonic() >= deadline:
                            raise
                        time.sleep(min(delay, max(0, deadline - time.monotonic())))
                        delay = min(delay * 2, 2.0)

            def _recover_session(self, client_session, old_upstream, deadline):
                with proxy._session_lock:
                    state = proxy._sessions.get(client_session)
                if not state:
                    return False
                with state["lock"]:
                    if state["upstream"] != old_upstream:
                        return True  # another request already recovered it
                    def handshake(body, headers, session=None):
                        delay = proxy._retry_interval
                        while True:
                            response = self._retry_refused(
                                lambda: self._upstream_request("POST", body, headers,
                                                               session=session), deadline)
                            if response.status < 500 or time.monotonic() >= deadline:
                                return response
                            response.close()
                            time.sleep(min(delay, max(0, deadline - time.monotonic())))
                            delay = min(delay * 2, 2.0)

                    response = handshake(state["init"], state["init_headers"])
                    try:
                        if response.status >= 400:
                            return False
                        new_session = response.headers.get("Mcp-Session-Id")
                        response.read()
                    finally:
                        response.close()
                    if not new_session:
                        return False
                    if state["initialized"] is not None:
                        response = handshake(state["initialized"],
                                             state["initialized_headers"], new_session)
                        try:
                            if response.status >= 400:
                                return False
                            response.read()
                        finally:
                            response.close()
                    state["upstream"] = new_session
                    return True

            def _send_buffered(self, response, client_session=None, body=None):
                try:
                    if body is None:
                        body = response.read()
                    self.send_response(response.status)
                    self._send_response_headers(response.headers, client_session)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if body:
                        self.wfile.write(body)
                finally:
                    response.close()

            def _send_rpc_error(self, raw, message):
                try:
                    request_id = json.loads(raw).get("id")
                except (ValueError, AttributeError):
                    request_id = None
                payload = json.dumps({"jsonrpc": "2.0", "id": request_id,
                                      "error": {"code": -32000, "message": message}}).encode()
                self.send_response(200 if request_id is not None else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                body = self._maybe_inject_sender(raw)
                headers = dict(self.headers.items())
                client_session = self.headers.get("Mcp-Session-Id")
                try:
                    method = json.loads(raw).get("method")
                except (ValueError, AttributeError):
                    method = None
                deadline = time.monotonic() + proxy._recovery_window
                for attempt in range(2):
                    with proxy._session_lock:
                        state = proxy._sessions.get(client_session)
                        upstream_session = state["upstream"] if state else client_session
                    try:
                        response = self._retry_refused(
                            lambda: self._upstream_request("POST", body, headers,
                                                           timeout=300 if method == "tools/call" else 30,
                                                           session=upstream_session), deadline)
                    except (URLError, OSError, HTTPException) as exc:
                        self._send_rpc_error(raw, "Upstream transport failed; request not replayed: " + str(exc))
                        return
                    error_body = None
                    if response.status == 404 and state and method != "initialize" and attempt == 0:
                        try:
                            error_body = response.read()
                        except (URLError, OSError, HTTPException) as exc:
                            response.close()
                            self._send_rpc_error(raw, "Upstream response broke; request not replayed: " + str(exc))
                            return
                    if error_body is not None and _session_not_found(response.status, error_body):
                        try:
                            recovered = self._recover_session(client_session, upstream_session, deadline)
                        except (URLError, OSError, HTTPException):
                            recovered = False
                        if recovered:
                            response.close()
                            continue
                    if method == "initialize" and response.status < 400:
                        new_session = response.headers.get("Mcp-Session-Id")
                        if new_session:
                            with proxy._session_lock:
                                proxy._sessions[new_session] = {
                                    "upstream": new_session, "init": raw, "init_headers": headers,
                                    "initialized": None, "initialized_headers": None,
                                    "lock": threading.Lock(),
                                }
                    if method == "notifications/initialized" and state and response.status < 400:
                        with proxy._session_lock:
                            state["initialized"] = raw
                            state["initialized_headers"] = headers
                    try:
                        self._send_buffered(response, client_session if state else None,
                                            error_body)
                    except (URLError, OSError, HTTPException) as exc:
                        self._send_rpc_error(raw, "Upstream response broke; request not replayed: " + str(exc))
                    return

            def do_GET(self):
                """Forward GET — handles both streamable-http and SSE streams."""
                headers = dict(self.headers.items())
                client_session = self.headers.get("Mcp-Session-Id")
                sent_headers = False
                deadline = time.monotonic() + proxy._recovery_window
                while True:
                    with proxy._session_lock:
                        state = proxy._sessions.get(client_session)
                        upstream_session = state["upstream"] if state else client_session
                    try:
                        resp = self._retry_refused(
                            lambda: self._upstream_request("GET", headers=headers,
                                                           timeout=300, session=upstream_session),
                            deadline)
                    except (URLError, OSError, HTTPException) as exc:
                        if not sent_headers:
                            self.send_error(503, f"Upstream unavailable: {exc}")
                        return
                    if resp.status == 404 and state:
                        try:
                            recovered = self._recover_session(client_session, upstream_session, deadline)
                        except (URLError, OSError, HTTPException):
                            recovered = False
                        if recovered:
                            resp.close()
                            continue
                    if not sent_headers:
                        if resp.status >= 400 or "text/event-stream" not in resp.headers.get("Content-Type", ""):
                            try:
                                self._send_buffered(resp, client_session if state else None)
                            except (URLError, OSError, HTTPException) as exc:
                                self.send_error(503, f"Upstream response broke: {exc}")
                            return
                        self.send_response(resp.status)
                        self._send_response_headers(resp.headers, client_session if state else None)
                        self.end_headers()
                        sent_headers = True
                    elif resp.status >= 400:
                        resp.close()
                        return
                    try:
                        event_lines = []
                        while True:
                            try:
                                line = resp.readline()
                            except (URLError, OSError, HTTPException):
                                break  # upstream broke; reconnect
                            if not line:
                                break
                            if line.startswith(b"data:"):
                                line = self._rewrite_sse_endpoint(line)
                            event_lines.append(line)
                            if line in (b"\n", b"\r\n"):
                                try:
                                    self.wfile.write(b"".join(event_lines))
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                                    return  # client disconnected
                                event_lines.clear()
                                deadline = time.monotonic() + proxy._recovery_window
                    finally:
                        resp.close()
                    if time.monotonic() >= deadline:
                        return
                    time.sleep(proxy._retry_interval)

            def do_DELETE(self):
                client_session = self.headers.get("Mcp-Session-Id")
                with proxy._session_lock:
                    state = proxy._sessions.get(client_session)
                    upstream_session = state["upstream"] if state else client_session
                deadline = time.monotonic() + proxy._recovery_window
                try:
                    resp = self._retry_refused(
                        lambda: self._upstream_request("DELETE", session=upstream_session,
                                                       timeout=10), deadline)
                except (URLError, OSError, HTTPException) as exc:
                    self.send_error(503, f"Upstream unavailable: {exc}")
                    return
                try:
                    self._send_buffered(resp, client_session if state else None)
                except (URLError, OSError, HTTPException) as exc:
                    self.send_error(503, f"Upstream response broke: {exc}")
                    return
                if resp.status < 400 and state:
                    with proxy._session_lock:
                        proxy._sessions.pop(client_session, None)

            def _rewrite_sse_endpoint(self, line: bytes) -> bytes:
                """Rewrite upstream endpoint URLs in SSE data lines.

                FastMCP SSE sends: data: http://127.0.0.1:8201/messages/?session_id=xxx
                We rewrite to:     data: http://127.0.0.1:{proxy_port}/messages/?session_id=xxx
                so the client routes tool call POSTs through our proxy.
                """
                try:
                    text = line.decode("utf-8")
                    # Match "data: http://host:port/path..."
                    rewritten = re.sub(
                        r'data:\s*http://127\.0\.0\.1:\d+/',
                        f'data: {proxy.url}/',
                        text,
                    )
                    return rewritten.encode("utf-8")
                except Exception:
                    return line

            def _maybe_inject_sender(self, raw: bytes) -> bytes:
                """Parse JSON-RPC, inject sender for tools/call if missing."""
                if not raw:
                    return raw
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return raw

                # Handle both single requests and batches
                messages = data if isinstance(data, list) else [data]
                modified = False

                for msg in messages:
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("method") != "tools/call":
                        continue

                    params = msg.get("params", {})
                    tool_name = params.get("name", "")
                    args = params.get("arguments", {})

                    sender_key = _SENDER_PARAMS.get(tool_name)
                    if sender_key is None:
                        continue

                    current = args.get(sender_key, "")
                    if current != proxy.agent_name:
                        args[sender_key] = proxy.agent_name
                        params["arguments"] = args
                        modified = True

                if modified:
                    return json.dumps(data).encode("utf-8")
                return raw

        try:
            self._server = _ThreadingHTTPServer(("127.0.0.1", self._port), Handler)
        except OSError as e:
            if self._port > 0:
                # Fixed port in use — another wrapper instance owns the proxy
                log.info(f"Proxy port {self._port} in use, skipping (another instance owns it)")
                print(f"  MCP proxy: port {self._port} in use (shared with another instance)")
                self._server = None
                return False
            raise
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info(f"MCP proxy for {self._agent_name} on port {self.port}")
        print(f"  MCP proxy: port {self.port}")
        return True

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
