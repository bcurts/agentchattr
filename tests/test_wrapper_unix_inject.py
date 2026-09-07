"""Exercise real child-process timeouts without hanging a real tmux server."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

import wrapper_unix


@unittest.skipIf(sys.platform == 'win32', 'Unix executable fixture')
class InjectTimeoutTests(unittest.TestCase):
    def test_stuck_delivery_and_cleanup_return_without_retyping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = root / 'calls'
            executable = root / 'tmux'
            executable.write_text(
                '#!' + sys.executable + '\n'
                'import os, sys, time\n'
                'verb = sys.argv[1]\n'
                'with open(os.environ["TEST_TMUX_CALLS"], "a") as f: f.write(verb + "\\n")\n'
                'if verb == os.environ["TEST_TMUX_STALL"]: time.sleep(30)\n'
                'if verb == "display-message": print("%7")\n'
                'if verb == "paste-buffer" and os.environ["TEST_TMUX_STALL"] == "delete-buffer": sys.exit(1)\n'
            )
            executable.chmod(0o755)
            for stalled in ['display-message', 'load-buffer', 'paste-buffer', 'send-keys', 'delete-buffer']:
                with self.subTest(stalled=stalled):
                    calls.write_text('')
                    env = {'PATH': str(root) + os.pathsep + os.environ.get('PATH', ''),
                           'TEST_TMUX_CALLS': str(calls), 'TEST_TMUX_STALL': stalled}
                    with mock.patch.dict(os.environ, env), mock.patch.object(wrapper_unix, 'TMUX_COMMAND_TIMEOUT', .2):
                        start = time.monotonic()
                        ok = wrapper_unix.inject('short test', tmux_session='test', delay=0)
                        elapsed = time.monotonic() - start
                    verbs = calls.read_text().splitlines()
                    self.assertIs(ok, False)
                    self.assertLess(elapsed, 3, 'a child-process hang must be bounded')
                    self.assertLessEqual(verbs.count('paste-buffer'), 1, 'do not retry a possibly delivered paste')
                    self.assertLessEqual(verbs.count('send-keys'), 1, 'do not repeat Enter')
                    if stalled != 'send-keys':
                        self.assertNotIn('send-keys', verbs, 'no Enter after an earlier failure')
