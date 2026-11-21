"""Runs the Node behavior checks for static/channel-attention.js (skipped without Node)."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('channel_attention_smoke.js')


@unittest.skipUnless(shutil.which('node'), 'Node.js is not installed')
class ChannelAttentionScriptTests(unittest.TestCase):
    def test_behavior_checks_pass(self):
        result = subprocess.run(['node', str(SCRIPT)], capture_output=True, text=True, timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
