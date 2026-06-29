"""Compatibility entry point for the web launcher control panel."""

from typing import Any

from launcher_supervisor import AgentTemplate, Launcher, LogEvent, ManagedProcess


def _web_registry_provider() -> Any:
    """Return app.registry lazily to avoid importing app from reusable supervisor code."""
    try:
        import app as _app

        return getattr(_app, "registry", None)
    except Exception:
        return None


def _web_role_setter(name: str, role: str) -> None:
    """Apply roles through the existing web runtime bridge."""
    import mcp_bridge

    mcp_bridge.set_role(name, role)


launcher = Launcher(
    registry_provider=_web_registry_provider,
    role_setter=_web_role_setter,
)


__all__ = [
    "AgentTemplate",
    "Launcher",
    "LogEvent",
    "ManagedProcess",
    "launcher",
]
