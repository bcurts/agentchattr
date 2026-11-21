"""The synced channel_attention UI pref accepts only its three modes."""
import unittest

import app


class ChannelAttentionPrefTests(unittest.TestCase):
    def test_accepts_known_modes(self):
        for mode in ('off', 'highlight', 'flash'):
            self.assertEqual(app._clean_ui_prefs({'channel_attention': mode}), {'channel_attention': mode})

    def test_rejects_unknown_values(self):
        for value in ('glow', '', None, 1, ['flash']):
            self.assertEqual(app._clean_ui_prefs({'channel_attention': value}), {})


if __name__ == '__main__':
    unittest.main()
