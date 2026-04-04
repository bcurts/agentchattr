import importlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

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
    def test_public_browser_route_policy_is_explicit_and_minimal(self):
        app_module = _fresh_app()

        self.assertTrue(app_module._is_public_browser_route("/", "GET"))
        self.assertTrue(app_module._is_public_browser_route("/static/chat.js", "GET"))
        self.assertTrue(app_module._is_public_browser_route("/uploads/example.png", "GET"))
        self.assertTrue(app_module._is_public_browser_route("/api/roles", "GET"))
        self.assertFalse(app_module._is_public_browser_route("/api/roles/claude", "POST"))
        self.assertFalse(app_module._is_public_browser_route("/api/send", "POST"))

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

    def test_cross_site_browser_request_is_blocked(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        resp = client.post(
            "/api/roles/claude",
            json={"role": "reviewer"},
            headers={
                "X-Session-Token": "test-csrf",
                "Sec-Fetch-Site": "cross-site",
            },
            cookies={"agentchattr_session": "test-session"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("cross-site browser request blocked", resp.text)

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

    def test_uploaded_files_are_served_with_nosniff(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        upload_resp = client.post(
            "/api/upload",
            files={"file": ("note.png", b"not-a-real-png", "image/png")},
            headers={"X-Session-Token": "test-csrf"},
            cookies={"agentchattr_session": "test-session"},
        )
        self.assertEqual(upload_resp.status_code, 200)

        served = client.get(upload_resp.json()["url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers.get("X-Content-Type-Options"), "nosniff")

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

    def test_mutating_routes_do_not_succeed_without_auth(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        def sample_path(path: str) -> str:
            return re.sub(r"\{[^}]+\}", "1", path)

        for route in app_module.app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                if method not in {"POST", "PATCH", "DELETE", "PUT"}:
                    continue
                resp = client.request(method, sample_path(route.path))
                self.assertNotIn(
                    resp.status_code,
                    {200, 201, 202, 204},
                    msg=f"{method} {route.path} unexpectedly succeeded without auth",
                )

    def test_security_headers_are_set_on_public_and_protected_responses(self):
        app_module = _fresh_app()
        client = TestClient(app_module.app)

        public_resp = client.get("/api/roles")
        self.assertEqual(public_resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(public_resp.headers.get("X-Content-Type-Options"), "nosniff")

        protected_resp = client.post("/api/roles/claude", json={"role": "reviewer"})
        self.assertEqual(protected_resp.status_code, 403)
        self.assertEqual(protected_resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(protected_resp.headers.get("X-Content-Type-Options"), "nosniff")


if __name__ == "__main__":
    unittest.main()
