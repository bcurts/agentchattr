"""Per-instance MCP identity proxy.

Sits between an agent CLI and the real Mehub MCP server.
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
import re
import threading
import logging
import sys
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


class McpIdentityProxy:
    """Local HTTP proxy that stamps agent identity on MCP tool calls.

    Args:
        upstream_base: Base URL without path, e.g. "http://127.0.0.1:8200"
        upstream_path: Path prefix for the transport, e.g. "/mcp" or "/sse"
        agent_name: Current canonical name for this instance
        instance_token: Server-issued token (forwarded as Authorization: Bearer)
    """

    def __init__(self, upstream_base: str, upstream_path: str,
                 agent_name: str, instance_token: str, port: int = 0):
        self._upstream_base = upstream_base.rstrip("/")
        self._upstream_path = upstream_path
        self._agent_name = agent_name
        self._token = instance_token
        self._port = port  # 0 = OS-assigned (legacy), >0 = fixed
        self._lock = threading.Lock()
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

            def _send_response_headers(self, headers):
                for key in (
                    "Content-Type",
                    "Mcp-Session-Id",
                    "mcp-session-id",
                    "Cache-Control",
                    "X-Accel-Buffering",
                    "Connection",
                ):
                    val = headers.get(key)
                    if val:
                        self.send_header(key, val)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""

                # Inject sender into MCP tool calls
                body = self._maybe_inject_sender(raw)

                try:
                    status, resp_body, resp_headers = self._post_upstream(body)
                except (URLError, OSError) as e:
                    self.send_error(502, f"Upstream error: {e}")
                    return

                # Streamable HTTP sessions can expire after server restarts.
                # If upstream says "Session not found", bootstrap a fresh
                # session and retry the same payload once.
                if (
                    self.path.startswith("/mcp")
                    and self._is_session_not_found(resp_body)
                ):
                    try:
                        recovered_session = self._bootstrap_streamable_session()
                        if recovered_session:
                            status, resp_body, resp_headers = self._post_upstream(
                                body,
                                drop_session_header=True,
                                force_session_id=recovered_session,
                            )
                        else:
                            status, resp_body, resp_headers = self._post_upstream(
                                body,
                                drop_session_header=True,
                            )
                    except (URLError, OSError):
                        # Keep the original error response if recovery fails.
                        pass

                self.send_response(status)
                self._send_response_headers(resp_headers)
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)

            def do_GET(self):
                """Forward GET — handles both streamable-http and SSE streams."""
                try:
                    req = Request(self._upstream_url(), method="GET")
                    # Forward all headers from client
                    for hdr, val in self.headers.items():
                        if hdr.lower() not in ("host",):
                            req.add_header(hdr, val)
                    
                    req.add_header("Authorization", f"Bearer {proxy.token}")
                    req.add_header("X-Agent-Token", proxy.token)

                    resp = urlopen(req, timeout=300)
                except HTTPError as e:
                    status = e.code
                    resp_body = e.read()
                    resp_headers = e.headers
                    self.send_response(status)
                    self._send_response_headers(resp_headers)
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    if resp_body:
                        self.wfile.write(resp_body)
                    return
                except BrokenPipeError:
                    return
                except (URLError, OSError) as e:
                    self.send_error(502, f"Upstream error: {e}")
                    return

                self.send_response(resp.status)
                self._send_response_headers(resp.headers)
                self.end_headers()

                try:
                    # Stream line-by-line for SSE (events are line-delimited)
                    for line in resp:
                        # Rewrite endpoint URLs in SSE events so the client
                        # POSTs back through the proxy, not directly to upstream
                        if line.startswith(b"data:"):
                            line = self._rewrite_sse_endpoint(line)
                        self.wfile.write(line)
                        self.wfile.flush()
                except BrokenPipeError:
                    pass

            def do_DELETE(self):
                try:
                    req = Request(self._upstream_url(), method="DELETE")
                    for hdr in ("Mcp-Session-Id",):
                        val = self.headers.get(hdr)
                        if val:
                            req.add_header(hdr, val)
                    req.add_header("Authorization", f"Bearer {proxy.token}")
                    req.add_header("X-Agent-Token", proxy.token)
                    resp = urlopen(req, timeout=10)
                    self.send_response(resp.status)
                    self.end_headers()
                except Exception:
                    self.send_error(502)

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

            def _post_upstream(
                self,
                body: bytes,
                *,
                drop_session_header: bool = False,
                force_session_id: str | None = None,
            ):
                req = Request(
                    self._upstream_url(),
                    data=body,
                    method="POST",
                )
                for hdr, val in self.headers.items():
                    h = hdr.lower()
                    if h in ("content-length", "host"):
                        continue
                    if drop_session_header and h == "mcp-session-id":
                        continue
                    req.add_header(hdr, val)
                if force_session_id:
                    req.add_header("Mcp-Session-Id", force_session_id)

                req.add_header("Authorization", f"Bearer {proxy.token}")
                req.add_header("X-Agent-Token", proxy.token)

                try:
                    resp = urlopen(req, timeout=30)
                    return resp.status, resp.read(), resp.headers
                except HTTPError as e:
                    return e.code, e.read(), e.headers

            def _is_session_not_found(self, payload: bytes) -> bool:
                if not payload:
                    return False
                try:
                    decoded = json.loads(payload.decode("utf-8"))
                except Exception:
                    return False

                def _has_session_error(item) -> bool:
                    if not isinstance(item, dict):
                        return False
                    err = item.get("error")
                    if not isinstance(err, dict):
                        return False
                    msg = str(err.get("message", "")).lower()
                    return "session not found" in msg

                if isinstance(decoded, list):
                    return any(_has_session_error(x) for x in decoded)
                return _has_session_error(decoded)

            def _bootstrap_streamable_session(self) -> str | None:
                init_payload = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "proxy-init",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "mehub-proxy", "version": "1.0"},
                        },
                    }
                ).encode("utf-8")

                init_status, _, init_headers = self._post_upstream(
                    init_payload,
                    drop_session_header=True,
                )
                if not (200 <= int(init_status) < 300):
                    return None

                session_id = (
                    init_headers.get("Mcp-Session-Id")
                    or init_headers.get("mcp-session-id")
                )
                if not session_id:
                    return None

                initialized_payload = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    }
                ).encode("utf-8")
                self._post_upstream(
                    initialized_payload,
                    drop_session_header=True,
                    force_session_id=session_id,
                )
                log.info("Recovered streamable HTTP MCP session for %s", proxy.agent_name)
                return session_id

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
