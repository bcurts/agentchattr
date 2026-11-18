"""Local-vs-LAN client rules used when server.allow_lan is on."""
import app


def test_local_clients(monkeypatch):
    monkeypatch.setattr(app, "_local_ips", lambda: frozenset({"127.0.0.1", "::1", "192.168.1.181"}))
    for ip in ("127.0.0.1", "127.0.0.5", "::1", "::ffff:127.0.0.1", "192.168.1.181", "localhost"):
        assert app.is_local_client(ip), ip


def test_remote_clients_refused(monkeypatch):
    monkeypatch.setattr(app, "_local_ips", lambda: frozenset({"127.0.0.1", "::1", "192.168.1.181"}))
    for ip in ("192.168.1.50", "10.0.0.5", "8.8.8.8", "", None):
        assert not app.is_local_client(ip), ip


def test_origin_must_be_own_host_and_port(monkeypatch):
    monkeypatch.setattr(app, "_local_ips", lambda: frozenset({"127.0.0.1", "::1", "192.168.1.181"}))
    ok = ("http://localhost:8300", "http://127.0.0.1:8300", "http://192.168.1.181:8300")
    bad = ("http://192.168.1.50:8300", "http://evil.com:8300", "http://localhost:9999", "https://localhost:8300", "null", "")
    for o in ok:
        assert app._origin_allowed(o, 8300), o
    for o in bad:
        assert not app._origin_allowed(o, 8300), o
