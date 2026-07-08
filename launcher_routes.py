"""FastAPI routes for launcher control panel.

All launcher API endpoints live under /api/launcher/*.
WebSocket is at /ws/launcher/events (mounted separately in app.py).
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from launcher import launcher
from launcher_supervisor import Launcher


def create_router(supervisor: Launcher) -> APIRouter:
    router = APIRouter(prefix="/api/launcher")

    @router.get("/status")
    async def launcher_status():
        return await supervisor.get_status()

    @router.get("/agents")
    async def launcher_agents():
        return await supervisor.get_agents()

    @router.post("/server/start")
    async def server_start():
        result = await supervisor.start_server()
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/server/stop")
    async def server_stop():
        result = await supervisor.stop_server()
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/server/restart")
    async def server_restart():
        result = await supervisor.restart_process("server")
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/agents/{base}/start")
    async def agent_start(base: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}

        result = await supervisor.start_agent(
            base=base,
            mode=body.get("mode", "normal"),
            role=body.get("role"),
            custom_role=body.get("custom_role"),
            cwd=body.get("cwd"),
            auto_start=body.get("auto_start", False),
        )
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/processes/{key}/stop")
    async def process_stop(key: str):
        result = await supervisor.stop_process(key)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @router.post("/processes/{key}/restart")
    async def process_restart(key: str):
        result = await supervisor.restart_process(key)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @router.get("/logs/{key}")
    async def process_logs(key: str, limit: int = 100):
        return {"logs": supervisor.get_logs(key, limit)}

    return router


def create_launcher_events_ws(supervisor: Launcher):
    async def launcher_events_ws(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                status = await supervisor.get_status()
                await websocket.send_json({"type": "status", "data": status})
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    return launcher_events_ws


router = create_router(launcher)
launcher_events_ws = create_launcher_events_ws(launcher)


__all__ = [
    "create_launcher_events_ws",
    "create_router",
    "launcher_events_ws",
    "router",
]
