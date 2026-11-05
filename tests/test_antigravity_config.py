"""Antigravity uses a settings-file MCP serverURL, with existing settings preserved."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from wrapper import _antigravity_env, _build_provider_launch, _lock_antigravity_settings, main


def test_antigravity_settings_file_uses_server_url(tmp_path):
    settings = tmp_path / "mcp_config.json"
    settings.write_text(json.dumps({"mcpServers": {"other": {"url": "http://example"}}}), "utf-8")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "other": {"url": "http://ignored"},
        "project": {"url": "http://project"},
        "agentchattr": {"url": "http://ignored"},
    }}), "utf-8")
    config = {
        "command": "agy",
        "mcp_inject": "settings_file",
        "mcp_settings_path": str(settings),
        "mcp_transport": "http",
        "mcp_http_key": "serverURL",
        "mcp_merge_project": False,
    }
    args, _, _, actual_path = _build_provider_launch(
        agent="antigravity", agent_cfg=config, instance_name="antigravity",
        data_dir=tmp_path / "data", proxy_url=None, extra_args=[], env={},
        token="test-token", mcp_cfg={"http_port": 18200}, project_dir=tmp_path,
    )
    servers = json.loads(settings.read_text("utf-8"))["mcpServers"]
    assert actual_path == settings
    assert args == []
    assert servers["other"]["url"] == "http://example"
    assert "project" not in servers
    assert servers["agentchattr"]["serverURL"] == "http://127.0.0.1:18200/mcp"
    assert servers["agentchattr"]["headers"]["Authorization"] == "Bearer test-token"


def test_fixed_settings_never_import_project_tools_between_launches(tmp_path):
    settings = tmp_path / "global.json"
    settings.write_text(json.dumps({"mcpServers": {"global": {"url": "http://global"}}}), "utf-8")
    config = {"mcp_inject": "settings_file", "mcp_settings_path": str(settings),
              "mcp_transport": "http", "mcp_http_key": "serverURL",
              "mcp_merge_project": False}
    for label in ("A", "B"):
        project = tmp_path / label
        project.mkdir()
        (project / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "filesystem": {"url": "http://" + label},
            label + "-only": {"url": "http://" + label},
        }}), "utf-8")
        _build_provider_launch(
            agent="antigravity", agent_cfg=config, instance_name="antigravity",
            data_dir=tmp_path / "data", proxy_url=None, extra_args=[], env={},
            token="token-" + label, mcp_cfg={"http_port": 18200}, project_dir=project,
        )
        servers = json.loads(settings.read_text("utf-8"))["mcpServers"]
        assert set(servers) == {"global", "agentchattr"}
        assert servers["global"]["url"] == "http://global"
        assert servers["agentchattr"]["headers"]["Authorization"] == "Bearer token-" + label


def test_antigravity_lock_excludes_other_process_and_releases_on_exit(tmp_path):
    settings = tmp_path / "mcp_config.json"
    code = (
        "import sys; from pathlib import Path; from wrapper import _lock_antigravity_settings; "
        "lock = _lock_antigravity_settings(Path(sys.argv[1])); "
        "print('ready', flush=True); sys.stdin.read()"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(settings)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(OSError):
            _lock_antigravity_settings(settings)
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
    deadline = time.monotonic() + 3
    while True:
        try:
            lock = _lock_antigravity_settings(settings)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    lock.close()
    assert not settings.exists()


def test_antigravity_environment_pins_updates_and_finds_windows_install(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows install location")
    bindir = tmp_path / "agy" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "agy.exe").write_bytes(b"")
    env = _antigravity_env({"LOCALAPPDATA": str(tmp_path), "PATH": "original"})
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"
    assert env["PATH"].split(os.pathsep)[0] == str(bindir)


def test_registration_failure_releases_settings_lock(tmp_path):
    settings = tmp_path / "mcp_config.json"
    config = {"agents": {"antigravity": {"cwd": str(tmp_path),
              "mcp_settings_path": str(settings)}},
              "server": {"data_dir": str(tmp_path / "data")}}
    with (patch.object(sys, "argv", ["wrapper.py", "antigravity"]),
          patch("config_loader.apply_cli_overrides"),
          patch("config_loader.load_config", return_value=config),
          patch("wrapper._register_instance", side_effect=OSError("unavailable"))):
        with pytest.raises(SystemExit):
            main()
    lock = _lock_antigravity_settings(settings)
    lock.close()
    assert not settings.exists()
