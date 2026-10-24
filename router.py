"""Message routing based on @mentions with per-channel loop guard."""

import re


class Router:
    def __init__(self, agent_names: list[str], default_mention: str = "both",
                 max_hops: int = 4, online_checker=None):
        self.agent_names = set(n.lower() for n in agent_names)
        self.default_mention = default_mention
        self.max_hops = max_hops
        self._online_checker = online_checker  # callable() -> set of online agent names
        # Per-channel state: { channel: { hop_count, paused, guard_emitted, blocked } }
        self._channels: dict[str, dict] = {}
        self._build_pattern()

    def _get_ch(self, channel: str) -> dict:
        if channel not in self._channels:
            self._channels[channel] = {
                "hop_count": 0,
                "paused": False,
                "guard_emitted": False,
                "blocked": [],  # origins of mention messages held by the loop guard
            }
        return self._channels[channel]

    def _build_pattern(self):
        # Sort longest-first so "gemini-2" is tried before "gemini"
        names = [re.escape(n) for n in sorted(self.agent_names, key=len, reverse=True)]
        alternatives = "|".join(names + ["both", "all"])
        self._mention_re = re.compile(
            rf"@({alternatives})(?![\w-])", re.IGNORECASE
        )

    def parse_mentions(self, text: str) -> list[str]:
        mentions = set()
        for match in self._mention_re.finditer(text):
            name = match.group(1).lower()
            if name in ("both", "all"):
                # Only tag online agents when using @all
                if self._online_checker:
                    online = self._online_checker()
                    mentions.update(n for n in self.agent_names if n in online)
                else:
                    mentions.update(self.agent_names)
            else:
                mentions.add(name)
        return list(mentions)

    def _is_agent(self, sender: str) -> bool:
        return sender.lower() in self.agent_names

    def get_targets(self, sender: str, text: str, channel: str = "general",
                    origin: dict | None = None) -> list[str]:
        """Determine which agents should receive this message.

        `origin` describes the message (sender, text, msg_id). When the loop
        guard blocks an agent message that has mentions, its origin is kept so
        continue_routing() can hand it back for re-delivery.
        """
        ch = self._get_ch(channel)
        mentions = self.parse_mentions(text)

        if not self._is_agent(sender):
            # Human message resets hop counter and unpauses
            ch["hop_count"] = 0
            ch["paused"] = False
            ch["guard_emitted"] = False
            # The human has taken over; stale agent hops must not replay.
            ch["blocked"] = []
            if not mentions:
                if self.default_mention in ("both", "all"):
                    return list(self.agent_names)
                elif self.default_mention == "none":
                    return []
                return [self.default_mention]
            return mentions
        else:
            # Agent message: blocked while loop guard is active
            # Only route if explicit @mention
            if not mentions:
                return []
            if ch["paused"]:
                if origin is not None:
                    ch["blocked"].append(origin)
                return []
            ch["hop_count"] += 1
            if ch["hop_count"] > self.max_hops:
                ch["paused"] = True
                if origin is not None:
                    ch["blocked"].append(origin)
                return []
            # Don't route back to self
            return [m for m in mentions if m != sender]

    def continue_routing(self, channel: str = "general") -> list[dict]:
        """Resume after loop guard pause.

        Returns the origins of messages the guard blocked, oldest first, so
        the caller can re-deliver them.
        """
        ch = self._get_ch(channel)
        ch["hop_count"] = 0
        ch["paused"] = False
        ch["guard_emitted"] = False
        blocked, ch["blocked"] = ch["blocked"], []
        return blocked

    def is_paused(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["paused"]

    def is_guard_emitted(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["guard_emitted"]

    def set_guard_emitted(self, channel: str = "general"):
        self._get_ch(channel)["guard_emitted"] = True

    def update_agents(self, names: list[str]):
        """Replace the agent name set and rebuild the mention regex."""
        self.agent_names = set(n.lower() for n in names)
        self._build_pattern()
