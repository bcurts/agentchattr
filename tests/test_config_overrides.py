"""Tests for AGENTCHATTR_* env var overrides in config_loader.

These tests exercise load_config() directly (not through run.py) because
wrappers also call load_config(), and the core guarantee is that the
same env vars produce the same config regardless of entry point.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader  # noqa: E402


ENV_VARS = [
    "AGENTCHATTR_DATA_DIR",
    "AGENTCHATTR_PORT",
    "AGENTCHATTR_MCP_HTTP_PORT",
    "AGENTCHATTR_MCP_SSE_PORT",
    "AGENTCHATTR_UPLOAD_DIR",
]


class ConfigOverrideTests(unittest.TestCase):
    def setUp(self):
        # Snapshot and clear all override env vars so tests don't interfere.
        self._saved = {k: os.environ.get(k) for k in ENV_VARS}
        for k in ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_env_vars_uses_config_toml_values(self):
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["server"]["port"], 8300)
        self.assertEqual(config["server"]["data_dir"], "./data")

    def test_port_env_var_overrides_config(self):
        os.environ["AGENTCHATTR_PORT"] = "8310"
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["server"]["port"], 8310)

    def test_mcp_ports_env_vars_override_config(self):
        os.environ["AGENTCHATTR_MCP_HTTP_PORT"] = "8210"
        os.environ["AGENTCHATTR_MCP_SSE_PORT"] = "8211"
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["mcp"]["http_port"], 8210)
        self.assertEqual(config["mcp"]["sse_port"], 8211)

    def test_data_dir_absolute_path_preserved(self):
        abs_path = str(Path("/tmp/test-agentchattr").resolve())
        os.environ["AGENTCHATTR_DATA_DIR"] = abs_path
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["server"]["data_dir"], abs_path)

    def test_data_dir_relative_path_resolves_to_cwd(self):
        # Relative path should resolve against CWD, not agentchattr install
        os.environ["AGENTCHATTR_DATA_DIR"] = "./my-project-data"
        config = config_loader.load_config(ROOT)
        expected = str((Path.cwd() / "my-project-data").resolve())
        self.assertEqual(config["server"]["data_dir"], expected)

    def test_upload_dir_relative_path_resolves_to_cwd(self):
        os.environ["AGENTCHATTR_UPLOAD_DIR"] = "./my-uploads"
        config = config_loader.load_config(ROOT)
        expected = str((Path.cwd() / "my-uploads").resolve())
        self.assertEqual(config["images"]["upload_dir"], expected)

    def test_empty_env_var_does_not_override(self):
        os.environ["AGENTCHATTR_PORT"] = ""
        config = config_loader.load_config(ROOT)
        # Empty value is ignored, default stays
        self.assertEqual(config["server"]["port"], 8300)

    def test_invalid_int_env_var_warns_and_keeps_default(self):
        os.environ["AGENTCHATTR_PORT"] = "not-a-number"
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["server"]["port"], 8300)

    def test_all_overrides_applied_together(self):
        abs_data = str(Path("/tmp/proj-a/.agentchattr").resolve())
        abs_uploads = str(Path("/tmp/proj-a/uploads").resolve())
        os.environ["AGENTCHATTR_DATA_DIR"] = abs_data
        os.environ["AGENTCHATTR_PORT"] = "8310"
        os.environ["AGENTCHATTR_MCP_HTTP_PORT"] = "8210"
        os.environ["AGENTCHATTR_MCP_SSE_PORT"] = "8211"
        os.environ["AGENTCHATTR_UPLOAD_DIR"] = abs_uploads
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["server"]["data_dir"], abs_data)
        self.assertEqual(config["server"]["port"], 8310)
        self.assertEqual(config["mcp"]["http_port"], 8210)
        self.assertEqual(config["mcp"]["sse_port"], 8211)
        self.assertEqual(config["images"]["upload_dir"], abs_uploads)

    def test_agents_section_unchanged_by_overrides(self):
        os.environ["AGENTCHATTR_PORT"] = "8310"
        config = config_loader.load_config(ROOT)
        # Agent definitions must be untouched by path/port overrides
        self.assertIn("claude", config["agents"])
        self.assertEqual(config["agents"]["claude"]["command"], "claude")


class CliOverrideExtractionTests(unittest.TestCase):
    """apply_cli_overrides() extracts CLI flags into env vars.

    This is the shared helper used by run.py, wrapper.py, and wrapper_api.py
    so the same flags produce the same config regardless of entry point.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ENV_VARS}
        for k in ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_space_separated_flags_set_env_vars(self):
        argv = ["run.py", "--port", "8310", "--data-dir", "./foo"]
        config_loader.apply_cli_overrides(argv)
        self.assertEqual(os.environ["AGENTCHATTR_PORT"], "8310")
        self.assertEqual(os.environ["AGENTCHATTR_DATA_DIR"], "./foo")

    def test_equals_form_flags_set_env_vars(self):
        argv = ["run.py", "--port=8310", "--data-dir=./foo"]
        config_loader.apply_cli_overrides(argv)
        self.assertEqual(os.environ["AGENTCHATTR_PORT"], "8310")
        self.assertEqual(os.environ["AGENTCHATTR_DATA_DIR"], "./foo")

    def test_missing_flags_do_not_touch_env(self):
        argv = ["run.py"]
        config_loader.apply_cli_overrides(argv)
        for env in ENV_VARS:
            self.assertNotIn(env, os.environ)

    def test_overrides_flow_through_to_load_config(self):
        argv = ["run.py", "--port", "8315", "--mcp-http-port", "8215"]
        config_loader.apply_cli_overrides(argv)
        config = config_loader.load_config(ROOT)
        self.assertEqual(config["server"]["port"], 8315)
        self.assertEqual(config["mcp"]["http_port"], 8215)

    def test_all_five_flags_extracted(self):
        argv = [
            "run.py",
            "--data-dir", "/tmp/proj",
            "--port", "8310",
            "--mcp-http-port", "8210",
            "--mcp-sse-port", "8211",
            "--upload-dir", "/tmp/proj-uploads",
        ]
        config_loader.apply_cli_overrides(argv)
        self.assertEqual(os.environ["AGENTCHATTR_DATA_DIR"], "/tmp/proj")
        self.assertEqual(os.environ["AGENTCHATTR_PORT"], "8310")
        self.assertEqual(os.environ["AGENTCHATTR_MCP_HTTP_PORT"], "8210")
        self.assertEqual(os.environ["AGENTCHATTR_MCP_SSE_PORT"], "8211")
        self.assertEqual(os.environ["AGENTCHATTR_UPLOAD_DIR"], "/tmp/proj-uploads")

    def test_pass_through_separator_ignores_later_flags(self):
        # `-- --port 9999` belongs to the agent CLI, not agentchattr.
        # Flags AFTER `--` must NOT leak into the env.
        argv = [
            "wrapper.py", "claude",
            "--port", "8310",
            "--",
            "--port", "9999",
            "--data-dir", "/agent-arg",
        ]
        config_loader.apply_cli_overrides(argv)
        self.assertEqual(os.environ["AGENTCHATTR_PORT"], "8310")
        self.assertNotIn("AGENTCHATTR_DATA_DIR", os.environ)

    def test_pass_through_alone_ignores_everything(self):
        # If agentchattr flags appear ONLY after `--`, none are applied.
        argv = [
            "wrapper.py", "claude",
            "--",
            "--port", "9999",
            "--data-dir", "/agent-arg",
        ]
        config_loader.apply_cli_overrides(argv)
        self.assertNotIn("AGENTCHATTR_PORT", os.environ)
        self.assertNotIn("AGENTCHATTR_DATA_DIR", os.environ)


if __name__ == "__main__":
    unittest.main()
