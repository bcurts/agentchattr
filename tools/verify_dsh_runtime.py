"""Discover DSH, or explicitly run an isolated live chat verification."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

VERIFY_NAME = 'dsh-99'
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config_loader import load_config
from dsh_runtime import check_dependencies


class CheckError(Exception):
    def __init__(self, message, *, partial=None):
        super().__init__(message)
        self.partial = partial


def web_hashes():
    home = Path(os.environ.get('DSH_HOME') or Path.home() / '.dsh')
    paths = {'web_profile': home / 'profiles' / 'web' / 'cordis.patch.yml',
             'global_patch': home / 'cordis.patch.yml', 'credentials': home / '.credentials.yaml'}
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            for name, path in paths.items()}


def free_ports():
    sockets = []
    try:
        for _ in range(3):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            sockets.append(s)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:
            s.close()


def request(port, path, token='', body=None):
    headers = {'X-Session-Token': token} if token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=3) as response:
        raw = response.read().decode('utf-8')
    return raw if path == '/' else json.loads(raw)


def wait_for(test, timeout, process=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if process is not None and process.poll() is not None:
            raise CheckError('验证进程提前退出，请检查 DSH Web 模型配置或本地环境。')
        try:
            value = test()
            if value:
                return value
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.25)
    raise CheckError('等待验证结果超时。')


def token_for(port):
    html = request(port, '/')
    match = re.search(r'window\.__SESSION_TOKEN__\s*=\s*["\']([^"\']+)', html)
    return match.group(1) if match else None


def terminate_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=10, check=False)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        raise CheckError('验证进程未能结束。')


def stop_wrapper(wrapper, stop, port, token, result):
    if not wrapper:
        return
    try:
        if wrapper.poll() is None:
            stop.touch()
            wrapper.wait(timeout=25)
        result['wrapper_exit_code'] = wrapper.poll()
        result['graceful_stop'] = wrapper.returncode == 0
        if result['graceful_stop']:
            result['deregistered'] = VERIFY_NAME not in request(port, '/api/status', token)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def prompt(sentinel):
    return (f'@{VERIFY_NAME} 请先使用 chat_read 读取当前会话，再使用 chat_send 回复，'
            f'回复内容仅为 {sentinel}。不要访问文件、运行 shell 或浏览网页。')


def recv_until(ws, predicate, timeout, process):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CheckError('DSH 验证进程提前退出。')
        try:
            event = json.loads(ws.recv(timeout=min(1, max(.01, deadline - time.monotonic()))))
        except TimeoutError:
            continue
        if predicate(event):
            return event
    raise CheckError('等待聊天室回复超时。')


def tool_counts(log_path):
    try:
        lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    except FileNotFoundError:
        return {'chat_read': 0, 'chat_send': 0}
    return {name: sum(bool(re.search(r'\[chat-tool\] ' + name + r' session=[\w:-]* status=ok$', line))
                      for line in lines) for name in ('chat_read', 'chat_send')}


def verify_tools(log_path, before, timeout, process):
    def both_completed():
        after = tool_counts(log_path)
        return all(after[name] > before[name] for name in ('chat_read', 'chat_send'))
    wait_for(both_completed, timeout, process)


def completion_count(log_path, scope):
    try:
        text = log_path.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return 0
    return text.count(f'completed trigger for {scope} (session/wait confirmed)')


def runtime_recovered(text, status):
    named = [name for name, info in status.items()
             if isinstance(info, dict) and info.get('base') == 'dsh']
    instance = status.get(VERIFY_NAME, {})
    return ('recovered original identity, lease and MCP token' in text
            and 'SDK runtime reconnected; MCP registry verified; new sessions ready' in text
            and named == [VERIFY_NAME] and instance.get('available')
            and not instance.get('busy'))


def safe_diagnostics(text):
    """Retain bounded structural diagnostics, never arbitrary log text or secrets."""
    safe = []
    for line in text.splitlines():
        # These exact messages originate in our adapter, not remote responses.
        for marker in ('Error: DSH runtime failed to start.', 'Error: DSH initialization timed out.',
                       'Error: DSH initialization failed', 'Error: DSH rejected initialization',
                       'SDK ready; verified chat tools in runtime registry:',
                       'recovered original identity, lease and MCP token',
                       'preferred_name_mismatch', 'HTTP Error 409', 'HTTP Error 401',
                       'DSH adapter stopped.', 'caller requested graceful stop'):
            if marker in line:
                safe.append(marker)
                break
        else:
            match = re.search(r'\b([A-Za-z_][\w.]*(?:Error|Exception))\b', line)
            if match:
                safe.append('exception type: ' + match.group(1))
            elif 'Traceback (most recent call last)' in line:
                safe.append('Python traceback present')
    return '\n'.join(safe[-150:]) + '\n'


def save_debug(parent, base):
    """Write sanitized log summaries in a new user-private directory."""
    target = Path(parent).expanduser().resolve() / ('agentchattr-dsh-debug-' + secrets.token_hex(6))
    target.mkdir(parents=True, exist_ok=False)
    if os.name == 'nt':
        try:
            user = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, check=True)
            sid = next(csv.reader(io.StringIO(user.stdout)))[1]
            subprocess.run(['icacls', str(target), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'],
                           capture_output=True, check=True)
        except (OSError, subprocess.SubprocessError, IndexError, StopIteration):
            target.rmdir()
            raise CheckError('无法设置私有诊断目录权限。') from None
    else:
        target.chmod(0o700)
    paths = {'server.log': base / 'server.log', 'wrapper.log': base / 'wrapper.log',
             'adapter.log': base / 'data' / 'dsh' / VERIFY_NAME / 'logs' / 'adapter.log'}
    for name, source in paths.items():
        if source.is_file():
            (target / name).write_text(safe_diagnostics(source.read_text(encoding='utf-8', errors='replace')),
                                       encoding='utf-8')
    return str(target)


def live(timeout, restart_check=False, debug_dir=None):
    from websockets.sync.client import connect
    baseline = web_hashes()
    result = {'channel_replies': 0, 'job_reply': False, 'tool_registry_ready': False,
              'real_chat_read_verified': False, 'real_chat_send_verified': False, 'web_files_unchanged': None,
              'graceful_stop': False, 'deregistered': False,
              'restart_requested': restart_check, 'restart_reply': False,
              'same_lease_recovered': False, 'no_duplicate_instances': False,
              'stage': '准备', 'server_exit_code': None, 'wrapper_exit_code': None}
    temp_parent = Path(tempfile.gettempdir()).resolve()
    server = wrapper = None
    token = ''
    try:
        with tempfile.TemporaryDirectory(prefix='agentchattr-dsh-verify-', dir=temp_parent) as folder:
            base = Path(folder).resolve()
            if base.parent != temp_parent or not base.name.startswith('agentchattr-dsh-verify-'):
                raise CheckError('临时目录校验失败。')
            data, uploads, project = (base / n for n in ('data', 'uploads', 'project'))
            for directory in (data, uploads, project):
                directory.mkdir()
            ports = free_ports()
            options = ['--data-dir', str(data), '--upload-dir', str(uploads), '--port', str(ports[0]),
                       '--mcp-http-port', str(ports[1]), '--mcp-sse-port', str(ports[2])]
            env = os.environ.copy()
            env.update({'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1', 'TMP': str(base), 'TEMP': str(base), 'TMPDIR': str(base)})
            stop = base / 'stop'
            audit_log = data / 'dsh' / VERIFY_NAME / 'logs' / 'adapter.log'
            with (base / 'server.log').open('wb') as server_log, (base / 'wrapper.log').open('wb') as wrapper_log:
                failed = False
                try:
                    result['stage'] = '启动服务器'
                    server = subprocess.Popen([sys.executable, str(ROOT / 'run.py'), *options], cwd=ROOT, env=env,
                                              stdout=server_log, stderr=subprocess.STDOUT)
                    token = wait_for(lambda: token_for(ports[0]), timeout, server)
                    result['stage'] = '启动 DSH 并等待就绪'
                    wrapper = subprocess.Popen([sys.executable, str(ROOT / 'wrapper_dsh.py'), 'dsh', *options,
                                                '--preferred-name', VERIFY_NAME, '--workdir', str(project),
                                                '--stop-file', str(stop)], cwd=ROOT, env=env,
                                               stdout=wrapper_log, stderr=subprocess.STDOUT)
                    def ready():
                        text = (base / 'wrapper.log').read_text(encoding='utf-8', errors='replace')
                        status = request(ports[0], '/api/status', token)
                        return ('SDK ready; verified chat tools in runtime registry:' in text
                                and status.get(VERIFY_NAME, {}).get('available')
                                and status.get(VERIFY_NAME, {}).get('state') != 'pending')
                    wait_for(ready, timeout, wrapper)
                    result['tool_registry_ready'] = True
                    result['stage'] = '验证频道收发'
                    with connect(f'ws://127.0.0.1:{ports[0]}/ws?token={token}', open_timeout=10) as ws:
                        for channel in ('dsh-check-a', 'dsh-check-b'):
                            ws.send(json.dumps({'type': 'channel_create', 'name': channel}))
                            recv_until(ws, lambda e: e.get('type') == 'settings' and channel in e.get('data', {}).get('channels', []), timeout, wrapper)
                            sentinel = 'VERIFY_' + secrets.token_hex(8)
                            before = tool_counts(audit_log)
                            completed_before = completion_count(audit_log, f'channel-{channel}')
                            ws.send(json.dumps({'type': 'message', 'sender': 'user', 'channel': channel, 'text': prompt(sentinel)}))
                            recv_until(ws, lambda e: e.get('type') == 'message' and e.get('data', {}).get('channel') == channel
                                       and e.get('data', {}).get('sender') == VERIFY_NAME
                                       and e.get('data', {}).get('text', '').strip() == sentinel, timeout, wrapper)
                            verify_tools(audit_log, before, timeout, wrapper)
                            wait_for(lambda: completion_count(audit_log, f'channel-{channel}') > completed_before, timeout, wrapper)
                            result['channel_replies'] += 1
                    result['stage'] = '验证任务收发'
                    job = request(ports[0], '/api/jobs', token, {'title': 'DSH live verification', 'channel': 'dsh-check-a'})
                    job_id = int(job['id'])
                    sentinel = 'VERIFY_' + secrets.token_hex(8)
                    before = tool_counts(audit_log)
                    completed_before = completion_count(audit_log, f'job-{job_id}')
                    request(ports[0], f'/api/jobs/{job_id}/messages', token, {'sender': 'user', 'text': prompt(sentinel)})
                    def job_reply():
                        messages = request(ports[0], f'/api/jobs/{job_id}/messages', token)
                        return any(m.get('sender') == VERIFY_NAME and m.get('text', '').strip() == sentinel for m in messages)
                    wait_for(job_reply, timeout, wrapper)
                    verify_tools(audit_log, before, timeout, wrapper)
                    wait_for(lambda: completion_count(audit_log, f'job-{job_id}') > completed_before, timeout, wrapper)
                    result['job_reply'] = True
                    result['real_chat_read_verified'] = result['real_chat_send_verified'] = True
                    if restart_check:
                        result['stage'] = '验证服务器重启恢复'
                        # Wait through at least one heartbeat interval after the
                        # reply so a stale pre-turn idle status cannot suffice.
                        earliest_idle = time.monotonic() + 6.0
                        def idle():
                            status = request(ports[0], '/api/status', token)
                            instance = status.get(VERIFY_NAME, {})
                            return (time.monotonic() >= earliest_idle
                                    and instance.get('available') and not instance.get('busy'))
                        wait_for(idle, timeout, wrapper)
                        terminate_tree(server)
                        server = subprocess.Popen([sys.executable, str(ROOT / 'run.py'), *options], cwd=ROOT, env=env,
                                                  stdout=server_log, stderr=subprocess.STDOUT)
                        token = wait_for(lambda: token_for(ports[0]), timeout, server)
                        def recovered():
                            text = (base / 'wrapper.log').read_text(encoding='utf-8', errors='replace')
                            status = request(ports[0], '/api/status', token)
                            return runtime_recovered(text, status)
                        wait_for(recovered, timeout, wrapper)
                        result['same_lease_recovered'] = True
                        result['no_duplicate_instances'] = True
                        sentinel = 'VERIFY_' + secrets.token_hex(8)
                        before = tool_counts(audit_log)
                        completed_before = completion_count(audit_log, 'channel-dsh-check-a')
                        with connect(f'ws://127.0.0.1:{ports[0]}/ws?token={token}', open_timeout=10) as ws:
                            ws.send(json.dumps({'type': 'message', 'sender': 'user', 'channel': 'dsh-check-a', 'text': prompt(sentinel)}))
                            recv_until(ws, lambda e: e.get('type') == 'message' and e.get('data', {}).get('channel') == 'dsh-check-a'
                                       and e.get('data', {}).get('sender') == VERIFY_NAME
                                       and e.get('data', {}).get('text', '').strip() == sentinel, timeout, wrapper)
                        verify_tools(audit_log, before, timeout, wrapper)
                        wait_for(lambda: completion_count(audit_log, 'channel-dsh-check-a') > completed_before, timeout, wrapper)
                        result['restart_reply'] = True
                    result['stage'] = '停止并注销'
                except Exception as exc:
                    failed = True
                    result['server_exit_code'] = server.poll() if server else None
                    result['wrapper_exit_code'] = wrapper.poll() if wrapper else None
                    raise CheckError('验证未完成。', partial=result) from exc
                finally:
                    stop_wrapper(wrapper, stop, ports[0], token, result)
                    terminate_tree(wrapper)
                    if wrapper:
                        result['wrapper_exit_code'] = wrapper.poll()
                    terminate_tree(server)
                    result['web_files_unchanged'] = web_hashes() == baseline
                    if debug_dir is not None and (failed or not result['graceful_stop']
                                                  or not result['deregistered']
                                                  or not result['web_files_unchanged']):
                        try:
                            result['debug_dir'] = save_debug(debug_dir, base)
                        except Exception:
                            result['debug_error'] = '无法创建私有诊断文件。'
    finally:
        result['web_files_unchanged'] = web_hashes() == baseline
    if not all((result['channel_replies'] == 2, result['job_reply'], result['graceful_stop'],
                result['deregistered'], result['web_files_unchanged'],
                not restart_check or (result['same_lease_recovered'] and result['no_duplicate_instances'] and result['restart_reply']))):
        raise CheckError('收发、注销或 Web 配置完整性检查未全部通过。', partial=result)
    result['stage'] = '完成'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='DSH 运行环境发现及隔离真人模型收发验证')
    parser.add_argument('--live', action='store_true', help='调用真实模型进行隔离聊天室验证')
    parser.add_argument('--restart-check', action='store_true', help='重启隔离服务器并检查原身份恢复（需要 --live）')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--report', type=Path, help='保存不含凭据的 JSON 报告')
    parser.add_argument('--debug-dir', type=Path, help='失败时保存脱敏诊断至新建的私有目录')
    args = parser.parse_args(argv)
    if args.restart_check and not args.live:
        parser.error('--restart-check 需要 --live')
    report = {'ok': False, 'live_requested': args.live}
    try:
        cfg = load_config(ROOT).get('agents', {}).get('dsh', {})
        resolved, error = check_dependencies(cfg, ROOT)
        if error:
            raise CheckError(error)
        report['runtime'] = {k: str(resolved[k]) if resolved.get(k) is not None else None for k in ('version', 'entry', 'node', 'source')}
        print('DSH 版本：', report['runtime']['version'])
        print('入口：', report['runtime']['entry'])
        print('Node：', report['runtime']['node'])
        if args.live:
            report['live'] = live(args.timeout, args.restart_check, args.debug_dir)
            print('隔离聊天室及任务回复验证通过；临时调试日志已清理。')
            print('已核验每轮实际 chat_read 和 chat_send 成功事件。')
        report['ok'] = True
    except CheckError as exc:
        report['error'] = str(exc)
        print('验证失败：', str(exc))
        if exc.partial is not None:
            report['live'] = exc.partial
            print('失败阶段：', exc.partial.get('stage'))
            if exc.partial.get('debug_dir'):
                print('私有脱敏诊断：', exc.partial['debug_dir'])
    except Exception:
        report['error'] = '验证过程异常；详情已隐藏以保护凭据。'
        print('验证失败：', report['error'])
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
