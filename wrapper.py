"""Agent wrapper - runs the real interactive CLI with auto-trigger on @mentions.

Usage:
    python wrapper.py claude
    python wrapper.py codex
    python wrapper.py gemini
    python wrapper.py kimi
    python wrapper.py qwen

Cross-platform:
  - Windows: injects keystrokes via Win32 WriteConsoleInput (wrapper_windows.py)
  - Mac/Linux: injects keystrokes via tmux send-keys (wrapper_unix.py)

How it works:
  1. Starts the agent CLI in an interactive terminal.
  2. Watches the queue file in the background for @mentions from the chat room.
  3. When triggered, injects "use mcp to read #channel - you're mentioned, take appropriate action and respond".
  4. The agent picks up the prompt as if the user typed it.
"""

import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).parent

SERVER_NAME = "agentchattr"


# ---------------------------------------------------------------------------
# Per-instance provider config
# ---------------------------------------------------------------------------

def _write_json_mcp_settings(config_file: Path, url: str, transport: str = "http",
                              *, token: str = "", http_key: str = "httpUrl",
                              bearer_token_env: str = "") -> Path:
    """Write/merge a settings-style JSON file with nested mcpServers config.

    Preserves existing servers in the file — only updates the agentchattr entry.

    Gemini CLI 0.32+ expects:
      - "httpUrl" key (not "url") for streamable-http transport
      - "url" key for SSE transport
      - "trust": true to skip per-call approval prompts

    `http_key` controls which JSON key names the HTTP transport URL. Defaults
    to "httpUrl" (Gemini/Qwen). Providers like CodeBuddy that follow the
    standard MCP shape should set `mcp_http_key = "url"` in their config.
    When `bearer_token_env` is set, the settings file stores only the
    environment variable name and the provider process receives the token
    through its environment. This is used by Kimi so concurrent instances do
    not overwrite each other's token in a shared project mcp.json.
    Only affects settings_file / env injector modes (not the Claude flag
    writer or Kilo env_content writer).
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if config_file.exists():
        try:
            existing = json.loads(config_file.read_text("utf-8"))
        except Exception:
            pass
    servers = existing.get("mcpServers", {})
    # Default: Gemini-style "httpUrl" for HTTP. Override with http_key="url"
    # for providers that follow the standard MCP shape (e.g. CodeBuddy).
    if transport in ("http", "streamable-http"):
        entry: dict = {"type": "http", http_key: url, "trust": True}
    else:
        entry = {"type": transport, "url": url, "trust": True}
    if bearer_token_env:
        entry["bearerTokenEnvVar"] = bearer_token_env
    elif token:
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    servers[SERVER_NAME] = entry
    existing["mcpServers"] = servers

    # Enable folder trust so ~/.gemini/trustedFolders.json is respected
    security = existing.get("security", {})
    folder_trust = security.get("folderTrust", {})
    folder_trust["enabled"] = True
    security["folderTrust"] = folder_trust
    existing["security"] = security

    config_file.write_text(json.dumps(existing, indent=2) + "\n", "utf-8")
    return config_file


def _read_project_mcp_servers(project_dir: Path) -> dict:
    """Read existing MCP servers from the project's .mcp.json."""
    mcp_file = project_dir / ".mcp.json"
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text("utf-8"))
            servers = data.get("mcpServers", {})
            # Remove agentchattr — we'll add our own authenticated version
            servers.pop(SERVER_NAME, None)
            return servers
        except Exception:
            pass
    return {}


def _write_claude_mcp_config(
    config_file: Path,
    url: str,
    *,
    token: str = "",
    project_servers: dict | None = None,
) -> Path:
    """Write a Claude Code --mcp-config file with bearer auth.

    Includes all project MCP servers (unity-mcp etc.) so --strict-mcp-config
    can be used without losing other servers."""
    config_file.parent.mkdir(parents=True, exist_ok=True)

    # Start with other project servers (e.g. unity-mcp)
    servers = dict(project_servers or {})

    # Add agentchattr with bearer token for direct server auth
    entry: dict = {"type": "http", "url": url}
    if token:
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    servers[SERVER_NAME] = entry

    payload = {"mcpServers": servers}
    config_file.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    return config_file


# ---------------------------------------------------------------------------
# Built-in provider defaults (applied when agent config has no mcp_inject)
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: dict[str, dict] = {
    "claude": {
        "mcp_inject": "flag",
        "mcp_flag": "--mcp-config",
        "mcp_transport": "http",
        "mcp_merge_project": True,  # include unity-mcp etc.
    },
    "gemini": {
        "mcp_inject": "env",
        "mcp_env_var": "GEMINI_CLI_SYSTEM_SETTINGS_PATH",
        "mcp_transport": "http",  # streamable-http; SSE has blocking issues in Gemini 0.32.x
        "mcp_merge_project": True,
    },
    "codex": {
        "mcp_inject": "proxy_flag",
        "mcp_proxy_flag_template": '-c mcp_servers.{server}.url="{url}"',
        # mcp_merge_project disabled — Codex reads .mcp.json natively,
        # and duplicate detection is name-based only (e.g. unityMCP vs unity-mcp)
    },
    "kimi": {
        "mcp_inject": "settings_file",
        "mcp_settings_path": ".kimi-code/mcp.json",
        "mcp_http_key": "url",
        "mcp_bearer_token_env": "AGENTCHATTR_MCP_TOKEN",
        "mcp_transport": "http",
        "mcp_merge_project": True,
    },
    "kilo": {
        "mcp_inject": "env_content",
        "mcp_env_var": "KILO_CONFIG_CONTENT",
        "mcp_transport": "http",
    },
    "opencode": {
        "mcp_inject": "env_content",
        "mcp_env_var": "OPENCODE_CONFIG_CONTENT",
        "mcp_oauth": False,
        "mcp_merge_env_content": True,
        "mcp_transport": "http",
        # Auto (yolo) mode: replace the child's `permission` subtree with this
        # overlay so everything inside the workdir is auto-approved while
        # external-directory access is denied. Applied ONLY when launched with
        # --auto (see _build_provider_launch). Normal mode leaves permissions
        # untouched.
        "mcp_auto_flag": "--auto",
        "mcp_auto_permission": {"*": "allow", "external_directory": "deny"},
    },
}

_VALID_INJECT_MODES = {"settings_file", "env", "flag", "proxy_flag", "env_content"}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Merge overlay into base recursively; overlay wins on conflicts."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_passthrough_args(args: list[str]) -> list[str]:
    """Remove the wrapper's ``--`` separator before launching the provider.

    ``argparse.parse_known_args`` sometimes leaves the separator in ``extra``
    when wrapper-owned options precede it.  Forwarding that separator would
    make provider flags after it positional input instead of CLI options.
    """
    normalized = list(args)
    if normalized[:1] == ["--"]:
        return normalized[1:]
    return normalized


def _resolve_mcp_inject(agent: str, agent_cfg: dict) -> dict:
    """Resolve MCP injection config: explicit agent_cfg > built-in defaults > None."""
    inject_mode = agent_cfg.get("mcp_inject")
    if inject_mode:
        return dict(agent_cfg)
    if agent in _BUILTIN_DEFAULTS:
        merged = dict(_BUILTIN_DEFAULTS[agent])
        merged.update({k: v for k, v in agent_cfg.items() if k.startswith("mcp_")})
        return merged
    return {}


def _get_server_url(mcp_cfg: dict, transport: str) -> str:
    """Build the MCP server URL for the given transport."""
    if transport == "sse":
        port = mcp_cfg.get("sse_port", 8201)
        return f"http://127.0.0.1:{port}/sse"
    port = mcp_cfg.get("http_port", 8200)
    return f"http://127.0.0.1:{port}/mcp"


def _apply_mcp_inject(
    inject_cfg: dict,
    instance_name: str,
    data_dir: Path,
    proxy_url: str | None,
    *,
    token: str = "",
    mcp_cfg: dict | None = None,
    project_dir: Path | None = None,
) -> tuple[list[str], dict[str, str], Path | None]:
    """Apply MCP config injection based on the resolved inject config.

    Returns (extra_launch_args, inject_env, settings_path_or_None).
    settings_path is stored so re-registration can rewrite it.
    """
    mode = inject_cfg.get("mcp_inject")
    if not mode:
        return [], {}, None

    launch_args: list[str] = []
    inject_env: dict[str, str] = {}
    settings_path: Path | None = None
    config_dir = data_dir / "provider-config"
    transport = inject_cfg.get("mcp_transport", "http")
    server_url = _get_server_url(mcp_cfg or {}, transport)

    http_key = inject_cfg.get("mcp_http_key", "httpUrl")

    if mode == "settings_file":
        # Write a settings JSON file at a user-specified path (e.g. .qwen/settings.json,
        # or ~/.codebuddy/.mcp.json for user-scope configs).
        raw_path = inject_cfg.get("mcp_settings_path", "")
        if not raw_path:
            raise ValueError(f"mcp_inject = 'settings_file' requires mcp_settings_path")
        # Expand ~ to user home (e.g. ~/.codebuddy/.mcp.json), then resolve
        # relative paths against project_dir/CWD as before.
        target = Path(raw_path).expanduser()
        if not target.is_absolute():
            base = Path(project_dir) if project_dir else Path.cwd()
            target = base / target
        bearer_token_env = inject_cfg.get("mcp_bearer_token_env", "")
        settings_path = _write_json_mcp_settings(
            target, server_url,
            transport=transport, token=token, http_key=http_key,
            bearer_token_env=bearer_token_env,
        )
        if bearer_token_env:
            inject_env[bearer_token_env] = token
        # Optionally set an env var pointing to the settings file
        env_var = inject_cfg.get("mcp_env_var")
        if env_var:
            inject_env[env_var] = str(settings_path)

    elif mode == "env":
        # Write a settings file in provider-config dir, expose via env var
        env_var = inject_cfg.get("mcp_env_var")
        if not env_var:
            raise ValueError(f"mcp_inject = 'env' requires mcp_env_var")
        settings_path = _write_json_mcp_settings(
            config_dir / f"{instance_name}-settings.json",
            server_url, transport=transport, token=token, http_key=http_key,
        )
        # Merge project .mcp.json servers into the settings file
        merge_project = inject_cfg.get("mcp_merge_project", False)
        if merge_project and project_dir and settings_path:
            project_servers = _read_project_mcp_servers(project_dir)
            if project_servers:
                try:
                    data = json.loads(settings_path.read_text("utf-8"))
                    servers = data.get("mcpServers", {})
                    for name, cfg in project_servers.items():
                        if name not in servers:
                            # Normalize url key for providers that expect "httpUrl"
                            # (Gemini/Qwen). For standard-MCP providers with
                            # http_key="url", leave existing "url" entries as-is.
                            entry = dict(cfg)
                            srv_type = entry.get("type", "http")
                            if srv_type in ("http", "streamable-http") and http_key != "url":
                                if "url" in entry and http_key not in entry:
                                    entry[http_key] = entry.pop("url")
                            entry.setdefault("trust", True)
                            servers[name] = entry
                    data["mcpServers"] = servers
                    settings_path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
                except Exception:
                    pass
        inject_env[env_var] = str(settings_path)

    elif mode == "flag":
        # Write a config file, pass it as a CLI flag
        flag = inject_cfg.get("mcp_flag", "--mcp-config")
        merge_project = inject_cfg.get("mcp_merge_project", False)
        project_servers = _read_project_mcp_servers(project_dir) if (merge_project and project_dir) else {}
        settings_path = _write_claude_mcp_config(
            config_dir / f"{instance_name}-mcp.json",
            server_url, token=token, project_servers=project_servers,
        )
        launch_args = [flag, str(settings_path)]

    elif mode == "env_content":
        # Build JSON config content and set it as an env var directly (no file written).
        # Used by Kilo CLI which reads KILO_CONFIG_CONTENT at startup.
        env_var = inject_cfg.get("mcp_env_var")
        if not env_var:
            raise ValueError("mcp_inject = 'env_content' requires mcp_env_var")
        entry: dict = {"type": "remote", "url": server_url, "enabled": True}
        if "mcp_oauth" in inject_cfg:
            entry["oauth"] = inject_cfg["mcp_oauth"]
        if token:
            entry["headers"] = {"Authorization": f"Bearer {token}"}
        payload = {"mcp": {SERVER_NAME: entry}}
        inject_env[env_var] = json.dumps(payload)

    elif mode == "proxy_flag":
        # Pass the proxy URL as CLI flags (e.g. codex -c ...)
        template = inject_cfg.get("mcp_proxy_flag_template",
                                  '-c mcp_servers.{server}.url="{url}"')
        expanded = template.format(server=SERVER_NAME, url=proxy_url or "")
        launch_args = expanded.split()

    return launch_args, inject_env, settings_path


def _ensure_gemini_folder_trusted(project_dir: Path) -> None:
    """Add project_dir as TRUST_FOLDER in ~/.gemini/trustedFolders.json.

    Gemini CLI blocks ALL MCPs (including system-settings ones) for untrusted
    folders. A more-specific TRUST_FOLDER entry overrides any parent-level
    DO_NOT_TRUST rule, so we always write the exact cwd we're launching in.
    Respects GEMINI_CLI_TRUSTED_FOLDERS_PATH env override if set.
    """
    trusted_path_env = os.environ.get("GEMINI_CLI_TRUSTED_FOLDERS_PATH", "")
    if trusted_path_env:
        trusted_file = Path(trusted_path_env)
    else:
        trusted_file = Path.home() / ".gemini" / "trustedFolders.json"

    try:
        data: dict = {}
        if trusted_file.exists():
            try:
                data = json.loads(trusted_file.read_text("utf-8"))
            except Exception:
                data = {}

        folder_key = str(project_dir)
        if data.get(folder_key) == "TRUST_FOLDER":
            return  # already trusted — nothing to do

        data[folder_key] = "TRUST_FOLDER"
        trusted_file.parent.mkdir(parents=True, exist_ok=True)
        trusted_file.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
        print(f"  Trusted folder for Gemini MCPs: {folder_key}")
    except Exception as exc:
        print(f"  Warning: could not update Gemini trusted folders: {exc}")


def _build_provider_launch(
    agent: str,
    agent_cfg: dict,
    instance_name: str,
    data_dir: Path,
    proxy_url: str | None,
    extra_args: list[str],
    env: dict[str, str],
    *,
    token: str = "",
    mcp_cfg: dict | None = None,
    project_dir: Path | None = None,
) -> tuple[list[str], dict[str, str], dict[str, str], Path | None]:
    """Return provider-specific launch args/env/inject_env/settings_path.

    inject_env: env vars that must propagate INTO the agent process.  On
    Mac/Linux these are prefixed onto the tmux command via ``env VAR=val``
    because subprocess.run(env=...) only affects the tmux client binary.
    On Windows they are simply merged into the Popen env dict.
    """
    inject_cfg = _resolve_mcp_inject(agent, agent_cfg)
    mcp_args, inject_env, settings_path = _apply_mcp_inject(
        inject_cfg, instance_name, data_dir, proxy_url,
        token=token, mcp_cfg=mcp_cfg, project_dir=project_dir,
    )

    # env_content providers read inline JSON config from an env var.
    # Providers that opt in via mcp_merge_env_content (e.g. OpenCode)
    # deep-merge our agentchattr entry into the user's existing JSON
    # instead of overwriting their model/plugin/other settings.
    # Providers without the flag (e.g. Kilo) keep the original
    # overwrite semantics.
    env_var = inject_cfg.get("mcp_env_var", "")
    if (inject_cfg.get("mcp_inject") == "env_content"
            and inject_cfg.get("mcp_merge_env_content")
            and env_var and env_var in inject_env and env.get(env_var)):
        try:
            existing = json.loads(env[env_var])
            overlay = json.loads(inject_env[env_var])
            if isinstance(existing, dict) and isinstance(overlay, dict):
                inject_env[env_var] = json.dumps(_deep_merge(existing, overlay))
        except Exception:
            pass  # unparseable existing value — keep our injected config

    # OpenCode Auto (yolo) mode: when the auto flag is present in the launch
    # args, REPLACE the child's `permission` subtree with the configured
    # overlay (instead of deep-merging — user deny rules must not survive).
    # Everything else in the config content (provider/model/plugin/MCP etc.)
    # is preserved. Normal mode never applies this overlay.
    auto_flag = inject_cfg.get("mcp_auto_flag", "")
    auto_permission = inject_cfg.get("mcp_auto_permission")
    if (auto_flag and auto_permission and auto_flag in extra_args
            and inject_cfg.get("mcp_inject") == "env_content"
            and env_var and env_var in inject_env):
        try:
            data = json.loads(inject_env[env_var])
            if isinstance(data, dict):
                data["permission"] = dict(auto_permission)
                inject_env[env_var] = json.dumps(data)
        except Exception:
            pass  # malformed config content — leave as-is

    launch_args = [*mcp_args, *extra_args]
    launch_env = dict(env)

    return launch_args, launch_env, inject_env, settings_path


def _register_instance(
    server_port: int,
    base: str,
    label: str | None = None,
    preferred_name: str | None = None,
    lease_id: str | None = None,
    pid: int | None = None,
    start_marker: str | None = None,
    resume_token: str | None = None,
) -> dict:
    import urllib.request

    reg_body = json.dumps(
        {"base": base, "label": label, "preferred_name": preferred_name,
         "lease_id": lease_id or "", "pid": pid or 0,
         "start_marker": start_marker or "", "resume_token": resume_token or ""}
    ).encode()
    reg_req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/api/register",
        method="POST",
        data=reg_body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(reg_req, timeout=5) as reg_resp:
        return json.loads(reg_resp.read())


def _auth_headers(token: str, *, include_json: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


# ---------------------------------------------------------------------------
# Heartbeat sender (shared by the periodic heartbeat and the activity monitor)
# ---------------------------------------------------------------------------

class HeartbeatSender:
    """Single HTTP heartbeat path for both wrapper heartbeat threads.

    Carries the process lease (lease_id + wrapper PID + start marker) on every
    beat so the server can verify liveness before ever deregistering, and can
    resume the original name/token after a server restart.

    Rate-limited logging: first failure logs immediately, repeats are
    summarized at most every LOG_INTERVAL seconds, and recovery is logged once.
    Tokens are NEVER logged. Thread-safe: the failure counter and rate-limit
    state are shared by the heartbeat and activity threads under a lock.
    """

    LOG_INTERVAL = 30  # seconds between consecutive-failure summaries

    def __init__(self, server_port: int, get_identity, get_token,
                 lease_id: str, pid: int, start_marker: str = "",
                 log_fn=None, now_fn=time.time):
        self.server_port = server_port
        self._get_identity = get_identity
        self._get_token = get_token
        self.lease_id = lease_id
        self.pid = pid
        self.start_marker = start_marker
        self._log = log_fn or (lambda msg: print(f"  [heartbeat] {msg}", flush=True))
        self._now = now_fn
        self._state_lock = threading.Lock()
        self.consecutive_failures = 0
        self._last_failure_log = 0.0
        # Terminal flag: set when the server rejects our lease proof
        # (invalid_lease_proof). Retrying can never succeed — all heartbeat
        # sends short-circuit locally and the heartbeat thread exits.
        self.terminal = False

    def send(self, active: bool | None = None) -> dict:
        """One heartbeat attempt.

        Returns {"ok": True, "name": <canonical name>} on success, or
        {"ok": False, "status": <http code or None>, "error": <detail>}.
        Never raises.
        """
        if self.terminal:
            return {"ok": False, "status": None,
                    "error": "terminal: lease proof rejected", "terminal": True}
        name, _ = self._get_identity()
        token = self._get_token()
        body: dict = {"lease_id": self.lease_id, "pid": self.pid,
                      "start_marker": self.start_marker}
        if active is not None:
            body["active"] = bool(active)
        url = f"http://127.0.0.1:{self.server_port}/api/heartbeat/{name}"
        try:
            req = urllib.request.Request(
                url,
                method="POST",
                data=json.dumps(body).encode(),
                headers=_auth_headers(token, include_json=True),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp_data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self._record_failure(f"HTTP {exc.code}")
            return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self._record_failure(detail)
            return {"ok": False, "status": None, "error": detail}

        self._record_success()
        return {"ok": True, "name": resp_data.get("name", name)}

    def _record_success(self):
        with self._state_lock:
            if self.consecutive_failures:
                self._log(f"recovered after {self.consecutive_failures} consecutive "
                          f"failure(s) — server reachable again")
            self.consecutive_failures = 0

    def _record_failure(self, detail: str):
        with self._state_lock:
            self.consecutive_failures += 1
            now = self._now()
            if self.consecutive_failures == 1:
                self._log(f"heartbeat failed ({detail})")
                self._last_failure_log = now
            elif now - self._last_failure_log >= self.LOG_INTERVAL:
                self._log(f"heartbeat still failing ({detail}) — "
                          f"{self.consecutive_failures} consecutive failures")
                self._last_failure_log = now


def _activity_monitor_loop(get_checker, sender: HeartbeatSender, *,
                           should_run=lambda: True, sleep_fn=time.sleep,
                           log_fn=None):
    """Activity monitor body: report busy/idle state via the shared sender.

    Exception-isolated: a failing activity checker is logged (rate-limited)
    and the loop CONTINUES — one bad console read must never kill the monitor.
    `should_run`/`sleep_fn` exist for tests; production uses the defaults.
    """
    log = log_fn or (lambda msg: print(f"  [activity] {msg}", flush=True))
    last_active = None
    last_report_time = 0.0
    checker_failures = 0
    last_checker_log = 0.0
    REPORT_INTERVAL = 3  # re-send state every 3s while active (keeps server lease fresh)
    IDLE_REPORT_INTERVAL = 8  # keep-alive while idle
    while should_run():
        sleep_fn(1)
        checker = get_checker()
        if not checker:
            continue
        try:
            active = checker()
        except Exception as exc:
            checker_failures += 1
            now = time.time()
            if checker_failures == 1 or now - last_checker_log >= HeartbeatSender.LOG_INTERVAL:
                suffix = (f" — {checker_failures} consecutive failures"
                          if checker_failures > 1 else "")
                log(f"activity checker failed ({type(exc).__name__}: {exc}){suffix}")
                last_checker_log = now
            continue
        if checker_failures:
            log(f"activity checker recovered after {checker_failures} failure(s)")
            checker_failures = 0
        now = time.time()
        # Send on state change, periodically while active (refresh lease),
        # or periodically while idle (keep presence alive)
        should_send = (
            active != last_active
            or (active and now - last_report_time >= REPORT_INTERVAL)
            or (not active and now - last_report_time >= IDLE_REPORT_INTERVAL)
        )
        if should_send:
            result = sender.send(active=active)
            if result["ok"]:
                last_active = active
            # Count the attempt even on failure so a dead server is not
            # hammered every second; HeartbeatSender already logged it.
            last_report_time = now


def _heartbeat_loop(sender: HeartbeatSender, shutdown: threading.Event, *,
                    get_identity, set_identity, recover,
                    interval: float = 5.0):
    """Periodic heartbeat body (module-level for testability).

    Shutdown-aware: the inter-beat sleep is `shutdown.wait(interval)` so the
    thread exits promptly; no send or 409 recovery is INITIATED once shutdown
    is signaled; and a response/409 that arrives after shutdown began is not
    acted on. Together with the finally-block join this guarantees the
    deregister is the wrapper's LAST registry HTTP mutation.
    """
    while not shutdown.is_set():
        if sender.terminal:
            # Lease proof rejected — terminal for this process. Stop
            # hammering; the CLI session keeps running until restarted.
            return
        result = sender.send()
        if shutdown.is_set():
            # Shutdown began while the beat was in flight — do not act on
            # the response (identity update or 409 recovery).
            return
        if result["ok"]:
            server_name = result.get("name")
            current_name, _ = get_identity()
            if server_name and server_name != current_name:
                set_identity(server_name)
        elif result.get("status") == 409:
            recover()
        shutdown.wait(interval)


def _shutdown_and_deregister(shutdown: threading.Event,
                             sender: HeartbeatSender,
                             threads: list,
                             deregister_fn,
                             *,
                             join_timeout: float = 7.0,
                             log_fn=None) -> bool:
    """Quiesce heartbeat/activity threads, then deregister LAST.

    Signals shutdown FIRST (no new sends/recoveries may start — sender is
    marked terminal so send() short-circuits locally), then joins the given
    threads. An in-flight heartbeat/register can take up to the 5s HTTP
    timeout, so `join_timeout` defaults above that. If any thread fails to
    exit, quiescence cannot be confirmed and the deregister is SKIPPED —
    racing it could resurrect a ghost identity; the server's crash timeout
    reaps the identity once the process is gone. Returns True iff the
    deregister was sent.
    """
    log = log_fn or (lambda msg: print(f"  [shutdown] {msg}", flush=True))
    shutdown.set()
    sender.terminal = True  # further send() calls short-circuit locally
    for t in threads:
        t.join(timeout=join_timeout)
    stuck = [t for t in threads if t.is_alive()]
    if stuck:
        log("heartbeat/activity threads did not quiesce in time — skipping "
            "deregister to avoid a post-shutdown ghost registration; the "
            "server will reap this identity via the crash timeout.")
        return False
    deregister_fn()
    return True


# ---------------------------------------------------------------------------
# Queue watcher
# ---------------------------------------------------------------------------

def _notify_recovery(data_dir: Path, agent_name: str):
    """Write a flag file that the server picks up and broadcasts as a system message."""
    try:
        flag = data_dir / f"{agent_name}_recovered"
        flag.write_text(agent_name, "utf-8")
    except Exception:
        pass


_IDENTITY_HINT = (
    " (If this is a multi-instance session, reclaim your previous identity from "
    "your context window, NOT from the chat history before responding. If you "
    "didn't have one, tell the user to give you a name by clicking your status "
    "pill at the top.)"
)


def _resolve_agent_workdir(
    root: Path,
    configured_cwd: str | os.PathLike[str] | None,
    override: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve the CLI working directory, preferring an explicit override."""
    raw = override if override not in (None, "") else configured_cwd
    work_dir = Path(raw or ".").expanduser()
    if not work_dir.is_absolute():
        work_dir = root / work_dir
    return work_dir.resolve()


def _fetch_role(server_port: int, agent_name: str) -> str:
    """Fetch this agent's role from the server status endpoint."""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{server_port}/api/roles")
        with urllib.request.urlopen(req, timeout=3) as resp:
            roles = json.loads(resp.read())
        return roles.get(agent_name, "")
    except Exception:
        return ""


def _fetch_active_rules(server_port: int, token: str = "") -> dict | None:
    """Fetch active rules from the server."""
    try:
        import urllib.request
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(f"http://127.0.0.1:{server_port}/api/rules/active", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _report_rule_sync(server_port: int, agent_name: str, epoch: int, token: str = ""):
    """Report that this agent has seen rules at the given epoch."""
    try:
        import urllib.request
        body = json.dumps({"epoch": epoch}).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/rules/agent_sync/{agent_name}",
            method="POST",
            data=body,
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def _queue_watcher(get_identity_fn, inject_fn, *, is_multi_instance: bool = False, trigger_flag=None,
                   server_port: int = 8300, agent_name: str = "", get_token_fn=None,
                   refresh_interval: int = 10):
    """Poll queue file and inject an MCP read task when triggered."""
    first_mention = True
    last_rules_epoch = 0  # 0 = unknown/cold start — will inject on first trigger
    trigger_count = 0
    while True:
        try:
            _, queue_file = get_identity_fn()
            if queue_file.exists() and queue_file.stat().st_size > 0:
                with open(queue_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                queue_file.write_text("", "utf-8")

                has_trigger = False
                channel = "general"
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    has_trigger = True
                    if isinstance(data, dict) and "channel" in data:
                        channel = data["channel"]

                if has_trigger:
                    # Signal activity BEFORE injecting — covers the thinking phase
                    if trigger_flag is not None:
                        trigger_flag[0] = True
                    time.sleep(0.5)

                    # Check if this is a job/activity-scoped trigger
                    job_id = None
                    custom_prompt = ""
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict) and "job_id" in data:
                                job_id = data["job_id"]
                            if isinstance(data, dict):
                                raw_prompt = data.get("prompt", "")
                                if isinstance(raw_prompt, str) and raw_prompt.strip():
                                    custom_prompt = raw_prompt.strip()
                        except json.JSONDecodeError:
                            pass

                    if custom_prompt:
                        prompt = custom_prompt
                    elif job_id:
                        prompt = f"use mcp to read job_id={job_id} - you're mentioned in a job thread, take appropriate action and respond"
                    else:
                        prompt = f"use mcp to read #{channel} - you're mentioned, take appropriate action and respond"

                    # Use current identity (may have changed via rename)
                    current_name, _ = get_identity_fn()
                    # Append role if set — check both current name and base name
                    role = _fetch_role(server_port, current_name)
                    if not role and current_name != agent_name:
                        role = _fetch_role(server_port, agent_name)
                    if role:
                        prompt += f"\n\nROLE: {role}"

                    # Smart rules injection: first trigger, epoch change, or periodic refresh
                    _token = get_token_fn() if get_token_fn else ""
                    rules_data = _fetch_active_rules(server_port, _token)
                    trigger_count += 1
                    if rules_data:
                        # Use server-side refresh_interval (live from settings UI)
                        ri = rules_data.get("refresh_interval", refresh_interval)
                        need_inject = (
                            last_rules_epoch == 0
                            or rules_data["epoch"] != last_rules_epoch
                            or (ri > 0 and trigger_count % ri == 0)
                        )
                        if need_inject:
                            if rules_data["rules"]:
                                rules_text = "; ".join(rules_data["rules"])
                                prompt += f"\n\nRULES:\n{rules_text}"
                            last_rules_epoch = rules_data["epoch"]
                            _report_rule_sync(server_port, current_name, rules_data["epoch"], _token)

                    if first_mention and is_multi_instance:
                        prompt += _IDENTITY_HINT
                        first_mention = False
                    # Flatten to single line — multi-line text triggers paste
                    # detection in CLIs (Claude Code shows "[Pasted text +N]")
                    # which can break injection of long session prompts
                    inject_fn(prompt.replace("\n", " "))
        except Exception:
            pass

        time.sleep(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    import urllib.error
    import urllib.request

    from config_loader import apply_cli_overrides, load_config

    # Apply AGENTCHATTR_* overrides (from CLI flags or env) BEFORE loading
    # config so the wrapper connects to the same data_dir/ports as a server
    # launched with matching flags.
    apply_cli_overrides()
    config = load_config(ROOT)

    agent_names = list(config.get("agents", {}).keys())

    parser = argparse.ArgumentParser(description="Agent wrapper with chat auto-trigger")
    parser.add_argument("agent", choices=agent_names, help=f"Agent to wrap ({', '.join(agent_names)})")
    parser.add_argument("--no-restart", action="store_true", help="Do not restart on exit")
    parser.add_argument("--label", type=str, default=None, help="Custom display label")
    parser.add_argument("--preferred-name", type=str, default=None, help="Reuse a stopped launcher-owned instance name")
    parser.add_argument("--workdir", type=str, default=None, help="Override the agent working directory")
    # Per-project isolation flags (must match the server's flags so wrappers
    # launched separately connect to the right instance). Values are consumed
    # by apply_cli_overrides() above; listing here so --help shows them.
    parser.add_argument("--data-dir",      default=None, help="Override server.data_dir (path)")
    parser.add_argument("--port",          default=None, help="Override server.port (int)")
    parser.add_argument("--mcp-http-port", default=None, help="Override mcp.http_port (int)")
    parser.add_argument("--mcp-sse-port",  default=None, help="Override mcp.sse_port (int)")
    parser.add_argument("--upload-dir",    default=None, help="Override images.upload_dir (path)")
    args, extra = parser.parse_known_args()
    extra = _normalize_passthrough_args(extra)

    agent = args.agent
    agent_cfg = config.get("agents", {}).get(agent, {})
    project_dir = _resolve_agent_workdir(ROOT, agent_cfg.get("cwd", "."), args.workdir)
    command = agent_cfg.get("command", agent)
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    server_port = config.get("server", {}).get("port", 8300)
    mcp_cfg = config.get("mcp", {})

    # Process lease: one stable lease_id + our own PID + creation fingerprint
    # for this wrapper's lifetime. Sent on register and every heartbeat so the
    # server can resume our original name/token (the running child's MCP env
    # carries that token) and can check PID + fingerprint liveness before ever
    # timing us out (the fingerprint defeats PID reuse).
    lease_id = uuid.uuid4().hex
    wrapper_pid = os.getpid()
    try:
        from launcher_supervisor import process_start_marker
        wrapper_start_marker = process_start_marker(wrapper_pid)
    except Exception:
        wrapper_start_marker = ""

    try:
        registration = _register_instance(server_port, agent, args.label, args.preferred_name,
                                          lease_id=lease_id, pid=wrapper_pid,
                                          start_marker=wrapper_start_marker)
    except Exception as exc:
        print(f"  Registration failed ({exc}).")
        print("  Wrapper cannot continue without a registered identity.")
        sys.exit(1)

    assigned_name = registration["name"]
    assigned_token = registration["token"]
    print(f"  Registered as: {assigned_name} (slot {registration.get('slot', '?')})")

    proxy = None
    proxy_url = None

    # Resolve MCP injection mode to determine if a proxy is needed.
    # Direct-connect modes (settings_file, env, flag) don't need a proxy.
    # proxy_flag mode needs a proxy. No mcp_inject = proxy fallback.
    inject_cfg = _resolve_mcp_inject(agent, agent_cfg)
    inject_mode = inject_cfg.get("mcp_inject", "")
    if inject_mode and inject_mode not in _VALID_INJECT_MODES:
        print(f"  Error: unknown mcp_inject mode '{inject_mode}' for agent '{agent}'.")
        print(f"  Valid modes: {', '.join(sorted(_VALID_INJECT_MODES))}")
        sys.exit(1)
    needs_proxy = inject_mode in ("proxy_flag", "") or not inject_mode

    if needs_proxy:
        from mcp_proxy import McpIdentityProxy

        transport = inject_cfg.get("mcp_transport", "http")
        if transport == "sse":
            upstream_base = f"http://127.0.0.1:{mcp_cfg.get('sse_port', 8201)}"
            proxy_path = "/sse"
        else:
            upstream_base = f"http://127.0.0.1:{mcp_cfg.get('http_port', 8200)}"
            proxy_path = "/mcp"

        proxy = McpIdentityProxy(
            upstream_base=upstream_base,
            upstream_path=proxy_path,
            agent_name=assigned_name,
            instance_token=assigned_token,
        )
        if proxy.start() is False:
            print("  Failed to start MCP proxy.")
            sys.exit(1)
        proxy_url = f"{proxy.url}{proxy_path}"

    _identity_lock = threading.Lock()
    _identity = {
        "name": assigned_name,
        "queue": data_dir / f"{assigned_name}_queue.jsonl",
        "token": assigned_token,
    }

    def get_identity():
        with _identity_lock:
            return _identity["name"], _identity["queue"]

    def get_token():
        with _identity_lock:
            return _identity["token"]

    # Rewrite MCP config when token/name changes (e.g. after 409 re-register).
    # Most CLIs won't re-read mid-session, but the file is correct for next restart.
    def _rewrite_mcp_config(instance_name: str, new_token: str):
        if not inject_mode or needs_proxy:
            return  # proxy-based agents don't have config files to rewrite
        try:
            _apply_mcp_inject(
                inject_cfg, instance_name, data_dir, proxy_url,
                token=new_token, mcp_cfg=mcp_cfg,
                project_dir=project_dir,
            )
        except Exception:
            pass

    def set_runtime_identity(new_name: str | None = None, new_token: str | None = None):
        with _identity_lock:
            old_name = _identity["name"]
            old_token = _identity["token"]
            changed = False
            if new_name and new_name != old_name:
                _identity["name"] = new_name
                _identity["queue"] = data_dir / f"{new_name}_queue.jsonl"
                changed = True
            if new_token and new_token != old_token:
                _identity["token"] = new_token
                changed = True
            current_name = _identity["name"]
            current_token = _identity["token"]

        if changed and proxy is not None:
            proxy.agent_name = current_name
            proxy.token = current_token
        if changed:
            if new_name and new_name != old_name:
                print(f"  Identity updated: {old_name} -> {new_name}")
            if new_token and new_token != old_token:
                print(f"  Session refreshed for @{current_name}")
            _rewrite_mcp_config(current_name, current_token)

        return changed

    queue_file = _identity["queue"]
    if queue_file.exists():
        queue_file.write_text("", "utf-8")

    strip_vars = {"CLAUDECODE"} | set(agent_cfg.get("strip_env", []))
    env = {k: v for k, v in os.environ.items() if k not in strip_vars}

    resolved = shutil.which(command)
    if not resolved:
        print(f"  Error: '{command}' not found on PATH.")
        print("  Install it first, then try again.")
        sys.exit(1)
    command = resolved

    # Gemini: ensure the project directory is trusted so MCPs are allowed.
    # Gemini blocks ALL MCPs for untrusted folders — even system-settings ones.
    if agent == "gemini" or inject_cfg.get("mcp_inject") == "env":
        _ensure_gemini_folder_trusted(project_dir)

    launch_args, env, inject_env, mcp_settings_path = _build_provider_launch(
        agent=agent,
        agent_cfg=agent_cfg,
        instance_name=assigned_name,
        data_dir=data_dir,
        proxy_url=proxy_url,
        extra_args=extra,
        env=env,
        token=assigned_token,
        mcp_cfg=mcp_cfg,
        project_dir=project_dir,
    )

    print(f"  === {assigned_name.capitalize()} Chat Wrapper ===")
    if not needs_proxy:
        print(f"  MCP: direct connect ({inject_mode}) with bearer auth")
        if mcp_settings_path:
            print(f"  Config: {mcp_settings_path}")
    elif proxy_url:
        print(f"  Local MCP proxy: {proxy_url}")
    print(f"  @{assigned_name} mentions auto-inject MCP reads")
    print(f"  Starting {command} in {project_dir}...\n")

    # Shared heartbeat sender: one HTTP path + one failure counter/logger for
    # both the periodic heartbeat and the activity monitor.
    # Shutdown signal shared by the periodic heartbeat and the activity
    # monitor. Set FIRST in the finally block; both threads sleep via
    # `shutdown_event.wait(...)` (wakeable) and never initiate a send or a
    # 409 recovery once it is set. The finally block then joins both threads
    # so the deregister is guaranteed to be the LAST registry HTTP mutation.
    shutdown_event = threading.Event()

    heartbeat = HeartbeatSender(
        server_port=server_port,
        get_identity=get_identity,
        get_token=get_token,
        lease_id=lease_id,
        pid=wrapper_pid,
        start_marker=wrapper_start_marker,
    )

    def _recover_registration():
        """Re-register after a heartbeat 409 (e.g. server restart).

        The server recognizes our lease_id and — after verifying resume_token
        against the stored digest — returns the ORIGINAL name + token, so the
        already-running child's MCP config stays valid and no identity drift
        occurs. If the lease is NOT recognized (data dir wiped, digest
        mismatch), the token changes and the running child can no longer
        authenticate — warn loudly and rewrite the MCP config for the next
        launch.
        """
        if shutdown_event.is_set():
            # Shutdown began before this recovery started — a re-register now
            # would race the final deregister and resurrect a ghost identity.
            return
        old_name, _ = get_identity()
        old_token = get_token()
        try:
            replacement = _register_instance(
                server_port,
                agent,
                args.label,
                args.preferred_name,
                lease_id=lease_id,
                pid=wrapper_pid,
                start_marker=wrapper_start_marker,
                resume_token=old_token,
            )
        except urllib.error.HTTPError as exc:
            err_body = {}
            if exc.code == 409:
                try:
                    err_body = json.loads(exc.read())
                except Exception:
                    pass
            if err_body.get("error") == "invalid_lease_proof":
                # TERMINAL: the server holds a lease/session for us whose token
                # no longer matches ours. Retrying can never succeed — stop
                # all heartbeat/registration traffic. The CLI keeps running so
                # the user's session is not interrupted; chat connectivity
                # requires a wrapper restart.
                print("  [heartbeat] Session lease proof rejected by the server "
                      "(lease/token mismatch — server data may have been reset).")
                print("  [heartbeat] RESTART REQUIRED: restart this wrapper to register a "
                      "fresh session. Heartbeats stopped; the agent CLI keeps running.")
                heartbeat.terminal = True
                return
            print(f"  [heartbeat] re-registration failed (HTTP {exc.code}) — will retry")
            return
        except Exception as exc:
            print(f"  [heartbeat] re-registration failed ({type(exc).__name__}: {exc}) — will retry")
            return
        set_runtime_identity(replacement["name"], replacement["token"])
        new_name, _ = get_identity()
        new_token = get_token()
        if new_token != old_token or new_name != old_name:
            print(f"  [heartbeat] WARNING: session was NOT resumable — identity/token changed "
                  f"while the agent process is running ({old_name} -> {new_name}).")
            print(f"  [heartbeat] The running agent's MCP config still uses the old session. "
                  f"Restart this wrapper to restore chat connectivity.")
        _notify_recovery(data_dir, new_name)

    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(heartbeat, shutdown_event),
        kwargs={"get_identity": get_identity,
                "set_identity": set_runtime_identity,
                "recover": _recover_registration},
        daemon=True)
    _heartbeat_thread.start()

    _watcher_inject_fn = None
    _watcher_thread = None
    _is_multi_instance = registration.get("slot", 1) > 1
    _trigger_flag = [False]  # shared: queue watcher sets True, activity checker reads
    _refresh_interval = 10  # default; overridden per-trigger by server settings

    def start_watcher(inject_fn):
        nonlocal _watcher_inject_fn, _watcher_thread
        _watcher_inject_fn = inject_fn
        _watcher_thread = threading.Thread(
            target=_queue_watcher,
            args=(get_identity, inject_fn),
            kwargs={"is_multi_instance": _is_multi_instance, "trigger_flag": _trigger_flag,
                    "server_port": server_port, "agent_name": assigned_name,
                    "get_token_fn": get_token, "refresh_interval": _refresh_interval},
            daemon=True,
        )
        _watcher_thread.start()

    def _watcher_monitor():
        nonlocal _watcher_thread
        while True:
            time.sleep(5)
            if _watcher_thread and not _watcher_thread.is_alive() and _watcher_inject_fn:
                _watcher_thread = threading.Thread(
                    target=_queue_watcher,
                    args=(get_identity, _watcher_inject_fn),
                    kwargs={"is_multi_instance": _is_multi_instance, "trigger_flag": _trigger_flag,
                            "server_port": server_port, "agent_name": assigned_name,
                            "get_token_fn": get_token, "refresh_interval": _refresh_interval},
                    daemon=True,
                )
                _watcher_thread.start()
                current_name, _ = get_identity()
                _notify_recovery(data_dir, current_name)

    threading.Thread(target=_watcher_monitor, daemon=True).start()

    _activity_checker = None

    def _set_activity_checker(checker):
        nonlocal _activity_checker
        _activity_checker = checker

    def _activity_monitor():
        # Module-level loop is exception-isolated and unit-testable; the
        # closure over `_activity_checker` picks up the checker once set.
        # Shutdown-aware: wakeable sleep, no sends once shutdown is signaled.
        _activity_monitor_loop(
            lambda: _activity_checker, heartbeat,
            should_run=lambda: not shutdown_event.is_set(),
            sleep_fn=shutdown_event.wait)

    _activity_thread = threading.Thread(target=_activity_monitor, daemon=True)
    _activity_thread.start()

    _agent_pid = [None]

    if sys.platform == "win32":
        from wrapper_windows import get_activity_checker, run_agent

        _set_activity_checker(get_activity_checker(_agent_pid, agent_name=assigned_name, trigger_flag=_trigger_flag))
    else:
        from wrapper_unix import get_activity_checker, run_agent

        unix_session_name = f"agentchattr-{assigned_name}"
        _set_activity_checker(get_activity_checker(unix_session_name, trigger_flag=_trigger_flag))

    run_kwargs = dict(
        command=command,
        extra_args=launch_args,
        cwd=str(project_dir),
        env=env,
        queue_file=queue_file,
        agent=agent,
        no_restart=args.no_restart,
        start_watcher=start_watcher,
        strip_env=list(strip_vars),
        pid_holder=_agent_pid,
        inject_env=inject_env,
        inject_delay=agent_cfg.get("inject_delay", 0.3),
    )
    # Windows-only injection tuning (no-op on other platforms).
    if sys.platform == "win32":
        run_kwargs["enter_backend"] = agent_cfg.get("enter_backend", "console_input")
    if sys.platform != "win32":
        run_kwargs["session_name"] = unix_session_name

    try:
        run_agent(**run_kwargs)
    finally:
        # Shutdown ordering invariant: once shutdown begins, no new
        # heartbeat/register may be initiated; after all in-flight heartbeats
        # and 409 recoveries complete, the deregister must be the wrapper's
        # LAST registry HTTP mutation (a post-deregister 409 re-register
        # would resurrect a ghost identity for a dead process).
        def _final_deregister():
            try:
                current_name, _ = get_identity()
                current_token = get_token()
                dereg_req = urllib.request.Request(
                    f"http://127.0.0.1:{server_port}/api/deregister/{current_name}",
                    method="POST",
                    data=b"",
                    headers=_auth_headers(current_token),
                )
                urllib.request.urlopen(dereg_req, timeout=5)
                print(f"  Deregistered {current_name}")
            except Exception:
                pass

        _shutdown_and_deregister(
            shutdown_event, heartbeat,
            [_heartbeat_thread, _activity_thread],
            _final_deregister)

        if proxy is not None:
            proxy.stop()

    print("  Wrapper stopped.")


if __name__ == "__main__":
    main()
