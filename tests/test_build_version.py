import re
import unittest


class BuildVersionTests(unittest.TestCase):
    def test_build_version_is_local_datetime(self):
        import app
        self.assertRegex(app.BUILD_VERSION, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_build_endpoint(self):
        import asyncio
        import app
        self.assertEqual(asyncio.run(app.get_build()), {"build": app.BUILD_VERSION})


if __name__ == "__main__":
    unittest.main()
