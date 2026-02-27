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
_presence: dict[str, float] = {}
_presence_lock = threading.Lock()
_cursors: dict[str, int] = {}  # agent_name → last seen message id
_cursors_lock = threading.Lock()
PRESENCE_TIMEOUT = 300

_MCP_INSTRUCTIONS = (
    "agentchattr — a shared chat channel for coordinating development between AI agents and humans. "
    "Use chat_send to post messages. Use chat_read to check recent messages. "
    "Use chat_join when you start a session to announce your presence. "
    "Use chat_decision to list or propose project decisions (humans approve via the web UI). "
    "Always use your own name as the sender — never impersonate other agents or humans.\n\n"
    "Decisions are lightweight project memory. They help agents stay aligned on agreed conventions, "
    "architecture choices, and workflow rules. At the start of a session, call chat_decision(action='list') "
    "to read existing approved decisions — treat approved decisions as authoritative guidance. "
    "When you make a significant choice that other agents should follow (e.g. a library pick, naming "
    "convention, or architecture pattern), propose it as a decision so the human can approve it. "
    "Keep decisions short and actionable (max 80 chars). Don't propose trivial or session-specific things."
)

# --- Tool implementations (shared between both servers) ---


def chat_send(sender: str, message: str, image_path: str = "", reply_to: int = -1) -> str:
    """Send a message to the agentchattr chat. Use your name as sender (claude/codex/ben).
    Optionally attach a local image by providing image_path (absolute path).
    Optionally reply to a message by providing reply_to (message ID)."""
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
        if src.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
            return f"Unsupported image type: {src.suffix}"
        upload_dir = Path("./uploads")
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:8]}{src.suffix}"
        shutil.copy2(str(src), str(upload_dir / filename))
        attachments.append({"name": src.name, "url": f"/uploads/{filename}"})

    reply_id = reply_to if reply_to >= 0 else None
    if reply_id is not None and store.get_by_id(reply_id) is None:
        return f"Message #{reply_to} not found."

    msg = store.add(sender, message.strip(), attachments=attachments, reply_to=reply_id)
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
        }
        if m.get("attachments"):
            entry["attachments"] = m["attachments"]
        if m.get("reply_to") is not None:
            entry["reply_to"] = m["reply_to"]
        out.append(entry)
    return json.dumps(out, indent=2, ensure_ascii=False) if out else "No new messages."


def _update_cursor(sender: str, msgs: list[dict]):
    if sender and msgs:
        with _cursors_lock:
            _cursors[sender] = msgs[-1]["id"]


def chat_read(sender: str = "", since_id: int = 0, limit: int = 20) -> str:
    """Read chat messages. Returns JSON array with: id, sender, text, type, time.

    Smart defaults:
    - First call with sender: returns last `limit` messages (full context).
    - Subsequent calls with same sender: returns only NEW messages since last read.
    - Pass since_id to override and read from a specific point.
    - Omit sender to always get the last `limit` messages (no cursor)."""
    if since_id:
        msgs = store.get_since(since_id)
    elif sender:
        with _cursors_lock:
            cursor = _cursors.get(sender, 0)
        if cursor:
            msgs = store.get_since(cursor)
        else:
            msgs = store.get_recent(limit)
    else:
        msgs = store.get_recent(limit)

    msgs = msgs[-limit:]
    _update_cursor(sender, msgs)
    return _serialize_messages(msgs)


def chat_resync(sender: str, limit: int = 50) -> str:
    """Explicit full-context fetch.

    Returns the latest `limit` messages and resets the sender cursor
    to the latest returned message id.
    """
    if not sender.strip():
        return "Error: sender is required for chat_resync."
    msgs = store.get_recent(limit)
    _update_cursor(sender, msgs)
    return _serialize_messages(msgs)


def chat_join(name: str) -> str:
    """Announce that you've connected to agentchattr."""
    with _presence_lock:
        _presence[name] = time.time()
    store.add(name, f"{name} connected", msg_type="join")
    online = _get_online()
    return f"Joined. Online: {', '.join(online)}"


def chat_who() -> str:
    """Check who's currently online in agentchattr."""
    online = _get_online()
    return f"Online: {', '.join(online)}" if online else "Nobody online."


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
    action = action.strip().lower()

    if action == "list":
        items = decisions.list_all()
        if not items:
            return "No decisions yet."
        return json.dumps(items, indent=2, ensure_ascii=False)

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

_ALL_TOOLS = [
    chat_send, chat_read, chat_resync, chat_join, chat_who, chat_decision,
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

