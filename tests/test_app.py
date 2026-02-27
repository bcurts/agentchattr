"""Tests for TODO parsing and task endpoint."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app import _parse_todo_tasks, get_tasks  # noqa: E402


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
