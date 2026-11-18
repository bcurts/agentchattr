import unittest

import app


class BrowserClientsTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_open_chat_pages(self):
        app.ws_clients.clear()
        self.assertEqual(await app.get_browser_clients(), {"count": 0})
        app.ws_clients.add(object())
        try:
            self.assertEqual(await app.get_browser_clients(), {"count": 1})
        finally:
            app.ws_clients.clear()


if __name__ == "__main__":
    unittest.main()
