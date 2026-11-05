import json
from pathlib import Path

import dsh_runtime as runtime


def package(cache, key, version='0.2.1-rc.5', binary='lib/bin.js'):
    root = cache / '_npx' / key / 'node_modules' / '@deepseek-ai' / 'dsh'
    root.mkdir(parents=True)
    (root / 'package.json').write_text(json.dumps({'name': '@deepseek-ai/dsh', 'version': version, 'bin': {'dsh': binary}}), encoding='utf-8')
    entry = root / binary
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text('// entry', encoding='utf-8')
    return root, entry


def setup(monkeypatch, tmp_path):
    node = tmp_path / 'Node with spaces' / 'node.exe'
    node.parent.mkdir()
    node.touch()
    monkeypatch.delenv('AGENTCHATTR_DSH_ENTRY', raising=False)
    monkeypatch.delenv('AGENTCHATTR_NODE_PATH', raising=False)
    monkeypatch.setattr(runtime, '_npm_cache', lambda _: (tmp_path / 'cache', ''))
    return {'node_path': str(node)}


def test_cache_versions_bad_packages_and_legacy_ignored(monkeypatch, tmp_path):
    cfg = setup(monkeypatch, tmp_path)
    cache = tmp_path / 'cache'
    package(cache, 'old', '0.1.5')
    chosen, entry = package(cache, 'new', '0.2.1-rc.5')
    package(cache, 'bad', '0.9.0')
    (cache / '_npx' / 'bad' / 'node_modules' / '@deepseek-ai' / 'dsh' / 'package.json').write_text('{', encoding='utf-8')
    cfg['runtime_dir'] = 'dsh-runtime/dsh-0.1.5'
    result, error = runtime.check_dependencies(cfg, tmp_path)
    assert not error
    assert result['entry'] == str(entry.resolve())
    assert result['package_root'] == result['runtime_dir'] == chosen.resolve()
    assert result['source'] == 'npm_npx_cache'
    assert result['version'] == '0.2.1-rc.5'
    assert result['warnings']


def test_explicit_paths_take_precedence_and_bad_override_fails(monkeypatch, tmp_path):
    cfg = setup(monkeypatch, tmp_path)
    root, entry = package(tmp_path / 'cache', 'a')
    monkeypatch.setenv('AGENTCHATTR_DSH_ENTRY', str(entry))
    result, error = runtime.check_dependencies({**cfg, 'dsh_entry': 'wrong'}, tmp_path)
    assert not error and result['package_root'] == root.resolve()
    assert result['source'] == 'explicit_entry'
    monkeypatch.setenv('AGENTCHATTR_DSH_ENTRY', str(tmp_path / 'missing'))
    assert runtime.check_dependencies(cfg, tmp_path)[1]
    monkeypatch.setenv('AGENTCHATTR_NODE_PATH', str(tmp_path / 'missing-node'))
    assert 'Node' in runtime.check_dependencies(cfg, tmp_path)[1]


def test_missing_cache_gives_npx_instruction(monkeypatch, tmp_path):
    cfg = setup(monkeypatch, tmp_path)
    result, error = runtime.check_dependencies(cfg, tmp_path)
    assert result == {} and 'npx @deepseek-ai/dsh web' in error


def test_npm_query_argument_vector(monkeypatch, tmp_path):
    npm = tmp_path / 'npm'
    npm.touch()
    monkeypatch.setattr(runtime.shutil, 'which', lambda name: str(npm) if name == 'npm' else None)
    calls = []
    class Result:
        returncode = 0
        stdout = str(tmp_path / 'cache with spaces') + '\n'
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    cache, error = runtime._npm_cache('node')
    assert not error and cache == (tmp_path / 'cache with spaces').resolve()
    assert calls[0][0] == [str(npm), 'config', 'get', 'cache']
    assert 'shell' not in calls[0][1]


def test_bin_path_escape_rejected(tmp_path):
    root = tmp_path / 'pkg'
    root.mkdir()
    (tmp_path / 'outside.js').touch()
    (root / 'package.json').write_text(json.dumps({'name': '@deepseek-ai/dsh', 'version': '1.0.0', 'bin': '../outside.js'}), encoding='utf-8')
    assert runtime._package(root) is None


def test_original_npx_cache_wins_over_newer_prerelease(monkeypatch, tmp_path):
    cfg = setup(monkeypatch, tmp_path)
    cache = tmp_path / 'cache'
    _, expected = package(cache, runtime._npx_key('@deepseek-ai/dsh'), '0.2.1-rc.5')
    package(cache, runtime._npx_key('@deepseek-ai/dsh@latest'), '0.2.1-rc.8')
    package(cache, 'alpha', '0.2.2-alpha.1')
    resolved, error = runtime.check_dependencies(cfg, tmp_path)
    assert not error and resolved['entry'] == str(expected.resolve())
    assert runtime._npx_key('@deepseek-ai/dsh') == '1e7f6d9597241db0'
    assert runtime._npx_key('@deepseek-ai/dsh@latest') == 'b86ed90107c62dab'
    assert not resolved['warnings']


def test_latest_cache_precedes_fallback(monkeypatch, tmp_path):
    cfg = setup(monkeypatch, tmp_path)
    cache = tmp_path / 'cache'
    _, expected = package(cache, runtime._npx_key('@deepseek-ai/dsh@latest'), '0.2.1-rc.5')
    package(cache, 'alpha', '0.2.2-alpha.1')
    resolved, error = runtime.check_dependencies(cfg, tmp_path)
    assert not error and resolved['entry'] == str(expected.resolve())
