import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import wrapper


class RegistrationRetryTests(unittest.TestCase):
    def test_release_after_window_shutdown_and_lease_expiry(self):
        clock = [0]
        def register(*args):
            if clock[0] < 30:
                raise wrapper.NameInUseError('in use')
            return {'name': 'c2'}
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(wrapper, '_register_instance', side_effect=register), \
             patch.object(wrapper.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(wrapper.time, 'sleep', side_effect=sleep):
            self.assertEqual(wrapper._register_at_startup(8300, 'codex', 'c2', 'c2'), {'name': 'c2'})
        self.assertEqual(clock[0], 30)

    def test_conflict_then_release(self):
        conflict = wrapper.NameInUseError('Name already in use: c2')
        with patch.object(wrapper, '_register_instance', side_effect=[conflict, {'name': 'c2'}]) as register, \
             patch.object(wrapper.time, 'sleep') as sleep:
            self.assertEqual(wrapper._register_at_startup(8300, 'codex', 'c2', 'c2'), {'name': 'c2'})
        self.assertEqual(register.call_count, 2)
        sleep.assert_called_once()

    def test_live_owner_is_not_replaced_after_deadline(self):
        with patch.object(wrapper, '_register_instance', side_effect=wrapper.NameInUseError('in use')) as register, \
             patch.object(wrapper.time, 'monotonic', side_effect=[0, 1, 2]), \
             patch.object(wrapper.time, 'sleep'):
            with self.assertRaises(wrapper.NameInUseError):
                wrapper._register_at_startup(8300, 'codex', 'c2', 'c2', timeout=2)
        self.assertEqual(register.call_count, 2)

    def test_other_registration_errors_are_not_retried(self):
        with patch.object(wrapper, '_register_instance', side_effect=RuntimeError('invalid name')) as register:
            with self.assertRaisesRegex(RuntimeError, 'invalid name'):
                wrapper._register_at_startup(8300, 'codex', 'bad', 'bad')
        register.assert_called_once()

    def test_http_conflict_is_classified_for_requested_name_only(self):
        def error(message):
            body = io.BytesIO(json.dumps({'error': message}).encode())
            return urllib.error.HTTPError('http://localhost', 409, 'Conflict', {}, body)
        with patch('urllib.request.urlopen', side_effect=error('Name already in use: c2')):
            with self.assertRaises(wrapper.NameInUseError):
                wrapper._register_instance(8300, 'codex', 'c2', 'c2')
        with patch('urllib.request.urlopen', side_effect=error('That name is reserved')):
            with self.assertRaises(RuntimeError) as raised:
                wrapper._register_instance(8300, 'codex', 'c2', 'c2')
            self.assertNotIsInstance(raised.exception, wrapper.NameInUseError)


if __name__ == '__main__':
    unittest.main()
