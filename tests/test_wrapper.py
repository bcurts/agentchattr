"""Tests for wrapper.py — cooldown selection and queue watcher logic."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from wrapper import DEFAULT_TRIGGER_COOLDOWN_SECONDS, _trigger_cooldown_seconds  # noqa: E402


# ---------------------------------------------------------------------------
# Cooldown selection
# ---------------------------------------------------------------------------

def test_default_cooldown_when_no_config():
    assert _trigger_cooldown_seconds("claude", {}) == DEFAULT_TRIGGER_COOLDOWN_SECONDS


def test_custom_cooldown_from_config():
    assert _trigger_cooldown_seconds("gemini", {"trigger_cooldown": 10.0}) == 10.0


def test_cooldown_accepts_int_value():
    assert _trigger_cooldown_seconds("codex", {"trigger_cooldown": 5}) == 5.0


def test_cooldown_falls_back_when_key_missing():
    assert _trigger_cooldown_seconds("gemini", {"color": "#4285f4"}) == DEFAULT_TRIGGER_COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# inject() call ordering (wrapper_unix)
# ---------------------------------------------------------------------------

def test_inject_sends_escape_before_text():
    """Escape must be sent before the literal text to clear pending TUI input."""
    with patch("subprocess.run") as mock_run:
        from wrapper_unix import inject
        inject("chat - use mcp", tmux_session="test-session")

    calls = mock_run.call_args_list
    assert len(calls) == 4, f"Expected 4 subprocess calls (C-u, Escape, text, Enter), got {len(calls)}"

    # First call: C-u (line clear)
    first_cmd = calls[0][0][0]
    assert "C-u" in first_cmd, f"First call should send C-u, got: {first_cmd}"

    # Second call: Escape
    second_cmd = calls[1][0][0]
    assert "Escape" in second_cmd, f"Second call should send Escape, got: {second_cmd}"

    # Third call: literal text
    third_cmd = calls[2][0][0]
    assert "-l" in third_cmd, f"Third call should use -l flag for literal text, got: {third_cmd}"
    assert "chat - use mcp" in third_cmd

    # Fourth call: Enter
    fourth_cmd = calls[3][0][0]
    assert "Enter" in fourth_cmd, f"Fourth call should send Enter, got: {fourth_cmd}"


def test_inject_uses_correct_session_name():
    """All tmux calls must target the specified session."""
    with patch("subprocess.run") as mock_run:
        from wrapper_unix import inject
        inject("hello", tmux_session="agentchattr-gemini")

    for c in mock_run.call_args_list:
        cmd = c[0][0]
        assert "agentchattr-gemini" in cmd, f"Expected session name in call: {cmd}"


# ---------------------------------------------------------------------------
# Queue watcher — trigger collapsing and malformed JSON handling
# ---------------------------------------------------------------------------

def test_queue_watcher_ignores_malformed_json(tmp_path):
    """Malformed JSON lines in the queue file should be skipped silently."""
    from wrapper import _queue_watcher

    queue_file = tmp_path / "test_queue.jsonl"
    inject_fn = MagicMock()

    # Write one valid + one invalid line
    queue_file.write_text(
        'not valid json\n'
        '{"sender": "user", "text": "hello", "time": "00:00:00"}\n'
    )

    # Run one iteration by patching time.sleep to raise after first pass
    call_count = 0

    def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise SystemExit

    with patch("time.sleep", side_effect=fake_sleep):
        with pytest.raises(SystemExit):
            _queue_watcher(queue_file, "claude", inject_fn, {})

    # Valid line should still trigger an inject
    inject_fn.assert_called_once_with("chat - use mcp")


def test_queue_watcher_drains_queue_after_trigger(tmp_path):
    """Queue file should be cleared after processing triggers."""
    from wrapper import _queue_watcher

    queue_file = tmp_path / "test_queue.jsonl"
    inject_fn = MagicMock()

    queue_file.write_text('{"sender": "user", "text": "ping", "time": "00:00:00"}\n')

    call_count = 0

    def fake_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise SystemExit

    with patch("time.sleep", side_effect=fake_sleep):
        with pytest.raises(SystemExit):
            _queue_watcher(queue_file, "claude", inject_fn, {})

    assert queue_file.read_text() == "", "Queue file should be empty after processing"


# ---------------------------------------------------------------------------
# run_agent command construction
# ---------------------------------------------------------------------------

def test_run_agent_constructs_resume_command():
    """Verify that extra_args are properly formatted into the agent_cmd."""
    with patch("subprocess.run") as mock_run, \
         patch("shutil.which", return_value="/usr/bin/tmux"):
        from wrapper_unix import run_agent
        
        def fake_start_watcher(inject_fn):
            pass

        # mock_run side effects for the loop: kill-session, new-session, attach-session
        mock_run.side_effect = [
            MagicMock(returncode=0), # tmux kill-session
            MagicMock(returncode=0), # tmux new-session
            KeyboardInterrupt(),     # tmux attach-session (simulating user Ctrl+C)
            MagicMock(returncode=0), # tmux kill-session (in except block)
        ]
        
        run_agent(
            command="gemini",
            extra_args=["--approval-mode", "yolo", "--resume"],
            cwd=".",
            env={},
            queue_file="dummy.jsonl",
            agent="gemini",
            no_restart=True,
            start_watcher=fake_start_watcher
        )

    # Find the new-session call
    new_session_call = next(c for c in mock_run.call_args_list if "new-session" in c[0][0])
    cmd_args = new_session_call[0][0]
    
    # The agent_cmd is the last argument passed to tmux new-session -c <cwd> <cmd>
    agent_cmd = cmd_args[-1]
    
    assert "gemini" in agent_cmd
    assert "--resume" in agent_cmd
    assert "--approval-mode yolo" in agent_cmd


# ---------------------------------------------------------------------------
# Startup queue flushing
# ---------------------------------------------------------------------------

def test_wrapper_main_flushes_queue_file_on_startup(tmp_path):
    """Ensure leftover trigger files from previous runs are deleted when wrapper starts."""
    from wrapper import main

    # Setup dummy data dir and queue file
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    queue_file = data_dir / "claude_queue.jsonl"
    queue_file.write_text("stale_data\n")

    # Write a dummy config.toml
    config_file = tmp_path / "config.toml"
    config_file.write_text('[agents.claude]\ncommand = "claude"\n[server]\ndata_dir = "data"\n')

    with patch("wrapper.ROOT", tmp_path), \
         patch("sys.argv", ["wrapper.py", "claude"]), \
         patch("shutil.which", return_value="/bin/claude"), \
         patch("wrapper.threading.Thread"), \
         patch("sys.platform", "linux"), \
         patch("wrapper_unix.run_agent"):

        main()

    # Check if queue file was cleared
    assert queue_file.read_text() == "", "Queue file should be flushed on wrapper startup"


# ---------------------------------------------------------------------------
# Windows inject — Escape-before-inject parity
# ---------------------------------------------------------------------------

def test_inject_windows_sends_escape_before_text():
    """Verify windows inject path also sends Escape first."""
    # We must patch sys.platform and WinDLL before importing wrapper_windows
    with patch("sys.platform", "win32"), \
         patch("ctypes.WinDLL", create=True), \
         patch("ctypes.byref"):

        # In case it was already imported/failed
        if "wrapper_windows" in sys.modules:
            del sys.modules["wrapper_windows"]

        import wrapper_windows

        with patch("wrapper_windows._write_key") as mock_write:
            wrapper_windows.inject("test")

            calls = mock_write.call_args_list
            # First two calls to _write_key are for Escape (down/up)
            assert calls[0][0][1] == "\x1b"
            assert calls[0][0][2] is True  # key_down
            assert calls[1][0][1] == "\x1b"
            assert calls[1][0][2] is False  # key_up

            # Check that "test" follows
            assert calls[2][0][1] == "t"

