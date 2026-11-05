import json
import subprocess
import sys
from pathlib import Path

from tools import verify_dsh_runtime as verify


def test_defaults_do_not_run_model(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, 'load_config', lambda root: {'agents': {'dsh': {}}})
    monkeypatch.setattr(verify, 'check_dependencies', lambda cfg, root: ({'version': '0.2.1', 'entry': 'entry', 'node': 'node', 'source': 'mock'}, ''))
    monkeypatch.setattr(verify, 'live', lambda timeout: (_ for _ in ()).throw(AssertionError('live ran')))
    report = tmp_path / 'report.json'
    assert verify.main(['--report', str(report)]) == 0
    assert json.loads(report.read_text(encoding='utf8'))['live_requested'] is False


def test_failures_do_not_disclose_exception(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(verify, 'load_config', lambda root: (_ for _ in ()).throw(RuntimeError('secret-token')))
    report = tmp_path / 'report.json'
    assert verify.main(['--report', str(report)]) == 1
    assert 'secret-token' not in capsys.readouterr().out
    assert 'secret-token' not in report.read_text(encoding='utf8')


def test_free_ports_unique_and_loopback():
    ports = verify.free_ports()
    assert len(set(ports)) == 3 and all(0 < port < 65536 for port in ports)


def test_token_and_reply_instructions(monkeypatch):
    monkeypatch.setattr(verify, 'request', lambda *a: '<script>window.__SESSION_TOKEN__="abc";</script>')
    assert verify.token_for(123) == 'abc'
    message = verify.prompt('RANDOM')
    assert all(value in message for value in ('@dsh-99', 'chat_read', 'chat_send', 'RANDOM'))


def test_tool_counts_require_success_not_registry_or_failure(tmp_path):
    log = tmp_path / 'adapter.log'
    log.write_text('[00] SDK ready; chat_read chat_send\n[00] [chat-tool] chat_read session=channel-1 status=ok\n[00] [chat-tool] chat_send session=channel-1 status=error\n', encoding='utf-8')
    assert verify.tool_counts(log) == {'chat_read': 1, 'chat_send': 0}
    with log.open('a', encoding='utf-8') as f:
        f.write('[00] [chat-tool] chat_send session=channel-1 status=ok\n')
    assert verify.tool_counts(log) == {'chat_read': 1, 'chat_send': 1}


def test_restart_requires_live():
    import pytest
    with pytest.raises(SystemExit) as error:
        verify.main(['--restart-check'])
    assert error.value.code == 2


def test_debug_diagnostics_do_not_copy_arbitrary_log_content(tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    (root / 'wrapper.log').write_text('Bearer TOP_SECRET\nTraceback (most recent call last):\nValueError: TOP_SECRET\nError: DSH initialization failed TOP_SECRET\n', encoding='utf-8')
    target = Path(verify.save_debug(tmp_path / 'private', root))
    out = (target / 'wrapper.log').read_text(encoding='utf-8')
    assert 'TOP_SECRET' not in out
    assert 'ValueError' in out and 'Error: DSH initialization failed' in out


def test_failed_live_reports_partial_state(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, 'load_config', lambda root: {'agents': {'dsh': {}}})
    monkeypatch.setattr(verify, 'check_dependencies', lambda cfg, root: ({'version': '1', 'entry': 'entry', 'node': 'node', 'source': 'mock'}, ''))
    def fail(*args):
        raise verify.CheckError('未完成', partial={'stage': '启动 DSH', 'web_files_unchanged': True, 'wrapper_exit_code': 1})
    monkeypatch.setattr(verify, 'live', fail)
    report = tmp_path / 'report.json'
    assert verify.main(['--live', '--report', str(report)]) == 1
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['live']['wrapper_exit_code'] == 1
    assert data['live']['web_files_unchanged'] is True



def test_verifier_identity_uses_actual_registry_contract(tmp_path):
    from registry import RuntimeRegistry
    registry = RuntimeRegistry(data_dir=str(tmp_path))
    registry.seed({'dsh': {'label': 'DSH', 'color': '#123456'}})
    registered = registry.register('dsh', preferred_name=verify.VERIFY_NAME)
    assert registered['name'] == verify.VERIFY_NAME


def test_cleanup_updates_partial_result_after_failure(monkeypatch, tmp_path):
    class Proc:
        returncode = 0
        def poll(self):
            return 0
    result = {'graceful_stop': False, 'deregistered': False, 'wrapper_exit_code': None}
    failure = verify.CheckError('failed', partial=result)
    monkeypatch.setattr(verify, 'request', lambda *args: {})
    verify.stop_wrapper(Proc(), tmp_path / 'stop', 1, 'token', result)
    assert failure.partial['wrapper_exit_code'] == 0
    assert failure.partial['graceful_stop'] is True
    assert failure.partial['deregistered'] is True


def test_recovery_requires_new_runtime_idle_and_unique_identity():
    status = {verify.VERIFY_NAME: {'base': 'dsh', 'available': True, 'busy': False}}
    lease = 'recovered original identity, lease and MCP token'
    ready = 'SDK runtime reconnected; MCP registry verified; new sessions ready'
    assert not verify.runtime_recovered(lease, status)
    assert verify.runtime_recovered(lease + ready, status)
    assert not verify.runtime_recovered(lease + ready, {verify.VERIFY_NAME: {**status[verify.VERIFY_NAME], 'busy': True}})
    assert not verify.runtime_recovered(lease + ready, {**status, 'dsh-2': {'base': 'dsh'}})


def test_completion_is_scoped_and_counted(tmp_path):
    log = tmp_path / 'adapter.log'
    log.write_text('[now] completed trigger for job-4 (session/wait confirmed)\n[now] completed trigger for job-5 (session/wait confirmed)\n', encoding='utf-8')
    assert verify.completion_count(log, 'job-4') == 1
    assert verify.completion_count(log, 'job-6') == 0
