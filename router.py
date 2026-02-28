"""Message routing based on @mentions with per-channel loop guard."""

import re
import threading


class Router:
    def __init__(self, agent_names: list[str], default_mention: str = "both",
                 max_hops: int = 4):
        self.agent_names = set(n.lower() for n in agent_names)
        self.default_mention = default_mention
        self.max_hops = max_hops
        # Per-channel state: { channel: { hop_count, paused, guard_emitted } }
        self._channels: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._build_pattern()

    def _get_ch(self, channel: str) -> dict:
        if channel not in self._channels:
            self._channels[channel] = {
                "hop_count": 0,
                "paused": False,
                "guard_emitted": False,
            }
        return self._channels[channel]

    def _build_pattern(self):
        names = "|".join(re.escape(n) for n in sorted(self.agent_names))
        self._mention_re = re.compile(
            rf"@({names}|both|all)\b", re.IGNORECASE
        )

    def parse_mentions(self, text: str) -> list[str]:
        mentions = set()
        for match in self._mention_re.finditer(text):
            name = match.group(1).lower()
            if name in ("both", "all"):
                mentions.update(self.agent_names)
            else:
                mentions.add(name)
        return list(mentions)

    def _is_agent(self, sender: str) -> bool:
        return sender.lower() in self.agent_names

    def get_targets(self, sender: str, text: str, channel: str = "general") -> list[str]:
        """Determine which agents should receive this message."""
        with self._lock:
            ch = self._get_ch(channel)

            if ch["paused"]:
                return []

            mentions = self.parse_mentions(text)

            if not self._is_agent(sender):
                # Human message resets hop counter for this channel
                ch["hop_count"] = 0
                ch["paused"] = False
                ch["guard_emitted"] = False
                if not mentions:
                    if self.default_mention in ("both", "all"):
                        return list(self.agent_names)
                    elif self.default_mention == "none":
                        return []
                    return [self.default_mention]
                return mentions
            else:
                # Agent message: only route if explicit @mention
                if not mentions:
                    return []
                ch["hop_count"] += 1
                if ch["hop_count"] > self.max_hops:
                    ch["paused"] = True
                    return []
                # Don't route back to self
                return [m for m in mentions if m != sender]

    def continue_routing(self, channel: str = "general"):
        """Resume after loop guard pause."""
        with self._lock:
            ch = self._get_ch(channel)
            ch["hop_count"] = 0
            ch["paused"] = False
            ch["guard_emitted"] = False

    def is_paused(self, channel: str = "general") -> bool:
        with self._lock:
            return self._get_ch(channel)["paused"]

    def is_guard_emitted(self, channel: str = "general") -> bool:
        with self._lock:
            return self._get_ch(channel)["guard_emitted"]

    def set_guard_emitted(self, channel: str = "general"):
        with self._lock:
            self._get_ch(channel)["guard_emitted"] = True
