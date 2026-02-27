"""Tests for config.toml loading and trigger_cooldown values."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.toml"


def load_config():
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def test_config_loads():
    """config.toml must be valid TOML and load without errors."""
    config = load_config()
    assert isinstance(config, dict)


def test_required_sections_present():
    """Top-level sections server, agents, mcp must exist."""
    config = load_config()
    for section in ("server", "agents", "mcp"):
        assert section in config, f"Missing required section: [{section}]"


def test_each_agent_has_required_fields():
    """Every agent entry must have command, cwd, color, and label."""
    config = load_config()
    required = {"command", "cwd", "color", "label"}
    for name, agent in config["agents"].items():
        missing = required - agent.keys()
        assert not missing, f"Agent '{name}' missing fields: {missing}"


def test_trigger_cooldown_is_positive_float():
    """trigger_cooldown, when set, must be a positive number."""
    config = load_config()
    for name, agent in config["agents"].items():
        if "trigger_cooldown" in agent:
            val = agent["trigger_cooldown"]
            assert isinstance(val, (int, float)), (
                f"Agent '{name}': trigger_cooldown must be a number, got {type(val)}"
            )
            assert val > 0, (
                f"Agent '{name}': trigger_cooldown must be > 0, got {val}"
            )


def test_gemini_cooldown_greater_than_default():
    """Gemini's cooldown should be higher than other agents to handle its slower TUI."""
    config = load_config()
    agents = config["agents"]
    if "gemini" not in agents or "trigger_cooldown" not in agents["gemini"]:
        return  # not configured, skip

    gemini_cooldown = agents["gemini"]["trigger_cooldown"]
    for name, agent in agents.items():
        if name == "gemini":
            continue
        other_cooldown = agent.get("trigger_cooldown", 2.0)
        assert gemini_cooldown >= other_cooldown, (
            f"Gemini cooldown ({gemini_cooldown}s) should be >= {name} cooldown ({other_cooldown}s)"
        )


def test_mcp_ports_are_different():
    """http_port and sse_port must not clash."""
    config = load_config()
    mcp = config["mcp"]
    assert mcp["http_port"] != mcp["sse_port"], "MCP http_port and sse_port must differ"


def test_server_port_not_conflicting_with_mcp():
    """Server port must not overlap with MCP ports."""
    config = load_config()
    server_port = config["server"]["port"]
    mcp = config["mcp"]
    assert server_port != mcp["http_port"], "Server port clashes with MCP http_port"
    assert server_port != mcp["sse_port"], "Server port clashes with MCP sse_port"
