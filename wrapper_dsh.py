"""DSH (DeepSeek Harness) chat adapter for agentchattr.

Unlike the CLI wrapper (``wrapper.py``), which injects keystrokes into an
interactive terminal, this adapter drives a **DeepSeek Harness SDK runtime**
over its JSON-RPC stdio protocol. There is no terminal and no keyboard
simulation involved.

Flow:

  1. Register with the agentchattr server and receive a per-instance bearer
     token (reusing the same registration/heartbeat/deregister path).
  2. Write a per-instance DSH configuration overlay that mounts the
     agentchattr MCP server (authenticated by the token) and pins the
     fully-permissive execution policy — the launcher shows this as
     "完全访问" and no interactive approval surface is loaded.
  3. Spawn ``dsh --profile sdk`` with an isolated DSH home, profile, session
     store and log directory.
  4. Handshake via ``initialize`` and verify real MCP tool registrations.
     Provider authentication is only proven by a successful model request.
  5. Poll the agentchattr trigger queue. On a trigger, enqueue a
     ``session/prompt`` carrying the channel/job target, rules, role and
     identity hints. The DSH agent then calls ``chat_read`` / ``chat_send``
     through MCP to participate.
  6. Report busy/idle from runtime session events plus the local wait queue,
     over the existing heartbeat path.

Deliberate design points (see the development plan):

  * DSH has no per-task cancel protocol — "stop" stops the whole instance.
  * One trigger runs at a time per instance; the rest wait in order.
  * A session is kept per channel/job and reused by later triggers.
  * Replies are published only through MCP: this adapter never re-forwards
    final model text into the chat room.
  * On restart a new session is created; conversational context is recovered
    from chat history. Interrupted coding tasks are NOT replayed.
"""

from __future__ import annotations

import argparse
import json
import os
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

from dsh_protocol import (  # noqa: E402
    DshClient,
    DshNotification,
    DshProtocolError,
    DshRpcError,
    DshTimeout,
    DshTransportClosed,
    build_launch_command,
    extract_assistant_text,
    normalize_prompt_blocks,
)

SERVER_NAME = "agentchattr"

# The launcher surfaces this label; the runtime is genuinely unconfined.
ACCESS_MODE_LABEL = "完全访问"
ACCESS_MODE_DESC = (
    "完整权限自动执行（danger-full-access，无审批）。DSH 可修改工作目录内外的文件并执行 PowerShell。"
)


# ---------------------------------------------------------------------------
# Environment / dependency resolution
# ---------------------------------------------------------------------------

def _check_dependencies(agent_cfg: dict) -> tuple[dict, str]:
    """Return (resolved, error). ``error`` is a specific reason, not a bool."""
    from dsh_runtime import check_dependencies
    return check_dependencies(agent_cfg, ROOT)


# ---------------------------------------------------------------------------
# DSH profile overlay (MCP mount + full-access policy)
# ---------------------------------------------------------------------------

def write_profile_manifest(profile_dir: Path, *, bundles: list[str], patch_reload: str = "startup") -> Path:
    """Write ``package.json`` for a custom DSH profile.

    A profile is a ``package.json`` (with a ``dsh.profile`` manifest) plus a
    ``cordis.patch.yml`` user layer. We build a custom profile so each chat
    room instance owns its own MCP mount and policy without touching the
    user's shipped profiles.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": f"agentchattr-{profile_dir.name}",
        "private": True,
        "version": "0.0.0",
        "dsh": {
            "profile": {
                "bundles": list(bundles),
                "patchReload": patch_reload,
            }
        },
    }
    path = profile_dir / "package.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def write_patch_overlay(
    patch_path: Path,
    *,
    mcp_url: str,
    token_env_var: str,
    server_name: str = SERVER_NAME,
) -> Path:
    """Write a ``--patch`` overlay mounting the chat MCP server.

    Only the patch layer is generated per instance: it inserts the MCP client
    row whose bearer token is read from the child's environment, so the token
    never lands on disk.
    """
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    # YAML with a single quoted JS expression. Hand-writing it keeps this module
    # dependency-free (pyyaml is not a project dependency).
    content = (
        "# Generated per instance by wrapper_dsh.py — do not edit by hand.\n"
        "# Mounts the agentchattr chat room as native MCP tools.\n"
        "\n"
        "- insert:\n"
        f"    - id: mcp-{server_name}\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "      config:\n"
        f"        serverName: {server_name}\n"
        "        transport: streamable-http\n"
        f"        url: '{mcp_url}'\n"
        "        headers:\n"
        f"          Authorization: !!js \"`Bearer ${{process.env.{token_env_var}}}`\"\n"
        "        # Fail loudly if the chat room is unreachable: an instance that\n"
        "        # cannot talk to chat must never look like it is working.\n"
        "        failOnStartupError: true\n"
    )
    patch_path.write_text(content, encoding="utf-8")
    return patch_path


def write_readiness_overlay(instance_dir: Path) -> Path:
    """Observe the actual SDK tool registry without a model call or chat mutation."""
    plugin = instance_dir / "chat-readiness.mjs"
    plugin.write_text('''export const name = 'agentchattr-readiness';
export const inject = ['tools'];
export function apply(ctx) {
  const required = ['mcp__agentchattr__chat_read', 'mcp__agentchattr__chat_send'];
  let previous = '';
  function check() {
    const tools = required.filter(name => ctx.tools.get(name));
    const state = JSON.stringify({ tools });
    if (state !== previous) {
      previous = state;
      process.stderr.write('AGENTCHATTR_MCP_REGISTRY ' + state + '\\n');
    }
  }
  ctx.on('tools/change', check);
  ctx.on('tools/result', (exec, result) => {
    if (!required.includes(exec.name)) return;
    process.stderr.write('AGENTCHATTR_CHAT_TOOL ' + JSON.stringify({
      tool: exec.name.slice('mcp__agentchattr__'.length),
      session: String(exec.agent?.session?.id ?? ''),
      status: result.isError ? 'error' : 'ok'
    }) + '\\n');
  });
  check();
}
''', encoding="utf-8")
    patch = instance_dir / "chat-readiness.patch.yml"
    patch.write_text("- insert:\n    - id: agentchattr-readiness\n      name: "
                     + json.dumps(str(plugin.resolve())) + "\n", encoding="utf-8")
    return patch


def write_full_access_overlay(patch_path: Path) -> Path:
    """Pin ``danger-full-access`` with no approval surface for one instance.

    The base bundle already derives sandbox mode and approval policy from
    ``DSH_PERMISSION_MODE``; this overlay makes the policy explicit in the
    composed tree and disables the interactive approval row so nothing can
    block an unattended run.
    """
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Generated per instance by wrapper_dsh.py — do not edit by hand.\n"
        "# Full-access execution policy, no interactive approval surface.\n"
        "\n"
        "- id: sandbox-policy\n"
        "  config:\n"
        "    mode: danger-full-access\n"
        "    workspaceRoot: !!js process.cwd()\n"
        "\n"
        "- id: approval\n"
        "  config:\n"
        "    policy: never\n"
        "\n"
        "# The approval service can never prompt without a surface to prompt on.\n"
        "- id: user-questions\n"
        "  disabled: true\n"
    )
    patch_path.write_text(content, encoding="utf-8")
    return patch_path


# ---------------------------------------------------------------------------
# Server interaction (mirrors wrapper.py's contract)
# ---------------------------------------------------------------------------

def _auth_headers(token: str, *, include_json: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


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
    body = json.dumps(
        {
            "base": base,
            "label": label,
            "preferred_name": preferred_name,
            "lease_id": lease_id or "",
            "pid": pid or 0,
            "start_marker": start_marker or "",
            "resume_token": resume_token or "",
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server_port}/api/register",
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _fetch_role(server_port: int, agent_name: str) -> str:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server_port}/api/roles")
        with urllib.request.urlopen(req, timeout=3) as resp:
            roles = json.loads(resp.read())
        return roles.get(agent_name, "")
    except Exception:
        return ""


def _fetch_active_rules(server_port: int, token: str = "") -> dict | None:
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/rules/active", headers=headers
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _report_rule_sync(server_port: int, agent_name: str, epoch: int, token: str = "") -> None:
    try:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{server_port}/api/rules/agent_sync/{agent_name}",
            method="POST",
            data=json.dumps({"epoch": epoch}).encode(),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Heartbeat (same lease semantics as wrapper.py)
# ---------------------------------------------------------------------------

class DshHeartbeat:
    """Heartbeat sender carrying the process lease and busy/idle state."""

    LOG_INTERVAL = 30

    def __init__(self, server_port: int, get_identity, get_token,
                 lease_id: str, pid: int, start_marker: str = "", log_fn=None,
                 base: str = "dsh", label: str | None = None):
        self.server_port = server_port
        self._get_identity = get_identity
        self._get_token = get_token
        self.lease_id = lease_id
        self.pid = pid
        self.start_marker = start_marker
        self._log = log_fn or (lambda msg: print(f"  [heartbeat] {msg}", flush=True))
        self._lock = threading.Lock()
        self.consecutive_failures = 0
        self._last_failure_log = 0.0
        self.terminal = False
        self.base = base
        self.label = label
        self.failure_reason = ""

    def send(self, active: bool | None = None) -> dict:
        if self.terminal:
            return {"ok": False, "status": None, "error": "terminal: lease proof rejected"}
        name, _ = self._get_identity()
        body: dict = {
            "lease_id": self.lease_id,
            "pid": self.pid,
            "start_marker": self.start_marker,
        }
        if active is not None:
            body["active"] = bool(active)
        url = f"http://127.0.0.1:{self.server_port}/api/heartbeat/{name}"
        try:
            req = urllib.request.Request(
                url,
                method="POST",
                data=json.dumps(body).encode(),
                headers=_auth_headers(self._get_token(), include_json=True),
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            self._record_failure(f"HTTP {exc.code}")
            return {"ok": False, "status": exc.code, "error": f"HTTP {exc.code}"}
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self._record_failure(detail)
            return {"ok": False, "status": None, "error": detail}
        if data.get("name", name) != name:
            self.terminal = True
            self.failure_reason = "server changed the identity of an active DSH runtime"
            return {"ok": False, "status": 403, "error": self.failure_reason}
        self._record_success()
        return {"ok": True, "name": data.get("name", name)}

    def _record_success(self) -> None:
        with self._lock:
            if self.consecutive_failures:
                self._log(f"recovered after {self.consecutive_failures} consecutive failure(s)")
            self.consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        with self._lock:
            self.consecutive_failures += 1
            now = time.time()
            if self.consecutive_failures == 1:
                self._log(f"heartbeat failed ({detail})")
                self._last_failure_log = now
            elif now - self._last_failure_log >= self.LOG_INTERVAL:
                self._log(
                    f"heartbeat still failing ({detail}) — "
                    f"{self.consecutive_failures} consecutive failures"
                )
                self._last_failure_log = now


# ---------------------------------------------------------------------------
# Per-instance state
# ---------------------------------------------------------------------------

class DshInstance:
    """Owns one DSH runtime and its queue/session bookkeeping."""

    def __init__(self, *, name: str, token: str, queue_file: Path,
                 client: DshClient, log_fn, channel_sessions_path: Path):
        self.name = name
        self.token = token
        self.queue_file = queue_file
        self.client = client
        self.log = log_fn
        self._lock = threading.Lock()
        # One DSH session per channel/job, reused by later triggers so the
        # conversation keeps its context.
        self._sessions: dict[str, str] = {}
        self._sessions_path = channel_sessions_path
        # Persisted mapping is diagnostic only: never replay or adopt a previous
        # SDK process's interrupted session on adapter restart.
        self._save_sessions()
        # Local wait queue: triggers waiting for the single execution slot.
        self._pending_triggers: list[dict] = []
        self._busy = False
        self._busy_scope: str = ""
        self.failure_reason = ""
        self._inflight: dict | None = None

    # -- session persistence ------------------------------------------------

    def _save_sessions(self) -> None:
        try:
            self._sessions_path.parent.mkdir(parents=True, exist_ok=True)
            self._sessions_path.write_text(
                json.dumps(self._sessions, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def scope_session_id(self, scope: str) -> str:
        """Return the (possibly new) DSH session id for a channel/job scope.

        Sessions are intentionally NOT resumed across adapter restarts: the
        plan states that a restart creates a new session and recovers context
        from chat history. We therefore namespace the id with a per-process
        run marker.
        """
        with self._lock:
            session_id = self._sessions.get(scope)
            if not session_id:
                session_id = f"{scope}-{uuid.uuid4().hex[:12]}"
                self._sessions[scope] = session_id
                self._save_sessions()
                self.log(f"created session for scope '{scope}': {session_id}")
            return session_id

    # -- busy state ---------------------------------------------------------

    def set_busy(self, busy: bool, scope: str = "") -> None:
        with self._lock:
            self._busy = busy
            self._busy_scope = scope if busy else ""

    def is_busy(self) -> bool:
        """Busy when a trigger is executing or work is still queued."""
        with self._lock:
            return self._busy or bool(self._pending_triggers)

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._pending_triggers)

    def clear_pending(self) -> None:
        with self._lock:
            self._pending_triggers.clear()

    def begin_trigger(self, trigger: dict, scope: str) -> None:
        with self._lock:
            self._inflight = trigger
            self._busy = True
            self._busy_scope = scope

    def complete_trigger(self) -> None:
        with self._lock:
            self._inflight = None

    def requeue_unsubmitted(self, trigger: dict) -> None:
        with self._lock:
            self._inflight = None
            self._pending_triggers.insert(0, trigger)

    def take_unfinished(self, *, include_pending: bool) -> list[dict]:
        """Called only after the old pump has joined; never replay in-flight work."""
        with self._lock:
            records = [self._inflight] if self._inflight is not None else []
            self._inflight = None
            if include_pending:
                records.extend(self._pending_triggers)
                self._pending_triggers.clear()
            self._busy = False
            self._busy_scope = ""
            return records

    def reset_sessions(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._save_sessions()

    # -- triggers -----------------------------------------------------------

    def enqueue(self, trigger: dict) -> None:
        """Queue a trigger for the single execution slot."""
        with self._lock:
            self._pending_triggers.append(trigger)

    def dequeue(self) -> dict | None:
        with self._lock:
            if not self._pending_triggers:
                return None
            return self._pending_triggers.pop(0)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def build_trigger_prompt(
    *,
    channel: str,
    job_id: int | None,
    role: str,
    rules: list[str],
    multi_instance: bool = False,
    first_mention: bool = False,
) -> str:
    """Assemble the prompt delivered to DSH for one trigger.

    The prompt carries the target, the working rules and the role, then hands
    the loop to the agent: it must read the room and post its reply through
    the chat MCP tools. The adapter never forwards model output itself.
    """
    if job_id:
        target = f"job thread (job_id={job_id})"
    else:
        target = f"channel #{channel}"
    lines = [
        f"You have been mentioned in {target} in the agentchattr chat room.",
        "",
        f"Use the `mcp__{SERVER_NAME}__chat_read` tool to read {target}, "
        "then take appropriate action and post your reply with "
        f"`mcp__{SERVER_NAME}__chat_send`.",
        "",
        "IMPORTANT: Your answer must be published to the chat room itself via "
        f"`mcp__{SERVER_NAME}__chat_send`. Do not answer only in this session — "
        "the humans and other agents only see what you send to the room.",
    ]
    if rules:
        lines += ["", "RULES:", "; ".join(rules)]
    if role:
        lines += ["", f"ROLE: {role}"]
    if first_mention and multi_instance:
        lines += [
            "",
            "This is a multi-instance session. Reclaim your previous identity from "
            "your context window if you had one, NOT from the chat history before "
            "responding.",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Queue polling
# ---------------------------------------------------------------------------

def read_triggers(queue_file: Path) -> list[dict]:
    """Drain the queue file and return parsed trigger records."""
    try:
        if not queue_file.exists() or queue_file.stat().st_size == 0:
            return []
        drained = queue_file.with_name(queue_file.name + ".drain-" + uuid.uuid4().hex)
        # New writer opens go to a fresh queue, avoiding read-then-truncate loss.
        queue_file.replace(drained)
        lines = drained.read_text(encoding="utf-8").splitlines()
        drained.unlink()
    except Exception:
        return []
    triggers: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            triggers.append(data)
    return triggers


def trigger_scope(trigger: dict) -> str:
    """The session scope a trigger belongs to (job takes precedence)."""
    job_id = trigger.get("job_id")
    if job_id not in (None, ""):
        return f"job-{job_id}"
    channel = trigger.get("channel") or "general"
    return f"channel-{channel}"


def snapshot_pre_registration_queues(data_dir: Path, base: str) -> dict[str, bytes]:
    """Read, never move, queues before the server assigns our actual identity."""
    snapshots = {}
    for path in data_dir.glob(f"{base}*_queue.jsonl"):
        name = path.name.removesuffix("_queue.jsonl")
        if name != base and not (name.startswith(base + "-") and name[len(base) + 1:].isdigit()):
            continue
        try:
            snapshots[name] = path.read_bytes()
        except FileNotFoundError:
            pass
    return snapshots


def archive_interrupted(records: list[dict], instance_dir: Path, reason: str, log_fn) -> None:
    """Keep a private, non-replayed artifact and visible content-free diagnostics."""
    if not records:
        return
    from dsh_web_config import _secure_file
    target = instance_dir / "interrupted" / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.jsonl"
    _secure_file(target, "".join(json.dumps({"state": "interrupted/result unknown", "reason": reason,
                       "trigger": row}, ensure_ascii=False) + "\n" for row in records))
    for row in records:
        log_fn(f"中断/结果未知 interrupted/result unknown: scope={trigger_scope(row)}; {reason}; not replayed")
    log_fn(f"Preserved {len(records)} unfinished trigger(s) in private archive: {target}")


def quarantine_old_queue(queue_file: Path, snapshot: bytes, instance_dir: Path, log_fn) -> list[dict]:
    """Remove only the exact pre-registration prefix; retain new appended triggers."""
    if not snapshot:
        return []
    try:
        drained = queue_file.with_name(queue_file.name + ".startup-" + uuid.uuid4().hex)
        queue_file.replace(drained)
    except FileNotFoundError:
        return []
    raw = drained.read_bytes()
    if not raw.startswith(snapshot):
        # Never guess which messages are old after an unexpected file rewrite.
        raise DshProtocolError(f"queue changed during registration; retained without replay at {drained}")
    from dsh_web_config import _secure_file
    target = instance_dir / "interrupted" / f"startup-{time.time_ns()}.jsonl"
    _secure_file(target, snapshot.decode("utf-8", errors="replace"))
    fresh = []
    for line in raw[len(snapshot):].decode("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                fresh.append(row)
    drained.unlink()
    log_fn(f"中断/结果未知 interrupted/result unknown: pre-registration queue archived at {target}; not replayed")
    return fresh


def preserve_shutdown_work(queue_file: Path, memory_records: list[dict], *, deregistered: bool,
                           instance_dir: Path, log_fn) -> None:
    """Never drain a queue while the server may still route messages to it."""
    archive_interrupted(memory_records, instance_dir, "adapter stopped before confirmed completion", log_fn)
    if not deregistered:
        log_fn("注销未确认，磁盘队列保留，结果未知；deregistration unconfirmed; disk queue retained for review, not replayed")
        return
    staged = queue_file.with_name(queue_file.name + ".stopped-" + uuid.uuid4().hex)
    try:
        queue_file.replace(staged)
    except FileNotFoundError:
        return
    # Keep the staged original until its private archive is durable. A failed
    # archive write must not destroy the only copy of unexecuted messages.
    raw = staged.read_text(encoding="utf-8")
    if raw:
        from dsh_web_config import _secure_file
        target = instance_dir / "interrupted" / f"stopped-{time.time_ns()}.jsonl"
        _secure_file(target, raw)
        count = sum(bool(line.strip()) for line in raw.splitlines())
        log_fn(f"中断/结果未知 interrupted/result unknown: {count} queued record(s) archived at {target}; not replayed")
    staged.unlink()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    from config_loader import apply_cli_overrides, load_config

    apply_cli_overrides()
    config = load_config(ROOT)

    agent_names = list(config.get("agents", {}).keys())
    parser = argparse.ArgumentParser(description="DSH chat adapter with auto-trigger")
    parser.add_argument("agent", choices=agent_names, help=f"Agent to wrap ({', '.join(agent_names)})")
    parser.add_argument("--no-restart", action="store_true", help="Do not restart on exit")
    parser.add_argument("--label", type=str, default=None, help="Custom display label")
    parser.add_argument("--preferred-name", type=str, default=None, help="Reuse a stopped launcher-owned instance name")
    parser.add_argument("--workdir", type=str, default=None, help="Override the agent working directory")
    parser.add_argument("--data-dir", default=None, help="Override server.data_dir (path)")
    parser.add_argument("--port", default=None, help="Override server.port (int)")
    parser.add_argument("--mcp-http-port", default=None, help="Override mcp.http_port (int)")
    parser.add_argument("--mcp-sse-port", default=None, help="Override mcp.sse_port (int)")
    parser.add_argument("--upload-dir", default=None, help="Override images.upload_dir (path)")
    parser.add_argument("--stop-file", default=None, help="Gracefully stop when this caller-owned path appears")
    args, _extra = parser.parse_known_args()
    stop_file = Path(args.stop_file).expanduser().resolve() if args.stop_file else None
    if stop_file is not None and stop_file.exists():
        parser.error("--stop-file already exists; use a new caller-owned path")

    agent = args.agent
    agent_cfg = config.get("agents", {}).get(agent, {})
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    server_port = config.get("server", {}).get("port", 8300)
    mcp_cfg = config.get("mcp", {})

    def log(message: str) -> None:
        print(f"  [dsh] {message}", flush=True)

    # -- dependency check (before registering, so failures are unambiguous) --
    resolved, dep_error = _check_dependencies(agent_cfg)
    if dep_error:
        print(f"  Error: {dep_error}")
        sys.exit(1)
    dsh_entry = resolved["entry"]
    node_path = resolved["node"]

    project_dir = Path(args.workdir).expanduser() if args.workdir else Path(
        agent_cfg.get("cwd", "..")
    ).expanduser()
    if not project_dir.is_absolute():
        project_dir = ROOT / project_dir
    project_dir = project_dir.resolve()
    if not project_dir.exists():
        print(f"  Error: working directory does not exist: {project_dir}")
        sys.exit(1)

    reasoning_effort = agent_cfg.get("reasoning_effort") or ""
    max_tokens = agent_cfg.get("max_tokens")

    # -- register ------------------------------------------------------------
    old_queue_snapshots = snapshot_pre_registration_queues(data_dir, agent)
    lease_id = uuid.uuid4().hex
    wrapper_pid = os.getpid()
    try:
        from launcher_supervisor import process_start_marker

        wrapper_start_marker = process_start_marker(wrapper_pid)
    except Exception:
        wrapper_start_marker = ""

    try:
        registration = _register_instance(
            server_port, agent, args.label, args.preferred_name,
            lease_id=lease_id, pid=wrapper_pid, start_marker=wrapper_start_marker,
        )
    except Exception as exc:
        print(f"  Registration failed ({exc}).")
        print("  Wrapper cannot continue without a registered identity.")
        sys.exit(1)

    assigned_name = registration["name"]
    assigned_token = registration["token"]
    print(f"  Registered as: {assigned_name} (slot {registration.get('slot', '?')})")

    deregistered = False
    def _deregister() -> None:
        nonlocal deregistered
        if deregistered:
            return
        try:
            name = assigned_name
            req = urllib.request.Request(
                f"http://127.0.0.1:{server_port}/api/deregister/{name}",
                method="POST",
                data=b"",
                headers=_auth_headers(assigned_token),
            )
            urllib.request.urlopen(req, timeout=5)
            deregistered = True
            print(f"  Deregistered {name}")
        except Exception as exc:
            log(f"Deregistration not confirmed: {type(exc).__name__}; unfinished queue retained")

    client = None
    instance = None
    heartbeat = None
    hb_thread = None
    pump_thread = None
    startup_pending = []
    instance_dir = data_dir / "dsh" / assigned_name
    queue_file = data_dir / f"{assigned_name}_queue.jsonl"
    shutdown_event = threading.Event()
    runtime_stop = threading.Event()
    restart_requested = threading.Event()
    protocol_log = log
    try:
        startup_pending = quarantine_old_queue(queue_file, old_queue_snapshots.get(assigned_name, b""), instance_dir, log)
        # -- per-instance DSH home / profile / logs ------------------------------
        instance_dir = data_dir / "dsh" / assigned_name
        dsh_home = instance_dir / "home"
        profile_name = f"agentchattr-{assigned_name}"
        profile_dir = dsh_home / "profiles" / profile_name
        logs_dir = instance_dir / "logs"
        for path in (dsh_home, profile_dir, logs_dir):
            path.mkdir(parents=True, exist_ok=True)

        mcp_http_port = mcp_cfg.get("http_port", 8200)
        mcp_url = f"http://127.0.0.1:{mcp_http_port}/mcp"
        token_env_var = "AGENTCHATTR_MCP_TOKEN"

        write_profile_manifest(
            profile_dir,
            bundles=[
                "@deepseek-ai/dsh-base",
                "@deepseek-ai/dsh-sdk-app",
            ],
        )
        mcp_patch = write_patch_overlay(
            instance_dir / "chat-mcp.patch.yml",
            mcp_url=mcp_url,
            token_env_var=token_env_var,
            server_name=SERVER_NAME,
        )
        access_patch = write_full_access_overlay(instance_dir / "full-access.patch.yml")
        readiness_patch = write_readiness_overlay(instance_dir)
        from dsh_web_config import prepare_web_config
        web_config = prepare_web_config(agent_cfg, instance_dir, runtime=resolved)
        provider, model = web_config["provider"], web_config["model"]

        # -- runtime environment -------------------------------------------------
        env = dict(os.environ)
        env["DSH_HOME"] = str(dsh_home)
        env[token_env_var] = assigned_token
        # Base bundle derives sandbox + approval from this variable; the explicit
        # overlay above re-states it so the composed tree is self-describing.
        env["DSH_PERMISSION_MODE"] = "danger-full-access"
        # Credentials resolve from the environment; never written to the overlay.
        env.update(web_config.get("env", {}))
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        command = build_launch_command(
            node_path,
            dsh_entry,
            profile=profile_name,
            patches=[*web_config.get("patches", []), str(mcp_patch), str(access_patch),
                     str(readiness_patch)],
        )

        print(f"  === {assigned_name.capitalize()} DSH Adapter ===")
        print(f"  Runtime: {dsh_entry}")
        print(f"  Node: {node_path}")
        print(f"  DSH home: {dsh_home}")
        print(f"  Provider/model: {provider} / {model}")
        print(f"  Access: {ACCESS_MODE_LABEL} ({ACCESS_MODE_DESC})")
        print(f"  MCP: {mcp_url} (bearer token via env)")
        print(f"  Workdir: {project_dir}")

        # -- protocol diagnostics log -------------------------------------------
        protocol_log_path = logs_dir / "adapter.log"

        def protocol_log(message: str) -> None:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{stamp}] {message}"
            print(f"  {line}", flush=True)
            try:
                with open(protocol_log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass

        exit_reason: dict = {"reason": "", "failed": False}
        instance = DshInstance(
            name=assigned_name, token=assigned_token, queue_file=queue_file,
            client=None, log_fn=log, channel_sessions_path=instance_dir / "sessions.json",
        )
        for trigger in startup_pending:
            instance.enqueue(trigger)
        startup_pending = []

        def get_identity():
            return assigned_name, queue_file

        def get_token():
            return assigned_token

        heartbeat = DshHeartbeat(
            server_port=server_port, get_identity=get_identity, get_token=get_token,
            lease_id=lease_id, pid=wrapper_pid, start_marker=wrapper_start_marker,
            base=agent, label=args.label,
        )

        def on_recovered():
            # This event is generation-local. An old callback cannot stop a
            # replacement client or accidentally signal global shutdown.
            restart_requested.set()
            runtime_stop.set()

        def on_notification(notification: DshNotification) -> None:
            if notification.method == "session.status":
                log(f"session {notification.payload.get('sessionId')} -> {notification.payload.get('status')}")
            elif notification.method == "subagent.started":
                log(f"subagent started: {notification.payload.get('childSessionId')}")
            # Prompt/assistant/tool content is deliberately absent from logs.

        generation = 0
        while not shutdown_event.is_set():
            generation += 1
            runtime_stop = threading.Event()
            restart_requested.clear()

            def on_exit(returncode: int | None, stopping=runtime_stop) -> None:
                if not stopping.is_set() and not shutdown_event.is_set():
                    exit_reason["reason"] = f"DSH runtime exited unexpectedly with code {returncode}"
                    exit_reason["failed"] = True
                    shutdown_event.set()

            client = DshClient(command, cwd=str(project_dir), env=env,
                               on_notification=on_notification, on_exit=on_exit,
                               log_fn=protocol_log)
            instance.client = client
            try:
                client.start()
                client.initialize(cwd=str(project_dir), provider=provider, model=model,
                                  reasoning_effort=reasoning_effort or None,
                                  max_tokens=int(max_tokens) if max_tokens else None)
                ready_tools = client.wait_mcp_ready(timeout=30.0)
            except Exception as exc:
                instance.failure_reason = f"SDK initialization failed: {type(exc).__name__}: {exc}"
                protocol_log("ERROR: " + instance.failure_reason)
                shutdown_event.set()
                break
            protocol_log("SDK ready; verified chat tools in runtime registry: " + ", ".join(sorted(ready_tools)))
            if generation > 1:
                protocol_log("SDK runtime reconnected; MCP registry verified; new sessions ready")
            if hb_thread is None:
                hb_thread = threading.Thread(target=_heartbeat_loop,
                    args=(heartbeat, shutdown_event, instance, server_port, get_identity, get_token),
                    kwargs={"on_recovered": on_recovered}, daemon=True)
                hb_thread.start()
            pump_thread = threading.Thread(target=_trigger_pump,
                args=(instance, client, heartbeat, server_port, get_identity, get_token,
                      runtime_stop, protocol_log, registration), daemon=True)
            pump_thread.start()
            try:
                while not shutdown_event.is_set() and not restart_requested.is_set():
                    if stop_file is not None and stop_file.exists():
                        exit_reason["reason"] = "caller requested graceful stop"
                        shutdown_event.set()
                        break
                    if runtime_stop.is_set():
                        # A pump fault is fatal unless the heartbeat explicitly
                        # requested a new runtime after recovering the same lease.
                        shutdown_event.set()
                        break
                    shutdown_event.wait(0.2)
            except KeyboardInterrupt:
                exit_reason["reason"] = "interrupted by user"
                shutdown_event.set()

            runtime_stop.set()
            if shutdown_event.is_set():
                heartbeat.terminal = True
                hb_thread.join(timeout=7.0)
                _deregister()  # stop routing before collecting unfinished work
            else:
                protocol_log("SDK runtime reconnecting after server lease recovery; in-flight work will not be replayed")
            client.shutdown(timeout=10.0)
            client.close()
            pump_thread.join(timeout=7.0)
            if pump_thread.is_alive():
                instance.failure_reason = "old SDK pump did not stop; refusing overlapping runtime generations"
                shutdown_event.set()
                break
            if not shutdown_event.is_set():
                archive_interrupted(instance.take_unfinished(include_pending=False), instance_dir,
                                    "server restarted; old SDK runtime stopped", protocol_log)
                instance.reset_sessions()
                instance.failure_reason = ""

        protocol_log("DSH adapter stopping: " + (exit_reason.get("reason") or instance.failure_reason
                                                or heartbeat.failure_reason or "requested"))
        if heartbeat.failure_reason or instance.failure_reason or exit_reason.get("failed"):
            sys.exit(1)
    finally:
        # No heartbeat may re-register after this point. Deregister first, then
        # preserve every queued/in-flight trigger rather than silently clearing.
        shutdown_event.set()
        runtime_stop.set()
        if heartbeat is not None:
            heartbeat.terminal = True
        if hb_thread is not None:
            hb_thread.join(timeout=7.0)
        _deregister()
        if client is not None:
            client.shutdown(timeout=10.0)
            client.close()
        if pump_thread is not None:
            pump_thread.join(timeout=7.0)
        unfinished = list(startup_pending)
        if instance is not None:
            unfinished.extend(instance.take_unfinished(include_pending=True))
        preserve_shutdown_work(queue_file, unfinished, deregistered=deregistered,
                               instance_dir=instance_dir, log_fn=protocol_log)
        protocol_log("DSH adapter stopped; unconfirmed work requires review and will not be replayed")
        if not deregistered:
            sys.exit(1)


def _heartbeat_loop(heartbeat: DshHeartbeat, shutdown: threading.Event,
                    instance: DshInstance, server_port: int,
                    get_identity, get_token, interval: float = 5.0,
                    on_recovered=None) -> None:
    """Report presence and busy/idle state until shutdown."""
    while not shutdown.is_set():
        if heartbeat.terminal:
            shutdown.set()
            return
        active = instance.is_busy() or bool(instance.client.running_sessions())
        result = heartbeat.send(active=active)
        if shutdown.is_set():
            return
        if result.get("status") in (401, 403):
            heartbeat.terminal = True
            heartbeat.failure_reason = "server rejected the DSH identity/lease; stopping runtime"
            print(f"  [heartbeat] ERROR: {heartbeat.failure_reason}", flush=True)
            shutdown.set()
            return
        if result.get("status") == 409:
            # Server restarted or our lease was dropped.
            try:
                replacement = _register_instance(
                    server_port, heartbeat.base, heartbeat.label, get_identity()[0],
                    lease_id=heartbeat.lease_id, pid=heartbeat.pid,
                    start_marker=heartbeat.start_marker,
                    resume_token=get_token(),
                )
                if (replacement.get("name") != get_identity()[0]
                        or replacement.get("token") != get_token()):
                    heartbeat.terminal = True
                    heartbeat.failure_reason = "re-registration changed identity/token; existing MCP authorization cannot be rebound"
                    print(f"  [heartbeat] ERROR: {heartbeat.failure_reason}", flush=True)
                    shutdown.set()
                    return
                heartbeat._record_success()
                print("  [heartbeat] recovered original identity, lease and MCP token", flush=True)
                if on_recovered is not None and not shutdown.is_set():
                    on_recovered()
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403, 409):
                    heartbeat.terminal = True
                    heartbeat.failure_reason = f"lease recovery rejected (HTTP {exc.code})"
                    print(f"  [heartbeat] ERROR: {heartbeat.failure_reason}", flush=True)
                    shutdown.set()
                    return
                print(f"  [heartbeat] lease recovery temporarily failed (HTTP {exc.code})", flush=True)
            except Exception as exc:
                print(f"  [heartbeat] re-registration failed ({type(exc).__name__}: {exc})")
        shutdown.wait(interval)


def _trigger_pump(instance: DshInstance, client: DshClient, heartbeat: DshHeartbeat,
                  server_port: int, get_identity, get_token,
                  shutdown: threading.Event, protocol_log,
                  registration: dict) -> None:
    """Poll the trigger queue; execute one trigger at a time."""
    multi_instance = int(registration.get("slot", 1) or 1) > 1
    first_mention = True
    last_rules_epoch = 0
    trigger_count = 0

    while not shutdown.is_set():
        try:
            if not client.is_running():
                instance.failure_reason = "SDK runtime stopped while accepting triggers"
                shutdown.set()
                return

            # Drain new triggers into the local wait queue in arrival order.
            for trigger in read_triggers(instance.queue_file):
                instance.enqueue(trigger)

            trigger = instance.dequeue()
            if trigger is None:
                shutdown.wait(1.0)
                continue

            channel = trigger.get("channel") or "general"
            job_id = trigger.get("job_id")
            scope = trigger_scope(trigger)
            custom_prompt = trigger.get("prompt", "")
            if not isinstance(custom_prompt, str):
                custom_prompt = ""

            instance.begin_trigger(trigger, scope)
            try:
                # Rules are refreshed on first trigger, epoch change, or the
                # server-configured cadence — same policy as the CLI wrapper.
                token = get_token()
                rules_data = _fetch_active_rules(server_port, token)
                rules: list[str] = []
                trigger_count += 1
                if rules_data:
                    refresh_interval = rules_data.get("refresh_interval", 10)
                    need_inject = (
                        last_rules_epoch == 0
                        or rules_data.get("epoch") != last_rules_epoch
                        or (refresh_interval and trigger_count % refresh_interval == 0)
                    )
                    if need_inject:
                        rules = list(rules_data.get("rules") or [])
                        last_rules_epoch = rules_data.get("epoch", 0)
                        current_name, _ = get_identity()
                        _report_rule_sync(server_port, current_name, last_rules_epoch, token)

                role = _fetch_role(server_port, instance.name)

                if custom_prompt:
                    prompt = custom_prompt
                else:
                    prompt = build_trigger_prompt(
                        channel=channel,
                        job_id=job_id,
                        role=role,
                        rules=rules,
                        multi_instance=multi_instance,
                        first_mention=first_mention,
                    )
                if first_mention:
                    first_mention = False

                session_id = instance.scope_session_id(scope)
                if shutdown.is_set():
                    instance.requeue_unsubmitted(trigger)
                    return
                protocol_log(f"trigger -> scope={scope} session={session_id}")
                client.session_prompt(
                    session_id,
                    normalize_prompt_blocks(prompt),
                    timeout=60.0,
                )
                instance.log(f"delivered trigger for {scope} (waiting for SDK completion)")

                # Wait for the runtime to go idle again so the next trigger does
                # not interleave context. Bounded so a stuck turn cannot wedge
                # the pump forever.
                _await_idle(client, session_id, shutdown, max_wait=1800.0)
                instance.complete_trigger()
                protocol_log(f"completed trigger for {scope} (session/wait confirmed)")
            except DshRpcError as exc:
                if shutdown.is_set():
                    return
                instance.failure_reason = f"SDK prompt/wait rejected for {scope}: {exc.message}"
                protocol_log(f"prompt rejected for {scope}: code {exc.code} {exc.message}")
                instance.log(f"ERROR: DSH rejected the prompt/wait for {scope}: {exc.message}")
                shutdown.set()
                return
            except DshTransportClosed as exc:
                if shutdown.is_set():
                    return
                instance.failure_reason = f"SDK transport closed during {scope}: {exc}"
                protocol_log(f"transport closed during {scope}: {exc}")
                print(f"  ERROR: DSH runtime connection lost: {exc}")
                shutdown.set()
                return
            except Exception as exc:
                if shutdown.is_set():
                    return
                instance.failure_reason = f"trigger failed for {scope}: {exc}"
                protocol_log(f"trigger failed for {scope}: {type(exc).__name__}: {exc}")
                instance.log(f"ERROR: trigger for {scope} failed; result unknown: {exc}")
                shutdown.set()
                return
            finally:
                instance.set_busy(False)
        except Exception as exc:  # noqa: BLE001 - pump must never die
            protocol_log(f"pump loop error: {type(exc).__name__}: {exc}")

        shutdown.wait(1.0)


def _await_idle(client: DshClient, session_id: str, shutdown: threading.Event,
                max_wait: float) -> None:
    """Use the authoritative SDK wait, including progressing managed children."""
    if shutdown.is_set():
        raise DshTransportClosed("adapter stopping")
    client.session_wait(session_id, timeout=max_wait)


def _shutdown_and_close(client: DshClient, shutdown: threading.Event,
                        heartbeat: DshHeartbeat) -> None:
    """Graceful stop: stop accepting work, ask the runtime to close, escalate."""
    shutdown.set()
    heartbeat.terminal = True
    try:
        client.shutdown(timeout=10.0)
    except Exception:
        pass
    client.close(terminate_grace=2.0)


if __name__ == "__main__":
    main()
