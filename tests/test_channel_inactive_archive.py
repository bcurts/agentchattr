import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
from store import MessageStore


class ChannelActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))

    def test_reports_the_newest_message_time_per_channel(self):
        self.store.add("a", "one", channel="general", timestamp=100.0)
        self.store.add("a", "two", channel="ops", timestamp=50.0)
        self.store.add("a", "three", channel="ops", timestamp=300.0)
        self.store.add("a", "four", channel="ops", timestamp=200.0)

        self.assertEqual(self.store.channel_activity(), {"general": 100.0, "ops": 300.0})

    def test_channels_without_messages_are_absent(self):
        self.assertEqual(self.store.channel_activity(), {})

    def test_a_message_with_no_channel_counts_for_general(self):
        self.store.add("a", "x", timestamp=10.0)
        self.store._messages[-1].pop("channel")

        self.assertEqual(self.store.channel_activity(), {"general": 10.0})


class InactiveHoursTests(unittest.TestCase):
    def test_accepts_zero_to_turn_the_group_off(self):
        self.assertEqual(app._clean_inactive_hours(0), 0)
        self.assertEqual(app._clean_inactive_hours("24"), 24)

    def test_clamps_to_one_year_and_rejects_garbage(self):
        self.assertEqual(app._clean_inactive_hours(10 ** 9), 24 * 365)
        self.assertEqual(app._clean_inactive_hours(-5), 0)
        self.assertIsNone(app._clean_inactive_hours("soon"))
        self.assertIsNone(app._clean_inactive_hours(None))


class NormaliseChannelPrefsTests(unittest.TestCase):
    def setUp(self):
        saved = {k: app.room_settings.get(k) for k in ("channels", "archived_channels", "inactive_hours")}
        self.addCleanup(lambda: app.room_settings.update(saved))
        app.room_settings["channels"] = ["general", "ops", "dev"]

    def test_defaults_are_empty_archive_and_24_hours(self):
        app.room_settings.pop("archived_channels", None)
        app.room_settings.pop("inactive_hours", None)

        app._normalise_channel_prefs()

        self.assertEqual(app.room_settings["archived_channels"], [])
        self.assertEqual(app.room_settings["inactive_hours"], 24)

    def test_general_unknown_duplicate_and_non_string_entries_are_dropped(self):
        app.room_settings["archived_channels"] = ["general", "ops", "ghost", "ops", 7, "dev"]

        app._normalise_channel_prefs()

        self.assertEqual(app.room_settings["archived_channels"], ["ops", "dev"])

    def test_a_hand_edited_non_list_archive_resets_to_empty(self):
        app.room_settings["archived_channels"] = "ops"

        app._normalise_channel_prefs()

        self.assertEqual(app.room_settings["archived_channels"], [])

    def test_invalid_hours_fall_back_to_the_default(self):
        app.room_settings["inactive_hours"] = "soon"

        app._normalise_channel_prefs()

        self.assertEqual(app.room_settings["inactive_hours"], 24)



class UnarchiveOnActivityTests(unittest.TestCase):
    def setUp(self):
        saved = {k: app.room_settings.get(k) for k in ("channels", "archived_channels")}
        self.addCleanup(lambda: app.room_settings.update(saved))
        app.room_settings["channels"] = ["general", "ops", "dev"]
        app.room_settings["archived_channels"] = ["ops", "dev"]
        patcher = patch.object(app, "_save_settings")
        self.save = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_chat_message_restores_only_its_channel(self):
        self.assertTrue(app._unarchive_for_activity("ops", "chat"))

        self.assertEqual(app.room_settings["archived_channels"], ["dev"])
        self.save.assert_called_once()

    def test_any_real_message_type_restores_it(self):
        for kind in ("chat", "decision", "job_proposal"):
            app.room_settings["archived_channels"] = ["ops"]
            self.assertTrue(app._unarchive_for_activity("ops", kind), kind)

    def test_join_leave_and_system_notices_do_not(self):
        for kind in ("join", "leave", "system"):
            self.assertFalse(app._unarchive_for_activity("ops", kind), kind)
        self.assertEqual(app.room_settings["archived_channels"], ["ops", "dev"])
        self.save.assert_not_called()

    def test_a_channel_that_is_not_archived_changes_nothing(self):
        self.assertFalse(app._unarchive_for_activity("general", "chat"))
        self.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
