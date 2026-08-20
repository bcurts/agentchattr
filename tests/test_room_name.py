import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


class RoomSubtitleTests(unittest.TestCase):
    """A second name shown beside 'agentchattr' in the header.

    Several servers run at once (one per repo) and every one of them says
    'agentchattr', so the tab and header are indistinguishable. This adds a
    name alongside the product name rather than replacing it -- the existing
    `title` setting already covers a full rename.
    """

    def test_a_name_is_kept_as_typed(self):
        self.assertEqual(app.normalize_room_subtitle("urbly"), "urbly")

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(app.normalize_room_subtitle("  urbly  "), "urbly")

    def test_an_empty_value_clears_the_name(self):
        self.assertEqual(app.normalize_room_subtitle("   "), "")

    def test_an_overlong_name_is_capped_rather_than_rejected(self):
        result = app.normalize_room_subtitle("x" * 200)

        self.assertEqual(len(result), 40)

    def test_a_non_string_is_refused_rather_than_coerced(self):
        self.assertIsNone(app.normalize_room_subtitle(42))
        self.assertIsNone(app.normalize_room_subtitle(None))
        self.assertIsNone(app.normalize_room_subtitle(["urbly"]))

    def test_newlines_cannot_break_the_header_onto_two_lines(self):
        self.assertEqual(app.normalize_room_subtitle("ur\nbly"), "ur bly")

    def test_the_default_room_settings_carry_an_empty_name(self):
        self.assertEqual(app.room_settings.get("subtitle"), "")

    def test_a_settings_payload_reaches_the_room_through_the_validator(self):
        """Covers the wiring, not just the validator in isolation."""
        saved = app.room_settings.get("subtitle")
        self.addCleanup(lambda: app.room_settings.__setitem__("subtitle", saved))

        app.apply_room_subtitle({"subtitle": "  urbly\nlab  "})

        self.assertEqual(app.room_settings["subtitle"], "urbly lab")

    def test_a_payload_without_a_subtitle_leaves_the_current_one_alone(self):
        saved = app.room_settings.get("subtitle")
        self.addCleanup(lambda: app.room_settings.__setitem__("subtitle", saved))
        app.room_settings["subtitle"] = "urbly"

        app.apply_room_subtitle({"font": "mono"})

        self.assertEqual(app.room_settings["subtitle"], "urbly")

    def test_an_unusable_subtitle_does_not_overwrite_the_current_one(self):
        saved = app.room_settings.get("subtitle")
        self.addCleanup(lambda: app.room_settings.__setitem__("subtitle", saved))
        app.room_settings["subtitle"] = "urbly"

        app.apply_room_subtitle({"subtitle": {"evil": 1}})

        self.assertEqual(app.room_settings["subtitle"], "urbly")

    def test_a_subtitle_read_from_disk_is_normalised_like_a_typed_one(self):
        """settings.json is hand-editable, so the load path must check too."""
        import json
        import tempfile

        saved_settings = dict(app.room_settings)
        saved_config = app.config
        self.addCleanup(lambda: setattr(app, "config", saved_config))
        self.addCleanup(
            lambda: (app.room_settings.clear(), app.room_settings.update(saved_settings))
        )

        tmp = Path(tempfile.mkdtemp())
        (tmp / "settings.json").write_text(json.dumps({"subtitle": "x" * 200}))
        app.config = {"server": {"data_dir": str(tmp)}}

        app._load_settings()

        self.assertEqual(len(app.room_settings["subtitle"]), app.ROOM_SUBTITLE_MAX)

    def test_the_product_name_is_not_replaced_by_the_new_setting(self):
        """Control: `title` still means a full rename and is untouched."""
        self.assertEqual(app.room_settings.get("title"), "agentchattr")


if __name__ == "__main__":
    unittest.main()
