"""Session store — persists active session runs to JSON."""

import json
import time
import threading
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, path: str, templates_dir: str | None = None):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._sessions: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._callbacks: list = []
        self._templates: dict[str, dict] = {}
        self._load()

        # Warn about legacy file
        legacy = self._path.parent / "sessions.json"
        if legacy.exists() and legacy != self._path:
            log.info("Ignoring legacy sessions.json; Sessions uses %s", self._path.name)

        if templates_dir:
            self._load_templates(Path(templates_dir))

        # Load custom (user/agent-created) templates
        custom_path = self._path.parent / "custom_templates.json"
        if custom_path.exists():
            try:
                custom = json.loads(custom_path.read_text("utf-8"))
                for tmpl in (custom if isinstance(custom, list) else []):
                    tid = tmpl.get("id", "")
                    if tid:
                        tmpl["is_custom"] = True
                        self._templates[tid] = tmpl
                        log.info("Loaded custom template: %s", tid)
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("Failed to load custom templates: %s", exc)

    # --- Persistence ---

    def _load(self):
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, list):
                self._sessions = raw
                if self._sessions:
                    self._next_id = max(s["id"] for s in self._sessions) + 1
        except (json.JSONDecodeError, KeyError):
            self._sessions = []

    def _save(self):
        self._path.write_text(
            json.dumps(self._sessions, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

    # --- Templates ---

    def _load_templates(self, directory: Path):
        if not directory.exists():
            log.warning("Session templates directory not found: %s", directory)
            return
        for f in sorted(directory.glob("*.json")):
            try:
                tmpl = json.loads(f.read_text("utf-8"))
                tid = tmpl.get("id", f.stem)
                tmpl["id"] = tid
                tmpl.setdefault("is_custom", False)
                self._templates[tid] = tmpl
                log.info("Loaded session template: %s", tid)
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("Failed to load template %s: %s", f.name, exc)

    def get_templates(self) -> list[dict]:
        return list(self._templates.values())

    def get_template(self, template_id: str) -> dict | None:
        return self._templates.get(template_id)

    def save_custom_template(self, tmpl: dict) -> dict:
        custom_path = self._path.parent / "custom_templates.json"
        custom = []
        if custom_path.exists():
            try:
                custom = json.loads(custom_path.read_text("utf-8"))
            except (json.JSONDecodeError, KeyError):
                custom = []

        saved = dict(tmpl)
        saved["is_custom"] = True
        custom = [t for t in custom if t.get("id") != saved.get("id")]
        custom.append(saved)
        custom_path.write_text(json.dumps(custom, indent=2, ensure_ascii=False) + "\n", "utf-8")
        self._templates[saved["id"]] = saved
        return saved

    def delete_custom_template(self, template_id: str) -> bool:
        tmpl = self._templates.get(template_id)
        if not tmpl or not tmpl.get("is_custom"):
            return False

        custom_path = self._path.parent / "custom_templates.json"
        custom = []
        if custom_path.exists():
            try:
                custom = json.loads(custom_path.read_text("utf-8"))
            except (json.JSONDecodeError, KeyError):
                custom = []

        new_custom = [t for t in custom if t.get("id") != template_id]
        if len(new_custom) != len(custom):
            custom_path.write_text(json.dumps(new_custom, indent=2, ensure_ascii=False) + "\n", "utf-8")

        self._templates.pop(template_id, None)
        return True

    # --- Callbacks ---

    def on_change(self, callback):
        """Register a callback(action, session) on any change.
        action: 'create', 'update', 'complete', 'interrupt'."""
        self._callbacks.append(callback)

    def _fire(self, action: str, session: dict):
        for cb in self._callbacks:
            try:
                cb(action, session)
            except Exception:
                pass

    # --- Session lifecycle ---

    def create(self, template_id: str, channel: str, cast: dict,
               started_by: str, goal: str = "") -> dict | None:
        """Create and persist a new session run."""
        tmpl = self._templates.get(template_id)
        if not tmpl:
            return None

        with self._lock:
            # One active session per channel
            for s in self._sessions:
                if s.get("channel") == channel and s.get("state") in ("active", "waiting", "paused"):
                    return None

            session = {
                "id": self._next_id,
                "template_id": template_id,
                "template_name": tmpl.get("name", template_id),
                "channel": channel,
                "cast": cast,
                "state": "active",
                "current_phase": 0,
                "current_turn": 0,
                "started_by": started_by,
                "started_at": time.time(),
                "updated_at": time.time(),
                "last_message_id": None,
                "output_message_id": None,
                "goal": goal.strip()[:500],
            }
            self._next_id += 1
            self._sessions.append(session)
            self._save()

        self._fire("create", session)
        return session

    def get(self, session_id: int) -> dict | None:
        with self._lock:
            for s in self._sessions:
                if s["id"] == session_id:
                    return dict(s)
            return None

    def get_active(self, channel: str) -> dict | None:
        """Get the active/waiting/paused session for a channel."""
        with self._lock:
            for s in self._sessions:
                if s.get("channel") == channel and s.get("state") in ("active", "waiting", "paused"):
                    return dict(s)
            return None

    def list_all(self, channel: str | None = None) -> list[dict]:
        with self._lock:
            result = list(self._sessions)
        if channel:
            result = [s for s in result if s.get("channel") == channel]
        return result

    def advance_turn(self, session_id: int, message_id: int | None = None) -> dict | None:
        """Advance to the next turn within the current phase."""
        with self._lock:
            session = self._find(session_id)
            if not session or session["state"] not in ("active", "waiting"):
                return None
            session["current_turn"] += 1
            session["state"] = "active"
            session["updated_at"] = time.time()
            if message_id is not None:
                session["last_message_id"] = message_id
            self._save()
            result = dict(session)
        self._fire("update", result)
        return result

    def advance_phase(self, session_id: int, message_id: int | None = None) -> dict | None:
        """Advance to the next phase, resetting turn to 0."""
        with self._lock:
            session = self._find(session_id)
            if not session or session["state"] not in ("active", "waiting"):
                return None
            session["current_phase"] += 1
            session["current_turn"] = 0
            session["state"] = "active"
            session["updated_at"] = time.time()
            if message_id is not None:
                session["last_message_id"] = message_id
            self._save()
            result = dict(session)
        self._fire("update", result)
        return result

    def set_waiting(self, session_id: int, agent: str) -> dict | None:
        """Mark session as waiting on a specific agent."""
        with self._lock:
            session = self._find(session_id)
            if not session:
                return None
            session["state"] = "waiting"
            session["waiting_on"] = agent
            session["updated_at"] = time.time()
            self._save()
            result = dict(session)
        self._fire("update", result)
        return result

    def set_choice(self, session_id: int, message_id: int | None) -> dict | None:
        """Keep the current turn while its participant asks the human a question."""
        with self._lock:
            session = self._find(session_id)
            if not session or session["state"] not in ("active", "waiting", "paused"):
                return None
            session["choice_message_id"] = message_id
            if message_id is not None:
                session["last_choice_message_id"] = message_id
            session["updated_at"] = time.time()
            self._save()
            result = dict(session)
        self._fire("update", result)
        return result

    def pause(self, session_id: int) -> dict | None:
        """Pause session (human interruption)."""
        with self._lock:
            session = self._find(session_id)
            if not session or session["state"] not in ("active", "waiting"):
                return None
            session["state"] = "paused"
            session["updated_at"] = time.time()
            self._save()
            result = dict(session)
        self._fire("update", result)
        return result

    def resume(self, session_id: int) -> dict | None:
        """Resume a paused session."""
        with self._lock:
            session = self._find(session_id)
            if not session or session["state"] != "paused":
                return None
            session["state"] = "active"
            session["updated_at"] = time.time()
            self._save()
            result = dict(session)
        self._fire("update", result)
        return result

    def complete(self, session_id: int, output_message_id: int | None = None) -> dict | None:
        """Mark session as complete."""
        with self._lock:
            session = self._find(session_id)
            if not session:
                return None
            session["state"] = "complete"
            session["updated_at"] = time.time()
            if output_message_id is not None:
                session["output_message_id"] = output_message_id
            self._save()
            result = dict(session)
        self._fire("complete", result)
        return result

    def interrupt(self, session_id: int, reason: str = "ended by user") -> dict | None:
        """End session early."""
        with self._lock:
            session = self._find(session_id)
            if not session or session["state"] in ("complete", "interrupted"):
                return None
            session["state"] = "interrupted"
            session["interrupt_reason"] = reason
            session["updated_at"] = time.time()
            self._save()
            result = dict(session)
        self._fire("interrupt", result)
        return result

    def _find(self, session_id: int) -> dict | None:
        """Find session by ID (caller must hold lock)."""
        for s in self._sessions:
            if s["id"] == session_id:
                return s
        return None


def validate_session_template(tmpl: dict) -> list[str]:
    """Validate a session template dict. Returns list of errors (empty = valid)."""
    errors = []

    if not isinstance(tmpl, dict):
        return ["Template must be a JSON object"]

    if not tmpl.get("name") or not isinstance(tmpl.get("name"), str):
        errors.append("Missing or invalid 'name' (string required)")

    roles = tmpl.get("roles", [])
    if not isinstance(roles, list) or len(roles) == 0:
        errors.append("'roles' must be a non-empty array")
    elif len(roles) > 6:
        errors.append(f"Too many roles ({len(roles)}, max 6)")

    phases = tmpl.get("phases", [])
    if not isinstance(phases, list) or len(phases) == 0:
        errors.append("'phases' must be a non-empty array")
    elif len(phases) > 6:
        errors.append(f"Too many phases ({len(phases)}, max 6)")

    roles_set = set(roles) if isinstance(roles, list) else set()
    output_count = 0

    for i, phase in enumerate(phases if isinstance(phases, list) else []):
        if not isinstance(phase, dict):
            errors.append(f"Phase {i + 1}: must be an object")
            continue
        if not phase.get("name"):
            errors.append(f"Phase {i + 1}: missing 'name'")
        participants = phase.get("participants", [])
        if not isinstance(participants, list) or len(participants) == 0:
            errors.append(f"Phase {i + 1}: 'participants' must be a non-empty array")
        elif len(participants) > 4:
            errors.append(f"Phase {i + 1}: too many participants ({len(participants)}, max 4)")
        for p in (participants if isinstance(participants, list) else []):
            if p not in roles_set:
                errors.append(f"Phase {i + 1}: participant '{p}' not in roles list")
        prompt = phase.get("prompt", "")
        if isinstance(prompt, str) and len(prompt) > 200:
            errors.append(f"Phase {i + 1}: prompt too long ({len(prompt)} chars, max 200)")
        if phase.get("is_output"):
            output_count += 1

    if output_count == 0 and isinstance(phases, list) and len(phases) > 0:
        errors.append("No phase marked as 'is_output: true'")
    elif output_count > 1:
        errors.append(f"Multiple phases marked as output ({output_count}, expected 1)")

    return errors
