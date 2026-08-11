import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if sys.platform != "win32":
    raise unittest.SkipTest("Windows console tests")

import wrapper_windows


class ConsoleAttachmentTests(unittest.TestCase):
    def test_existing_shared_console_is_preserved(self):
        api = MagicMock()
        with (
            patch.object(wrapper_windows, "kernel32", api),
            patch.object(wrapper_windows, "_current_console_contains_pid", return_value=True),
        ):
            attached, detail = wrapper_windows._ensure_agent_console(1234)
        self.assertTrue(attached)
        self.assertEqual(detail, "already attached")
        api.FreeConsole.assert_not_called()
        api.AttachConsole.assert_not_called()

    def test_attach_retries_after_detaching_wrong_console(self):
        api = MagicMock()
        api.AttachConsole.side_effect = [False, True]
        with (
            patch.object(wrapper_windows, "kernel32", api),
            patch.object(wrapper_windows, "_current_console_contains_pid", return_value=False),
            patch.object(wrapper_windows.time, "sleep"),
            patch.object(wrapper_windows.ctypes, "get_last_error", return_value=5),
        ):
            attached, detail = wrapper_windows._ensure_agent_console(4321, retries=2)
        self.assertTrue(attached)
        self.assertEqual(detail, "attached")
        self.assertEqual(api.FreeConsole.call_count, 2)
        self.assertEqual(api.AttachConsole.call_count, 2)

    def test_open_console_input_uses_conin_device(self):
        api = MagicMock()
        api.CreateFileW.return_value = 99
        with patch.object(wrapper_windows, "kernel32", api):
            handle = wrapper_windows._open_console_input()
        self.assertEqual(handle, 99)
        self.assertEqual(api.CreateFileW.call_args.args[0], "CONIN$")

    def test_inject_reports_write_failure_and_closes_handle(self):
        api = MagicMock()
        api.WriteConsoleInputW.return_value = False
        with (
            patch.object(wrapper_windows, "kernel32", api),
            patch.object(wrapper_windows, "_ensure_agent_console", return_value=(True, "attached")),
            patch.object(wrapper_windows, "_open_console_input", return_value=99),
            patch.object(wrapper_windows.ctypes, "get_last_error", return_value=6),
            patch("builtins.print") as output,
        ):
            result = wrapper_windows.inject("hello", agent_pid=4321, delay=0)
        self.assertFalse(result)
        api.CloseHandle.assert_called_once_with(99)
        self.assertIn("Injection failed", output.call_args.args[0])

    def test_attach_failure_is_written_to_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostic = Path(temp_dir) / "kimi_queue_injection.log"
            with (
                patch.object(
                    wrapper_windows,
                    "_ensure_agent_console",
                    return_value=(False, "AttachConsole failed"),
                ),
                patch("builtins.print"),
            ):
                result = wrapper_windows.inject(
                    "hello",
                    agent_pid=4321,
                    diagnostic_file=diagnostic,
                )
            contents = diagnostic.read_text(encoding="utf-8")
        self.assertFalse(result)
        self.assertIn("pid=4321", contents)
        self.assertIn("AttachConsole failed", contents)


class ActivityHandleTests(unittest.TestCase):
    def test_checker_recovers_after_console_becomes_available(self):
        api = MagicMock()
        api.GetConsoleScreenBufferInfo.return_value = True
        api.ReadConsoleOutputW.return_value = True
        with (
            patch.object(wrapper_windows, "kernel32", api),
            patch.object(wrapper_windows, "_open_console_output", side_effect=[None, 99]) as open_output,
            patch.object(wrapper_windows, "_console_generation", 0),
        ):
            checker = wrapper_windows.get_activity_checker([None])
            self.assertFalse(checker())
            self.assertFalse(checker())
        self.assertEqual(open_output.call_count, 2)
        api.GetConsoleScreenBufferInfo.assert_called_once()
        api.ReadConsoleOutputW.assert_called_once()

    def test_checker_reopens_output_after_console_switch(self):
        api = MagicMock()
        api.GetConsoleScreenBufferInfo.return_value = True
        api.ReadConsoleOutputW.return_value = True
        with (
            patch.object(wrapper_windows, "kernel32", api),
            patch.object(wrapper_windows, "_open_console_output", side_effect=[11, 22]) as open_output,
            patch.object(wrapper_windows, "_console_generation", 0),
        ):
            checker = wrapper_windows.get_activity_checker([None])
            checker()
            wrapper_windows._console_generation = 1
            checker()
        self.assertEqual(open_output.call_count, 2)
        api.CloseHandle.assert_called_once_with(11)

    def test_repeated_read_failures_eventually_clear_active_state(self):
        api = MagicMock()
        api.GetConsoleScreenBufferInfo.return_value = False
        trigger = [True]
        with (
            patch.object(wrapper_windows, "kernel32", api),
            patch.object(wrapper_windows, "_open_console_output", return_value=99),
            patch.object(wrapper_windows, "_console_generation", 0),
        ):
            checker = wrapper_windows.get_activity_checker([None], trigger_flag=trigger)
            states = [checker() for _ in range(5)]
        self.assertEqual(states, [True, True, True, True, False])
        self.assertEqual(api.CloseHandle.call_count, 5)


class RunAgentPidTests(unittest.TestCase):
    def test_watcher_injects_using_current_agent_pid(self):
        captured = {}

        class Process:
            pid = 2468
            returncode = 0

            def wait(self):
                captured["inject"]("message")

        def start_watcher(inject_fn):
            captured["inject"] = inject_fn

        holder = [None]
        with (
            patch.object(wrapper_windows, "enable_vt_mode"),
            patch.object(wrapper_windows, "_vt_keepalive_thread"),
            patch.object(wrapper_windows.subprocess, "Popen", return_value=Process()),
            patch.object(wrapper_windows, "inject", return_value=True) as inject,
        ):
            wrapper_windows.run_agent(
                "kimi",
                [],
                ".",
                {},
                None,
                "kimi",
                True,
                start_watcher,
                pid_holder=holder,
            )
        inject.assert_called_once_with(
            "message",
            delay=0.3,
            enter_backend="console_input",
            agent_pid=2468,
            diagnostic_file=None,
        )
        self.assertIsNone(holder[0])


if __name__ == "__main__":
    unittest.main()
