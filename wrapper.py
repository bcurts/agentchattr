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
from pathlib import Path

ROOT = Path(__file__).parent

SERVER_NAME = "agentchattr"


# ---------------------------------------------------------------------------
# Per-instance provider config
# ---------------------------------------------------------------------------

def _write_json_mcp_settings(config_file: Path, url: str, transport: str = "http",
                              *, token: str = "", http_key: str = "httpUrl") -> Path:
    """Write/merge a settings-style JSON file with nested mcpServers config.

    Preserves existing servers in the file — only updates the agentchattr entry.

    Gemini CLI 0.32+ expects:
      - "httpUrl" key (not "url") for streamable-http transport
      - "url" key for SSE transport
      - "trust": true to skip per-call approval prompts

    `http_key` controls which JSON key names the HTTP transport URL. Defaults
    to "httpUrl" (Gemini/Qwen). Providers like CodeBuddy that follow the
    standard MCP shape should set `mcp_http_key = "url"` in their config.
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
    if token:
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
        "mcp_inject": "flag",
        "mcp_flag": "--mcp-config-file",
        "mcp_transport": "http",
        "mcp_merge_project": True,
    },
    "kilo": {
        "mcp_inject": "env_content",
        "mcp_env_var": "KILO_CONFIG_CONTENT",
        "mcp_transport": "http",
    },
}

_VALID_INJECT_MODES = {"settings_file", "env", "flag", "proxy_flag", "env_content"}


def _needs_proxy(inject_mode: str) -> bool:
    """Modes routed through the local MCP proxy.

    flag (Claude, Kimi) and proxy_flag (Codex) use it, and so does no mode
    (the fallback). settings_file, env and env_content still connect directly.
    """
    return inject_mode in ("flag", "proxy_flag", "")


def _provider_from_command(command: object) -> str:
    """Return a built-in provider for an exact configured executable name.

    Named agent identities (for example ``claude2``) are independent of
    the CLI they launch.  Resolve only exact known executable basenames; do
    not guess from prefixes or arbitrary command text.
    """
    if not isinstance(command, str):
        return ""
    executable = command.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if executable.endswith(suffix):
            executable = executable[:-len(suffix)]
            break
    return executable if executable in _BUILTIN_DEFAULTS else ""


def _resolve_mcp_inject(agent: str, agent_cfg: dict) -> dict:
    """Resolve MCP injection config: explicit config > provider defaults > none."""
    if agent_cfg.get("mcp_inject"):
        return dict(agent_cfg)
    provider = agent
    if provider not in _BUILTIN_DEFAULTS:
        provider = _provider_from_command(agent_cfg.get("command", ""))
    if provider:
        merged = dict(_BUILTIN_DEFAULTS[provider])
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
        settings_path = _write_json_mcp_settings(target, server_url,
                                                  transport=transport, token=token,
                                                  http_key=http_key)
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
        # Write a config file, pass it as a CLI flag. Points at the local
        # proxy so the session survives a chat-server restart (F4b).
        flag = inject_cfg.get("mcp_flag", "--mcp-config")
        merge_project = inject_cfg.get("mcp_merge_project", False)
        project_servers = _read_project_mcp_servers(project_dir) if (merge_project and project_dir) else {}
        settings_path = _write_claude_mcp_config(
            config_dir / f"{instance_name}-mcp.json",
            proxy_url or server_url, token=token, project_servers=project_servers,
        )
        launch_args = [flag, str(settings_path)]

    elif mode == "env_content":
        # Build JSON config content and set it as an env var directly (no file written).
        # Used by Kilo CLI which reads KILO_CONFIG_CONTENT at startup.
        env_var = inject_cfg.get("mcp_env_var")
        if not env_var:
            raise ValueError("mcp_inject = 'env_content' requires mcp_env_var")
        entry: dict = {"type": "remote", "url": server_url, "enabled": True}
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

    launch_args = [*mcp_args, *extra_args]
    launch_env = dict(env)

    return launch_args, launch_env, inject_env, settings_path


def _register_instance(server_port: int, base: str, label: str | None = None) -> dict:
    import urllib.request

    reg_body = json.dumps({"base": base, "label": label}).encode()
    reg_req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/api/register",
        method="POST",
        data=reg_body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(reg_req, timeout=5) as reg_resp:
        return json.loads(reg_resp.read())


def _post_register(server_port: int, body: dict) -> tuple[int, dict]:
    """POST /api/register; returns (status, json) instead of raising on 409."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/api/register",
        method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        return exc.code, json.loads(exc.read() or b"{}")


def _post_deregister(server_port: int, name: str, token: str, body: dict):
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/api/deregister/{name}",
        method="POST",
        data=json.dumps(body).encode(),
        headers=_auth_headers(token, include_json=True),
    )
    urllib.request.urlopen(req, timeout=3)


# ---------------------------------------------------------------------------
# Identity takeover (F3): a relaunched wrapper takes back its own stale identity
# ---------------------------------------------------------------------------

CTRL_CLOSE_EVENT = 2
_CLOSE_EVENTS = (CTRL_CLOSE_EVENT, 5, 6)  # close, logoff, shutdown


def _token_dir(data_dir: Path) -> Path:
    return data_dir / "wrapper-tokens"


def _restrict_permissions(path: Path):
    """Make `path` accessible to the current user only. Raises if that fails."""
    if os.name != "nt":
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        return
    import subprocess

    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    principal = f"{domain}\\{user}" if domain else user
    # The folder's ACE is inheritable so new files start user-only; without it
    # Windows falls back to the token's default DACL (Administrators, SYSTEM).
    rights = "(OI)(CI)F" if path.is_dir() else "F"
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:{rights}"],
                   check=True, capture_output=True)


def _save_token_file(data_dir: Path, entry: dict, pid: int | None = None) -> Path:
    """Persist this wrapper's instance token, readable only by the current user.

    The file is named by a hash of the token, so a rotated token gets a new file
    and two wrappers racing for one identity never delete each other's file.
    """
    import hashlib

    folder = _token_dir(data_dir)
    if not folder.exists():
        folder.mkdir(parents=True)
        _restrict_permissions(folder)
    digest = hashlib.sha256(entry["token"].encode()).hexdigest()[:16]
    path = folder / f"{entry['base']}-{digest}.json"
    record = {k: entry[k] for k in ("base", "name", "identity_id", "token")}
    record["pid"] = os.getpid() if pid is None else pid
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    try:
        _restrict_permissions(tmp)  # before the token is written
        tmp.write_text(json.dumps(record), "utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _load_token_files(data_dir: Path, base: str) -> list[dict]:
    found = []
    for path in _token_dir(data_dir).glob("*.json"):
        try:
            # Another wrapper may delete the file between listing and reading it.
            mtime = path.stat().st_mtime
            record = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if isinstance(record, dict) and record.get("base") == base and record.get("token"):
            record["path"] = path
            record["mtime"] = mtime
            found.append(record)
    return found


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reclaim_candidates(data_dir: Path, base: str) -> list[dict]:
    """Saved tokens of dead wrappers of `base`, newest first. Live owners are skipped."""
    records = [r for r in _load_token_files(data_dir, base) if not _pid_alive(r.get("pid"))]
    return sorted(records, key=lambda r: r["mtime"], reverse=True)


def _append_claim(claimed: Path, target: Path):
    text = claimed.read_text("utf-8")
    if text:
        if not text.endswith("\n"):
            text += "\n"
        if target.exists() and target.stat().st_size:
            with open(target, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    text = "\n" + text
        with open(target, "a", encoding="utf-8") as f:
            f.write(text)
    claimed.unlink()


def _finish_claims(data_dir: Path, name: str):
    """Append claims into `name`'s queue that a crash left behind."""
    claims = sorted(data_dir.glob(f"{name}_queue.from-*.migrating"),
                    key=lambda p: p.stat().st_mtime)
    for claimed in claims:
        _append_claim(claimed, data_dir / f"{name}_queue.jsonl")


def _pending_marker(data_dir: Path, old_name: str, new_name: str) -> Path:
    return data_dir / f"{new_name}_queue.from-{old_name}.pending"


def _has_pending_migrations(data_dir: Path, name: str) -> bool:
    return any(data_dir.glob(f"{name}_queue.from-*.pending"))


def _recover_queue_claims(data_dir: Path, name: str):
    """Finish every migration into `name`'s queue: crash-interrupted claims, and
    migrations recorded by a .pending marker that have not succeeded yet."""
    _finish_claims(data_dir, name)
    prefix = f"{name}_queue.from-"
    for marker in data_dir.glob(f"{prefix}*.pending"):
        _migrate_queue(data_dir, marker.name[len(prefix):-len(".pending")], name)
        marker.unlink()


def _migrate_queue(data_dir: Path, old_name: str, new_name: str):
    """Move undelivered wake-ups from old_name's queue files onto new_name's queue.

    Each source is first claimed by renaming it to a file that names its target, so
    a crash between claim and append leaves something _recover_queue_claims finds.
    """
    if old_name == new_name:
        return
    _finish_claims(data_dir, new_name)  # never overwrite an unrecovered claim
    target = data_dir / f"{new_name}_queue.jsonl"
    for source in (data_dir / f"{old_name}_queue.processing",
                   data_dir / f"{old_name}_queue.jsonl"):
        claimed = data_dir / f"{new_name}_queue.from-{source.name}.migrating"
        try:
            # Claim first: a writer appending meanwhile creates a fresh file.
            os.replace(source, claimed)
        except FileNotFoundError:
            continue
        _append_claim(claimed, target)


def _acquire_identity(register, data_dir: Path, base: str, label: str | None = None,
                      *, sleep=time.sleep, max_wait: float = 20) -> dict:
    """Register, preferring to take over this base's identity left by a dead wrapper.

    `register(body) -> (status, json)`. A 409 identity_in_use means the old owner
    still looks alive to the server (its last heartbeat is recent); wait for that to
    lapse up to `max_wait` seconds, then fall back to a fresh registration.
    """
    candidates = _reclaim_candidates(data_dir, base)
    offered = list(candidates)
    waited = 0
    while True:
        body = {"base": base, "label": label}
        if candidates:
            body["reclaim"] = [{"token": c["token"], "name": c["name"]} for c in candidates]
        status, reg = register(body)
        if status == 409 and candidates and reg.get("error") == "identity_in_use":
            delay = reg.get("retry_after", 5)
            if waited + delay > max_wait:
                candidates = []
            else:
                sleep(delay)
                waited += delay
            continue
        if status != 200:
            raise RuntimeError(f"registration failed ({status}): {reg}")
        break

    # Save the rotated token before anything that can fail: the old one is dead now.
    try:
        reg["token_path"] = _save_token_file(data_dir, {**reg, "base": base})
    except Exception as exc:
        print(f"  Warning: could not save identity token ({exc}); a relaunch will get a new name.")
    dead = set(reg.get("dead_tokens", []))
    for c in offered:
        if c["token"] in dead or c.get("identity_id") == reg.get("identity_id"):
            c["path"].unlink(missing_ok=True)

    # Record the migration durably first; the marker stays until it succeeds, so
    # a later retry or the next startup finishes it.
    if reg.get("migrate_queue_from"):
        try:
            _pending_marker(data_dir, reg["migrate_queue_from"], reg["name"]).touch()
        except OSError:
            pass
    # Queue files may be briefly held open by the server's writer; retry, then
    # leave them for _retry_pending_migrations rather than fail startup.
    for attempt in range(5):
        try:
            _recover_queue_claims(data_dir, reg["name"])
            break
        except OSError as exc:
            if attempt == 4:
                print(f"  Warning: could not move queued wake-ups yet ({exc}); will retry.")
            else:
                sleep(0.2)
    return reg


def _retry_pending_migrations(data_dir: Path, get_name, *, sleep=time.sleep, interval=5):
    """Keep finishing recorded queue migrations until none remain.

    The markers follow this wrapper's name: if it is renamed meanwhile (e.g. slot 1
    becoming base-1 when a second instance joins), they are retargeted to the new
    name, whose queue the wrapper now watches.
    """
    target = [get_name()]

    def follow_rename():
        current = get_name()
        if current == target[0]:
            return
        prefix = f"{target[0]}_queue.from-"
        for marker in data_dir.glob(f"{prefix}*.pending"):
            try:
                os.replace(marker, data_dir / f"{current}_queue.from-{marker.name[len(prefix):]}")
            except OSError:
                pass
        target[0] = current

    while True:
        follow_rename()
        if not _has_pending_migrations(data_dir, target[0]):
            return
        sleep(interval)
        follow_rename()
        try:
            _recover_queue_claims(data_dir, target[0])
        except OSError:
            pass


def _deregister(post, name: str, token: str, token_path: Path | None, *, reclaimable: bool):
    """Deregister this wrapper. A deliberate exit also forgets the saved token."""
    try:
        post(name, token, {"reclaimable": reclaimable})
        return True
    except Exception:
        return False
    finally:
        if not reclaimable and token_path is not None:
            token_path.unlink(missing_ok=True)


def _console_ctrl_handler(on_close):
    """Build a console control handler that runs `on_close` once on close/logoff/shutdown.

    Ctrl+C / Ctrl+Break return False so their default handling still applies.
    """
    fired = threading.Lock()

    def handler(event) -> bool:
        if event not in _CLOSE_EVENTS:
            return False
        if fired.acquire(blocking=False):
            on_close()
        return True

    return handler


def _install_shutdown_handler(on_close):
    """Run `on_close` when the console window closes (Windows) or on SIGTERM/SIGHUP."""
    handler = _console_ctrl_handler(on_close)
    if os.name == "nt":
        import ctypes

        routine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)(lambda e: int(handler(e)))
        ctypes.windll.kernel32.SetConsoleCtrlHandler(routine, True)
        return routine  # caller keeps the reference alive
    import signal

    def on_signal(signum, frame):
        handler(CTRL_CLOSE_EVENT)
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_signal)
    return on_signal


def _auth_headers(token: str, *, include_json: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


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

# Appended to every wake prompt: one read, act, at most one reply, end the turn.
_WAKE_STEPS = (
    "Call chat_read once with your sender name (it returns only unread messages), "
    "do what the message asks, call chat_send once if a reply is needed, then END YOUR TURN. "
    "Do not call chat_who, or chat_rules to list rules (rules are included below when they change). "
    "Never wait by sleeping or re-reading; the room wakes you when there is something new. "
    "Acknowledge with chat_react(msg_id=<id>, emoji='✅', sender=<your name>), not a message, "
    "and with no @mention. @mention another agent only when they must act."
)

_NEW_SESSION_STEPS = (
    "NEW SESSION: before anything else call chat_join(name=<your name>, channel='{channel}'), "
    "then chat_summary(action='read', sender=<your name>, channel='{channel}'). "
)


def _session_marker(path: Path | None) -> str | None:
    """The CLI session that already received its new-session wake prompt."""
    if path is None:
        return None
    try:
        return json.loads(path.read_text("utf-8")).get("session")
    except Exception:
        return None


def _save_session_marker(path: Path, session: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"session": session}), "utf-8")
    os.replace(tmp, path)


def _is_restart_line(line: str) -> bool:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and data.get("type") == "restart"


def _restart_cli(pid_holder: list, restart_event: threading.Event, kill_fn, *,
                 sleep=time.sleep, timeout: float = 60, boot_grace: float = 8):
    """Stop the CLI and wait until the run loop has launched a fresh one.

    The wrapper itself keeps running, so its identity, token, proxy and queue
    stay as they are; only the CLI session (and its context) starts over.
    """
    old = pid_holder[0]
    restart_event.set()  # relaunch even under --no-restart
    if old is not None:
        kill_fn(old)
    waited = 0.0
    while pid_holder[0] is None or pid_holder[0] == old:
        if waited >= timeout:
            restart_event.clear()
            raise RuntimeError("CLI did not relaunch")
        sleep(0.5)
        waited += 0.5
    sleep(boot_grace)  # let the new CLI reach its prompt before a wake is typed


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


def _group_queue_triggers(lines: list[str]) -> list[tuple[tuple, list[str]]]:
    """Group wake requests by destination while keeping first-seen order."""
    groups: dict[tuple, list[str]] = {}
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        channel = data.get("channel") or "general"
        job_id = data.get("job_id")
        raw_prompt = data.get("prompt", "")
        custom_prompt = raw_prompt.strip() if isinstance(raw_prompt, str) else ""
        key = (channel, job_id, custom_prompt)
        groups.setdefault(key, []).append(line)
    return list(groups.items())


MAX_ACTIVE_DEFER_SECONDS = 180


def _defer_queue_injection(get_activity_fn, processing: Path) -> bool:
    """Keep the queued prompt while the agent is busy or awaiting approval.

    The processing file's mtime survives watcher restarts and bounds an
    erroneously persistent active report. Approval prompts are never forced:
    an injected Enter could approve a command.
    """
    if get_activity_fn is None:
        return False
    report = get_activity_fn()
    if report and report.get("waiting_approval"):
        return True
    if report is not None and not report.get("active"):
        return False
    return time.time() - processing.stat().st_mtime < MAX_ACTIVE_DEFER_SECONDS


def _queue_watcher(get_identity_fn, inject_fn, *, is_multi_instance: bool = False, trigger_flag=None,
                   server_port: int = 8300, agent_name: str = "", get_token_fn=None,
                   refresh_interval: int = 10, get_activity_fn=None,
                   session_state_path: Path | None = None, get_session_fn=None,
                   restart_fn=None):
    """Poll queue file and inject an MCP read task when triggered.

    The first wake of each CLI session (identified by get_session_fn) asks for
    chat_join and the summary; session_state_path records that it was sent, so
    a restarted watcher does not ask again. A queued restart entry runs
    restart_fn before any wake-up in the same batch.
    """
    greeted = None  # used when no state file is configured
    last_rules_epoch = 0  # 0 = unknown/cold start; inject on first trigger
    trigger_count = 0
    while True:
        try:
            _, queue_file = get_identity_fn()
            processing = queue_file.with_suffix(".processing")
            if not processing.exists() and queue_file.exists() and queue_file.stat().st_size > 0:
                try:
                    # Rename before reading; new appends create a fresh queue file.
                    os.replace(queue_file, processing)
                except (FileNotFoundError, PermissionError):
                    # On Windows a writer may still have the file open.
                    pass

            if processing.exists():
                lines = processing.read_text("utf-8").splitlines(keepends=True)
                wakes = [line for line in lines if not _is_restart_line(line)]
                if len(wakes) < len(lines):
                    if _defer_queue_injection(get_activity_fn, processing):
                        wakes = None  # hold everything until the agent is idle
                    else:
                        if restart_fn is not None:
                            restart_fn()  # raises on failure, keeping the queue
                        temporary = processing.with_suffix(".processing.tmp")
                        temporary.write_text("".join(wakes), encoding="utf-8")
                        os.replace(temporary, processing)
                groups = _group_queue_triggers(wakes or [])
                if wakes is not None and not groups:
                    processing.unlink()
                for index, ((channel, job_id, custom_prompt), _) in enumerate(groups):
                    if _defer_queue_injection(get_activity_fn, processing):
                        break
                    time.sleep(0.5)
                    if _defer_queue_injection(get_activity_fn, processing):
                        break

                    if custom_prompt:
                        prompt = custom_prompt
                    elif job_id:
                        prompt = f"use mcp to read job_id={job_id} - you're mentioned in a job thread, take appropriate action and respond."
                    else:
                        prompt = f"use mcp to read #{channel} - you're mentioned, take appropriate action and respond."
                    prompt += " " + _WAKE_STEPS

                    session = str(get_session_fn()) if get_session_fn else "default"
                    stored = (_session_marker(session_state_path)
                              if session_state_path is not None else greeted)
                    new_session = stored != session
                    if new_session:
                        prompt = _NEW_SESSION_STEPS.format(channel=channel) + prompt

                    current_name, _ = get_identity_fn()
                    role = _fetch_role(server_port, current_name)
                    if not role and current_name != agent_name:
                        role = _fetch_role(server_port, agent_name)
                    if role:
                        prompt += f"\n\nROLE: {role}"

                    token = get_token_fn() if get_token_fn else ""
                    rules_data = _fetch_active_rules(server_port, token)
                    trigger_count += 1
                    sync_rules = False
                    if rules_data:
                        ri = rules_data.get("refresh_interval", refresh_interval)
                        sync_rules = (
                            new_session
                            or last_rules_epoch == 0
                            or rules_data["epoch"] != last_rules_epoch
                            or (ri > 0 and trigger_count % ri == 0)
                        )
                        if sync_rules and rules_data["rules"]:
                            prompt += f"\n\nRULES: {'; '.join(rules_data['rules'])}"

                    if new_session and is_multi_instance:
                        prompt += _IDENTITY_HINT
                    # Flatten to one line to avoid CLI paste detection.
                    if inject_fn(prompt.replace("\n", " ")) is False:
                        raise RuntimeError("CLI injection failed")
                    if trigger_flag is not None:
                        trigger_flag[0] = True
                    if new_session:
                        greeted = session
                        if session_state_path is not None:
                            try:
                                _save_session_marker(session_state_path, session)
                            except OSError:
                                pass  # worst case the next wake asks to join again
                    if sync_rules:
                        last_rules_epoch = rules_data["epoch"]
                        _report_rule_sync(server_port, current_name, last_rules_epoch, token)

                    # Acknowledge each destination only after injection succeeds.
                    # Unacknowledged destinations survive failure or restart.
                    remaining = [line for _, group_lines in groups[index + 1:]
                                 for line in group_lines]
                    if remaining:
                        temporary = processing.with_suffix(".processing.tmp")
                        temporary.write_text("".join(remaining), encoding="utf-8")
                        os.replace(temporary, processing)
                    else:
                        processing.unlink()
        except Exception:
            pass

        time.sleep(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _build_activity_report(checker):
    """Translate the console checker state into the heartbeat payload."""
    state = checker()
    return {"active": state is True, "waiting_approval": state == "waiting_approval"}


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
    # Per-project isolation flags (must match the server's flags so wrappers
    # launched separately connect to the right instance). Values are consumed
    # by apply_cli_overrides() above; listing here so --help shows them.
    parser.add_argument("--data-dir",      default=None, help="Override server.data_dir (path)")
    parser.add_argument("--port",          default=None, help="Override server.port (int)")
    parser.add_argument("--mcp-http-port", default=None, help="Override mcp.http_port (int)")
    parser.add_argument("--mcp-sse-port",  default=None, help="Override mcp.sse_port (int)")
    parser.add_argument("--upload-dir",    default=None, help="Override images.upload_dir (path)")
    args, extra = parser.parse_known_args()

    agent = args.agent
    agent_cfg = config.get("agents", {}).get(agent, {})
    cwd = agent_cfg.get("cwd", ".")
    command = agent_cfg.get("command", agent)
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    server_port = config.get("server", {}).get("port", 8300)
    mcp_cfg = config.get("mcp", {})

    try:
        # Takes back this agent's identity from a dead wrapper when it can (F3).
        registration = _acquire_identity(lambda body: _post_register(server_port, body),
                                         data_dir, agent, args.label)
    except Exception as exc:
        print(f"  Registration failed ({exc}).")
        print("  Wrapper cannot continue without a registered identity.")
        sys.exit(1)

    assigned_name = registration["name"]
    assigned_token = registration["token"]
    reclaimed = " - took over previous session" if registration.get("reclaimed") else ""
    print(f"  Registered as: {assigned_name} (slot {registration.get('slot', '?')}){reclaimed}")

    proxy = None
    proxy_url = None

    # Resolve MCP injection mode to determine if a proxy is needed.
    # See _needs_proxy for which modes connect directly.
    inject_cfg = _resolve_mcp_inject(agent, agent_cfg)
    inject_mode = inject_cfg.get("mcp_inject", "")
    if inject_mode and inject_mode not in _VALID_INJECT_MODES:
        print(f"  Error: unknown mcp_inject mode '{inject_mode}' for agent '{agent}'.")
        print(f"  Valid modes: {', '.join(sorted(_VALID_INJECT_MODES))}")
        sys.exit(1)
    needs_proxy = _needs_proxy(inject_mode)

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
        "identity_id": registration.get("identity_id", ""),
        "token_path": registration.get("token_path"),
    }

    def get_identity():
        with _identity_lock:
            return _identity["name"], _identity["queue"]

    def get_token():
        with _identity_lock:
            return _identity["token"]

    if _has_pending_migrations(data_dir, assigned_name):
        threading.Thread(target=_retry_pending_migrations,
                         args=(data_dir, lambda: get_identity()[0]), daemon=True).start()

    # Rewrite MCP config when token/name changes (e.g. after 409 re-register).
    # Most CLIs won't re-read mid-session, but the file is correct for next restart.
    def _rewrite_mcp_config(instance_name: str, new_token: str):
        if not inject_mode or needs_proxy:
            return  # the proxy URL is stable and the proxy stamps the new token
        try:
            _apply_mcp_inject(
                inject_cfg, instance_name, data_dir, proxy_url,
                token=new_token, mcp_cfg=mcp_cfg,
                project_dir=(ROOT / cwd).resolve(),
            )
        except Exception:
            pass

    def _resave_token_file():
        """Keep the saved token in step with a rename or re-registration."""
        with _identity_lock:
            entry = {"base": agent, "name": _identity["name"],
                     "identity_id": _identity["identity_id"], "token": _identity["token"]}
            old_path = _identity["token_path"]
        try:
            new_path = _save_token_file(data_dir, entry)
        except Exception:
            return
        with _identity_lock:
            _identity["token_path"] = new_path
        if old_path and old_path != new_path:
            old_path.unlink(missing_ok=True)

    def set_runtime_identity(new_name: str | None = None, new_token: str | None = None,
                             new_identity_id: str | None = None):
        with _identity_lock:
            old_name = _identity["name"]
            old_token = _identity["token"]
            if new_identity_id:
                _identity["identity_id"] = new_identity_id
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
            _resave_token_file()

        return changed

    queue_file = _identity["queue"]

    strip_vars = {"CLAUDECODE"} | set(agent_cfg.get("strip_env", []))
    env = {k: v for k, v in os.environ.items() if k not in strip_vars}

    resolved = shutil.which(command)
    if not resolved:
        print(f"  Error: '{command}' not found on PATH.")
        print("  Install it first, then try again.")
        sys.exit(1)
    command = resolved

    project_dir = (ROOT / cwd).resolve()

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
        if mcp_settings_path:
            print(f"  Config: {mcp_settings_path}")
    print(f"  @{assigned_name} mentions auto-inject MCP reads")
    print(f"  Starting {command} in {cwd}...\n")

    def _heartbeat():
        while True:
            current_name, _ = get_identity()
            current_token = get_token()
            url = f"http://127.0.0.1:{server_port}/api/heartbeat/{current_name}"
            try:
                req = urllib.request.Request(
                    url,
                    method="POST",
                    data=b"",
                    headers=_auth_headers(current_token),
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp_data = json.loads(resp.read())
                server_name = resp_data.get("name", current_name)
                if server_name != current_name:
                    set_runtime_identity(server_name)
            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    try:
                        replacement = _register_instance(server_port, agent, args.label)
                        set_runtime_identity(replacement["name"], replacement["token"],
                                             replacement.get("identity_id"))
                        _notify_recovery(data_dir, replacement["name"])
                    except Exception:
                        pass
                time.sleep(5)
                continue
            except Exception:
                time.sleep(5)
                continue

            time.sleep(5)

    threading.Thread(target=_heartbeat, daemon=True).start()

    _watcher_inject_fn = None
    _watcher_thread = None
    _is_multi_instance = registration.get("slot", 1) > 1
    _trigger_flag = [False]  # shared: queue watcher sets True, activity checker reads
    _activity_report = None  # latest result from the activity checker
    _refresh_interval = 10  # default; overridden per-trigger by server settings

    def get_activity_report():
        # An injection that the checker has not consumed yet is already active.
        # Before the first checker poll, hold queued prompts rather than guess idle.
        if _trigger_flag[0]:
            return {"active": True, "waiting_approval": False}
        return _activity_report

    # The CLI session is this wrapper process plus the CLI launch; a relaunched
    # wrapper or a restarted CLI is a new session that should join and catch up.
    _agent_pid = [None]
    _restart_event = threading.Event()
    # Not in the token folder: creating that folder here would skip its ACL setup.
    _session_marker_path = data_dir / (
        f"{agent}-{registration.get('identity_id') or assigned_name}.session.json")

    def _kill_cli(pid):
        import subprocess
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            subprocess.run(["tmux", "kill-session", "-t", unix_session_name], capture_output=True)

    def restart_cli():
        current_name, _ = get_identity()
        print(f"\n  New task: restarting {current_name}'s CLI session...")
        _restart_cli(_agent_pid, _restart_event, _kill_cli)

    _session_kwargs = {
        "session_state_path": _session_marker_path,
        "get_session_fn": lambda: f"{os.getpid()}:{_agent_pid[0]}",
        "restart_fn": restart_cli,
    }

    def start_watcher(inject_fn):
        nonlocal _watcher_inject_fn, _watcher_thread
        _watcher_inject_fn = inject_fn
        _watcher_thread = threading.Thread(
            target=_queue_watcher,
            args=(get_identity, inject_fn),
            kwargs={"is_multi_instance": _is_multi_instance, "trigger_flag": _trigger_flag,
                    "server_port": server_port, "agent_name": assigned_name,
                    "get_token_fn": get_token, "refresh_interval": _refresh_interval,
                    "get_activity_fn": get_activity_report, **_session_kwargs},
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
                            "get_token_fn": get_token, "refresh_interval": _refresh_interval,
                            "get_activity_fn": get_activity_report, **_session_kwargs},
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
        nonlocal _activity_report
        last_state = None
        last_report_time = 0
        REPORT_INTERVAL = 3  # re-send state every 3s while active (keeps server lease fresh)
        while True:
            time.sleep(1)
            if not _activity_checker:
                continue
            try:
                report = _build_activity_report(_activity_checker)
                _activity_report = report
                state = (report["active"], report["waiting_approval"])
                now = time.time()
                # Send on state change, periodically while active (refresh lease),
                # or periodically while idle (keep presence alive)
                IDLE_REPORT_INTERVAL = 8  # keep-alive while idle
                should_send = (
                    state != last_state
                    or (any(state) and now - last_report_time >= REPORT_INTERVAL)
                    or (not any(state) and now - last_report_time >= IDLE_REPORT_INTERVAL)
                )
                if should_send:
                    current_name, _ = get_identity()
                    current_token = get_token()
                    url = f"http://127.0.0.1:{server_port}/api/heartbeat/{current_name}"
                    body = json.dumps(report).encode()
                    req = urllib.request.Request(
                        url,
                        method="POST",
                        data=body,
                        headers=_auth_headers(current_token, include_json=True),
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    resp_code = resp.getcode()
                    last_state = state
                    last_report_time = now
            except Exception:
                pass

    threading.Thread(target=_activity_monitor, daemon=True).start()

    if sys.platform == "win32":
        from wrapper_windows import get_activity_checker, run_agent

        _set_activity_checker(get_activity_checker(_agent_pid, agent_name=assigned_name, trigger_flag=_trigger_flag))
    else:
        from wrapper_unix import get_activity_checker, run_agent

        unix_session_name = f"agentchattr-{assigned_name}"
        _set_activity_checker(get_activity_checker(
            unix_session_name, trigger_flag=_trigger_flag,
            provider=_provider_from_command(command),
        ))

    run_kwargs = dict(
        command=command,
        extra_args=launch_args,
        cwd=cwd,
        env=env,
        queue_file=queue_file,
        agent=agent,
        no_restart=args.no_restart,
        start_watcher=start_watcher,
        strip_env=list(strip_vars),
        pid_holder=_agent_pid,
        restart_event=_restart_event,
        inject_env=inject_env,
        inject_delay=agent_cfg.get("inject_delay", 0.3),
    )
    # Windows-only injection tuning (no-op on other platforms).
    if sys.platform == "win32":
        run_kwargs["enter_backend"] = agent_cfg.get("enter_backend", "console_input")
    if sys.platform != "win32":
        run_kwargs["session_name"] = unix_session_name

    def _deregister_current(reclaimable: bool):
        current_name, _ = get_identity()
        with _identity_lock:
            current_token, token_path = _identity["token"], _identity["token_path"]
        if _deregister(lambda n, t, b: _post_deregister(server_port, n, t, b),
                       current_name, current_token, token_path, reclaimable=reclaimable):
            print(f"  Deregistered {current_name}")

    # Console closed / SIGTERM: deregister but keep the token, so a relaunch takes
    # this identity back. A normal exit below is deliberate and forgets the token.
    _closing = threading.Event()

    def _close_reclaimable():
        _closing.set()
        _deregister_current(reclaimable=True)

    _shutdown_handler_ref = _install_shutdown_handler(_close_reclaimable)  # noqa: F841 (keep alive)

    try:
        run_agent(**run_kwargs)
    finally:
        if not _closing.is_set():
            _deregister_current(reclaimable=False)

        if proxy is not None:
            proxy.stop()

    print("  Wrapper stopped.")


if __name__ == "__main__":
    main()
