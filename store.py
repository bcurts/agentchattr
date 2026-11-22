"""JSONL message persistence for the chat room with observer callbacks."""

import json
import logging
import os
import time
import threading
import uuid
from pathlib import Path

from atomic_io import atomic_write_text

log = logging.getLogger(__name__)


class MessageStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._todos_path = self._path.parent / "todos.json"
        self._messages: list[dict] = []
        self._next_id: int = 0  # monotonically increasing, survives deletions
        self._todos: dict[int, str] = {}  # msg_id → "todo" | "done"
        self._lock = threading.Lock()
        self._log_dirty = False
        self._callbacks: list = []  # called on each new message
        self._todo_callbacks: list = []  # called on todo changes
        self._delete_callbacks: list = []  # called on message deletion
        self.upload_dir = self._path.parent.parent / "uploads"  # Default fallback
        self._load()
        self._load_todos()

    def _load(self):
        if not self._path.exists():
            return
        max_id = -1
        with open(self._path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    # Preserve persisted ID; fall back to line number for legacy data
                    if "id" not in msg:
                        msg["id"] = i
                    if msg["id"] > max_id:
                        max_id = msg["id"]
                    self._messages.append(msg)
                except json.JSONDecodeError:
                    continue
        self._next_id = max_id + 1

    def on_message(self, callback):
        """Register a callback(msg) called whenever a message is added."""
        self._callbacks.append(callback)

    def add(self, sender: str, text: str, msg_type: str = "chat",
            attachments: list | None = None, reply_to: int | None = None,
            channel: str = "general",
            metadata: dict | None = None,
            uid: str | None = None,
            timestamp: float | None = None,
            time_str: str | None = None,
            _bulk: bool = False) -> dict:
        persistence_error = None
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            msg = {
                "id": self._next_id,
                "uid": uid or str(uuid.uuid4()),
                "sender": sender,
                "text": text,
                "type": msg_type,
                "timestamp": ts,
                "time": time_str or time.strftime("%H:%M:%S"),
                "attachments": attachments or [],
                "channel": channel,
            }
            if reply_to is not None:
                msg["reply_to"] = reply_to
            if metadata:
                msg["metadata"] = metadata
            self._next_id += 1
            self._messages.append(msg)
            if not _bulk:
                try:
                    if self._log_dirty:
                        self._rewrite()
                    else:
                        with open(self._path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                            f.flush()
                            os.fsync(f.fileno())
                    self._log_dirty = False
                except OSError as exc:
                    self._log_dirty = True
                    persistence_error = str(exc)
                    log.warning("Message %s delivered in memory but log write failed: %s", msg["id"], exc)

        # Fire callbacks outside the lock (skip during bulk import)
        if not _bulk:
            for cb in self._callbacks:
                try:
                    cb(msg)
                except Exception:
                    pass

        if persistence_error:
            return {**msg, "persistence_error": persistence_error}
        return msg

    def flush_bulk(self):
        """Write all in-memory messages to disk. Call after bulk add operations."""
        with self._lock:
            self._rewrite()

    def update_reply_to(self, msg_id: int, reply_to: int):
        """Set reply_to on an existing message (used by import to rebuild links)."""
        with self._lock:
            for m in self._messages:
                if m["id"] == msg_id:
                    m["reply_to"] = reply_to
                    self._rewrite()
                    return

    def _rewrite(self):
        """Rewrite the full JSONL file from memory (used after bulk edits)."""
        atomic_write_text(self._path, "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in self._messages))

    def get_by_id(self, msg_id: int) -> dict | None:
        with self._lock:
            for m in self._messages:
                if m["id"] == msg_id:
                    return m
            return None

    def get_recent(self, count: int = 50, channel: str | None = None) -> list[dict]:
        with self._lock:
            msgs = self._messages
            if channel:
                msgs = [m for m in msgs if m.get("channel", "general") == channel]
            return list(msgs[-count:])

    def get_before(self, before_id: int, count: int = 50, channel: str | None = None) -> tuple[list[dict], bool]:
        """Up to `count` messages older than `before_id` (oldest first) and whether more exist."""
        with self._lock:
            older = [m for m in self._messages if m["id"] < before_id]
            if channel:
                older = [m for m in older if m.get("channel", "general") == channel]
            count = max(1, count)
            return list(older[-count:]), len(older) > count

    def count_channel(self, channel: str) -> int:
        with self._lock:
            return sum(1 for m in self._messages if m.get("channel", "general") == channel)

    def channel_activity(self) -> dict[str, float]:
        """Epoch timestamp of the newest message in each channel that has any."""
        latest: dict[str, float] = {}
        with self._lock:
            for m in self._messages:
                ts = m.get("timestamp")
                if not isinstance(ts, (int, float)):
                    continue
                ch = m.get("channel", "general")
                if ts > latest.get(ch, 0):
                    latest[ch] = ts
        return latest

    def get_since(self, since_id: int = 0, channel: str | None = None) -> list[dict]:
        with self._lock:
            msgs = [m for m in self._messages if m["id"] > since_id]
            if channel:
                msgs = [m for m in msgs if m.get("channel", "general") == channel]
            return msgs

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
                        self._messages.pop(i)
                        deleted.append(mid)
                        break
            if deleted:
                self._rewrite_jsonl()
                self._save_todos()

        # Clean up uploaded images outside the lock
        for filename in deleted_attachments:
            filepath = self.upload_dir / filename
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

    def update_message(self, msg_id: int, updates: dict) -> dict | None:
        """Update fields on a message in-place. Returns the updated message or None."""
        with self._lock:
            for m in self._messages:
                if m["id"] == msg_id:
                    m.update(updates)
                    self._rewrite_jsonl()
                    return dict(m)
            return None

    def _rewrite_jsonl(self):
        """Rewrite the JSONL file from current in-memory messages."""
        atomic_write_text(self._path, "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in self._messages))

    def clear(self, channel: str | None = None):
        """Wipe messages and rewrite the log file.
        If channel is given, only clear messages in that channel."""
        with self._lock:
            if channel:
                removed_ids = {m["id"] for m in self._messages if m.get("channel", "general") == channel}
                self._messages = [m for m in self._messages if m.get("channel", "general") != channel]
                self._rewrite_jsonl()
                # Clean up todos for cleared messages
                for tid in list(self._todos.keys()):
                    if tid in removed_ids:
                        del self._todos[tid]
                if removed_ids:
                    self._save_todos()
            else:
                self._messages.clear()
                atomic_write_text(self._path, "")
                self._todos.clear()
                self._save_todos()

    def rename_channel(self, old_name: str, new_name: str):
        """Migrate all messages from old_name to new_name."""
        with self._lock:
            modified = False
            for m in self._messages:
                if m.get("channel") == old_name:
                    m["channel"] = new_name
                    modified = True
            if modified:
                self._rewrite_jsonl()

    def rename_sender(self, old_name: str, new_name: str) -> int:
        """Rename sender on all messages from old_name to new_name. Returns count updated."""
        with self._lock:
            count = 0
            for m in self._messages:
                if m.get("sender") == old_name:
                    m["sender"] = new_name
                    count += 1
            if count:
                self._rewrite_jsonl()
        return count

    def delete_channel(self, name: str):
        """Remove all messages belonging to a deleted channel."""
        with self._lock:
            original_len = len(self._messages)
            # Collect IDs of messages being removed so we can clean up their todos
            removed_ids = {m["id"] for m in self._messages if m.get("channel") == name}
            self._messages = [m for m in self._messages if m.get("channel") != name]
            if len(self._messages) != original_len:
                self._rewrite_jsonl()
                # Clean up todos that referenced deleted messages
                for tid in list(self._todos.keys()):
                    if tid in removed_ids:
                        del self._todos[tid]
                self._save_todos()

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
        atomic_write_text(
            self._todos_path,
            json.dumps({str(k): v for k, v in self._todos.items()}, indent=2),
        )

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
            if not any(m["id"] == msg_id for m in self._messages):
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

    TODO_SUMMARY_TEXT_MAX = 500

    @classmethod
    def _todo_summary(cls, msg: dict, status: str) -> dict:
        """Just enough of a pinned message to render it without the message in the DOM."""
        text = msg.get("text") or ""
        if len(text) > cls.TODO_SUMMARY_TEXT_MAX:
            text = text[:cls.TODO_SUMMARY_TEXT_MAX].rstrip() + "..."
        return {
            "id": msg["id"],
            "status": status,
            "sender": msg.get("sender", ""),
            "text": text,
            "time": msg.get("time", ""),
            "timestamp": msg.get("timestamp", 0),
            "channel": msg.get("channel", "general"),
        }

    def get_todo_summary(self, msg_id: int) -> dict | None:
        """Summary of one pinned message, or None if it isn't pinned or no longer exists."""
        with self._lock:
            status = self._todos.get(msg_id)
            if status is None:
                return None
            for m in self._messages:
                if m["id"] == msg_id:
                    return self._todo_summary(m, status)
        return None

    def get_todo_summaries(self) -> dict[int, dict]:
        """{msg_id: summary} for every pinned message that still exists (one pass)."""
        with self._lock:
            todos = dict(self._todos)
            return {
                m["id"]: self._todo_summary(m, todos[m["id"]])
                for m in self._messages if m["id"] in todos
            }

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
