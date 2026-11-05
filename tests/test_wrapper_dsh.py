"""Message-layer tests for the DSH chat adapter (wrapper_dsh.py).

Covered behaviours come straight from the development plan:

  * @-wake routing: a queued trigger becomes a prompt on the right scope
  * per-channel / per-task session isolation and reuse
  * busy state + FIFO queueing while one turn runs
  * multi-instance identity isolation (session ids and names never collide)
  * reply published only through MCP (the adapter never forwards final text)
  * an interrupted task is reported, never silently replayed

These tests import the adapter module and drive its pure/queue logic without
starting the real DSH runtime.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wrapper_dsh  # noqa: E402
from wrapper_dsh import (  # noqa: E402
    DshInstance,
    build_trigger_prompt,
    read_triggers,
    trigger_scope,
)


class FakeClient:
    """Stands in for DshClient so tests never spawn a runtime."""

    def __init__(self) -> None:
        self.prompts: list[tuple[str, list[dict]]] = []
        self._running: set[str] = set()

    def session_prompt(self, session_id: str, blocks: list[dict], **kwargs) -> str:
        self.prompts.append((session_id, blocks))
        return f"msg-{len(self.prompts)}"

    def running_sessions(self) -> set[str]:
        return set(self._running)


def make_instance(tmp_path: Path, name: str = "DSH-01") -> DshInstance:
    return DshInstance(
        name=name,
        token="tok",
        queue_file=tmp_path / f"{name}_queue.jsonl",
        client=FakeClient(),
        log_fn=lambda _msg: None,
        channel_sessions_path=tmp_path / f"{name}-sessions.json",
    )


# --------------------------------------------------------------------------
# Trigger scope / prompt assembly
# --------------------------------------------------------------------------

def test_trigger_scope_prefers_job_over_channel():
    assert trigger_scope({"job_id": 42, "channel": "general"}) == "job-42"
    assert trigger_scope({"channel": "general"}) == "channel-general"


def test_trigger_scope_defaults_to_general():
    assert trigger_scope({}) == "channel-general"


def test_session_id_is_stable_per_scope(tmp_path: Path):
    inst = make_instance(tmp_path)
    first = inst.scope_session_id("channel-general")
    second = inst.scope_session_id("channel-general")
    assert first == second
    assert first.startswith("channel-general-")


def test_sessions_are_isolated_per_scope(tmp_path: Path):
    inst = make_instance(tmp_path)
    channel = inst.scope_session_id("channel-general")
    job = inst.scope_session_id("job-7")
    assert channel != job


def test_sessions_do_not_collide_across_instances(tmp_path: Path):
    """Two instances must never share a session id for the same scope."""
    a = make_instance(tmp_path, "DSH-01")
    b = make_instance(tmp_path, "DSH-02")
    assert a.scope_session_id("channel-general") != b.scope_session_id("channel-general")


def test_sessions_are_not_reused_after_restart(tmp_path: Path):
    inst = make_instance(tmp_path, "DSH-09")
    sid = inst.scope_session_id("channel-general")
    reloaded = make_instance(tmp_path, "DSH-09")
    assert reloaded.scope_session_id("channel-general") != sid


def test_new_session_created_after_restart_without_state(tmp_path: Path):
    """A fresh process (no sessions file) issues a brand-new session id."""
    inst = make_instance(tmp_path, "DSH-11")
    sid = inst.scope_session_id("channel-general")
    (tmp_path / "DSH-11-sessions.json").unlink()
    fresh = make_instance(tmp_path, "DSH-11")
    assert fresh.scope_session_id("channel-general") != sid


def test_prompt_mentions_chat_tools_and_target_channel():
    prompt = build_trigger_prompt(
        channel="general",
        job_id=None,
        role="reviewer",
        rules=["keep replies short"],
        first_mention=True,
    )
    assert "general" in prompt
    assert "mcp__agentchattr__chat_send" in prompt
    assert "reviewer" in prompt
    assert "keep replies short" in prompt
    # Reply must go back to the chat room, not be echoed locally.
    assert "chat room" in prompt.lower() or "room" in prompt.lower()
    assert "send" in prompt.lower()


def test_prompt_includes_job_routing_when_present():
    prompt = build_trigger_prompt(
        channel="dev",
        job_id=17,
        role="",
        rules=[],
        first_mention=True,
    )
    assert "17" in prompt


# --------------------------------------------------------------------------
# Busy state / queue semantics
# --------------------------------------------------------------------------

def test_busy_reflects_execution_and_queue(tmp_path: Path):
    inst = make_instance(tmp_path)
    assert inst.is_busy() is False
    inst.set_busy(True, "channel-general")
    assert inst.is_busy() is True
    inst.set_busy(False)
    assert inst.is_busy() is False


def test_queued_trigger_keeps_instance_busy(tmp_path: Path):
    inst = make_instance(tmp_path)
    inst.enqueue({"sender": "human", "text": "hi", "channel": "general"})
    assert inst.is_busy() is True
    assert inst.queue_depth() == 1
    inst.dequeue()
    assert inst.is_busy() is False


def test_queue_is_fifo(tmp_path: Path):
    inst = make_instance(tmp_path)
    for i in range(3):
        inst.enqueue({"text": f"m{i}", "channel": "general"})
    order = [inst.dequeue()["text"] for _ in range(3)]
    assert order == ["m0", "m1", "m2"]
    assert inst.dequeue() is None


def test_queue_is_thread_safe(tmp_path: Path):
    """Concurrent producers must not lose items."""
    inst = make_instance(tmp_path)
    total = 200

    def producer(tag: str) -> None:
        for i in range(total // 2):
            inst.enqueue({"text": f"{tag}{i}", "channel": "general"})

    threads = [threading.Thread(target=producer, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert inst.queue_depth() == total
    drained = 0
    while inst.dequeue() is not None:
        drained += 1
    assert drained == total


# --------------------------------------------------------------------------
# Queue file reading
# --------------------------------------------------------------------------

def test_read_triggers_parses_jsonl(tmp_path: Path):
    queue = tmp_path / "q.jsonl"
    queue.write_text(
        "\n".join(
            json.dumps(entry, ensure_ascii=False)
            for entry in [
                {"sender": "human", "text": "第一条", "channel": "general", "time": 1.0},
                {"sender": "bot", "text": "second", "channel": "dev", "time": 2.0},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    triggers = read_triggers(queue)
    assert [t["text"] for t in triggers] == ["第一条", "second"]


def test_read_triggers_skips_malformed_and_blank_lines(tmp_path: Path):
    queue = tmp_path / "q.jsonl"
    queue.write_text(
        '{"text": "ok", "channel": "general"}\n'
        "\n"
        "{not json\n"
        '{"text": "also ok", "channel": "general"}\n',
        encoding="utf-8",
    )
    triggers = read_triggers(queue)
    assert [t["text"] for t in triggers] == ["ok", "also ok"]


def test_read_triggers_missing_file_returns_empty(tmp_path: Path):
    assert read_triggers(tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------------------
# Access mode labelling (plan: launcher must show "完全访问")
# --------------------------------------------------------------------------

def test_access_mode_is_full_access_not_approval_prompt():
    assert wrapper_dsh.ACCESS_MODE_LABEL == "完全访问"
    # Plan requirement: the launcher must state that approval prompts are off.
    assert "无审批" in wrapper_dsh.ACCESS_MODE_DESC
    assert "danger-full-access" in wrapper_dsh.ACCESS_MODE_DESC


def test_server_name_matches_chat_mcp_tool_prefix():
    # Tools are exposed as mcp__<SERVER_NAME>__<tool>
    assert wrapper_dsh.SERVER_NAME == "agentchattr"


def test_wait_timeout_stops_pump_without_starting_next_trigger(tmp_path, monkeypatch):
    from dsh_protocol import DshTimeout
    inst = make_instance(tmp_path)
    shutdown = threading.Event()
    inst.client.is_running = lambda: True
    def wait(*args, **kwargs):
        raise DshTimeout("managed child still running")
    inst.client.session_wait = wait
    inst.enqueue({"channel": "first", "prompt": "one"})
    inst.enqueue({"channel": "second", "prompt": "two"})
    monkeypatch.setattr(wrapper_dsh, "_fetch_active_rules", lambda *args: None)
    monkeypatch.setattr(wrapper_dsh, "_fetch_role", lambda *args: "")
    wrapper_dsh._trigger_pump(inst, inst.client, None, 8300,
        lambda: (inst.name, inst.queue_file), lambda: inst.token,
        shutdown, lambda message: None, {"slot": 1})
    assert shutdown.is_set()
    assert len(inst.client.prompts) == 1
    assert inst.queue_depth() == 1
    assert "managed child still running" in inst.failure_reason


def test_queue_append_after_drain_is_preserved(tmp_path, monkeypatch):
    queue = tmp_path / "queue.jsonl"
    queue.write_text('{"channel":"first"}\n', encoding="utf-8")
    original = Path.replace
    def replace(path, target):
        result = original(path, target)
        if path == queue:
            queue.write_text('{"channel":"second"}\n', encoding="utf-8")
        return result
    monkeypatch.setattr(Path, "replace", replace)
    assert read_triggers(queue) == [{"channel": "first"}]
    monkeypatch.setattr(Path, "replace", original)
    assert read_triggers(queue) == [{"channel": "second"}]


def test_startup_snapshot_quarantines_only_old_prefix(tmp_path):
    import wrapper_dsh as adapter
    queue = tmp_path / "dsh-2_queue.jsonl"
    old = '{"channel":"old","prompt":"old task"}\n'
    new = '{"channel":"new","prompt":"new task"}\n'
    queue.write_text(old, encoding="utf-8")
    snapshots = adapter.snapshot_pre_registration_queues(tmp_path, "dsh")
    # Simulates a trigger arriving after registration but before SDK startup.
    with queue.open("a", encoding="utf-8") as stream:
        stream.write(new)
    logs = []
    fresh = adapter.quarantine_old_queue(queue, snapshots["dsh-2"], tmp_path / "instance", logs.append)
    assert fresh == [{"channel": "new", "prompt": "new task"}]
    archived = list((tmp_path / "instance" / "interrupted").glob("*.jsonl"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == old
    assert "old task" not in "\n".join(logs)
    assert "new task" not in "\n".join(logs)


def test_startup_without_old_snapshot_preserves_new_queue(tmp_path):
    queue = tmp_path / "dsh_queue.jsonl"
    queue.write_text('{"channel":"registered-after-snapshot"}\n', encoding="utf-8")
    assert wrapper_dsh.quarantine_old_queue(queue, b"", tmp_path / "instance", lambda _: None) == []
    assert read_triggers(queue) == [{"channel": "registered-after-snapshot"}]


def test_reconnect_archives_only_inflight_and_keeps_queued_work(tmp_path):
    inst = make_instance(tmp_path)
    inst.begin_trigger({"channel": "inflight", "prompt": "private unfinished task"}, "channel-inflight")
    inst.enqueue({"channel": "queued"})
    old_session = inst.scope_session_id("channel-inflight")
    records = inst.take_unfinished(include_pending=False)
    logs = []
    wrapper_dsh.archive_interrupted(records, tmp_path / "instance", "server restarted", logs.append)
    inst.reset_sessions()
    assert inst.scope_session_id("channel-inflight") != old_session
    assert inst.dequeue() == {"channel": "queued"}
    assert len(records) == 1 and records[0]["channel"] == "inflight"
    assert "private unfinished task" not in "\n".join(logs)
    assert any("interrupted/result unknown" in line for line in logs)


def test_shutdown_before_prompt_keeps_trigger_for_next_runtime(tmp_path, monkeypatch):
    inst = make_instance(tmp_path)
    shutdown = threading.Event()
    inst.client.is_running = lambda: True
    inst.enqueue({"channel": "new", "prompt": "do not submit yet"})
    def fetch_rules(*args):
        shutdown.set()  # server recovered while the pump was preparing a turn
        return None
    monkeypatch.setattr(wrapper_dsh, "_fetch_active_rules", fetch_rules)
    monkeypatch.setattr(wrapper_dsh, "_fetch_role", lambda *args: "")
    wrapper_dsh._trigger_pump(inst, inst.client, None, 8300,
        lambda: (inst.name, inst.queue_file), lambda: inst.token,
        shutdown, lambda _: None, {"slot": 1})
    assert inst.client.prompts == []
    assert inst.queue_depth() == 1
    assert inst.take_unfinished(include_pending=False) == []


def test_shutdown_preserves_all_unstarted_records_in_private_archive(tmp_path):
    inst = make_instance(tmp_path)
    inst.enqueue({"channel": "one"})
    inst.enqueue({"channel": "two"})
    inst.queue_file.write_text('{"channel":"arrived-during-stop"}\n', encoding="utf-8")
    records = inst.take_unfinished(include_pending=True) + read_triggers(inst.queue_file)
    wrapper_dsh.archive_interrupted(records, tmp_path / "instance", "adapter stopped", lambda _: None)
    archive = next((tmp_path / "instance" / "interrupted").glob("*.jsonl"))
    saved = [json.loads(row) for row in archive.read_text(encoding="utf-8").splitlines()]
    assert [row["trigger"]["channel"] for row in saved] == ["one", "two", "arrived-during-stop"]
    assert all(row["state"] == "interrupted/result unknown" for row in saved)
    assert inst.queue_depth() == 0


def test_unconfirmed_deregistration_keeps_disk_queue_untouched(tmp_path):
    queue = tmp_path / "dsh_queue.jsonl"
    original = '{"channel":"still-routed"}\n'
    queue.write_text(original, encoding="utf-8")
    logs = []
    wrapper_dsh.preserve_shutdown_work(queue, [{"channel": "already-in-memory"}],
        deregistered=False, instance_dir=tmp_path / "instance", log_fn=logs.append)
    assert queue.read_text(encoding="utf-8") == original
    archives = list((tmp_path / "instance" / "interrupted").glob("*.jsonl"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["trigger"]["channel"] == "already-in-memory"
    assert any("deregistration unconfirmed; disk queue retained" in line for line in logs)


def test_confirmed_deregistration_archives_disk_queue(tmp_path):
    queue = tmp_path / "dsh_queue.jsonl"
    original = '{"channel":"not-executed"}\n'
    queue.write_text(original, encoding="utf-8")
    wrapper_dsh.preserve_shutdown_work(queue, [], deregistered=True,
        instance_dir=tmp_path / "instance", log_fn=lambda _: None)
    assert not queue.exists()
    archive = next((tmp_path / "instance" / "interrupted").glob("stopped-*.jsonl"))
    assert archive.read_text(encoding="utf-8") == original


def test_failed_shutdown_archive_retains_staged_queue(tmp_path, monkeypatch):
    import dsh_web_config
    queue = tmp_path / "dsh_queue.jsonl"
    original = '{"channel":"do-not-lose"}\n'
    queue.write_text(original, encoding="utf-8")
    def fail(*args):
        raise OSError("disk write failed")
    monkeypatch.setattr(dsh_web_config, "_secure_file", fail)
    with pytest.raises(OSError, match="disk write failed"):
        wrapper_dsh.preserve_shutdown_work(queue, [], deregistered=True,
            instance_dir=tmp_path / "instance", log_fn=lambda _: None)
    staged = next(tmp_path.glob("dsh_queue.jsonl.stopped-*"))
    assert staged.read_text(encoding="utf-8") == original
