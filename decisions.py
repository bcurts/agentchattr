"""Lightweight decision store — agents propose, humans approve."""

import json
import time
import threading
from pathlib import Path

MAX_DECISIONS = 30
MAX_CHARS = 80


class DecisionStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._decisions: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._callbacks: list = []
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, list):
                self._decisions = raw
                if self._decisions:
                    self._next_id = max(d["id"] for d in self._decisions) + 1
        except (json.JSONDecodeError, KeyError):
            self._decisions = []

    def _save(self):
        self._path.write_text(
            json.dumps(self._decisions, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    def on_change(self, callback):
        """Register a callback(action, decision) fired on any change.
        action is 'propose', 'approve', 'edit', or 'delete'."""
        self._callbacks.append(callback)

    def _fire(self, action: str, decision: dict):
        for cb in self._callbacks:
            try:
                cb(action, decision)
            except Exception:
                pass

    def list_all(self) -> list[dict]:
        with self._lock:
            return list(self._decisions)

    def get(self, decision_id: int) -> dict | None:
        with self._lock:
            for d in self._decisions:
                if d["id"] == decision_id:
                    return dict(d)
            return None

    def propose(self, decision: str, owner: str, reason: str = "") -> dict | None:
        with self._lock:
            if len(self._decisions) >= MAX_DECISIONS:
                return None
            d = {
                "id": self._next_id,
                "decision": decision.strip()[:MAX_CHARS],
                "owner": owner.strip(),
                "reason": reason.strip()[:MAX_CHARS],
                "status": "proposed",
                "created_at": time.time(),
            }
            self._next_id += 1
            self._decisions.append(d)
            self._save()
        self._fire("propose", d)
        return d

    def approve(self, decision_id: int) -> dict | None:
        with self._lock:
            for d in self._decisions:
                if d["id"] == decision_id:
                    d["status"] = "approved"
                    self._save()
                    result = dict(d)
                    break
            else:
                return None
        self._fire("approve", result)
        return result

    def unapprove(self, decision_id: int) -> dict | None:
        with self._lock:
            for d in self._decisions:
                if d["id"] == decision_id:
                    d["status"] = "proposed"
                    self._save()
                    result = dict(d)
                    break
            else:
                return None
        self._fire("edit", result)
        return result

    def edit(self, decision_id: int, decision: str | None = None,
             reason: str | None = None) -> dict | None:
        with self._lock:
            for d in self._decisions:
                if d["id"] == decision_id:
                    if decision is not None:
                        d["decision"] = decision.strip()[:MAX_CHARS]
                    if reason is not None:
                        d["reason"] = reason.strip()[:MAX_CHARS]
                    self._save()
                    result = dict(d)
                    break
            else:
                return None
        self._fire("edit", result)
        return result

    def delete(self, decision_id: int) -> dict | None:
        with self._lock:
            for i, d in enumerate(self._decisions):
                if d["id"] == decision_id:
                    removed = self._decisions.pop(i)
                    self._save()
                    result = dict(removed)
                    break
            else:
                return None
        self._fire("delete", result)
        return result

    def count_proposed(self) -> int:
        with self._lock:
            return sum(1 for d in self._decisions if d["status"] == "proposed")
