import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from store import MessageStore


class MessageIdPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'messages.jsonl'

    def test_delete_highest_id_then_restart(self):
        store = MessageStore(str(self.path))
        first = store.add('a', 'first')
        deleted = store.add('a', 'deleted')
        store.add('a', 'reply', reply_to=deleted['id'])
        store.delete([deleted['id'], 2])
        restarted = MessageStore(str(self.path))
        self.assertEqual(restarted.add('a', 'new')['id'], 3)
        self.assertEqual(restarted.get_by_id(first['id'])['text'], 'first')

    def test_clear_and_delete_channel_preserve_counter(self):
        for operation in ('clear', 'clear_channel', 'delete_channel'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as d:
                path = Path(d) / 'messages.jsonl'
                store = MessageStore(str(path))
                store.add('a', 'one', channel='other')
                store.add('a', 'two', channel='other')
                if operation == 'clear':
                    store.clear()
                elif operation == 'clear_channel':
                    store.clear('other')
                else:
                    store.delete_channel('other')
                self.assertEqual(MessageStore(str(path)).add('a', 'next')['id'], 2)

    def test_log_max_overrides_older_marker(self):
        store = MessageStore(str(self.path))
        store.add('a', 'one')
        store.delete([0])
        store.add('a', 'two')
        self.assertEqual(MessageStore(str(self.path)).add('a', 'three')['id'], 2)

    def test_failed_marker_write_does_not_rewrite_log(self):
        store = MessageStore(str(self.path))
        store.add('a', 'one')
        with patch('store.atomic_write_text', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                store.delete([0])
        self.assertEqual(MessageStore(str(self.path)).add('a', 'next')['id'], 1)


if __name__ == '__main__':
    unittest.main()
