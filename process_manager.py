"""ProcessManager — spawn and track agent subprocesses.

Provides ManagedAgent (per-process state machine + log ring buffer) and
ProcessManager (launch / stop / list / persist).  Used by the FastAPI server
to manage wrapper.py subprocesses from the web UI.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flag presets per agent type
# ---------------------------------------------------------------------------

AGENT_FLAG_PRESETS: dict[str, list[dict[str, str]]] = {
    "claude": [
        {"label": "Skip permissions", "flag": "--dangerously-skip-permissions"},
        {"label": "Worktree", "flag": "--worktree"},
    ],
    "codex": [
        {"label": "Bypass approvals", "flag": "--dangerously-bypass-approvals-and-sandbox"},
    ],
    "gemini": [
        {"label": "Yolo", "flag": "--yolo"},
        {"label": "Sandbox", "flag": "--sandbox"},
    ],
    "qwen": [
        {"label": "Yolo", "flag": "--yolo"},
    ],
}


# ---------------------------------------------------------------------------
# ManagedAgent — wraps a single subprocess
# ---------------------------------------------------------------------------

class ManagedAgent:
    """Tracks a single subprocess with a state machine and log ring buffer."""

    VALID_STATES = ("starting", "running", "crashed", "stopped")

    def __init__(
        self,
        name: str,
        proc: subprocess.Popen,
        command: str,
        flags: list[str],
        extra_args: list[str],
        cwd: str,
        *,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
    ):
        self.name = name
        self.proc = proc
        self.command = command
        self.flags = list(flags)
        self.extra_args = list(extra_args)
        self.cwd = cwd
        self.pid = proc.pid
        self.log_buffer: deque[str] = deque(maxlen=100)
        self._state = "starting"
        self._on_log = on_log
        self._on_state_change = on_state_change
        self._stop_event = threading.Event()

        # Daemon threads so they don't block interpreter shutdown
        self._reader_thread = threading.Thread(
            target=self._drain_stdout, daemon=True, name=f"reader-{name}"
        )
        self._waiter_thread = threading.Thread(
            target=self._wait_for_exit, daemon=True, name=f"waiter-{name}"
        )
        self._reader_thread.start()
        self._waiter_thread.start()

    # -- state property -----------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, new: str) -> None:
        old = self._state
        if old == new:
            return
        self._state = new
        log.debug("agent %s: %s -> %s", self.name, old, new)
        if self._on_state_change:
            try:
                self._on_state_change(self.name, old, new)
            except Exception:
                log.exception("on_state_change callback failed for %s", self.name)

    # -- background threads -------------------------------------------------

    def _drain_stdout(self) -> None:
        """Read subprocess stdout line-by-line into the ring buffer."""
        try:
            assert self.proc.stdout is not None
            for raw_line in self.proc.stdout:
                if self._stop_event.is_set():
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                self.log_buffer.append(line)
                # Transition starting -> running on first output
                if self._state == "starting":
                    self.state = "running"
                if self._on_log:
                    try:
                        self._on_log(self.name, line)
                    except Exception:
                        pass
        except Exception:
            log.exception("reader thread error for %s", self.name)

    def _wait_for_exit(self) -> None:
        """Wait for the subprocess to terminate and update state."""
        retcode = self.proc.wait()
        # If we explicitly stopped it, state is already "stopped"
        if self._state != "stopped":
            self.state = "crashed" if retcode != 0 else "stopped"

    # -- public API ---------------------------------------------------------

    def stop(self) -> None:
        """Gracefully terminate the subprocess (SIGTERM, then SIGKILL after 5s)."""
        if self.proc.poll() is not None:
            # Already dead
            self.state = "stopped"
            return

        self._stop_event.set()
        self.state = "stopped"

        # On Windows there is no SIGTERM for subprocesses; terminate() sends
        # the platform-appropriate signal.
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pid": self.pid,
            "state": self.state,
            "command": self.command,
            "flags": self.flags,
            "extra_args": self.extra_args,
            "cwd": self.cwd,
            "log_lines": len(self.log_buffer),
        }


# ---------------------------------------------------------------------------
# ProcessManager
# ---------------------------------------------------------------------------

class ProcessManager:
    """Spawn, track, and persist agent subprocesses."""

    def __init__(
        self,
        data_dir: Path,
        server_port: int,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
    ):
        self.data_dir = Path(data_dir)
        self.server_port = server_port
        self._on_log = on_log
        self._on_state_change = on_state_change
        self._agents: dict[str, ManagedAgent] = {}
        self._lock = threading.Lock()

        self.data_dir.mkdir(parents=True, exist_ok=True)

    # -- name allocation ----------------------------------------------------

    def _assign_name(self, base: str) -> str:
        """Return *base* if unused, else base-2, base-3, ..."""
        with self._lock:
            if base not in self._agents:
                return base
            n = 2
            while f"{base}-{n}" in self._agents:
                n += 1
            return f"{base}-{n}"

    # -- launch state persistence -------------------------------------------

    @property
    def _state_file(self) -> Path:
        return self.data_dir / "launch_state.json"

    def _save_launch_state(self) -> None:
        """Persist running agents' configs to launch_state.json."""
        entries = []
        for agent in self._agents.values():
            if agent.state in ("starting", "running"):
                entries.append({
                    "name": agent.name,
                    "command": agent.command,
                    "flags": agent.flags,
                    "extra_args": agent.extra_args,
                    "cwd": agent.cwd,
                })
        try:
            self._state_file.write_text(
                json.dumps(entries, indent=2), encoding="utf-8"
            )
        except Exception:
            log.exception("failed to save launch state")

    def get_restore_state(self) -> list[dict[str, Any]]:
        """Load launch_state.json if it exists, else return []."""
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                log.exception("failed to read launch state")
        return []

    def clear_restore_state(self) -> None:
        """Delete the launch state file."""
        try:
            self._state_file.unlink(missing_ok=True)
        except Exception:
            log.exception("failed to clear launch state")

    # -- core API -----------------------------------------------------------

    def launch(
        self,
        base: str,
        command: str,
        flags: list[str],
        extra_args: list[str],
        cwd: str,
        instance_label: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a subprocess and begin tracking it.

        Returns ``{"ok": True, "name": ..., "pid": ...}`` on success,
        or ``{"ok": False, "error": ...}`` on failure.
        """
        # Validate cwd
        cwd_path = Path(cwd)
        if not cwd_path.exists():
            return {"ok": False, "error": f"cwd does not exist: {cwd}"}

        name = self._assign_name(instance_label or base)

        cmd_list = [command] + flags + extra_args

        try:
            proc = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(cwd_path),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        agent = ManagedAgent(
            name=name,
            proc=proc,
            command=command,
            flags=flags,
            extra_args=extra_args,
            cwd=str(cwd_path),
            on_log=self._on_log,
            on_state_change=self._on_state_change,
        )

        with self._lock:
            self._agents[name] = agent

        self._save_launch_state()

        return {"ok": True, "name": name, "pid": proc.pid}

    def stop(self, name: str) -> dict[str, Any]:
        """Stop a managed agent by name."""
        with self._lock:
            agent = self._agents.get(name)
        if agent is None:
            return {"ok": False, "error": f"unknown agent: {name}"}

        agent.stop()
        self._save_launch_state()
        return {"ok": True}

    def get_logs(self, name: str) -> list[str]:
        """Return the log ring buffer for *name*."""
        with self._lock:
            agent = self._agents.get(name)
        if agent is None:
            return []
        return list(agent.log_buffer)

    def list_managed(self) -> list[dict[str, Any]]:
        """Return a snapshot list of all tracked agents."""
        with self._lock:
            agents = list(self._agents.values())
        return [a.to_dict() for a in agents]

    def shutdown(self) -> None:
        """Stop every tracked agent."""
        with self._lock:
            names = list(self._agents.keys())
        for name in names:
            self.stop(name)
