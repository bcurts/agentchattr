#!/usr/bin/env python3
"""Build the Windows desktop launcher as a PyInstaller onedir app."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
APP_DIR = DIST_DIR / "agentchattr"
ICON_SVG = ROOT / "static" / "agentchattr-icon.svg"
ICON_ICO = BUILD_DIR / "agentchattr-icon.ico"

RESOURCE_FILES = [
    "config.toml",
    "VERSION",
    "LICENSE",
]

RESOURCE_DIRS = [
    "static",
    "session_templates",
]

HIDDEN_IMPORTS = [
    "run",
    "wrapper",
    "wrapper_windows",
    "wrapper_unix",
    "wrapper_api",
    "app",
    "launcher_supervisor",
    "launcher_routes",
    "mcp_bridge",
    "mcp_proxy",
    "config_loader",
    "agents",
    "archive",
    "jobs",
    "registry",
    "router",
    "rules",
    "schedules",
    "session_engine",
    "session_store",
    "store",
    "summaries",
    "uvicorn",
    "fastapi",
    "mcp.server.fastmcp",
]


def _make_icon() -> None:
    BUILD_DIR.mkdir(exist_ok=True)
    try:
        from PIL import Image
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QGuiApplication, QIcon
    except Exception as exc:
        raise SystemExit(
            "Icon generation requires Pillow and PySide6. "
            "Install requirements-desktop.txt first."
        ) from exc

    app = QGuiApplication.instance() or QGuiApplication([])
    icon = QIcon(str(ICON_SVG))
    images: list[Image.Image] = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = icon.pixmap(QSize(size, size))
        if pixmap.isNull():
            raise SystemExit(f"Failed to render SVG icon: {ICON_SVG}")
        png_path = BUILD_DIR / f"agentchattr-icon-{size}.png"
        pixmap.save(str(png_path), "PNG")
        images.append(Image.open(png_path).convert("RGBA"))
    images[-1].save(
        ICON_ICO,
        format="ICO",
        sizes=[(image.width, image.height) for image in images],
        append_images=images[:-1],
    )
    app.quit()


def _clean_previous_build() -> None:
    for path in [APP_DIR, ROOT / "agentchattr.spec"]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _copy_runtime_resources() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if ICON_ICO.exists():
        shutil.copy2(ICON_ICO, APP_DIR / "agentchattr-icon.ico")
    for rel in RESOURCE_FILES:
        src = ROOT / rel
        if src.exists():
            shutil.copy2(src, APP_DIR / rel)
    for rel in RESOURCE_DIRS:
        src = ROOT / rel
        dst = APP_DIR / rel
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)


def build() -> Path:
    if os.name != "nt":
        raise SystemExit("Desktop EXE packaging is supported on Windows only.")
    _make_icon()
    _clean_previous_build()

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "agentchattr",
        "--icon",
        str(ICON_ICO),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "pyinstaller"),
        "--specpath",
        str(BUILD_DIR),
    ]
    for module in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", module])
    cmd.append(str(ROOT / "desktop_launcher.py"))

    subprocess.run(cmd, cwd=str(ROOT), check=True)
    _copy_runtime_resources()
    exe = APP_DIR / "agentchattr.exe"
    if not exe.exists():
        raise SystemExit(f"Build did not produce {exe}")
    print(f"Built {exe}")
    return exe


if __name__ == "__main__":
    build()
