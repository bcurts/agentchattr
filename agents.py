"""Agent trigger — writes to queue files picked up by visible worker terminals."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class AgentTrigger:
    def __init__(self, registry, data_dir: str = "./data"):
        self._registry = registry
        self._data_dir = Path(data_dir)

    def is_available(self, name: str) -> bool:
        return self._registry.is_registered(name)

    def get_status(self) -> dict:
        from mcp_bridge import is_online, is_active, get_role
        instances = self._registry.get_all()
        return {
            name: {
                "available": is_online(name),
                "busy": is_active(name),
                "label": info["label"],
                "color": info["color"],
                "role": get_role(name),
            }
            for name, info in instances.items()
        }

    async def trigger(self, agent_name: str, message: str = "", channel: str = "general", **kwargs):
        """Write to the agent's queue file. The worker terminal picks it up."""
        queue_file = self._data_dir / f"{agent_name}_queue.jsonl"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        import time
        entry = {
            "sender": message.split(":")[0].strip() if ":" in message else "?",
            "text": message,
            "time": time.strftime("%H:%M:%S"),
            "channel": channel,
        }

        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        log.info("Queued @%s trigger (ch=%s): %s", agent_name, channel, message[:80])

    def trigger_purge(self):
        """Write purge to each agent's queue so the wrapper injects /clear into the agent terminal."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        for name in self._config:
            queue_file = self._data_dir / f"{name}_queue.jsonl"
            try:
                with open(queue_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "purge"}) + "\n")
                log.info("Queued purge for %s", name)
            except Exception as e:
                log.warning("Failed to queue purge for %s: %s", name, e)
