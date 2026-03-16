"""Entry point — starts MCP server (port 8200) + web UI (port 8300)."""

import asyncio
import secrets
import sys
import threading
import time
import logging
from pathlib import Path

# Ensure the project directory is on the import path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from config_loader import load_config
    config_path = ROOT / "config.toml"
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    config = load_config(ROOT)

    # --- Security: generate a random session token (in-memory only) ---
    session_token = secrets.token_hex(32)

    # Configure the FastAPI app (creates shared store)
    from app import app, configure, set_event_loop, store as _store_ref
    configure(config, session_token=session_token)

    # Share stores with the MCP bridge
    from app import store, rules, summaries, jobs, room_settings, registry, router as app_router, agents as app_agents, session_engine, session_store
    import mcp_bridge
    mcp_bridge.store = store
    mcp_bridge.rules = rules
    mcp_bridge.summaries = summaries
    mcp_bridge.jobs = jobs
    mcp_bridge.room_settings = room_settings
    mcp_bridge.registry = registry
    mcp_bridge.config = config
    mcp_bridge.router = app_router
    mcp_bridge.agents = app_agents

    # Enable cursor and role persistence across restarts
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    mcp_bridge._CURSORS_FILE = data_dir / "mcp_cursors.json"
    mcp_bridge._load_cursors()
    mcp_bridge._ROLES_FILE = data_dir / "roles.json"
    mcp_bridge._load_roles()

    # Start MCP servers in background threads
    http_port = config.get("mcp", {}).get("http_port", 8200)
    sse_port = config.get("mcp", {}).get("sse_port", 8201)
    mcp_bridge.mcp_http.settings.port = http_port
    mcp_bridge.mcp_sse.settings.port = sse_port

    threading.Thread(target=mcp_bridge.run_http_server, daemon=True).start()
    threading.Thread(target=mcp_bridge.run_sse_server, daemon=True).start()
    time.sleep(0.5)
    logging.getLogger(__name__).info("MCP streamable-http on port %d, SSE on port %d", http_port, sse_port)

    # Mount static files
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import HTMLResponse, JSONResponse

    static_dir = ROOT / "static"

    @app.get("/")
    async def index():
        # Read index.html fresh each request so changes take effect without restart.
        # Inject the session token into the HTML so the browser client can use it.
        # This is safe: same-origin policy prevents cross-origin pages from reading
        # the response body, so only the user's own browser tab gets the token.
        html = (static_dir / "index.html").read_text("utf-8")
        injected = html.replace(
            "</head>",
            f'<script>window.__SESSION_TOKEN__="{session_token}";</script>\n</head>',
        )
        return HTMLResponse(injected, headers={"Cache-Control": "no-store"})

    @app.get("/api/session-token")
    async def dev_session_token():
        # Dev React UI runs on a separate Vite origin, so it can't receive the
        # backend-injected token from `/`. Expose the same in-memory token via
        # a lightweight local endpoint for proxied dev bootstrap.
        return JSONResponse({"token": session_token}, headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Capture the event loop for the store→WebSocket bridge
    @app.on_event("startup")
    async def on_startup():
        set_event_loop(asyncio.get_running_loop())
        # Resume any sessions that were active before restart
        if session_engine:
            session_engine.resume_active_sessions()

    # Run web server
    import uvicorn
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8300)

    # --- Security: warn if binding to a non-localhost address ---
    # if host not in ("127.0.0.1", "localhost", "::1"):
    #     print(f"\n  !! SECURITY WARNING — binding to {host} !!")
    #     print("  This exposes agentchattr to your local network.")
    #     print()
    #     print("  Risks:")
    #     print("  - No TLS: traffic (including session token) is plaintext")
    #     print("  - Anyone on your network can sniff the token and gain full access")
    #     print("  - With the token, anyone can @mention agents and trigger tool execution")
    #     print("  - If agents run with auto-approve, this means remote code execution")
    #     print()
    #     print("  Only use this on a trusted home network. Never on public/shared WiFi.")
    #     if "--allow-network" not in sys.argv:
    #         print("  Pass --allow-network to start anyway, or set host to 127.0.0.1.\n")
    #         sys.exit(1)
    #     else:
    #         print()
    #         try:
    #             confirm = input("  Type YES to accept these risks and start: ").strip()
    #         except (EOFError, KeyboardInterrupt):
    #             confirm = ""
    #         if confirm != "YES":
    #             print("  Aborted.\n")
    #             sys.exit(1)

    print(f"\n  Mehub")
    print(f"  Web UI:  http://{host}:{port}")
    print(f"  MCP HTTP: http://{host}:{http_port}/mcp  (Claude, Codex)")
    print(f"  MCP SSE:  http://{host}:{sse_port}/sse   (Gemini)")
    print(f"  Agents auto-trigger on @mention")
    print(f"\n  Session token: {session_token}\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
