"""agentchattr — FastAPI web UI + agent auto-trigger."""

import asyncio
import json
import re as _re
import sys
import threading
import uuid
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from store import MessageStore
from rules import RuleStore
from summaries import SummaryStore
from jobs import JobStore
from schedules import ScheduleStore, parse_schedule_spec
from router import Router
from agents import AgentTrigger
from registry import RuntimeRegistry
from session_store import SessionStore, validate_session_template
from session_engine import SessionEngine

log = logging.getLogger(__name__)

app = FastAPI(title="agentchattr")

# --- globals (set by configure()) ---
store: MessageStore | None = None
rules: RuleStore | None = None
summaries: SummaryStore | None = None
jobs: JobStore | None = None
schedules: ScheduleStore | None = None
router: Router | None = None
agents: AgentTrigger | None = None
registry: RuntimeRegistry | None = None
session_store: SessionStore | None = None
session_engine: SessionEngine | None = None
config: dict = {}
ws_clients: set[WebSocket] = set()

# --- Security: session token (set by configure()) ---
session_token: str = ""

# Room settings (persisted to data/settings.json)
room_settings: dict = {
    "title": "agentchattr",
    "username": "user",
    "font": "sans",
    "channels": ["general"],
    "history_limit": "all",
    "contrast": "normal",
}

# Channel validation
_CHANNEL_NAME_RE = _re.compile(r'^[a-z0-9][a-z0-9\-]{0,19}$')
MAX_CHANNELS = 8

# Agent hats (persisted to data/hats.json)
agent_hats: dict[str, str] = {}  # { agent_name: svg_string }


def _hats_path() -> Path:
    data_dir = config.get("server", {}).get("data_dir", "./data")
    return Path(data_dir) / "hats.json"


def _load_hats():
    global agent_hats
    p = _hats_path()
    if p.exists():
        try:
            agent_hats = json.loads(p.read_text("utf-8"))
        except Exception:
            agent_hats = {}


def _save_hats():
    p = _hats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(agent_hats), "utf-8")


def _sanitize_svg(svg: str) -> str:
    """Strip dangerous content from SVG string."""
    svg = _re.sub(r'<script[^>]*>.*?</script>', '', svg, flags=_re.DOTALL | _re.IGNORECASE)
    svg = _re.sub(r'\bon\w+\s*=', '', svg, flags=_re.IGNORECASE)
    svg = _re.sub(r'javascript\s*:', '', svg, flags=_re.IGNORECASE)
    return svg


def set_agent_hat(agent: str, svg: str) -> str | None:
    """Validate, sanitize, and store a hat SVG. Returns error string or None."""
    svg = svg.strip()
    if not svg.lower().startswith("<svg"):
        return "Hat must be an SVG element (starts with <svg)."
    if len(svg) > 5120:
        return "Hat SVG too large (max 5KB)."
    svg = _sanitize_svg(svg)
    agent_hats[agent.lower()] = svg
    _save_hats()
    if _event_loop:
        asyncio.run_coroutine_threadsafe(broadcast_hats(), _event_loop)
    return None


def clear_agent_hat(agent: str):
    """Remove an agent's hat."""
    key = agent.lower()
    if key in agent_hats:
        del agent_hats[key]
        _save_hats()
        if _event_loop:
            asyncio.run_coroutine_threadsafe(broadcast_hats(), _event_loop)


def _settings_path() -> Path:
    data_dir = config.get("server", {}).get("data_dir", "./data")
    return Path(data_dir) / "settings.json"


def _load_settings():
    global room_settings
    p = _settings_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text("utf-8"))
            room_settings.update(saved)
        except Exception:
            pass
    # Ensure "general" always exists and is first
    if "channels" not in room_settings or not room_settings["channels"]:
        room_settings["channels"] = ["general"]
    elif "general" not in room_settings["channels"]:
        room_settings["channels"].insert(0, "general")


def _save_settings():
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(room_settings, indent=2), "utf-8")


def _extract_agent_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-agent-token", "").strip()


def _resolve_authenticated_agent(request: Request) -> dict | None:
    if not registry:
        return None
    token = _extract_agent_token(request)
    if not token:
        return None
    return registry.resolve_token(token)


# --- Security middleware ---
# Paths that don't require the session token (public assets).
_PUBLIC_PREFIXES = ("/", "/static/")


def _install_security_middleware(token: str, cfg: dict):
    """Add token validation and origin checking middleware to the app."""
    import app as _self
    _self.session_token = token
    port = cfg.get("server", {}).get("port", 8300)
    allowed_origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    }

    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path

            # Static assets, index page, and uploaded images are public.
            # The index page injects the token client-side via same-origin script.
            # Uploads use random filenames and have path-traversal protection.
            if path == "/" or path.startswith(("/static/", "/uploads/", "/api/roles")):
                return await call_next(request)

            # Agent registration/heartbeat: loopback only (no remote agent minting).
            if path.startswith(("/api/register", "/api/deregister/", "/api/heartbeat/")):
                client_ip = request.client.host if request.client else ""
                if client_ip not in ("127.0.0.1", "::1", "localhost"):
                    return JSONResponse(
                        {"error": f"forbidden: agent registration is restricted to local loopback. Source {client_ip} is not allowed."},
                        status_code=403,
                    )
                return await call_next(request)

            # --- Origin check (blocks cross-origin / DNS-rebinding attacks) ---
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": "forbidden: origin not allowed"},
                    status_code=403,
                )

            # --- Token check ---
            # Allow registered agents to authenticate via Bearer token
            # for /api/messages and /api/send (no browser session needed).
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer ") and (path in ("/api/messages", "/api/send") or path.startswith("/api/rules/")):
                bearer = auth_header[7:].strip()
                if _self.registry and _self.registry.resolve_token(bearer):
                    return await call_next(request)

            req_token = (
                request.headers.get("x-session-token")
                or request.query_params.get("token")
            )
            if req_token != _self.session_token:
                return JSONResponse(
                    {"error": "forbidden: invalid or missing session token"},
                    status_code=403,
                )

            return await call_next(request)

    app.add_middleware(SecurityMiddleware)


def configure(cfg: dict, session_token: str = ""):
    global store, rules, summaries, jobs, schedules, router, agents, registry, session_store, session_engine, config
    config = cfg
    # --- Security: store the session token and install middleware ---
    _install_security_middleware(session_token, cfg)

    data_dir = cfg.get("server", {}).get("data_dir", "./data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    log_path = Path(data_dir) / "agentchattr_log.jsonl"
    legacy_log_path = Path(data_dir) / "room_log.jsonl"
    if not log_path.exists() and legacy_log_path.exists():
        # Backward compatibility for existing installs.
        log_path = legacy_log_path

    store = MessageStore(str(log_path))
    # Initialize store upload dir from config
    raw_upload_dir = cfg.get("images", {}).get("upload_dir", "./uploads")
    store.upload_dir = Path(raw_upload_dir)
    
    # Rules store — migrates from legacy decisions.json automatically
    rules_path = Path(data_dir) / "rules.json"
    legacy_decisions = Path(data_dir) / "decisions.json"
    if not rules_path.exists() and legacy_decisions.exists():
        legacy_decisions.rename(rules_path)
    rules = RuleStore(str(rules_path))
    rules.on_change(_on_rule_change)

    summaries = SummaryStore(str(Path(data_dir) / "summaries.json"))

    # Migrate legacy activities.json → jobs.json
    jobs_path = Path(data_dir) / "jobs.json"
    legacy_activities = Path(data_dir) / "activities.json"
    if not jobs_path.exists() and legacy_activities.exists():
        legacy_activities.rename(jobs_path)

    jobs = JobStore(str(jobs_path))
    jobs.on_change(_on_job_change)

    schedules = ScheduleStore(str(Path(data_dir) / "schedules.json"))
    schedules.on_change(_on_schedule_change)

    max_hops = cfg.get("routing", {}).get("max_agent_hops", 4)

    # Registry: single source of truth for all live agent state
    registry = RuntimeRegistry(data_dir=data_dir)
    registry.seed(cfg.get("agents", {}))
    registry.on_change(_on_registry_change)

    # Router starts with base agent names (backward compat for direct MCP users),
    # registry.on_change updates it dynamically when instances register/deregister
    agent_names = list(cfg.get("agents", {}).keys())
    router = Router(
        agent_names=agent_names,
        default_mention=cfg.get("routing", {}).get("default", "none"),
        max_hops=max_hops,
    )
    agents = AgentTrigger(registry, data_dir=data_dir)

    # Sessions
    ROOT = Path(__file__).parent
    session_store = SessionStore(
        str(Path(data_dir) / "session_runs.json"),
        templates_dir=str(ROOT / "session_templates"),
    )
    session_engine = SessionEngine(session_store, store, agents, registry)
    session_store.on_change(_on_session_change)

    # Bridge: when ANY message is added to store (including via MCP),
    # broadcast to all WebSocket clients
    store.on_message(_on_store_message)

    _load_settings()
    _load_hats()

    # Apply saved loop guard setting
    if "max_agent_hops" in room_settings:
        router.max_hops = room_settings["max_agent_hops"]

    # Background thread: check for wrapper recovery flag files
    _data_dir = Path(data_dir)

    _known_online: set[str] = set()  # agents we've seen join — track for leave messages
    _posted_leave: set[str] = set()  # agents we've already posted a leave for — debounce

    _known_active = set()

    def _background_checks():
        import time as _time
        import mcp_bridge

        while True:
            _time.sleep(3)
            # Recovery flags
            try:
                for flag in _data_dir.glob("*_recovered"):
                    agent_name = flag.read_text("utf-8").strip()
                    flag.unlink()
                    store.add(
                        "system",
                        f"Agent routing for {agent_name} interrupted — auto-recovered. "
                        "If agents aren't responding, try sending your message again."
                    )
            except Exception:
                pass

            # Pending instances (slot 2+) wait for human naming or agent claim.
            # No auto-confirm — identity must be explicitly resolved.

            # Presence expiry — post leave messages (but do NOT deregister).
            # Deregistration only happens via /api/deregister (wrapper shutdown)
            # OR the 60s crash timeout below.
            # Short timeout (10s) prevents slot theft when MCP tool calls are intermittent.
            try:
                now = _time.time()
                with mcp_bridge._presence_lock:
                    currently_online = {
                        name for name, ts in mcp_bridge._presence.items()
                        if now - ts < mcp_bridge.PRESENCE_TIMEOUT
                    }
                    currently_active = set()
                    for name, active in mcp_bridge._activity.items():
                        if active:
                            if now - mcp_bridge._activity_ts.get(name, 0) < mcp_bridge.ACTIVITY_TIMEOUT:
                                currently_active.add(name)
                            else:
                                mcp_bridge._activity[name] = False  # auto-expire

                # Crash timeout: if a wrapper hasn't heartbeated for 60s,
                # it's dead — deregister it to free the slot.
                _CRASH_TIMEOUT = 60
                registered = set(registry.get_all_names())
                for name in registered:
                    with mcp_bridge._presence_lock:
                        last_seen = mcp_bridge._presence.get(name, 0)
                    if last_seen > 0 and now - last_seen > _CRASH_TIMEOUT:
                        log.info(f"Crash timeout: deregistering {name} (no heartbeat for {_CRASH_TIMEOUT}s)")
                        result = registry.deregister(name)
                        if result:
                            mcp_bridge.purge_identity(name)
                            registry.clean_renames_for(name)
                            renamed = result.get("_renamed_back")
                            if renamed:
                                mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
                                store.rename_sender(renamed["old"], renamed["new"])
                                if _event_loop:
                                    rename_event = json.dumps({
                                        "type": "agent_renamed",
                                        "old_name": renamed["old"],
                                        "new_name": renamed["new"],
                                    })
                                    asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
                            channels = room_settings.get("channels", ["general"])
                            for ch in channels:
                                store.add(name, f"{name} disconnected (timeout)", msg_type="leave", channel=ch)
                            _posted_leave.add(name)

                # Re-fetch registered names (may have changed from crash timeout above)
                registered = set(registry.get_all_names())

                # Detect registered instances going offline (leave message only)
                timed_out = registered - currently_online
                for name in timed_out:
                    inst = registry.get_instance(name)
                    if not inst:
                        continue
                    # Skip names that were just renamed (not actually offline)
                    with mcp_bridge._presence_lock:
                        was_renamed = name in mcp_bridge._renamed_from
                        if was_renamed:
                            mcp_bridge._renamed_from.discard(name)
                    if was_renamed:
                        continue
                    # Post leave message ONCE per offline transition (debounced)
                    if name not in _posted_leave:
                        _posted_leave.add(name)
                        channels = room_settings.get("channels", ["general"])
                        for ch in channels:
                            store.add(name, f"{name} disconnected", msg_type="leave", channel=ch)

                # Clear leave debounce for agents that came back online
                _posted_leave -= currently_online

                # Detect other agents (non-registered) going offline
                went_offline = (_known_online - currently_online) - timed_out
                for name in went_offline:
                    # Skip leave messages for names that were just renamed
                    with mcp_bridge._presence_lock:
                        was_renamed = name in mcp_bridge._renamed_from
                        if was_renamed:
                            mcp_bridge._renamed_from.discard(name)
                    if was_renamed:
                        continue
                    if not registry.is_registered(name) and name not in _posted_leave:
                        _posted_leave.add(name)
                        channels = room_settings.get("channels", ["general"])
                        for ch in channels:
                            store.add(name, f"{name} disconnected", msg_type="leave", channel=ch)

                if _known_online != currently_online and _event_loop:
                    asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)

                # Clear stale activity for agents that went offline
                with mcp_bridge._presence_lock:
                    stale_active = [n for n in mcp_bridge._activity
                                    if mcp_bridge._activity.get(n) and n not in currently_online]
                    for n in stale_active:
                        mcp_bridge._activity[n] = False
                    if stale_active:
                        currently_active -= set(stale_active)

                # Broadcast status on any change (online set or activity set)
                if currently_active != _known_active or _known_online != currently_online:
                    _known_active.clear()
                    _known_active.update(currently_active)
                    if _event_loop:
                        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)
                _known_online.clear()
                _known_online.update(currently_online)
            except Exception:
                pass

    threading.Thread(target=_background_checks, daemon=True).start()

    # --- Schedule runner: fires due scheduled prompts every 30s ---
    def _schedule_runner():
        import time as _time
        while True:
            _time.sleep(30)
            try:
                if not schedules:
                    continue
                due = schedules.run_due()
                for s in due:
                    prompt = s.get("prompt", "")
                    targets = s.get("targets", [])
                    channel = s.get("channel", "general")
                    if not prompt or not targets:
                        schedules.mark_run(s["id"])
                        continue
                    sender = s.get("created_by", "user")
                    mention_str = " ".join(f"@{t}" for t in targets)
                    full_text = f"{mention_str} {prompt}" if mention_str else prompt
                    # store.add triggers _handle_new_message via callback,
                    # which routes @mentions to agents — no manual trigger needed.
                    store.add(
                        sender,
                        full_text,
                        channel=channel,
                    )
                    if s.get("one_shot"):
                        schedules.delete(s["id"])
                    else:
                        schedules.mark_run(s["id"])
            except Exception:
                log.exception("schedule runner error")

    threading.Thread(target=_schedule_runner, daemon=True).start()


# --- Store → WebSocket bridge ---

_event_loop = None  # set by run.py after starting the event loop


def set_event_loop(loop):
    global _event_loop
    _event_loop = loop


def _on_store_message(msg: dict):
    """Called from any thread when a message is added to the store."""
    if _event_loop is None:
        return
    try:
        # If called from the event loop thread (e.g. WebSocket handler),
        # schedule directly as a task
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(_handle_new_message(msg))
            return
    except RuntimeError:
        pass  # No running loop — we're in a different thread (MCP)
    asyncio.run_coroutine_threadsafe(_handle_new_message(msg), _event_loop)


def _on_rule_change(action: str, rule: dict):
    """Called from any thread when a rule changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_rule(action, rule))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_rule(action, rule), _event_loop)


def _on_job_change(action: str, data: dict):
    """Called from any thread when a job changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_job(action, data))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_job(action, data), _event_loop)


def _on_schedule_change(action: str, schedule: dict):
    """Called from any thread when a schedule changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_schedule(action, schedule))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_schedule(action, schedule), _event_loop)


def _on_session_change(action: str, session: dict):
    """Called from any thread when a session changes."""
    if _event_loop is None:
        return
    # Enrich with computed fields so the frontend gets phase_name, current_agent, etc.
    if session_engine:
        session = session_engine._enrich(dict(session))

    # Add completion/interruption banners to chat timeline
    if action == "complete" and store:
        output_id = session.get("output_message_id")
        # Tag the output message so it renders highlighted on reload
        if output_id:
            msg = store.get_by_id(output_id)
            if msg:
                meta = msg.get("metadata") or {}
                meta["session_output"] = True
                store.update_message(output_id, {"metadata": meta})
        store.add(
            sender="system",
            text=f"Session complete: {session.get('template_name', '?')}",
            msg_type="session_end",
            channel=session.get("channel", "general"),
            metadata={"session_id": session.get("id"), "output_message_id": output_id},
        )
    elif action == "interrupt" and store:
        reason = session.get("interrupt_reason", "interrupted")
        store.add(
            sender="system",
            text=f"Session ended: {session.get('template_name', '?')} ({reason})",
            msg_type="session_end",
            channel=session.get("channel", "general"),
            metadata={"session_id": session.get("id"), "reason": reason},
        )

    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_session(action, session))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_session(action, session), _event_loop)


_draft_ref_re = _re.compile(r'\[([a-f0-9]{8})\]')

def _resolve_draft_lineage(text: str, channel: str) -> tuple[str, int]:
    """Check if a session draft block is a revision of an existing draft.

    Looks at the agent's own message text for a [draft_id] reference, and also
    scans recent channel messages for "revise session draft [XXXX]" requests.
    Returns (draft_id, revision). New drafts get a fresh id and revision=1.
    """
    # Check the message text itself for a draft_id reference
    ref_match = _draft_ref_re.search(text)
    ref_id = ref_match.group(1) if ref_match else None

    if not ref_id:
        # Also check recent messages for a "revise session draft [XXXX]" request
        recent = store.get_recent(count=20, channel=channel)
        for m in reversed(recent):
            m_text = m.get("text", "")
            if "revise session draft" in m_text.lower():
                ref_match = _draft_ref_re.search(m_text)
                if ref_match:
                    ref_id = ref_match.group(1)
                    break

    if ref_id:
        # Find the highest revision for this draft_id in existing messages
        max_rev = 0
        recent = store.get_recent(count=100, channel=channel)
        for m in recent:
            meta = m.get("metadata") or {}
            if meta.get("draft_id") == ref_id:
                max_rev = max(max_rev, meta.get("revision", 1))
        if max_rev > 0:
            return ref_id, max_rev + 1

    return str(uuid.uuid4())[:8], 1


async def _handle_new_message(msg: dict):
    """Broadcast message to web clients + check for @mention triggers."""
    # For broadcast slash commands, suppress the raw message — only the expanded
    # version should appear. Delete from store if it was persisted (MCP path),
    # and skip broadcasting the raw text.
    text = msg.get("text", "")
    msg_type = msg.get("type", "chat")
    sender = msg.get("sender", "")
    channel = msg.get("channel", "general")
    # Strip @mentions to find the slash command (e.g. "@claude @codex /hatmaking")
    stripped = _re.sub(r"@[\w-]+\s*", "", text).strip().lower()
    _broadcast_cmds = ("/hatmaking", "/artchallenge", "/roastreview", "/poetry")
    cmd_word = stripped.split()[0] if stripped else ""
    is_broadcast_cmd = cmd_word in _broadcast_cmds
    known_agents = set(registry.get_all_names()) if registry else set()
    known_agents.update(config.get("agents", {}).keys())
    _session_draft_re = _re.compile(r'```session\s*\n(.*?)\n```', _re.DOTALL)
    draft_match = _session_draft_re.search(text)
    is_agent_session_draft = bool(draft_match and sender in known_agents)
    is_hidden_session_request = msg_type == "session_request"

    is_agent_continue = (stripped == "/continue" and sender in known_agents)
    suppress_broadcast = (
        is_broadcast_cmd
        or is_hidden_session_request
        or is_agent_session_draft
        or is_agent_continue
    )

    if not suppress_broadcast:
        await broadcast(msg)

    # If the raw slash command was persisted (MCP path), silently remove it.
    # It was never broadcast to WebSocket clients, so no delete event needed.
    if suppress_broadcast and msg.get("id"):
        store.delete([msg["id"]])

    # System messages never trigger routing - prevents infinite callback loops
    if sender == "system":
        return

    # Check for slash commands — use stripped text (sans @mentions)
    if stripped == "/continue":
        if sender in known_agents:
            store.add("system", f"Loop guard: only humans can /continue. {sender} tried to self-resume.", channel=channel)
            return
        router.continue_routing(channel)
        store.add("system", f"Routing resumed by {sender}.", channel=channel)
        await broadcast_status()
        return

    if stripped == "/roastreview":
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        store.add(sender, f"{mentions} Time for a roast review! Inspect each other's work and constructively roast it.", channel=channel)
        return

    if stripped.startswith("/artchallenge"):
        parts = stripped.split(None, 1)
        theme = parts[1] if len(parts) > 1 else "anything you like"
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        store.add(
            sender,
            f"{mentions} Art challenge! Create an SVG artwork with the theme: **{theme}**. "
            "Write your SVG code to a .svg file, then attach it using chat_send(image_path=...). "
            "Make it creative, keep it under 5KB. Let's see what you've got!",
            channel=channel,
        )
        return

    if stripped == "/hatmaking":
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        all_instances = registry.get_all() if registry else {}
        agents_cfg = config.get("agents", {})
        color_parts = ", ".join(
            f"{a}={all_instances[a]['color']}" if a in all_instances
            else f"{a}={agents_cfg.get(a, {}).get('color', '#888')}"
            for a in agent_names
        )
        store.add(
            sender,
            f"{mentions} Hat making time! Design a new hat for your avatar using SVG. "
            "Use viewBox=\"0 0 32 16\" so it fits on top of a 32px avatar circle. "
            f"Background is dark (#0f0f17). Avatar colors: {color_parts}. Design for good contrast! "
            "Call chat_set_hat(sender=your_name, svg='<svg ...>...</svg>') to wear it. "
            "Be creative — top hats, party hats, crowns, propeller beanies, whatever you want!",
            channel=channel,
        )
        return

    if stripped.startswith("/poetry"):
        parts = stripped.split(None, 1)
        form = parts[1] if len(parts) > 1 else "haiku"
        if form not in ("haiku", "limerick", "sonnet"):
            form = "haiku"
        agent_names = registry.get_all_names() if registry else list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        prompts = {
            "haiku": "Write a haiku about the current state of this codebase.",
            "limerick": "Write a limerick about the current state of this codebase.",
            "sonnet": "Write a sonnet about the current state of this codebase.",
        }
        store.add(sender, f"{mentions} {prompts[form]}", channel=channel)
        return

    # Detect session draft blocks from agents only.
    # The session request prompt contains an example ```session block,
    # so treating every non-system sender as a draft source creates a false
    # invalid-draft card the moment the user asks for a custom session.
    _session_draft_re = _re.compile(r'```session\s*\n(.*?)\n```', _re.DOTALL)
    draft_match = _session_draft_re.search(text)
    known_agents = set(registry.get_all_names()) if registry else set()
    known_agents.update(config.get("agents", {}).keys())
    if draft_match and sender in known_agents:
        # Check if this is a revision of an existing draft
        draft_id, revision = _resolve_draft_lineage(text, channel)

        try:
            draft_json = json.loads(draft_match.group(1))
            errors = validate_session_template(draft_json)
            if errors:
                store.add(
                    "system",
                    f"Session draft from {sender} has errors:\n" + "\n".join(f"- {e}" for e in errors),
                    msg_type="session_draft",
                    channel=channel,
                    metadata={"draft_id": draft_id, "revision": revision, "proposed_by": sender,
                              "template": draft_json, "errors": errors, "valid": False},
                )
            else:
                draft_json.setdefault("id", f"draft-{draft_id}")
                store.add(
                    "system",
                    f"Session draft from {sender}: **{draft_json.get('name', '?')}**",
                    msg_type="session_draft",
                    channel=channel,
                    metadata={"draft_id": draft_id, "revision": revision, "proposed_by": sender,
                              "template": draft_json, "errors": [], "valid": True},
                )
        except json.JSONDecodeError:
            store.add(
                "system",
                f"Session draft from {sender} contains invalid JSON.",
                msg_type="session_draft",
                channel=channel,
                metadata={"draft_id": draft_id, "revision": revision, "proposed_by": sender,
                           "errors": ["Invalid JSON in session block"], "valid": False},
            )

    raw_targets = router.get_targets(sender, text, channel)
    # Resolve base family names to actual registered instances
    # e.g. 'claude' → 'claude-prime' when slot-1 was renamed
    targets = []
    for t in raw_targets:
        if registry:
            targets.extend(registry.resolve_to_instances(t))
        else:
            targets.append(t)
    targets = list(dict.fromkeys(targets))  # dedupe, preserve order

    if router.is_paused(channel):
        # Only emit the loop guard notice once per pause
        if not router.is_guard_emitted(channel):
            router.set_guard_emitted(channel)
            store.add(
                "system",
                f"Loop guard: {router.max_hops} agent-to-agent hops reached. "
                "Type /continue to resume.",
                channel=channel
            )
        return

    # Build a readable message string for the wake prompt
    chat_msg = f"{sender}: {text}" if text else ""
    custom_prompt = text if is_hidden_session_request else ""

    # Session turn guard: if a session is active on this channel and the sender
    # is an agent, only allow triggering the agent whose turn it is.
    # Human @mentions are always allowed (the session engine handles pausing).
    sender_is_agent = sender in known_agents
    allowed_agent = session_engine.get_allowed_agent(channel) if session_engine and sender_is_agent else None

    import mcp_bridge
    for target in targets:
        # Skip pending instances — they haven't been named/claimed yet
        if registry:
            inst = registry.get_instance(target)
            if inst and inst.get("state") == "pending":
                continue
        # Session guard: suppress out-of-turn agent triggers
        if allowed_agent and target != allowed_agent:
            continue
        if not mcp_bridge.is_online(target):
            store.add("system", f"{target} appears offline — message queued.", msg_type="system", channel=channel)
        if agents.is_available(target):
            await agents.trigger(target, message=chat_msg, channel=channel, prompt=custom_prompt)


# --- broadcasting ---

async def _broadcast(raw_json: str):
    """Send a pre-serialized JSON string to all WebSocket clients."""
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(raw_json)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast(msg: dict):
    data = json.dumps({"type": "message", "data": msg})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) for ch in room_settings.get("channels", ["general"]))
    data = json.dumps({"type": "status", "data": status})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_typing(agent_name: str, is_typing: bool):
    data = json.dumps({"type": "typing", "agent": agent_name, "active": is_typing})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_clear(channel: str | None = None):
    payload = {"type": "clear"}
    if channel:
        payload["channel"] = channel
    data = json.dumps(payload)
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_todo_update(msg_id: int, status: str | None):
    data = json.dumps({"type": "todo_update", "data": {"id": msg_id, "status": status}})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_settings():
    data = json.dumps({"type": "settings", "data": room_settings})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_rule(action: str, rule: dict):
    data = json.dumps({"type": "rule", "action": action, "data": rule})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_job(action: str, data: dict):
    payload = json.dumps({"type": "job", "action": action, "data": data})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_schedule(action: str, schedule: dict):
    payload = json.dumps({"type": "schedule", "action": action, "data": schedule})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_session(action: str, session: dict):
    payload = json.dumps({"type": "session", "action": action, "data": session})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(payload)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_hats():
    data = json.dumps({"type": "hats", "data": agent_hats})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_agents():
    """Send updated agent config (from registry) to all WebSocket clients."""
    agent_cfg = registry.get_agent_config() if registry else {}
    data = json.dumps({"type": "agents", "data": agent_cfg})
    dead = set()
    for client in list(ws_clients):
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


def _on_registry_change():
    """Called from registry (any thread) when instances register/deregister/claim/rename."""
    # Update router with current agent names (base names + registered instances)
    if router and registry:
        base_names = list(registry.get_bases().keys())
        # Only include active instances in routing (pending ones are inert)
        instance_names = registry.get_active_names()
        all_names = list(set(base_names + instance_names))
        router.update_agents(all_names)
    # Broadcast to WebSocket clients
    if _event_loop:
        asyncio.run_coroutine_threadsafe(broadcast_agents(), _event_loop)
        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # --- Security: validate session token on WebSocket connect ---
    token = websocket.query_params.get("token", "")
    if token != session_token:
        # Must accept before closing so the browser receives the close frame.
        # Code 4003 triggers an auto-reload in the client to pick up the new token.
        await websocket.accept()
        await websocket.close(code=4003, reason="forbidden: invalid session token")
        return

    await websocket.accept()
    ws_clients.add(websocket)

    # Send settings
    await websocket.send_text(json.dumps({"type": "settings", "data": room_settings}))

    # Send registered instances (used for pills/mentions)
    agent_cfg = registry.get_agent_config() if registry else {}
    await websocket.send_text(json.dumps({"type": "agents", "data": agent_cfg}))

    # Send base agent colors (used for message coloring, no pills)
    base_colors = {}
    for name, cfg in config.get("agents", {}).items():
        base_colors[name] = {"color": cfg.get("color", "#888"), "label": cfg.get("label", name)}
    await websocket.send_text(json.dumps({"type": "base_colors", "data": base_colors}))

    # Send todos {msg_id: status}
    await websocket.send_text(json.dumps({"type": "todos", "data": store.get_todos()}))

    # Send rules
    await websocket.send_text(json.dumps({"type": "rules", "data": rules.list_all()}))

    # Send hats
    await websocket.send_text(json.dumps({"type": "hats", "data": agent_hats}))

    # Send jobs
    await websocket.send_text(json.dumps({"type": "jobs", "data": jobs.list_all()}))

    # Send schedules
    await websocket.send_text(json.dumps({"type": "schedules", "data": schedules.list_all()}))

    # Send pending instances (so late-connecting browsers still see the naming lightbox)
    if registry:
        for inst in registry.get_all().values():
            if inst.get("state") == "pending":
                await websocket.send_text(json.dumps({
                    "type": "pending_instance",
                    "name": inst["name"],
                    "base": inst.get("base", ""),
                    "label": inst.get("label", inst["name"]),
                    "color": inst.get("color", "#888"),
                }))

    # Send history (per channel based on history_limit)
    limit_val = room_settings.get("history_limit", "all")
    count = 10000 if limit_val == "all" else int(limit_val)
    
    history = []
    for ch in room_settings["channels"]:
        history.extend(store.get_recent(count, channel=ch))
    
    # Sort history by timestamp to interleave messages from different channels correctly
    history.sort(key=lambda m: m.get("timestamp", 0))
    
    for msg in history:
        await websocket.send_text(json.dumps({"type": "message", "data": msg}))

    # Send status
    await broadcast_status()

    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)

            if event.get("type") == "message":
                text = event.get("text", "").strip()
                attachments = event.get("attachments", [])
                sender = event.get("sender") or room_settings.get("username", "user")
                channel = event.get("channel", "general")

                if not text and not attachments:
                    continue

                # Command handling
                if text.startswith("/"):
                    cmd_parts = text.split()
                    cmd = cmd_parts[0].lower()
                    if cmd == "/clear":
                        store.clear(channel=channel)
                        await broadcast_clear(channel=channel)
                        continue
                    if cmd == "/continue":
                        router.continue_routing()
                        store.add("system", "Resuming agent conversation...", msg_type="system", channel=channel)
                        await broadcast_status()
                        continue
                    # Broadcast slash commands — expand without storing the raw command.
                    # _handle_new_message will store the expanded version.
                    if cmd in ("/hatmaking", "/artchallenge", "/roastreview", "/poetry"):
                        await _handle_new_message({"sender": sender, "text": text, "channel": channel})
                        continue

                # Store message — the on_message callback handles broadcast + triggers
                reply_to = event.get("reply_to")
                if reply_to is not None:
                    reply_to = int(reply_to)

                store.add(sender, text, attachments=attachments, reply_to=reply_to,
                          channel=channel)

            elif event.get("type") == "delete":
                ids = event.get("ids", [])
                if ids:
                    deleted = store.delete([int(i) for i in ids])
                    if deleted:
                        data = json.dumps({"type": "delete", "ids": deleted})
                        dead = set()
                        for client in list(ws_clients):
                            try:
                                await client.send_text(data)
                            except Exception:
                                dead.add(client)
                        ws_clients.difference_update(dead)
                continue

            elif event.get("type") == "todo_add":
                msg_id = event.get("id")
                if msg_id is not None:
                    store.add_todo(int(msg_id))
                    await broadcast_todo_update(int(msg_id), "todo")
                continue

            elif event.get("type") == "todo_toggle":
                msg_id = event.get("id")
                if msg_id is not None:
                    mid = int(msg_id)
                    status = store.get_todo_status(mid)
                    if status == "todo":
                        store.complete_todo(mid)
                        await broadcast_todo_update(mid, "done")
                    elif status == "done":
                        store.reopen_todo(mid)
                        await broadcast_todo_update(mid, "todo")
                continue

            elif event.get("type") == "todo_remove":
                msg_id = event.get("id")
                if msg_id is not None:
                    store.remove_todo(int(msg_id))
                    await broadcast_todo_update(int(msg_id), None)
                continue

            elif event.get("type") in ("decision_propose", "rule_propose"):
                text = event.get("text") or event.get("decision", "")
                text = text.strip()
                author = event.get("author") or event.get("owner") or room_settings.get("username", "user")
                reason = event.get("reason", "")
                is_human = author.lower() == room_settings.get("username", "user").lower()
                if text:
                    rule = rules.propose(text, author, reason)
                    if rule:
                        if is_human:
                            # Human-created rules go straight to draft, no card
                            rules.make_draft(rule["id"])
                        else:
                            # Agent proposals get a card in the timeline
                            channel = event.get("channel", "general")
                            msg = store.add(
                                author, f"Rule proposal: {text}",
                                msg_type="rule_proposal",
                                channel=channel,
                                metadata={"rule_id": rule["id"], "text": text, "status": "pending"},
                            )
                            await broadcast(msg)
                continue

            elif event.get("type") in ("decision_approve", "rule_activate"):
                rid = event.get("id")
                if rid is not None:
                    rules.activate(int(rid))
                continue

            elif event.get("type") in ("decision_unapprove", "rule_deactivate"):
                rid = event.get("id")
                if rid is not None:
                    rules.deactivate(int(rid))
                continue

            elif event.get("type") == "rule_make_draft":
                rid = event.get("id")
                if rid is not None:
                    rules.make_draft(int(rid))
                continue

            elif event.get("type") in ("decision_edit", "rule_edit"):
                rid = event.get("id")
                if rid is not None:
                    rules.edit(
                        int(rid),
                        text=event.get("text") or event.get("decision"),
                        reason=event.get("reason"),
                    )
                continue

            elif event.get("type") in ("decision_delete", "rule_delete"):
                rid = event.get("id")
                if rid is not None:
                    rules.delete(int(rid))
                continue

            elif event.get("type") == "rule_remind":
                rules.set_remind()
                remind_data = json.dumps({"type": "rules_remind", "data": {}})
                for client in list(ws_clients):
                    try:
                        await client.send_text(remind_data)
                    except Exception:
                        pass
                continue

            elif event.get("type") == "update_settings":
                new = event.get("data", {})
                if "title" in new and isinstance(new["title"], str):
                    room_settings["title"] = new["title"].strip() or "agentchattr"
                if "username" in new and isinstance(new["username"], str):
                    room_settings["username"] = new["username"].strip() or "user"
                if "font" in new and new["font"] in ("mono", "serif", "sans"):
                    room_settings["font"] = new["font"]
                if "max_agent_hops" in new:
                    try:
                        hops = int(new["max_agent_hops"])
                        hops = max(1, min(hops, 50))
                        room_settings["max_agent_hops"] = hops
                        router.max_hops = hops
                    except (ValueError, TypeError):
                        pass
                if "contrast" in new and new["contrast"] in ("normal", "high"):
                    room_settings["contrast"] = new["contrast"]
                if "rules_refresh_interval" in new:
                    try:
                        ri = int(new["rules_refresh_interval"])
                        room_settings["rules_refresh_interval"] = max(0, min(ri, 100))
                    except (ValueError, TypeError):
                        pass
                if "history_limit" in new:
                    val = str(new["history_limit"]).strip().lower()
                    if val == "all":
                        room_settings["history_limit"] = "all"
                    else:
                        try:
                            val_int = int(val)
                            room_settings["history_limit"] = max(1, min(val_int, 10000))
                        except (ValueError, TypeError):
                            pass
                _save_settings()
                await broadcast_settings()

            elif event.get("type") == "rename_agent":
                agent_name = (event.get("name") or "").strip()
                new_label = (event.get("label") or "").strip()
                if agent_name and new_label and registry:
                    # Derive a sanitized sender ID from the label
                    import re as _re
                    new_id = _re.sub(r'[^a-z0-9-]', '', new_label.lower().replace(' ', '-')).strip('-')
                    if not new_id:
                        new_id = agent_name  # fallback: keep old name, just change label
                    if new_id == agent_name:
                        # Same ID — label-only change
                        registry.set_label(agent_name, new_label)
                    else:
                        result = registry.rename(agent_name, new_id, new_label)
                        if isinstance(result, str):
                            # Rename failed (collision etc.) — fall back to label-only
                            registry.set_label(agent_name, new_label)
                        else:
                            # Migrate presence + cursors to new name
                            import mcp_bridge
                            mcp_bridge.migrate_identity(agent_name, new_id)
                            # Update sender on all historical messages
                            store.rename_sender(agent_name, new_id)
                            # Notify clients so they can update sender in DOM
                            rename_event = json.dumps({
                                "type": "agent_renamed",
                                "old_name": agent_name,
                                "new_name": new_id,
                            })
                            await _broadcast(rename_event)
                continue

            elif event.get("type") == "name_pending":
                # Human names a pending instance (from lightbox)
                agent_name = (event.get("name") or "").strip()
                new_label = (event.get("label") or "").strip()
                if agent_name and registry:
                    if not new_label:
                        # Accept default name
                        registry.confirm_pending(agent_name)
                    else:
                        import re as _re
                        new_id = _re.sub(r'[^a-z0-9-]', '', new_label.lower().replace(' ', '-')).strip('-')
                        if not new_id:
                            new_id = agent_name
                        if new_id == agent_name:
                            # Same ID — just update label and confirm
                            registry.set_label(agent_name, new_label)
                            registry.confirm_pending(agent_name)
                        else:
                            result = registry.rename(agent_name, new_id, new_label)
                            if isinstance(result, str):
                                # Rename failed — just confirm with label
                                registry.set_label(agent_name, new_label)
                                registry.confirm_pending(agent_name)
                            else:
                                # Rename succeeded — confirm new name
                                registry.confirm_pending(new_id)
                                import mcp_bridge
                                mcp_bridge.migrate_identity(agent_name, new_id)
                                # Update sender on all historical messages
                                store.rename_sender(agent_name, new_id)
                                rename_event = json.dumps({
                                    "type": "agent_renamed",
                                    "old_name": agent_name,
                                    "new_name": new_id,
                                })
                                await _broadcast(rename_event)
                continue

            elif event.get("type") == "channel_create":
                name = (event.get("name") or "").strip().lower()
                if not name or not _CHANNEL_NAME_RE.match(name):
                    continue
                if name in room_settings["channels"]:
                    continue
                if len(room_settings["channels"]) >= MAX_CHANNELS:
                    continue
                room_settings["channels"].append(name)
                _save_settings()
                await broadcast_settings()

            elif event.get("type") == "channel_rename":
                old_name = (event.get("old_name") or "").strip().lower()
                new_name = (event.get("new_name") or "").strip().lower()
                if old_name == "general":
                    continue
                if not new_name or not _CHANNEL_NAME_RE.match(new_name):
                    continue
                if old_name not in room_settings["channels"]:
                    continue
                if new_name in room_settings["channels"]:
                    continue
                idx = room_settings["channels"].index(old_name)
                room_settings["channels"][idx] = new_name
                store.rename_channel(old_name, new_name)
                import mcp_bridge
                mcp_bridge.migrate_cursors_rename(old_name, new_name)
                _save_settings()
                await broadcast_settings()
                # Tell clients to migrate DOM elements
                rename_event = json.dumps({
                    "type": "channel_renamed",
                    "old_name": old_name,
                    "new_name": new_name,
                })
                for c in list(ws_clients):
                    try:
                        await c.send_text(rename_event)
                    except Exception:
                        pass

            elif event.get("type") == "channel_delete":
                name = (event.get("name") or "").strip().lower()
                if name == "general":
                    continue
                if name not in room_settings["channels"]:
                    continue
                room_settings["channels"].remove(name)
                store.delete_channel(name)
                import mcp_bridge
                mcp_bridge.migrate_cursors_delete(name)
                _save_settings()
                await broadcast_settings()

    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except Exception:
        ws_clients.discard(websocket)
        log.exception("WebSocket error")


# --- REST endpoints ---

ALLOWED_UPLOAD_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB default


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".png"
    if ext.lower() not in ALLOWED_UPLOAD_EXTS:
        return JSONResponse({"error": f"unsupported file type: {ext}"}, status_code=400)

    content = await file.read()
    max_bytes = config.get("images", {}).get("max_size_mb", 10) * 1024 * 1024
    if len(content) > max_bytes:
        return JSONResponse({"error": f"file too large (max {max_bytes // 1024 // 1024} MB)"}, status_code=400)

    filename = f"{uuid.uuid4().hex[:8]}{ext}"
    filepath = upload_dir / filename
    filepath.write_bytes(content)

    return JSONResponse({
        "name": file.filename,
        "url": f"/uploads/{filename}",
    })


@app.get("/api/messages")
async def get_messages(since_id: int = 0, limit: int = 50, channel: str = ""):
    ch = channel if channel else None
    if since_id:
        return store.get_since(since_id, channel=ch)
    return store.get_recent(limit, channel=ch)


@app.post("/api/send")
async def api_send(request: Request):
    """REST endpoint for API agents to send messages without WebSocket.

    Authenticated via Bearer registration token. Sender is resolved from
    the token — the agent cannot impersonate another identity.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"error": "missing Authorization: Bearer <token>"}, status_code=401)
    token = auth[7:].strip()
    inst = registry.resolve_token(token) if registry else None
    if not inst:
        return JSONResponse({"error": "invalid or expired token"}, status_code=403)

    sender = inst["name"]
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    channel = body.get("channel", "general")

    msg = store.add(sender, text, channel=channel)
    return JSONResponse(msg)


@app.get("/api/status")
async def get_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) for ch in room_settings.get("channels", ["general"]))
    return status


@app.get("/api/settings")
async def get_settings():
    return room_settings


@app.delete("/api/hat/{agent_name}")
async def delete_hat(agent_name: str):
    """Remove an agent's hat (called by the trash-can UI)."""
    clear_agent_hat(agent_name)
    return JSONResponse({"ok": True})


# --- Jobs API ---

@app.get("/api/schedules")
async def get_schedules():
    return schedules.list_all()


@app.post("/api/schedules")
async def create_schedule(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    targets = body.get("targets", [])
    channel = body.get("channel", "general")
    spec = body.get("spec", "")
    one_shot = body.get("one_shot", False)
    send_at_date = body.get("send_at_date", "")  # "YYYY-MM-DD" for one-shot
    created_by = body.get("created_by", "user")
    if not prompt or not targets or not spec:
        return JSONResponse({"error": "prompt, targets, and spec are required"}, status_code=400)
    interval_sec, daily_at = parse_schedule_spec(spec)
    if interval_sec is None:
        return JSONResponse({"error": f"Invalid schedule spec: {spec}"}, status_code=400)
    # For one-shot, compute exact send_at timestamp from date + daily_at time
    send_at = None
    if one_shot and daily_at and send_at_date:
        import datetime as _dt
        try:
            dt = _dt.datetime.strptime(f"{send_at_date} {daily_at}", "%Y-%m-%d %H:%M")
            send_at = dt.timestamp()
        except ValueError:
            pass
    s = schedules.create(
        prompt=prompt, targets=targets, channel=channel,
        interval_seconds=interval_sec, daily_at=daily_at,
        one_shot=one_shot, send_at=send_at,
        created_by=created_by,
    )
    return JSONResponse(s)


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    removed = schedules.delete(schedule_id)
    if not removed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


@app.patch("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str):
    result = schedules.toggle(schedule_id)
    if not result:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


@app.get("/api/jobs")
async def get_jobs(channel: str = "", status: str = ""):
    """List jobs, optionally filtered."""
    ch = channel if channel else None
    st = status if status else None
    return jobs.list_all(channel=ch, status=st)


@app.post("/api/messages/{msg_id}/demote")
async def demote_proposal(msg_id: int):
    """Demote a proposal-style message back to a regular chat message."""
    msg = store.get_by_id(msg_id)
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    msg_type = msg.get("type")
    if msg_type not in {"job_proposal", "session_draft"}:
        return JSONResponse({"error": "not a proposal"}, status_code=400)
    meta = msg.get("metadata", {})
    updated_fields = {"type": "chat", "metadata": {}}

    if msg_type == "job_proposal":
        body_text = meta.get("body", "")
        title = meta.get("title", "")
        plain_text = f"**{title}**\n\n{body_text}" if title else body_text or msg.get("text", "")
        updated_fields["text"] = plain_text
    else:
        tmpl = meta.get("template")
        errors = meta.get("errors", []) or []
        proposed_by = meta.get("proposed_by") or msg.get("sender", "system")
        parts = []

        if isinstance(tmpl, dict):
            name = str(tmpl.get("name", "")).strip()
            desc = str(tmpl.get("description", "")).strip()
            if name:
                parts.append(f"**{name}**")
            if desc:
                parts.append(desc)
            phases = tmpl.get("phases") or []
            if phases:
                lines = []
                for i, ph in enumerate(phases, 1):
                    ph_name = ph.get("name", f"Round {i}")
                    participants = ", ".join(ph.get("participants", []))
                    line = f"{i}. {ph_name}"
                    if participants:
                        line += f" -- {participants}"
                    prompt = (ph.get("prompt") or "").strip()
                    if prompt:
                        line += f"\n   {prompt}"
                    lines.append(line)
                parts.append("\n".join(lines))
        else:
            label = str(msg.get("text", "")).strip() or "Session draft"
            parts.append(label)
            if errors:
                parts.append("\n".join(f"- {e}" for e in errors))

        updated_fields["sender"] = proposed_by
        updated_fields["text"] = "\n\n".join(p for p in parts if p).strip()

    updated = store.update_message(msg_id, updated_fields)
    if updated:
        # Broadcast the updated message to all clients
        payload = json.dumps({"type": "edit", "message": updated})
        dead = set()
        for client in list(ws_clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        ws_clients.difference_update(dead)
    return updated or {"ok": True}


@app.post("/api/messages/{msg_id}/resolve_rule_proposal")
async def resolve_rule_proposal(msg_id: int, request: Request):
    """Activate or dismiss a rule proposal."""
    msg = store.get_by_id(msg_id)
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    if msg.get("type") != "rule_proposal":
        return JSONResponse({"error": "not a rule proposal"}, status_code=400)
    body = await request.json()
    action = body.get("action", "")
    meta = msg.get("metadata", {})
    rule_id = meta.get("rule_id")

    if action == "activate" and rule_id is not None:
        rules.activate(int(rule_id))
        meta["status"] = "activated"
    elif action == "draft" and rule_id is not None:
        rules.make_draft(int(rule_id))
        meta["status"] = "drafted"
    elif action == "dismiss" and rule_id is not None:
        rules.delete(int(rule_id))
        meta["status"] = "dismissed"
    else:
        return JSONResponse({"error": "invalid action"}, status_code=400)

    updated = store.update_message(msg_id, {"metadata": meta})
    if updated:
        # Broadcast the updated message so all clients re-render the card
        payload = json.dumps({"type": "edit", "message": updated})
        dead = set()
        for client in list(ws_clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        ws_clients.difference_update(dead)
    return updated or {"ok": True}


@app.post("/api/messages/{msg_id}/demote_rule_proposal")
async def demote_rule_proposal(msg_id: int):
    """Demote a rule_proposal message back to a regular chat message and delete the rule."""
    msg = store.get_by_id(msg_id)
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    if msg.get("type") != "rule_proposal":
        return JSONResponse({"error": "not a rule proposal"}, status_code=400)
    meta = msg.get("metadata", {})
    rule_id = meta.get("rule_id")
    if rule_id is not None:
        rules.delete(int(rule_id))
    text = meta.get("text", msg.get("text", ""))
    updated = store.update_message(msg_id, {
        "type": "chat",
        "text": text,
        "metadata": {},
    })
    if updated:
        payload = json.dumps({"type": "edit", "message": updated})
        dead = set()
        for client in list(ws_clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.add(client)
        ws_clients.difference_update(dead)
    return updated or {"ok": True}


@app.post("/api/trigger-agent")
async def trigger_agent_silent(request: Request):
    """Silently trigger an agent with a message (no chat message posted)."""
    body = await request.json()
    agent_name = body.get("agent", "").strip()
    message = body.get("message", "").strip()
    channel = body.get("channel", "general")
    source_msg_id = body.get("source_msg_id")
    if not agent_name or not message:
        return JSONResponse({"error": "agent and message required"}, status_code=400)

    custom_prompt = body.get("prompt", "").strip()
    if not custom_prompt:
        if source_msg_id is not None:
            custom_prompt = (
                f"mcp read #{channel} - you were mentioned, take appropriate action "
                f"- conversion request: use chat history to find message #{source_msg_id} "
                f"and use chat_propose_job to propose it as a job with title<=80 chars and body<=500 chars."
            )
        else:
            custom_prompt = (
                f"mcp read #{channel} - you were mentioned, take appropriate action "
                f"- conversion request: use chat_propose_job to propose a job from the referenced message."
            )
    # Resolve to instances if multi-instance
    targets = [agent_name]
    if registry:
        resolved = registry.resolve_to_instances(agent_name)
        if resolved:
            targets = resolved
    for target in targets:
        if agents.is_available(target):
            await agents.trigger(target, message=message, channel=channel, prompt=custom_prompt)
    return {"ok": True, "triggered": targets}


@app.post("/api/jobs")
async def create_job(request: Request):
    """Create a new job."""
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    job_type = body.get("type", "job")
    channel = body.get("channel", "general")
    created_by = body.get("created_by", "user")
    anchor_msg_id = body.get("anchor_msg_id")
    assignee = body.get("assignee", "")
    job_body = body.get("body", "")
    result = jobs.create(
        title=title, job_type=job_type, channel=channel,
        created_by=created_by, anchor_msg_id=anchor_msg_id,
        assignee=assignee, body=job_body,
    )
    # Mark the proposal message as accepted so it persists across refresh
    if anchor_msg_id:
        anchor_msg = store.get_by_id(anchor_msg_id)
        if anchor_msg and anchor_msg.get("type") == "job_proposal":
            meta = dict(anchor_msg.get("metadata", {}))
            meta["status"] = "accepted"
            updated_msg = store.update_message(anchor_msg_id, {"metadata": meta})
            if updated_msg:
                payload = json.dumps({"type": "edit", "message": updated_msg})
                dead = set()
                for client in list(ws_clients):
                    try:
                        await client.send_text(payload)
                    except Exception:
                        dead.add(client)
                ws_clients.difference_update(dead)
    # Post breadcrumb in main timeline with job_id for clickable link
    store.add(created_by, f"Job created: {title}", msg_type="job_created",
              channel=channel, metadata={"job_id": result["id"]})
    return result


@app.patch("/api/jobs/{job_id}")
async def update_job(job_id: int, request: Request):
    """Update a job's status, title, or assignee."""
    body = await request.json()
    result = None
    if "status" in body:
        result = jobs.update_status(job_id, body["status"])
    if "title" in body:
        result = jobs.update_title(job_id, body["title"])
    if "assignee" in body:
        result = jobs.update_assignee(job_id, body["assignee"])
    if result is None:
        return JSONResponse({"error": "not found or invalid"}, status_code=404)
    return result


@app.post("/api/jobs/reorder")
async def reorder_jobs(request: Request):
    """Reorder jobs within a status group (globally, not per-channel)."""
    body = await request.json()
    status = body.get("status", "open")
    ordered_ids = body.get("ordered_ids", [])
    if not isinstance(ordered_ids, list) or len(ordered_ids) == 0:
        return JSONResponse({"error": "ordered_ids required"}, status_code=400)
    updated = jobs.reorder(status=status, ordered_ids=ordered_ids)
    return {"ok": True, "updated": len(updated)}


@app.get("/api/jobs/{job_id}/messages")
async def get_job_messages(job_id: int):
    """Get all messages in a job."""
    msgs = jobs.get_messages(job_id)
    if msgs is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return msgs


@app.post("/api/jobs/{job_id}/messages")
async def post_job_message(job_id: int, request: Request):
    """Post a message to a job."""
    body = await request.json()
    text = body.get("text", "").strip()
    sender = body.get("sender", "user")
    attachments = body.get("attachments", [])
    if not text and not attachments:
        return JSONResponse({"error": "text or attachments required"}, status_code=400)
    msg_type = body.get("type", "chat")
    msg = jobs.add_message(job_id, sender, text,
                           attachments=attachments, msg_type=msg_type)
    if msg is None:
        return JSONResponse({"error": "job not found"}, status_code=404)

    # Route @mentions in job messages to agents (with job_id context)
    job = jobs.get(job_id)
    if job:
        channel = job.get("channel", "general")
        raw_targets = router.get_targets(sender, text, channel)
        targets = []
        for t in raw_targets:
            if registry:
                targets.extend(registry.resolve_to_instances(t))
            else:
                targets.append(t)
        targets = list(dict.fromkeys(targets))

        import mcp_bridge
        chat_msg = f"{sender}: {text}" if text else ""
        for target in targets:
            if registry:
                inst = registry.get_instance(target)
                if inst and inst.get("state") == "pending":
                    continue
            if agents.is_available(target):
                await agents.trigger(target, message=chat_msg, channel=channel,
                                     job_id=job_id)

    return msg


@app.delete("/api/jobs/{job_id}/messages/{msg_id}")
async def delete_job_message(job_id: int, msg_id: int):
    """Soft-delete a message in a job thread."""
    result = jobs.delete_message(job_id, msg_id)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True, **result}


@app.post("/api/jobs/{job_id}/messages/{msg_index}/resolve")
async def resolve_job_message(job_id: int, msg_index: int, request: Request):
    """Resolve a suggestion message (accept/dismiss)."""
    body = await request.json()
    resolution = body.get("resolution", "dismissed")
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    msgs = job.get("messages", [])
    if msg_index < 0 or msg_index >= len(msgs):
        return JSONResponse({"error": "invalid message index"}, status_code=400)
    msg = msgs[msg_index]
    msg["resolved"] = resolution
    jobs._save()

    # If accepted, trigger the suggesting agent with context
    if resolution == "accepted" and msg.get("sender"):
        agent_name = msg["sender"]
        channel = job.get("channel", "general")
        if agents.is_available(agent_name):
            await agents.trigger(agent_name,
                                 message=f"Your suggestion was accepted: {msg.get('text', '')}",
                                 channel=channel, job_id=job_id)

    return {"ok": True, "resolution": resolution}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, request: Request):
    """Delete or archive a job. ?permanent=true for real delete."""
    permanent = request.query_params.get("permanent", "").lower() == "true"
    if permanent:
        result = jobs.delete(job_id)
    else:
        result = jobs.update_status(job_id, "archived")
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return result


@app.get("/api/roles")
async def get_roles():
    """Get all agent roles."""
    import mcp_bridge
    return mcp_bridge.get_all_roles()


@app.post("/api/roles/{agent_name}")
async def set_agent_role(agent_name: str, request: Request):
    """Set or clear an agent's role."""
    import mcp_bridge
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    role = body.get("role", "").strip()
    mcp_bridge.set_role(agent_name, role)
    await broadcast_status()
    return JSONResponse({"ok": True, "role": role})


# --- Rules API ---

@app.get("/api/rules")
async def get_rules():
    """Get all rules (all states)."""
    return JSONResponse(rules.list_all())


@app.get("/api/rules/active")
async def get_active_rules():
    """Get compact active rules for agent injection."""
    data = rules.active_list()
    data["refresh_interval"] = room_settings.get("rules_refresh_interval", 10)
    return JSONResponse(data)


@app.post("/api/rules/remind")
async def remind_agents():
    """Set remind flag — agents get rules on next trigger."""
    rules.set_remind()
    remind_data = json.dumps({"type": "rules_remind", "data": {}})
    for client in list(ws_clients):
        try:
            await client.send_text(remind_data)
        except Exception:
            pass
    return JSONResponse({"ok": True})


@app.post("/api/rules/agent_sync/{agent_name}")
async def report_rule_sync(agent_name: str, request: Request):
    """Wrapper reports that an agent has seen rules at a given epoch."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    epoch = body.get("epoch", 0)
    rules.report_agent_sync(agent_name, epoch)
    # Clear remind flag once any agent has seen the updated rules
    rules.clear_remind()
    return JSONResponse({"ok": True})


@app.get("/api/rules/freshness")
async def get_rules_freshness():
    """Get per-agent sync status."""
    return JSONResponse(rules.agent_freshness())


@app.post("/api/register")
async def register_agent(request: Request):
    """Wrapper calls this to register a new agent instance."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    base = body.get("base", "")
    label = body.get("label")
    if not base:
        return JSONResponse({"error": "base is required"}, status_code=400)
    result = registry.register(base, label)
    if result is None:
        return JSONResponse({"error": f"unknown base: {base}"}, status_code=400)
    # Touch presence so the instance doesn't immediately time out
    import mcp_bridge
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[result["name"]] = __import__("time").time()
    # If slot 1 was renamed (e.g. "claude" → "claude-1"), migrate state
    renamed = result.pop("_renamed_slot1", None)
    if renamed:
        mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
        store.rename_sender(renamed["old"], renamed["new"])
        if _event_loop:
            rename_event = json.dumps({
                "type": "agent_renamed",
                "old_name": renamed["old"],
                "new_name": renamed["new"],
            })
            asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
    # Broadcast pending_instance event so UI can show naming lightbox
    if result.get("state") == "pending" and _event_loop:
        pending_event = json.dumps({
            "type": "pending_instance",
            "name": result["name"],
            "base": base,
            "label": result.get("label", result["name"]),
            "color": result.get("color", "#888"),
        })
        asyncio.run_coroutine_threadsafe(_broadcast(pending_event), _event_loop)
    return JSONResponse(result)


@app.post("/api/deregister/{name}")
async def deregister_agent(name: str, request: Request):
    """Wrapper calls this on shutdown to remove its instance."""
    auth_inst = _resolve_authenticated_agent(request)
    presented_token = _extract_agent_token(request)
    if presented_token and not auth_inst:
        return JSONResponse({"error": "stale_session"}, status_code=409)
    if auth_inst:
        name = auth_inst["name"]
    elif registry and registry.is_agent_family(name):
        return JSONResponse({"error": "authenticated agent session required"}, status_code=403)

    result = registry.deregister(name)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Clean up runtime state (presence, activity, cursors, rename chains)
    import mcp_bridge
    mcp_bridge.purge_identity(name)
    registry.clean_renames_for(name)
    # If the remaining instance was renamed back (e.g. "claude-1" → "claude"), migrate state
    renamed = result.pop("_renamed_back", None)
    if renamed:
        mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
        store.rename_sender(renamed["old"], renamed["new"])
        if _event_loop:
            rename_event = json.dumps({
                "type": "agent_renamed",
                "old_name": renamed["old"],
                "new_name": renamed["new"],
            })
            asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
    return JSONResponse({"ok": True})


@app.post("/api/label/{name}")
async def rename_agent_label(name: str, request: Request):
    """Rename an agent (human-initiated from UI). Changes identity + label."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    label = body.get("label", "").strip()
    if not label:
        return JSONResponse({"error": "label is required"}, status_code=400)

    import re as _re
    new_id = _re.sub(r'[^a-z0-9-]', '', label.lower().replace(' ', '-')).strip('-')
    if not new_id:
        new_id = name

    if new_id == name:
        # Same ID — label-only change
        if registry.set_label(name, label):
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "not found"}, status_code=404)

    result = registry.rename(name, new_id, label)
    if isinstance(result, str):
        # Rename failed — try label-only as fallback
        if registry.set_label(name, label):
            return JSONResponse({"ok": True, "warning": result})
        return JSONResponse({"error": result}, status_code=400)

    import mcp_bridge
    mcp_bridge.migrate_identity(name, new_id)
    # Update sender on all historical messages
    store.rename_sender(name, new_id)
    return JSONResponse({"ok": True, "new_name": new_id})


@app.post("/api/heartbeat/{agent_name}")
async def heartbeat(agent_name: str, request: Request):
    """Wrapper calls this to keep presence alive and report activity.

    Returns the canonical name from the registry so the wrapper can
    detect renames (e.g. claim renamed 'claude-2' to 'claude-music').
    """
    import mcp_bridge
    auth_inst = _resolve_authenticated_agent(request)
    presented_token = _extract_agent_token(request)
    if presented_token and not auth_inst:
        return JSONResponse({"error": "stale_session"}, status_code=409)
    if registry and registry.is_agent_family(agent_name) and not auth_inst:
        return JSONResponse({"error": "authenticated agent session required"}, status_code=403)

    current_name = auth_inst["name"] if auth_inst else agent_name
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[current_name] = __import__("time").time()
    # Optional activity report from wrapper's terminal monitor
    _activity_changed = False
    try:
        body = await request.json()
        if "active" in body:
            active_val = bool(body["active"])
            was_active = mcp_bridge._activity.get(current_name, False)
            mcp_bridge.set_active(current_name, active_val)
            _activity_changed = was_active != active_val
    except Exception:
        pass  # No body = plain heartbeat
    # Immediately broadcast on activity state change (don't wait for background checker)
    if _activity_changed:
        await broadcast_status()
    # Return canonical name so wrapper can track renames
    resp = {"ok": True, "name": current_name}
    if registry:
        # Follow rename chain (e.g. claude-2 was renamed to claude-music)
        canonical = registry.resolve_name(current_name)
        inst = registry.get_instance(canonical)
        # If rename chain didn't help, try family-based lookup
        # (handles case where _renames was cleared by server restart but
        # the instance was claimed/renamed via MCP)
        if not inst:
            base = current_name.split("-")[0] if "-" in current_name else current_name
            family_inst = registry.get_family_instance(base)
            if family_inst:
                inst = family_inst
                canonical = inst["name"]
        if inst:
            resp["name"] = inst["name"]
            resp["pending"] = inst.get("state") == "pending"
            # Also update presence under the canonical name
            if canonical != current_name:
                now = __import__("time").time()
                with mcp_bridge._presence_lock:
                    mcp_bridge._presence[canonical] = now
    return resp


# --- Open agent session in terminal ---

@app.get("/api/platform")
async def get_platform():
    """Return the server's platform so the web UI can match path formats."""
    import sys
    return JSONResponse({"platform": sys.platform})


@app.post("/api/open-path")
async def open_path(body: dict):
    """Open a file or directory in the native file manager.

    Cross-platform: Explorer on Windows, Finder on macOS, xdg-open on Linux.

    Security note: This endpoint is intended for local-only use (127.0.0.1).
    Do not expose this server on a public network without additional access controls.
    """
    import subprocess
    import sys

    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "no path"}, status_code=400)

    p = Path(path)
    try:
        if sys.platform == "win32":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            elif p.is_dir():
                subprocess.Popen(["explorer", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
        elif sys.platform == "darwin":
            if p.is_file():
                subprocess.Popen(["open", "-R", str(p)])
            elif p.is_dir():
                subprocess.Popen(["open", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
        else:
            # Linux — xdg-open opens the containing folder for files
            if p.is_file():
                subprocess.Popen(["xdg-open", str(p.parent)])
            elif p.is_dir():
                subprocess.Popen(["xdg-open", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"ok": True})


# Serve uploaded images
# --- Sessions API ---

@app.get("/api/sessions/templates")
async def get_session_templates():
    if not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    return JSONResponse(session_store.get_templates())


@app.get("/api/sessions/active")
async def get_active_session(channel: str = "general"):
    if not session_engine:
        return JSONResponse(None)
    session = session_engine.get_active(channel)
    return JSONResponse(session)


@app.get("/api/sessions/active-all")
async def get_all_active_sessions():
    if not session_engine:
        return JSONResponse([])
    return JSONResponse(session_engine.list_active())


@app.post("/api/sessions/start")
async def start_session(request: Request):
    if not session_engine or not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    body = await request.json()
    template_id = body.get("template_id", "")
    draft_message_id = body.get("draft_message_id")
    channel = body.get("channel", "general")
    cast = body.get("cast", {})
    goal = body.get("goal", "")
    started_by = body.get("started_by", "user")

    # If running from a draft, load the inline template from message metadata
    tmpl = None
    if draft_message_id:
        draft_msg = store.get_by_id(int(draft_message_id))
        if not draft_msg:
            return JSONResponse({"error": "draft message not found"}, status_code=404)
        meta = draft_msg.get("metadata", {})
        if not meta.get("valid"):
            return JSONResponse({"error": "draft is not valid"}, status_code=400)
        tmpl = meta.get("template")
        if not tmpl:
            return JSONResponse({"error": "draft has no template"}, status_code=400)
        # Register as a temporary template
        template_id = tmpl.get("id", f"draft-{draft_message_id}")
        tmpl["id"] = template_id
        tmpl["is_custom"] = True
        session_store._templates[template_id] = tmpl

    # Validate template exists
    if not tmpl:
        tmpl = session_store.get_template(template_id)
    if not tmpl:
        return JSONResponse({"error": f"unknown template: {template_id}"}, status_code=400)

    # Auto-fill cast from available agents if not fully provided
    if not cast:
        online = registry.get_active_names() if registry else []
        roles = tmpl.get("roles", [])
        cast = _auto_cast(roles, online, started_by)
        if not cast:
            return JSONResponse(
                {"error": "not enough agents online to fill all roles"},
                status_code=400,
            )

    session = session_engine.start_session(template_id, channel, cast, started_by, goal)
    if not session:
        return JSONResponse({"error": "could not start session (one may already be active)"}, status_code=409)

    # Add start banner to chat (only after confirmed success)
    store.add(
        sender="system",
        text=f"Session started: {tmpl.get('name', template_id)}",
        msg_type="session_start",
        channel=channel,
        metadata={"template_id": template_id, "goal": goal, "session_id": session["id"]},
    )
    session_engine.emit_current_phase_banner(session)

    return JSONResponse(session)


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: int):
    if not session_engine:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    session = session_engine.end_session(session_id)
    if not session:
        return JSONResponse({"error": "session not found or already ended"}, status_code=404)

    # Banner is added by _on_session_change("interrupt", ...) callback
    return JSONResponse(session)


@app.post("/api/sessions/request-draft")
async def request_session_draft(request: Request):
    """Ask an agent to design a session template. Called by the 'Design a session' UI."""
    body = await request.json()
    agent_name = body.get("agent", "").strip()
    description = body.get("description", "").strip()
    channel = body.get("channel", "general")
    sender = body.get("sender", "user")
    if not agent_name or not description:
        return JSONResponse({"error": "agent and description required"}, status_code=400)

    mention_str = f"@{agent_name}"
    store.add(
        "system",
        f"Requested session draft from {mention_str}. Wait for a proposal.",
        channel=channel,
    )
    store.add(
        sender,
        f"{mention_str} Design a session workflow for: **{description}**\n\n"
        "Respond with a single chat message containing a fenced JSON code block with this exact structure:\n"
        "```session\n"
        '{"name": "...", "description": "...", "roles": ["role1", "role2", ...], '
        '"phases": [{"name": "...", "participants": ["role1"], "prompt": "...", "is_output": false}, ...]}\n'
        "```\n"
        "Rules: max 6 roles, max 6 phases, max 4 participants per phase, max 200 chars per prompt. "
        "Mark exactly one phase as `is_output: true` (the final deliverable). "
        f"Keep it focused and sequential. Use the chat_send tool to post your response in the #{channel} channel. "
        "Do NOT respond only in your terminal.",
        channel=channel,
        msg_type="session_request",
        metadata={"session_request": True, "mentions": [f"@{agent_name}"], "request": description},
    )
    return JSONResponse({"ok": True})


@app.post("/api/sessions/save-draft")
async def save_draft(request: Request):
    if not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    body = await request.json()
    msg_id = body.get("message_id")
    if not msg_id:
        return JSONResponse({"error": "message_id required"}, status_code=400)
    msg = store.get_by_id(int(msg_id))
    if not msg:
        return JSONResponse({"error": "message not found"}, status_code=404)
    meta = msg.get("metadata", {})
    if not meta.get("valid"):
        return JSONResponse({"error": "draft is not valid"}, status_code=400)
    tmpl = meta.get("template")
    if not tmpl:
        return JSONResponse({"error": "no template in draft"}, status_code=400)

    tmpl.setdefault("id", f"custom-{msg_id}")
    session_store.save_custom_template(tmpl)
    return JSONResponse({"ok": True, "template_id": tmpl["id"]})


@app.delete("/api/sessions/templates/{template_id}")
async def delete_session_template(template_id: str):
    if not session_store:
        return JSONResponse({"error": "sessions not configured"}, status_code=500)
    deleted = session_store.delete_custom_template(template_id)
    if not deleted:
        return JSONResponse({"error": "template not found or not custom"}, status_code=404)
    return JSONResponse({"ok": True, "template_id": template_id})


def _auto_cast(roles: list[str], online_agents: list[str], started_by: str) -> dict:
    """Auto-assign roles to available agents. Returns empty dict if not enough agents."""
    cast = {}
    available = list(online_agents)

    for role in roles:
        if not available:
            # Reuse agents if we run out (one agent, multiple roles)
            available = list(online_agents)
        if not available:
            return {}
        agent = available.pop(0)
        cast[role] = agent

    return cast


# --- Version check (GitHub release notifier) ---

_version_cache: dict = {"data": None, "fetched_at": 0.0}
_VERSION_CACHE_TTL = 1800  # 30 minutes


def _read_local_version() -> str:
    """Read version from VERSION file in project root."""
    vfile = Path(__file__).parent / "VERSION"
    try:
        return vfile.read_text().strip()
    except Exception:
        return ""


def _detect_install_kind() -> str:
    """Detect how this copy was installed: official_git, fork, or unknown."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).parent,
        )
        url = result.stdout.strip().lower()
        if "bcurts/agentchattr" in url:
            return "official_git"
        elif url:
            return "fork"
    except Exception:
        pass
    return "unknown"


def _fetch_latest_release() -> dict | None:
    """Fetch latest release from GitHub API, with 30-min cache."""
    import time
    import urllib.request

    now = time.time()
    if _version_cache["data"] and (now - _version_cache["fetched_at"]) < _VERSION_CACHE_TTL:
        return _version_cache["data"]

    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/bcurts/agentchattr/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "agentchattr"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            result = {
                "tag": data.get("tag_name", ""),
                "url": data.get("html_url", ""),
            }
            _version_cache["data"] = result
            _version_cache["fetched_at"] = now
            return result
    except Exception:
        return _version_cache.get("data")


def _compare_versions(current: str, latest_tag: str) -> str:
    """Compare version strings. Returns 'behind', 'current', or 'unknown'."""
    # Strip leading 'v' from tag
    latest = latest_tag.lstrip("v")
    if not current or not latest:
        return "unknown"
    try:
        from packaging.version import Version
        if Version(current) < Version(latest):
            return "behind"
        return "current"
    except Exception:
        return "unknown"


@app.get("/api/version_check")
async def version_check():
    """Check for newer releases on GitHub."""
    current = _read_local_version()
    loop = asyncio.get_event_loop()
    release = await loop.run_in_executor(None, _fetch_latest_release)

    if not release or not release.get("tag"):
        return JSONResponse({"current": current, "latest": "", "state": "unknown", "url": ""})

    latest_tag = release["tag"]
    install_kind = _detect_install_kind()
    comparison = _compare_versions(current, latest_tag)

    if comparison == "behind":
        if install_kind == "official_git":
            state = "update_available"
        elif install_kind == "fork":
            state = "upstream_update"
        else:
            state = "unknown"
    elif comparison == "current":
        state = "current"
    else:
        state = "unknown"

    return JSONResponse({
        "current": current,
        "latest": latest_tag,
        "state": state,
        "url": release.get("url", ""),
    })


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    filepath = (upload_dir / filename).resolve()
    if not filepath.is_relative_to(upload_dir.resolve()):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if filepath.exists():
        return FileResponse(filepath)
    return JSONResponse({"error": "not found"}, status_code=404)
