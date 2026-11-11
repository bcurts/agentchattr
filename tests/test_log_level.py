import importlib.util
import logging
import os
import unittest
from pathlib import Path


def _load_run():
    spec = importlib.util.spec_from_file_location("run_mod", Path(__file__).parent.parent / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LogLevelTests(unittest.TestCase):
    def test_default_is_quiet_and_env_overrides(self):
        mod = _load_run()
        old = os.environ.pop("AGENTCHATTR_LOG_LEVEL", None)
        try:
            self.assertEqual(mod._log_level(), logging.WARNING)
            os.environ["AGENTCHATTR_LOG_LEVEL"] = "info"
            self.assertEqual(mod._log_level(), logging.INFO)
            os.environ["AGENTCHATTR_LOG_LEVEL"] = "nonsense"
            self.assertEqual(mod._log_level(), logging.WARNING)
        finally:
            os.environ.pop("AGENTCHATTR_LOG_LEVEL", None)
            if old is not None:
                os.environ["AGENTCHATTR_LOG_LEVEL"] = old


if __name__ == "__main__":
    unittest.main()
