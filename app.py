"""agentchattr — FastAPI web UI + agent auto-trigger."""

import asyncio
import json
import os
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
from projects import ProjectManager
from router import Router
from agents import AgentTrigger
from tmux_cleanup import SessionCleanup

log = logging.getLogger(__name__)

app = FastAPI(title="agentchattr")

# --- globals (set by configure()) ---
project_manager: ProjectManager | None = None
store: MessageStore | None = None
router: Router | None = None
agents: AgentTrigger | None = None
config: dict = {}
ws_clients: set[WebSocket] = set()

# --- Security: session token (set by configure()) ---
session_token: str = ""
access_token: str = ""

# Room settings (persisted to data/settings.json)
room_settings: dict = {
    "title": "agentchattr",
    "username": "user",
    "font": "mono",
    "max_agent_hops": 10,
}


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


def _save_settings():
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(room_settings, indent=2), "utf-8")


def _todo_path() -> Path:
    return Path(__file__).with_name("TODO.md")


def _parse_task_owner(owner_line: str) -> tuple[str, str]:
    """Parse '- **Owner:** status - agent (notes)' into (status, agent)."""
    statuses = ("Pending", "In Progress", "Review", "Done")
    raw = owner_line.strip()
    for status in statuses:
        if not raw.startswith(status):
            continue
        owner = ""
        prefix = f"{status} - "
        if raw.startswith(prefix):
            owner = raw[len(prefix):].strip()
            # Stop at first separator: space, paren, or comma
            for sep in (" (", ","):
                idx = owner.find(sep)
                if idx != -1:
                    owner = owner[:idx].strip()
                    break
        return status, owner
    return raw, ""


def _parse_todo_tasks(text: str) -> list[dict[str, str]]:
    """Parse TODO.md backlog section into JSON-ready list of task dicts."""
    tasks: list[dict[str, str]] = []
    lines = text.splitlines()
    in_backlog = False
    current: dict[str, str] | None = None

    for line in lines:
        stripped = line.strip()

        if stripped == "## Backlog":
            in_backlog = True
            continue
        if not in_backlog:
            continue

        # Stop at next H2
        if stripped.startswith("## "):
            break

        # New task
        if stripped.startswith("### "):
            if current and current.get("title"):
                tasks.append(current)
            current = {
                "title": stripped[4:].strip(),
                "owner": "",
                "status": "Pending",
                "branch": "",
            }
            continue

        if not current:
            continue

        if stripped.startswith("- **Owner:**"):
            owner_line = stripped.removeprefix("- **Owner:**").strip()
            status, owner = _parse_task_owner(owner_line)
            current["status"] = status
            current["owner"] = owner
        elif stripped.startswith("- **Branch:**"):
            current["branch"] = stripped.removeprefix("- **Branch:**").strip()

    if current and current.get("title"):
        tasks.append(current)

    return tasks


# --- Security middleware ---
# Paths that don't require the session token (public assets).
_PUBLIC_PREFIXES = ("/", "/static/")


def _load_access_token() -> str:
    return os.environ.get("ACCESS_TOKEN", "").strip()


def _access_token_valid(configured_token: str, query_token: str = "", header_token: str = "") -> bool:
    """ACCESS_TOKEN is optional; when unset, all requests are allowed."""
    if not configured_token:
        return True
    return query_token == configured_token or header_token == configured_token


def _install_security_middleware(token: str, cfg: dict, ngrok_token: str = ""):
    """Add token validation and origin checking middleware to the app."""
    import app as _self
    _self.session_token = token
    _self.access_token = ngrok_token
    port = cfg.get("server", {}).get("port", 8300)
    allowed_origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    }

    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path

            # Static assets, uploads, the root page, and MCP endpoints are
            # exempt from session-token gating. MCP has its own session
            # management; blocking /mcp initialization prevents agents from
            # connecting at all.
            if (path in ("/", "/favicon.ico")
                    or path.startswith(("/static/", "/uploads/", "/mcp", "/sse"))):
                return await call_next(request)

            if not _access_token_valid(
                _self.access_token,
                request.query_params.get("token", ""),
                request.headers.get("x-access-token", ""),
            ):
                return JSONResponse(
                    {"error": "forbidden: invalid or missing access token"},
                    status_code=403,
                )

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
    global project_manager, store, router, agents, config
    config = cfg

    # --- Security: store the session token and install middleware ---
    _install_security_middleware(session_token, cfg, _load_access_token())

    data_dir = cfg.get("server", {}).get("data_dir", "./data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    project_manager = ProjectManager(data_dir)
    store = project_manager.get_store()

    # Bridge: expose store and project_manager to MCP
    import mcp_bridge
    mcp_bridge.store = store
    mcp_bridge.project_manager = project_manager

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

    # Apply saved loop guard setting
    if "max_agent_hops" in room_settings:
        router.max_hops = room_settings["max_agent_hops"]

    # Start background cleanup for stale tmux sessions
    cleanup = SessionCleanup(config, store=store)
    cleanup.start()

    # Background thread: check for wrapper recovery flag files
    _data_dir = Path(data_dir)

    def _check_recovery_flags():
        import time as _time
        while True:
            _time.sleep(3)
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

    threading.Thread(target=_check_recovery_flags, daemon=True).start()


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


async def _handle_new_message(msg: dict):
    """Broadcast message to web clients + check for @mention triggers."""
    await broadcast(msg)

    # System messages never trigger routing — prevents infinite callback loops
    sender = msg.get("sender", "")
    text = msg.get("text", "")
    if sender == "system":
        return

    targets = router.get_targets(sender, text)

    if router.is_paused:
        # Only emit the loop guard notice once per pause
        if not router.guard_emitted:
            router.guard_emitted = True
            store.add(
                "system",
                f"Loop guard: {router.max_hops} agent-to-agent hops reached. "
                "Type /continue to resume."
            )
        return

    # Build a readable message string for the wake prompt
    chat_msg = f"{sender}: {text}" if text else ""

    for target in targets:
        if agents.is_available(target):
            await agents.trigger(target, message=chat_msg)


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
    status["paused"] = router.is_paused
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


async def broadcast_clear():
    data = json.dumps({"type": "clear"})
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


async def broadcast_reaction_update(msg_id: int, reactions: dict[str, list[str]]):
    data = json.dumps({"type": "reaction_update", "data": {"id": msg_id, "reactions": reactions}})
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

    # Send history
    history = store.get_recent(50)
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

                if not text and not attachments:
                    continue

                # /continue command
                if text.lower() == "/continue":
                    router.continue_routing()
                    store.add("system", "Routing resumed.")
                    await broadcast_status()
                    continue

                # /clear command
                if text.lower() == "/clear":
                    store.clear()
                    await broadcast_clear()
                    continue

                # /status command
                if text.lower() == "/status":
                    status = agents.get_status()
                    lines = ["**Agent Status:**"]
                    for name, info in status.items():
                        if name == "paused":
                            continue
                        pill = "🟢" if info.get("available") else "⬜"
                        if info.get("busy"):
                            pill = "⏳"
                        label = config.get("agents", {}).get(name, {}).get("label", name)
                        lines.append(f"{pill} **{label}**: {'Busy' if info.get('busy') else 'Available' if info.get('available') else 'Offline'}")
                    
                    lines.append(f"\n**Loop Guard:** {'Paused (type /continue to resume)' if router.is_paused else 'Active'}")
                    lines.append(f"**Hops:** {router._hop_count} / {router.max_hops}")
                    
                    store.add("system", "\n".join(lines))
                    continue

                # /roastreview command
                if text.lower() == "/roastreview":
                    agents = list(config.get("agents", {}).keys())
                    mentions = " ".join(f"@{a}" for a in agents)
                    store.add(sender, f"{mentions} Time for a roast review! Inspect each other's work and constructively roast it.")
                    continue

                # /poetry command
                if text.lower().startswith("/poetry"):
                    parts = text.lower().split(None, 1)
                    form = parts[1] if len(parts) > 1 else "haiku"
                    if form not in ("haiku", "limerick", "sonnet"):
                        form = "haiku"
                    agents = list(config.get("agents", {}).keys())
                    mentions = " ".join(f"@{a}" for a in agents)
                    prompts = {
                        "haiku": "Write a haiku about the current state of this codebase.",
                        "limerick": "Write a limerick about the current state of this codebase.",
                        "sonnet": "Write a sonnet about the current state of this codebase.",
                    }
                    store.add(sender, f"{mentions} {prompts[form]}")
                    continue

                # /history command
                if text.lower().startswith("/history"):
                    parts = text.split()
                    try:
                        count = int(parts[1]) if len(parts) > 1 else 20
                    except (ValueError, IndexError):
                        count = 20
                    
                    # Limit count to avoid massive messages
                    count = max(1, min(count, 100))
                    
                    recent = store.get_recent(count)
                    lines = [f"**Recent History (last {len(recent)} messages):**"]
                    for m in recent:
                        sender_name = m.get("sender", "unknown")
                        msg_text = m.get("text", "")
                        msg_time = m.get("time", "")
                        lines.append(f"[{msg_time}] **{sender_name}**: {msg_text}")
                    
                    store.add("system", "\n".join(lines))
                    continue

                # Store message — the on_message callback handles broadcast + triggers
                reply_to = event.get("reply_to")
                if reply_to is not None:
                    reply_to = int(reply_to)
                store.add(sender, text, attachments=attachments, reply_to=reply_to)

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
                _save_settings()
                await broadcast_settings()

    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except Exception:
        ws_clients.discard(websocket)
        log.exception("WebSocket error")


# --- REST endpoints ---

ALLOWED_UPLOAD_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
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


@app.get("/api/projects")
async def list_projects():
    return {
        "current": project_manager.current_project,
        "all": project_manager.list_projects()
    }


@app.post("/api/projects")
async def switch_project(body: dict):
    name = body.get("name")
    if not name:
        return JSONResponse({"error": "project name required"}, status_code=400)
    
    global store
    store = project_manager.switch_project(name)
    
    # Update MCP bridge
    import mcp_bridge
    mcp_bridge.store = store
    
    # Re-register callback
    store.on_message(_on_store_message)
    
    # Notify all clients to reload
    await broadcast({"type": "project_switched", "name": name})
    
    return {"ok": True, "name": name}


@app.get("/api/messages")
async def get_messages(since_id: int = 0, limit: int = 50):
    if since_id:
        return store.get_since(since_id)
    return store.get_recent(limit)


@app.post("/api/reactions")
async def add_reaction(body: dict):
    try:
        msg_id = int(body.get("message_id"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid message_id"}, status_code=400)

    emoji = str(body.get("emoji", "")).strip()
    sender = str(body.get("sender", "")).strip()
    if not emoji or not sender:
        return JSONResponse({"error": "emoji and sender are required"}, status_code=400)

    reactions = store.toggle_reaction(msg_id, emoji, sender)
    if reactions is None:
        return JSONResponse({"error": "message not found"}, status_code=404)

    await broadcast_reaction_update(msg_id, reactions)
    return JSONResponse({"message_id": msg_id, "reactions": reactions})


@app.get("/api/tasks")
async def get_tasks():
    todo_path = _todo_path()
    if not todo_path.exists():
        return []
    try:
        return _parse_todo_tasks(todo_path.read_text("utf-8"))
    except OSError:
        return []


@app.get("/api/status")
async def get_status():
    status = agents.get_status()
    status["paused"] = router.is_paused
    return status


@app.get("/api/settings")
async def get_settings():
    return room_settings


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

    import os
    import subprocess
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
