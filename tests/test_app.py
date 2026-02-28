"""Tests for TODO parsing, task endpoint, and access-token helpers."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
from app import _access_token_valid, _load_access_token, _parse_todo_tasks, add_reaction, get_tasks  # noqa: E402


def test_parse_todo_tasks_extracts_backlog_items():
    todo_text = (ROOT / "TODO.md").read_text("utf-8")

    tasks = _parse_todo_tasks(todo_text)

    assert tasks, "Expected TODO backlog tasks to be parsed"
    assert tasks[0]["title"] == "Merge PR #1 — fix/gemini-input-stacking"
    assert tasks[0]["status"] == "Done"
    assert tasks[0]["owner"] == ""


def test_parse_todo_tasks_normalizes_status_owner_and_branch():
    todo_text = """
# TODO

## Backlog

### Example Task
- **Owner:** Review - codex (implemented by gemini-cli, PR open)
- **Branch:** feature/example-task

### Planning Task
- **Owner:** Pending (needs design discussion first)
- **Branch:** feature/planning-task
"""

    tasks = _parse_todo_tasks(todo_text)

    assert tasks == [
        {
            "title": "Example Task",
            "owner": "codex",
            "status": "Review",
            "branch": "feature/example-task",
        },
        {
            "title": "Planning Task",
            "owner": "",
            "status": "Pending",
            "branch": "feature/planning-task",
        },
    ]


def test_get_tasks_returns_parsed_json_from_todo_file(tmp_path):
    todo_path = tmp_path / "TODO.md"
    todo_path.write_text(
        "## Backlog\n\n"
        "### Kanban task sidebar\n"
        "- **Owner:** In Progress - codex\n"
        "- **Branch:** feature/kanban-sidebar\n",
        "utf-8",
    )

    with patch("app._todo_path", return_value=todo_path):
        tasks = asyncio.run(get_tasks())

    assert tasks == [
        {
            "title": "Kanban task sidebar",
            "owner": "codex",
            "status": "In Progress",
            "branch": "feature/kanban-sidebar",
        }
    ]


def test_get_tasks_returns_empty_list_when_todo_missing(tmp_path):
    with patch("app._todo_path", return_value=tmp_path / "TODO.md"):
        tasks = asyncio.run(get_tasks())

    assert tasks == []


def test_load_access_token_reads_environment():
    with patch.dict(os.environ, {"ACCESS_TOKEN": "secret-token"}, clear=False):
        assert _load_access_token() == "secret-token"


def test_access_token_allows_all_when_unset():
    assert _access_token_valid("", "", "")


def test_access_token_accepts_query_param():
    assert _access_token_valid("secret-token", query_token="secret-token")


def test_access_token_accepts_header():
    assert _access_token_valid("secret-token", header_token="secret-token")


def test_access_token_rejects_missing_or_wrong_tokens():
    assert not _access_token_valid("secret-token")
    assert not _access_token_valid("secret-token", query_token="wrong")
    assert not _access_token_valid("secret-token", header_token="wrong")


def test_add_reaction_toggles_and_returns_payload():
    fake_store = MagicMock()
    fake_store.toggle_reaction.return_value = {"👍": ["alice"]}

    with patch.object(app, "store", fake_store), \
         patch.object(app, "broadcast_reaction_update", new=AsyncMock()) as mock_broadcast:
        response = asyncio.run(add_reaction({"message_id": 3, "emoji": "👍", "sender": "alice"}))

    assert response.status_code == 200
    assert json.loads(response.body) == {"message_id": 3, "reactions": {"👍": ["alice"]}}
    fake_store.toggle_reaction.assert_called_once_with(3, "👍", "alice")
    mock_broadcast.assert_awaited_once_with(3, {"👍": ["alice"]})


def test_add_reaction_rejects_invalid_payload():
    response = asyncio.run(add_reaction({"message_id": "bad", "emoji": "", "sender": ""}))
    assert response.status_code == 400


def test_add_reaction_returns_404_when_message_missing():
    fake_store = MagicMock()
    fake_store.toggle_reaction.return_value = None

    with patch.object(app, "store", fake_store), \
         patch.object(app, "broadcast_reaction_update", new=AsyncMock()) as mock_broadcast:
        response = asyncio.run(add_reaction({"message_id": 99, "emoji": "👍", "sender": "alice"}))

    assert response.status_code == 404
    mock_broadcast.assert_not_awaited()
