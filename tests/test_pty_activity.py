import unittest
from unittest.mock import Mock

from pty_activity import get_activity_checker


class ActivityTests(unittest.TestCase):
    def test_unknown_is_not_idle_and_valid_patterns_drive_state(self):
        client = Mock()
        client.screen.side_effect = [None, ['initial'], ['working'], ['permission'], None, ['done']]
        patterns = Mock()
        patterns.classify.side_effect = [(None, None), ('working', None), ('needs_input', None), ('idle', None)]
        check = get_activity_checker(client, pattern_store=patterns)
        self.assertIsNone(check())
        self.assertIsNone(check())
        self.assertTrue(check())
        self.assertFalse(check())
        self.assertTrue(check.needs_input())
        self.assertIsNone(check())
        self.assertFalse(check.needs_input())
        self.assertFalse(check())

    def test_unclassified_first_screen_stays_unknown_until_quiet(self):
        client = Mock()
        client.screen.return_value = ['unchanged']
        patterns = Mock()
        patterns.classify.return_value = (None, None)
        check = get_activity_checker(client, pattern_store=patterns)
        for _ in range(5):
            self.assertIsNone(check())
        self.assertFalse(check())

    def test_unavailable_classifier_remains_unknown(self):
        client = Mock()
        client.screen.return_value = ['screen']
        patterns = Mock()
        patterns.classify.return_value = ('unavailable', None)
        self.assertIsNone(get_activity_checker(client, pattern_store=patterns)())
