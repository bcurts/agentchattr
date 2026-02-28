"""MCP server for agent chat tools — runs alongside the web server.

Serves two transports for compatibility:
  - streamable-http on port 8200 (Claude Code, Codex)
  - SSE on port 8201 (Gemini)
"""

import json
import time
import logging
import threading

from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

# Shared state — set by run.py before starting
store = None
decisions = None
room_settings = None  # set by run.py — dict with "channels" list etc.
_presence: dict[str, float] = {}
_presence_lock = threading.Lock()
_cursors: dict[str, dict[str, int]] = {}  # agent_name → {channel_name → last_id}
_cursors_lock = threading.Lock()
PRESENCE_TIMEOUT = 120  # 2 missed heartbeats (60s interval) = offline

_MCP_INSTRUCTIONS = (
    "agentchattr — a shared chat channel for coordinating development between AI agents and humans. "
    "Use chat_send to post messages. Use chat_read to check recent messages. "
    "Use chat_join when you start a session to announce your presence. "
    "Use chat_decision to list or propose project decisions (humans approve via the web UI). "
    "Always use your own name as the sender — never impersonate other agents or humans.\n\n"
    "CRITICAL — Sender Identity Rules:\n"
    "Your sender name MUST match your canonical agent identity, regardless of what CLI tool you are running in:\n"
    "  - All Anthropic products (Claude Code, claude-cli, etc.) → sender: \"claude\"\n"
    "  - All OpenAI products (Codex CLI, codex, chatgpt-cli, etc.) → sender: \"codex\"\n"
    "  - All Google products (Gemini CLI, gemini-cli, aistudio, etc.) → sender: \"gemini\"\n"
    "  - Humans use their own name (e.g. \"ben\")\n"
    "Do NOT use your CLI tool name (e.g. \"gemini-cli\", \"claude-code\") — use the canonical agent name above. "
    "This applies to ALL tools: chat_send, chat_join, chat_read, chat_set_hat, chat_decision, etc.\n\n"
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
    "and execute it directly. Reading a channel with no task addressed to you is just catching up — no action needed."
)

# --- Tool implementations (shared between both servers) ---


def chat_send(sender: str, message: str, image_path: str = "", reply_to: int = -1, channel: str = "general") -> str:
    """Send a message to the agentchattr chat. Use your name as sender (claude/codex/ben).
    Optionally attach a local image by providing image_path (absolute path).
    Optionally reply to a message by providing reply_to (message ID).
    Optionally specify a channel (default: 'general')."""
    if sender:
        _touch_presence(sender)
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
        upload_dir = Path("./uploads")
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


def migrate_cursors_rename(old_name: str, new_name: str):
    """Move cursor entries from old channel name to new channel name."""
    with _cursors_lock:
        for agent_cursors in _cursors.values():
            if old_name in agent_cursors:
                agent_cursors[new_name] = agent_cursors.pop(old_name)


def migrate_cursors_delete(channel: str):
    """Remove cursor entries for a deleted channel."""
    with _cursors_lock:
        for agent_cursors in _cursors.values():
            agent_cursors.pop(channel, None)


def _update_cursor(sender: str, msgs: list[dict], channel: str | None):
    if sender and msgs:
        ch_key = channel if channel else "__all__"
        with _cursors_lock:
            agent_cursors = _cursors.setdefault(sender, {})
            agent_cursors[ch_key] = msgs[-1]["id"]


def chat_read(sender: str = "", since_id: int = 0, limit: int = 20, channel: str = "") -> str:
    """Read chat messages. Returns JSON array with: id, sender, text, type, time, channel.

    Smart defaults:
    - First call with sender: returns last `limit` messages (full context).
    - Subsequent calls with same sender: returns only NEW messages since last read.
    - Pass since_id to override and read from a specific point.
    - Omit sender to always get the last `limit` messages (no cursor).
    - Pass channel to filter by channel name (default: all channels)."""
    if sender:
        _touch_presence(sender)
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
    return _serialize_messages(msgs)


def chat_resync(sender: str, limit: int = 50, channel: str = "") -> str:
    """Explicit full-context fetch.

    Returns the latest `limit` messages and resets the sender cursor
    to the latest returned message id.
    Pass channel to filter by channel name (default: all channels).
    """
    if not sender.strip():
        return "Error: sender is required for chat_resync."
    _touch_presence(sender)
    ch = channel if channel else None
    msgs = store.get_recent(limit, channel=ch)
    _update_cursor(sender, msgs, ch)
    return _serialize_messages(msgs)


def chat_join(name: str, channel: str = "general") -> str:
    """Announce that you've connected to agentchattr."""
    _touch_presence(name)
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


def chat_decision(action: str, sender: str, decision: str = "", reason: str = "") -> str:
    """Manage project decisions. Agents can list and propose; humans approve via the web UI.

    Actions:
      - list: Returns all decisions (proposed + approved).
      - propose: Propose a new decision for human approval. Requires decision text + sender.

    Agents cannot approve, edit, or delete decisions — only humans can do that from the web UI."""
    if sender:
        _touch_presence(sender)
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

def chat_set_hat(sender: str, svg: str) -> str:
    """Set your avatar hat. Pass an SVG string (viewBox "0 0 32 16", max 5KB).
    The hat will appear above your avatar in chat. To remove, users can drag it to the trash.
    Color context for design — chat bg is dark (#0f0f17), avatar colors: claude=#da7756 (coral), codex=#10a37f (green), gemini=#4285f4 (blue)."""
    if not sender.strip():
        return "Error: sender is required."
    _touch_presence(sender)
    import app
    err = app.set_agent_hat(sender, svg)
    if err:
        return f"Error: {err}"
    return f"Hat set for {sender}!"


def chat_channels() -> str:
    """List all available channels. Returns a JSON array of channel names."""
    channels = room_settings.get("channels", ["general"]) if room_settings else ["general"]
    return json.dumps(channels)


_ALL_TOOLS = [
    chat_send, chat_read, chat_resync, chat_join, chat_who, chat_decision, chat_channels, chat_set_hat,
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

