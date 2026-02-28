"""JSONL message persistence for the chat room with observer callbacks."""

import json
import time
import threading
from pathlib import Path


class MessageStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._todos_path = self._path.parent / "todos.json"
        self._reactions_path = self._path.parent / "reactions.json"
        self._messages: list[dict] = []
        self._todos: dict[int, str] = {}  # msg_id → "todo" | "done"
        self._reactions: dict[int, dict[str, list[str]]] = {}
        self._lock = threading.Lock()
        self._callbacks: list = []  # called on each new message
        self._todo_callbacks: list = []  # called on todo changes
        self._delete_callbacks: list = []  # called on message deletion
        self._load()
        self._load_todos()
        self._load_reactions()

    def _load(self):
        if not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    msg["id"] = i
                    self._messages.append(msg)
                except json.JSONDecodeError:
                    continue

    def on_message(self, callback):
        """Register a callback(msg) called whenever a message is added."""
        self._callbacks.append(callback)

    def add(self, sender: str, text: str, msg_type: str = "chat",
            attachments: list | None = None, reply_to: int | None = None) -> dict:
        with self._lock:
            msg = {
                "id": len(self._messages),
                "sender": sender,
                "text": text,
                "type": msg_type,
                "timestamp": time.time(),
                "time": time.strftime("%H:%M:%S"),
                "attachments": attachments or [],
            }
            if reply_to is not None:
                msg["reply_to"] = reply_to
            self._messages.append(msg)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            result = self._with_reactions(msg)

        # Fire callbacks outside the lock
        for cb in self._callbacks:
            try:
                cb(result)
            except Exception:
                pass

        return result

    def get_by_id(self, msg_id: int) -> dict | None:
        with self._lock:
            if 0 <= msg_id < len(self._messages):
                return self._with_reactions(self._messages[msg_id])
            return None

    def get_recent(self, count: int = 50) -> list[dict]:
        with self._lock:
            return [self._with_reactions(m) for m in self._messages[-count:]]

    def get_since(self, since_id: int = 0) -> list[dict]:
        with self._lock:
            return [self._with_reactions(m) for m in self._messages if m["id"] > since_id]

    def delete(self, msg_ids: list[int]) -> list[int]:
        """Delete messages by ID. Returns list of IDs actually deleted."""
        deleted = []
        deleted_attachments = []
        with self._lock:
            for mid in msg_ids:
                for i, m in enumerate(self._messages):
                    if m["id"] == mid:
                        # Collect attachment files for cleanup
                        for att in m.get("attachments", []):
                            url = att.get("url", "")
                            if url.startswith("/uploads/"):
                                deleted_attachments.append(url.split("/")[-1])
                        # Remove any associated todo
                        if mid in self._todos:
                            del self._todos[mid]
                        if mid in self._reactions:
                            del self._reactions[mid]
                        self._messages.pop(i)
                        deleted.append(mid)
                        break
            if deleted:
                self._rewrite_jsonl()
                self._save_todos()
                self._save_reactions()

        # Clean up uploaded images outside the lock
        for filename in deleted_attachments:
            filepath = Path("./uploads") / filename
            if filepath.exists():
                try:
                    filepath.unlink()
                except Exception:
                    pass

        # Fire callbacks
        for cb in self._delete_callbacks:
            try:
                cb(deleted)
            except Exception:
                pass

        return deleted

    def on_delete(self, callback):
        """Register a callback(ids) called when messages are deleted."""
        self._delete_callbacks.append(callback)

    def _rewrite_jsonl(self):
        """Rewrite the JSONL file from current in-memory messages."""
        with open(self._path, "w", encoding="utf-8") as f:
            for m in self._messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    def clear(self):
        """Wipe all messages and truncate the log file."""
        with self._lock:
            self._messages.clear()
            self._reactions.clear()
            with open(self._path, "w", encoding="utf-8") as f:
                f.truncate(0)
            self._save_reactions()

    # --- Todos ---

    def _load_todos(self):
        # Migrate old pins.json (list of ints) → todos.json (dict of id→status)
        old_pins = self._todos_path.parent / "pins.json"
        if old_pins.exists() and not self._todos_path.exists():
            try:
                ids = json.loads(old_pins.read_text("utf-8"))
                if isinstance(ids, list):
                    self._todos = {int(i): "todo" for i in ids}
                    self._save_todos()
                    old_pins.unlink()
            except Exception:
                pass

        if self._todos_path.exists():
            try:
                raw = json.loads(self._todos_path.read_text("utf-8"))
                self._todos = {int(k): v for k, v in raw.items()}
            except Exception:
                self._todos = {}

    def _save_todos(self):
        self._todos_path.write_text(
            json.dumps({str(k): v for k, v in self._todos.items()}, indent=2),
            "utf-8"
        )

    # --- Reactions ---

    def _load_reactions(self):
        if self._reactions_path.exists():
            try:
                raw = json.loads(self._reactions_path.read_text("utf-8"))
                self._reactions = {
                    int(msg_id): {
                        str(emoji): [str(sender) for sender in senders]
                        for emoji, senders in reactions.items()
                        if isinstance(senders, list)
                    }
                    for msg_id, reactions in raw.items()
                    if isinstance(reactions, dict)
                }
            except Exception:
                self._reactions = {}

    def _save_reactions(self):
        self._reactions_path.write_text(
            json.dumps({str(k): v for k, v in self._reactions.items()}, indent=2, ensure_ascii=False),
            "utf-8",
        )

    def _with_reactions(self, msg: dict) -> dict:
        out = dict(msg)
        reactions = self._reactions.get(msg["id"], {})
        out["reactions"] = {emoji: list(senders) for emoji, senders in reactions.items()}
        return out

    def toggle_reaction(self, msg_id: int, emoji: str, sender: str) -> dict[str, list[str]] | None:
        with self._lock:
            if msg_id < 0 or msg_id >= len(self._messages):
                return None

            reactions = self._reactions.setdefault(msg_id, {})
            senders = reactions.setdefault(emoji, [])

            if sender in senders:
                senders.remove(sender)
            else:
                senders.append(sender)

            if not senders:
                del reactions[emoji]
            if not reactions:
                self._reactions.pop(msg_id, None)

            self._save_reactions()
            current = self._reactions.get(msg_id, {})
            return {key: list(value) for key, value in current.items()}

    def on_todo(self, callback):
        """Register a callback(msg_id, status) called on todo changes.
        status is 'todo', 'done', or None (removed)."""
        self._todo_callbacks.append(callback)

    def _fire_todo(self, msg_id: int, status: str | None):
        for cb in self._todo_callbacks:
            try:
                cb(msg_id, status)
            except Exception:
                pass

    def add_todo(self, msg_id: int) -> bool:
        with self._lock:
            if msg_id < 0 or msg_id >= len(self._messages):
                return False
            self._todos[msg_id] = "todo"
            self._save_todos()
        self._fire_todo(msg_id, "todo")
        return True

    def complete_todo(self, msg_id: int) -> bool:
        with self._lock:
            if msg_id not in self._todos:
                return False
            self._todos[msg_id] = "done"
            self._save_todos()
        self._fire_todo(msg_id, "done")
        return True

    def reopen_todo(self, msg_id: int) -> bool:
        with self._lock:
            if msg_id not in self._todos:
                return False
            self._todos[msg_id] = "todo"
            self._save_todos()
        self._fire_todo(msg_id, "todo")
        return True

    def remove_todo(self, msg_id: int) -> bool:
        with self._lock:
            if msg_id not in self._todos:
                return False
            del self._todos[msg_id]
            self._save_todos()
        self._fire_todo(msg_id, None)
        return True

    def get_todo_status(self, msg_id: int) -> str | None:
        return self._todos.get(msg_id)

    def get_todos(self) -> dict[int, str]:
        """Returns {msg_id: status} for all todos."""
        return dict(self._todos)

    def get_todo_messages(self, status: str | None = None) -> list[dict]:
        """Get todo messages, optionally filtered by status."""
        with self._lock:
            if status:
                ids = {k for k, v in self._todos.items() if v == status}
            else:
                ids = set(self._todos.keys())
            return [m for m in self._messages if m["id"] in ids]

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._messages[-1]["id"] if self._messages else -1
