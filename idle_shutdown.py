"""Idle auto-shutdown: stop the server once the room is empty.

The room counts as empty when no browser tab is connected AND no agent is
connected. The timer only arms after a browser has connected at least once,
so a headless start (start_team.bat before the browser opens) never exits on
its own. A grace period absorbs page refreshes and brief reconnects.
"""

from __future__ import annotations

DEFAULT_GRACE_SECONDS = 30


class IdleShutdown:
    def __init__(self, grace_seconds: float = DEFAULT_GRACE_SECONDS):
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.armed = False
        self.idle_since: float | None = None

    def check(self, browsers: int, agents: int, now: float) -> bool:
        """Record the current room state; return True when it is time to exit."""
        if browsers > 0:
            self.armed = True
        if not self.armed or browsers > 0 or agents > 0:
            self.idle_since = None
            return False
        if self.idle_since is None:
            self.idle_since = now
        return now - self.idle_since >= self.grace_seconds


def from_config(cfg: dict) -> IdleShutdown | None:
    """Build from [server] auto_shutdown / idle_shutdown_seconds; None if disabled."""
    server = cfg.get("server", {})
    if not server.get("auto_shutdown", True):
        return None
    return IdleShutdown(server.get("idle_shutdown_seconds", DEFAULT_GRACE_SECONDS))
