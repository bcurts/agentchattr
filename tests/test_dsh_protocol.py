"""Protocol tests for the DSH SDK stdio transport (dsh_protocol.py).

These tests exercise the transport against a *fake* JSON-RPC server implemented
as a tiny Python script, so they run without the real DSH runtime. They cover
the failure modes the development plan calls out:

  * frame classification (request / response / notification / malformed)
  * segmented reads and non-ASCII (Chinese) payloads
  * JSON-RPC error responses
  * initialize timeout
  * abrupt process exit while a request is in flight
  * shutdown + close cleanup (no zombie process)

Run:  python -m pytest tests/test_dsh_protocol.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dsh_protocol import (  # noqa: E402
    DSH_SERVER_NAME,
    DshClient,
    DshProtocolError,
    DshRpcError,
    DshTimeout,
    DshTransportClosed,
    build_launch_command,
    normalize_prompt_blocks,
)


# --------------------------------------------------------------------------
# Fake server
# --------------------------------------------------------------------------

FAKE_SERVER = textwrap.dedent(
    '''
    """Minimal JSON-RPC 2.0 stdio server used by the transport tests."""
    import json, sys, time

    MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"

    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if MODE == "crash" and method:
            # Die mid-request without answering.
            sys.exit(3)

        if method == "initialize":
            if MODE == "error":
                send({"jsonrpc": "2.0", "id": msg_id,
                      "error": {"code": -32602, "message": "unknown model"}})
                continue
            if MODE == "timeout":
                time.sleep(30)
                continue
            params = msg.get("params") or {}
            send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "serverInfo": {"name": "deepseek-harness-sdk-runtime", "version": "0.2.1-rc.5"},
                "route": [params.get("provider"), params.get("model")],
            }})
            # Emit a notification right after the response to exercise the
            # write-then-read interleaving.
            send({"jsonrpc": "2.0", "method": "session.status",
                  "params": {"sessionId": "s-1", "status": "idle"}})
            continue

        if method == "session/prompt":
            params = msg.get("params") or {}
            send({"jsonrpc": "2.0", "id": msg_id,
                  "result": {"messageId": "msg-" + str(params.get("sessionId"))}})
            send({"jsonrpc": "2.0", "method": "session.status",
                  "params": {"sessionId": params.get("sessionId"), "status": "running"}})
            send({"jsonrpc": "2.0", "method": "session.event",
                  "params": {"type": "assistant.message",
                             "message": {"content": [{"type": "text", "text": "收到中文消息"}]}}})
            send({"jsonrpc": "2.0", "method": "session.status",
                  "params": {"sessionId": params.get("sessionId"), "status": "idle"}})
            continue

        if method == "session/wait":
            if MODE == "wait-error":
                send({"jsonrpc": "2.0", "id": msg_id,
                      "error": {"code": -32603, "message": "managed child failed"}})
                continue
            # The root was already idle. A managed child still needs time.
            time.sleep(0.35)
            send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            continue

        if method == "shutdown":
            if MODE != "no-shutdown-reply":
                send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            sys.exit(0)

        if msg_id is not None:
            # Malformed/unsupported line with garbage should be ignored.
            send({"jsonrpc": "2.0", "id": msg_id,
                  "error": {"code": -32601, "message": "method not found"}})
    '''
).strip()


@pytest.fixture()
def fake_server(tmp_path: Path) -> Path:
    path = tmp_path / "fake_dsh_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def make_client(fake_server: Path, mode: str = "normal", **kwargs) -> DshClient:
    command = [sys.executable, "-X", "utf8", str(fake_server), mode]
    return DshClient(
        command,
        cwd=str(fake_server.parent),
        env=None,
        initialize_timeout=kwargs.pop("initialize_timeout", 10.0),
        **kwargs,
    )


# --------------------------------------------------------------------------
# Frame / transport basics
# --------------------------------------------------------------------------

def test_build_launch_command_shape():
    cmd = build_launch_command("node", "/opt/dsh/bin.js", profile="sdk", patches=["a.yml", "b.yml"])
    assert cmd == ["node", "/opt/dsh/bin.js", "--profile", "sdk", "--patch", "a.yml", "--patch", "b.yml"]


def test_build_launch_command_default_profile():
    cmd = build_launch_command("node", "/opt/dsh/bin.js")
    assert cmd[3] == "sdk"
    assert "--patch" not in cmd


def test_normalize_prompt_blocks():
    blocks = normalize_prompt_blocks("你好")
    assert blocks == [{"type": "text", "text": "你好"}]


def test_initialize_resolves_route_and_server_identity(fake_server: Path):
    client = make_client(fake_server)
    try:
        client.start()
        result = client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        assert result["route"] == ["deepseek-official", "deepseek-v4-flash"]
        assert client.server_info["name"] == DSH_SERVER_NAME
        assert client.server_info["version"] == "0.2.1-rc.5"
    finally:
        client.shutdown()
        client.close()


def test_chinese_payload_survives_round_trip(fake_server: Path):
    """Non-ASCII must survive the stdin write and the stdout read."""
    notes: list[dict] = []
    client = make_client(fake_server, on_notification=lambda n: notes.append(n.payload))
    try:
        client.start()
        client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        message_id = client.session_prompt("s-中文", normalize_prompt_blocks("请回复：你好，世界"))
        assert message_id == "msg-s-中文"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
            n.get("type") == "assistant.message" for n in notes
        ):
            time.sleep(0.05)
        text = [
            block["text"]
            for n in notes
            if n.get("type") == "assistant.message"
            for block in n.get("message", {}).get("content", [])
        ]
        assert text == ["收到中文消息"]
    finally:
        client.shutdown()
        client.close()


def test_notifications_are_dispatched_and_sessions_tracked(fake_server: Path):
    notes: list[str] = []
    client = make_client(fake_server, on_notification=lambda n: notes.append(n.method))
    try:
        client.start()
        client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and "session.status" not in notes:
            time.sleep(0.01)
        assert "session.status" in notes
        assert client.running_sessions() == set()
        client.session_prompt("s-9", normalize_prompt_blocks("hi"))
        client.session_wait("s-9", timeout=2)
        assert client.running_sessions() == set()
        assert "session.event" in notes
    finally:
        client.shutdown()
        client.close()


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------

def test_rpc_error_response_raises_with_code(fake_server: Path):
    client = make_client(fake_server, mode="error")
    try:
        client.start()
        with pytest.raises(DshRpcError) as excinfo:
            client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="nope")
        assert excinfo.value.code == -32602
        assert "unknown model" in excinfo.value.message
    finally:
        client.close()


def test_initialize_timeout_raises_dsh_timeout(fake_server: Path):
    client = make_client(fake_server, mode="timeout", initialize_timeout=1.0)
    try:
        client.start()
        started = time.monotonic()
        with pytest.raises(DshTimeout):
            client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        assert time.monotonic() - started < 5
    finally:
        client.close()


def test_abrupt_exit_fails_in_flight_request(fake_server: Path):
    """A crash mid-request must surface as a transport error, not a hang."""
    client = make_client(fake_server, mode="crash")
    try:
        client.start()
        with pytest.raises(DshProtocolError) as excinfo:
            client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        assert not isinstance(excinfo.value, DshRpcError)
        assert "exit code" in str(excinfo.value)
    finally:
        client.close()


def test_request_on_dead_transport_raises_immediately(fake_server: Path):
    client = make_client(fake_server)
    try:
        client.start()
        client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        client.shutdown()
        client.close()
        with pytest.raises(DshTransportClosed):
            client.request("session/prompt", {"sessionId": "s", "contentBlocks": []}, timeout=2)
    finally:
        client.close()


def test_shutdown_is_best_effort_and_never_raises(fake_server: Path):
    """Even without a reply, shutdown returns False instead of raising."""
    client = make_client(fake_server, mode="no-shutdown-reply")
    try:
        client.start()
        client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
        assert client.shutdown(timeout=1.0) is False
    finally:
        client.close()


def test_close_leaves_no_running_process(fake_server: Path):
    client = make_client(fake_server)
    client.start()
    pid = client.pid
    assert pid is not None
    client.initialize(cwd=str(fake_server.parent), provider="deepseek-official", model="deepseek-v4-flash")
    client.close(terminate_grace=3.0)
    assert client.is_running() is False
    assert client.pid is None


def test_close_is_idempotent(fake_server: Path):
    client = make_client(fake_server)
    client.start()
    client.close()
    client.close()  # must not raise


def test_malformed_stdout_lines_are_ignored(tmp_path: Path):
    """Garbage on stdout must not break the reader."""
    script = tmp_path / "noisy.py"
    script.write_text(
        textwrap.dedent(
            '''
            import json, sys
            sys.stdout.write("not json at all\\n")
            sys.stdout.write("{\\"broken\\": \\n")
            sys.stdout.flush()
            for line in sys.stdin:
                msg = json.loads(line)
                if msg.get("method") == "initialize":
                    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                        "result": {"serverInfo": {"name": "deepseek-harness-sdk-runtime"}}}) + "\\n")
                    sys.stdout.flush()
                elif msg.get("method") == "shutdown":
                    sys.exit(0)
            '''
        ).strip(),
        encoding="utf-8",
    )
    client = DshClient([sys.executable, str(script)], cwd=str(tmp_path))
    try:
        client.start()
        result = client.initialize(cwd=str(tmp_path), provider="deepseek-official", model="deepseek-v4-flash")
        assert result["serverInfo"]["name"] == DSH_SERVER_NAME
    finally:
        client.close()


def test_session_wait_includes_work_after_root_idle(fake_server):
    client = make_client(fake_server)
    try:
        client.start()
        client.session_prompt("root", normalize_prompt_blocks("test"))
        started = time.monotonic()
        client.session_wait("root", timeout=2)
        assert time.monotonic() - started >= 0.3
    finally:
        client.close()


def test_session_wait_failure_is_not_success(fake_server):
    client = make_client(fake_server, mode="wait-error")
    try:
        client.start()
        client.session_prompt("root", normalize_prompt_blocks("test"))
        with pytest.raises(DshRpcError, match="managed child failed"):
            client.session_wait("root", timeout=2)
    finally:
        client.close()


def test_initialize_alone_is_not_mcp_readiness(fake_server):
    client = make_client(fake_server)
    try:
        client.start()
        client.initialize(cwd=str(fake_server.parent), provider="test", model="test")
        with pytest.raises(DshTimeout, match="MCP tools"):
            client.wait_mcp_ready(timeout=0.05)
    finally:
        client.close()


def test_registry_requires_both_chat_tools():
    import io
    from types import SimpleNamespace
    client = DshClient(["unused"])
    client._proc = SimpleNamespace(stderr=io.StringIO(
        'AGENTCHATTR_MCP_REGISTRY {"tools":["mcp__agentchattr__chat_read"]}\n'))
    client._stderr_loop()
    assert not client._mcp_ready.is_set()
    client._proc.stderr = io.StringIO(
        'AGENTCHATTR_MCP_REGISTRY {"tools":["mcp__agentchattr__chat_read","mcp__agentchattr__chat_send"]}\n')
    client._stderr_loop()
    assert client._mcp_ready.is_set()
    client._proc.stderr = io.StringIO('AGENTCHATTR_MCP_REGISTRY {"tools":[]}\n')
    client._stderr_loop()
    assert not client._mcp_ready.is_set()
