"""Agent trigger — writes to queue files picked up by visible worker terminals."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class AgentTrigger:
    def __init__(self, config: dict, data_dir: str = "./data"):
        self._config = config
        self._data_dir = Path(data_dir)
        self._sessions_path = self._data_dir / "sessions.json"
        self._sessions: dict[str, str] = {}
        self._load_sessions()

    def _load_sessions(self):
        if self._sessions_path.exists():
            try:
                self._sessions = json.loads(
                    self._sessions_path.read_text("utf-8"))
            except Exception:
                self._sessions = {}

    def is_available(self, name: str) -> bool:
        return name in self._config

    def is_busy(self, name: str) -> bool:
        from mcp_bridge import is_busy
        return is_busy(name)

    def get_status(self) -> dict:
        # Reload sessions in case worker updated them
        self._load_sessions()
        # Check MCP presence to determine if agent is actually online
        from mcp_bridge import is_online, is_busy
        return {
            name: {
                "available": is_online(name),
                "active": is_busy(name),
                "label": cfg.get("label", name),
                "color": cfg.get("color", "#888"),
                "session_id": self._sessions.get(name),
            }
            for name, cfg in self._config.items()
        }

    async def trigger(self, agent_name: str, message: str = "", **kwargs):
        """Write to the agent's queue file. The worker terminal picks it up."""
        queue_file = self._data_dir / f"{agent_name}_queue.jsonl"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        import time
        entry = {
            "sender": message.split(":")[0].strip() if ":" in message else "?",
            "text": message,
            "time": time.strftime("%H:%M:%S"),
        }

        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        log.info("Queued @%s trigger: %s", agent_name, message[:80])
