"""Upload/download policy and persisted file paths for channel and job reads."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app
import mcp_bridge
from jobs import JobStore
from store import MessageStore


class FileAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.uploads = self.root / 'uploads'
        self.config = {'server': {'port': 8300}, 'images': {'upload_dir': str(self.uploads)},
                       'attachments': {'max_size_mb': 1}}
        api = FastAPI()
        api.add_api_route('/api/upload', app.upload_image, methods=['POST'])
        api.add_api_route('/uploads/{filename}', app.serve_upload, methods=['GET'])
        for name, value in {'app': api, 'config': self.config, 'registry': None,
                            'session_token': 'attachment-test'}.items():
            self.stack.enter_context(patch.object(app, name, value))
        app._install_security_middleware('attachment-test', self.config)
        self.client = self.stack.enter_context(TestClient(api))
        self.headers = {'X-Session-Token': 'attachment-test'}

    def upload(self, name, content=b'test bytes', mime='application/octet-stream'):
        return self.client.post('/api/upload', files={'file': (name, content, mime)},
                                headers=self.headers)

    def test_documents_round_trip_with_metadata_and_authenticated_download(self):
        for extension in ['pdf', 'docx', 'xlsx', 'csv', 'txt', 'md', 'log', 'zip', 'py', 'wav', 'exe', 'html', 'unusual', 'c++', '日本語']:
            with self.subTest(extension=extension):
                data = ('unique content: ' + extension).encode()
                response = self.upload('report.' + extension, data)
                self.assertEqual(response.status_code, 200)
                attachment = response.json()
                self.assertEqual(attachment['kind'], 'file')
                self.assertEqual(attachment['size'], len(data))
                self.assertEqual(attachment['name'], 'report.' + extension)
                self.assertTrue(attachment['media_type'])
                self.assertEqual(self.client.get(attachment['url']).status_code, 403)
                download = self.client.get(attachment['url'], headers=self.headers)
                self.assertEqual(download.content, data)
                self.assertTrue(download.headers['content-disposition'].startswith('attachment;'))
                self.assertEqual(download.headers['x-content-type-options'], 'nosniff')

    def test_legacy_images_still_have_public_inline_previews(self):
        response = self.upload('picture.png', b'png bytes', 'image/png')
        self.assertEqual(response.json()['kind'], 'image')
        download = self.client.get(response.json()['url'])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers['content-type'], 'image/png')
        self.assertNotIn('content-disposition', download.headers)

    def test_rejections_leave_no_files_and_limits_apply_before_full_copy(self):
        self.config['attachments']['allowed_extensions'] = ['.pdf']
        self.assertEqual(self.upload('script.exe').status_code, 400)
        self.assertEqual(self.upload('page.html').status_code, 400)
        self.assertEqual(self.upload('large.pdf', b'x' * (1024 * 1024 + 1)).status_code, 413)
        self.assertEqual(list(self.uploads.iterdir()), [])
        self.assertEqual(self.upload('exact.pdf', b'x' * (1024 * 1024)).status_code, 200)

    def test_files_without_extensions_and_dotfiles_are_accepted(self):
        for name in ['Makefile', '.gitignore']:
            with self.subTest(name=name):
                response = self.upload(name, b'plain text')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['kind'], 'file')
                self.assertEqual(response.json()['name'], name)
                downloaded = self.client.get(response.json()['url'], headers=self.headers)
                self.assertEqual(downloaded.content, b'plain text')
                self.assertTrue(downloaded.headers['content-disposition'].startswith('attachment;'))

    def test_configurable_extensions_do_not_make_documents_executable(self):
        self.config['attachments']['allowed_extensions'] = ['.html']
        self.assertEqual(self.upload('report.pdf').status_code, 400)
        uploaded = self.upload('page.html', b'<script>alert(1)</script>').json()
        response = self.client.get(uploaded['url'], headers=self.headers)
        self.assertEqual(response.headers['content-type'], 'application/octet-stream')
        self.assertTrue(response.headers['content-disposition'].startswith('attachment;'))

    def test_names_cannot_choose_storage_paths_or_overwrite_files(self):
        one = self.upload('../../report.pdf', b'first').json()
        two = self.upload('../../report.pdf', b'second').json()
        self.assertEqual(one['name'], 'report.pdf')
        self.assertNotEqual(one['url'], two['url'])
        self.assertEqual(self.client.get(one['url'], headers=self.headers).content, b'first')
        outside = self.root / 'private.pdf'
        outside.write_bytes(b'secret')
        (self.uploads / 'link.pdf').symlink_to(outside)
        self.assertEqual(self.client.get('/uploads/link.pdf', headers=self.headers).status_code, 400)

    def test_browser_display_name_preserves_quotes_and_unicode(self):
        name = 'report " café.pdf'
        response = self.client.post('/api/upload',
                                    files={'file': ('report %22 café.pdf', b'contents')},
                                    data={'display_name': name}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], name)

    def test_upload_requires_session_and_rejects_cross_origin(self):
        response = self.client.post('/api/upload', files={'file': ('x.pdf', b'x')})
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/upload', files={'file': ('x.pdf', b'x')},
                                    headers={**self.headers, 'Origin': 'https://outside.example'})
        self.assertEqual(response.status_code, 403)

    def test_reloaded_channel_and_job_reads_include_original_bytes_path(self):
        attachment = self.upload('report.pdf', b'persisted file').json()
        store_path, jobs_path = self.root / 'chat.jsonl', self.root / 'jobs.json'
        messages, jobs = MessageStore(str(store_path)), JobStore(str(jobs_path))
        messages.add('human', 'read this', attachments=[attachment])
        job = jobs.create('Files', 'task', 'general', 'human')
        jobs.add_message(job['id'], 'human', 'read this too', attachments=[attachment])
        jobs.update_status(job['id'], status='archived')
        jobs = JobStore(str(jobs_path))
        jobs.update_status(job['id'], status='open')
        for name, value in {'store': MessageStore(str(store_path)), 'jobs': jobs,
                            'config': self.config, 'registry': None}.items():
            self.stack.enter_context(patch.object(mcp_bridge, name, value))
        for kwargs in ({'channel': 'general'}, {'job_id': job['id']}):
            entries = json.loads(mcp_bridge.chat_read(**kwargs))
            read = next(entry for entry in entries if entry.get('attachments'))['attachments'][0]
            self.assertEqual(read['size'], len(b'persisted file'))
            self.assertEqual(Path(read['file_path']).read_bytes(), b'persisted file')


if __name__ == '__main__':
    unittest.main()
