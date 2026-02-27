"""Tests for wrapper.py — cooldown selection and queue watcher logic."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from wrapper import DEFAULT_TRIGGER_COOLDOWN_SECONDS, _trigger_cooldown_seconds


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
    assert len(calls) == 3, f"Expected 3 subprocess calls, got {len(calls)}"

    # First call: Escape
    first_cmd = calls[0][0][0]
    assert "Escape" in first_cmd, f"First call should send Escape, got: {first_cmd}"

    # Second call: literal text
    second_cmd = calls[1][0][0]
    assert "-l" in second_cmd, f"Second call should use -l flag for literal text, got: {second_cmd}"
    assert "chat - use mcp" in second_cmd

    # Third call: Enter
    third_cmd = calls[2][0][0]
    assert "Enter" in third_cmd, f"Third call should send Enter, got: {third_cmd}"


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
