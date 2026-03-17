"""Schedule store — recurring prompts fired without human intervention."""

import json
import re
import time
import threading
import uuid
from pathlib import Path


# Interval parsing: "every 30m", "every 1h", "every 2h", "daily at 09:00"
_INTERVAL_RE = re.compile(
    r"every\s+(\d+)\s*(m|min|h|hr|d|day)s?\b",
    re.IGNORECASE
)
_DAILY_RE = re.compile(
    r"daily\s+at\s+(\d{1,2}):(\d{2})\b",
    re.IGNORECASE
)


def parse_schedule_spec(spec: str) -> tuple[int | None, str | None]:
    """Parse natural-language schedule spec.
    Returns (interval_seconds, None) for intervals, or (None, cron_expr) for daily.
    Returns (None, None) if unparseable.
    """
    spec = spec.strip()
    m = _INTERVAL_RE.search(spec)
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit in ("m", "min"):
            secs = val * 60
        elif unit in ("h", "hr"):
            secs = val * 3600
        elif unit in ("d", "day"):
            secs = val * 86400
        else:
            return (None, None)
        if secs < 60:
            secs = 60  # minimum 1 minute
        return (secs, None)

    dm = _DAILY_RE.search(spec)
    if dm:
        hour = int(dm.group(1)) % 24
        minute = int(dm.group(2)) % 60
        # Store as "HH:MM" for daily; we compute next_run from current time
        return (86400, f"{hour:02d}:{minute:02d}")  # interval=1 day, time hint

    return (None, None)


def compute_next_run(
    interval_seconds: int,
    last_run: float | None,
    daily_at: str | None = None,
) -> float:
    """Compute next run timestamp. daily_at is "HH:MM" for daily schedules."""
    now = time.time()
    if last_run is None:
        if daily_at:
            # First run: today at HH:MM, or tomorrow if already past
            parts = daily_at.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            from datetime import datetime
            today = datetime.fromtimestamp(now)
            target = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
            ts = target.timestamp()
            if ts <= now:
                from datetime import timedelta
                target = target + timedelta(days=1)
                ts = target.timestamp()
            return ts
        return now + interval_seconds
    if daily_at:
        from datetime import datetime, timedelta
        last_dt = datetime.fromtimestamp(last_run)
        parts = daily_at.split(":")
        hour, minute = int(parts[0]), int(parts[1])
        next_dt = last_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_dt <= last_dt:
            next_dt = next_dt + timedelta(days=1)
        return next_dt.timestamp()
    return last_run + interval_seconds


class ScheduleStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._schedules: list[dict] = []
        self._lock = threading.Lock()
        self._callbacks: list = []
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, list):
                self._schedules = raw
        except (json.JSONDecodeError, TypeError):
            self._schedules = []

    def _save(self):
        self._path.write_text(
            json.dumps(self._schedules, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    def on_change(self, callback):
        """Register callback(action, schedule) on any change."""
        self._callbacks.append(callback)

    def _fire(self, action: str, schedule: dict):
        for cb in self._callbacks:
            try:
                cb(action, schedule)
            except Exception:
                pass

    def list_all(self, active_only: bool = False) -> list[dict]:
        with self._lock:
            result = list(self._schedules)
        if active_only:
            result = [s for s in result if s.get("active", True)]
        return result

    def get(self, schedule_id: str) -> dict | None:
        with self._lock:
            for s in self._schedules:
                if s.get("id") == schedule_id:
                    return dict(s)
            return None

    def create(
        self,
        prompt: str,
        targets: list[str],
        channel: str = "general",
        interval_seconds: int | None = None,
        daily_at: str | None = None,
        one_shot: bool = False,
        send_at: float | None = None,
        created_by: str = "user",
    ) -> dict:
        """Create a schedule. Either interval_seconds or daily_at must be set.
        If one_shot=True, the schedule auto-deletes after firing once.
        If send_at is provided (epoch), use it as next_run directly.
        """
        schedule_id = str(uuid.uuid4())[:8]
        now = time.time()
        last_run = None
        if daily_at:
            interval_seconds = 86400
        if send_at:
            next_run = send_at
        else:
            next_run = compute_next_run(
                interval_seconds or 86400,
                last_run,
                daily_at=daily_at,
            )
        with self._lock:
            s = {
                "id": schedule_id,
                "prompt": prompt.strip()[:500],
                "targets": [t.strip().lstrip("@") for t in targets if t.strip()],
                "channel": channel or "general",
                "interval_seconds": interval_seconds or 86400,
                "daily_at": daily_at,
                "next_run": next_run,
                "created_at": now,
                "last_run": None,
                "active": True,
                "one_shot": one_shot,
                "created_by": created_by,
            }
            self._schedules.append(s)
            self._save()
        self._fire("create", s)
        return dict(s)

    def run_due(self) -> list[dict]:
        """Return list of schedules that are due (next_run <= now). Does not update."""
        now = time.time()
        with self._lock:
            due = [s for s in self._schedules if s.get("active") and s.get("next_run", 0) <= now]
        return [dict(s) for s in due]

    def mark_run(self, schedule_id: str) -> dict | None:
        """Mark schedule as run, advance next_run. Returns updated schedule."""
        with self._lock:
            for s in self._schedules:
                if s.get("id") != schedule_id:
                    continue
                now = time.time()
                s["last_run"] = now
                s["next_run"] = compute_next_run(
                    s.get("interval_seconds", 3600),
                    now,
                    daily_at=s.get("daily_at"),
                )
                self._save()
                result = dict(s)
                break
            else:
                return None
        self._fire("update", result)
        return result

    def delete(self, schedule_id: str) -> dict | None:
        with self._lock:
            for i, s in enumerate(self._schedules):
                if s.get("id") == schedule_id:
                    removed = self._schedules.pop(i)
                    self._save()
                    result = dict(removed)
                    break
            else:
                return None
        self._fire("delete", result)
        return result

    def rename_channel(self, old_name: str, new_name: str) -> list[dict]:
        changed: list[dict] = []
        with self._lock:
            for s in self._schedules:
                if s.get("channel") != old_name:
                    continue
                s["channel"] = new_name
                changed.append(dict(s))
            if changed:
                self._save()
        for item in changed:
            self._fire("update", item)
        return changed

    def delete_channel(self, channel: str) -> list[dict]:
        removed: list[dict] = []
        with self._lock:
            kept: list[dict] = []
            for s in self._schedules:
                if s.get("channel") == channel:
                    removed.append(dict(s))
                else:
                    kept.append(s)
            if not removed:
                return []
            self._schedules = kept
            self._save()
        for item in removed:
            self._fire("delete", item)
        return removed

    def toggle(self, schedule_id: str) -> dict | None:
        with self._lock:
            for s in self._schedules:
                if s.get("id") == schedule_id:
                    s["active"] = not s.get("active", True)
                    self._save()
                    result = dict(s)
                    break
            else:
                return None
        self._fire("update", result)
        return result
