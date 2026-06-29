"""Reusable process supervisor for launcher-style UIs.

The web `/launcher` page and the native desktop launcher both use this module.
It manages processes it starts itself. Desktop-launched servers are recovered
from a local state file; external agents are detected and displayed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import locale
import os
import platform
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from config_loader import ROOT, load_config


_YOLO_ARG_MAP: dict[str, list[str]] = {
    "kimi": ["--yolo"],
    "codex": ["--", "--dangerously-bypass-approvals-and-sandbox"],
    "claude": ["--dangerously-skip-permissions"],
    "gemini": ["--", "--yolo"],
    "qwen": ["--yolo"],
}

_SESSION_TOKEN_RE = re.compile(r"Session token:\s*([0-9a-fA-F]{32,})")


ANSI_CONTROL_RE = re.compile(
    r"""
    \x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))
    |[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]
    """,
    re.VERBOSE,
)


RegistryProvider = Callable[[], Any]
RoleSetter = Callable[[str, str], Any]


@dataclass
class AgentTemplate:
    base: str
    label: str
    command: str
    cwd: str
    color: str
    normal_args: list[str] = field(default_factory=list)
    yolo_args: list[str] = field(default_factory=list)
    supports_yolo: bool = False


@dataclass
class ManagedProcess:
    key: str
    kind: str
    base: Optional[str]
    assigned_name: Optional[str]
    pid: int
    status: str
    started_by_launcher: bool
    started_at: float
    last_error: Optional[str] = None
    mode: Optional[str] = None
    role: Optional[str] = None
    cwd: Optional[str] = None


@dataclass
class LogEvent:
    process_key: str
    stream: str
    text: str
    timestamp: float


@dataclass
class ServerProbe:
    running: bool
    occupied: bool = False
    status: str = "stopped"
    detail: str = ""


def http_role_setter(host: str, port: int, token: str = "") -> RoleSetter:
    """Create a role setter that talks to a running agentchattr server."""

    def _set(name: str, role: str) -> None:
        body = json.dumps({"role": role}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Session-Token"] = token
        req = urllib.request.Request(
            f"http://{host}:{port}/api/roles/{name}",
            method="POST",
            data=body,
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=3).read()

    return _set


class Launcher:
    """Thin process supervisor shared by web and desktop launchers."""

    MAX_LOG_LINES = 500

    def __init__(
        self,
        *,
        root: Path | None = None,
        config: dict | None = None,
        registry_provider: RegistryProvider | None = None,
        role_setter: RoleSetter | None = None,
        session_token: str = "",
        env_overrides: dict[str, str] | None = None,
    ):
        self.root = root or ROOT
        self._config: dict = config or {}
        self._templates: dict[str, AgentTemplate] = {}
        self._processes: dict[str, ManagedProcess] = {}
        self._subprocesses: dict[str, asyncio.subprocess.Process] = {}
        self._logs: dict[str, deque[LogEvent]] = {}
        self._lock = asyncio.Lock()
        self._registry_provider = registry_provider
        self._role_setter = role_setter
        self._session_token = session_token
        self._env_overrides = dict(env_overrides or {})
        self._load_config()

    def _load_config(self) -> None:
        if not self._config:
            self._config = load_config(self.root)
        self._templates = {}
        for name, cfg in self._config.get("agents", {}).items():
            base = name.lower()
            yolo_args = _YOLO_ARG_MAP.get(base, [])
            self._templates[base] = AgentTemplate(
                base=base,
                label=cfg.get("label", base.capitalize()),
                command=cfg.get("command", base),
                cwd=cfg.get("cwd", ".."),
                color=cfg.get("color", "#888"),
                normal_args=[],
                yolo_args=yolo_args,
                supports_yolo=bool(yolo_args),
            )

    def set_session_token(self, token: str) -> None:
        self._session_token = token or ""

    def _server_host_port(self) -> tuple[str, int]:
        server = self._config.get("server", {})
        return server.get("host", "127.0.0.1"), int(server.get("port", 8300))

    def _probe_host_port(self) -> tuple[str, int]:
        host, port = self._server_host_port()
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return host, port

    def _data_dir(self) -> Path:
        raw = self._config.get("server", {}).get("data_dir", "./data")
        path = Path(raw)
        if not path.is_absolute():
            path = (self.root / path).resolve()
        return path

    def _server_state_path(self) -> Path:
        return self._data_dir() / "launcher_server.json"

    def _load_server_state(self) -> dict[str, Any]:
        path = self._server_state_path()
        try:
            state = json.loads(path.read_text("utf-8"))
        except Exception:
            return {}

        _, port = self._server_host_port()
        if state.get("root") != str(self.root.resolve()):
            return {}
        if int(state.get("port") or 0) != port:
            return {}
        if not state.get("launcher_token"):
            return {}
        return state

    def _save_server_state(self, *, pid: int, launcher_token: str) -> None:
        host, port = self._server_host_port()
        state = {
            "pid": pid,
            "host": host,
            "port": port,
            "root": str(self.root.resolve()),
            "launcher_token": launcher_token,
            "started_at": time.time(),
        }
        path = self._server_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), "utf-8")

    def _clear_server_state(self) -> None:
        try:
            self._server_state_path().unlink(missing_ok=True)
        except Exception:
            pass

    def _child_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self._env_overrides)
        if extra:
            env.update(extra)
        return env

    async def _http_health_ok(self, path: str) -> bool:
        host, port = self._probe_host_port()
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        url = f"http://{url_host}:{port}{path}"
        try:
            def request() -> bool:
                req = urllib.request.Request(
                    url,
                    method="GET",
                    headers={"User-Agent": "agentchattr-launcher/1.0"},
                )
                with urllib.request.urlopen(req, timeout=1.5) as response:
                    return 200 <= response.status < 400

            return await asyncio.to_thread(request)
        except Exception:
            return False

    async def _http_json(self, path: str, timeout: float = 1.5) -> dict | None:
        host, port = self._probe_host_port()
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        url = f"http://{url_host}:{port}{path}"

        def request() -> dict | None:
            headers = {"User-Agent": "agentchattr-launcher/1.0"}
            if self._session_token:
                headers["X-Session-Token"] = self._session_token
                headers["Authorization"] = f"Bearer {self._session_token}"
            req = urllib.request.Request(url, method="GET", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if not 200 <= response.status < 400:
                    return None
                data = json.loads(response.read().decode("utf-8"))
                return data if isinstance(data, dict) else None

        try:
            return await asyncio.to_thread(request)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None
        except Exception:
            return None

    async def _tcp_port_open(self) -> bool:
        host, port = self._probe_host_port()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    async def _probe_server(self) -> ServerProbe:
        _, port = self._server_host_port()
        if await self._http_health_ok("/launcher") or await self._http_health_ok("/"):
            return ServerProbe(running=True, occupied=False, status="running")
        if await self._tcp_port_open():
            return ServerProbe(
                running=False,
                occupied=True,
                status="occupied",
                detail=f"Port {port} is occupied but did not pass HTTP health checks",
            )
        return ServerProbe(running=False, occupied=False, status="stopped")

    async def _is_server_running(self) -> bool:
        return (await self._probe_server()).running

    def _pid_is_alive(self, pid: int | None) -> bool:
        if not pid:
            return False
        if pid == os.getpid():
            return True
        if platform.system().lower() == "windows":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except Exception:
                return False
            return result.returncode == 0 and str(pid) in result.stdout
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return False

    def _saved_server_is_manageable(self) -> bool:
        state = self._load_server_state()
        return bool(state and self._pid_is_alive(int(state.get("pid") or 0)))

    def _find_windows_pid_for_port(self, port: int) -> int | None:
        if platform.system().lower() != "windows":
            return None
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        suffix = f":{port}"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local_addr = parts[1]
            state = parts[3].upper()
            if state != "LISTENING" or not local_addr.endswith(suffix):
                continue
            try:
                return int(parts[-1])
            except ValueError:
                return None
        return None

    async def _terminate_port_owner(self) -> dict:
        _, port = self._server_host_port()
        pid = await asyncio.to_thread(self._find_windows_pid_for_port, port)
        if not pid:
            return {
                "error": "Server is running externally and cannot be stopped by launcher",
                "status": "external",
            }
        if pid == os.getpid():
            return {
                "error": "Refusing to terminate the current launcher process",
                "status": "external",
            }
        try:
            if platform.system().lower() == "windows":
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()
                    return {
                        "error": f"Failed to stop PID {pid}: {detail or 'taskkill failed'}",
                        "status": "error",
                    }
            else:
                os.kill(pid, 15)
        except Exception as exc:
            return {"error": f"Failed to stop PID {pid}: {exc}", "status": "error"}

        for _ in range(20):
            if not (await self._probe_server()).running:
                self._clear_server_state()
                return {"key": "server", "status": "stopped", "pid": pid}
            await asyncio.sleep(0.25)
        return {
            "error": f"PID {pid} was stopped, but the server port still responds",
            "status": "error",
        }

    async def _request_launcher_shutdown(self, token: str) -> dict:
        host, port = self._probe_host_port()
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        url = f"http://{url_host}:{port}/api/shutdown_launcher_server"

        def request() -> tuple[int, bytes]:
            req = urllib.request.Request(
                url,
                method="POST",
                headers={
                    "User-Agent": "agentchattr-launcher/1.0",
                    "X-Launcher-Token": token,
                },
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, response.read()

        status, body = await asyncio.to_thread(request)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        payload.setdefault("http_status", status)
        return payload

    async def _stop_server_with_saved_token(self) -> dict | None:
        state = self._load_server_state()
        token = state.get("launcher_token")
        if not token:
            return None
        try:
            await self._request_launcher_shutdown(str(token))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404, 405):
                self._clear_server_state()
                return None
            return {
                "error": f"Launcher shutdown request failed: HTTP {exc.code}",
                "status": "error",
            }
        except Exception as exc:
            return {
                "error": f"Launcher shutdown request failed: {exc}",
                "status": "error",
            }

        for _ in range(30):
            if not (await self._probe_server()).running:
                self._clear_server_state()
                return {
                    "key": "server",
                    "status": "stopped",
                    "pid": state.get("pid"),
                }
            await asyncio.sleep(0.25)
        return {
            "error": "Server accepted shutdown but did not stop before timeout",
            "status": "error",
        }

    def _decode_log_bytes(self, data: bytes) -> str:
        encodings = ["utf-8", locale.getpreferredencoding(False), sys.getfilesystemencoding()]
        if os.name == "nt":
            encodings.extend(["mbcs", "cp936"])

        seen: set[str] = set()
        for encoding in encodings:
            if not encoding or encoding.lower() in seen:
                continue
            seen.add(encoding.lower())
            try:
                return data.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return data.decode("utf-8", errors="replace")

    def _clean_log_text(self, text: str) -> str:
        text = ANSI_CONTROL_RE.sub("", text)
        return text.replace("\r", "").rstrip("\n")

    async def _read_stream(
        self, key: str, stream: asyncio.StreamReader | None, stream_name: str
    ) -> None:
        if stream is None:
            return
        while True:
            try:
                line = await stream.readline()
                if not line:
                    break
                text = self._clean_log_text(self._decode_log_bytes(line))
                token_match = _SESSION_TOKEN_RE.search(text)
                if token_match:
                    self.set_session_token(token_match.group(1))
                self._logs.setdefault(key, deque(maxlen=self.MAX_LOG_LINES)).append(
                    LogEvent(
                        process_key=key,
                        stream=stream_name,
                        text=text,
                        timestamp=time.time(),
                    )
                )
            except Exception:
                break

    async def _monitor_process(
        self,
        key: str,
        proc: asyncio.subprocess.Process,
        process: ManagedProcess,
    ) -> None:
        await asyncio.gather(
            self._read_stream(key, proc.stdout, "stdout"),
            self._read_stream(key, proc.stderr, "stderr"),
            return_exceptions=True,
        )
        await proc.wait()
        async with self._lock:
            if proc.returncode != 0:
                process.status = "error"
                process.last_error = f"Exited with code {proc.returncode}"
            else:
                process.status = "stopped"
            if key in self._subprocesses and self._subprocesses[key] is proc:
                del self._subprocesses[key]
            if key == "server":
                self._clear_server_state()

    async def send_input(self, key: str, text: str, *, append_newline: bool = True) -> dict:
        async with self._lock:
            proc = self._subprocesses.get(key)
            process = self._processes.get(key)
            if not proc:
                if key.startswith("external:"):
                    return {
                        "error": f"Process {key} is external and has no launcher stdin pipe",
                        "status": "external",
                    }
                if process and not process.started_by_launcher:
                    return {
                        "error": f"Process {key} is not managed by launcher",
                        "status": "external",
                    }
                return {"error": f"Process {key} not found", "status": "not_found"}
            if proc.returncode is not None:
                return {"error": f"Process {key} has already exited", "status": "stopped"}
            if proc.stdin is None:
                return {
                    "error": f"Process {key} was not started with a stdin pipe",
                    "status": "unsupported",
                }
            payload = text + ("\n" if append_newline else "")
            stdin = proc.stdin

        try:
            stdin.write(payload.encode("utf-8"))
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return {"error": f"Process {key} stdin pipe is closed", "status": "closed"}
        except Exception as exc:
            return {"error": f"Failed to send input to {key}: {exc}", "status": "error"}

        return {
            "key": key,
            "status": "sent",
            "bytes": len(payload.encode("utf-8")),
            "capability": "stdin_pipe",
        }

    def _registry_instances(self) -> dict[str, dict]:
        if not self._registry_provider:
            return {}
        try:
            registry = self._registry_provider()
            if not registry:
                return {}
            if isinstance(registry, dict):
                return registry
            if hasattr(registry, "get_all"):
                return registry.get_all()
        except Exception:
            return {}
        return {}

    async def _server_runtime_status(self, server_running: bool) -> dict[str, dict]:
        if not server_running:
            return {}
        status = await self._http_json("/api/status")
        if not status:
            return {}
        return {
            str(name): value
            for name, value in status.items()
            if name != "paused" and isinstance(value, dict)
        }

    def _registry_runtime_status(self, instances: dict[str, dict]) -> dict[str, dict]:
        runtime: dict[str, dict] = {}
        if not instances:
            return runtime

        is_online = is_active = get_role = None
        try:
            import mcp_bridge

            is_online = getattr(mcp_bridge, "is_online", None)
            is_active = getattr(mcp_bridge, "is_active", None)
            get_role = getattr(mcp_bridge, "get_role", None)
        except Exception:
            pass

        for name, inst in instances.items():
            inst_name = str(inst.get("name") or name)
            state = inst.get("state", "unknown")
            available = state == "active"
            busy = False
            role = inst.get("role") or ""
            try:
                if callable(is_online):
                    available = bool(is_online(inst_name))
                if callable(is_active):
                    busy = bool(is_active(inst_name))
                if callable(get_role):
                    role = role or str(get_role(inst_name) or "")
            except Exception:
                pass
            runtime[inst_name] = {
                "available": available,
                "busy": busy,
                "label": inst.get("label", inst_name),
                "color": inst.get("color", "#888"),
                "role": role,
                "base": inst.get("base"),
                "state": state,
            }
        return runtime

    @staticmethod
    def _runtime_display_status(runtime: dict | None, fallback: str = "unknown") -> str:
        if not runtime:
            return fallback
        if runtime.get("busy"):
            return "working"
        if runtime.get("available") or runtime.get("state") == "active":
            return "active"
        if runtime.get("state") == "pending":
            return "pending"
        if fallback in {"starting", "stopping", "error"}:
            return fallback
        return "stopped"

    def _merge_runtime_status(
        self,
        processes: dict[str, dict],
        runtime_status: dict[str, dict],
    ) -> None:
        assigned_to_key = {
            proc.get("assigned_name"): key
            for key, proc in processes.items()
            if proc.get("kind") == "agent" and proc.get("assigned_name")
        }

        for name, runtime in runtime_status.items():
            key = assigned_to_key.get(name)
            if key:
                proc = processes[key]
                proc["status"] = self._runtime_display_status(
                    runtime, str(proc.get("status") or "unknown")
                )
                proc["available"] = bool(runtime.get("available"))
                proc["busy"] = bool(runtime.get("busy"))
                proc["label"] = runtime.get("label") or proc.get("label")
                proc["color"] = runtime.get("color") or proc.get("color")
                proc["state"] = runtime.get("state") or proc.get("state")
                proc["role"] = runtime.get("role") or proc.get("role")
                proc["base"] = proc.get("base") or runtime.get("base")
                continue

            key = f"external:{name}"
            processes.setdefault(
                key,
                {
                    "key": key,
                    "kind": "agent",
                    "base": runtime.get("base"),
                    "assigned_name": name,
                    "pid": None,
                    "status": self._runtime_display_status(runtime, "unknown"),
                    "started_by_launcher": False,
                    "started_at": runtime.get("registered_at", 0),
                    "last_error": None,
                    "mode": None,
                    "role": runtime.get("role") or "",
                    "cwd": None,
                    "can_send_input": False,
                    "input_capability": "external",
                    "available": bool(runtime.get("available")),
                    "busy": bool(runtime.get("busy")),
                    "label": runtime.get("label", name),
                    "color": runtime.get("color", "#888"),
                    "state": runtime.get("state"),
                },
            )

    def _instances_for(self, base: str) -> list[dict]:
        if not self._registry_provider:
            return []
        try:
            registry = self._registry_provider()
            if not registry:
                return []
            if hasattr(registry, "get_instances_for"):
                return registry.get_instances_for(base)
            if isinstance(registry, dict):
                return [
                    dict(inst, name=name)
                    for name, inst in registry.items()
                    if inst.get("base") == base
                ]
        except Exception:
            return []
        return []

    async def _apply_role(self, name: str, role: str | None) -> None:
        if not role or role == "none" or not self._role_setter:
            return
        try:
            result = self._role_setter(name, role)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass

    async def get_status(self) -> dict:
        probe = await self._probe_server()
        saved_server_state = self._load_server_state()
        server_managed = "server" in self._subprocesses or (
            probe.running and self._saved_server_is_manageable()
        )
        if server_managed and not probe.running:
            server_status = self._processes.get("server")
            status_text = server_status.status if server_status else "starting"
        else:
            status_text = probe.status

        processes = {
            k: {
                "key": p.key,
                "kind": p.kind,
                "base": p.base,
                "assigned_name": p.assigned_name,
                "pid": p.pid,
                "status": p.status,
                "started_by_launcher": p.started_by_launcher,
                "started_at": p.started_at,
                "last_error": p.last_error,
                "mode": p.mode,
                "role": p.role,
                "cwd": p.cwd,
                "can_send_input": (
                    k in self._subprocesses
                    and self._subprocesses[k].stdin is not None
                    and self._subprocesses[k].returncode is None
                ),
                "input_capability": (
                    "stdin_pipe"
                    if (
                        k in self._subprocesses
                        and self._subprocesses[k].stdin is not None
                        and self._subprocesses[k].returncode is None
                    )
                    else "unavailable"
                ),
            }
            for k, p in self._processes.items()
        }

        registry_instances = self._registry_instances()
        managed_names = {
            p.get("assigned_name")
            for p in processes.values()
            if p.get("started_by_launcher") and p.get("assigned_name")
        }
        for name, inst in registry_instances.items():
            inst_name = inst.get("name", name)
            if inst_name in managed_names:
                continue
            key = f"external:{inst_name}"
            processes.setdefault(
                key,
                {
                    "key": key,
                    "kind": "agent",
                    "base": inst.get("base"),
                    "assigned_name": inst_name,
                    "pid": None,
                    "status": inst.get("state", "unknown"),
                    "started_by_launcher": False,
                    "started_at": inst.get("registered_at", 0),
                    "last_error": None,
                    "mode": None,
                    "role": inst.get("role"),
                    "cwd": None,
                    "can_send_input": False,
                    "input_capability": "external",
                },
            )
        runtime_status = await self._server_runtime_status(probe.running)
        if not runtime_status:
            runtime_status = self._registry_runtime_status(registry_instances)
        self._merge_runtime_status(processes, runtime_status)

        host, port = self._server_host_port()
        return {
            "server": {
                "running": probe.running,
                "occupied": probe.occupied,
                "port_occupied": probe.occupied,
                "status": status_text,
                "probe_status": probe.status,
                "detail": probe.detail,
                "managed_by_launcher": server_managed,
                "pid": self._processes.get("server").pid
                if self._processes.get("server")
                else saved_server_state.get("pid"),
                "port": port,
                "host": host,
                "data_dir": self._config.get("server", {}).get("data_dir", "./data"),
                "mcp_http_port": self._config.get("mcp", {}).get("http_port", 8200),
                "mcp_sse_port": self._config.get("mcp", {}).get("sse_port", 8201),
            },
            "templates": {
                t.base: {
                    "base": t.base,
                    "label": t.label,
                    "command": t.command,
                    "cwd": t.cwd,
                    "color": t.color,
                    "supports_yolo": t.supports_yolo,
                }
                for t in self._templates.values()
            },
            "processes": processes,
        }

    async def start_server(self) -> dict:
        async with self._lock:
            probe = await self._probe_server()
            if probe.running:
                return {"error": "Server is already running", "status": "running"}
            if probe.occupied:
                return {
                    "error": probe.detail or "Server port is occupied",
                    "status": "occupied",
                }
            if "server" in self._subprocesses:
                return {"error": "Server start already in progress", "status": "starting"}

            cmd = [sys.executable, str(self.root / "run.py")]
            launcher_token = secrets.token_urlsafe(32)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.root),
                    env=self._child_env(
                        {
                            "AGENTCHATTR_LAUNCHER_TOKEN": launcher_token,
                        }
                    ),
                )
                self._save_server_state(pid=proc.pid, launcher_token=launcher_token)
            except Exception as exc:
                return {"error": f"Failed to start server: {exc}", "status": "error"}

            process = ManagedProcess(
                key="server",
                kind="server",
                base=None,
                assigned_name=None,
                pid=proc.pid,
                status="starting",
                started_by_launcher=True,
                started_at=time.time(),
                cwd=str(self.root),
            )
            self._processes["server"] = process
            self._subprocesses["server"] = proc

        asyncio.create_task(self._monitor_process("server", proc, process))
        return {"key": "server", "status": "starting", "pid": proc.pid}

    async def stop_server(self) -> dict:
        if "server" in self._subprocesses:
            return await self.stop_process("server")

        probe = await self._probe_server()
        if probe.running:
            token_result = await self._stop_server_with_saved_token()
            if token_result:
                return token_result
            return await self._terminate_port_owner()
        if probe.occupied:
            port_result = await self._terminate_port_owner()
            if "error" not in port_result:
                return port_result
            return {
                "error": probe.detail or port_result.get("error") or "Server port is occupied and cannot be stopped by launcher",
                "status": "occupied",
            }
        self._clear_server_state()
        return {"error": "Server is not running", "status": "idle"}

    async def start_agent(
        self,
        base: str,
        mode: str = "normal",
        role: Optional[str] = None,
        custom_role: Optional[str] = None,
        cwd: Optional[str] = None,
        auto_start: bool = False,
    ) -> dict:
        base = base.lower()
        template = self._templates.get(base)
        if not template:
            return {"error": f"Unknown agent type: {base}"}
        if mode not in ("normal", "yolo"):
            return {"error": f"Unknown launch mode: {mode}"}
        if mode == "yolo" and not template.supports_yolo:
            return {"error": f"Agent {base} does not support yolo mode"}
        if not await self._is_server_running():
            return {"error": "Server must be running before starting agents"}

        cmd = [sys.executable, str(self.root / "wrapper.py"), base]
        if mode == "yolo" and template.yolo_args:
            cmd.extend(template.yolo_args)

        raw_work_dir = cwd or template.cwd or str(self.root.parent)
        work_dir = Path(raw_work_dir)
        if not work_dir.is_absolute():
            work_dir = (self.root / work_dir).resolve()
        else:
            work_dir = work_dir.resolve()

        async with self._lock:
            key = f"agent:{base}:{int(time.time() * 1000)}"
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(work_dir),
                    env=self._child_env(),
                )
            except Exception as exc:
                return {"error": f"Failed to start agent: {exc}"}

            role_value = custom_role if role == "custom" else role
            if role_value == "none":
                role_value = None
            process = ManagedProcess(
                key=key,
                kind="agent",
                base=base,
                assigned_name=None,
                pid=proc.pid,
                status="starting",
                started_by_launcher=True,
                started_at=time.time(),
                mode=mode,
                role=role_value,
                cwd=str(work_dir),
            )
            self._processes[key] = process
            self._subprocesses[key] = proc

        asyncio.create_task(self._monitor_process(key, proc, process))
        asyncio.create_task(self._resolve_assigned_name(key, base))
        return {
            "process_key": key,
            "base": base,
            "assigned_name": None,
            "status": "starting",
            "started_by_launcher": True,
            "pid": proc.pid,
        }

    async def _resolve_assigned_name(self, key: str, base: str) -> None:
        for _ in range(30):
            await asyncio.sleep(1)
            async with self._lock:
                process = self._processes.get(key)
                if not process or process.status in ("stopped", "error"):
                    return

            instances = self._instances_for(base)
            used_names = {
                p.assigned_name
                for p in self._processes.values()
                if p.assigned_name and p.key != key
            }
            for inst in sorted(
                instances,
                key=lambda item: item.get("registered_at", 0),
                reverse=True,
            ):
                name = inst.get("name")
                if not name or name in used_names:
                    continue
                role_value = None
                async with self._lock:
                    current = self._processes.get(key)
                    if not current:
                        return
                    current.assigned_name = name
                    if current.status == "starting":
                        current.status = "running"
                    role_value = current.role
                await self._apply_role(name, role_value)
                return

    async def stop_process(self, key: str) -> dict:
        async with self._lock:
            proc = self._subprocesses.get(key)
            process = self._processes.get(key)

            if not proc and not process:
                if key.startswith("external:"):
                    return {
                        "error": f"Agent {key} is running externally and cannot be stopped by launcher",
                        "status": "external",
                    }
                return {"error": f"Process {key} not found", "status": "not_found"}
            if not proc:
                return {
                    "error": f"Process {key} is not managed by launcher",
                    "status": "external",
                }
            if process:
                process.status = "stopping"

            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except Exception as exc:
                return {"error": f"Failed to stop process: {exc}"}

            self._subprocesses.pop(key, None)
            if process:
                process.status = "stopped"
            if key == "server":
                self._clear_server_state()

        return {"key": key, "status": "stopped"}

    async def restart_process(self, key: str) -> dict:
        if key == "server" and "server" not in self._subprocesses:
            stop_result = await self.stop_server()
            if "error" in stop_result and stop_result.get("status") not in ("idle", "stopped"):
                return stop_result
            for _ in range(20):
                probe = await self._probe_server()
                if not probe.running and not probe.occupied:
                    break
                await asyncio.sleep(0.25)
            return await self.start_server()

        process = self._processes.get(key)
        if not process:
            return {"error": f"Process {key} not found"}

        is_server = key == "server" or process.kind == "server"
        base = process.base
        mode = process.mode or "normal"
        role = process.role
        cwd = process.cwd

        stop_result = await self.stop_process(key)
        if "error" in stop_result and stop_result.get("status") != "stopped":
            return stop_result

        for _ in range(10):
            await asyncio.sleep(0.5)
            current = self._processes.get(key)
            if current and current.status in ("stopped", "error"):
                break

        if is_server:
            return await self.start_server()
        if base:
            return await self.start_agent(base=base, mode=mode, role=role, cwd=cwd)
        return {"error": "Cannot restart: unknown process type"}

    def get_logs(self, key: str, limit: int = 100) -> list[dict]:
        events = list(self._logs.get(key, []))
        return [
            {
                "process_key": event.process_key,
                "stream": event.stream,
                "text": event.text,
                "timestamp": event.timestamp,
            }
            for event in events[-limit:]
        ]

    async def get_agents(self) -> dict:
        status = await self.get_status()
        return {
            "templates": status["templates"],
            "processes": status["processes"],
            "registry_instances": self._registry_instances(),
        }
