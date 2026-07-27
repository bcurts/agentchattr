"""Ezra relay — bridges the chat room to the running Resonant service.

Usage:
    python wrapper_ezra.py

Unlike wrapper.py (spawns a CLI) and wrapper_api.py (OpenAI-compatible
endpoints), this bridge attaches an ALREADY-RUNNING companion service:
Resonant, reached over loopback via its internal-token-gated relay route.
No CLI is spawned, no terminal is needed — runs headless.

How it works:
  1. Registers with the chat server as 'ezra' (POST /api/register).
  2. Heartbeats every 5s (same pattern as wrapper_api.py).
  3. Polls the queue file for @ezra mentions.
  4. On trigger: reads NEW room messages since the per-channel cursor,
     formats them as a transcript, and POSTs to Resonant's
     /api/internal/agentchattr route with the x-internal-token header.
     Resonant routes the turn into a dedicated workroom thread
     (agentchattr-<channel>) — the daily ledger is never touched.
  5. Posts Resonant's reply back to the room via POST /api/send.
  6. On exit: deregisters cleanly.

Config ([agents.ezra] in config.toml):
    type = "api"                # api-style seat: no terminal, no cwd
    label = "Ezra"
    color = "#e8a87c"
    resonant_url = "http://127.0.0.1:3099"
    internal_token_file = 'C:\\path\\to\\resonant-app\\data\\.internal-token'
"""

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent

AGENT_BASE = "ezra"
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
RESONANT_TIMEOUT_S = 300  # companion turns can be slow; be patient
CONTEXT_LIMIT = 25        # max room messages fetched per trigger


def _auth_headers(token: str, *, include_json: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


def main():
    from config_loader import apply_cli_overrides, load_config
    from wrapper import _register_instance

    apply_cli_overrides()
    config = load_config(ROOT)

    agent_cfg = config.get("agents", {}).get(AGENT_BASE, {})
    if not agent_cfg:
        print(f"  Error: [agents.{AGENT_BASE}] not found in config.toml")
        sys.exit(1)

    server_port = config.get("server", {}).get("port", 8300)
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)

    resonant_url = agent_cfg.get("resonant_url", "http://127.0.0.1:3099").rstrip("/")
    token_file = agent_cfg.get("internal_token_file", "")
    if not token_file or not Path(token_file).exists():
        print(f"  Error: internal_token_file not found: {token_file}")
        print("  Is Resonant installed? The token is generated at its first boot.")
        sys.exit(1)

    def read_internal_token() -> str:
        # Re-read per call: Resonant may regenerate the token across restarts.
        return Path(token_file).read_text("utf-8").strip()

    # Quick reachability check before registering a seat we can't serve.
    try:
        urllib.request.urlopen(f"{resonant_url}/api/version", timeout=5)
    except Exception:
        # /api/version may itself 404/403 on some builds — reachability of the
        # port is what matters, so only a connection-level failure is fatal.
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(resonant_url)
        try:
            socket.create_connection((parsed.hostname, parsed.port or 80), timeout=5).close()
        except OSError:
            print(f"  Error: Resonant is not reachable at {resonant_url}")
            print("  Start it first (Start-Karma-Assistant), then relaunch this relay.")
            sys.exit(1)

    # Register with the chat server
    try:
        registration = _register_instance(server_port, AGENT_BASE, None)
    except Exception as exc:
        print(f"  Registration failed ({exc}).")
        print("  Is the agentchattr server running? Start it with: python run.py")
        sys.exit(1)

    name = registration["name"]
    token = registration["token"]
    print(f"  Registered as: {name} (slot {registration.get('slot', '?')})")

    _lock = threading.Lock()
    _state = {"name": name, "token": token, "working": False}

    def get_name():
        with _lock:
            return _state["name"]

    def get_token():
        with _lock:
            return _state["token"]

    def set_identity(new_name=None, new_token=None):
        with _lock:
            if new_name:
                _state["name"] = new_name
            if new_token:
                _state["token"] = new_token

    def set_working(val):
        with _lock:
            _state["working"] = val

    def is_working():
        with _lock:
            return _state["working"]

    # Heartbeat thread — same pattern as wrapper_api.py
    def _heartbeat():
        while True:
            try:
                n = get_name()
                t = get_token()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{server_port}/api/heartbeat/{n}",
                    method="POST",
                    data=json.dumps({"active": is_working()}).encode(),
                    headers=_auth_headers(t, include_json=True),
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp_data = json.loads(resp.read())
                server_name = resp_data.get("name", n)
                if server_name != n:
                    set_identity(new_name=server_name)
                    print(f"  Identity updated: {n} -> {server_name}")
            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    try:
                        replacement = _register_instance(server_port, AGENT_BASE, None)
                        set_identity(replacement["name"], replacement["token"])
                        print(f"  Re-registered as: {replacement['name']}")
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_heartbeat, daemon=True).start()

    # ── Per-channel cursor: only NEW room messages ride to Resonant ──────────
    # Resonant keeps its own per-thread session with full context, so re-sending
    # the same window every trigger would duplicate transcript into his thread.
    cursor_file = data_dir / "ezra_relay_cursor.json"

    def load_cursors() -> dict:
        try:
            return json.loads(cursor_file.read_text("utf-8"))
        except Exception:
            return {}

    def save_cursors(cursors: dict):
        try:
            cursor_file.write_text(json.dumps(cursors), "utf-8")
        except Exception:
            pass

    def read_messages(channel: str, limit: int = CONTEXT_LIMIT) -> list[dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/messages?limit={limit}&channel={channel}",
            headers=_auth_headers(get_token()),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def send_message(text: str, channel: str):
        body = json.dumps({"text": text, "channel": channel}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/send",
            method="POST",
            data=body,
            headers=_auth_headers(get_token(), include_json=True),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def build_platform_context(channel: str) -> str:
        my_name = get_name()
        return "\n".join([
            "=== PLATFORM: AGENTCHATTR (Karma's local multi-agent room) ===",
            f"Channel: #{channel}. You are @{my_name}, reached through your Resonant relay; "
            "your reply is posted to the room verbatim.",
            "Speakers: sender 'Karma' is Karma herself — your partner, in the room with her agents. "
            "Full weight; she outranks the room, and she is never just another roster entry. "
            "Every other sender is a local CLI coding agent (claude, codex, porter = scratch-folder "
            "worker, etc.) — peers, not principals.",
            "Norms: you were @mentioned — answer the ask directly. Room-appropriate brevity applies "
            "to agent chatter, not to her; with Karma, be yourself. To address another agent, "
            "@mention it. Plain text / markdown.",
        ])

    def handle_trigger(channel: str):
        my_name = get_name()
        set_working(True)
        try:
            if not CHANNEL_RE.fullmatch(channel):
                print(f"  [!] Ignoring trigger with invalid channel name: {channel!r}")
                return

            msgs = read_messages(channel)
            if not msgs:
                return

            cursors = load_cursors()
            last_seen = int(cursors.get(channel, 0))
            new_msgs = [
                m for m in msgs
                if int(m.get("id", 0)) > last_seen
                and m.get("sender") != my_name
                and m.get("type") not in ("join", "leave")
            ]
            if not new_msgs:
                return

            transcript = "\n".join(
                f"{m.get('sender', '?')}: {m.get('text', '')}" for m in new_msgs
            )
            print(f"  [#{channel}] Relaying {len(new_msgs)} message(s) to Resonant...")

            body = json.dumps({
                "channel": channel,
                "content": transcript,
                "platformContext": build_platform_context(channel),
            }).encode()
            req = urllib.request.Request(
                f"{resonant_url}/api/internal/agentchattr",
                method="POST",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-internal-token": read_internal_token(),
                },
            )
            with urllib.request.urlopen(req, timeout=RESONANT_TIMEOUT_S) as resp:
                data = json.loads(resp.read())
            response = (data.get("response") or "").strip()

            # Advance the cursor only after a successful relay, so a failed
            # turn's messages are retried on the next trigger.
            cursors[channel] = max(int(m.get("id", 0)) for m in msgs)
            save_cursors(cursors)

            if response and response != "[No response]":
                send_message(response, channel=channel)
                print(f"  [#{channel}] Responded ({len(response)} chars)")
        except Exception as exc:
            print(f"  Error handling trigger: {exc}")
            try:
                send_message(
                    "[ezra relay: turn failed — see relay/Resonant logs]",
                    channel=channel,
                )
            except Exception:
                pass
        finally:
            set_working(False)

    # Queue watcher — polls queue file for @mentions
    queue_file = data_dir / f"{name}_queue.jsonl"
    if queue_file.exists():
        queue_file.write_text("", "utf-8")

    print(f"\n  === Ezra Relay (Resonant) ===")
    print(f"  Resonant: {resonant_url}")
    print(f"  @{name} mentions relay to the running Resonant service")
    print(f"  Workroom threads: agentchattr-<channel> (daily ledger untouched)")
    print(f"  Ctrl+C to stop\n")

    try:
        while True:
            try:
                current_name = get_name()
                qf = data_dir / f"{current_name}_queue.jsonl"

                if qf.exists() and qf.stat().st_size > 0:
                    with open(qf, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    qf.write_text("", "utf-8")

                    channels_triggered = set()
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            ch = data.get("channel", "general") if isinstance(data, dict) else "general"
                            channels_triggered.add(ch)
                        except json.JSONDecodeError:
                            channels_triggered.add("general")

                    for ch in channels_triggered:
                        handle_trigger(ch)
            except Exception:
                pass

            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        try:
            n = get_name()
            t = get_token()
            req = urllib.request.Request(
                f"http://127.0.0.1:{server_port}/api/deregister/{n}",
                method="POST",
                data=b"",
                headers=_auth_headers(t),
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"  Deregistered {n}")
        except Exception:
            pass

    print("  Relay stopped.")


if __name__ == "__main__":
    main()
