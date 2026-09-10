"""Ready-gate registry contract.

The gate's registry invariants: gated registration enters `starting`;
`mark_ready` is the ONLY path that activates a starting instance; claim
refuses starting instances; cancel_starting removes with NO reservation and
NO reclaimable entry via the same removal primitive as deregister, so family
rename-back bookkeeping runs on both paths.
"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry import RuntimeRegistry


class ReadyGateRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.reg = RuntimeRegistry(data_dir=self._tmp.name)
        self.reg.seed({"claude": {"label": "Claude", "color": "#da7756"},
                       "codex": {"label": "Codex", "color": "#10a37f"}})

    def tearDown(self):
        self._tmp.cleanup()

    def test_gated_register_starts_in_starting(self):
        result = self.reg.register("claude", ready_gate=True)
        self.assertEqual(result["state"], "starting")
        self.assertEqual(self.reg.get_state("claude"), "starting")

    def test_ungated_register_still_active(self):
        result = self.reg.register("claude")
        self.assertEqual(result["state"], "active")
        self.assertEqual(self.reg.get_state("claude"), "active")

    def test_mark_ready_only_from_starting(self):
        self.reg.register("claude", ready_gate=True)
        self.assertTrue(self.reg.mark_ready("claude"))
        self.assertEqual(self.reg.get_state("claude"), "active")
        # Illegal transitions refuse
        self.assertFalse(self.reg.mark_ready("claude"))   # active -> ready: no
        self.assertFalse(self.reg.mark_ready("gemini"))   # unknown: no
        self.reg.register("codex")                        # ungated, active
        self.assertFalse(self.reg.mark_ready("codex"))    # never entered gate: no

    def test_mark_starting_regates_active_but_not_pending(self):
        self.reg.register("claude", ready_gate=True)
        self.reg.mark_ready("claude")
        self.assertTrue(self.reg.mark_starting("claude"))
        self.assertEqual(self.reg.get_state("claude"), "starting")
        # A pending instance (unclaimed placeholder) must NOT be re-gateable.
        self.reg.register("codex")
        with self.reg._lock:
            self.reg._instances["codex"].state = "pending"
        self.assertFalse(self.reg.mark_starting("codex"))

    def test_claim_refuses_starting_instance(self):
        self.reg.register("claude", ready_gate=True)
        res = self.reg.claim("claude")
        self.assertIsInstance(
            res, str,
            "claim must return an error string, not activate a starting instance")
        self.assertIn("starting", res)
        self.assertEqual(self.reg.get_state("claude"), "starting")

    def test_cancel_starting_no_reservation_no_reclaim_dead_token(self):
        first = self.reg.register("claude", ready_gate=True)
        self.assertTrue(self.reg.cancel_starting("claude"))
        self.assertIsNone(self.reg.resolve_token(first["token"]))
        relaunch = self.reg.register("claude", ready_gate=True)
        self.assertEqual(relaunch["name"], "claude")      # not claude-2
        self.assertNotIn("_renamed_slot1", relaunch)

    def test_cancel_starting_second_instance_renames_back(self):
        """Cancel shares deregister's family bookkeeping. claude active
        + gated claude-2; cancelling claude-2 must rename claude-1 -> claude."""
        self.reg.register("claude")                            # slot 1, active
        second = self.reg.register("claude", ready_gate=True)  # slot1 -> claude-1
        self.assertEqual(second["name"], "claude-2")
        self.assertTrue(self.reg.cancel_starting("claude-2"))
        names = set(self.reg.get_all().keys())
        self.assertIn("claude", names,
                      "rename-back bookkeeping must run on cancel")
        self.assertNotIn("claude-1", names)

    def test_cancel_starting_refuses_non_starting(self):
        self.reg.register("claude")
        self.assertFalse(self.reg.cancel_starting("claude"))   # active: deregister only

    def test_normal_deregister_still_reserves(self):
        """Pinned current behavior: the 30 s reservation SURVIVES normal deregister."""
        self.reg.register("claude")
        self.reg.deregister("claude")
        self.assertEqual(self.reg.register("claude")["name"], "claude-2")


class RemovalReturnShapeTests(unittest.TestCase):
    """The gate must not change what the existing removals return.

    This branch is opt-in and promises unchanged behaviour when the gate is
    unused. `cancelled` is meaningful only for the state-dependent expiry, so
    the ordinary removals keep their documented `{'ok': True}` plus optional
    `_renamed_back` exactly.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.reg = RuntimeRegistry(data_dir=self._tmp.name)
        self.reg.seed({"claude": {"label": "Claude", "color": "#da7756"}})

    def tearDown(self):
        self._tmp.cleanup()

    def test_deregister_returns_exactly_ok(self):
        self.reg.register("claude")
        self.assertEqual(self.reg.deregister("claude"), {"ok": True})

    def test_reclaimable_deregister_returns_exactly_ok(self):
        self.reg.register("claude")
        self.assertEqual(self.reg.deregister("claude", reclaimable=True), {"ok": True})

    def test_cancel_starting_returns_exactly_ok(self):
        self.reg.register("claude", ready_gate=True)
        self.assertEqual(self.reg.cancel_starting("claude"), {"ok": True})

    def test_deregister_with_rename_back_adds_only_that_key(self):
        self.reg.register("claude")
        self.reg.register("claude")                     # claude -> claude-1, claude-2
        result = self.reg.deregister("claude-2")
        self.assertEqual(set(result), {"ok", "_renamed_back"})
        self.assertEqual(result["_renamed_back"], {"old": "claude-1", "new": "claude"})

    def test_only_expire_crashed_reports_cancelled(self):
        self.reg.register("claude", ready_gate=True)
        self.assertIn("cancelled", self.reg.expire_crashed("claude"))


class ExpireCrashedAtomicityTests(unittest.TestCase):
    """Expiry must decide by the state the instance has AT REMOVAL.

    The removal semantics differ by state, so choosing them outside the removal
    lock is a race: a wrapper re-gating from active to starting in the window
    would still be deregistered reclaimably, which reserves its name and leaves
    a revivable token — the ghost the gate cancel exists to prevent.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.reg = RuntimeRegistry(data_dir=self._tmp.name)
        self.reg.seed({"claude": {"label": "Claude", "color": "#da7756"}})

    def tearDown(self):
        self._tmp.cleanup()

    def test_starting_at_removal_time_is_cancelled(self):
        inst = self.reg.register("claude")               # registered active
        self.assertTrue(self.reg.mark_starting("claude"))  # wrapper re-gates

        record = self.reg.expire_crashed("claude")

        self.assertTrue(record["cancelled"])
        fresh = self.reg.register("claude", ready_gate=True)
        self.assertEqual(fresh["name"], "claude", "must reacquire the bare name")
        self.assertIsNone(self.reg.resolve_token(inst["token"]),
                          "the dead token must not be revivable")

    def test_active_at_removal_time_stays_reclaimable(self):
        inst = self.reg.register("claude")

        record = self.reg.expire_crashed("claude")

        self.assertFalse(record["cancelled"])
        recovered = self.reg.resolve_token(inst["token"])
        self.assertIsNotNone(recovered, "a sleeping agent recovers its identity")
        self.assertEqual(recovered["name"], "claude")

    def test_decision_is_not_taken_from_a_stale_read(self):
        """The choice must not come from a state read before the removal.

        An implementation that reads the state first and removes afterwards acts
        on a value that may already be wrong. The hook below fires only for such
        an implementation — the atomic path never consults `get_state` — and it
        moves the state inside exactly that window, so a split implementation
        selects the wrong removal and fails here.
        """
        self.reg.register("claude", ready_gate=True)      # starting
        real_get_state = self.reg.get_state

        def get_state_then_change(name):
            observed = real_get_state(name)
            self.reg.mark_ready(name)      # the state moves in the window...
            return observed                # ...and the caller acts on the old one

        self.reg.get_state = get_state_then_change

        record = self.reg.expire_crashed("claude")

        self.assertIsNotNone(record, "expiry must remove the instance it was given")
        self.assertTrue(record["cancelled"],
                        "a starting instance must take the cancel path")

    def test_a_concurrent_regate_cannot_land_inside_the_removal(self):
        """Mutual exclusion, proven with a barrier inside the removal lock.

        The hook runs after expiry has chosen its semantics but before the lock
        is released. A `mark_starting` issued from another thread must still be
        waiting at that point; if it could complete, the decision and the
        removal would be describing different states.
        """
        self.reg.register("claude")
        inside = threading.Event()
        regate_done = threading.Event()

        original = self.reg._rename_back_if_single_locked

        def barrier(base):
            inside.set()
            regate_done.wait(0.5)
            self.assertFalse(
                regate_done.is_set(),
                "mark_starting completed while the removal held the lock")
            return original(base)

        self.reg._rename_back_if_single_locked = barrier

        def regate():
            inside.wait(1)
            self.reg.mark_starting("claude")
            regate_done.set()

        racer = threading.Thread(target=regate)
        racer.start()
        try:
            self.assertIsNotNone(self.reg.expire_crashed("claude"))
        finally:
            racer.join(2)
        self.assertFalse(racer.is_alive())


class GateSurvivesRestartTests(unittest.TestCase):
    """A server restart must not activate an agent whose CLI was never proven.

    On startup every persisted instance is reloaded as reclaimable, and a live
    wrapper transparently recovers its identity through resolve_token. That
    recovery path must preserve `starting`, or restarting the server silently
    opens the gate for an agent that is still booting.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.seed = {"claude": {"label": "Claude", "color": "#da7756"}}

    def tearDown(self):
        self._tmp.cleanup()

    def _restart(self):
        """A fresh registry over the same data dir, as a server restart would."""
        reg = RuntimeRegistry(data_dir=self._tmp.name)
        reg.seed(self.seed)
        return reg

    def test_starting_survives_token_recovery_after_restart(self):
        reg = RuntimeRegistry(data_dir=self._tmp.name)
        reg.seed(self.seed)
        inst = reg.register("claude", ready_gate=True)

        recovered = self._restart().resolve_token(inst["token"])

        self.assertIsNotNone(recovered, "a live wrapper must still recover")
        self.assertEqual(recovered["state"], "starting",
                         "restart must not activate an unproven CLI")

    def test_active_still_reactivates_after_restart(self):
        """The sleep/crash recovery path for a proven agent is unchanged."""
        reg = RuntimeRegistry(data_dir=self._tmp.name)
        reg.seed(self.seed)
        inst = reg.register("claude")

        recovered = self._restart().resolve_token(inst["token"])

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["state"], "active")

    def test_ready_after_recovery_activates(self):
        reg = RuntimeRegistry(data_dir=self._tmp.name)
        reg.seed(self.seed)
        inst = reg.register("claude", ready_gate=True)

        restarted = self._restart()
        restarted.resolve_token(inst["token"])
        self.assertTrue(restarted.mark_ready("claude"))
        self.assertEqual(restarted.get_state("claude"), "active")


if __name__ == "__main__":
    unittest.main()
