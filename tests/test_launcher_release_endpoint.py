import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module
from registry import RuntimeRegistry


class FakeRequest:
    client = SimpleNamespace(host="127.0.0.1")
    headers = {}
    query_params = {}


class LauncherReleaseEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_registry = app_module.registry
        self.old_token = app_module.launcher_shutdown_token
        self.registry = RuntimeRegistry(tempfile.mkdtemp(prefix="agentchattr-release-test-"))
        self.registry.seed({"kimi": {"label": "Kimi", "color": "#111111"}})
        app_module.registry = self.registry
        app_module.launcher_shutdown_token = ""

    async def asyncTearDown(self):
        app_module.registry = self.old_registry
        app_module.launcher_shutdown_token = self.old_token

    async def test_release_agent_identity_is_idempotent(self):
        self.registry.register("kimi")

        response = await app_module.launcher_release_agent("kimi", FakeRequest())
        second = await app_module.launcher_release_agent("kimi", FakeRequest())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIsNone(self.registry.get_instance("kimi"))


if __name__ == "__main__":
    unittest.main()
