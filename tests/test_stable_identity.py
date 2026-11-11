"""Stable display identities across wrapper exits, reconnects and server restarts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from registry import RuntimeRegistry


class StableIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {"codex": {"label": "Codex", "color": "#10a37f"}}
        self.reg = self.restart()

    def restart(self):
        reg = RuntimeRegistry(self.tmp.name)
        reg.seed(self.config)
        return reg

    def test_concurrent_instance_never_renames_or_recolors_survivor(self):
        first = self.reg.register("codex")
        second = self.reg.register("codex")
        self.assertEqual(second["name"], "codex-2")
        self.assertEqual(self.reg.resolve_token(first["token"])["name"], "codex")
        self.reg.deregister("codex")
        survivor = self.reg.resolve_token(second["token"])
        for key in ("name", "label", "color", "slot"):
            self.assertEqual(survivor[key], second[key])
        fresh = self.reg.register("codex")
        self.assertEqual(fresh["name"], "codex")
        self.assertEqual(fresh["color"], first["color"])

    def test_immediate_clean_relaunch_keeps_display_but_rotates_token(self):
        first = self.reg.register("codex")
        self.reg.deregister("codex")
        fresh = self.reg.register("codex")
        for key in ("name", "label", "color", "slot"):
            self.assertEqual(fresh[key], first[key])
        self.assertNotEqual(first["token"], fresh["token"])
        self.assertIsNone(self.reg.resolve_token(first["token"]))

    def test_custom_name_label_and_color_survive_clean_exit_and_restart(self):
        first = self.reg.register("codex")
        self.reg.rename("codex", "builder", "Team Builder")
        self.reg.deregister("builder")
        self.config["codex"]["color"] = "#abcdef"
        fresh = self.restart().register("codex")
        self.assertEqual(fresh["name"], "builder")
        self.assertEqual(fresh["label"], "Team Builder")
        self.assertEqual(fresh["color"], first["color"])
        data = json.loads((Path(self.tmp.name) / "registry.json").read_text())
        self.assertEqual(set(data["profiles"]["codex"]["1"]), {"name", "label", "color"})

    def test_stale_heartbeat_reuses_slot_before_background_sweep(self):
        first = self.reg.register("codex")
        with patch("registry.time.time", return_value=first["registered_at"] + 16):
            fresh = self.reg.register("codex")
        self.assertEqual(fresh["name"], "codex")
        self.assertEqual(fresh["_expired"], ["codex"])
        self.assertIsNone(self.reg.resolve_token(first["token"]))

    def test_authenticated_activity_keeps_live_slot(self):
        first = self.reg.register("codex")
        with patch("registry.time.time", return_value=first["registered_at"] + 14):
            self.reg.resolve_token(first["token"])
        with patch("registry.time.time", return_value=first["registered_at"] + 20):
            second = self.reg.register("codex")
        self.assertEqual(second["name"], "codex-2")
        self.assertNotIn("_expired", second)

    def test_claim_exact_primary_does_not_claim_other_pending_instance(self):
        self.reg.register("codex")
        self.reg.register("codex")
        self.reg._instances["codex-2"].state = "pending"
        self.assertEqual(self.reg.claim("codex")["name"], "codex")

    def test_legacy_slot1_migrates_on_token_reconnect(self):
        first = self.reg.register("codex")
        path = Path(self.tmp.name) / "registry.json"
        data = json.loads(path.read_text())
        inst = data["instances"].pop("codex")
        inst.update(name="codex-1", label="Codex 1")
        data["instances"]["codex-1"] = inst
        data.pop("profiles")  # pre-upgrade format
        path.write_text(json.dumps(data))
        (Path(self.tmp.name) / "renames.json").write_text(json.dumps({"codex": "codex-1"}))
        reg = self.restart()
        restored = reg.resolve_token(first["token"])
        self.assertEqual(restored["name"], "codex")
        self.assertEqual(restored["label"], "Codex")
        self.assertEqual(restored["color"], first["color"])
        self.assertEqual(reg.resolve_name("codex"), "codex")

    def test_reused_name_overrides_old_rename_chain(self):
        self.reg.register("codex")
        self.reg.rename("codex", "builder")
        self.reg.rename("builder", "codex")
        self.assertEqual(self.reg.resolve_name("codex"), "codex")


if __name__ == "__main__":
    unittest.main()
