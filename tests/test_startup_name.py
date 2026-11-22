import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app
from registry import RuntimeRegistry


class StartupNameTests(unittest.TestCase):
    def test_registration_endpoint_uses_requested_identity_and_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = RuntimeRegistry(tmp)
            registry.seed({'codex': {'label': 'Codex'}})
            async def body():
                return {'base': 'codex', 'name': 'c2'}
            request = SimpleNamespace(json=body)
            with patch.object(app, 'registry', registry), patch.object(app, '_event_loop', None):
                first = asyncio.run(app.register_agent(request))
                self.assertEqual(first.status_code, 200)
                data = json.loads(first.body)
                self.assertEqual(data['name'], 'c2')
                self.assertEqual(registry.resolve_token(data['token'])['name'], 'c2')
                duplicate = asyncio.run(app.register_agent(request))
                self.assertEqual(duplicate.status_code, 409)
                self.assertIn('already in use', json.loads(duplicate.body)['error'])
                self.assertEqual(registry.resolve_token(data['token'])['name'], 'c2')

    @unittest.skipUnless(os.name == 'nt', 'Windows batch launchers')
    def test_batch_launchers_forward_names_before_cli_separator(self):
        source = Path(__file__).resolve().parents[1] / 'windows'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            windows = root / 'windows'
            windows.mkdir()
            (windows / 'ensure_venv.bat').write_text(f'@set "VENV_PY={sys.executable}"\n')
            for cli in ('codex', 'claude', 'qwen', 'kilo'):
                (windows / f'{cli}.cmd').write_text('@exit /b 0\n')
            fake = "import json,sys; open('args.json','w').write(json.dumps(sys.argv[1:]))\n"
            (root / 'wrapper.py').write_text(fake)
            (root / 'wrapper_api.py').write_text(fake)
            env = dict(os.environ, PATH=f'{windows}{os.pathsep}{os.environ["PATH"]}')
            cases = [
                ('start_codex.bat', ['--name', 'c2'], ['codex', '--name', 'c2', '--', '--no-daemon', '--approve-for-me']),
                ('start_claude.bat', ['--name', 'c2'], ['claude', '--name', 'c2']),
                ('start_kilo.bat', ['vendor/model', '--name', 'c2'], ['kilo', '--name', 'c2', '--', '-m', 'vendor/model']),
                ('start_kilo.bat', ['--name', 'c2'], ['kilo', '--name', 'c2']),
                ('start_api_agent.bat', ['qwen', '--name', 'c2'], ['qwen', '--name', 'c2']),
            ]
            for filename, args, expected in cases:
                with self.subTest(filename=filename, args=args):
                    text = (source / filename).read_text(encoding='utf-8')
                    # Keep the launcher logic, replacing only its live server probe with a no-op.
                    text = text.replace("import socket; socket.create_connection(('127.0.0.1', 8300), 1)", 'pass')
                    for cli in ('codex', 'claude', 'qwen', 'kilo'):
                        text = text.replace(f'where {cli} >nul 2>&1', 'ver >nul')
                    script = windows / filename
                    script.write_text(text, encoding='utf-8')
                    result = subprocess.run(['cmd.exe', '/d', '/c', str(script), *args], input='\n', text=True,
                                            capture_output=True, env=env, timeout=15,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(json.loads((root / 'args.json').read_text()), expected)


if __name__ == '__main__':
    unittest.main()
