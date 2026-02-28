"""Agent wrapper — runs the real interactive CLI with auto-trigger on @mentions.

Usage:
    python wrapper.py claude     # Claude Code with chat auto-trigger
    python wrapper.py codex      # Codex with chat auto-trigger

Cross-platform:
  - Windows: injects keystrokes via Win32 WriteConsoleInput  (wrapper_windows.py)
  - Mac/Linux: injects keystrokes via tmux send-keys          (wrapper_unix.py)

How it works:
  1. Starts the agent CLI in an interactive terminal (full TUI)
  2. Watches the queue file in background for @mentions from the chat room
  3. When triggered, injects "chat - use mcp" + Enter into the agent
  4. The agent picks up the prompt as if the user typed it
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
log = logging.getLogger(__name__)

SERVER_NAME = "agentchattr"
DEFAULT_TRIGGER_COOLDOWN_SECONDS = 2.0

# ---------------------------------------------------------------------------
# MCP auto-config — ensure .mcp.json and .gemini/settings.json exist
# ---------------------------------------------------------------------------

def _ensure_mcp(project_dir: Path, mcp_cfg: dict):
    """Create MCP config files in the agent's working directory if missing."""
    http_port = mcp_cfg.get("http_port", 8200)
    sse_port = mcp_cfg.get("sse_port", 8201)
    http_url = f"http://127.0.0.1:{http_port}/mcp"
    sse_url = f"http://127.0.0.1:{sse_port}/sse"

    # --- Claude (.mcp.json) ---
    _ensure_json_mcp(project_dir / ".mcp.json", http_url)

    # --- Gemini (.gemini/settings.json) ---
    _ensure_json_mcp(project_dir / ".gemini" / "settings.json", sse_url, transport="sse")

    # --- Codex (.codex/config.toml) ---
    _ensure_codex_mcp(project_dir / ".codex" / "config.toml", http_url)


def _ensure_json_mcp(mcp_file: Path, url: str, transport: str = "http"):
    """Add agentchattr to a JSON MCP config file (Claude / Gemini)."""
    mcp_file.parent.mkdir(parents=True, exist_ok=True)

    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text("utf-8"))
        except json.JSONDecodeError:
            print(f"  MCP: WARNING — {mcp_file} has invalid JSON, can't add {SERVER_NAME}")
            return
    else:
        data = {}

    servers = data.setdefault("mcpServers", {})
    if SERVER_NAME in servers:
        return

    servers[SERVER_NAME] = {"type": transport, "url": url}
    mcp_file.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    print(f"  MCP: added {SERVER_NAME} to {mcp_file}")


def _ensure_codex_mcp(toml_file: Path, url: str):
    """Add agentchattr to Codex's TOML config file."""
    toml_file.parent.mkdir(parents=True, exist_ok=True)
    section = f"mcp_servers.{SERVER_NAME}"

    if toml_file.exists():
        content = toml_file.read_text("utf-8")
        if section in content:
            return
    else:
        content = ""

    block = f'\n[{section}]\nurl = "{url}"\n'
    toml_file.write_text(content + block, "utf-8")
    print(f"  MCP: added {SERVER_NAME} to {toml_file}")


# ---------------------------------------------------------------------------
# Queue Watcher — polls for @mention triggers, calls platform inject function
# ---------------------------------------------------------------------------

class MonitorState:
    def __init__(self):
        self.last_inject_at = 0.0
        self.lock = threading.Lock()

    def record_inject(self):
        with self.lock:
            self.last_inject_at = time.time()

    def get_last_inject(self):
        with self.lock:
            return self.last_inject_at


def _notify_recovery(data_dir: Path, agent_name: str):
    """Write a flag file that the server picks up and broadcasts as a system message."""
    try:
        flag = data_dir / f"{agent_name}_recovered"
        flag.write_text(agent_name, "utf-8")
    except Exception:
        pass


def _trigger_cooldown_seconds(agent_name: str, agent_cfg: dict) -> float:
    """Per-agent debounce to avoid spamming interactive CLIs with repeated wake commands.

    Reads 'trigger_cooldown' from the agent's config section (config.toml).
    Falls back to DEFAULT_TRIGGER_COOLDOWN_SECONDS if not set.
    """
    return float(agent_cfg.get("trigger_cooldown", DEFAULT_TRIGGER_COOLDOWN_SECONDS))


def _queue_watcher(queue_file: Path, agent_name: str, inject_fn, agent_cfg: dict, state: MonitorState):
    """Poll queue file; call inject_fn('chat - use mcp') when triggered."""
    cooldown = _trigger_cooldown_seconds(agent_name, agent_cfg)

    while True:
        try:
            if queue_file.exists() and queue_file.stat().st_size > 0:
                with open(queue_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                queue_file.write_text("")

                has_trigger = False
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                        has_trigger = True
                    except json.JSONDecodeError:
                        pass

                if has_trigger:
                    # Debounce wake-ups so slower TUIs (notably Gemini CLI)
                    # can finish MCP prompts before the next injected command.
                    now = time.time()
                    last_inject_at = state.get_last_inject()
                    elapsed = now - last_inject_at
                    if elapsed < cooldown:
                        time.sleep(cooldown - elapsed)
                    # Small delay to let the TUI settle
                    time.sleep(0.5)
                    ok = inject_fn("chat - use mcp")
                    if ok is not False:  # None (old callers) or True = success
                        state.record_inject()
        except Exception as e:
            log.exception("queue watcher error (agent=%s): %s", agent_name, e)

        time.sleep(1)


def _task_monitor(queue_file: Path, inject_fn, state: MonitorState, timeout_minutes: float):
    """If queue is non-empty but no injection for >timeout, auto-reinject."""
    timeout_seconds = timeout_minutes * 60
    while True:
        try:
            time.sleep(30)  # Check every 30s
            if queue_file.exists() and queue_file.stat().st_size > 0:
                now = time.time()
                last_inject = state.get_last_inject()
                if now - last_inject > timeout_seconds:
                    print(f"  [Monitor] Agent seems stuck (queue non-empty for >{timeout_minutes}m). Re-injecting...")
                    ok = inject_fn("chat - use mcp")
                    if ok is not False:
                        state.record_inject()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Server restart watcher — kills the tmux session when the server restarts
# so the agent reconnects with a fresh MCP session instead of using stale IDs
# ---------------------------------------------------------------------------

def _watch_for_server_restart(data_dir: Path, tmux_session: str, stop_event: threading.Event):
    """Detect server restarts and kill the tmux session so the agent reconnects.

    Waits one extra cycle after detecting a change to confirm the server is
    stable before restarting — avoids killing agents during server mid-boot.
    """
    started_at_file = data_dir / "server_started_at.txt"
    known_start = started_at_file.read_text().strip() if started_at_file.exists() else ""
    pending_restart = False

    while not stop_event.is_set():
        stop_event.wait(30)
        if stop_event.is_set():
            break
        if not started_at_file.exists():
            pending_restart = False
            continue
        current = started_at_file.read_text().strip()
        if current != known_start:
            if pending_restart:
                # Second consecutive cycle with new timestamp — server is stable, restart
                log.info("Server restart confirmed — restarting tmux session %s", tmux_session)
                subprocess.run(["tmux", "send-keys", "-t", tmux_session, "C-c"], capture_output=True)
                known_start = current
                pending_restart = False
            else:
                # First detection — wait one more cycle to confirm
                log.info("Server restart detected for %s — confirming next cycle", tmux_session)
                pending_restart = True
        else:
            pending_restart = False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    # Load config to get valid agent names
    with open(ROOT / "config.toml", "rb") as f:
        config = tomllib.load(f)

    agent_names = list(config.get("agents", {}).keys())

    parser = argparse.ArgumentParser(description="Agent wrapper with chat auto-trigger")
    parser.add_argument("agent", choices=agent_names,
                        help=f"Agent to wrap ({', '.join(agent_names)})")
    parser.add_argument("--no-restart", action="store_true", help="Don't restart on exit")
    args, extra = parser.parse_known_args()

    agent = args.agent
    agent_cfg = config.get("agents", {}).get(agent, {})
    server_cfg = config.get("server", {})
    task_timeout = float(server_cfg.get("agent_task_timeout_minutes", 5.0))

    # Append resume_flag from config if not already manually provided
    resume_flag = agent_cfg.get("resume_flag")
    if resume_flag:
        import shlex
        resume_args = shlex.split(resume_flag)
        if not any(arg in extra for arg in resume_args):
            extra.extend(resume_args)

    cwd = agent_cfg.get("cwd", ".")
    command = agent_cfg.get("command", agent)
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    queue_file = data_dir / f"{agent}_queue.jsonl"

    # Monitor state shared between threads
    state = MonitorState()

    # Flush stale queue entries from previous crashed sessions
    if queue_file.exists():
        queue_file.write_text("", "utf-8")

    # Auto-configure MCP in the agent's working directory so it just works
    mcp_cfg = config.get("mcp", {})
    project_dir = (ROOT / cwd).resolve()
    _ensure_mcp(project_dir, mcp_cfg)

    # Strip CLAUDECODE to avoid "nested session" detection.
    # Also strip any env vars listed in the agent's strip_env config
    # (e.g. ANTHROPIC_API_KEY so Claude uses its stored OAuth credentials).
    strip_vars = {"CLAUDECODE"} | set(agent_cfg.get("strip_env", []))
    env = {k: v for k, v in os.environ.items() if k not in strip_vars}

    # Resolve command on PATH
    resolved = shutil.which(command)
    if not resolved:
        print(f"  Error: '{command}' not found on PATH.")
        print("  Install it first, then try again.")
        sys.exit(1)
    command = resolved

    print(f"  === {agent.capitalize()} Chat Wrapper ===")
    print(f"  @{agent} mentions auto-inject 'chat - use mcp'")
    print(f"  Starting {command} in {cwd}...\n")

    # Helper: start the queue watcher with a given inject function
    # Returns the thread so the monitor can check is_alive()
    _watcher_inject_fn = None
    _watcher_thread = None

    def start_watcher(inject_fn):
        nonlocal _watcher_inject_fn, _watcher_thread
        _watcher_inject_fn = inject_fn
        _watcher_thread = threading.Thread(
            target=_queue_watcher, args=(queue_file, agent, inject_fn, agent_cfg, state), daemon=True
        )
        _watcher_thread.start()
        
        # Start task monitor thread
        threading.Thread(
            target=_task_monitor, args=(queue_file, inject_fn, state, task_timeout), daemon=True
        ).start()

    # Monitor thread: checks watcher health and auto-restarts if dead
    def _watcher_monitor():
        nonlocal _watcher_thread
        while True:
            time.sleep(5)
            if _watcher_thread and not _watcher_thread.is_alive() and _watcher_inject_fn:
                _watcher_thread = threading.Thread(
                    target=_queue_watcher, args=(queue_file, agent, _watcher_inject_fn, agent_cfg, state), daemon=True
                )
                _watcher_thread.start()
                _notify_recovery(data_dir, agent)

    monitor = threading.Thread(target=_watcher_monitor, daemon=True)
    monitor.start()

    # Start server restart watcher — kills tmux session on server restart
    # so the agent process reconnects with a fresh MCP session
    _stop_event = threading.Event()
    tmux_session = f"agentchattr-{agent}"
    server_watcher = threading.Thread(
        target=_watch_for_server_restart,
        args=(data_dir, tmux_session, _stop_event),
        daemon=True,
    )
    server_watcher.start()

    # Dispatch to platform-specific runner
    if sys.platform == "win32":
        from wrapper_windows import run_agent
    else:
        from wrapper_unix import run_agent

    run_agent(
        command=command,
        extra_args=extra,
        cwd=cwd,
        env=env,
        queue_file=queue_file,
        agent=agent,
        no_restart=args.no_restart,
        start_watcher=start_watcher,
        strip_env=list(strip_vars),
    )

    print("  Wrapper stopped.")


if __name__ == "__main__":
    main()
