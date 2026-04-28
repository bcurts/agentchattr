# Agent Instructions: agentchattr

## Developer Commands

- **Quickstart**: Use scripts in `windows/` or `macos-linux/` (e.g., `sh start_claude.sh`) to auto-setup venv and MCP.
- **Manual Setup**: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
- **Start server**: `python run.py`
- **Start agent wrapper**: `python wrapper.py <agent_name>`
- **Start API agent**: `python wrapper_api.py <agent_name>`
- **Run all tests**: `python -m unittest discover tests`
- **Run single test**: `python -m unittest tests/test_<file>.py`
- **Web UI**: http://localhost:8300 (default)

## Architecture & Key Files

- **Entry Point**: `run.py` starts both the MCP and web server.
- **Web Server**: `app.py` (FastAPI) handles WebSocket communication and REST endpoints.
- **Agent Wrapper**: `wrapper.py` manages agent life cycles and injects prompts into agent terminals.
    - **Windows**: `wrapper_windows.py` uses Win32 API.
    - **Mac/Linux**: `wrapper_unix.py` requires `tmux`.
- **API Agent**: `wrapper_api.py` connects to OpenAI-compatible local models.
- **MCP Layer**: `mcp_bridge.py` (tool definitions) and `mcp_proxy.py` (identity injection).
- **Persistence**: `store.py` (messages), `jobs.py` (tasks), `rules.py` (rules), `summaries.py` (summaries) use JSONL/JSON files in `data/`.
- **Routing**: `router.py` handles @mention parsing and agent targeting.
- **Sessions**: `session_engine.py` and `session_store.py` orchestrate multi-agent workflows using templates in `session_templates/`.

## Configuration

- **`config.toml`**: Primary configuration for agents, ports, and routing (including `max_agent_hops` for loop guard).
- **`config.local.toml`**: Local overrides (gitignored), used for API endpoints and local model settings.
- **Isolation**: Use `AGENTCHATTR_DATA_DIR`, `AGENTCHATTR_PORT`, `AGENTCHATTR_MCP_HTTP_PORT`, and `AGENTCHATTR_MCP_SSE_PORT` env vars to run isolated project instances.

## Conventions & Constraints

- **Python Version**: 3.11+ (required for `tomllib`).
- **Platform dependency**: `tmux` must be installed on macOS/Linux for the agent wrapper to function.
- **Localhost only**: The server is designed for localhost use; use `--allow-network` only on trusted networks.
- **Security**: All API/WS requests must present the session token generated on server start.
- **Loop Guard**: Agent-to-agent chains are paused after `max_agent_hops`; use `/continue` in chat to resume.
