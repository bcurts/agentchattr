"""MCP server for agent chat tools — runs alongside the web server.

Serves two transports for compatibility:
  - streamable-http on port 8200 (Claude Code, Codex)
  - SSE on port 8201 (Gemini)
"""

import json
import os
import time
import logging
import threading
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

log = logging.getLogger(__name__)

# Shared state — set by run.py before starting
store = None
decisions = None
summaries = None
room_settings = None  # set by run.py — dict with "channels" list etc.
registry = None       # set by run.py — RuntimeRegistry instance
config = None         # set by run.py — full config.toml dict
_presence: dict[str, float] = {}
_activity: dict[str, bool] = {}   # True = screen changed on last poll
_activity_ts: dict[str, float] = {}  # timestamp of last active=True heartbeat
ACTIVITY_TIMEOUT = 8  # auto-expire activity after 8s without a fresh active=True
_presence_lock = threading.Lock()   # guards both _presence and _activity
_renamed_from: set[str] = set()    # old names from renames — suppress leave messages
_cursors: dict[str, dict[str, int]] = {}  # agent_name → {channel_name → last_id}
_cursors_lock = threading.Lock()
PRESENCE_TIMEOUT = 10  # ~2 missed heartbeats (5s interval) = offline

# Roles — per-instance, persisted to roles.json
_roles: dict[str, str] = {}  # agent_name → role string
_ROLES_FILE: Path | None = None

# Cursor persistence — set by run.py to enable saving cursors across restarts
_CURSORS_FILE: Path | None = None


def reset_cursors() -> None:
    """Clear all agent read cursors. Call after store.clear() so agents get fresh context."""
    with _cursors_lock:
        _cursors.clear()

_MCP_INSTRUCTIONS = (
    "agentchattr — a shared chat channel for coordinating development between AI agents and humans. "
    "Use chat_send to post messages. Use chat_read to check recent messages. "
    "Use chat_join when you start a session to announce your presence. "
    "Use chat_decision to list or propose project decisions (humans approve via the web UI). "
    "Always use your own name as the sender — never impersonate other agents or humans.\n\n"
    "CRITICAL — Sender Identity Rules:\n"
    "Your BASE agent identity (used for chat_claim and chat_read) is:\n"
    "  - All Anthropic products (Claude Code, claude-cli, etc.) → base: \"claude\"\n"
    "  - All OpenAI products (Codex CLI, codex, chatgpt-cli, etc.) → base: \"codex\"\n"
    "  - All Google products (Gemini CLI, gemini-cli, aistudio, etc.) → base: \"gemini\"\n"
    "  - Humans use their own name (e.g. \"user\")\n"
    "Do NOT use your CLI tool name (e.g. \"gemini-cli\", \"claude-code\") — use the base name above.\n"
    "IMPORTANT: When multiple instances run, the server renames slot 1 (e.g. \"claude\" → \"claude-1\"). "
    "If chat_send rejects your sender, call chat_claim(sender='your_base_name') and use the confirmed_name "
    "as your sender for ALL subsequent tool calls. The confirmed_name overrides the base name.\n\n"
    "CRITICAL — Identity:\n"
    "Always use your base agent name (claude/codex/gemini) as sender. "
    "Do NOT call chat_claim on fresh sessions — it is only for "
    "recovering a previous identity after /resume.\n\n"
    "CRITICAL — Always Respond In Chat:\n"
    "When you are addressed in a chat message (@yourname or @all agents), you MUST respond using chat_send "
    "in the same channel. NEVER respond only in your terminal/console output. The human and other agents "
    "cannot see your terminal — only chat messages are visible to everyone. If you need to do work first, "
    "do the work, then post your response/results in chat using chat_send.\n\n"
    "Decisions are lightweight project memory. They help agents stay aligned on agreed conventions, "
    "architecture choices, and workflow rules. At the start of a session, call chat_decision(action='list') "
    "to read existing approved decisions — treat approved decisions as authoritative guidance. "
    "When you make a significant choice that other agents should follow (e.g. a library pick, naming "
    "convention, or architecture pattern), propose it as a decision so the human can approve it. "
    "Keep decisions short and actionable (max 80 chars). Don't propose trivial or session-specific things.\n\n"
    "Messages belong to channels (default: 'general'). Use the 'channel' parameter in chat_send and "
    "chat_read to target a specific channel. Omit channel or pass empty string to read from all channels.\n\n"
    "If you are addressed in chat, respond in chat — use chat_send to reply in the same channel. "
    "Do not take the answer back to your terminal session. "
    "If the latest message in a channel is addressed to you (or all agents), treat it as your active task "
    "and execute it directly. Reading a channel with no task addressed to you is just catching up — no action needed.\n\n"
    "Multi-instance support:\n"
    "When multiple instances of the same agent run simultaneously, each gets a unique identity.\n"
    "The server assigns names like claude-1, claude-2 automatically.\n"
    "On /resume, if your conversation history shows you previously used a different name (e.g. 'claude-music'), "
    "call chat_claim(sender='your_base_name', name='claude-music') to reclaim it.\n"
    "If chat_send rejects your sender with an identity error, call chat_claim first to get your identity.\n\n"
    "Summaries are per-channel snapshots that help agents catch up quickly. "
    "Use chat_summary(action='read') at session start to get context before reading raw messages. "
    "Use chat_summary(action='write', text='...') to update the summary ONLY when:\n"
    "- A significant task or discussion has concluded\n"
    "- You are explicitly asked via /summary\n"
    "- The channel has had 20+ messages since the last summary\n"
    "Do NOT update the summary mid-conversation, after trivial exchanges, or when another agent just updated it. "
    "Keep summaries factual and concise (under 150 words) — focus on decisions made, tasks completed, and open questions."
)

# --- Tool implementations (shared between both servers) ---


def _request_headers(ctx: Context | None):
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
    except Exception:
        return None
    return getattr(request, "headers", None)


def _extract_agent_token(ctx: Context | None) -> str:
    headers = _request_headers(ctx)
    if not headers:
        return ""
    auth = headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-agent-token", "").strip()


def _authenticated_instance(ctx: Context | None) -> dict | None:
    if not registry:
        return None
    token = _extract_agent_token(ctx)
    if not token:
        return None
    return registry.resolve_token(token)


def _resolve_tool_identity(
    raw_name: str,
    ctx: Context | None,
    *,
    field_name: str,
    required: bool = False,
) -> tuple[str, str | None]:
    provided = raw_name.strip() if raw_name else ""
    token = _extract_agent_token(ctx)
    inst = _authenticated_instance(ctx)
    if inst:
        resolved = inst["name"]
        if resolved:
            _touch_presence(resolved)
        return resolved, None
    if token:
        return "", "Error: stale or unknown authenticated agent session. Re-register and retry."

    if not provided:
        if required:
            return "", f"Error: {field_name} is required."
        return "", None

    if registry:
        resolved = registry.resolve_name(provided)
        if resolved != provided and registry.is_registered(resolved):
            provided = resolved
        if registry.is_agent_family(provided):
            return "", f"Error: authenticated agent session required for '{provided}'."

    if provided:
        _touch_presence(provided)
    return provided, None


def chat_send(
    sender: str,
    message: str,
    image_path: str = "",
    reply_to: int = -1,
    channel: str = "general",
    ctx: Context | None = None,
) -> str:
    """Send a message to the agentchattr chat. Use your name as sender (claude/codex/user).
    Optionally attach a local image by providing image_path (absolute path).
    Optionally reply to a message by providing reply_to (message ID).
    Optionally specify a channel (default: 'general')."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True)
    if err:
        return err
    # Block pending instances (identity not yet confirmed)
    if registry and registry.is_pending(sender):
        return "Error: identity not confirmed. Call chat_claim(sender=your_base_name) to get your identity."
    # Block base family names when multi-instance is active
    # (but allow if sender is a registered+active instance — e.g. slot-1 'claude' that already claimed)
    if registry and sender in registry.get_bases() and registry.family_instance_count(sender) >= 2:
        inst = registry.get_instance(sender)
        if not inst or inst.get("state") != "active":
            return (f"Error: multiple {sender} instances are registered. "
                    f"Call chat_claim(sender='{sender}') to get your unique identity, then use the confirmed_name as sender.")
    # Block unregistered agent names (stale identity from resumed session)
    if registry and registry.is_agent_family(sender) and not registry.is_registered(sender):
        return f"Error: sender '{sender}' is not registered. Call chat_claim(sender=your_base_name) to get your identity."
    if not message.strip() and not image_path:
        return "Empty message, not sent."

    attachments = []
    if image_path:
        import shutil
        import uuid
        from pathlib import Path
        src = Path(image_path)
        if not src.exists():
            return f"Image not found: {image_path}"
        if src.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'):
            return f"Unsupported image type: {src.suffix}"
        
        # Get upload dir from config (fall back to ./uploads)
        raw_dir = "./uploads"
        if config and "images" in config:
            raw_dir = config["images"].get("upload_dir", raw_dir)
        upload_dir = Path(raw_dir)
        
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:8]}{src.suffix}"
        shutil.copy2(str(src), str(upload_dir / filename))
        attachments.append({"name": src.name, "url": f"/uploads/{filename}"})

    reply_id = reply_to if reply_to >= 0 else None
    if reply_id is not None and store.get_by_id(reply_id) is None:
        return f"Message #{reply_to} not found."

    msg = store.add(sender, message.strip(), attachments=attachments, reply_to=reply_id, channel=channel)
    with _presence_lock:
        _presence[sender] = time.time()
    return f"Sent (id={msg['id']})"


def _serialize_messages(msgs: list[dict]) -> str:
    """Serialize store messages into MCP chat_read output shape."""
    out = []
    for m in msgs:
        entry = {
            "id": m["id"],
            "sender": m["sender"],
            "text": m["text"],
            "type": m["type"],
            "time": m["time"],
            "channel": m.get("channel", "general"),
        }
        if m.get("attachments"):
            entry["attachments"] = m["attachments"]
        if m.get("reply_to") is not None:
            entry["reply_to"] = m["reply_to"]
        out.append(entry)
    return json.dumps(out, ensure_ascii=False) if out else "No new messages."


def _load_cursors():
    """Load cursor state from disk (called by run.py after store init)."""
    global _cursors
    if _CURSORS_FILE is None or not _CURSORS_FILE.exists():
        return
    try:
        data = json.loads(_CURSORS_FILE.read_text("utf-8"))
        with _cursors_lock:
            _cursors.update(data)
    except Exception:
        log.warning("Failed to load cursor state from %s", _CURSORS_FILE)


def _save_cursors():
    """Persist cursor state to disk atomically (write temp + rename)."""
    if _CURSORS_FILE is None:
        return
    try:
        with _cursors_lock:
            snapshot = dict(_cursors)
        _CURSORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CURSORS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot), "utf-8")
        os.replace(tmp, _CURSORS_FILE)  # atomic on POSIX
    except Exception:
        log.warning("Failed to save cursor state to %s", _CURSORS_FILE)


def _load_roles():
    """Load persisted roles from disk."""
    global _roles
    if _ROLES_FILE is None or not _ROLES_FILE.exists():
        return
    try:
        _roles = json.loads(_ROLES_FILE.read_text("utf-8"))
    except Exception:
        log.warning("Failed to load roles from %s", _ROLES_FILE)


def _save_roles():
    """Persist roles to disk atomically."""
    if _ROLES_FILE is None:
        return
    try:
        _ROLES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ROLES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_roles), "utf-8")
        os.replace(tmp, _ROLES_FILE)
    except Exception:
        log.warning("Failed to save roles to %s", _ROLES_FILE)


def set_role(name: str, role: str):
    """Set or clear an agent's role. Empty string clears."""
    if role:
        _roles[name] = role
    else:
        _roles.pop(name, None)
    _save_roles()


def get_role(name: str) -> str:
    """Get an agent's current role, or empty string."""
    return _roles.get(name, "")


def get_all_roles() -> dict[str, str]:
    """All active roles."""
    return dict(_roles)


def migrate_identity(old_name: str, new_name: str):
    """Migrate all runtime state when an agent is renamed (presence, cursors, activity, roles)."""
    with _presence_lock:
        if old_name in _presence:
            _presence[new_name] = _presence.pop(old_name)
        if old_name in _activity:
            _activity[new_name] = _activity.pop(old_name)
        if old_name in _activity_ts:
            _activity_ts[new_name] = _activity_ts.pop(old_name)
        _renamed_from.add(old_name)  # suppress leave message for old name
    with _cursors_lock:
        if old_name in _cursors:
            _cursors[new_name] = _cursors.pop(old_name)
    if old_name in _roles:
        _roles[new_name] = _roles.pop(old_name)
        _save_roles()
    _save_cursors()


def purge_identity(name: str):
    """Remove all runtime state for a deregistered agent (presence, activity, cursors, roles)."""
    with _presence_lock:
        _presence.pop(name, None)
        _activity.pop(name, None)
        _activity_ts.pop(name, None)
    with _cursors_lock:
        _cursors.pop(name, None)
    if name in _roles:
        del _roles[name]
        _save_roles()
    _save_cursors()


def migrate_cursors_rename(old_name: str, new_name: str):
    """Move cursor entries from old channel name to new channel name."""
    with _cursors_lock:
        for agent_cursors in _cursors.values():
            if old_name in agent_cursors:
                agent_cursors[new_name] = agent_cursors.pop(old_name)
    _save_cursors()


def migrate_cursors_delete(channel: str):
    """Remove cursor entries for a deleted channel."""
    with _cursors_lock:
        for agent_cursors in _cursors.values():
            agent_cursors.pop(channel, None)
    _save_cursors()


def _update_cursor(sender: str, msgs: list[dict], channel: str | None):
    if sender and msgs:
        ch_key = channel if channel else "__all__"
        with _cursors_lock:
            agent_cursors = _cursors.setdefault(sender, {})
            agent_cursors[ch_key] = msgs[-1]["id"]
        _save_cursors()


def chat_read(
    sender: str = "",
    since_id: int = 0,
    limit: int = 20,
    channel: str = "",
    ctx: Context | None = None,
) -> str:
    """Read chat messages. Returns JSON array with: id, sender, text, type, time, channel.

    Smart defaults:
    - First call with sender: returns last `limit` messages (full context).
    - Subsequent calls with same sender: returns only NEW messages since last read.
    - Pass since_id to override and read from a specific point.
    - Omit sender to always get the last `limit` messages (no cursor).
    - Pass channel to filter by channel name (default: all channels)."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=False)
    if err:
        return err
    ch = channel if channel else None
    if since_id:
        msgs = store.get_since(since_id, channel=ch)
    elif sender:
        ch_key = ch if ch else "__all__"
        with _cursors_lock:
            agent_cursors = _cursors.get(sender, {})
            cursor = agent_cursors.get(ch_key, 0)
        if cursor:
            msgs = store.get_since(cursor, channel=ch)
        else:
            msgs = store.get_recent(limit, channel=ch)
    else:
        msgs = store.get_recent(limit, channel=ch)

    msgs = msgs[-limit:]
    _update_cursor(sender, msgs, ch)
    serialized = _serialize_messages(msgs)
    # Prepend identity breadcrumb if multi-instance
    if sender and registry and registry.is_registered(sender):
        multi = registry.family_instance_count(sender) >= 2
        if multi:
            inst = registry.get_instance(sender)
            if inst:
                breadcrumb = f"[identity: {inst['name']} | label: {inst['label']}]"
                serialized = f"{breadcrumb}\n{serialized}"
    return serialized


def chat_resync(
    sender: str,
    limit: int = 50,
    channel: str = "",
    ctx: Context | None = None,
) -> str:
    """Explicit full-context fetch.

    Returns the latest `limit` messages and resets the sender cursor
    to the latest returned message id.
    Pass channel to filter by channel name (default: all channels).
    """
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True)
    if err:
        return err
    ch = channel if channel else None
    msgs = store.get_recent(limit, channel=ch)
    _update_cursor(sender, msgs, ch)
    serialized = _serialize_messages(msgs)
    return serialized


def chat_join(name: str, channel: str = "general", ctx: Context | None = None) -> str:
    """Announce that you've connected to agentchattr."""
    name, err = _resolve_tool_identity(name, ctx, field_name="name", required=True)
    if err:
        return err
    # Block pending instances (identity not yet confirmed)
    if registry and registry.is_pending(name):
        return "Error: identity not confirmed. Call chat_claim(sender=your_base_name) to get your identity."
    # Block base family names when multi-instance is active
    # (but allow if name is a registered+active instance — e.g. slot-1 'claude' that already claimed)
    if registry and name in registry.get_bases() and registry.family_instance_count(name) >= 2:
        inst = registry.get_instance(name)
        if not inst or inst.get("state") != "active":
            return (f"Error: multiple {name} instances registered. "
                    f"Call chat_claim(sender='{name}') to get your unique identity first.")
    # Block unregistered agent names (stale identity from resumed session)
    if registry and registry.is_agent_family(name) and not registry.is_registered(name):
        return f"Error: '{name}' is not registered. Call chat_claim(sender=your_base_name) to get your identity."
    # Only post join to general — don't spam topic channels
    store.add(name, f"{name} is online", msg_type="join", channel="general")
    online = _get_online()
    return f"Joined. Online: {', '.join(online)}"


def chat_who() -> str:
    """Check who's currently online in agentchattr."""
    online = _get_online()
    return f"Online: {', '.join(online)}" if online else "Nobody online."


def _touch_presence(name: str):
    """Update presence timestamp — called on any MCP tool use."""
    with _presence_lock:
        _presence[name] = time.time()


def _get_online() -> list[str]:
    now = time.time()
    with _presence_lock:
        return [name for name, ts in _presence.items()
                if now - ts < PRESENCE_TIMEOUT]


def is_online(name: str) -> bool:
    now = time.time()
    with _presence_lock:
        return name in _presence and now - _presence.get(name, 0) < PRESENCE_TIMEOUT


def set_active(name: str, active: bool):
    with _presence_lock:
        _activity[name] = active
        if active:
            _activity_ts[name] = __import__("time").time()


def is_active(name: str) -> bool:
    import time as _time
    with _presence_lock:
        if not _activity.get(name, False):
            return False
        # Auto-expire stale activity
        ts = _activity_ts.get(name, 0)
        if _time.time() - ts > ACTIVITY_TIMEOUT:
            _activity[name] = False
            return False
        return True


def chat_decision(
    action: str,
    sender: str,
    decision: str = "",
    reason: str = "",
    ctx: Context | None = None,
) -> str:
    """Manage project decisions. Agents can list and propose; humans approve via the web UI.

    Actions:
      - list: Returns all decisions (proposed + approved).
      - propose: Propose a new decision for human approval. Requires decision text + sender.

    Agents cannot approve, edit, or delete decisions — only humans can do that from the web UI."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=False)
    if err:
        return err
    action = action.strip().lower()

    if action == "list":
        items = decisions.list_all()
        if not items:
            return "No decisions yet."
        return json.dumps(items, ensure_ascii=False)

    if action == "propose":
        if not decision.strip():
            return "Error: decision text is required."
        if not sender.strip():
            return "Error: sender is required."
        result = decisions.propose(decision, sender, reason)
        if result is None:
            return "Error: max 30 decisions reached."
        return f"Proposed decision #{result['id']}: {result['decision']}"

    if action in ("approve", "edit", "delete"):
        return f"Error: '{action}' is only available to humans via the web UI."

    return f"Unknown action: {action}. Valid actions: list, propose."


# --- Server instances ---

def chat_set_hat(sender: str, svg: str, target: str = "", ctx: Context | None = None) -> str:
    """Set your avatar hat. Pass an SVG string (viewBox "0 0 32 16", max 5KB).
    The hat will appear above your avatar in chat. To remove, users can drag it to the trash.
    Color context for design — chat bg is dark (#0f0f17), avatar colors: claude=#da7756 (coral), codex=#10a37f (green), gemini=#4285f4 (blue).
    Optional: pass target to set a hat on another agent (e.g. target="qwen")."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True)
    if err:
        return err
    hat_owner = target.strip() if target.strip() else sender
    import app
    err = app.set_agent_hat(hat_owner, svg)
    if err:
        return f"Error: {err}"
    if hat_owner != sender:
        return f"Hat set for {hat_owner} (by {sender})!"
    return f"Hat set for {sender}!"


def chat_claim(sender: str, name: str = "", ctx: Context | None = None) -> str:
    """Claim your identity in a multi-instance setup.

    - Without name: accept the auto-assigned identity and unlock chat_send.
    - With name: reclaim a previous identity (e.g. from a breadcrumb after /resume).

    Your sender must be your current registered name (the one assigned at registration).
    The identity breadcrumb in chat_read responses shows your current identity."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True)
    if err:
        return err
    if not registry:
        return "Error: registry not available."
    target = name.strip() if name.strip() else None
    result = registry.claim(sender, target)
    if isinstance(result, str):
        return f"Error: {result}"
    # Touch presence with the CONFIRMED name (may differ from sender)
    confirmed = result.get("name", sender)
    _touch_presence(confirmed)
    return json.dumps({"confirmed_name": confirmed, "label": result.get("label", ""), "base": result.get("base", "")})


def chat_channels() -> str:
    """List all available channels. Returns a JSON array of channel names."""
    channels = room_settings.get("channels", ["general"]) if room_settings else ["general"]
    return json.dumps(channels)


def chat_summary(
    action: str,
    sender: str,
    text: str = "",
    channel: str = "",
    ctx: Context | None = None,
) -> str:
    """Read or write per-channel summaries. Summaries help agents catch up quickly.

    Actions:
      - read: Get the current summary for a channel (default: sender's last active channel).
      - write: Update the channel summary. Requires text (max 1000 chars).

    Keep summaries factual and concise (under 150 words). Focus on decisions made,
    tasks completed, and open questions."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=False)
    if err:
        return err
    action = action.strip().lower()
    channel = (channel or "general").strip()

    if action == "read":
        entry = summaries.get(channel)
        if not entry:
            return json.dumps({"channel": channel, "text": None, "message": f"No summary for #{channel} yet — one hasn't been written."})
        return json.dumps(entry, ensure_ascii=False)

    if action == "write":
        if not text.strip():
            return "Error: text is required."
        if len(text.strip()) > 1000:
            return "Error: summary too long (max 1000 characters)."
        # Get the latest message ID for staleness tracking
        latest_id = 0
        if store:
            recent = store.get_recent(1, channel=channel)
            if recent:
                latest_id = recent[-1]["id"]
        result = summaries.write(channel, text, sender, message_id=latest_id)
        if result is None:
            return "Error: failed to write summary."
        return f"Summary for #{channel} updated ({len(text.strip())} chars)."

    return f"Unknown action: {action}. Valid actions: read, write."


_ALL_TOOLS = [
    chat_send, chat_read, chat_resync, chat_join, chat_who, chat_decision,
    chat_channels, chat_set_hat, chat_claim, chat_summary,
]


def _create_server(port: int) -> FastMCP:
    server = FastMCP(
        "agentchattr",
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        instructions=_MCP_INSTRUCTIONS,
    )
    for func in _ALL_TOOLS:
        server.tool()(func)
    return server


mcp_http = _create_server(8200)  # streamable-http for Claude/Codex
mcp_sse = _create_server(8201)   # SSE for Gemini

# Keep backward compat — run.py references mcp_bridge.store
# (store is set by run.py before starting)


def run_http_server():
    """Block — run streamable-http MCP in a background thread."""
    mcp_http.run(transport="streamable-http")


def run_sse_server():
    """Block — run SSE MCP in a background thread."""
    mcp_sse.run(transport="sse")

