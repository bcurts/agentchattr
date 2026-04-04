import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_app():
    import app as app_module

    app_module = importlib.reload(app_module)
    cfg = {
        "server": {"data_dir": tempfile.mkdtemp(prefix="agentchattr-security-")},
        "images": {"upload_dir": tempfile.mkdtemp(prefix="agentchattr-uploads-")},
        "agents": {
            "claude": {
                "command": "claude",
                "cwd": "..",
                "color": "#da7756",
                "label": "Claude",
            }
        },
        "routing": {"default": "none", "max_agent_hops": 4},
    }
    app_module.configure(cfg, session_token="test-session", csrf_token="test-csrf")
    return app_module


class SecurityMiddlewareTests(unittest.TestCase):
    def test_roles_list_is_public_but_role_mutation_requires_session_token(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        read_resp = client.get("/api/roles")
        self.assertEqual(read_resp.status_code, 200)

        write_resp = client.post("/api/roles/claude", json={"role": "reviewer"})
        self.assertEqual(write_resp.status_code, 403)
        self.assertIn("invalid or missing session cookie", write_resp.text)

        authed_resp = client.post(
            "/api/roles/claude",
            json={"role": "reviewer"},
            headers={"X-Session-Token": "test-csrf"},
            cookies={"agentchattr_session": "test-session"},
        )
        self.assertEqual(authed_resp.status_code, 200)
        self.assertEqual(authed_resp.json()["role"], "reviewer")

    def test_write_routes_require_csrf_even_with_valid_session_cookie(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        resp = client.post(
            "/api/roles/claude",
            json={"role": "reviewer"},
            cookies={"agentchattr_session": "test-session"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("invalid or missing csrf token", resp.text)

    def test_svg_upload_is_rejected(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        resp = client.post(
            "/api/upload",
            files={"file": ("hat.svg", b"<svg></svg>", "image/svg+xml")},
            headers={"X-Session-Token": "test-csrf"},
            cookies={"agentchattr_session": "test-session"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unsupported file type", resp.text)

    def test_open_path_is_restricted_to_known_roots(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        resp = client.post(
            "/api/open-path",
            json={"path": "/etc/passwd"},
            headers={"X-Session-Token": "test-csrf"},
            cookies={"agentchattr_session": "test-session"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("outside allowed roots", resp.text)

    def test_websocket_requires_session_cookie(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        with client.websocket_connect("/ws") as ws:
            with self.assertRaises(Exception):
                ws.receive_text()

        client.cookies.set("agentchattr_session", "test-session")
        with client.websocket_connect("/ws") as ws:
            first = ws.receive_json()
            self.assertEqual(first["type"], "settings")


if __name__ == "__main__":
    unittest.main()
