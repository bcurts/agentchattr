import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
from jobs import JobStore
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

    def test_a_system_sender_with_the_default_chat_type_does_not(self):
        self.assertFalse(app._unarchive_for_activity("ops", "chat", "system"))
        self.assertEqual(app.room_settings["archived_channels"], ["ops", "dev"])
        self.save.assert_not_called()

    def test_a_channel_that_is_not_archived_changes_nothing(self):
        self.assertFalse(app._unarchive_for_activity("general", "chat"))
        self.save.assert_not_called()


class ChatCallbackActivityTests(unittest.TestCase):
    """The real _handle_new_message path, for notices stored as ordinary chat messages."""

    def setUp(self):
        saved = {k: app.room_settings.get(k) for k in ("channels", "archived_channels")}
        self.addCleanup(lambda: app.room_settings.update(saved))
        app.room_settings["channels"] = ["general", "ops"]
        app.room_settings["archived_channels"] = ["ops"]
        router = MagicMock()
        router.get_targets.return_value = []
        router.is_paused.return_value = False
        for name, value in (("_save_settings", MagicMock()), ("broadcast_settings", AsyncMock()),
                            ("broadcast", AsyncMock()), ("router", router)):
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, sender, msg_type="chat"):
        message = {"id": 1, "sender": sender, "text": "hello", "type": msg_type, "channel": "ops"}
        asyncio.run(app._handle_new_message(message))

    def test_a_person_or_agent_message_un_archives_and_broadcasts_settings(self):
        self.post("derek")

        self.assertEqual(app.room_settings["archived_channels"], [])
        app.broadcast_settings.assert_awaited_once()

    def test_summary_card_is_broadcast_without_routing_its_mentions(self):
        message = {"id": 1, "sender": "agy", "text": "Selected @all", "type": "summary", "channel": "ops"}
        asyncio.run(app._handle_new_message(message))
        app.broadcast.assert_awaited_once_with(message)
        app.router.get_targets.assert_not_called()

    def test_a_system_sender_chat_message_does_not(self):
        self.post("system")

        self.assertEqual(app.room_settings["archived_channels"], ["ops"])
        app.broadcast_settings.assert_not_awaited()


class JobThreadActivityTests(unittest.TestCase):
    """Job messages skip _handle_new_message, so they reach the archive logic through the job callback."""

    def setUp(self):
        saved = {k: app.room_settings.get(k) for k in ("channels", "archived_channels")}
        self.addCleanup(lambda: app.room_settings.update(saved))
        app.room_settings["channels"] = ["general", "ops", "dev"]
        app.room_settings["archived_channels"] = ["ops", "dev"]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(str(Path(self.tmp.name) / "jobs.json"))
        self.fired = []
        self.store.on_change(lambda action, data: self.fired.append((action, data)))
        self.job = self.store.create("Fix it", "job", "ops", "derek")
        self.fired.clear()
        for target, replacement in (
            (app, {"jobs": self.store}),
            (app, {"_save_settings": MagicMock()}),
            (app, {"broadcast_settings": AsyncMock()}),
        ):
            for name, value in replacement.items():
                patcher = patch.object(target, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)

    def deliver(self):
        for action, data in self.fired:
            asyncio.run(app.broadcast_job(action, data))

    def test_a_job_message_un_archives_its_channel_and_broadcasts_settings(self):
        self.store.add_message(self.job["id"], "derek", "any update?")
        self.deliver()

        self.assertEqual(app.room_settings["archived_channels"], ["dev"])
        app._save_settings.assert_called_once()
        app.broadcast_settings.assert_awaited_once()

    def test_creating_or_updating_a_job_alone_does_not(self):
        self.store.update_status(self.job["id"], "done")
        self.deliver()

        self.assertEqual(app.room_settings["archived_channels"], ["ops", "dev"])
        app.broadcast_settings.assert_not_awaited()

    def test_a_job_message_sent_by_system_does_not(self):
        self.store.add_message(self.job["id"], "system", "auto note")
        self.deliver()

        self.assertEqual(app.room_settings["archived_channels"], ["ops", "dev"])
        app.broadcast_settings.assert_not_awaited()

    def test_settings_payload_counts_the_job_reply_as_fresh_activity(self):
        with patch.object(app, "store", MessageStore(str(Path(self.tmp.name) / "messages.jsonl"))):
            app.store.add("a", "old", channel="ops", timestamp=100.0)
            self.store.add_message(self.job["id"], "derek", "any update?", timestamp=5000.0)
            self.deliver()

            self.assertEqual(app._settings_payload()["channel_activity"]["ops"], 5000.0)
            self.assertEqual(app._settings_payload()["channel_activity"]["ops"], 5000.0)  # and after a reload

    def test_system_and_deleted_job_replies_do_not_count_as_activity(self):
        self.store.add_message(self.job["id"], "system", "auto note", timestamp=9000.0)
        deleted = self.store.add_message(self.job["id"], "derek", "oops", timestamp=8000.0)
        self.store.delete_message(self.job["id"], deleted["id"])
        self.store.add_message(self.job["id"], "derek", "real", timestamp=7000.0)

        self.assertEqual(self.store.channel_activity(), {"ops": 7000.0})

    def test_a_system_type_job_message_does_not(self):
        self.store.add_message(self.job["id"], "system", "status changed", msg_type="system")
        self.deliver()

        self.assertEqual(app.room_settings["archived_channels"], ["ops", "dev"])


if __name__ == "__main__":
    unittest.main()
