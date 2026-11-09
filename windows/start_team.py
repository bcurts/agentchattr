"""Check CLI updates and start missing Codex, Claude, and Agy sessions."""

import argparse
import importlib.util
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tempfile
from contextlib import contextmanager
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config_loader import load_config

AGENTS = ("codex", "claude", "antigravity")
LABELS = {"codex": "Codex", "claude": "Claude", "antigravity": "Agy"}


def read_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "agentchattr-team-launcher"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def version_number(text):
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)", text)
    if not match:
        raise ValueError("unrecognized version response")
    return tuple(map(int, match[1].split(".")))


def check_updates(commands, env):
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    sources = {
        "codex": ("https://api.github.com/repos/openai/codex/releases/latest", "tag_name"),
        "claude": ("https://api.github.com/repos/anthropics/claude-code/releases/latest", "tag_name"),
        # Manifest endpoint used by https://antigravity.google/cli/install.ps1.
        "antigravity": ("https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
                        f"/manifests/windows_{arch}.json", "version"),
    }
    for name, command in commands.items():
        installed = "unknown"
        try:
            result = subprocess.run([command, "--version"], env=env, capture_output=True,
                                    text=True, timeout=10, check=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            current = version_number(result.stdout)
            installed = ".".join(map(str, current))
            url, key = sources[name]
            latest = version_number(read_json(url)[key])
            state = "update available" if latest > current else "up to date or newer"
            available = ".".join(map(str, latest))
            print(f"{LABELS[name]}: installed {installed}; latest {available} ({state}).")
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            print(f"{LABELS[name]}: installed {installed}; update check unavailable ({type(exc).__name__}); continuing.")


def process_inventory():
    # Query processes rather than persisted presence, which can be stale after a crash.
    script = ("$ErrorActionPreference='Stop'; Get-CimInstance Win32_Process | "
              "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=20, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        processes = json.loads(result.stdout)
        if isinstance(processes, dict):
            processes = [processes]
        if not isinstance(processes, list):
            raise ValueError("invalid process inventory")
        return processes
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Cannot inspect running agents; refusing to risk duplicate sessions.") from exc


def running_agents(processes, commands, unrelated=None):
    running = set()
    for process in processes:
        executable = (process.get("Name") or "").lower()
        line = (process.get("CommandLine") or "").replace("\\", "/").lower()
        for name, command in commands.items():
            cli = Path(command).stem.lower()
            # Only executable processes count: terminal shells may retain old command text.
            wrapper = (executable.startswith("python") and
                       re.search(r'(?<![\w-])wrapper(?:_api)?\.py"?\s+"?' + re.escape(name) + r'(?:"?\s|"?$)', line))
            native = executable == cli + ".exe"
            if name == "codex" and ("/app-server-daemon/" in line or
                                    re.search(r'(?:^|\s)app-server(?:\s|$)', line)):
                native = False
            node_cli = (executable == "node.exe" and
                        re.search(r'/(?:' + re.escape(cli) + r')(?:/bin/[^\s"]+|\.(?:js|cjs|mjs))(?:"|\s|$)', line))
            if name == "claude" and executable == "node.exe":
                node_cli = node_cli or re.search(r'/@anthropic-ai/claude-code/cli\.js(?:"|\s|$)', line)
            # Executable names alone also match Desktop, IDE, and unrelated CLI sessions.
            attached = False
            if name == "codex":
                attached = bool(re.search(r'(?<![\w.])mcp_servers\.agentchattr\.url\s*=', line))
            elif name == "claude":
                attached = bool(re.search(
                    r'--mcp-config(?:=|\s+)(?:"[^"\r\n]*agentchattr/[^"\r\n]*\.json"|'
                    r'[^\s"]*agentchattr/[^\s"]*\.json(?:\s|$))', line))
            # Agy uses a settings file without CLI arguments: only its wrapper proves membership.
            if wrapper or ((native or node_cli) and attached):
                running.add(name)
            elif (native or node_cli) and unrelated is not None:
                unrelated.add(name)
    return running


def inspect_agents(commands):
    unrelated = set()
    active = running_agents(process_inventory(), commands, unrelated)
    for name in AGENTS:
        if name in unrelated - active:
            print(f"{LABELS[name]}: another app or CLI session was found outside the team; "
                  "it will not block team startup.")
    return active


@contextmanager
def startup_lock():
    import msvcrt
    # Per-user, across checkouts. Windows releases the byte lock even after a crash.
    path = Path(tempfile.gettempdir()) / "agentchattr-team-start.lock"
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError("Another team launcher is starting. Try again when it finishes.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def server_ready(url):
    try:
        # Identify the agentchattr page by its token marker; never print the page or token.
        with urllib.request.urlopen(url, timeout=1) as response:
            return b"window.__SESSION_TOKEN__=" in response.read()
    except (OSError, urllib.error.URLError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check setup without starting anything")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Use this launcher on Windows.")

    # Explorer can inherit PATH from before agy was installed.
    env = os.environ.copy()
    extra_bins = [Path(env.get("LOCALAPPDATA", "")) / "agy" / "bin",
                  Path.home() / ".local" / "bin"]
    env["PATH"] = os.pathsep.join(map(str, extra_bins)) + os.pathsep + env.get("PATH", "")
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"

    config = load_config(ROOT)
    commands = {}
    for name in AGENTS:
        agent = config.get("agents", {}).get(name)
        if not agent:
            raise RuntimeError(f"Agent {name!r} is missing from config.toml.")
        command = agent.get("command", name)
        commands[name] = shutil.which(command, path=env.get("PATH"))
        if not commands[name]:
            raise RuntimeError(f"{name}: {command!r} is not installed/on PATH. Install and sign in first.")
    for module in ("fastapi", "uvicorn", "mcp"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"Missing dependency {module}. Run start_team.bat to install dependencies.")

    host = config.get("server", {}).get("host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost"):
        raise RuntimeError("This launcher requires server.host = 127.0.0.1 or localhost.")
    port = int(config.get("server", {}).get("port", 8300))
    url = f"http://127.0.0.1:{port}"
    check_updates(commands, env)
    if args.check:
        active = inspect_agents(commands)
        for name in AGENTS:
            print(f"{LABELS[name]}: {'already running' if name in active else 'ready to start'}.")
        print(f"Setup OK: {url}; Codex, Claude, Agy. No sessions started.")
        return

    with startup_lock():
        start_team(url, env, commands)


def start_team(url, env, commands):
    # Fail closed before starting the server if process inspection is unavailable.
    inspect_agents(commands)

    if not server_ready(url):
        log_path = ROOT / "team-server.log"
        with log_path.open("ab") as log:
            server = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "run.py")], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        deadline = time.monotonic() + 30
        while not server_ready(url):
            if server.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f"Server did not become ready. See {log_path}")
            time.sleep(0.5)
    else:
        print(f"Reusing the server at {url}")

    for name in AGENTS:
        if name in running_agents(process_inventory(), commands):
            print(f"{LABELS[name]}: already running; skipped.")
            continue
        # cmd /k keeps errors visible; each interactive CLI needs its own console.
        command = subprocess.list2cmdline([sys.executable, str(ROOT / "wrapper.py"), name])
        shell = subprocess.list2cmdline([env.get("COMSPEC", "cmd.exe")])
        console = subprocess.Popen(
            f'{shell} /d /s /k "{command}"',
            cwd=ROOT, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        deadline = time.monotonic() + 15
        while name not in running_agents(process_inventory(), commands):
            if console.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f"Cannot confirm {LABELS[name]} startup. Check its terminal before retrying.")
            time.sleep(1)
        print(f"{LABELS[name]}: started; its wrapper registers it with agentchattr.")
    webbrowser.open(url)
    print("Use @mentions in chat to invite the agents into a conversation.")
    print("Run this launcher again to start only missing agents.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
