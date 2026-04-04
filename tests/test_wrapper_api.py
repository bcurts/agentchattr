import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import wrapper_api


class WrapperApiPolicyTests(unittest.TestCase):
    def test_private_and_local_urls_are_allowed_without_opt_in(self):
        ok, lines, base_url = wrapper_api._validate_api_endpoint_policy(
            "qwen",
            {"base_url": "http://127.0.0.1:8189/v1"},
        )
        self.assertTrue(ok)
        self.assertEqual(lines, [])
        self.assertEqual(base_url, "http://127.0.0.1:8189/v1")

    def test_remote_url_requires_allow_remote_opt_in(self):
        ok, lines, base_url = wrapper_api._validate_api_endpoint_policy(
            "minimax",
            {"base_url": "https://api.minimax.io/v1"},
        )
        self.assertFalse(ok)
        self.assertIn("allow_remote = true", "\n".join(lines))
        self.assertEqual(base_url, "https://api.minimax.io/v1")

    def test_remote_url_with_opt_in_is_allowed_but_warns(self):
        ok, lines, base_url = wrapper_api._validate_api_endpoint_policy(
            "minimax",
            {"base_url": "https://api.minimax.io/v1", "allow_remote": True},
        )
        self.assertTrue(ok)
        self.assertIn("WARNING", "\n".join(lines))
        self.assertEqual(base_url, "https://api.minimax.io/v1")


if __name__ == "__main__":
    unittest.main()
