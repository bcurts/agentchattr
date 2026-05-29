"""Shared fixtures for the browser-based responsive layout tests.

Launches a real agentchattr web server on isolated ports (so it never
collides with a live instance) and exposes its base URL to the tests.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def server():
    """Start the web UI on a throwaway port + data dir, yield its base URL."""
    web_port = _free_port()
    http_port = _free_port()
    sse_port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="agentchattr-test-")

    env = dict(os.environ)
    proc = subprocess.Popen(
        [
            sys.executable, "run.py",
            "--port", str(web_port),
            "--mcp-http-port", str(http_port),
            "--mcp-sse-port", str(sse_port),
            "--data-dir", data_dir,
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{web_port}"
    deadline = time.time() + 30
    last_err = None
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            raise RuntimeError(f"server exited early (code {proc.returncode}):\n{out}")
        try:
            with urllib.request.urlopen(base_url, timeout=1) as r:
                if r.status == 200:
                    break
        except Exception as e:  # noqa: BLE001 - polling until ready
            last_err = e
            time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError(f"server did not become ready: {last_err}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# Viewport presets used across the responsive suite.
MOBILE = {"width": 375, "height": 812}    # iPhone-class portrait
TABLET = {"width": 768, "height": 1024}   # iPad-class portrait
DESKTOP = {"width": 1280, "height": 900}  # laptop
