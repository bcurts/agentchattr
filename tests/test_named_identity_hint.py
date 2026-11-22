import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wrapper


class StopWatcher(BaseException):
    pass


class IdentityHintTests(unittest.TestCase):
    def test_first_trigger_hint_depends_on_explicit_name_and_slot(self):
        for slot, requested, expects_hint in [(2, "c2", False), (2, None, True), (1, None, False)]:
            with self.subTest(slot=slot, requested=requested), tempfile.TemporaryDirectory(
                dir=r"C:\Users\DL\Documents\Github\Temp"
            ) as directory:
                queue = Path(directory) / "queue"
                queue.write_text('{"channel":"agentchattr"}\n', encoding="utf-8")
                prompts = []

                def inject(prompt):
                    prompts.append(prompt)
                    raise StopWatcher()

                with patch.object(wrapper.time, "sleep"), patch.object(wrapper, "_fetch_role", return_value=""), \
                     patch.object(wrapper, "_fetch_active_rules", return_value=None):
                    with self.assertRaises(StopWatcher):
                        wrapper._queue_watcher(lambda: ("c2", queue), inject,
                            is_multi_instance=wrapper._needs_identity_hint({"slot": slot}, requested))
                self.assertEqual("status pill" in prompts[0], expects_hint)
                self.assertIn("#agentchattr", prompts[0])
