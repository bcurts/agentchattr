#!/usr/bin/env python3
"""Build a clean release zip for Mehub.

Packages only user-facing files — no .git, no dev files, no logs, no caches.
Output: mehub-{version}.zip in the repo root.
"""

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
VERSION = (ROOT / "VERSION").read_text().strip()
OUT_NAME = f"mehub-{VERSION}"

# Files and dirs to include (relative to repo root)
INCLUDE_FILES = [
    "app.py",
    "agents.py",
    "config_loader.py",
    "jobs.py",
    "mcp_bridge.py",
    "mcp_proxy.py",
    "registry.py",
    "router.py",
    "rules.py",
    "run.py",
    "session_engine.py",
    "session_store.py",
    "store.py",
    "schedules.py",
    "summaries.py",
    "wrapper.py",
    "wrapper_api.py",
    "wrapper_unix.py",
    "wrapper_windows.py",
    "open_chat.html",
    "config.toml",
    "config.local.toml.example",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "VERSION",
    "screenshot.png",
    "gang.gif",
]

INCLUDE_DIRS = [
    "static",
    "windows",
    "macos-linux",
    "session_templates",
]


def build():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / OUT_NAME
        dest.mkdir()

        for f in INCLUDE_FILES:
            src = ROOT / f
            if src.exists():
                shutil.copy2(src, dest / f)

        for d in INCLUDE_DIRS:
            src = ROOT / d
            if src.exists():
                shutil.copytree(src, dest / d)

        out_path = ROOT / OUT_NAME
        shutil.make_archive(str(out_path), "zip", tmp, OUT_NAME)

    print(f"Built {out_path}.zip")
    return f"{out_path}.zip"


if __name__ == "__main__":
    build()
