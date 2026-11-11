import unittest
from pathlib import Path

STATIC = Path(__file__).parent.parent / "static"


class HumanSettingsStaticTests(unittest.TestCase):
    def test_color_field_in_settings(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="setting-user-color"', html)
        self.assertIn('id="setting-user-color-reset"', html)

    def test_color_wiring_and_no_own_send_sound(self):
        js = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("setupUserColorSetting();", js)
        self.assertIn("agentchattr-user-color", js)
        # audio is only for @mentions of you: no sound for your own messages
        self.assertNotIn("playOwnSendSound", js)
        self.assertNotIn("'you'", js)


if __name__ == "__main__":
    unittest.main()
