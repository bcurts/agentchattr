"""Automated stale tmux session cleanup for agents."""

import subprocess
import time
import logging
import threading

log = logging.getLogger(__name__)


def get_tmux_sessions() -> list[str]:
    """Get list of active tmux session names."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            check=True
        )
        return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def kill_tmux_session(session_name: str):
    """Kill a tmux session by name."""
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=True
        )
        log.info(f"Killed stale tmux session: {session_name}")
    except subprocess.CalledProcessError:
        log.error(f"Failed to kill tmux session: {session_name}")


class SessionCleanup:
    def __init__(self, config: dict, store=None):
        self._config = config
        self._store = store
        self._cleanup_cfg = config.get("cleanup", {})
        self._enabled = self._cleanup_cfg.get("enabled", False)
        self._timeout = self._cleanup_cfg.get("idle_timeout_minutes", 10) * 60
        self._interval = self._cleanup_cfg.get("check_interval_seconds", 60)
        self._last_online: dict[str, float] = {}  # agent_name -> last_seen_timestamp

    def start(self):
        if not self._enabled:
            return
        threading.Thread(target=self._run_loop, daemon=True).start()
        log.info(f"Session cleanup started (timeout={self._timeout/60}m, interval={self._interval}s)")

    def _run_loop(self):
        while True:
            try:
                self._check_sessions()
            except Exception:
                log.exception("Error in session cleanup loop")
            time.sleep(self._interval)

    def _check_sessions(self):
        from mcp_bridge import is_online
        
        now = time.time()
        agent_names = list(self._config.get("agents", {}).keys())
        sessions = get_tmux_sessions()
        
        for agent in agent_names:
            session_name = f"agentchattr-{agent}"
            if session_name not in sessions:
                continue
            
            online = is_online(agent)
            if online:
                self._last_online[agent] = now
            else:
                last_seen = self._last_online.get(agent)
                if last_seen is None:
                    self._last_online[agent] = now
                    continue
                
                if now - last_seen > self._timeout:
                    kill_tmux_session(session_name)
                    del self._last_online[agent]
                    if self._store:
                        label = self._config.get("agents", {}).get(agent, {}).get("label", agent)
                        self._store.add("system", f"Cleaned up stale tmux session for {label} (offline for >{self._timeout/60}m)")
