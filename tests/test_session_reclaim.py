"""Tests for session reclaim / reconnection survival.

Regression coverage for the "stale or unknown authenticated agent session" issue:
a long-running agent (e.g. Claude Code) gets deregistered by the crash-timeout when
the machine sleeps (heartbeat threads freeze), then on wake re-presents its old token.
The registry must let that identity recover (reactivate the token) instead of treating
it as permanently dead — while a *fresh* re-registration (e.g. Codex relaunch) must
still supersede the old token.
"""

import sys
import unittest
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry import RuntimeRegistry


class SessionReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg = RuntimeRegistry(data_dir=self.tmp)
        self.reg.seed({
            "claude": {"label": "Claude", "color": "#ff6a00"},
            "codex": {"label": "Codex", "color": "#00B67D"},
        })

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deregistered_token_is_reclaimable(self):
        """Single persistent agent deregistered during sleep recovers via its token."""
        inst = self.reg.register("claude")
        token = inst["token"]
        self.reg.deregister("claude")          # crash-timeout fires during sleep
        resolved = self.reg.resolve_token(token)  # same token presented on wake
        self.assertIsNotNone(resolved, "stale token should reactivate, not be rejected")
        self.assertEqual(resolved["name"], "claude")
        self.assertEqual(resolved["state"], "active")

    def test_fresh_registration_supersedes_reclaimable(self):
        """A fresh relaunch (new token) must win; the old token must NOT reactivate."""
        first = self.reg.register("codex")
        old_tok = first["token"]
        self.reg.deregister("codex")
        # The overnight gap is far longer than the 30s name-reservation grace, so the
        # fresh morning relaunch reclaims the canonical 'codex' name (not 'codex-2').
        self.reg._reserved.clear()
        second = self.reg.register("codex")     # fresh relaunch, new token, same name
        new_tok = second["token"]
        self.assertEqual(second["name"], "codex")
        self.assertIsNone(self.reg.resolve_token(old_tok),
                          "old token must stay stale once the name is freshly re-registered")
        self.assertIsNotNone(self.reg.resolve_token(new_tok))

    def test_claim_recovers_reclaimable_identity(self):
        """chat_claim(sender='claude') after deregister recovers the identity."""
        self.reg.register("claude")
        self.reg.deregister("claude")
        result = self.reg.claim("claude")
        self.assertIsInstance(result, dict, f"claim should recover, got: {result!r}")
        self.assertEqual(result["name"], "claude")
        self.assertEqual(result["state"], "active")

    def test_persistence_round_trip_survives_server_restart(self):
        """A live token must survive a server restart (new RuntimeRegistry, same data_dir)."""
        inst = self.reg.register("claude")
        token = inst["token"]
        reg2 = RuntimeRegistry(data_dir=self.tmp)   # simulates server process restart
        reg2.seed({"claude": {"label": "Claude", "color": "#ff6a00"}})
        self.assertIsNotNone(reg2.resolve_token(token),
                             "token should survive a server restart via persistence")

    def test_fresh_register_after_restart_takes_slot1_not_ghost(self):
        """After a server restart, a fresh wrapper must reclaim slot-1 'claude'.

        Regression for the restart-ghost bug: persisted instances must reload as
        reclaimable (token recoverable) rather than active. If they reload active,
        the empty post-restart presence map means the crash-timeout never clears the
        dead wrapper, so the fresh launch is bumped to 'claude-2' and the ghost is
        renamed 'claude-1' — two active instances where there should be one.
        """
        self.reg.register("claude")                  # long-running agent, persisted to disk
        reg2 = RuntimeRegistry(data_dir=self.tmp)    # server restart, presence map empty
        reg2.seed({
            "claude": {"label": "Claude", "color": "#ff6a00"},
            "codex": {"label": "Codex", "color": "#00B67D"},
        })
        fresh = reg2.register("claude")              # fresh wrapper launches
        self.assertEqual(fresh["name"], "claude",
                         "fresh register after restart must get slot-1 'claude', not a numbered slot")
        active = [n for n, d in reg2.get_all().items()
                  if d["base"] == "claude" and d["state"] == "active"]
        self.assertEqual(active, ["claude"], f"exactly one active claude expected, got {active}")

    def test_custom_name_fresh_register_supersedes_old_token(self):
        """Fresh register('claude') must supersede a deregistered custom-name identity.

        Regression for the exact-name supersede gap: a custom alias (claude-music,
        base=claude slot=1) is not removed by register's name-keyed pop, so its old
        token can later reactivate alongside the fresh slot-1 'claude' — two active
        instances at base=claude slot=1. Fresh-wins must be enforced by (base, slot).
        """
        first = self.reg.register("claude")
        old_tok = first["token"]
        self.reg.claim("claude", "claude-music")     # custom identity, base=claude slot=1
        self.reg.deregister("claude-music")          # crash-timeout / shutdown -> reclaimable
        self.reg._reserved.clear()                   # overnight gap >> 30s name-reservation grace
        fresh = self.reg.register("claude")          # fresh relaunch of the base
        self.assertEqual(fresh["name"], "claude")
        self.assertIsNone(self.reg.resolve_token(old_tok),
                          "old custom-name token must not reactivate once slot-1 is freshly registered")
        active_slot1 = [n for n, d in self.reg.get_all().items()
                        if d["base"] == "claude" and d["slot"] == 1 and d["state"] == "active"]
        self.assertEqual(active_slot1, ["claude"],
                         f"exactly one active base=claude slot=1 expected, got {active_slot1}")

    def test_claim_cannot_revive_identity_whose_slot_is_now_live(self):
        """chat_claim must not recover a reclaimable identity whose (base, slot) is live.

        Same fresh-wins invariant as register/resolve_token, but at the claim path
        (_restore_reclaimable_locked). Repro via rename-back: two claude instances, the
        slot-1 deregisters (→ reclaimable as 'claude-1'), the survivor is renamed back to
        the canonical 'claude' (slot 1). A later claim('claude-1') must NOT revive the old
        slot-1 identity on top of the live 'claude' — that would be two active slot-1s.
        """
        self.reg.register("claude")                  # claude (slot 1)
        self.reg.register("claude")                  # -> claude-1 (slot 1) + claude-2 (slot 2)
        self.reg.deregister("claude-1")              # slot-1 leaves -> reclaimable; claude-2 renamed back to 'claude'
        self.assertIn("claude", self.reg.get_all_names(), "survivor should be renamed back to canonical 'claude'")
        self.reg.claim("claude-1")                   # stale wrapper tries to recover its old slot-1 identity
        active_slot1 = [n for n, d in self.reg.get_all().items()
                        if d["base"] == "claude" and d["slot"] == 1 and d["state"] == "active"]
        self.assertEqual(active_slot1, ["claude"],
                         f"claim must not create a second active slot-1; got {active_slot1}")


if __name__ == "__main__":
    unittest.main()
