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
from decisions import DecisionStore
from router import Router
from agents import AgentTrigger

log = logging.getLogger(__name__)

app = FastAPI(title="agentchattr")

# --- globals (set by configure()) ---
store: MessageStore | None = None
decisions: DecisionStore | None = None
router: Router | None = None
agents: AgentTrigger | None = None
config: dict = {}
ws_clients: set[WebSocket] = set()

# --- Security: session token (set by configure()) ---
session_token: str = ""

# Room settings (persisted to data/settings.json)
room_settings: dict = {
    "title": "agentchattr",
    "username": "user",
    "font": "mono",
    "max_agent_hops": 4,
    "channels": ["general"],
    "history_limit": "all",
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
            if path == "/" or path.startswith(("/static/", "/uploads/", "/api/heartbeat/")):
                return await call_next(request)

            # --- Origin check (blocks cross-origin / DNS-rebinding attacks) ---
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": "forbidden: origin not allowed"},
                    status_code=403,
                )

            # --- Token check ---
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
    global store, decisions, router, agents, config
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
    decisions = DecisionStore(str(Path(data_dir) / "decisions.json"))
    decisions.on_change(_on_decision_change)

    max_hops = cfg.get("routing", {}).get("max_agent_hops", 4)

    agent_names = list(cfg.get("agents", {}).keys())
    router = Router(
        agent_names=agent_names,
        default_mention=cfg.get("routing", {}).get("default", "none"),
        max_hops=max_hops,
    )
    agents = AgentTrigger(cfg.get("agents", {}), data_dir=data_dir)

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

            # Presence expiry — post leave messages for agents that went offline
            try:
                now = _time.time()
                with mcp_bridge._presence_lock:
                    currently_online = {
                        name for name, ts in mcp_bridge._presence.items()
                        if now - ts < mcp_bridge.PRESENCE_TIMEOUT
                    }
                # Detect agents that were online but are no longer
                went_offline = _known_online - currently_online
                came_online = currently_online - _known_online
                for name in went_offline:
                    # Post leave in all channels so every agent sees it
                    channels = room_settings.get("channels", ["general"])
                    for ch in channels:
                        store.add(name, f"{name} disconnected", msg_type="leave", channel=ch)
                    if _event_loop:
                        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)
                _known_online.clear()
                _known_online.update(currently_online)
            except Exception:
                pass

    threading.Thread(target=_background_checks, daemon=True).start()


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


def _on_decision_change(action: str, decision: dict):
    """Called from any thread when a decision changes."""
    if _event_loop is None:
        return
    try:
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(broadcast_decision(action, decision))
            return
    except RuntimeError:
        pass
    asyncio.run_coroutine_threadsafe(broadcast_decision(action, decision), _event_loop)


async def _handle_new_message(msg: dict):
    """Broadcast message to web clients + check for @mention triggers."""
    # For broadcast slash commands, suppress the raw message — only the expanded
    # version should appear. Delete from store if it was persisted (MCP path),
    # and skip broadcasting the raw text.
    text = msg.get("text", "")
    # Strip @mentions to find the slash command (e.g. "@claude @codex /hatmaking")
    stripped = _re.sub(r"@\w+\s*", "", text).strip().lower()
    _broadcast_cmds = ("/hatmaking", "/artchallenge", "/roastreview", "/poetry")
    cmd_word = stripped.split()[0] if stripped else ""
    is_broadcast_cmd = cmd_word in _broadcast_cmds

    if not is_broadcast_cmd:
        await broadcast(msg)

    # If the raw slash command was persisted (MCP path), silently remove it.
    # It was never broadcast to WebSocket clients, so no delete event needed.
    if is_broadcast_cmd and msg.get("id"):
        store.delete([msg["id"]])

    # System messages never trigger routing — prevents infinite callback loops
    sender = msg.get("sender", "")
    channel = msg.get("channel", "general")
    if sender == "system":
        return

    # Check for slash commands — use stripped text (sans @mentions)
    if stripped == "/continue":
        router.continue_routing(channel)
        store.add("system", f"Routing resumed by {sender}.", channel=channel)
        await broadcast_status()
        return

    if stripped == "/roastreview":
        agent_names = list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        store.add(sender, f"{mentions} Time for a roast review! Inspect each other's work and constructively roast it.", channel=channel)
        return

    if stripped.startswith("/artchallenge"):
        parts = stripped.split(None, 1)
        theme = parts[1] if len(parts) > 1 else "anything you like"
        agent_names = list(config.get("agents", {}).keys())
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
        agent_names = list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        agents_cfg = config.get("agents", {})
        color_parts = ", ".join(
            f"{a}={agents_cfg[a].get('color', '#888')}" for a in agent_names if a in agents_cfg
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
        agent_names = list(config.get("agents", {}).keys())
        mentions = " ".join(f"@{a}" for a in agent_names)
        prompts = {
            "haiku": "Write a haiku about the current state of this codebase.",
            "limerick": "Write a limerick about the current state of this codebase.",
            "sonnet": "Write a sonnet about the current state of this codebase.",
        }
        store.add(sender, f"{mentions} {prompts[form]}", channel=channel)
        return

    targets = router.get_targets(sender, text, channel)

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

    import mcp_bridge
    for target in targets:
        if not mcp_bridge.is_online(target):
            store.add("system", f"{target} appears offline — message queued.", msg_type="system", channel=channel)
        if agents.is_available(target):
            await agents.trigger(target, message=chat_msg, channel=channel)


# --- broadcasting ---

async def broadcast(msg: dict):
    data = json.dumps({"type": "message", "data": msg})
    dead = set()
    for client in ws_clients:
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
    for client in ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_typing(agent_name: str, is_typing: bool):
    data = json.dumps({"type": "typing", "agent": agent_name, "active": is_typing})
    dead = set()
    for client in ws_clients:
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
    for client in ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_todo_update(msg_id: int, status: str | None):
    data = json.dumps({"type": "todo_update", "data": {"id": msg_id, "status": status}})
    dead = set()
    for client in ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_settings():
    data = json.dumps({"type": "settings", "data": room_settings})
    dead = set()
    for client in ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_decision(action: str, decision: dict):
    data = json.dumps({"type": "decision", "action": action, "data": decision})
    dead = set()
    for client in ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


async def broadcast_hats():
    data = json.dumps({"type": "hats", "data": agent_hats})
    dead = set()
    for client in ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.add(client)
    ws_clients.difference_update(dead)


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # --- Security: validate session token on WebSocket connect ---
    token = websocket.query_params.get("token", "")
    if token != session_token:
        await websocket.close(code=4003, reason="forbidden: invalid session token")
        return

    await websocket.accept()
    ws_clients.add(websocket)

    # Send settings
    await websocket.send_text(json.dumps({"type": "settings", "data": room_settings}))

    # Send agent config (names, colors, labels) so UI can build pills + color mentions
    agent_cfg = {
        name: {"color": cfg.get("color", "#888"), "label": cfg.get("label", name)}
        for name, cfg in config.get("agents", {}).items()
    }
    await websocket.send_text(json.dumps({"type": "agents", "data": agent_cfg}))

    # Send todos {msg_id: status}
    await websocket.send_text(json.dumps({"type": "todos", "data": store.get_todos()}))

    # Send decisions
    await websocket.send_text(json.dumps({"type": "decisions", "data": decisions.list_all()}))

    # Send hats
    await websocket.send_text(json.dumps({"type": "hats", "data": agent_hats}))

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
                store.add(sender, text, attachments=attachments, reply_to=reply_to, channel=channel)

            elif event.get("type") == "delete":
                ids = event.get("ids", [])
                if ids:
                    deleted = store.delete([int(i) for i in ids])
                    if deleted:
                        data = json.dumps({"type": "delete", "ids": deleted})
                        dead = set()
                        for client in ws_clients:
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

            elif event.get("type") == "decision_propose":
                text = event.get("decision", "").strip()
                owner = event.get("owner") or room_settings.get("username", "user")
                reason = event.get("reason", "")
                if text:
                    decisions.propose(text, owner, reason)
                continue

            elif event.get("type") == "decision_approve":
                did = event.get("id")
                if did is not None:
                    decisions.approve(int(did))
                continue

            elif event.get("type") == "decision_unapprove":
                did = event.get("id")
                if did is not None:
                    decisions.unapprove(int(did))
                continue

            elif event.get("type") == "decision_edit":
                did = event.get("id")
                if did is not None:
                    decisions.edit(
                        int(did),
                        decision=event.get("decision"),
                        reason=event.get("reason"),
                    )
                continue

            elif event.get("type") == "decision_delete":
                did = event.get("id")
                if did is not None:
                    decisions.delete(int(did))
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
async def get_messages(since_id: int = 0, limit: int = 50):
    if since_id:
        return store.get_since(since_id)
    return store.get_recent(limit)



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


@app.post("/api/heartbeat/{agent_name}")
async def heartbeat(agent_name: str):
    """Wrapper calls this every 60s to keep presence alive."""
    import mcp_bridge
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[agent_name] = __import__("time").time()
    return {"ok": True}


# --- Open agent session in terminal ---

@app.post("/api/open-session/{agent_name}")
async def open_session(agent_name: str):
    """Spawn a terminal window with the agent's resume command."""
    if agent_name not in config.get("agents", {}):
        return JSONResponse({"error": f"Unknown agent: {agent_name}"}, status_code=404)

    session_id = agents._sessions.get(agent_name)
    if not session_id:
        return JSONResponse({"error": f"No session for {agent_name} yet."}, status_code=404)

    agent_cfg = config["agents"][agent_name]
    cwd = agent_cfg.get("cwd", ".")
    cmd_name = agent_cfg.get("command", agent_name)

    # Build resume command from config, defaulting to --resume
    resume_flag = agent_cfg.get("resume_flag", "--resume")
    resume_cmd = f"{cmd_name} {resume_flag} {session_id}"

    import os, subprocess
    clean_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    if sys.platform == "win32":
        try:
            subprocess.Popen(
                ["wt", "-d", cwd, "--", "cmd", "/k", resume_cmd],
                env=clean_env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            # Security: use argument list instead of shell=True to prevent injection.
            subprocess.Popen(
                ["cmd", "/k", f"cd /d {cwd} && {resume_cmd}"],
                env=clean_env,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
    else:
        for term_cmd in [
            ["gnome-terminal", "--working-directory", cwd, "--", "bash", "-c", f"{resume_cmd}; exec bash"],
            ["open", "-a", "Terminal", cwd],
        ]:
            try:
                subprocess.Popen(term_cmd, env=clean_env)
                break
            except FileNotFoundError:
                continue

    return JSONResponse({"ok": True, "session_id": session_id, "command": resume_cmd})


@app.post("/api/open-path")
async def open_path(body: dict):
    """Open a file or directory in Windows Explorer.

    Security note: This endpoint is intended for local-only use (127.0.0.1).
    It calls explorer.exe to reveal/open the given path. Do not expose this
    server on a public network without additional access controls.
    """
    import subprocess

    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "no path"}, status_code=400)

    p = Path(path)
    try:
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif p.is_dir():
            subprocess.Popen(["explorer", str(p)])
        else:
            return JSONResponse({"error": "path not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"ok": True})


# Serve uploaded images
@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    filepath = (upload_dir / filename).resolve()
    if not filepath.is_relative_to(upload_dir.resolve()):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if filepath.exists():
        return FileResponse(filepath)
    return JSONResponse({"error": "not found"}, status_code=404)

