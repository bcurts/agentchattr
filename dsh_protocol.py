"""DeepSeek Harness (DSH) SDK wire protocol — JSON-RPC 2.0 over stdio.

This is a client-side implementation of the `dsh --profile sdk` runtime
protocol, mirroring the shapes documented in
``packages/sdk/protocol/README.md`` and the Python SDK client
(``python/sdk/src/deepseek_harness/client.py``).

Wire rules:
  * One JSON-RPC 2.0 message per ``\\n``-terminated line.
  * A frame carrying both ``id`` and ``method`` is a request.
  * A frame carrying ``id`` alone is a response.
  * A frame carrying ``method`` alone is a notification.
  * Malformed lines are ignored.

Client → server requests::

    initialize      InitializeParams  → InitializeResult
    session/prompt  SessionPromptParams → SessionPromptResult
    session/wait    SessionWaitParams   → {} (root and managed descendants)
    shutdown        (none)            → {}

Server → client notifications::

    session.event     SessionEventNotification
    session.status    SessionStatusNotification
    subagent.started  SubagentStartedNotification
    subagent.finished SubagentFinishedNotification (in-process runs only)

The transport is deliberately dependency-free: DSH is an external runtime
dependency, so this module must import with nothing but the Python standard
library.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# Wire-stable server identity returned by `initialize`.
DSH_SERVER_NAME = "deepseek-harness-sdk-runtime"

# JSON-RPC error codes used by the serving plugin.
ERR_METHOD_NOT_FOUND = -32601
ERR_INTERNAL = -32603


class DshProtocolError(RuntimeError):
    """Base class for DSH transport/protocol failures."""


class DshTransportClosed(DshProtocolError):
    """The runtime closed stdout, the pipe broke, or the process exited."""


class DshRpcError(DshProtocolError):
    """A JSON-RPC error response from the runtime."""

    def __init__(self, code: int | None, message: str, data: object = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class DshTimeout(DshProtocolError):
    """A request did not receive a response before its deadline."""


@dataclass
class DshNotification:
    """One server→client notification."""

    method: str
    payload: dict


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None


class DshClient:
    """Synchronous JSON-RPC client for the DSH SDK runtime over stdio.

    Thread-safe: ``request`` may be called from several threads; writes are
    serialized and each request tracks its own response slot.

    ``on_notification`` is invoked on the reader thread for every incoming
    notification. It must not block (the adapter only appends to a queue).
    """

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        initialize_timeout: float = 30.0,
        shutdown_timeout: float = 10.0,
        stderr_tail_lines: int = 400,
        on_notification: Callable[[DshNotification], None] | None = None,
        on_exit: Callable[[int | None], None] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._env = env
        self._initialize_timeout = initialize_timeout
        self._shutdown_timeout = shutdown_timeout
        self._on_notification = on_notification
        self._on_exit = on_exit
        self._log = log_fn or (lambda msg: None)

        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._stderr_tail: deque[str] = deque(maxlen=stderr_tail_lines)
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._exit_code: int | None = None
        self._server_info: dict = {}
        # Sessions the runtime reported as `running` (whole-agent lifecycle).
        # Kept here so the adapter can compute busy/idle without racing the
        # notification queue.
        self._running_sessions: set[str] = set()
        self._session_lock = threading.Lock()
        self._mcp_ready = threading.Event()
        self._mcp_tools: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the runtime. Protocol stdout stays private to this client."""
        if self._proc is not None:
            return
        # bufsize=1 (line buffered) + text mode keeps frames whole; encoding is
        # forced to UTF-8 because Windows would otherwise use the ANSI code page
        # and corrupt Chinese prompts.
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._cwd,
            env=self._env,
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name="dsh-stderr", daemon=True
        )
        self._stderr_thread.start()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="dsh-stdout", daemon=True
        )
        self._reader_thread.start()

    def initialize(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Perform the process-wide handshake and resolve the model route.

        The server resolves the provider/model route here and rejects
        ``session/prompt`` until this succeeds. This does not prove that a
        remote provider will accept the credentials on the first model call.
        """
        payload: dict = {
            "cwd": str(Path(cwd).resolve()),
            "provider": provider,
            "model": model,
        }
        if reasoning_effort:
            payload["reasoningEffort"] = reasoning_effort
        if max_tokens:
            payload["maxTokens"] = int(max_tokens)
        result = self.request(
            "initialize",
            payload,
            timeout=self._initialize_timeout if timeout is None else timeout,
        )
        if not isinstance(result, dict):
            raise DshProtocolError("initialize returned a non-object result")
        info = result.get("serverInfo")
        if not isinstance(info, dict):
            raise DshProtocolError("initialize result is missing serverInfo")
        self._server_info = info
        return result

    @property
    def server_info(self) -> dict:
        return dict(self._server_info)

    def session_prompt(
        self, session_id: str, content_blocks: list[dict], *, timeout: float | None = None
    ) -> str:
        """Enqueue one user turn on ``session_id``; return the queued message id.

        The returned id identifies the *user* message, not the assistant reply
        or a turn boundary — completion is observed through notifications.
        """
        result = self.request(
            "session/prompt",
            {"sessionId": session_id, "contentBlocks": content_blocks},
            timeout=timeout,
        )
        if not isinstance(result, dict) or not result.get("messageId"):
            raise DshProtocolError("session/prompt returned no messageId")
        return str(result["messageId"])

    def session_wait(self, session_id: str, *, timeout: float = 1800.0) -> None:
        """Wait for the root and progressing managed descendants (SDK authority).

        A timeout is an unknown outcome, never permission to submit another turn.
        """
        result = self.request("session/wait", {"sessionId": session_id}, timeout=timeout)
        if not isinstance(result, dict):
            raise DshProtocolError("session/wait returned a non-object result")

    def wait_mcp_ready(self, *, timeout: float = 30.0) -> set[str]:
        """Require evidence from our in-runtime registry probe, not handshake success."""
        deadline = time.monotonic() + timeout
        while not self._mcp_ready.wait(min(0.1, max(0.0, deadline - time.monotonic()))):
            if not self.is_running() or self._closed.is_set():
                raise DshTransportClosed(self._closed_reason())
            if time.monotonic() >= deadline:
                raise DshTimeout("Chat MCP tools did not become available in the DSH registry")
        return set(self._mcp_tools)

    def shutdown(self, *, timeout: float | None = None) -> bool:
        """Ask the runtime to flush durable state and exit. Never raises."""
        if self._proc is None:
            return True
        try:
            self.request(
                "shutdown",
                None,
                timeout=self._shutdown_timeout if timeout is None else timeout,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - shutdown is best-effort
            self._log(f"shutdown request failed: {type(exc).__name__}: {exc}")
            return False

    def close(self, *, terminate_grace: float = 2.0) -> None:
        """Close stdin, then terminate/kill as needed. Idempotent."""
        proc = self._proc
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        deadline = time.monotonic() + terminate_grace
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    # Only the SDK process we spawned and its descendants. Never
                    # target node.exe by name (the user's Web UI may use it).
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=terminate_grace + 3,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    proc.terminate()
            except Exception:
                pass
            deadline = time.monotonic() + terminate_grace
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=terminate_grace)
            except Exception:
                pass
        self._closed.set()
        self._fail_pending(DshTransportClosed("DSH runtime closed"))
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=0.5)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.5)
        self._proc = None

    # -- process state -----------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def returncode(self) -> int | None:
        proc = self._proc
        if proc is None:
            return self._exit_code
        return proc.poll()

    def is_running(self) -> bool:
        proc = self._proc
        return bool(proc is not None and proc.poll() is None)

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)

    def running_sessions(self) -> set[str]:
        with self._session_lock:
            return set(self._running_sessions)

    # -- requests ----------------------------------------------------------

    def request(self, method: str, params: object, *, timeout: float | None = None) -> object:
        """Send one request and block for its result."""
        proc = self._proc
        if proc is None or proc.poll() is not None or self._closed.is_set():
            raise DshTransportClosed(self._closed_reason())
        request_id = str(uuid.uuid4())
        pending = _Pending()
        with self._lock:
            self._pending[request_id] = pending
        message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._write(message)
        except BaseException:
            with self._lock:
                self._pending.pop(request_id, None)
            raise
        if not pending.event.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise DshTimeout(
                f"{method} timed out after {timeout}s\n{self._closed_reason()}".rstrip()
            )
        if pending.error is not None:
            raise pending.error
        return pending.result

    def notify(self, method: str, params: object = None) -> None:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)

    def _write(self, message: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise DshTransportClosed(self._closed_reason())
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            with self._write_lock:
                proc.stdin.write(payload)
                proc.stdin.flush()
        except Exception as exc:
            raise DshTransportClosed(
                f"failed to write to DSH runtime: {type(exc).__name__}: {exc}\n"
                f"{self._closed_reason()}".rstrip()
            ) from exc

    # -- reader ------------------------------------------------------------

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if not line or not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    # Malformed lines are ignored per the protocol contract.
                    continue
                self._handle_message(message)
        except BaseException as exc:  # noqa: BLE001 - reader must never die loudly
            self._fail_pending(DshTransportClosed(f"reader failed: {exc}"))
        finally:
            # EOF itself closes the transport, even if a broken child remains
            # alive. Wake requests before waiting for process cleanup.
            self._closed.set()
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            self._exit_code = proc.returncode
            self._fail_pending(DshTransportClosed("DSH protocol stdout closed\n" + self._closed_reason()))
            if self._on_exit is not None:
                try:
                    self._on_exit(proc.returncode)
                except Exception:
                    pass

    def _handle_message(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        msg_id = message.get("id")
        method = message.get("method")

        if isinstance(msg_id, (str, int)) and isinstance(method, str):
            # Server→client request. The shipped server never sends one, but the
            # transport supports it — answer so a future version cannot hang us.
            self._answer_unsupported(msg_id, method)
            return

        if isinstance(msg_id, (str, int)):
            with self._lock:
                pending = self._pending.pop(str(msg_id), None)
            if pending is None:
                return
            error = message.get("error")
            if isinstance(error, dict):
                pending.error = DshRpcError(
                    error.get("code") if isinstance(error.get("code"), int) else None,
                    str(error.get("message", "JSON-RPC error")),
                    error.get("data"),
                )
            else:
                pending.result = message.get("result")
            pending.event.set()
            return

        if isinstance(method, str):
            params = message.get("params")
            payload = params if isinstance(params, dict) else {}
            if method == "session.status":
                session_id = payload.get("sessionId")
                if isinstance(session_id, str) and session_id:
                    with self._session_lock:
                        if payload.get("status") == "running":
                            self._running_sessions.add(session_id)
                        else:
                            self._running_sessions.discard(session_id)
            if self._on_notification is not None:
                try:
                    self._on_notification(DshNotification(method=method, payload=payload))
                except Exception:
                    pass

    def _answer_unsupported(self, msg_id: object, method: str) -> None:
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": ERR_METHOD_NOT_FOUND,
                        "message": f"client does not implement {method}",
                    },
                }
            )
        except Exception:
            pass

    def _fail_pending(self, exc: BaseException) -> None:
        with self._lock:
            pendings = list(self._pending.values())
            self._pending.clear()
        for pending in pendings:
            if pending.error is None and pending.result is None:
                pending.error = exc
            pending.event.set()

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.rstrip("\r\n")
            if text:
                if text.startswith("AGENTCHATTR_CHAT_TOOL "):
                    try:
                        audit = json.loads(text.partition(" ")[2])
                        tool, status = audit.get("tool"), audit.get("status")
                        session = str(audit.get("session", ""))
                        if tool in ("chat_read", "chat_send") and status in ("ok", "error"):
                            # Only bounded identifiers, never tool inputs/results.
                            session = "".join(c for c in session[:200] if c.isalnum() or c in "-_:")
                            self._log(f"[chat-tool] {tool} session={session} status={status}")
                    except (ValueError, TypeError, AttributeError):
                        pass
                    continue
                if text.startswith("AGENTCHATTR_MCP_REGISTRY "):
                    try:
                        status = json.loads(text.partition(" ")[2])
                        required = {"mcp__agentchattr__chat_read", "mcp__agentchattr__chat_send"}
                        names = set(status.get("tools", []))
                        self._mcp_tools = names
                        if required <= names:
                            self._mcp_ready.set()
                        else:
                            self._mcp_ready.clear()
                    except (ValueError, TypeError, AttributeError):
                        pass
                self._stderr_tail.append(text)

    def _closed_reason(self) -> str:
        """Human-readable diagnostics for a transport failure."""
        parts: list[str] = []
        code = self.returncode
        if code is not None:
            parts.append(f"exit code: {code}")
        if self._stderr_tail:
            # Keep the tail bounded — enough to identify the failure.
            tail = list(self._stderr_tail)[-25:]
            parts.append("stderr tail:\n" + "\n".join(tail))
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Session event helpers
# ---------------------------------------------------------------------------

def extract_assistant_text(event: object) -> str:
    """Best-effort extraction of assistant text from a session-log event.

    Used only for logging/diagnostics — the authoritative reply path is the
    agent calling the chat MCP tool itself. Returns '' when the event is not a
    recognisable assistant message.
    """
    if not isinstance(event, dict):
        return ""
    kind = str(event.get("type") or event.get("kind") or "").lower()
    if kind and "assistant" not in kind and "message" not in kind:
        return ""
    message = event.get("message") if isinstance(event.get("message"), dict) else event
    blocks = message.get("content") if isinstance(message, dict) else None
    if not isinstance(blocks, (list, tuple)):
        return ""
    chunks: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def normalize_prompt_blocks(prompt: str) -> list[dict]:
    """Wrap plain text as the durable content-block shape the protocol expects."""
    return [{"type": "text", "text": prompt}]


def build_launch_command(
    node_path: str,
    dsh_entry: str,
    *,
    profile: str = "sdk",
    patches: Iterable[str] = (),
) -> list[str]:
    """Compose the `dsh` argv for a stdio SDK runtime."""
    command = [node_path, dsh_entry, "--profile", profile]
    for patch in patches:
        command.extend(["--patch", str(patch)])
    return command
