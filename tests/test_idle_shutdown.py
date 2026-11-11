import unittest

import idle_shutdown
from idle_shutdown import IdleShutdown


class IdleShutdownTests(unittest.TestCase):
    def test_never_fires_before_a_browser_has_connected(self):
        idle = IdleShutdown(30)
        self.assertFalse(idle.check(browsers=0, agents=0, now=0))
        self.assertFalse(idle.check(browsers=0, agents=0, now=10_000))

    def test_fires_after_grace_once_browser_closed_and_no_agents(self):
        idle = IdleShutdown(30)
        idle.check(browsers=1, agents=0, now=0)
        self.assertFalse(idle.check(browsers=0, agents=0, now=100))
        self.assertFalse(idle.check(browsers=0, agents=0, now=129))
        self.assertTrue(idle.check(browsers=0, agents=0, now=130))

    def test_connected_agent_keeps_server_up(self):
        idle = IdleShutdown(30)
        idle.check(browsers=1, agents=1, now=0)
        self.assertFalse(idle.check(browsers=0, agents=1, now=1_000))
        # Agent leaves later: grace starts from then.
        self.assertFalse(idle.check(browsers=0, agents=0, now=1_010))
        self.assertTrue(idle.check(browsers=0, agents=0, now=1_040))

    def test_refresh_within_grace_resets_timer(self):
        idle = IdleShutdown(30)
        idle.check(browsers=1, agents=0, now=0)
        idle.check(browsers=0, agents=0, now=10)
        idle.check(browsers=1, agents=0, now=15)
        self.assertFalse(idle.check(browsers=0, agents=0, now=40))
        self.assertTrue(idle.check(browsers=0, agents=0, now=70))

    def test_config_toggle_and_grace(self):
        self.assertIsNone(idle_shutdown.from_config({"server": {"auto_shutdown": False}}))
        self.assertEqual(idle_shutdown.from_config({}).grace_seconds, 30)
        self.assertEqual(
            idle_shutdown.from_config({"server": {"idle_shutdown_seconds": 5}}).grace_seconds, 5)


if __name__ == "__main__":
    unittest.main()
