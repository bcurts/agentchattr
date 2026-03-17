"""Job store — bounded work conversations with threaded messages."""

import json
import time
import threading
import uuid
from pathlib import Path


class JobStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._jobs: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._callbacks: list = []  # (action, job) on any change
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, list):
                self._jobs = raw
                if self._jobs:
                    self._next_id = max(a["id"] for a in self._jobs) + 1
                    if self._ensure_sort_orders_locked():
                        self._save()
        except (json.JSONDecodeError, KeyError):
            self._jobs = []

    def _save(self):
        self._path.write_text(
            json.dumps(self._jobs, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    def _next_sort_order_locked(self, status: str) -> int:
        max_order = 0
        for a in self._jobs:
            if a.get("status") != status:
                continue
            try:
                max_order = max(max_order, int(a.get("sort_order", 0)))
            except (TypeError, ValueError):
                continue
        return max_order + 1

    def _ensure_sort_orders_locked(self):
        max_by_group: dict[str, int] = {}
        changed = False
        for a in self._jobs:
            key = a.get("status", "open")
            try:
                cur = int(a.get("sort_order", 0))
            except (TypeError, ValueError):
                cur = 0
            if cur > 0:
                max_by_group[key] = max(max_by_group.get(key, 0), cur)

        for a in self._jobs:
            key = a.get("status", "open")
            try:
                cur = int(a.get("sort_order", 0))
            except (TypeError, ValueError):
                cur = 0
            if cur <= 0:
                next_order = max_by_group.get(key, 0) + 1
                a["sort_order"] = next_order
                max_by_group[key] = next_order
                changed = True
        return changed

    def on_change(self, callback):
        """Register a callback(action, job) on any change.
        action: 'create', 'update', 'message', 'message_delete'."""
        self._callbacks.append(callback)

    def _fire(self, action: str, job: dict):
        for cb in self._callbacks:
            try:
                cb(action, job)
            except Exception:
                pass

    def list_all(self, channel: str | None = None,
                 status: str | None = None) -> list[dict]:
        """List jobs, optionally filtered by channel and/or status."""
        with self._lock:
            changed = self._ensure_sort_orders_locked()
            if changed:
                self._save()
            result = list(self._jobs)
        if channel:
            result = [a for a in result if a.get("channel") == channel]
        if status:
            result = [a for a in result if a.get("status") == status]
        return result

    def get(self, job_id: int) -> dict | None:
        with self._lock:
            for a in self._jobs:
                if a["id"] == job_id:
                    return dict(a)
            return None

    def create(self, title: str, job_type: str, channel: str,
               created_by: str, anchor_msg_id: int | None = None,
               assignee: str | None = None,
               body: str | None = None,
               uid: str | None = None,
               status: str | None = None,
               created_at: float | None = None,
               updated_at: float | None = None) -> dict:
        """Create a new job. Returns the job dict."""
        with self._lock:
            st = status or "open"
            now = time.time()
            a = {
                "id": self._next_id,
                "uid": uid or str(uuid.uuid4()),
                "type": job_type,
                "title": title.strip()[:120],
                "body": (body or "").strip()[:1000],
                "status": st,
                "channel": channel,
                "created_by": created_by,
                "assignee": assignee or "",
                "anchor_msg_id": anchor_msg_id,
                "messages": [],
                "created_at": created_at or now,
                "updated_at": updated_at or now,
                "sort_order": self._next_sort_order_locked(st),
            }
            self._next_id += 1
            self._jobs.append(a)
            self._save()
        self._fire("create", a)
        return a

    def update_status(self, job_id: int, status: str) -> dict | None:
        """Update job status. Valid: open, done, archived."""
        if status not in ("open", "done", "archived"):
            return None
        with self._lock:
            for a in self._jobs:
                if a["id"] == job_id:
                    old_status = a.get("status")
                    next_order = None
                    if old_status != status:
                        # Compute destination order before moving this job so
                        # the job doesn't count itself in the target lane.
                        next_order = self._next_sort_order_locked(status)
                    a["status"] = status
                    a["updated_at"] = time.time()
                    if next_order is not None:
                        a["sort_order"] = next_order
                    self._save()
                    result = dict(a)
                    break
            else:
                return None
        self._fire("update", result)
        return result

    def update_title(self, job_id: int, title: str) -> dict | None:
        with self._lock:
            for a in self._jobs:
                if a["id"] == job_id:
                    a["title"] = title.strip()[:120]
                    a["updated_at"] = time.time()
                    self._save()
                    result = dict(a)
                    break
            else:
                return None
        self._fire("update", result)
        return result

    def update_assignee(self, job_id: int, assignee: str) -> dict | None:
        with self._lock:
            for a in self._jobs:
                if a["id"] == job_id:
                    a["assignee"] = assignee.strip()
                    a["updated_at"] = time.time()
                    self._save()
                    result = dict(a)
                    break
            else:
                return None
        self._fire("update", result)
        return result

    def add_message(self, job_id: int, sender: str, text: str,
                    attachments: list | None = None,
                    msg_type: str = "chat",
                    uid: str | None = None,
                    timestamp: float | None = None,
                    time_str: str | None = None) -> dict | None:
        """Add a message to a job's conversation. Returns the message."""
        with self._lock:
            for a in self._jobs:
                if a["id"] == job_id:
                    msg_id = len(a["messages"])
                    ts = timestamp if timestamp is not None else time.time()
                    msg = {
                        "id": msg_id,
                        "uid": uid or str(uuid.uuid4()),
                        "sender": sender,
                        "text": text.strip(),
                        "time": time_str or time.strftime("%H:%M:%S"),
                        "timestamp": ts,
                        "attachments": attachments or [],
                    }
                    if msg_type != "chat":
                        msg["type"] = msg_type
                    a["messages"].append(msg)
                    a["updated_at"] = time.time()
                    self._save()
                    result_msg = dict(msg)
                    result_msg["job_id"] = job_id
                    break
            else:
                return None
        self._fire("message", {"job_id": job_id, "message": result_msg})
        return result_msg

    def get_messages(self, job_id: int) -> list[dict] | None:
        """Get all messages for a job."""
        with self._lock:
            for a in self._jobs:
                if a["id"] == job_id:
                    return list(a["messages"])
            return None

    def delete_message(self, job_id: int, msg_id: int) -> dict | None:
        """Soft-delete a message from a job conversation by message id."""
        with self._lock:
            for a in self._jobs:
                if a["id"] != job_id:
                    continue
                msgs = a.get("messages", [])
                hit = None
                for i, m in enumerate(msgs):
                    try:
                        mid = int(m.get("id", -1))
                    except (TypeError, ValueError):
                        mid = -1
                    if mid == msg_id:
                        hit = (i, m)
                        break
                if hit is None:
                    return None
                _, msg = hit
                if msg.get("deleted"):
                    return {"job_id": job_id, "message_id": msg_id}
                msg["deleted"] = True
                msg["text"] = ""
                msg["attachments"] = []
                msg["updated_at"] = time.time()
                a["updated_at"] = time.time()
                self._save()
                payload = {"job_id": job_id, "message_id": msg_id}
                break
            else:
                return None
        self._fire("message_delete", payload)
        return payload

    def delete(self, job_id: int) -> dict | None:
        """Permanently delete a job."""
        with self._lock:
            for i, a in enumerate(self._jobs):
                if a["id"] == job_id:
                    removed = self._jobs.pop(i)
                    self._save()
                    result = dict(removed)
                    break
            else:
                return None
        self._fire("delete", result)
        return result

    def rename_channel(self, old_name: str, new_name: str) -> list[dict]:
        """Migrate all jobs from old_name to new_name."""
        changed: list[dict] = []
        with self._lock:
            for a in self._jobs:
                if a.get("channel") != old_name:
                    continue
                a["channel"] = new_name
                a["updated_at"] = time.time()
                changed.append(dict(a))
            if changed:
                self._save()
        for item in changed:
            self._fire("update", item)
        return changed

    def delete_channel(self, channel: str) -> list[dict]:
        """Delete all jobs that belong to a removed channel."""
        removed: list[dict] = []
        with self._lock:
            kept: list[dict] = []
            for a in self._jobs:
                if a.get("channel") == channel:
                    removed.append(dict(a))
                else:
                    kept.append(a)
            if not removed:
                return []
            self._jobs = kept
            self._save()
        for item in removed:
            self._fire("delete", item)
        return removed

    def reorder(self, status: str, ordered_ids: list[int]) -> list[dict]:
        """Reorder jobs within a status group by explicit id order (top to bottom)."""
        if status not in ("open", "done", "archived"):
            return []
        with self._lock:
            self._ensure_sort_orders_locked()
            group = [
                a for a in self._jobs
                if a.get("status") == status
            ]
            if not group:
                return []

            by_id = {int(a["id"]): a for a in group}
            ordered: list[int] = []
            seen = set()
            for raw in ordered_ids:
                try:
                    aid = int(raw)
                except (TypeError, ValueError):
                    continue
                if aid in by_id and aid not in seen:
                    ordered.append(aid)
                    seen.add(aid)

            if not ordered:
                return []

            existing_sorted = sorted(
                group,
                key=lambda x: (int(x.get("sort_order", 0) or 0), float(x.get("updated_at", 0) or 0)),
                reverse=True,
            )
            for a in existing_sorted:
                aid = int(a["id"])
                if aid not in seen:
                    ordered.append(aid)

            changed: list[dict] = []
            n = len(ordered)
            for idx, aid in enumerate(ordered):
                item = by_id.get(aid)
                if not item:
                    continue
                new_order = n - idx
                old_order = int(item.get("sort_order", 0) or 0)
                if old_order != new_order:
                    item["sort_order"] = new_order
                    changed.append(dict(item))

            if changed:
                self._save()

        for item in changed:
            self._fire("update", item)
        return changed
