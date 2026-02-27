import pytest
from unittest.mock import MagicMock, patch
from tmux_cleanup import SessionCleanup


@pytest.fixture
def mock_config():
    return {
        "agents": {
            "claude": {"label": "Claude"},
            "codex": {"label": "Codex"}
        },
        "cleanup": {
            "enabled": True,
            "idle_timeout_minutes": 1,
            "check_interval_seconds": 1
        }
    }


def test_cleanup_kills_stale_session(mock_config):
    mock_store = MagicMock()
    cleanup = SessionCleanup(mock_config, store=mock_store)
    
    # Mock current time
    now = 1000.0
    
    with patch("tmux_cleanup.get_tmux_sessions", return_value=["agentchattr-claude"]), \
         patch("tmux_cleanup.kill_tmux_session") as mock_kill, \
         patch("mcp_bridge.is_online", return_value=False), \
         patch("time.time", return_value=now):
        
        # First check: initializes last_online for the session
        cleanup._check_sessions()
        assert cleanup._last_online["claude"] == now
        mock_kill.assert_not_called()
        
        # Second check: after timeout
        timeout = mock_config["cleanup"]["idle_timeout_minutes"] * 60
        later = now + timeout + 1
        with patch("time.time", return_value=later):
            cleanup._check_sessions()
            mock_kill.assert_called_once_with("agentchattr-claude")
            mock_store.add.assert_called_once()
            assert "Cleaned up stale tmux session for Claude" in mock_store.add.call_args[0][1]


def test_cleanup_skips_online_session(mock_config):
    mock_store = MagicMock()
    cleanup = SessionCleanup(mock_config, store=mock_store)
    
    now = 1000.0
    with patch("tmux_cleanup.get_tmux_sessions", return_value=["agentchattr-claude"]), \
         patch("tmux_cleanup.kill_tmux_session") as mock_kill, \
         patch("mcp_bridge.is_online", return_value=True), \
         patch("time.time", return_value=now):
        
        # Initialize
        cleanup._check_sessions()
        
        # After timeout, but still online
        timeout = mock_config["cleanup"]["idle_timeout_minutes"] * 60
        later = now + timeout + 1
        with patch("time.time", return_value=later):
            cleanup._check_sessions()
            mock_kill.assert_not_called()
            assert cleanup._last_online["claude"] == later
