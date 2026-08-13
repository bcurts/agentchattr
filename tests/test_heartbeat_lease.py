"""Fault tests for the heartbeat-silence / identity-drift bug.

Root scenario: a CLI agent (opencode) wrapper keeps running but its
heartbeats stop reaching the server. After ~90s the server deregistered the
still-alive wrapper, the wrapper re-registered and got a NEW name/token
(opencode -> opencode-2 -> opencode loops), breaking the already-running
child's MCP token.

Fix under test:
  - wrapper registers/heartbeats with a stable lease_id + its own PID
  - server crash-timeout checks PID liveness before deregistering
  - registry can resume the original name/token for a known lease
    (including after a server restart, via data/leases.json)
  - wrapper heartbeat sending is merged into HeartbeatSender with
    rate-limited, token-free logging
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import mcp_bridge  # noqa: E402
from launcher_supervisor import pid_is_alive, process_start_marker  # noqa: E402
from registry import RuntimeRegistry  # noqa: E402
from wrapper import HeartbeatSender, _activity_monitor_loop, _register_instance  # noqa: E402


def _dead_pid() -> int:
    """Return a PID that was once valid but is now gone."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _spawn_child() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def _reap(proc: subprocess.Popen):
    """Cleanup helper: kill AND wait so no ResourceWarning is emitted."""
    try:
        proc.kill()
    except Exception:
        pass
    proc.wait()


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Registry lease behaviour
# ---------------------------------------------------------------------------

class RegistryLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})

    def test_repeated_register_with_same_lease_keeps_identity(self):
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        second = self.registry.register("opencode", lease_id="lease-a", pid=1234,
                                        resume_token=first["token"])
        self.assertEqual(first["name"], second["name"])
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(self.registry.get_all_names(), [first["name"]])

    def test_live_lease_with_wrong_proof_is_rejected(self):
        # A KNOWN lease_id (in memory) must always verify the old token.
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        rejected = self.registry.register("opencode", lease_id="lease-a", pid=1234,
                                          resume_token="wrong-token")
        self.assertEqual(rejected.get("error"), "invalid_lease_proof")
        # No new token minted, instance untouched.
        inst = self.registry.get_instance("opencode")
        self.assertEqual(inst["name"], "opencode")
        self.assertEqual(self.registry.resolve_token(first["token"])["name"], "opencode")

    def test_live_lease_with_missing_proof_is_rejected(self):
        self.registry.register("opencode", lease_id="lease-a", pid=1234)
        rejected = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        self.assertEqual(rejected.get("error"), "invalid_lease_proof")

    def test_invalid_proof_does_not_corrupt_lease(self):
        # Wrong proof must neither mint a token nor overwrite the stored digest:
        # the rightful wrapper can still recover afterwards.
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        before = (Path(self.tmp.name) / "leases.json").read_text("utf-8")

        rejected = restarted.register("opencode", lease_id="lease-a", pid=1234,
                                      resume_token="wrong-token")
        self.assertEqual(rejected.get("error"), "invalid_lease_proof")
        self.assertEqual(restarted.get_all_names(), [])  # no instance created
        after = (Path(self.tmp.name) / "leases.json").read_text("utf-8")
        self.assertEqual(json.loads(before), json.loads(after))  # lease unmodified

        recovered = restarted.register("opencode", lease_id="lease-a", pid=1234,
                                       resume_token=first["token"])
        self.assertEqual(recovered["name"], first["name"])
        self.assertEqual(recovered["token"], first["token"])

    def test_single_instance_has_no_numbered_slot(self):
        result = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        self.assertEqual(result["name"], "opencode")
        self.assertEqual(result["slot"], 1)
        # No rename entries generated for a lone instance.
        self.assertEqual(self.registry._renames, {})

    def test_lease_file_contains_only_token_digest(self):
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        raw = (Path(self.tmp.name) / "leases.json").read_text("utf-8")
        # The plaintext bearer token must NEVER hit disk.
        self.assertNotIn(first["token"], raw)
        entry = json.loads(raw)["lease-a"]
        self.assertNotIn("token", entry)
        self.assertEqual(
            entry["token_digest"],
            hashlib.sha256(first["token"].encode()).hexdigest(),
        )

    def test_lease_survives_server_restart_same_name_and_token(self):
        # Server restart = brand new RuntimeRegistry over the same data dir.
        # The wrapper proves ownership of the lease by presenting its old token
        # (resume_token); the server verifies it against the stored digest.
        first = self.registry.register(
            "opencode", preferred_name="opencode", lease_id="lease-a", pid=1234
        )
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        recovered = restarted.register(
            "opencode", preferred_name="opencode", lease_id="lease-a", pid=1234,
            resume_token=first["token"],
        )
        self.assertEqual(recovered["name"], first["name"])
        self.assertEqual(recovered["token"], first["token"])
        # The running child's MCP config (old token) stays valid: the token
        # must resolve to the instance again.
        resolved = restarted.resolve_token(first["token"])
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["name"], first["name"])

    def test_restart_recovery_with_wrong_resume_token_fails(self):
        first = self.registry.register(
            "opencode", preferred_name="opencode", lease_id="lease-a", pid=1234
        )
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        rejected = restarted.register(
            "opencode", preferred_name="opencode", lease_id="lease-a", pid=1234,
            resume_token="not-the-real-token",
        )
        # Digest mismatch → explicit error, NO fresh registration, lease intact.
        self.assertEqual(rejected.get("error"), "invalid_lease_proof")
        self.assertEqual(restarted.get_all_names(), [])
        self.assertIsNone(restarted.resolve_token(first["token"]))

    def test_restart_recovery_without_resume_token_fails(self):
        first = self.registry.register(
            "opencode", preferred_name="opencode", lease_id="lease-a", pid=1234
        )
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        rejected = restarted.register(
            "opencode", preferred_name="opencode", lease_id="lease-a", pid=1234,
        )
        self.assertEqual(rejected.get("error"), "invalid_lease_proof")
        self.assertEqual(restarted.get_all_names(), [])

    def test_restart_recovery_creates_no_rename_loops(self):
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        restarted.register("opencode", lease_id="lease-a", pid=1234,
                           resume_token=first["token"])
        renames = json.loads((Path(self.tmp.name) / "renames.json").read_text("utf-8")) \
            if (Path(self.tmp.name) / "renames.json").exists() else {}
        for src, dst in renames.items():
            self.assertNotEqual(renames.get(dst), src, f"bidirectional rename loop {src}<->{dst}")
        self.assertEqual(renames, {})

    def test_explicit_deregister_releases_lease_and_slot_exactly_once(self):
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        result = self.registry.deregister("opencode")
        self.assertIsNotNone(result)
        # Second deregister is a no-op (cleaned up exactly once).
        self.assertIsNone(self.registry.deregister("opencode"))
        # The lease was released: a re-register with the same lease_id starts
        # fresh (new token) instead of resurrecting the old session.
        second = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        self.assertNotEqual(second["token"], first["token"])
        # Slot reusable once the anti-theft grace reservation has expired.
        self.registry._reserved.clear()
        third = self.registry.register("opencode", lease_id="lease-c", pid=1234)
        self.assertEqual(third["name"], "opencode")
        self.assertEqual(third["slot"], 1)

    def test_conflicting_lease_cannot_take_over_used_name(self):
        self.registry.register("opencode", preferred_name="opencode",
                               lease_id="lease-a", pid=1234)
        stolen = self.registry.register("opencode", preferred_name="opencode",
                                        lease_id="lease-b", pid=5678)
        self.assertEqual(stolen.get("error"), "preferred_name_in_use")
        # Original instance untouched.
        inst = self.registry.get_instance("opencode")
        self.assertEqual(inst["lease_id"], "lease-a")

    def test_register_without_lease_still_works(self):
        result = self.registry.register("opencode")
        self.assertEqual(result["name"], "opencode")
        self.assertEqual(result["lease_id"], "")
        self.assertEqual(result["pid"], 0)


# ---------------------------------------------------------------------------
# Crash-timeout process-liveness decision
# ---------------------------------------------------------------------------

class CrashTimeoutActionTests(unittest.TestCase):
    def test_silent_but_wrapper_pid_alive_retains_registration(self):
        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=1000.0,
            now=1000.0 + 91,
            inst={"pid": os.getpid(), "lease_id": "x"},
            pid_alive_fn=pid_is_alive,
        )
        self.assertEqual(action, "retain")

    def test_silent_and_wrapper_pid_dead_deregisters(self):
        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=1000.0,
            now=1000.0 + 91,
            inst={"pid": _dead_pid(), "lease_id": "x"},
            pid_alive_fn=pid_is_alive,
        )
        self.assertEqual(action, "deregister")

    def test_silent_without_pid_deregisters(self):
        # Legacy wrapper never reported a PID — fall back to old behaviour.
        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=1000.0,
            now=1000.0 + 91,
            inst={"pid": 0},
            pid_alive_fn=pid_is_alive,
        )
        self.assertEqual(action, "deregister")

    def test_missing_instance_deregisters(self):
        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=1000.0,
            now=1000.0 + 91,
            inst=None,
            pid_alive_fn=pid_is_alive,
        )
        self.assertEqual(action, "deregister")

    def test_fresh_presence_is_ok(self):
        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=1000.0,
            now=1000.0 + 5,
            inst={"pid": 0},
            pid_alive_fn=pid_is_alive,
        )
        self.assertEqual(action, "ok")

    def test_never_seen_is_ok(self):
        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=0,
            now=10_000.0,
            inst={"pid": 0},
            pid_alive_fn=pid_is_alive,
        )
        self.assertEqual(action, "ok")

    def test_pid_reuse_fingerprint_mismatch_treated_as_dead(self):
        # A different process now holding the recorded PID (start-marker
        # mismatch) must be treated as "wrapper dead" → deregister.
        calls = []

        def fake_alive(pid, start_marker=""):
            calls.append((pid, start_marker))
            return False  # server-side liveness incl. fingerprint failed

        action = app_module._crash_timeout_action(
            "opencode",
            last_seen=1000.0,
            now=1000.0 + 91,
            inst={"pid": 4321, "lease_id": "x", "start_marker": "creation-t0"},
            pid_alive_fn=fake_alive,
        )
        self.assertEqual(action, "deregister")
        # The recorded fingerprint must be passed to the liveness check.
        self.assertEqual(calls, [(4321, "creation-t0")])


class PidIsAliveTests(unittest.TestCase):
    def test_current_process_is_alive(self):
        self.assertTrue(pid_is_alive(os.getpid()))

    def test_dead_pid_is_not_alive(self):
        self.assertFalse(pid_is_alive(_dead_pid()))

    def test_zero_and_none_are_not_alive(self):
        self.assertFalse(pid_is_alive(0))
        self.assertFalse(pid_is_alive(None))

    def test_live_child_with_matching_start_marker(self):
        p = _spawn_child()
        self.addCleanup(_reap, p)
        marker = process_start_marker(p.pid)
        if not marker:
            self.skipTest("no process start marker on this platform")
        self.assertTrue(pid_is_alive(p.pid, marker))

    def test_live_child_with_wrong_start_marker_is_rejected(self):
        # Simulates PID reuse: pid alive, but the creation fingerprint differs.
        p = _spawn_child()
        self.addCleanup(_reap, p)
        self.assertFalse(pid_is_alive(p.pid, "bogus-marker"))

    def test_dead_pid_with_start_marker_is_not_alive(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        marker = process_start_marker(p.pid)
        p.wait()
        self.assertFalse(pid_is_alive(p.pid, marker))

    def test_start_marker_is_stable_per_process(self):
        p = _spawn_child()
        self.addCleanup(_reap, p)
        m1 = process_start_marker(p.pid)
        m2 = process_start_marker(p.pid)
        if not m1:
            self.skipTest("no process start marker on this platform")
        self.assertEqual(m1, m2)


class OfflineActionTests(unittest.TestCase):
    """Unified presence semantics: wrapper-registered instances (lease pid
    recorded) degrade silently at presence expiry; pure MCP clients (no
    wrapper lease/pid) keep the old presence-based leave behavior."""

    def test_wrapper_instance_with_pid_degrades_without_leave(self):
        self.assertEqual(app_module._offline_action_for({"pid": 1234}), "degrade")

    def test_mcp_only_client_keeps_leave_behavior(self):
        self.assertEqual(app_module._offline_action_for({"pid": 0}), "leave")
        self.assertEqual(app_module._offline_action_for({}), "leave")
        self.assertEqual(app_module._offline_action_for(None), "leave")


# ---------------------------------------------------------------------------
# Wrapper HeartbeatSender (merged heartbeat + activity sender)
# ---------------------------------------------------------------------------

class HeartbeatSenderTests(unittest.TestCase):
    SECRET = "SECRETTOKEN123"

    def _make_sender(self, now_ref):
        identity = {"name": "opencode", "token": self.SECRET}
        logs = []
        sender = HeartbeatSender(
            server_port=8300,
            get_identity=lambda: (identity["name"], None),
            get_token=lambda: identity["token"],
            lease_id="lease-a",
            pid=4321,
            log_fn=logs.append,
            now_fn=lambda: now_ref[0],
        )
        return sender, logs

    def test_success_sends_lease_pid_and_auth_header(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            captured["auth"] = req.headers.get("Authorization")
            return _FakeResp({"ok": True, "name": "opencode"})

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            result = sender.send()
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "opencode")
        self.assertEqual(captured["url"], "http://127.0.0.1:8300/api/heartbeat/opencode")
        self.assertEqual(captured["body"]["lease_id"], "lease-a")
        self.assertEqual(captured["body"]["pid"], 4321)
        self.assertNotIn("active", captured["body"])
        self.assertEqual(captured["auth"], f"Bearer {self.SECRET}")

    def test_healthy_heartbeats_do_not_spam_logs(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=0: _FakeResp({"ok": True, "name": "opencode"})):
            for _ in range(5):
                sender.send()
        self.assertEqual(logs, [])

    def test_first_failure_logged_with_http_code_then_rate_limited(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        err = urllib.error.HTTPError("http://x", 500, "Server Error", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            sender.send()
            sender.send()  # rate-limited: no second log
        self.assertEqual(len(logs), 1)
        self.assertIn("HTTP 500", logs[0])

    def test_failure_summary_after_interval(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        err = urllib.error.HTTPError("http://x", 503, "Busy", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            sender.send()                       # t=1000 first failure log
            now_ref[0] += 5
            sender.send()                       # rate-limited
            now_ref[0] += sender.LOG_INTERVAL   # past the summary interval
            sender.send()                       # summary log
        self.assertEqual(len(logs), 2)
        self.assertIn("3", logs[1])
        self.assertIn("consecutive", logs[1].lower())

    def test_recovery_logged_after_failures(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        err = urllib.error.URLError("connection refused")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            sender.send()
            sender.send()
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=0: _FakeResp({"ok": True, "name": "opencode"})):
            sender.send()
        self.assertTrue(any("recover" in line.lower() for line in logs))
        self.assertEqual(sender.consecutive_failures, 0)

    def test_exception_type_logged_on_network_error(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("boom")):
            sender.send()
        self.assertEqual(len(logs), 1)
        self.assertIn("URLError", logs[0])

    def test_logs_never_contain_token(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        err = urllib.error.HTTPError("http://x", 409, "Conflict", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            sender.send()
            now_ref[0] += sender.LOG_INTERVAL + 1
            sender.send()
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("refused")):
            sender.send()
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=0: _FakeResp({"ok": True, "name": "opencode"})):
            sender.send()
        self.assertGreaterEqual(len(logs), 2)
        for line in logs:
            self.assertNotIn(self.SECRET, line)

    def test_409_reported_to_caller_without_raising(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        err = urllib.error.HTTPError("http://x", 409, "Conflict", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            result = sender.send()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 409)

    def test_active_flag_included_when_provided(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        bodies = []

        def fake_urlopen(req, timeout=0):
            bodies.append(json.loads(req.data.decode()))
            return _FakeResp({"ok": True, "name": "opencode"})

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            sender.send(active=True)
            sender.send(active=False)
        self.assertEqual(bodies[0]["active"], True)
        self.assertEqual(bodies[1]["active"], False)
        # lease info rides along on activity heartbeats too
        self.assertEqual(bodies[0]["lease_id"], "lease-a")
        self.assertEqual(bodies[0]["pid"], 4321)

    def test_concurrent_sends_keep_failure_accounting_correct(self):
        # Two threads share one sender in production (heartbeat + activity).
        # Hammer it from four threads: every failure must be counted exactly
        # once and the rate limiter must not lose or duplicate windows.
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        err = urllib.error.HTTPError("http://x", 500, "Server Error", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            threads = [
                threading.Thread(target=lambda: [sender.send() for _ in range(25)])
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(sender.consecutive_failures, 100)
        # Static fake clock → only the first-failure log, no summary windows.
        self.assertEqual(len(logs), 1)
        self.assertIn("HTTP 500", logs[0])

    def test_concurrent_mixed_outcomes_end_consistent(self):
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        lock = threading.Lock()
        state = {"fail": True}

        def fake_urlopen(req, timeout=0):
            with lock:
                failing = state["fail"]
            if failing:
                raise urllib.error.URLError("boom")
            return _FakeResp({"ok": True, "name": "opencode"})

        def worker(fail_sends, ok_sends):
            for _ in range(fail_sends):
                sender.send()
            with lock:
                state["fail"] = False
            for _ in range(ok_sends):
                sender.send()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            threads = [threading.Thread(target=worker, args=(10, 5)) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        # All sends succeeded at the end → counter must be reset to 0.
        self.assertEqual(sender.consecutive_failures, 0)
        for line in logs:
            self.assertNotIn(self.SECRET, line)

    def test_terminal_state_stops_all_network_sends(self):
        # invalid_lease_proof is terminal: no more heartbeat/register hammering.
        now_ref = [1000.0]
        sender, logs = self._make_sender(now_ref)
        sender.terminal = True
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            result = sender.send()
            result_active = sender.send(active=True)
        self.assertEqual(urlopen_mock.call_count, 0)
        self.assertFalse(result["ok"])
        self.assertTrue(result["terminal"])
        self.assertTrue(result_active["terminal"])
        self.assertEqual(logs, [])  # terminal short-circuit does not spam logs

    def test_register_instance_sends_lease_pid_marker_and_resume_token(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode())
            return _FakeResp({"name": "opencode", "token": "T"})

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            _register_instance(8300, "opencode", "Label", "opencode",
                               lease_id="lease-a", pid=42,
                               start_marker="creation-t0", resume_token="OLDTOKEN")
        body = captured["body"]
        self.assertEqual(body["base"], "opencode")
        self.assertEqual(body["lease_id"], "lease-a")
        self.assertEqual(body["pid"], 42)
        self.assertEqual(body["start_marker"], "creation-t0")
        self.assertEqual(body["resume_token"], "OLDTOKEN")


class ActivityMonitorLoopTests(unittest.TestCase):
    def test_checker_exception_does_not_kill_monitor(self):
        calls = {"n": 0}

        def checker():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("win32 console read boom")
            return True

        sends = []
        sender = SimpleNamespace(send=lambda active=None: sends.append(active) or {"ok": True})
        logs = []
        iterations = {"n": 0}

        def should_run():
            iterations["n"] += 1
            return iterations["n"] <= 5

        _activity_monitor_loop(
            lambda: checker,
            sender,
            should_run=should_run,
            sleep_fn=lambda s: None,
            log_fn=logs.append,
        )
        # Loop kept going after the exception and sent activity heartbeats.
        self.assertGreaterEqual(len(sends), 1)
        self.assertTrue(any("activity checker" in line.lower() for line in logs))
        self.assertTrue(any("RuntimeError" in line for line in logs))

    def test_no_checker_keeps_looping_without_sending(self):
        sends = []
        sender = SimpleNamespace(send=lambda active=None: sends.append(active) or {"ok": True})
        iterations = {"n": 0}

        def should_run():
            iterations["n"] += 1
            return iterations["n"] <= 3

        _activity_monitor_loop(
            lambda: None,
            sender,
            should_run=should_run,
            sleep_fn=lambda s: None,
            log_fn=lambda m: None,
        )
        self.assertEqual(sends, [])
        self.assertGreaterEqual(iterations["n"], 3)


# ---------------------------------------------------------------------------
# Register endpoint lease handling
# ---------------------------------------------------------------------------

class FakeRequest:
    def __init__(self, body=None, headers=None):
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.query_params = {}

    async def json(self):
        return self._body


class RegisterEndpointLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_registry = app_module.registry
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        app_module.registry = self.registry

    async def asyncTearDown(self):
        app_module.registry = self.old_registry
        mcp_bridge.purge_identity("opencode")

    async def test_same_lease_online_preferred_name_does_not_409(self):
        first = self.registry.register("opencode", preferred_name="opencode",
                                       lease_id="lease-a", pid=1234)
        mcp_bridge._touch_presence("opencode")  # instance looks online
        request = FakeRequest(body={
            "base": "opencode",
            "preferred_name": "opencode",
            "lease_id": "lease-a",
            "pid": 1234,
            "resume_token": first["token"],
        })
        response = await app_module.register_agent(request)
        self.assertEqual(response.status_code, 200)
        payload = json.loads(bytes(response.body).decode())
        self.assertEqual(payload["name"], first["name"])
        self.assertEqual(payload["token"], first["token"])
        self.assertEqual(self.registry.get_all_names(), ["opencode"])

    async def test_same_lease_wrong_proof_returns_409_invalid_lease_proof(self):
        first = self.registry.register("opencode", preferred_name="opencode",
                                       lease_id="lease-a", pid=1234)
        mcp_bridge._touch_presence("opencode")
        request = FakeRequest(body={
            "base": "opencode",
            "preferred_name": "opencode",
            "lease_id": "lease-a",
            "pid": 1234,
            "resume_token": "wrong-token",
        })
        response = await app_module.register_agent(request)
        self.assertEqual(response.status_code, 409)
        payload = json.loads(bytes(response.body).decode())
        self.assertEqual(payload["error"], "invalid_lease_proof")
        # Identity untouched, no new token minted.
        self.assertEqual(self.registry.get_all_names(), ["opencode"])
        self.assertIsNotNone(self.registry.resolve_token(first["token"]))

    async def test_different_lease_online_preferred_name_still_409(self):
        self.registry.register("opencode", preferred_name="opencode",
                               lease_id="lease-a", pid=1234)
        mcp_bridge._touch_presence("opencode")
        request = FakeRequest(body={
            "base": "opencode",
            "preferred_name": "opencode",
            "lease_id": "lease-b",
            "pid": 5678,
        })
        response = await app_module.register_agent(request)
        self.assertEqual(response.status_code, 409)


class HeartbeatEndpointLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_registry = app_module.registry
        self.old_agents = app_module.agents
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed({"opencode": {"label": "OpenCode", "color": "#222222"}})
        app_module.registry = self.registry
        # broadcast_status() fires on activity changes; stub it out
        app_module.agents = SimpleNamespace(get_status=lambda: {})
        self.old_router = app_module.router
        app_module.router = SimpleNamespace(is_paused=lambda ch: False)

    async def asyncTearDown(self):
        app_module.registry = self.old_registry
        app_module.agents = self.old_agents
        app_module.router = self.old_router
        mcp_bridge.purge_identity("opencode")

    async def test_heartbeat_binds_lease_and_pid(self):
        inst = self.registry.register("opencode")  # legacy: no lease at register
        request = FakeRequest(
            body={"lease_id": "lease-a", "pid": 1234, "active": True,
                  "start_marker": "creation-t0"},
            headers={"authorization": f"Bearer {inst['token']}"},
        )
        response = await app_module.heartbeat("opencode", request)
        self.assertTrue(response["ok"])  # heartbeat returns a plain dict (200)
        updated = self.registry.get_instance("opencode")
        self.assertEqual(updated["lease_id"], "lease-a")
        self.assertEqual(updated["pid"], 1234)
        self.assertEqual(updated["start_marker"], "creation-t0")

    async def test_heartbeat_lease_recovery_after_restart_presence_touch(self):
        inst = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        request = FakeRequest(
            body={"lease_id": "lease-a", "pid": 1234},
            headers={"authorization": f"Bearer {inst['token']}"},
        )
        response = await app_module.heartbeat("opencode", request)
        self.assertTrue(response["ok"])
        self.assertEqual(response["name"], "opencode")


# ---------------------------------------------------------------------------
# Degraded status annotation (API semantics for "running / chat abnormal")
# ---------------------------------------------------------------------------

class DegradedStatusTests(unittest.TestCase):
    def test_annotate_marks_degraded_instances(self):
        old = set(app_module._degraded_instances)
        try:
            app_module._degraded_instances.clear()
            app_module._degraded_instances.add("opencode")
            status = {"opencode": {"available": False}, "kimi": {"available": True}}
            annotated = app_module._annotate_degraded(status)
            self.assertTrue(annotated["opencode"]["presence_stale"])
            self.assertFalse(annotated["kimi"]["presence_stale"])
        finally:
            app_module._degraded_instances.clear()
            app_module._degraded_instances.update(old)


# ---------------------------------------------------------------------------
# Custom-name slot reservation (reviewer's reproduced blocker)
# ---------------------------------------------------------------------------

class CustomNameLeaseTests(unittest.TestCase):
    BASES = {"codex": {"label": "Codex", "color": "#333333"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed(self.BASES)

    def _leases(self) -> dict:
        p = Path(self.tmp.name) / "leases.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    def _spawn_live_lease(self):
        child = _spawn_child()
        self.addCleanup(_reap, child)
        marker = process_start_marker(child.pid)
        if not marker:
            self.skipTest("no process start marker on this platform")
        return child, marker

    def test_custom_name_slot1_reserved_and_recovered(self):
        # Reviewer's reproduction: lease-a registers codex/slot1, gets renamed
        # to the custom name "planner"; after a server restart, slot 1 must
        # stay reserved and recovery must not create a duplicate (base, slot).
        child, marker = self._spawn_live_lease()
        first = self.registry.register("codex", lease_id="lease-a",
                                       pid=child.pid, start_marker=marker)
        claimed = self.registry.claim("codex", "planner")
        self.assertEqual(claimed["name"], "planner")
        self.assertEqual(claimed["slot"], 1)
        # The lease record is updated synchronously (not via next heartbeat)
        # and carries an explicit slot.
        lease = self._leases()["lease-a"]
        self.assertEqual(lease["name"], "planner")
        self.assertEqual(lease["slot"], 1)

        # Server restart.
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)

        # Unknown lease-b registers codex: slot 1 is reserved by the live
        # lease even though the lease's NAME is custom ("planner").
        second = restarted.register("codex", lease_id="lease-b", pid=os.getpid())
        self.assertEqual(second["slot"], 2)
        self.assertNotEqual(second["name"], "codex")

        # Original lease recovers its custom name with the ORIGINAL slot.
        resumed = restarted.register("codex", lease_id="lease-a",
                                     pid=child.pid, start_marker=marker,
                                     resume_token=first["token"])
        self.assertEqual(resumed["name"], "planner")
        self.assertEqual(resumed["slot"], 1)
        self.assertEqual(resumed["token"], first["token"])

        # Family invariant: (base, slot) unique.
        family = restarted.get_instances_for("codex")
        slots = [i["slot"] for i in family]
        self.assertEqual(len(slots), len(set(slots)))
        # And leases.json carries slot on every record.
        for record in self._leases().values():
            self.assertIn("slot", record)

    def test_base_name_slot1_recovers_numbered_when_slot2_exists(self):
        # Item 4 semantics (numbered naming): a recovering slot-1 instance
        # whose lease name is the bare base name must come back as "codex-1"
        # when another instance already holds slot 2 — never "codex + codex-2".
        child, marker = self._spawn_live_lease()
        first = self.registry.register("codex", lease_id="lease-a",
                                       pid=child.pid, start_marker=marker)
        self.assertEqual(first["name"], "codex")

        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        second = restarted.register("codex", lease_id="lease-b", pid=os.getpid())
        self.assertEqual(second["name"], "codex-2")  # slot 1 was reserved

        resumed = restarted.register("codex", lease_id="lease-a",
                                     pid=child.pid, start_marker=marker,
                                     resume_token=first["token"])
        self.assertEqual(resumed["slot"], 1)
        self.assertEqual(resumed["name"], "codex-1")
        names = sorted(i["name"] for i in restarted.get_instances_for("codex"))
        self.assertEqual(names, ["codex-1", "codex-2"])

    def test_rename_persists_lease_and_recovers_new_name_and_slot(self):
        child, marker = self._spawn_live_lease()
        first = self.registry.register("codex", lease_id="lease-a",
                                       pid=child.pid, start_marker=marker)
        renamed = self.registry.rename("codex", "codex-3")
        self.assertEqual(renamed["name"], "codex-3")
        self.assertEqual(renamed["slot"], 3)
        lease = self._leases()["lease-a"]
        self.assertEqual(lease["name"], "codex-3")
        self.assertEqual(lease["slot"], 3)

        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        resumed = restarted.register("codex", lease_id="lease-a",
                                     pid=child.pid, start_marker=marker,
                                     resume_token=first["token"])
        self.assertEqual(resumed["name"], "codex-3")
        self.assertEqual(resumed["slot"], 3)
        self.assertEqual(resumed["token"], first["token"])

    def test_set_label_persists_lease_label_across_restart(self):
        # Reviewer's gap: set_label() changed only Instance.label — the lease
        # record kept the old label, so a restart recovered the stale one.
        child, marker = self._spawn_live_lease()
        first = self.registry.register("codex", lease_id="lease-a",
                                       pid=child.pid, start_marker=marker)
        self.assertTrue(self.registry.set_label("codex", "Planner"))
        # Lease record is updated synchronously, not via next heartbeat.
        self.assertEqual(self._leases()["lease-a"]["label"], "Planner")

        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        resumed = restarted.register("codex", lease_id="lease-a",
                                     pid=child.pid, start_marker=marker,
                                     resume_token=first["token"])
        self.assertEqual(resumed["label"], "Planner")

    def test_dead_lease_cleanup_persisted_by_leaseless_registration(self):
        # A confirmed-dead lease must not linger on disk even when the
        # triggering registration carries NO lease_id (pure MCP client).
        dead = _dead_pid()
        self.registry.register("codex", lease_id="lease-a", pid=dead)
        self.assertIn("lease-a", self._leases())

        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        fresh = restarted.register("codex")  # no lease_id at all
        self.assertEqual(fresh["name"], "codex")
        self.assertNotIn("lease-a", self._leases())  # cleanup hit the disk


# ---------------------------------------------------------------------------
# Persistence race hardening (stale snapshot must never write last)
# ---------------------------------------------------------------------------

class LeasePersistenceRaceTests(unittest.TestCase):
    """_save_leases()/_save_renames() must serialize the whole
    snapshot+write+replace sequence: a stalled writer must never persist an
    OLDER snapshot last (lost lease after a server restart), and concurrent
    writers must leave disk exactly equal to memory."""

    BASES = {"codex": {"label": "Codex", "color": "#333333"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed(self.BASES)

    def _leases_disk(self) -> dict:
        p = Path(self.tmp.name) / "leases.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    def test_stale_snapshot_cannot_overwrite_newer_leases(self):
        # Deterministic repro of the reviewer's race: T1 is paused mid-write
        # of leases.tmp while T2 registers lease-b and persists {a, b}. When
        # T1 resumes it must not clobber the file with its stale {a} snapshot
        # — the last write must reflect the freshest registry state.
        registry = self.registry
        real_write_text = Path.write_text
        t1_paused = threading.Event()
        t2_write_done = threading.Event()
        release_t1 = threading.Event()
        first_leases_write = []

        def gated_write_text(path_self, data, *args, **kwargs):
            if path_self.name == "leases.tmp":
                if not first_leases_write:
                    # First leases.tmp write is T1's — park it AFTER its
                    # snapshot was taken (unfixed code snapshots before write).
                    first_leases_write.append(True)
                    t1_paused.set()
                    self.assertTrue(release_t1.wait(5))
                else:
                    t2_write_done.set()
            return real_write_text(path_self, data, *args, **kwargs)

        with mock.patch.object(Path, "write_text", gated_write_text):
            t1 = threading.Thread(
                target=lambda: registry.register("codex", lease_id="lease-a",
                                                 pid=os.getpid()))
            t1.start()
            self.assertTrue(t1_paused.wait(5))  # T1 parked mid-write with {a}
            t2 = threading.Thread(
                target=lambda: registry.register("codex", lease_id="lease-b",
                                                 pid=os.getpid()))
            t2.start()
            # Unfixed code: T2 fully persists {a, b} while T1 is parked.
            # Fixed code: T2 blocks on the persist lock until T1 is released.
            t2_write_done.wait(1)
            release_t1.set()
            t1.join(5)
            t2.join(5)
        self.assertFalse(t1.is_alive() or t2.is_alive())
        # Disk must equal in-memory state — lease-b must not be lost.
        self.assertEqual(self._leases_disk(), registry._leases)
        self.assertIn("lease-b", self._leases_disk())

    def test_concurrent_register_rename_deregister_disk_equals_memory(self):
        registry = self.registry
        errors = []
        tokens = []
        token_lock = threading.Lock()

        def worker(i: int):
            try:
                r = registry.register("codex", lease_id=f"lease-{i}",
                                      pid=os.getpid())
                if "token" in r:
                    with token_lock:
                        tokens.append(r["token"])
                if i % 2 == 0:
                    registry.rename(r["name"], f"worker-{i}")
                if i % 3 == 0:
                    registry.deregister(r["name"])
            except Exception as exc:  # recorded, asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        # Disk is always parseable JSON and exactly matches memory.
        disk_text = (Path(self.tmp.name) / "leases.json").read_text("utf-8")
        self.assertEqual(json.loads(disk_text), registry._leases)
        # Only token digests are persisted — no plaintext token anywhere.
        for tok in tokens:
            self.assertNotIn(tok, disk_text)

    def test_concurrent_renames_disk_equals_memory(self):
        registry = self.registry
        names = []
        for i in range(6):
            r = registry.register("codex", lease_id=f"lease-{i}",
                                  pid=os.getpid())
            names.append(r["name"])

        errors = []

        def worker(i: int, name: str):
            try:
                for round_no in range(4):
                    renamed = registry.rename(name, f"custom-{i}-{round_no}")
                    if isinstance(renamed, str):
                        errors.append(renamed)
                        return
                    back = registry.rename(f"custom-{i}-{round_no}", name)
                    if isinstance(back, str):
                        errors.append(back)
                        return
            except Exception as exc:  # recorded, asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i, n))
                   for i, n in enumerate(names)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        p = Path(self.tmp.name) / "renames.json"
        disk = json.loads(p.read_text("utf-8")) if p.exists() else {}
        self.assertEqual(disk, registry._renames)


# ---------------------------------------------------------------------------
# Rename-back cycle invariant (reviewer round 8)
# ---------------------------------------------------------------------------

class RenameBackInvariantTests(unittest.TestCase):
    """Single-instance rename-back (base-1 -> base) must not leave a 2-cycle
    {base -> base-1, base-1 -> base}. Hard invariants at all times:
    _renames is acyclic (memory AND disk), and the active canonical name is
    never a KEY pointing at an old name."""

    BASES = {"codex": {"label": "Codex", "color": "#333333"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed(self.BASES)

    def _renames_disk(self) -> dict:
        p = Path(self.tmp.name) / "renames.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    @staticmethod
    def _assert_acyclic(renames: dict):
        for src in renames:
            seen, cur = {src}, renames.get(src)
            while cur in renames:
                assert cur not in seen, f"cycle through {cur}"
                seen.add(cur)
                cur = renames.get(cur)

    def _two_instances_then_b_leaves(self):
        a = self.registry.register("codex", lease_id="lease-a", pid=os.getpid())
        b = self.registry.register("codex", lease_id="lease-b", pid=os.getpid())
        self.assertEqual(sorted(self.registry.get_all_names()),
                         ["codex-1", "codex-2"])
        result = self.registry.deregister("codex-2")
        return a, b, result

    def test_rename_back_leaves_no_cycle_in_memory_or_on_disk(self):
        self._two_instances_then_b_leaves()
        # Backward-compat edge kept: old numbered name resolves to the base.
        self.assertEqual(self.registry.resolve_name("codex-1"), "codex")
        # Active canonical name self-resolves — it is not a KEY to an old name.
        self.assertEqual(self.registry.resolve_name("codex"), "codex")
        self.assertEqual(self.registry.get_all_names(), ["codex"])
        mem = dict(self.registry._renames)
        self.assertEqual(mem, {"codex-1": "codex"})
        self._assert_acyclic(mem)
        disk = self._renames_disk()
        self.assertEqual(disk, mem)  # disk == memory
        self._assert_acyclic(disk)

    def test_restart_recovers_base_slot1_and_no_stale_forward_edge(self):
        a, _b, _r = self._two_instances_then_b_leaves()
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        resumed = restarted.register("codex", lease_id="lease-a",
                                     pid=os.getpid(),
                                     resume_token=a["token"])
        self.assertEqual(resumed["name"], "codex")
        self.assertEqual(resumed["slot"], 1)
        self.assertEqual(resumed["token"], a["token"])
        # Heartbeat-side lease rebind still works on the active name.
        self.assertTrue(restarted.update_lease("codex", "lease-a",
                                               pid=os.getpid()))
        # No stale forward edge from the active name.
        self.assertNotIn("codex", restarted._renames)
        self.assertEqual(restarted.resolve_name("codex"), "codex")
        self.assertEqual(restarted.resolve_name("codex-1"), "codex")
        self._assert_acyclic(dict(restarted._renames))

    def test_rename_back_emitted_exactly_once_and_deregister_idempotent(self):
        _a, _b, result = self._two_instances_then_b_leaves()
        # The rename-back (which drives the single legitimate leave/rename
        # handling server-side) is emitted exactly once.
        self.assertEqual(result.get("_renamed_back"),
                         {"old": "codex-1", "new": "codex"})
        again = self.registry.deregister("codex-2")
        self.assertIsNone(again)  # no duplicate effects on re-deregister
        self.assertEqual(dict(self.registry._renames), {"codex-1": "codex"})

    def test_seed_breaks_legacy_numbered_cycle_keeping_backward_edge(self):
        # Legacy/manual 2-cycle on disk (pre-fix rename-back state). Seed must
        # break it WITHOUT leaving a stale edge from the (active) base name.
        (Path(self.tmp.name) / "renames.json").write_text(
            json.dumps({"codex": "codex-1", "codex-1": "codex"}), "utf-8")
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        self.assertEqual(dict(restarted._renames), {"codex-1": "codex"})
        self.assertNotIn("codex", restarted._renames)
        self.assertEqual(restarted.resolve_name("codex"), "codex")
        self.assertEqual(restarted.resolve_name("codex-1"), "codex")
        # And the cleaned state reached the disk.
        disk = self._renames_disk()
        self._assert_acyclic(disk)
        self.assertNotIn("codex", disk)

    def test_claim_back_to_previous_name_stays_acyclic(self):
        # claim A->B then B->A must not create a 2-cycle either.
        self.registry.register("codex", lease_id="lease-a", pid=os.getpid())
        self.registry.claim("codex", "planner")
        self.assertEqual(dict(self.registry._renames), {"codex": "planner"})
        self.registry.claim("planner", "codex")  # rename back to base
        mem = dict(self.registry._renames)
        self._assert_acyclic(mem)
        # "codex" is active again — never a KEY; planner redirects forward.
        self.assertNotIn("codex", mem)
        self.assertEqual(mem.get("planner"), "codex")
        self.assertEqual(self.registry.resolve_name("planner"), "codex")
        self.assertEqual(self.registry.resolve_name("codex"), "codex")
        self.assertEqual(self._renames_disk(), mem)


# ---------------------------------------------------------------------------
# Seed-time 2-cycle break with lease-name evidence (reviewer round 9)
# ---------------------------------------------------------------------------

class SeedCycleLeaseEvidenceTests(unittest.TestCase):
    """Legacy base<->custom 2-cycles on disk must be broken using the
    already-loaded lease records' canonical names as evidence:
    the ACTIVE canonical name never redirects to an old name."""

    BASES = {"codex": {"label": "Codex", "color": "#333333"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write_legacy(self, lease_name: str, lease_slot: int = 1,
                      extra_lease: dict | None = None):
        from registry import _token_digest
        leases = {
            "lease-a": {"base": "codex", "name": lease_name,
                        "label": "Codex", "token_digest": _token_digest("tok-a"),
                        "pid": os.getpid(), "start_marker": "",
                        "slot": lease_slot},
        }
        if extra_lease:
            leases.update(extra_lease)
        (Path(self.tmp.name) / "leases.json").write_text(
            json.dumps(leases), "utf-8")
        (Path(self.tmp.name) / "renames.json").write_text(
            json.dumps({"codex": "planner", "planner": "codex"}), "utf-8")

    def _renames_disk(self) -> dict:
        p = Path(self.tmp.name) / "renames.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    @staticmethod
    def _assert_acyclic(renames: dict):
        for src in renames:
            seen, cur = {src}, renames.get(src)
            while cur in renames:
                assert cur not in seen, f"cycle through {cur}"
                seen.add(cur)
                cur = renames.get(cur)

    def test_cycle_with_lease_canonical_base_keeps_custom_to_base(self):
        # Lease says the live name is the BASE — the stale base->custom edge
        # must go, custom->base stays.
        self._write_legacy(lease_name="codex")
        reg = RuntimeRegistry(self.tmp.name)
        reg.seed(self.BASES)
        self.assertEqual(dict(reg._renames), {"planner": "codex"})
        self.assertEqual(reg.resolve_name("codex"), "codex")  # self-resolves
        self.assertEqual(reg.resolve_name("planner"), "codex")

        resumed = reg.register("codex", lease_id="lease-a", pid=os.getpid(),
                               resume_token="tok-a")
        self.assertEqual(resumed["name"], "codex")
        self.assertEqual(resumed["slot"], 1)
        self.assertEqual(resumed["token"], "tok-a")
        self.assertTrue(reg.update_lease("codex", "lease-a", pid=os.getpid()))
        self._assert_acyclic(dict(reg._renames))
        self.assertEqual(self._renames_disk(), dict(reg._renames))  # disk == memory

    def test_cycle_with_lease_canonical_custom_keeps_base_to_custom(self):
        # Lease says the live name is the CUSTOM name — keep base->custom.
        self._write_legacy(lease_name="planner")
        reg = RuntimeRegistry(self.tmp.name)
        reg.seed(self.BASES)
        self.assertEqual(dict(reg._renames), {"codex": "planner"})
        self.assertEqual(reg.resolve_name("planner"), "planner")  # self-resolves

        resumed = reg.register("codex", lease_id="lease-a", pid=os.getpid(),
                               resume_token="tok-a")
        self.assertEqual(resumed["name"], "planner")
        self.assertEqual(resumed["slot"], 1)
        self.assertEqual(resumed["token"], "tok-a")
        self.assertTrue(reg.update_lease("planner", "lease-a", pid=os.getpid()))
        self._assert_acyclic(dict(reg._renames))
        self.assertEqual(self._renames_disk(), dict(reg._renames))

    def test_cycle_with_both_names_canonical_drops_both_edges(self):
        from registry import _token_digest
        self._write_legacy(
            lease_name="codex",
            extra_lease={"lease-b": {"base": "codex", "name": "planner",
                                     "label": "Planner",
                                     "token_digest": _token_digest("tok-b"),
                                     "pid": os.getpid(), "start_marker": "",
                                     "slot": 2}})
        reg = RuntimeRegistry(self.tmp.name)
        reg.seed(self.BASES)
        # Neither active name may redirect to the other.
        self.assertEqual(dict(reg._renames), {})
        self.assertEqual(reg.resolve_name("codex"), "codex")
        self.assertEqual(reg.resolve_name("planner"), "planner")
        self.assertEqual(self._renames_disk(), {})

    def test_cycle_without_any_evidence_falls_back_to_base_to_custom(self):
        # No leases at all -> existing heuristic unchanged: base -> custom.
        (Path(self.tmp.name) / "renames.json").write_text(
            json.dumps({"codex": "planner", "planner": "codex"}), "utf-8")
        reg = RuntimeRegistry(self.tmp.name)
        reg.seed(self.BASES)
        self.assertEqual(dict(reg._renames), {"codex": "planner"})
        self.assertEqual(self._renames_disk(), {"codex": "planner"})


# ---------------------------------------------------------------------------
# Lease conflict hardening (restart race + cross-base theft + atomicity)
# ---------------------------------------------------------------------------

class LeaseConflictHardeningTests(unittest.TestCase):
    BASES = {
        "opencode": {"label": "OpenCode", "color": "#222222"},
        "kimi": {"label": "Kimi", "color": "#111111"},
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed(self.BASES)

    def _leases_file(self) -> Path:
        return Path(self.tmp.name) / "leases.json"

    def test_cross_base_same_lease_rejected_with_zero_mutations(self):
        first = self.registry.register("opencode", lease_id="lease-a", pid=1234)
        before = self._leases_file().read_bytes()
        # Server restart: new in-memory registry over the same data dir.
        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)
        # Same lease_id but a DIFFERENT base — a known lease is never reused
        # cross-base, regardless of the presented token.
        rejected = restarted.register("kimi", lease_id="lease-a", pid=1234,
                                      resume_token=first["token"])
        self.assertEqual(rejected.get("error"), "invalid_lease_proof")
        self.assertEqual(restarted.get_all_names(), [])       # no instance created
        self.assertEqual(self._leases_file().read_bytes(), before)  # lease untouched

    def test_live_persisted_lease_blocks_unknown_lease_after_restart(self):
        child = _spawn_child()
        self.addCleanup(_reap, child)
        marker = process_start_marker(child.pid)
        if not marker:
            self.skipTest("no process start marker on this platform")
        first = self.registry.register("opencode", lease_id="lease-a",
                                       pid=child.pid, start_marker=marker)

        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)

        # Unknown lease explicitly asking for the live-lease-held name → 409.
        rejected = restarted.register("opencode", preferred_name="opencode",
                                      lease_id="lease-b", pid=999)
        self.assertEqual(rejected.get("error"), "name_reserved_by_lease")
        self.assertEqual(restarted.get_all_names(), [])

        # Unknown lease without a preference takes a DIFFERENT slot; the
        # original lease record stays intact.
        other = restarted.register("opencode", lease_id="lease-b", pid=999)
        self.assertNotEqual(other.get("error"), "name_reserved_by_lease")
        self.assertNotEqual(other["name"], "opencode")
        leases = json.loads(self._leases_file().read_text("utf-8"))
        self.assertIn("lease-a", leases)
        self.assertEqual(leases["lease-a"]["name"], "opencode")

        # The rightful wrapper can still resume its original identity (same
        # token). With slot 2 already taken it comes back NUMBERED — a bare
        # base name alongside "opencode-2" would break the family invariant.
        resumed = restarted.register("opencode", lease_id="lease-a",
                                     pid=child.pid, start_marker=marker,
                                     resume_token=first["token"])
        self.assertEqual(resumed["name"], "opencode-1")
        self.assertEqual(resumed["slot"], 1)
        self.assertEqual(resumed["token"], first["token"])
        names = sorted(i["name"] for i in restarted.get_instances_for("opencode"))
        self.assertEqual(names, ["opencode-1", "opencode-2"])

    def test_dead_persisted_lease_is_cleaned_and_name_reusable(self):
        dead = _dead_pid()
        first = self.registry.register("opencode", lease_id="lease-a", pid=dead)

        restarted = RuntimeRegistry(self.tmp.name)
        restarted.seed(self.BASES)

        # The stale lease's process is gone → atomically removed, name reused.
        fresh = restarted.register("opencode", preferred_name="opencode",
                                   lease_id="lease-b", pid=4321)
        self.assertEqual(fresh.get("error"), None)
        self.assertEqual(fresh["name"], "opencode")
        leases = json.loads(self._leases_file().read_text("utf-8"))
        self.assertNotIn("lease-a", leases)  # stale lease gone
        self.assertIn("lease-b", leases)
        # Old token no longer resolves to anything.
        self.assertIsNone(restarted.resolve_token(first["token"]))

    def test_concurrent_registrations_same_lease_yield_one_identity(self):
        first = self.registry.register("opencode", lease_id="lease-a", pid=os.getpid())
        barrier = threading.Barrier(6)
        results = []

        def worker():
            barrier.wait()
            results.append(self.registry.register(
                "opencode", lease_id="lease-a", pid=os.getpid(),
                resume_token=first["token"]))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 6)
        self.assertTrue(all(r.get("name") == first["name"] for r in results))
        self.assertTrue(all(r.get("token") == first["token"] for r in results))
        self.assertEqual(self.registry.get_all_names(), [first["name"]])

    def test_concurrent_fresh_registrations_get_distinct_slots(self):
        barrier = threading.Barrier(8)
        results = []

        def worker(i):
            barrier.wait()
            results.append(self.registry.register(
                "opencode", lease_id=f"lease-{i}", pid=os.getpid()))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 8)
        names = [r["name"] for r in results]
        self.assertEqual(len(set(names)), 8, f"duplicate names under race: {names}")
        self.assertEqual(len(self.registry.get_all_names()), 8)


# ---------------------------------------------------------------------------
# Caller-level broadcast aggregation (one broadcast_status per sweep)
# ---------------------------------------------------------------------------

class PresenceIterationBroadcastTests(unittest.TestCase):
    """Drive _presence_iteration (the _background_checks body) and assert
    broadcast_status is requested EXACTLY ONCE per state transition."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed({
            "opencode": {"label": "OpenCode", "color": "#222222"},
            "kimi": {"label": "Kimi", "color": "#111111"},
        })
        self.degraded: set[str] = set()
        self.known_online: set[str] = set()
        self.known_active: set[str] = set()
        self.posted_leave: set[str] = set()
        self.store_messages = []
        self.store = SimpleNamespace(
            add=lambda *a, **k: self.store_messages.append((a, k)),
            rename_sender=lambda old, new: None,
        )

    def tearDown(self):
        mcp_bridge.purge_identity("opencode")
        mcp_bridge.purge_identity("kimi")

    def _set_presence(self, name: str, ts: float):
        with mcp_bridge._presence_lock:
            mcp_bridge._presence[name] = ts

    def _iter(self, now: float) -> dict:
        return app_module._presence_iteration(
            now=now,
            registry=self.registry,
            store=self.store,
            mcp_bridge=mcp_bridge,
            pid_alive_fn=pid_is_alive,
            degraded=self.degraded,
            known_online=self.known_online,
            known_active=self.known_active,
            posted_leave=self.posted_leave,
            last_channel="general",
        )

    def test_stale_steady_recovery_broadcast_counts(self):
        self.registry.register("opencode", lease_id="lease-a", pid=os.getpid())
        t0 = 1000.0
        self._set_presence("opencode", t0)

        # Join: online set changes → exactly one broadcast.
        self.assertTrue(self._iter(t0 + 5)["broadcast_status"])
        # Steady online → none.
        self.assertFalse(self._iter(t0 + 8)["broadcast_status"])

        # Presence expires → exactly one broadcast (degraded transition).
        self.assertTrue(self._iter(t0 + 11)["broadcast_status"])
        self.assertIn("opencode", self.degraded)
        # Sustained stale (10–90s window) → ZERO broadcasts, no flapping.
        for tick in (14, 30, 60, 89):
            self.assertFalse(self._iter(t0 + tick)["broadcast_status"],
                             f"extra broadcast at +{tick}s")

        # Heartbeat recovery → exactly one broadcast.
        self._set_presence("opencode", t0 + 92)
        self.assertTrue(self._iter(t0 + 92)["broadcast_status"])
        self.assertNotIn("opencode", self.degraded)
        self.assertFalse(self._iter(t0 + 95)["broadcast_status"])

    def test_activity_only_change_broadcasts_once(self):
        self.registry.register("opencode", lease_id="lease-a", pid=os.getpid())
        t0 = 1000.0
        self._set_presence("opencode", t0)
        self._iter(t0 + 5)  # settle online state
        mcp_bridge.set_active("opencode", True)
        # set_active uses real time — align `now` with it.
        now = t0 + 2000
        self._set_presence("opencode", now)
        with mcp_bridge._presence_lock:
            mcp_bridge._activity_ts["opencode"] = now - 1
        self.assertTrue(self._iter(now)["broadcast_status"])
        # Same activity state next tick → no broadcast.
        self._set_presence("opencode", now + 3)
        self.assertFalse(self._iter(now + 3)["broadcast_status"])

    def test_death_deregisters_with_one_broadcast_and_one_leave(self):
        dead = _dead_pid()
        self.registry.register("kimi", lease_id="lease-k", pid=dead)
        t0 = 1000.0
        self._set_presence("kimi", t0)
        self._iter(t0 + 5)   # settle online
        self._iter(t0 + 11)  # degraded transition (one broadcast)
        self.store_messages.clear()

        result = self._iter(t0 + 91)
        self.assertTrue(result["broadcast_status"])  # exactly one broadcast
        self.assertIsNone(self.registry.get_instance("kimi"))
        leaves = [m for m in self.store_messages
                  if m[1].get("msg_type") == "leave"]
        self.assertEqual(len(leaves), 1)
        self.assertIn("disconnected (timeout)", leaves[0][0][1])
        # Subsequent sweeps: nothing more.
        self.assertFalse(self._iter(t0 + 94)["broadcast_status"])


# ---------------------------------------------------------------------------
# Converged presence/crash sweep — integration-level transition test
# ---------------------------------------------------------------------------

class PresenceSweepIntegrationTests(unittest.TestCase):
    """Drive multiple consecutive sweeps and assert each state transition
    (degrade / recover / deregister) is emitted EXACTLY ONCE — no flapping
    in the 10–90s window between presence expiry and crash timeout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.registry.seed({
            "opencode": {"label": "OpenCode", "color": "#222222"},
            "kimi": {"label": "Kimi", "color": "#111111"},
        })
        self.degraded: set[str] = set()
        self.presence: dict[str, float] = {}

    def _sweep(self, now: float):
        events = app_module._presence_sweep(
            self.registry.get_all_names(),
            now=now,
            currently_online={n for n, ts in self.presence.items() if now - ts < 10},
            presence=self.presence,
            registry=self.registry,
            pid_alive_fn=pid_is_alive,
            degraded=self.degraded,
            crash_timeout=90,
        )
        # Mimic the caller: deregistered names leave the registry.
        for kind, name in events:
            if kind == "deregister":
                self.registry.deregister(name)
        return events

    def test_degrade_then_steady_then_recover_each_exactly_once(self):
        self.registry.register("opencode", lease_id="lease-a", pid=os.getpid())
        t0 = 1000.0
        self.presence["opencode"] = t0

        self.assertEqual(self._sweep(t0), [])  # online, nothing happens

        # Presence expires → ONE degrade transition.
        self.assertEqual(self._sweep(t0 + 11), [("degraded", "opencode")])
        self.assertIn("opencode", self.degraded)

        # 10–90s window: subsequent sweeps are silent (no re-log/re-broadcast).
        for tick in (14, 30, 60, 89):
            self.assertEqual(self._sweep(t0 + tick), [], f"flap at +{tick}s")
            self.assertIn("opencode", self.degraded)

        # Heartbeat recovery → ONE recover transition, mark cleared.
        self.presence["opencode"] = t0 + 92
        self.assertEqual(self._sweep(t0 + 92), [("recovered", "opencode")])
        self.assertNotIn("opencode", self.degraded)
        self.assertEqual(self._sweep(t0 + 95), [])

    def test_process_death_deregisters_exactly_once(self):
        dead = _dead_pid()
        self.registry.register("kimi", lease_id="lease-k", pid=dead)
        t0 = 1000.0
        self.presence["kimi"] = t0

        # Presence expired but within the crash window → degraded, retained.
        self.assertEqual(self._sweep(t0 + 11), [("degraded", "kimi")])

        # Beyond the crash timeout with a dead process → exactly one deregister.
        self.assertEqual(self._sweep(t0 + 91), [("deregister", "kimi")])
        self.assertIsNone(self.registry.get_instance("kimi"))

        # No further events for the gone instance.
        self.assertEqual(self._sweep(t0 + 94), [])
        self.assertEqual(self._sweep(t0 + 200), [])

    def test_mcp_only_client_is_never_degraded_by_sweep(self):
        # No lease/pid → the sweep leaves it alone (leave messages are the
        # debounced offline pass's job, preserving old MCP-client behavior).
        self.registry.register("kimi")  # no lease, pid 0
        t0 = 1000.0
        self.presence["kimi"] = t0
        self.assertEqual(self._sweep(t0 + 11), [])
        self.assertEqual(self.degraded, set())
        # ...but past the crash timeout with no pid, death cannot be ruled out
        # → deregister (legacy behavior for pid-less instances).
        self.assertEqual(self._sweep(t0 + 91), [("deregister", "kimi")])


# ---------------------------------------------------------------------------
# Degraded status rendering (static checks on the frontend assets)
# ---------------------------------------------------------------------------

class UiDegradedRenderingTests(unittest.TestCase):
    """The UI must render presence_stale as a distinct degraded state, not as
    offline. Robust source-level checks (no brittle snapshotting)."""

    @classmethod
    def setUpClass(cls):
        cls.chat_js = (ROOT / "static" / "chat.js").read_text("utf-8")
        cls.i18n_js = (ROOT / "static" / "i18n.js").read_text("utf-8")
        cls.style_css = (ROOT / "static" / "style.css").read_text("utf-8")

    def test_update_status_handles_presence_stale_as_degraded(self):
        m = re.search(r"function updateStatus\(.*?\n\}", self.chat_js, re.S)
        self.assertIsNotNone(m, "updateStatus not found in chat.js")
        body = m.group(0)
        self.assertIn("presence_stale", body)
        self.assertIn("'degraded'", body)
        # Degraded branch must come BEFORE the offline fallback assignment.
        self.assertLess(body.index("info.presence_stale"),
                        body.index("pill.classList.add('offline')"))

    def test_degraded_i18n_strings_exist_in_both_locales(self):
        self.assertIn("'status.degraded'", self.i18n_js)
        self.assertIn("连接异常（进程仍运行）", self.i18n_js)
        self.assertRegex(self.i18n_js, r"'status\.degraded':\s*'[^']*[Cc]onnection[^']*'")

    def test_degraded_css_class_exists(self):
        self.assertIn(".status-pill.degraded", self.style_css)


if __name__ == "__main__":
    unittest.main()
