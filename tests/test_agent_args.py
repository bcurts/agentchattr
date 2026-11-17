"""Per-agent `args` from config.toml are passed to the CLI on every launch."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wrapper import _build_provider_launch  # noqa: E402


class AgentArgsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _launch(self, agent_cfg, extra=()):
        args, *_ = _build_provider_launch(
            agent="lead", agent_cfg=agent_cfg, instance_name="lead",
            data_dir=Path(self.tmp.name), proxy_url=None, extra_args=list(extra),
            env={}, project_dir=Path(self.tmp.name),
        )
        return args

    def test_config_args_come_before_command_line_args(self):
        args = self._launch({"command": "mycli", "args": ["--model", "opus"]}, extra=["--continue"])
        self.assertEqual(args[-3:], ["--model", "opus", "--continue"])

    def test_no_args_key_is_unchanged(self):
        self.assertEqual(self._launch({"command": "mycli"}, extra=["-x"]), ["-x"])

    def test_args_must_be_list_of_strings(self):
        for bad in ("--model opus", ["--model", 4]):
            with self.assertRaises(ValueError):
                self._launch({"command": "mycli", "args": bad})


if __name__ == "__main__":
    unittest.main()
