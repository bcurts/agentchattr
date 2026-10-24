"""F3: a relaunched wrapper takes over its own stale identity instead of churning names.

The wrapper keeps its instance token on disk. On startup it presents that token; the
server hands back the same identity (with a rotated token) if the old wrapper is dead,
and refuses if the old wrapper is still heartbeating.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import wrapper
from registry import RuntimeRegistry

DEAD = lambda name: False   # no recent heartbeat
LIVE = lambda name: True


class RegistryTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = RuntimeRegistry(data_dir=self.tmp)
        self.reg.seed({"claude": {"label": "Claude", "color": "#ff6a00"},
                       "codex": {"label": "Codex", "color": "#00B67D"}})

    def test_stale_live_entry_is_taken_over_with_same_name_and_rotated_token(self):
        old = self.reg.register("claude")
        result = self.reg.takeover("claude", [old["token"]], is_live=DEAD)
        self.assertEqual(result["name"], "claude")
        self.assertEqual(result["identity_id"], old["identity_id"])
        self.assertNotEqual(result["token"], old["token"])
        self.assertIsNone(self.reg.resolve_token(old["token"]), "old token must die")
        self.assertEqual(self.reg.resolve_token(result["token"])["name"], "claude")
        self.assertEqual(self.reg.get_all_names(), ["claude"])

    def test_genuinely_live_wrapper_is_not_stolen(self):
        old = self.reg.register("claude")
        result = self.reg.takeover("claude", [old["token"]], is_live=LIVE)
        self.assertEqual(result, {"busy": "claude"})
        self.assertEqual(self.reg.resolve_token(old["token"])["name"], "claude",
                         "live wrapper keeps its token")

    def test_reclaimable_identity_is_taken_over_inside_grace_period(self):
        old = self.reg.register("claude")
        self.reg.deregister("claude", reclaimable=True)  # console-close path
        result = self.reg.takeover("claude", [old["token"]], is_live=DEAD)
        self.assertEqual(result["name"], "claude", "must not churn to claude-2")
        self.assertEqual(result["state"], "active")
        self.assertIsNone(self.reg.resolve_token(old["token"]))

    def test_deliberately_deregistered_token_cannot_take_over(self):
        old = self.reg.register("claude")
        self.reg.deregister("claude")
        result = self.reg.takeover("claude", [old["token"]], is_live=DEAD)
        self.assertEqual(result, {"dead_tokens": [old["token"]]})

    def test_token_of_another_family_is_refused(self):
        old = self.reg.register("codex")
        result = self.reg.takeover("claude", [old["token"]], is_live=DEAD)
        self.assertEqual(result, {"dead_tokens": [old["token"]]})
        self.assertEqual(self.reg.resolve_token(old["token"])["name"], "codex")

    def test_reclaimable_superseded_by_fresh_slot_is_not_revived(self):
        old = self.reg.register("claude")
        self.reg.deregister("claude", reclaimable=True)
        self.reg._reserved.clear()
        self.reg.register("claude")  # fresh launch took slot 1
        result = self.reg.takeover("claude", [old["token"]], is_live=DEAD)
        self.assertEqual(result, {"dead_tokens": [old["token"]]})

    def test_two_simultaneous_takeovers_only_one_wins(self):
        old = self.reg.register("claude")
        barrier = threading.Barrier(2)
        results = []

        def relaunch():
            barrier.wait()
            results.append(self.reg.takeover("claude", [old["token"]], is_live=DEAD))

        threads = [threading.Thread(target=relaunch) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results if "token" in r]
        self.assertEqual(len(winners), 1, results)
        self.assertIn({"dead_tokens": [old["token"]]}, results)

    def test_rotated_token_survives_server_restart(self):
        old = self.reg.register("claude")
        new = self.reg.takeover("claude", [old["token"]], is_live=DEAD)
        reloaded = RuntimeRegistry(data_dir=self.tmp)
        reloaded.seed({"claude": {"label": "Claude", "color": "#ff6a00"}})
        self.assertIsNone(reloaded.resolve_token(old["token"]))
        self.assertEqual(reloaded.resolve_token(new["token"])["name"], "claude")


class _Req:
    def __init__(self, body, token=""):
        self.json = AsyncMock(return_value=body)
        self.headers = {"authorization": f"Bearer {token}"} if token else {}
        self.client = Mock(host="127.0.0.1")
        self.query_params = {}


class _EndpointHarness(unittest.TestCase):
    def setUp(self):
        import app
        import mcp_bridge
        self.app, self.bridge = app, mcp_bridge
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = RuntimeRegistry(data_dir=self.tmp)
        self.reg.seed({"claude": {"label": "Claude", "color": "#ff6a00"}})
        for target, name, value in [(app, "registry", self.reg), (mcp_bridge, "registry", self.reg),
                                    (app, "_event_loop", None), (app, "store", Mock())]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        presence = patch.dict(mcp_bridge._presence, clear=True)
        presence.start()
        self.addCleanup(presence.stop)

    def _register(self, body):
        resp = asyncio.run(self.app.register_agent(_Req(body)))
        return resp.status_code, json.loads(resp.body)


class EndpointTakeoverTests(_EndpointHarness):
    def test_register_with_stale_token_returns_same_identity(self):
        _, old = self._register({"base": "claude"})
        self.bridge._presence["claude"] = 0  # wrapper stopped heartbeating
        code, new = self._register({"base": "claude",
                                    "reclaim": [{"token": old["token"], "name": "claude"}]})
        self.assertEqual(code, 200)
        self.assertEqual(new["name"], "claude")
        self.assertTrue(new["reclaimed"])
        self.assertTrue(self.bridge.is_online("claude"), "takeover refreshes presence")

    def test_register_against_live_owner_is_409_and_allocates_nothing(self):
        _, old = self._register({"base": "claude"})
        code, body = self._register({"base": "claude", "reclaim": [{"token": old["token"]}]})
        self.assertEqual(code, 409)
        self.assertEqual(body["error"], "identity_in_use")
        self.assertEqual(self.reg.get_all_names(), ["claude"])

    def test_dead_tokens_fall_back_to_fresh_registration(self):
        code, new = self._register({"base": "claude", "reclaim": [{"token": "f" * 32}]})
        self.assertEqual(code, 200)
        self.assertEqual(new["name"], "claude")
        self.assertEqual(new["dead_tokens"], ["f" * 32])
        self.assertNotIn("reclaimed", new)

    def test_takeover_reports_queue_to_migrate_when_name_changed(self):
        _, old = self._register({"base": "claude"})
        self.bridge._presence["claude"] = 0
        code, new = self._register({"base": "claude",
                                    "reclaim": [{"token": old["token"], "name": "claude-2"}]})
        self.assertEqual(new["migrate_queue_from"], "claude-2")

    def test_reclaimable_deregister_keeps_token_for_relaunch(self):
        _, old = self._register({"base": "claude"})
        req = _Req({"reclaimable": True}, token=old["token"])
        resp = asyncio.run(self.app.deregister_agent("claude", req))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.reg.get_all_names(), [])
        code, new = self._register({"base": "claude", "reclaim": [{"token": old["token"]}]})
        self.assertEqual((code, new["name"]), (200, "claude"))

    def test_plain_deregister_is_still_deliberate(self):
        _, old = self._register({"base": "claude"})
        req = _Req({}, token=old["token"])
        asyncio.run(self.app.deregister_agent("claude", req))
        self.assertIsNone(self.reg.resolve_token(old["token"]))


class TokenFileTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)

    def _entry(self, **kw):
        base = {"base": "claude", "name": "claude", "identity_id": "id1", "token": "t1"}
        base.update(kw)
        return base

    def test_saved_token_round_trips_and_records_owner_pid(self):
        path = wrapper._save_token_file(self.data, self._entry())
        [loaded] = wrapper._load_token_files(self.data, "claude")
        self.assertEqual(loaded["token"], "t1")
        self.assertEqual(loaded["pid"], os.getpid())
        self.assertEqual(loaded["path"], path)

    def test_token_file_is_readable_only_by_current_user(self):
        path = wrapper._save_token_file(self.data, self._entry())
        if os.name == "nt":
            out = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
            aces = [l.strip() for l in out.splitlines()[0:] if ":(" in l]
            self.assertEqual(len(aces), 1, out)
            self.assertIn(os.environ["USERNAME"].lower(), aces[0].lower())
            self.assertNotIn("(I)", aces[0], "inherited ACEs must be removed")
        else:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_files_of_live_wrappers_are_skipped(self):
        wrapper._save_token_file(self.data, self._entry())  # owned by this live process
        self.assertEqual(wrapper._reclaim_candidates(self.data, "claude"), [])

    def test_files_of_dead_wrappers_are_candidates_newest_first(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        a = wrapper._save_token_file(self.data, self._entry(identity_id="a", token="ta"), pid=dead.pid)
        b = wrapper._save_token_file(self.data, self._entry(identity_id="b", token="tb"), pid=dead.pid)
        os.utime(a, (1, 1))
        self.assertEqual([c["token"] for c in wrapper._reclaim_candidates(self.data, "claude")],
                         ["tb", "ta"])

    def test_other_family_files_are_ignored(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        wrapper._save_token_file(self.data, self._entry(base="codex"), pid=dead.pid)
        self.assertEqual(wrapper._reclaim_candidates(self.data, "claude"), [])


class QueueMigrationTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)

    def test_old_processing_then_queue_are_appended_in_order(self):
        (self.data / "claude-2_queue.processing").write_text('{"n":1}\n', "utf-8")
        (self.data / "claude-2_queue.jsonl").write_text('{"n":2}\n', "utf-8")
        (self.data / "claude_queue.jsonl").write_text('{"n":0}\n', "utf-8")
        wrapper._migrate_queue(self.data, "claude-2", "claude")
        lines = (self.data / "claude_queue.jsonl").read_text("utf-8").splitlines()
        self.assertEqual([json.loads(l)["n"] for l in lines], [0, 1, 2])
        self.assertFalse((self.data / "claude-2_queue.jsonl").exists())
        self.assertFalse((self.data / "claude-2_queue.processing").exists())

    def test_missing_trailing_newline_does_not_merge_lines(self):
        (self.data / "claude-2_queue.jsonl").write_text('{"n":1}', "utf-8")
        (self.data / "claude_queue.jsonl").write_text('{"n":0}', "utf-8")
        wrapper._migrate_queue(self.data, "claude-2", "claude")
        lines = (self.data / "claude_queue.jsonl").read_text("utf-8").splitlines()
        self.assertEqual([json.loads(l)["n"] for l in lines], [0, 1])

    def test_claim_left_by_an_interrupted_migration_is_recovered(self):
        # Crash after the claim rename, before the append: the claim names its target.
        (self.data / "claude_queue.from-claude-2_queue.jsonl.migrating").write_text(
            '{"n":1}\n', "utf-8")
        wrapper._recover_queue_claims(self.data, "claude")
        lines = (self.data / "claude_queue.jsonl").read_text("utf-8").splitlines()
        self.assertEqual([json.loads(l)["n"] for l in lines], [1])
        self.assertEqual(list(self.data.glob("*.migrating")), [])

    def test_new_migration_does_not_overwrite_an_unrecovered_claim(self):
        (self.data / "claude_queue.from-claude-2_queue.jsonl.migrating").write_text(
            '{"n":1}\n', "utf-8")
        (self.data / "claude-2_queue.jsonl").write_text('{"n":2}\n', "utf-8")
        wrapper._migrate_queue(self.data, "claude-2", "claude")
        lines = (self.data / "claude_queue.jsonl").read_text("utf-8").splitlines()
        self.assertEqual([json.loads(l)["n"] for l in lines], [1, 2])

    def test_same_name_is_a_no_op(self):
        (self.data / "claude_queue.jsonl").write_text('{"n":0}\n', "utf-8")
        wrapper._migrate_queue(self.data, "claude", "claude")
        self.assertEqual((self.data / "claude_queue.jsonl").read_text("utf-8"), '{"n":0}\n')


class AcquireIdentityTests(unittest.TestCase):
    """Startup flow: try saved tokens, wait out a lagging heartbeat, else register fresh."""

    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.dead_pid = dead.pid

    def _save(self, **kw):
        entry = {"base": "claude", "name": "claude", "identity_id": "id1", "token": "old"}
        entry.update(kw)
        return wrapper._save_token_file(self.data, entry, pid=self.dead_pid)

    def test_takeover_rewrites_token_file_for_this_process(self):
        old_path = self._save()
        register = Mock(return_value=(200, {"name": "claude", "identity_id": "id1",
                                             "token": "new", "reclaimed": True}))
        reg = wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertEqual(reg["token"], "new")
        body = register.call_args.args[0]
        self.assertEqual(body["reclaim"], [{"token": "old", "name": "claude"}])
        [saved] = wrapper._load_token_files(self.data, "claude")
        self.assertEqual((saved["token"], saved["pid"]), ("new", os.getpid()))

    def test_busy_owner_is_retried_then_abandoned(self):
        self._save()
        register = Mock(side_effect=lambda body: (
            (409, {"error": "identity_in_use", "retry_after": 2}) if "reclaim" in body
            else (200, {"name": "claude-2", "identity_id": "id2", "token": "t2"})))
        sleep = Mock()
        reg = wrapper._acquire_identity(register, self.data, "claude", sleep=sleep,
                                        max_wait=10)
        self.assertEqual(reg["name"], "claude-2")
        self.assertLessEqual(sum(c.args[0] for c in sleep.call_args_list), 10)
        self.assertNotIn("reclaim", register.call_args.args[0], "final attempt is fresh")

    def test_busy_owner_that_goes_stale_is_taken_over(self):
        self._save()
        register = Mock(side_effect=[(409, {"error": "identity_in_use", "retry_after": 2}),
                                     (200, {"name": "claude", "identity_id": "id1",
                                            "token": "new", "reclaimed": True})])
        reg = wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertEqual(reg["name"], "claude")

    def test_dead_token_files_are_removed(self):
        path = self._save()
        register = Mock(return_value=(200, {"name": "claude", "identity_id": "id9",
                                             "token": "t9", "dead_tokens": ["old"]}))
        wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertFalse(path.exists())
        self.assertEqual([f["token"] for f in wrapper._load_token_files(self.data, "claude")],
                         ["t9"])

    def test_takeover_migrates_queue_under_previous_name(self):
        self._save(name="claude-2")
        (self.data / "claude-2_queue.jsonl").write_text('{"n":1}\n', "utf-8")
        register = Mock(return_value=(200, {"name": "claude", "identity_id": "id1",
                                             "token": "new", "reclaimed": True,
                                             "migrate_queue_from": "claude-2"}))
        wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertTrue((self.data / "claude_queue.jsonl").exists())
        self.assertFalse((self.data / "claude-2_queue.jsonl").exists())

    def _takeover_from_claude_2(self):
        self._save(name="claude-2")
        (self.data / "claude-2_queue.jsonl").write_text('{"n":1}\n', "utf-8")
        return Mock(return_value=(200, {"name": "claude", "identity_id": "id1",
                                        "token": "new", "reclaimed": True,
                                        "migrate_queue_from": "claude-2"}))

    def test_queue_locked_by_a_writer_does_not_lose_the_rotated_token(self):
        register = self._takeover_from_claude_2()
        real_replace = os.replace
        def locked(src, dst):
            if str(src).endswith("claude-2_queue.jsonl"):
                raise PermissionError("queue open")
            return real_replace(src, dst)
        with patch.object(wrapper.os, "replace", side_effect=locked):
            reg = wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertEqual(reg["name"], "claude", "startup must survive a failed migration")
        self.assertEqual([f["token"] for f in wrapper._load_token_files(self.data, "claude")],
                         ["new"])
        self.assertTrue((self.data / "claude-2_queue.jsonl").exists(), "nothing lost")

    def test_migration_that_never_succeeded_is_finished_on_next_startup(self):
        register = self._takeover_from_claude_2()
        real_replace = os.replace
        def locked(src, dst):
            if str(src).endswith("claude-2_queue.jsonl"):
                raise PermissionError("queue open")
            return real_replace(src, dst)
        with patch.object(wrapper.os, "replace", side_effect=locked):
            wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        # Relaunch: the server has no migration hint any more.
        for f in wrapper._load_token_files(self.data, "claude"):
            f["path"].unlink()
        self._save(token="new")
        restart = Mock(return_value=(200, {"name": "claude", "identity_id": "id1",
                                            "token": "newer", "reclaimed": True}))
        wrapper._acquire_identity(restart, self.data, "claude", sleep=Mock())
        self.assertFalse((self.data / "claude-2_queue.jsonl").exists())
        self.assertIn('"n":1', (self.data / "claude_queue.jsonl").read_text("utf-8"))
        self.assertEqual(list(self.data.glob("*.pending")), [])

    def test_pending_migration_is_retried_while_running(self):
        (self.data / "claude-2_queue.jsonl").write_text('{"n":1}\n', "utf-8")
        (self.data / "claude_queue.from-claude-2.pending").write_text("", "utf-8")
        self.assertTrue(wrapper._has_pending_migrations(self.data, "claude"))
        wrapper._recover_queue_claims(self.data, "claude")
        self.assertIn('"n":1', (self.data / "claude_queue.jsonl").read_text("utf-8"))
        self.assertFalse(wrapper._has_pending_migrations(self.data, "claude"))

    def test_background_retry_delivers_once_the_queue_is_released(self):
        (self.data / "claude-2_queue.jsonl").write_text('{"n":1}\n', "utf-8")
        wrapper._pending_marker(self.data, "claude-2", "claude").touch()
        real_replace = os.replace
        locked_for = [2]
        def locked(src, dst):
            if str(src).endswith("claude-2_queue.jsonl") and locked_for[0]:
                locked_for[0] -= 1
                raise PermissionError("queue open")
            return real_replace(src, dst)
        sleep = Mock()
        with patch.object(wrapper.os, "replace", side_effect=locked):
            wrapper._retry_pending_migrations(self.data, lambda: "claude", sleep=sleep)
        self.assertEqual(sleep.call_count, 3)
        self.assertIn('"n":1', (self.data / "claude_queue.jsonl").read_text("utf-8"))
        self.assertFalse(wrapper._has_pending_migrations(self.data, "claude"))

    def test_background_retry_follows_a_rename_of_this_wrapper(self):
        # A second Claude joins while the thread sleeps: slot 1 becomes claude-1.
        (self.data / "claude-2_queue.jsonl").write_text('{"n":1}\n', "utf-8")
        wrapper._pending_marker(self.data, "claude-2", "claude").touch()
        names = iter(["claude"])
        get_name = lambda: next(names, "claude-1")
        wrapper._retry_pending_migrations(self.data, get_name, sleep=Mock())
        self.assertIn('"n":1', (self.data / "claude-1_queue.jsonl").read_text("utf-8"))
        self.assertFalse((self.data / "claude-2_queue.jsonl").exists())
        self.assertEqual(list(self.data.glob("*.pending")), [])

    def test_queue_locked_briefly_is_migrated_on_retry(self):
        register = self._takeover_from_claude_2()
        real_replace = os.replace
        calls = []
        def flaky(src, dst):
            if str(src).endswith("claude-2_queue.jsonl") and not calls:
                calls.append(src)
                raise PermissionError("queue open")
            return real_replace(src, dst)
        with patch.object(wrapper.os, "replace", side_effect=flaky):
            wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertFalse((self.data / "claude-2_queue.jsonl").exists())
        self.assertIn('"n":1', (self.data / "claude_queue.jsonl").read_text("utf-8"))

    def test_startup_recovers_its_own_interrupted_migration(self):
        self._save()
        (self.data / "claude_queue.from-claude-2_queue.jsonl.migrating").write_text(
            '{"n":1}\n', "utf-8")
        register = Mock(return_value=(200, {"name": "claude", "identity_id": "id1",
                                             "token": "new", "reclaimed": True}))
        wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertIn('"n":1', (self.data / "claude_queue.jsonl").read_text("utf-8"))

    def test_live_wrappers_token_is_never_presented(self):
        wrapper._save_token_file(self.data, {"base": "claude", "name": "claude",
                                             "identity_id": "live", "token": "live-tok"})
        register = Mock(return_value=(200, {"name": "claude-2", "identity_id": "id2",
                                             "token": "t2"}))
        wrapper._acquire_identity(register, self.data, "claude", sleep=Mock())
        self.assertNotIn("reclaim", register.call_args.args[0])


class TwoWrappersEndToEndTests(_EndpointHarness):
    """Two relaunched wrappers race for one crashed identity through the real endpoint."""

    def test_two_simultaneous_wrappers_get_distinct_identities(self):
        data = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)
        _, old = self._register({"base": "claude"})
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        wrapper._save_token_file(data, old, pid=dead.pid)
        self.bridge._presence["claude"] = 0

        lock = threading.Lock()   # the server handles one request at a time
        def register(body):
            with lock:
                return self._register(body)

        barrier = threading.Barrier(2)
        out = []
        def launch():
            barrier.wait()
            out.append(wrapper._acquire_identity(register, data, "claude", sleep=Mock()))
        threads = [threading.Thread(target=launch) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(r["name"] for r in out), ["claude", "claude-2"])
        self.assertEqual(sum(1 for r in out if r.get("reclaimed")), 1)
        tokens = {f["token"] for f in wrapper._load_token_files(data, "claude")}
        self.assertEqual(tokens, {r["token"] for r in out})


class ShutdownHandlerTests(unittest.TestCase):
    def test_console_close_deregisters_as_reclaimable(self):
        on_close = Mock()
        handler = wrapper._console_ctrl_handler(on_close)
        self.assertTrue(handler(wrapper.CTRL_CLOSE_EVENT))
        on_close.assert_called_once_with()

    def test_ctrl_c_is_left_to_the_default_handling(self):
        on_close = Mock()
        handler = wrapper._console_ctrl_handler(on_close)
        self.assertFalse(handler(0))
        on_close.assert_not_called()

    def test_close_runs_once_even_if_signalled_twice(self):
        on_close = Mock()
        handler = wrapper._console_ctrl_handler(on_close)
        handler(wrapper.CTRL_CLOSE_EVENT)
        handler(wrapper.CTRL_CLOSE_EVENT)
        on_close.assert_called_once_with()

    def test_installed_handler_routes_close_to_callback(self):
        on_close = Mock()
        if os.name == "nt":
            import ctypes
            routine = wrapper._install_shutdown_handler(on_close)
            self.addCleanup(ctypes.windll.kernel32.SetConsoleCtrlHandler, routine, False)
            self.assertEqual(routine(wrapper.CTRL_CLOSE_EVENT), 1)
        else:
            import signal
            saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
            for s, h in saved.items():
                self.addCleanup(signal.signal, s, h)
            wrapper._install_shutdown_handler(on_close)
            with self.assertRaises(SystemExit):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        on_close.assert_called_once_with()

    def test_reclaimable_shutdown_keeps_token_file_and_deliberate_exit_removes_it(self):
        data = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)
        entry = {"base": "claude", "name": "claude", "identity_id": "i", "token": "t"}
        path = wrapper._save_token_file(data, entry)
        post = Mock()
        wrapper._deregister(post, "claude", "t", path, reclaimable=True)
        post.assert_called_once_with("claude", "t", {"reclaimable": True})
        self.assertTrue(path.exists())
        wrapper._deregister(post, "claude", "t", path, reclaimable=False)
        self.assertEqual(post.call_args.args[2], {"reclaimable": False})
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
