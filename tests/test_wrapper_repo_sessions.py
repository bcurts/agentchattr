"""Concurrent per-project wrapper sessions for shared installs (issue #67).

Two opt-in mechanisms, both no-ops when their env vars are unset:

* `AGENTCHATTR_AGENT_<KEY>` overlays one agent's config so a per-project
  launcher does not have to edit the shared config.toml. Whitelisted, because
  a committed project env file must not be able to redirect `command` or
  `mcp_inject`.
* `AGENTCHATTR_REPO_SLUG` qualifies the tmux session name so two repos'
  wrappers stop evicting each other.

Both helpers take their input explicitly rather than reading os.environ, so
these tests need no environment mutation and cannot leak into other suites.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wrapper import (  # noqa: E402
    _apply_agent_env_overrides,
    _build_tmux_session_name,
)


class AgentEnvOverrideTests(unittest.TestCase):
    def test_cwd_is_overridden(self):
        cfg = _apply_agent_env_overrides(
            {"cwd": "."}, {"AGENTCHATTR_AGENT_CWD": "/repos/project-a"})
        self.assertEqual(cfg["cwd"], "/repos/project-a")

    def test_mcp_settings_path_is_overridden(self):
        cfg = _apply_agent_env_overrides(
            {"mcp_settings_path": "~/.config/base.json"},
            {"AGENTCHATTR_AGENT_MCP_SETTINGS_PATH": "/repos/a/mcp.json"})
        self.assertEqual(cfg["mcp_settings_path"], "/repos/a/mcp.json")

    def test_override_applies_to_absent_key(self):
        cfg = _apply_agent_env_overrides(
            {}, {"AGENTCHATTR_AGENT_CWD": "/repos/project-a"})
        self.assertEqual(cfg["cwd"], "/repos/project-a")

    def test_key_suffix_is_matched_case_insensitively(self):
        """The env var is upper-case by convention; the config key is not."""
        cfg = _apply_agent_env_overrides(
            {"cwd": "."}, {"agentchattr_agent_cwd": "/lower"})
        self.assertEqual(cfg["cwd"], ".", "prefix match must stay case-sensitive")
        cfg = _apply_agent_env_overrides(
            {"cwd": "."}, {"AGENTCHATTR_AGENT_CWD": "/upper"})
        self.assertEqual(cfg["cwd"], "/upper")

    def test_empty_value_does_not_override(self):
        cfg = _apply_agent_env_overrides(
            {"cwd": "/keep"}, {"AGENTCHATTR_AGENT_CWD": ""})
        self.assertEqual(cfg["cwd"], "/keep")

    def test_command_cannot_be_overridden(self):
        """The whole point of the whitelist: no arbitrary command injection."""
        cfg = _apply_agent_env_overrides(
            {"command": "claude"}, {"AGENTCHATTR_AGENT_COMMAND": "/bin/evil"})
        self.assertEqual(cfg["command"], "claude")

    def test_mcp_inject_cannot_be_overridden(self):
        cfg = _apply_agent_env_overrides(
            {"mcp_inject": "settings"},
            {"AGENTCHATTR_AGENT_MCP_INJECT": "proxy_file"})
        self.assertEqual(cfg["mcp_inject"], "settings")

    def test_unknown_key_is_ignored(self):
        cfg = _apply_agent_env_overrides(
            {}, {"AGENTCHATTR_AGENT_LABEL": "spoofed"})
        self.assertNotIn("label", cfg)

    def test_unrelated_env_vars_are_ignored(self):
        cfg = _apply_agent_env_overrides(
            {"cwd": "/keep"},
            {"AGENTCHATTR_PORT": "8310", "HOME": "/root", "PATH": "/bin"})
        self.assertEqual(cfg, {"cwd": "/keep"})

    def test_empty_environment_leaves_config_untouched(self):
        cfg = _apply_agent_env_overrides({"cwd": ".", "command": "codex"}, {})
        self.assertEqual(cfg, {"cwd": ".", "command": "codex"})

    def test_updates_the_config_in_place(self):
        """Callers rely on the loaded config seeing the override."""
        original = {"cwd": "."}
        returned = _apply_agent_env_overrides(
            original, {"AGENTCHATTR_AGENT_CWD": "/repos/project-a"})
        self.assertIs(returned, original)
        self.assertEqual(original["cwd"], "/repos/project-a")


class TmuxSessionNameTests(unittest.TestCase):
    def test_slug_qualifies_the_session_name(self):
        self.assertEqual(
            _build_tmux_session_name("codex", "project-a"),
            "agentchattr-project-a-codex")

    def test_empty_slug_preserves_the_original_name(self):
        self.assertEqual(
            _build_tmux_session_name("codex", ""), "agentchattr-codex")

    def test_whitespace_only_slug_preserves_the_original_name(self):
        self.assertEqual(
            _build_tmux_session_name("codex", "   "), "agentchattr-codex")

    def test_none_slug_preserves_the_original_name(self):
        """os.environ.get(..., "") cannot return None, but callers may."""
        self.assertEqual(
            _build_tmux_session_name("codex", None), "agentchattr-codex")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(
            _build_tmux_session_name("codex", "  project-a  "),
            "agentchattr-project-a-codex")

    def test_multi_instance_names_are_kept_distinct(self):
        """Slot suffixes and slugs compose: repo A's codex-2 is not repo B's."""
        self.assertNotEqual(
            _build_tmux_session_name("codex-2", "project-a"),
            _build_tmux_session_name("codex-2", "project-b"))


if __name__ == "__main__":
    unittest.main()
