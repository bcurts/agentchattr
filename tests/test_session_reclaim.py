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


if __name__ == "__main__":
    unittest.main()
