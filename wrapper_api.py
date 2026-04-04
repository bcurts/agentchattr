"""API agent wrapper — bridges the chat room to an OpenAI-compatible endpoint.

Usage:
    python wrapper_api.py qwen
    python wrapper_api.py my-local-model

For local models (Ollama, llama-server, LM Studio, etc.) that expose an
OpenAI-compatible /v1/chat/completions endpoint but have no CLI to inject
keystrokes into.

How it works:
  1. Loads config (config.toml + config.local.toml).
  2. Registers with the chat server via POST /api/register.
  3. Starts a heartbeat thread (same pattern as wrapper.py).
  4. Polls the queue file for @mentions.
  5. On trigger: reads recent chat context, formats into OpenAI messages,
     POSTs to the model's /v1/chat/completions, sends reply via POST /api/send.
  6. On exit: deregisters cleanly.
"""

import argparse
import ipaddress
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent


def _is_private_or_local_base_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        pass
    return host.endswith(".local")


def _auth_headers(token: str, *, include_json: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


def main():
    from config_loader import load_config
    from wrapper import _register_instance

    config = load_config(ROOT)
    agent_names = list(config.get("agents", {}).keys())
    api_agents = [n for n in agent_names if config["agents"][n].get("type") == "api"]

    if not api_agents:
        print("  No API agents found in config.\n")
        print("  To add one, copy the example config:")
        print("    cp config.local.toml.example config.local.toml")
        print("  Then uncomment and edit an [agents.NAME] section (set type = \"api\").")
        print("  Finally: python wrapper_api.py <name>")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="API agent wrapper for OpenAI-compatible endpoints")
    parser.add_argument("agent", choices=api_agents,
                        help=f"API agent to run ({', '.join(api_agents)})")
    parser.add_argument("--label", type=str, default=None, help="Custom display label")
    args = parser.parse_args()

    agent = args.agent
    agent_cfg = config["agents"][agent]
    server_port = config.get("server", {}).get("port", 8300)
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)

    # Model API config
    base_url = agent_cfg.get("base_url", "").rstrip("/")
    if not base_url:
        print(f"  Error: [agents.{agent}] must have base_url (e.g. http://localhost:8189/v1)")
        sys.exit(1)
    if not _is_private_or_local_base_url(base_url):
        print("  WARNING: this API agent points at a non-local endpoint.")
        print("  Recent chat context sent to this wrapper will be transmitted to that remote service.")
        print(f"  Endpoint: {base_url}")
        print()
    model = agent_cfg.get("model", "")
    api_key_env = agent_cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""
    temperature = agent_cfg.get("temperature")
    if temperature is not None:
        temperature = float(temperature)
        # Clamp: some providers (e.g. MiniMax) require temperature in (0.0, 1.0]
        if temperature <= 0:
            temperature = 0.01
        if temperature > 2.0:
            temperature = 2.0
    context_messages = int(agent_cfg.get("context_messages", 20))
    system_prompt = agent_cfg.get("system_prompt",
        f"You are {agent_cfg.get('label', agent)}, a helpful AI assistant participating "
        "in a developer chat room. Keep responses concise and relevant. "
        "You can see recent messages for context.")

    # Register with server
    try:
        registration = _register_instance(server_port, agent, args.label)
    except Exception as exc:
        print(f"  Registration failed ({exc}).")
        print("  Is the server running? Start it with: python run.py")
        sys.exit(1)

    name = registration["name"]
    token = registration["token"]
    print(f"  Registered as: {name} (slot {registration.get('slot', '?')})")

    # Thread-safe identity state (can change via heartbeat rename)
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

    # Heartbeat thread — same pattern as wrapper.py
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
                        replacement = _register_instance(server_port, agent, args.label)
                        set_identity(replacement["name"], replacement["token"])
                        print(f"  Re-registered as: {replacement['name']}")
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_heartbeat, daemon=True).start()

    # Get this agent's role from server status
    def get_my_role():
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server_port}/api/status",
                headers=_auth_headers(get_token()),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = json.loads(resp.read())
            my_name = get_name()
            info = status.get(my_name, {})
            return info.get("role", "") if isinstance(info, dict) else ""
        except Exception:
            return ""

    # Get online agents from server
    def get_online_agents():
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server_port}/api/status",
                headers=_auth_headers(get_token()),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = json.loads(resp.read())
            online = [n for n, info in status.items()
                      if isinstance(info, dict) and info.get("available")]
            return online
        except Exception:
            return []

    # Read recent messages from chat server
    def read_messages(channel="general", since_id=0, limit=20):
        params = f"limit={limit}&channel={channel}"
        if since_id:
            params = f"since_id={since_id}&{params}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/messages?{params}",
            headers=_auth_headers(get_token()),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    # Send message back to chat
    def send_message(text, channel="general"):
        body = json.dumps({"text": text, "channel": channel}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/send",
            method="POST",
            data=body,
            headers=_auth_headers(get_token(), include_json=True),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    # Call OpenAI-compatible chat completions API
    def call_model(messages):
        url = f"{base_url}/chat/completions"
        payload = {"messages": messages}
        if model:
            payload["model"] = model
        if temperature is not None:
            payload["temperature"] = temperature
        body = json.dumps(payload).encode()

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, method="POST", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]

    # Format chat messages into OpenAI format
    def format_messages(chat_msgs):
        my_name = get_name()

        # Build dynamic system prompt with online status, role, and mention instructions
        online = get_online_agents()
        others = [n for n in online if n != my_name]
        parts = [system_prompt]
        # Inject role if set
        my_role = get_my_role()
        if my_role:
            parts.append(f"role: {my_role}")
        if others:
            parts.append(f"Currently online: {', '.join(others)}.")
            parts.append("To mention another agent and trigger them to respond, "
                         "use @name (e.g. " + ", ".join(f"@{n}" for n in others[:3]) + ").")
        parts.append(f"Your name in this chat is {my_name}. Do not prefix your "
                     "messages with your own name.")

        messages = [{"role": "system", "content": " ".join(parts)}]
        for msg in chat_msgs:
            sender = msg.get("sender", "")
            text = msg.get("text", "")
            if sender == "system":
                continue
            role = "assistant" if sender == my_name else "user"
            messages.append({"role": role, "content": f"{sender}: {text}"})
        return messages

    # Handle a trigger — read context, call model, respond
    def handle_trigger(channel="general"):
        my_name = get_name()
        set_working(True)
        try:
            chat_msgs = read_messages(channel=channel, limit=context_messages)
            if not chat_msgs:
                return

            messages = format_messages(chat_msgs)
            print(f"  [{channel}] Calling model with {len(messages)} messages...")

            response = call_model(messages)
            response = response.strip()
            if not response:
                return

            # Strip self-prefix if the model echoes its own name
            prefixes = [f"{my_name}: ", f"{my_name}:"]
            for prefix in prefixes:
                if response.startswith(prefix):
                    response = response[len(prefix):]
                    break

            send_message(response, channel=channel)
            print(f"  [{channel}] Responded ({len(response)} chars)")
        except Exception as exc:
            print(f"  Error handling trigger: {exc}")
        finally:
            set_working(False)

    # Queue watcher — polls queue file for @mentions
    queue_file = data_dir / f"{name}_queue.jsonl"
    if queue_file.exists():
        queue_file.write_text("", "utf-8")

    print(f"\n  === {agent_cfg.get('label', agent)} API Wrapper ===")
    print(f"  Model endpoint: {base_url}/chat/completions")
    if model:
        print(f"  Model: {model}")
    print(f"  @{name} mentions trigger model calls")
    print(f"  Ctrl+C to stop\n")

    try:
        while True:
            try:
                # Update queue file path in case of rename
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
                        handle_trigger(channel=ch)
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

    print("  Wrapper stopped.")


if __name__ == "__main__":
    main()
