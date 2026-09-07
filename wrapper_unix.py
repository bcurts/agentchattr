"""Mac/Linux agent injection — pastes the prompt into the agent CLI via tmux.

Called by wrapper.py on Mac and Linux. Requires tmux to be installed.
  - Mac:   brew install tmux
  - Linux: apt install tmux  (or yum, pacman, etc.)

How it works:
  1. Creates a tmux session running the agent CLI
  2. Queue watcher delivers the prompt as one bracketed paste
     ('tmux load-buffer' + 'tmux paste-buffer -p'), then presses Enter
  3. Wrapper attaches to the session so you see the full TUI
  4. Ctrl+B, D to detach (agent keeps running in background)
"""

import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid


def _session_exists(session_name: str) -> bool:
    """Return True while the tmux session is still alive."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


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


def _pane_id(tmux_session: str) -> str | None:
    """Resolve the session's active pane once, so paste and Enter hit the same pane."""
    result = subprocess.run(
        ["tmux", "display-message", "-p", "-t", tmux_session, "#{pane_id}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    pane = result.stdout.decode(errors="replace").strip()
    return pane or None


def _drop_buffer(name: str) -> None:
    subprocess.run(["tmux", "delete-buffer", "-b", name], capture_output=True)


def inject(text: str, *, tmux_session: str, delay: float = 0.3) -> bool:
    """Deliver text to the agent CLI as ONE bracketed paste, then press Enter.

    Why a paste and not send-keys: a pty's raw input queue is finite (1024
    bytes on macOS), so a single long `send-keys -l` can reach the CLI as
    several reads. Claude Code treats a large read as a paste and, when typed
    text follows a paste before Enter, submits only the typed text: measured on
    macOS, a 1204-byte prompt arrived as its last 182 characters. With
    `paste-buffer -p`, tmux wraps the text in ESC[200~ / ESC[201~ when the CLI
    has enabled bracketed paste mode, and those delimiters let the CLI
    reassemble one paste across split reads. A CLI that never asked for
    bracketed paste gets the plain bytes, exactly as before.

    Returns True only when tmux accepted both the paste and Enter. On any
    failure, a non-zero exit or an exception launching tmux, it prints a
    diagnostic, cleans up its buffer on a best-effort basis, sends nothing
    further, and returns False (the queue watcher swallows exceptions, so a
    raise alone would be invisible).
    """
    buffer_name = f"agentchattr-inject-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        return _deliver(text, tmux_session, buffer_name, delay)
    except Exception as exc:  # launching tmux itself failed, not a non-zero exit
        print(f"  INJECT FAILED: {type(exc).__name__}: {exc}")
        try:
            _drop_buffer(buffer_name)
        except Exception:
            pass
        return False


def _deliver(text: str, tmux_session: str, buffer_name: str, delay: float) -> bool:
    """The paste-then-Enter sequence; every tmux exit status is checked."""
    pane = _pane_id(tmux_session)
    if pane is None:
        print(f"  INJECT FAILED: no pane for tmux session {tmux_session!r}")
        return False

    loaded = subprocess.run(
        ["tmux", "load-buffer", "-b", buffer_name, "-"],
        input=text.encode("utf-8"),
        capture_output=True,
    )
    if loaded.returncode != 0:
        print(f"  INJECT FAILED: load-buffer exit {loaded.returncode}: "
              f"{loaded.stderr.decode(errors='replace').strip()}")
        _drop_buffer(buffer_name)
        return False

    # -p: bracket the paste if the pane asked for it; -d: drop the buffer after.
    pasted = subprocess.run(
        ["tmux", "paste-buffer", "-p", "-d", "-b", buffer_name, "-t", pane],
        capture_output=True,
    )
    if pasted.returncode != 0:
        print(f"  INJECT FAILED: paste-buffer exit {pasted.returncode}: "
              f"{pasted.stderr.decode(errors='replace').strip()}")
        _drop_buffer(buffer_name)
        return False

    # Scale delay with text length so longer prompts get more processing time
    time.sleep(max(delay, len(text) * 0.001))
    entered = subprocess.run(
        ["tmux", "send-keys", "-t", pane, "Enter"],
        capture_output=True,
    )
    if entered.returncode != 0:
        print(f"  INJECT FAILED: Enter exit {entered.returncode}: "
              f"{entered.stderr.decode(errors='replace').strip()}")
        return False
    return True


def get_activity_checker(session_name, trigger_flag=None):
    """Return a callable that detects tmux pane output by hashing content."""
    last_hash = [None]

    def check():
        # External trigger: queue watcher injected a message
        if trigger_flag is not None and trigger_flag[0]:
            trigger_flag[0] = False
            return True
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session_name, "-p"],
                capture_output=True, timeout=2,
            )
            h = hash(result.stdout)
            changed = last_hash[0] is not None and h != last_hash[0]
            last_hash[0] = h
            return changed
        except Exception:
            return False

    return check


def run_agent(
    command,
    extra_args,
    cwd,
    env,
    queue_file,
    agent,
    no_restart,
    start_watcher,
    strip_env=None,
    pid_holder=None,
    session_name=None,
    inject_env=None,
    inject_delay: float = 0.3,
):
    """Run agent inside a tmux session, inject via tmux send-keys."""
    _check_tmux()

    session_name = session_name or f"agentchattr-{agent}"
    agent_cmd = " ".join(
        [shlex.quote(command)] + [shlex.quote(a) for a in extra_args]
    )

    # Build env(1) prefix for the command INSIDE the tmux session.
    # subprocess.run(env=...) only affects the tmux client binary — the
    # session shell inherits from the tmux server instead.  Use env(1)
    # to set (-u to unset, VAR=val to inject) vars in the actual session.
    env_parts = []
    if strip_env:
        env_parts.extend(f"-u {shlex.quote(v)}" for v in strip_env)
    if inject_env:
        env_parts.extend(
            f"{shlex.quote(k)}={shlex.quote(v)}"
            for k, v in inject_env.items()
        )
    if env_parts:
        agent_cmd = f"env {' '.join(env_parts)} {agent_cmd}"

    # Resolve cwd to absolute path (tmux -c needs it)
    from pathlib import Path
    abs_cwd = str(Path(cwd).resolve())

    # Wire up injection with the tmux session name
    inject_fn = lambda text: inject(text, tmux_session=session_name, delay=inject_delay)
    start_watcher(inject_fn)

    print(f"  Using tmux session: {session_name}")
    print(f"  Detach: Ctrl+B, D  (agent keeps running)")
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
            if _session_exists(session_name):
                # Session still alive — user detached, agent running in background.
                # Keep the wrapper alive so the local proxy and heartbeats survive.
                print(f"\n  Detached. {agent.capitalize()} still running in tmux.")
                print(f"  Reattach: tmux attach -t {session_name}")
                while _session_exists(session_name):
                    time.sleep(1)
                break

            # Session gone — agent exited
            if no_restart:
                break

            print(f"\n  {agent.capitalize()} exited.")
            print(f"  Restarting in 3s... (Ctrl+C to quit)")
            time.sleep(3)
        except KeyboardInterrupt:
            # Kill the tmux session on Ctrl+C
            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                capture_output=True,
            )
            break
