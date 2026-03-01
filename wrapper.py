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
import os
import shutil
import sys
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent

SERVER_NAME = "agentchattr"

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

def _notify_recovery(data_dir: Path, agent_name: str):
    """Write a flag file that the server picks up and broadcasts as a system message."""
    try:
        flag = data_dir / f"{agent_name}_recovered"
        flag.write_text(agent_name, "utf-8")
    except Exception:
        pass


def _queue_watcher(queue_file: Path, agent_name: str, inject_fn):
    """Poll queue file; call inject_fn('chat - use mcp') when triggered."""
    while True:
        try:
            if queue_file.exists() and queue_file.stat().st_size > 0:
                with open(queue_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                queue_file.write_text("")

                has_trigger = False
                channel = "general"
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        has_trigger = True
                        if isinstance(data, dict) and "channel" in data:
                            channel = data["channel"]
                    except json.JSONDecodeError:
                        pass

                if has_trigger:
                    # Small delay to let the TUI settle
                    time.sleep(0.5)
                    inject_fn(f"mcp read #{channel} and if addressed respond in the chat")
        except Exception:
            pass  # Silently continue — monitor will restart if thread dies

        time.sleep(1)


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
    cwd = agent_cfg.get("cwd", ".")
    command_name = agent_cfg.get("command", agent)
    command = command_name
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    queue_file = data_dir / f"{agent}_queue.jsonl"

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
        print(f"  Install it first, then try again.")
        sys.exit(1)
    command = resolved

    print(f"  === {agent.capitalize()} Chat Wrapper ===")
    print(f"  @{agent} mentions auto-inject 'chat - use mcp'")
    print(f"  Starting {command} in {cwd}...\n")

    # Heartbeat — ping the server every 60s to keep presence alive
    server_port = config.get("server", {}).get("port", 8300)
    heartbeat_names = [agent]
    cmd_lower = (command_name or "").lower()
    if cmd_lower in ("codex", "claude", "gemini") and cmd_lower not in heartbeat_names:
        heartbeat_names.append(cmd_lower)

    def _heartbeat():
        import urllib.request
        while True:
            try:
                for name in heartbeat_names:
                    url = f"http://127.0.0.1:{server_port}/api/heartbeat/{name}"
                    req = urllib.request.Request(url, method="POST", data=b"")
                    urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=_heartbeat, daemon=True).start()

    # Helper: start the queue watcher with a given inject function
    # Returns the thread so the monitor can check is_alive()
    _watcher_inject_fn = None
    _watcher_thread = None

    def start_watcher(inject_fn):
        nonlocal _watcher_inject_fn, _watcher_thread
        _watcher_inject_fn = inject_fn
        _watcher_thread = threading.Thread(
            target=_queue_watcher, args=(queue_file, agent, inject_fn), daemon=True
        )
        _watcher_thread.start()

    # Monitor thread: checks watcher health and auto-restarts if dead
    def _watcher_monitor():
        nonlocal _watcher_thread
        while True:
            time.sleep(5)
            if _watcher_thread and not _watcher_thread.is_alive() and _watcher_inject_fn:
                _watcher_thread = threading.Thread(
                    target=_queue_watcher, args=(queue_file, agent, _watcher_inject_fn), daemon=True
                )
                _watcher_thread.start()
                _notify_recovery(data_dir, agent)

    monitor = threading.Thread(target=_watcher_monitor, daemon=True)
    monitor.start()

    # Activity monitor — detect terminal output and report to server
    _activity_checker = None

    def _set_activity_checker(checker):
        nonlocal _activity_checker
        _activity_checker = checker

    def _activity_monitor():
        import urllib.request
        url = f"http://127.0.0.1:{server_port}/api/heartbeat/{agent}"
        last_active = None
        while True:
            time.sleep(1)
            if not _activity_checker:
                continue
            try:
                active = _activity_checker()
                if active != last_active:
                    body = json.dumps({"active": active}).encode()
                    req = urllib.request.Request(
                        url, method="POST", data=body,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=5)
                    last_active = active
            except Exception:
                pass

    threading.Thread(target=_activity_monitor, daemon=True).start()

    # Dispatch to platform-specific runner
    _agent_pid = [None]  # shared mutable — run_agent sets [0] to the child PID

    if sys.platform == "win32":
        from wrapper_windows import run_agent, get_activity_checker
        _set_activity_checker(get_activity_checker(_agent_pid))
    else:
        from wrapper_unix import run_agent, get_activity_checker
        session_name = f"agentchattr-{agent}"
        _set_activity_checker(get_activity_checker(session_name))

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
        pid_holder=_agent_pid,
    )

    print("  Wrapper stopped.")


if __name__ == "__main__":
    main()
