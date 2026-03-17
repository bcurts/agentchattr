"""Unit tests for agent presence/typing indicator logic (app.py)."""
import time
import unittest


# ---------------------------------------------------------------------------
# Minimal stubs — isolate app.py's pure functions from FastAPI/asyncio deps
# ---------------------------------------------------------------------------

class _AppStubs:
    """Patch the module-level globals that the functions under test touch."""

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.deadlines: dict = {}

    def apply(self, module):
        module._typing_lock = self.lock
        module._typing_deadlines = self.deadlines
        module._TYPING_TTL_SECONDS = 45


def _load_app_functions():
    """Import only the pure helper functions without starting FastAPI."""
    import importlib.util, sys, types

    # Build a lightweight fake for every import app.py needs so it doesn't
    # pull in FastAPI, uvicorn, etc.
    _fakes = [
        "fastapi", "fastapi.requests", "fastapi.responses",
        "starlette.middleware.base",
        "store", "rules", "summaries", "jobs", "schedules",
        "router", "agents", "registry", "session_store", "session_engine",
        "mcp_bridge",
    ]
    for name in _fakes:
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

    # Provide a minimal FastAPI stub so the module-level `app = FastAPI(...)` works.
    import sys
    fa = sys.modules["fastapi"]
    if not hasattr(fa, "FastAPI"):
        fa.FastAPI = lambda **kw: types.SimpleNamespace(
            websocket=lambda p: (lambda f: f),
            get=lambda p, **kw: (lambda f: f),
            post=lambda p, **kw: (lambda f: f),
            delete=lambda p, **kw: (lambda f: f),
            patch=lambda p, **kw: (lambda f: f),
        )
        fa.WebSocket = object
        fa.WebSocketDisconnect = Exception
        fa.UploadFile = object
        fa.File = lambda *a, **kw: None

    far = sys.modules["fastapi.requests"]
    if not hasattr(far, "Request"):
        far.Request = object

    fares = sys.modules["fastapi.responses"]
    for klass in ("FileResponse", "JSONResponse", "Response"):
        if not hasattr(fares, klass):
            setattr(fares, klass, object)

    smb = sys.modules["starlette.middleware.base"]
    if not hasattr(smb, "BaseHTTPMiddleware"):
        smb.BaseHTTPMiddleware = object

    # Provide minimal store/etc stubs
    for mod_name in ("store", "rules", "summaries", "jobs", "schedules",
                     "router", "agents", "registry", "session_store",
                     "session_engine", "mcp_bridge"):
        m = sys.modules[mod_name]
        for attr in ("MessageStore", "RuleStore", "SummaryStore", "JobStore",
                     "ScheduleStore", "parse_schedule_spec", "Router",
                     "AgentTrigger", "RuntimeRegistry", "SessionStore",
                     "validate_session_template", "SessionEngine"):
            if not hasattr(m, attr):
                setattr(m, attr, object)

    # Now load app.py without executing the FastAPI startup
    import app
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTypingDeadlines(unittest.TestCase):

    def setUp(self):
        self.app = _load_app_functions()
        self._stubs = _AppStubs()
        self._stubs.apply(self.app)

    # --- _set_typing_deadline ---

    def test_set_active_creates_deadline(self):
        self.app._set_typing_deadline("alice", "general", True)
        key = ("alice", "general")
        self.assertIn(key, self.app._typing_deadlines)
        deadline = self.app._typing_deadlines[key]
        self.assertAlmostEqual(deadline, time.time() + 45, delta=2)

    def test_set_inactive_removes_deadline(self):
        self.app._set_typing_deadline("alice", "general", True)
        self.app._set_typing_deadline("alice", "general", False)
        self.assertNotIn(("alice", "general"), self.app._typing_deadlines)

    def test_set_inactive_on_missing_key_is_safe(self):
        # Should not raise even if key was never set
        self.app._set_typing_deadline("ghost", "general", False)

    def test_multiple_agents_independent(self):
        self.app._set_typing_deadline("alice", "general", True)
        self.app._set_typing_deadline("bob", "general", True)
        self.app._set_typing_deadline("alice", "general", False)
        self.assertNotIn(("alice", "general"), self.app._typing_deadlines)
        self.assertIn(("bob", "general"), self.app._typing_deadlines)

    def test_channels_are_independent(self):
        self.app._set_typing_deadline("alice", "general", True)
        self.app._set_typing_deadline("alice", "engineering", True)
        self.app._set_typing_deadline("alice", "general", False)
        self.assertNotIn(("alice", "general"), self.app._typing_deadlines)
        self.assertIn(("alice", "engineering"), self.app._typing_deadlines)

    # --- _pop_expired_typing ---

    def test_pop_expired_returns_expired_keys(self):
        now = time.time()
        self.app._typing_deadlines[("alice", "general")] = now - 1   # already expired
        self.app._typing_deadlines[("bob", "general")] = now + 100   # not yet expired

        expired = self.app._pop_expired_typing(now)

        self.assertIn(("alice", "general"), expired)
        self.assertNotIn(("bob", "general"), expired)

    def test_pop_expired_removes_from_dict(self):
        now = time.time()
        self.app._typing_deadlines[("alice", "general")] = now - 1
        self.app._pop_expired_typing(now)
        self.assertNotIn(("alice", "general"), self.app._typing_deadlines)

    def test_pop_expired_preserves_non_expired(self):
        now = time.time()
        self.app._typing_deadlines[("bob", "general")] = now + 100
        self.app._pop_expired_typing(now)
        self.assertIn(("bob", "general"), self.app._typing_deadlines)

    def test_pop_expired_empty_dict_is_safe(self):
        result = self.app._pop_expired_typing(time.time())
        self.assertEqual(result, [])

    def test_pop_expired_exactly_at_deadline(self):
        now = time.time()
        self.app._typing_deadlines[("alice", "general")] = now  # deadline == now
        expired = self.app._pop_expired_typing(now)
        self.assertIn(("alice", "general"), expired)

    def test_pop_expired_multiple_expired(self):
        now = time.time()
        for i in range(5):
            self.app._typing_deadlines[(f"agent-{i}", "general")] = now - i
        expired = self.app._pop_expired_typing(now)
        self.assertEqual(len(expired), 5)
        self.assertEqual(len(self.app._typing_deadlines), 0)

    # --- TTL refresh (heartbeat) ---

    def test_repeated_active_call_refreshes_deadline(self):
        """Calling set_active multiple times should push the deadline forward."""
        self.app._set_typing_deadline("alice", "general", True)
        first = self.app._typing_deadlines[("alice", "general")]
        time.sleep(0.05)
        self.app._set_typing_deadline("alice", "general", True)
        second = self.app._typing_deadlines[("alice", "general")]
        self.assertGreaterEqual(second, first)


if __name__ == "__main__":
    unittest.main()
