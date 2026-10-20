"""Exercise job research through MCP transports and an HTTP MCP fixture."""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import uvicorn
from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp_bridge
import parallel_search
from jobs import JobStore
from registry import RuntimeRegistry


class SearchFixture(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), SearchHandler)
        self.requests = []
        self.outcome = "success"


class SearchHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((self.path, dict(self.headers), message))
        if self.server.outcome == 'unavailable':
            self.send_response(503)
            self.end_headers()
            return
        if 'id' not in message:
            self.send_response(202)
            self.end_headers()
            return
        method = message['method']
        if method == 'initialize':
            result = {
                'protocolVersion': message['params']['protocolVersion'],
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'research-fixture', 'version': '1'},
            }
        elif method == 'tools/list':
            result = {'tools': [
                {'name': name, 'inputSchema': {'type': 'object'}}
                for name in ('web_search', 'web_fetch')
            ]}
        elif method == 'tools/call':
            if self.server.outcome == 'delay':
                time.sleep(0.3)
            text = 'Python asyncio documentation: https://docs.python.org/3/library/asyncio.html'
            result = {'content': [{'type': 'text', 'text': text}], 'isError': False}
            if self.server.outcome == 'quota':
                result = {'content': [{'type': 'text', 'text': 'Anonymous quota exceeded'}], 'isError': True}
        else:
            result = {}
        body = json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ResearchCallerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = JobStore(str(Path(self.tmp.name) / 'jobs.json'))
        self.registry = RuntimeRegistry(data_dir=self.tmp.name)
        self.registry.seed({'claude': {'label': 'Claude', 'color': '#fff'}})
        self.identity = self.registry.register('claude')
        self.job = self.new_job()
        self.remote = SearchFixture()
        self.thread = threading.Thread(target=self.remote.serve_forever, daemon=True)
        self.thread.start()
        self.patches = [
            patch.object(mcp_bridge, 'jobs', self.jobs),
            patch.object(mcp_bridge, 'registry', self.registry),
            patch.object(mcp_bridge, 'config', {}),
            patch.dict(os.environ, {'PARALLEL_API_KEY': 'must-not-be-forwarded',
                                    'OPENAI_API_KEY': 'must-not-be-forwarded'}),
            patch.object(parallel_search, 'ENDPOINT', f'http://127.0.0.1:{self.remote.server_port}/mcp'),
        ]
        for p in self.patches:
            p.start()
        self.servers = []

    async def asyncTearDown(self):
        for server, task in self.servers:
            server.should_exit = True
            await task
        for p in reversed(self.patches):
            p.stop()
        await asyncio.to_thread(self.remote.shutdown)
        self.remote.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def new_job(self):
        return self.jobs.create('Research Python', 'task', 'general', 'user')

    async def connect(self, transport='http', token=None):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = mcp_bridge._create_server(port)
        app = server.streamable_http_app() if transport == 'http' else server.sse_app()
        runner = uvicorn.Server(uvicorn.Config(app, log_level='critical'))
        task = asyncio.create_task(runner.serve(sockets=[sock]))
        self.servers.append((runner, task))
        for _ in range(200):
            if runner.started:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(runner.started)
        selected_token = self.identity['token'] if token is None else token
        headers = {'Authorization': 'Bearer ' + selected_token} if selected_token else {}
        if transport == 'http':
            return streamablehttp_client(f'http://127.0.0.1:{port}/mcp', headers=headers)
        return sse_client(f'http://127.0.0.1:{port}/sse', headers=headers)

    def calls(self):
        return [m['params'] for _, _, m in self.remote.requests if m['method'] == 'tools/call']

    async def search(self, session, job_id=None):
        return await session.call_tool('chat_search', {
            'job_id': job_id or self.job['id'],
            'objective': 'Find Python asyncio documentation',
            'search_queries': ['Python asyncio documentation'],
        })

    async def test_both_transports_discover_execute_consume_and_post_results(self):
        # The model is scripted: exercise the real SDK dispatcher and response,
        # then consume its source URL via the ordinary chat_send tool.
        for transport in ('http', 'sse'):
            async with await self.connect(transport) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertTrue({'chat_search', 'chat_fetch', 'chat_send'} <= names)
                    result = await self.search(session)
                    self.assertFalse(result.isError, repr(result))
                    text = '\n'.join(b.text for b in result.content if b.type == 'text')
                    self.assertIn('https://docs.python.org/', text)
                    fetched = await session.call_tool('chat_fetch', {
                        'job_id': self.job['id'], 'urls': ['https://docs.python.org/3/library/asyncio.html'],
                    })
                    self.assertFalse(fetched.isError)
                    await self.search(session)
                    sent = await session.call_tool('chat_send', {
                        'sender': 'ignored', 'job_id': self.job['id'], 'message': text,
                    })
                    self.assertFalse(sent.isError)
        self.assertEqual(len(self.jobs.get_messages(self.job['id'])), 2)
        ids = {c['arguments']['session_id'] for c in self.calls()}
        self.assertEqual(len(ids), 1)
        uuid.UUID(ids.pop())
        for path, headers, message in self.remote.requests:
            self.assertEqual(path, '/mcp')
            self.assertEqual(headers['User-Agent'], parallel_search.USER_AGENT)
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('X-Agent-Token', headers)
        self.assertEqual({c['name'] for c in self.calls()}, {'web_search', 'web_fetch'})
        self.assertEqual(self.calls()[1]['arguments']['urls'], ['https://docs.python.org/3/library/asyncio.html'])

    async def test_concurrent_jobs_restart_reopen_and_new_conversation(self):
        second = self.new_job()
        async with await self.connect() as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                results = await asyncio.gather(self.search(session), self.search(session, second['id']))
                self.assertTrue(all(not result.isError for result in results))
                first_id = self.jobs.research_session_id(self.job['id'])
                second_id = self.jobs.research_session_id(second['id'])
                self.assertNotEqual(first_id, second_id)
                self.jobs.update_status(self.job['id'], 'open')
                reloaded = JobStore(str(Path(self.tmp.name) / 'jobs.json'))
                with patch.object(mcp_bridge, 'jobs', reloaded):
                    await self.search(session)
                third = self.new_job()
                await self.search(session, third['id'])
                self.assertNotIn(self.jobs.research_session_id(third['id']), (first_id, second_id))
        ids = [c['arguments']['session_id'] for c in self.calls()]
        self.assertCountEqual(ids[:2], [first_id, second_id])
        self.assertEqual(ids[2], first_id)

    async def test_disabled_permissions_invalid_job_and_urls_make_no_requests(self):
        async with await self.connect() as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                with patch.object(mcp_bridge, 'config', {'search': {'enabled': False}}):
                    self.assertTrue((await self.search(session)).isError)
                self.assertTrue((await self.search(session, 9999)).isError)
                for url in ('file:///etc/passwd', 'http://user:password@example.com', 'http:///bad'):
                    result = await session.call_tool('chat_fetch', {'job_id': self.job['id'], 'urls': [url]})
                    self.assertTrue(result.isError)
                for queries in ([], ['']):
                    result = await session.call_tool('chat_search', {
                        'job_id': self.job['id'], 'objective': 'find docs', 'search_queries': queries,
                    })
                    self.assertTrue(result.isError)
        for token in ('stale-token', ''):
            async with await self.connect(token=token) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    self.assertTrue((await self.search(session)).isError)
        self.assertEqual(self.remote.requests, [])
        self.assertNotIn('research_session_id', self.jobs.get(self.job['id']))

    async def test_quota_and_retry_keep_conversation_and_surface_error(self):
        async with await self.connect() as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                self.remote.outcome = 'quota'
                result = await self.search(session)
                self.assertTrue(result.isError)
                self.assertIn('Anonymous quota exceeded', result.content[0].text)
                self.remote.outcome = 'success'
                self.assertFalse((await self.search(session)).isError)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.calls()[0]['arguments']['session_id'], self.calls()[1]['arguments']['session_id'])

    async def test_timeout_and_cancellation_stop_work_without_retry(self):
        self.remote.outcome = 'delay'
        async with await self.connect() as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                with patch.object(parallel_search, 'TIMEOUT_SECONDS', 0.15):
                    result = await self.search(session)
                    self.assertTrue(result.isError)
                    self.assertIn('timed out', result.content[0].text)
                timeout_id = self.calls()[0]['arguments']['session_id']
                cancelled = asyncio.Event()
                original_call = parallel_search.call_tool

                async def observe_cancellation(*args):
                    try:
                        return await original_call(*args)
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise

                with patch.object(parallel_search, 'call_tool', observe_cancellation):
                    # The SDK has no cancel helper. Send the MCP notification
                    # explicitly, as an agent host does when the user cancels.
                    request_id = session._request_id
                    task = asyncio.create_task(self.search(session))
                    for _ in range(200):
                        if len(self.calls()) == 2:
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(len(self.calls()), 2)
                    await session.send_notification(types.ClientNotification(
                        types.CancelledNotification(params=types.CancelledNotificationParams(
                            requestId=request_id, reason='User cancelled research',
                        )),
                    ))
                    await asyncio.wait_for(cancelled.wait(), timeout=1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                await asyncio.sleep(0.35)
                self.assertEqual(len(self.calls()), 2)
                self.assertEqual(self.calls()[1]['arguments']['session_id'], timeout_id)

    async def test_transport_failure_is_reported_without_retry_or_fallback(self):
        self.remote.outcome = 'unavailable'
        async with await self.connect() as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                self.assertTrue((await self.search(session)).isError)
        # Only initialization was attempted. No replacement provider or retry.
        self.assertEqual(len(self.remote.requests), 1)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
