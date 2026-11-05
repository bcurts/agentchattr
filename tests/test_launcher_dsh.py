"""Wrapper selection tests for the DSH agent type (launcher integration).

Verifies that:
  * `dsh` is routed to its dedicated adapter (`wrapper_dsh.py`) instead of the
    generic terminal-injection `wrapper.py`
  * the other CLI agents (codex / kimi / opencode / claude) keep their original
    launch path — this is the regression guard the plan requires
  * `--no-restart` lands in the right argv position for both paths
  * the packed (frozen) internal dispatch uses the `wrapper-dsh` subcommand
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launcher_supervisor import (  # noqa: E402
    _ADAPTER_ENTRY_MAP,
    _INTERNAL_ADAPTER_ROLE,
    Launcher,
)

CONFIG = {
    "server": {"host": "127.0.0.1", "port": 8300, "data_dir": "./data"},
    "mcp": {"http_port": 8200, "sse_port": 8201},
    "agents": {
        "dsh": {
            "label": "DeepSeek Harness",
            "command": "dsh",
            "cwd": "..",
            "color": "#4d6bfe",
            "runtime_dir": "./dsh-runtime/dsh-0.1.5",
            "provider": "deepseek-official",
            "model": "deepseek-v4-flash",
            "mode_label": "完全访问",
            "mode_desc": "完全访问模式（免审批执行，可修改文件与运行命令）",
        },
        "codex": {"label": "Codex", "command": "codex", "cwd": "..", "color": "#10a37f"},
        "claude": {"label": "Claude", "command": "claude", "cwd": "..", "color": "#da7756"},
        "kimi": {"label": "Kimi", "command": "kimi", "cwd": "..", "color": "#1783ff"},
        "opencode": {"label": "OpenCode", "command": "opencode", "cwd": "..", "color": "#f97316"},
    },
}


@pytest.fixture()
def launcher() -> Launcher:
    return Launcher(root=ROOT, config=CONFIG)


# --------------------------------------------------------------------------
# Adapter routing
# --------------------------------------------------------------------------

def test_dsh_uses_dedicated_adapter(launcher: Launcher):
    cmd = launcher._wrapper_command("dsh")
    assert cmd[-1] == "dsh"
    assert Path(cmd[1]).name == "wrapper_dsh.py"


def test_codex_keeps_generic_wrapper(launcher: Launcher):
    cmd = launcher._wrapper_command("codex")
    assert Path(cmd[1]).name == "wrapper.py"
    assert cmd[-1] == "codex"


def test_kimi_keeps_generic_wrapper(launcher: Launcher):
    assert Path(launcher._wrapper_command("kimi")[1]).name == "wrapper.py"


def test_opencode_keeps_generic_wrapper(launcher: Launcher):
    assert Path(launcher._wrapper_command("opencode")[1]).name == "wrapper.py"


def test_claude_keeps_generic_wrapper(launcher: Launcher):
    assert Path(launcher._wrapper_command("claude")[1]).name == "wrapper.py"


def test_adapter_map_only_contains_dsh():
    assert _ADAPTER_ENTRY_MAP == {"dsh": "wrapper_dsh.py"}


# --------------------------------------------------------------------------
# --no-restart placement
# --------------------------------------------------------------------------

def test_no_restart_added_for_dsh_adapter(launcher: Launcher):
    cmd = launcher._ensure_no_restart_arg(launcher._wrapper_command("dsh"))
    assert "--no-restart" in cmd
    # base name must stay immediately after the adapter path
    idx = cmd.index(str(ROOT / "wrapper_dsh.py"))
    assert cmd[idx - 1].endswith("python.exe") or "python" in cmd[idx - 1].lower()
    assert cmd[idx + 1] == "dsh"
    assert cmd[idx + 2] == "--no-restart"


def test_no_restart_added_for_generic_wrapper(launcher: Launcher):
    cmd = launcher._ensure_no_restart_arg(launcher._wrapper_command("codex"))
    idx = cmd.index(str(ROOT / "wrapper.py"))
    assert cmd[idx + 1] == "codex"
    assert cmd[idx + 2] == "--no-restart"


def test_no_restart_is_idempotent(launcher: Launcher):
    once = launcher._ensure_no_restart_arg(launcher._wrapper_command("dsh"))
    twice = launcher._ensure_no_restart_arg(once)
    assert twice.count("--no-restart") == 1


# --------------------------------------------------------------------------
# Frozen (packaged) dispatch
# --------------------------------------------------------------------------

def test_frozen_dsh_uses_internal_subcommand(monkeypatch, launcher: Launcher):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = launcher._wrapper_command("dsh")
    assert cmd[1] == "--agentchattr-internal"
    assert cmd[2] == _INTERNAL_ADAPTER_ROLE
    assert cmd[3] == "dsh"


def test_frozen_dsh_no_restart_placement(monkeypatch, launcher: Launcher):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = launcher._ensure_no_restart_arg(launcher._wrapper_command("dsh"))
    assert cmd[1:4] == ["--agentchattr-internal", _INTERNAL_ADAPTER_ROLE, "dsh"]
    assert cmd[4] == "--no-restart"


def test_frozen_codex_uses_generic_internal_subcommand(monkeypatch, launcher: Launcher):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = launcher._wrapper_command("codex")
    assert cmd[1:4] == ["--agentchattr-internal", "wrapper", "codex"]


# --------------------------------------------------------------------------
# Template metadata surfaced to the UI
# --------------------------------------------------------------------------

def test_dsh_template_is_registered(launcher: Launcher):
    template = launcher._templates.get("dsh")
    assert template is not None
    assert template.label == "DeepSeek Harness"
    assert template.color == "#4d6bfe"


def test_dsh_shows_full_access_mode_label(launcher: Launcher):
    template = launcher._templates["dsh"]
    assert template.mode_label == "完全访问"
    assert "免审批" in template.mode_desc


def test_dsh_config_carries_provider_and_model(launcher: Launcher):
    cfg = launcher._config["agents"]["dsh"]
    assert cfg["provider"] == "deepseek-official"
    assert cfg["model"] == "deepseek-v4-flash"
    assert cfg["runtime_dir"] == "./dsh-runtime/dsh-0.1.5"


def test_existing_agents_unchanged_in_config(launcher: Launcher):
    """Regression guard: adding dsh must not disturb other agent entries."""
    for base, expected in (
        ("codex", "#10a37f"),
        ("claude", "#da7756"),
        ("kimi", "#1783ff"),
        ("opencode", "#f97316"),
    ):
        assert launcher._config["agents"][base]["color"] == expected
        assert launcher._templates[base].color == expected


def test_opencode_keeps_its_auto_mode_label(launcher: Launcher):
    """opencode's "Auto" label must not be clobbered by the dsh defaults."""
    from launcher_supervisor import _MODE_DESC_MAP, _MODE_LABEL_MAP

    assert _MODE_LABEL_MAP["opencode"] == "Auto"
    assert _MODE_LABEL_MAP["dsh"] == "完全访问"
    assert "目录外直接拒绝" in _MODE_DESC_MAP["opencode"]
    assert "免审批" in _MODE_DESC_MAP["dsh"]
