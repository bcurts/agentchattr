"""Tests for wrapper_unix.inject tri-state result contract.

inject() must report "deferred" only when tmux rejected the text command
(retry is safe), and "injected-uncertain" once text may have reached the
composer — a retry there could duplicate the prompt.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import wrapper_unix  # noqa: E402


class UnixInjectContractTests(unittest.TestCase):
    @mock.patch.object(wrapper_unix.time, "sleep")
    @mock.patch.object(wrapper_unix.subprocess, "run")
    def test_returns_explicit_success_only_after_text_and_enter(self, run, _sleep):
        run.side_effect = [
            mock.Mock(returncode=0), mock.Mock(returncode=0),
            mock.Mock(returncode=0), mock.Mock(returncode=1),
        ]
        self.assertEqual(
            wrapper_unix.inject("task", tmux_session="s"), "injected"
        )
        self.assertEqual(
            wrapper_unix.inject("task", tmux_session="s"),
            "injected-uncertain",
        )

    @mock.patch.object(wrapper_unix.subprocess, "run")
    def test_text_rejection_is_retryable_and_timeout_is_uncertain(self, run):
        run.return_value = mock.Mock(returncode=1)
        self.assertEqual(
            wrapper_unix.inject("task", tmux_session="s"), "deferred"
        )
        run.side_effect = wrapper_unix.subprocess.TimeoutExpired("tmux", 1)
        self.assertEqual(
            wrapper_unix.inject("task", tmux_session="s"),
            "injected-uncertain",
        )


if __name__ == "__main__":
    unittest.main()
