import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from dsh_web_config import WebConfigError, prepare_web_config


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    node = shutil.which('node')
    cache = Path(os.environ.get('LOCALAPPDATA', '')) / 'npm-cache' / '_npx'
    entries = list(cache.glob('*/node_modules/@deepseek-ai/dsh/lib/bin.js')) if cache.is_dir() else []
    if not node or not entries:
        pytest.skip('requires Node and an existing DSH npm cache')
    home = tmp_path / 'web'
    profile = home / 'profiles' / 'web'
    profile.mkdir(parents=True)
    config = [
        {'id': 'ui-settings', 'config': {'private': 'ignored-ui'}},
        {'id': 'llm-pi-ai', 'config': {'providers': {
            'example': {'apiKeyEnv': 'TEST_DSH_KEY', 'baseURL': 'https://example.test', 'models': [{'id': 'test-model'}]},
            'other': {'apiKeyEnv': 'OTHER_SECRET', 'models': [{'id': 'other-model'}]}}}},
        {'id': 'agent-default-model', 'config': {'provider': 'example', 'model': 'test-model'}}]
    (profile / 'cordis.patch.yml').write_text(json.dumps(config), encoding='utf-8')
    (home / 'cordis.patch.yml').write_text('[]', encoding='utf-8')
    (home / '.credentials.yaml').write_text(json.dumps({'version': 1, 'refs': {'TEST_DSH_KEY': 'secret-for-test', 'OTHER_SECRET': 'must-not-copy'}}), encoding='utf-8')
    monkeypatch.delenv('TEST_DSH_KEY', raising=False)
    return home, tmp_path / 'instance', {'node': node, 'entry': str(entries[0]), 'web_home': str(home)}


def hashes(home):
    return [hashlib.sha256(p.read_bytes()).digest() for p in (home / 'profiles/web/cordis.patch.yml', home / 'cordis.patch.yml', home / '.credentials.yaml')]


def test_selects_only_route_and_key_without_touching_web(fixture):
    home, instance, runtime = fixture
    before = hashes(home)
    result = prepare_web_config({}, instance, runtime=runtime)
    assert (result['provider'], result['model']) == ('example', 'test-model')
    patch = json.loads(Path(result['patches'][0]).read_text(encoding='utf-8'))
    credentials = json.loads((instance / 'home/.credentials.yaml').read_text(encoding='utf-8'))
    assert list(patch[0]['config']['providers']) == ['example']
    assert credentials == {'version': 1, 'refs': {'TEST_DSH_KEY': 'secret-for-test'}}
    assert 'secret-for-test' not in json.dumps(result)
    assert hashes(home) == before
    assert json.loads(Path(result['audit_path']).read_text())['credentials'] == before[2].hex()
    if os.name != 'nt':
        assert (instance / 'home/.credentials.yaml').stat().st_mode & 0o077 == 0


def test_invalid_model_and_missing_credential_fail_closed(fixture):
    home, instance, runtime = fixture
    with pytest.raises(WebConfigError, match='模型'):
        prepare_web_config({'model': 'missing'}, instance, runtime=runtime)
    (home / '.credentials.yaml').write_text('{"version":1}', encoding='utf-8')
    with pytest.raises(WebConfigError, match='凭据'):
        prepare_web_config({}, instance, runtime=runtime)
    assert not (instance / 'home/.credentials.yaml').exists()


def test_official_environment_needs_no_web(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'hidden-value')
    result = prepare_web_config({'provider':'deepseek-official', 'model':'some-model'}, tmp_path, runtime={})
    assert result['patches'] == [] and result['provider'] == 'deepseek-official'
    assert 'hidden-value' not in str(result)


def test_official_environment_replaces_old_instance_credential(fixture, monkeypatch):
    home, instance, runtime = fixture
    before = hashes(home)
    credentials = instance / 'home' / '.credentials.yaml'
    credentials.parent.mkdir(parents=True)
    credentials.write_text('{"version":1,"refs":{"DEEPSEEK_API_KEY":"old-key"}}', encoding='utf-8')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'new-environment-key')

    result = prepare_web_config({'provider': 'deepseek-official', 'model': 'some-model'}, instance, runtime=runtime)

    assert json.loads(credentials.read_text(encoding='utf-8')) == {'version': 1}
    assert result['patches'] == [] and 'new-environment-key' not in str(result)
    assert hashes(home) == before
    if os.name != 'nt':
        assert credentials.stat().st_mode & 0o077 == 0
