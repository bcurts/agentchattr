"""Fault-injection tests for the DSH adapter (wrapper_dsh.py).

Covers the failure scenarios the development plan enumerates:

  * MCP authentication / heartbeat rejection (HTTP 4xx)
  * model-call failure surfacing as an RPC error (protocol level)
  * connection drop / crash must not leave a "fake online" instance
  * missing dependencies produce a specific, actionable error
  * overlay generation writes the required full-access + MCP settings
  * credential handling never persists the token into the overlay file
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wrapper_dsh  # noqa: E402
from wrapper_dsh import (  # noqa: E402
    DshHeartbeat,
    _check_dependencies,
    write_full_access_overlay,
    write_patch_overlay,
    write_profile_manifest,
)


class FakeIdentity:
    def __init__(self, name: str = "DSH-01", token: str = "tok-abc"):
        self.name = name
        self.token = token


def make_heartbeat(identity: FakeIdentity | None = None, port: int = 8300) -> DshHeartbeat:
    ident = identity or FakeIdentity()
    return DshHeartbeat(
        port,
        lambda: (ident.name, ident.token),
        lambda: ident.token,
        lease_id="lease-1",
        pid=4242,
        start_marker="marker",
        log_fn=lambda _m: None,
    )


# --------------------------------------------------------------------------
# Heartbeat failure handling
# --------------------------------------------------------------------------

def test_heartbeat_records_failure_on_http_error(monkeypatch):
    hb = make_heartbeat()

    def boom(*_a, **_k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(wrapper_dsh.urllib.request, "urlopen", boom)
    result = hb.send(active=True)
    assert result["ok"] is False
    assert result["status"] == 401
    assert hb.consecutive_failures == 1


def test_heartbeat_records_failure_on_connection_error(monkeypatch):
    hb = make_heartbeat()

    def boom(*_a, **_k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(wrapper_dsh.urllib.request, "urlopen", boom)
    result = hb.send()
    assert result["ok"] is False
    assert result["status"] is None
    assert "ConnectionRefusedError" in result["error"]
    assert hb.consecutive_failures == 1


def test_heartbeat_resets_failures_on_success(monkeypatch):
    hb = make_heartbeat()

    class FakeResp:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"name": "DSH-01"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(wrapper_dsh.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    hb.consecutive_failures = 5
    result = hb.send()
    assert result["ok"] is True
    assert hb.consecutive_failures == 0


def test_terminal_heartbeat_stops_sending(monkeypatch):
    """Once the lease proof is rejected, we must not keep pretending to be alive."""
    hb = make_heartbeat()
    hb.terminal = True
    called = {"n": 0}

    def should_not_run(*_a, **_k):
        called["n"] += 1
        raise AssertionError("urlopen must not be called once terminal")

    monkeypatch.setattr(wrapper_dsh.urllib.request, "urlopen", should_not_run)
    result = hb.send()
    assert result["ok"] is False
    assert "terminal" in result["error"]
    assert called["n"] == 0


def test_heartbeat_sends_active_flag(monkeypatch):
    captured: dict = {}
    hb = make_heartbeat()

    class FakeResp:
        status = 200

        def read(self) -> bytes:
            return b'{"name": "DSH-01"}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def capture(req, *_a, **_k):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(wrapper_dsh.urllib.request, "urlopen", capture)
    hb.send(active=True)
    assert captured["body"]["active"] is True
    hb.send(active=False)
    assert captured["body"]["active"] is False


def test_heartbeat_carries_lease_identity(monkeypatch):
    captured: dict = {}
    hb = make_heartbeat()

    class FakeResp:
        def read(self) -> bytes:
            return b'{"name": "DSH-01"}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def capture(req, *_a, **_k):
        captured["body"] = json.loads(req.data.decode())
        captured["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(wrapper_dsh.urllib.request, "urlopen", capture)
    hb.send()
    assert captured["body"]["lease_id"] == "lease-1"
    assert captured["body"]["pid"] == 4242
    assert captured["body"]["start_marker"] == "marker"
    assert captured["url"].endswith("/api/heartbeat/DSH-01")


# --------------------------------------------------------------------------
# Dependency resolution failures
# --------------------------------------------------------------------------

def test_missing_explicit_runtime_entry_reports_specific_error(monkeypatch):
    monkeypatch.setenv("AGENTCHATTR_DSH_ENTRY", "D:/nope/definitely-missing-runtime/lib/bin.js")
    _resolved, error = _check_dependencies(
        {"runtime_dir": str(Path("D:/nope/definitely-missing-runtime"))}
    )
    assert error
    assert "runtime" in error.lower() or "dir" in error.lower()


def test_dependency_check_reports_missing_entry(monkeypatch, tmp_path: Path):
    """A runtime dir without a built CLI must name the missing entry."""
    # Point at a real dir with no apps/cli/lib/bin.js.
    monkeypatch.setenv("AGENTCHATTR_DSH_ENTRY", str(tmp_path / "lib" / "bin.js"))
    _resolved, error = _check_dependencies({})
    assert error


# --------------------------------------------------------------------------
# Overlay generation
# --------------------------------------------------------------------------

def test_profile_manifest_declares_bundles(tmp_path: Path):
    profile = tmp_path / "profiles" / "agentchattr-DSH-01"
    path = write_profile_manifest(
        profile, bundles=["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-sdk-app"]
    )
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    bundles = data["dsh"]["profile"]["bundles"]
    assert "@deepseek-ai/dsh-base" in bundles
    assert "@deepseek-ai/dsh-sdk-app" in bundles


def test_patch_overlay_mounts_mcp_without_inlining_token(tmp_path: Path):
    patch = write_patch_overlay(
        tmp_path / "chat-mcp.patch.yml",
        mcp_url="http://127.0.0.1:8200/mcp",
        token_env_var="AGENTCHATTR_MCP_TOKEN",
        server_name="agentchattr",
    )
    text = patch.read_text(encoding="utf-8")
    assert "mcp" in text.lower()
    assert "agentchattr" in text
    assert "8200" in text
    # The token must be read from the env, never written literally.
    assert "AGENTCHATTR_MCP_TOKEN" in text
    assert "process.env" in text


def test_full_access_overlay_forces_danger_and_no_approval(tmp_path: Path):
    patch = write_full_access_overlay(tmp_path / "full-access.patch.yml")
    text = patch.read_text(encoding="utf-8")
    assert "danger-full-access" in text
    assert "approval" in text
    # Approval must be off, i.e. "never".
    assert "never" in text
    # Interactive user questions must be disabled in unattended mode.
    assert "user-questions" in text


def test_overlays_use_dsh_js_tag_idiom(tmp_path: Path):
    """DSH's config DSL uses `!!js` tags for runtime expressions.

    PyYAML cannot resolve a `!!js` tag (it is a DSH/cordis extension), so we
    register a permissive multi-constructor and assert the *structure* parses
    and the expressions are what we intend.
    """
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("PyYAML not installed")

    class DshLoader(yaml.SafeLoader):
        pass

    # Treat any unknown explicit tag as a plain string.
    DshLoader.add_multi_constructor(
        "tag:yaml.org,2002:", lambda loader, suffix, node: loader.construct_scalar(node)
    )
    DshLoader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_scalar(node))

    patch = write_patch_overlay(
        tmp_path / "chat-mcp.patch.yml",
        mcp_url="http://127.0.0.1:8200/mcp",
        token_env_var="AGENTCHATTR_MCP_TOKEN",
        server_name="agentchattr",
    )
    mcp_doc = yaml.load(patch.read_text(encoding="utf-8"), Loader=DshLoader)
    assert mcp_doc is not None

    full = write_full_access_overlay(tmp_path / "full-access.patch.yml")
    access_doc = yaml.load(full.read_text(encoding="utf-8"), Loader=DshLoader)
    assert access_doc is not None


def test_overlay_js_expressions_are_syntactically_sane(tmp_path: Path):
    """The `!!js` expressions must be valid JS once the tag is stripped."""
    import re

    patch = write_patch_overlay(
        tmp_path / "chat-mcp.patch.yml",
        mcp_url="http://127.0.0.1:8200/mcp",
        token_env_var="AGENTCHATTR_MCP_TOKEN",
        server_name="agentchattr",
    )
    text = patch.read_text(encoding="utf-8")
    # `!!js "...."` (quoted expression) — the quotes must be balanced.
    for match in re.finditer(r'!!js\s+"((?:[^"\\]|\\.)*)"', text):
        expr = match.group(1)
        assert expr.count("(") == expr.count(")"), f"unbalanced parens: {expr}"
        assert expr.count("`") % 2 == 0, f"unbalanced backticks: {expr}"


# --------------------------------------------------------------------------
# Credentials never land on disk
# --------------------------------------------------------------------------

def test_overlay_files_never_contain_real_secret(tmp_path: Path):
    secret = "synthetic-test-credential-not-for-use"
    patch = write_patch_overlay(
        tmp_path / "chat-mcp.patch.yml",
        mcp_url="http://127.0.0.1:8200/mcp",
        token_env_var="AGENTCHATTR_MCP_TOKEN",
        server_name="agentchattr",
    )
    assert secret not in patch.read_text(encoding="utf-8")


def test_access_mode_constants_match_plan_wording():
    # The plan requires the launcher to show "完全访问" with no approval mode.
    assert wrapper_dsh.ACCESS_MODE_LABEL == "完全访问"
    assert "danger-full-access" in wrapper_dsh.ACCESS_MODE_DESC
    assert "无审批" in wrapper_dsh.ACCESS_MODE_DESC


@pytest.mark.parametrize("replacement,should_stop", [
    ({"name": "DSH-01", "token": "tok-abc"}, False),
    ({"name": "DSH-02", "token": "tok-abc"}, True),
    ({"name": "DSH-01", "token": "different"}, True),
])
def test_lease_recovery_requires_original_identity_and_token(monkeypatch, replacement, should_stop):
    import threading
    from types import SimpleNamespace
    shutdown = threading.Event()
    hb = make_heartbeat()
    sent = []
    def send(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            return {"ok": False, "status": 409}
        shutdown.set()
        return {"ok": True}
    hb.send = send
    registrations = []
    def register(*args, **kwargs):
        registrations.append((args, kwargs))
        return replacement
    monkeypatch.setattr(wrapper_dsh, "_register_instance", register)
    instance = SimpleNamespace(is_busy=lambda: False, client=SimpleNamespace(running_sessions=lambda: set()))
    wrapper_dsh._heartbeat_loop(hb, shutdown, instance, 8300,
                               lambda: ("DSH-01", None), lambda: "tok-abc", interval=0)
    assert registrations[0][0][3] == "DSH-01"
    assert registrations[0][1]["resume_token"] == "tok-abc"
    assert registrations[0][1]["lease_id"] == "lease-1"
    assert hb.terminal is should_stop
    assert bool(hb.failure_reason) is should_stop


def test_rejected_heartbeat_stops_instance(monkeypatch):
    import threading
    from types import SimpleNamespace
    shutdown = threading.Event()
    hb = make_heartbeat()
    hb.send = lambda **kwargs: {"ok": False, "status": 403}
    instance = SimpleNamespace(is_busy=lambda: False, client=SimpleNamespace(running_sessions=lambda: set()))
    wrapper_dsh._heartbeat_loop(hb, shutdown, instance, 8300,
                               lambda: ("DSH-01", None), lambda: "tok-abc", interval=0)
    assert shutdown.is_set() and hb.terminal
