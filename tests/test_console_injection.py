import ctypes
import unittest
import sys
from unittest.mock import patch

if sys.platform == "win32":
    import wrapper_windows as windows


@unittest.skipUnless(sys.platform == "win32", "Windows console only")
class ConsoleInjectionTests(unittest.TestCase):
    def test_waits_for_queued_keys_before_enter_and_quiet_gap(self):
        events = []
        counts = iter([10, 2, 0])

        def pending(_handle, out):
            value = next(counts)
            ctypes.cast(out, ctypes.POINTER(windows.wintypes.DWORD)).contents.value = value
            events.append(('pending', value))
            return 1

        def write(_handle, _records, count, written):
            ctypes.cast(written, ctypes.POINTER(windows.wintypes.DWORD)).contents.value = count
            events.append(('queued', count))
            return 1

        with (patch.object(windows.kernel32, 'GetStdHandle', return_value=7),
              patch.object(windows.kernel32, 'WriteConsoleInputW', side_effect=write),
              patch.object(windows.kernel32, 'GetNumberOfConsoleInputEvents', side_effect=pending),
              patch.object(windows.time, 'sleep', side_effect=lambda delay: events.append(('sleep', delay))),
              patch.object(windows, '_write_key', side_effect=lambda *_a, **_kw: events.append(('enter',)))):
            windows.inject('hello', delay=0.3)
        self.assertEqual(events, [('queued', 10), ('pending', 10), ('sleep', .05), ('pending', 2),
                                  ('sleep', .05), ('pending', 0), ('sleep', .3), ('enter',), ('enter',)])

    def test_timeout_or_queue_error_does_not_send_enter(self):
        for ok in (True, False):
            with self.subTest(ok=ok):
                def pending(_handle, out):
                    ctypes.cast(out, ctypes.POINTER(windows.wintypes.DWORD)).contents.value = 5
                    return ok
                with (patch.object(windows.kernel32, 'GetNumberOfConsoleInputEvents', side_effect=pending),
                      patch.object(windows.time, 'monotonic', side_effect=[0, 31]),
                      patch.object(windows, '_write_key') as enter):
                    with self.assertRaises((TimeoutError, OSError)):
                        windows._wait_for_input_drain(7)
                    enter.assert_not_called()

    def test_incomplete_write_never_submits(self):
        def partial(_handle, _records, _count, written):
            ctypes.cast(written, ctypes.POINTER(windows.wintypes.DWORD)).contents.value = 1
            return 1
        with (patch.object(windows.kernel32, 'GetStdHandle', return_value=7),
              patch.object(windows.kernel32, 'WriteConsoleInputW', side_effect=partial),
              patch.object(windows, '_write_key') as enter):
            with self.assertRaises(OSError):
                windows.inject('hello')
            enter.assert_not_called()


if __name__ == '__main__':
    unittest.main()
