"""Mac/Linux agent injection — uses tmux send-keys to type into the agent CLI.

Called by wrapper.py on Mac and Linux. Requires tmux to be installed.
  - Mac:   brew install tmux
  - Linux: apt install tmux  (or yum, pacman, etc.)

How it works:
  1. Creates a tmux session running the agent CLI
  2. Queue watcher sends keystrokes via 'tmux send-keys'
  3. Wrapper attaches to the session so you see the full TUI
  4. Ctrl+B, D to detach (agent keeps running in background)
"""

import shlex
import shutil
import subprocess
import sys
import time


def _check_tmux():
    """Verify tmux is installed, exit with helpful message if not."""
    if shutil.which("tmux"):
        return
    print("\n  Error: tmux is required for auto-trigger on Mac/Linux.")
    if sys.platform == "darwin":
        print("  Install: brew install tmux")
    else:
        print("  Install: apt install tmux  (or yum/pacman equivalent)")
    sys.exit(1)


def inject(text: str, *, tmux_session: str):
    """Send text + Enter to a tmux session via send-keys."""
    # Send Escape first to clear any pending/stacked input in the TUI.
    # This prevents multiple rapid triggers from piling up in the input field
    # without submitting (observed with Gemini CLI).
    # Note: Escape is a soft clear — it dismisses autocomplete/history popups
    # and returns to a blank prompt. It will not interrupt an in-progress
    # model response. If the agent is mid-response, the inject will be queued
    # naturally by the per-agent trigger_cooldown in wrapper.py.
    subprocess.run(
        ["tmux", "send-keys", "-t", tmux_session, "Escape"],
        capture_output=True,
    )
    # Use -l to send text literally (avoids misinterpreting as key names),
    # then send Enter as a separate key press
    subprocess.run(
        ["tmux", "send-keys", "-t", tmux_session, "-l", text],
        capture_output=True,
    )
    # Enter submits prompt input for supported CLIs in this setup.
    subprocess.run(
        ["tmux", "send-keys", "-t", tmux_session, "Enter"],
        capture_output=True,
    )


def run_agent(command, extra_args, cwd, env, queue_file, agent, no_restart, start_watcher):
    """Run agent inside a tmux session, inject via tmux send-keys."""
    _check_tmux()

    session_name = f"agentchattr-{agent}"
    agent_cmd = " ".join(
        [shlex.quote(command)] + [shlex.quote(a) for a in extra_args]
    )

    # Resolve cwd to absolute path (tmux -c needs it)
    from pathlib import Path
    abs_cwd = str(Path(cwd).resolve())

    # Wire up injection with the tmux session name
    def inject_fn(text):
        inject(text, tmux_session=session_name)
    start_watcher(inject_fn)

    print(f"  Using tmux session: {session_name}")
    print("  Detach: Ctrl+B, D  (agent keeps running)")
    print(f"  Reattach: tmux attach -t {session_name}\n")

    while True:
        try:
            # Clean up stale session from a previous crash
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                capture_output=True,
            )

            # Create tmux session running the agent CLI
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session_name,
                 "-c", abs_cwd, agent_cmd],
                env=env,
            )
            if result.returncode != 0:
                print(f"  Error: failed to create tmux session (exit {result.returncode})")
                break

            # Attach — blocks until agent exits or user detaches (Ctrl+B, D)
            subprocess.run(["tmux", "attach-session", "-t", session_name])

            # Check: did the agent exit, or did the user just detach?
            check = subprocess.run(
                ["tmux", "has-session", "-t", session_name],
                capture_output=True,
            )
            if check.returncode == 0:
                # Session still alive — user detached, agent running in background
                print(f"\n  Detached. {agent.capitalize()} still running in tmux.")
                print(f"  Reattach: tmux attach -t {session_name}")
                break

            # Session gone — agent exited
            if no_restart:
                break

            print(f"\n  {agent.capitalize()} exited.")
            print("  Restarting in 3s... (Ctrl+C to quit)")
            time.sleep(3)
        except KeyboardInterrupt:
            # Kill the tmux session on Ctrl+C
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                capture_output=True,
            )
            break
