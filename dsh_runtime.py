"""Read-only discovery of the locally cached npm DSH package."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

_PACKAGE = Path('node_modules') / '@deepseek-ai' / 'dsh'
_HINT = '请先运行 npx @deepseek-ai/dsh web 获取运行环境，再重试。'


def _npx_key(spec: str) -> str:
    """npm/libnpmexec cache key for a one-package npx invocation."""
    return hashlib.sha512(spec.encode('utf-8')).hexdigest()[:16]


def _path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _node(value: str, root: Path) -> str:
    if not value:
        return shutil.which('node') or ''
    found = shutil.which(value)
    candidate = Path(found) if found else _path(value, root)
    return str(candidate.resolve()) if candidate.is_file() else ''


def _package(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / 'package.json').read_text(encoding='utf-8'))
        if data.get('name') != '@deepseek-ai/dsh' or not isinstance(data.get('version'), str):
            return None
        binary = data.get('bin')
        if isinstance(binary, dict):
            binary = binary.get('dsh')
        if not isinstance(binary, str) or not binary:
            return None
        entry = (directory / binary).resolve()
        # A package's bin must be within its own directory.
        if not entry.is_relative_to(directory.resolve()) or not entry.is_file():
            return None
        return {'entry': str(entry), 'package_root': directory.resolve(), 'version': data['version']}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _version_key(version: str) -> tuple:
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?', version)
    if not match:
        return (-1, -1, -1, 0, ())
    prerelease = match.group(4)
    parts = tuple((0, int(part)) if part.isdigit() else (1, part) for part in (prerelease or '').split('.'))
    return (*map(int, match.group(1, 2, 3)), int(prerelease is None), parts)


def _npm_cache(node: str) -> tuple[Path | None, str]:
    npm = shutil.which('npm')
    if not npm:
        return None, '找不到 npm，无法读取 npm 缓存位置。'
    npm_path = Path(npm).resolve()
    if os.name == 'nt' and npm_path.suffix.lower() in ('.cmd', '.bat'):
        # npm's standard Windows installation puts npm-cli.js beside its shim.
        cli = npm_path.parent / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js'
        if not cli.is_file():
            return None, '找不到 npm-cli.js，无法安全读取 npm 缓存位置。'
        command = [node, str(cli), 'config', 'get', 'cache']
    else:
        command = [str(npm_path), 'config', 'get', 'cache']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        cache = result.stdout.strip()
        if result.returncode or not cache or cache in ('undefined', 'null'):
            return None, 'npm 未返回有效的缓存位置。'
        return Path(cache).expanduser().resolve(), ''
    except (OSError, subprocess.SubprocessError):
        return None, '读取 npm 缓存位置失败。'


def check_dependencies(agent_cfg: dict, root: Path) -> tuple[dict, str]:
    """Find Node and DSH without installing packages or reading Web state.

    Explicit entry and Node paths take precedence; invalid overrides fail closed.
    Legacy runtime directory settings are ignored, including the old bundled runtime.
    """
    root = Path(root).resolve()
    entry_setting = os.environ.get('AGENTCHATTR_DSH_ENTRY', '').strip() or str(agent_cfg.get('dsh_entry') or '').strip()
    node_setting = os.environ.get('AGENTCHATTR_NODE_PATH', '').strip() or str(agent_cfg.get('node_path') or '').strip()
    node = _node(node_setting, root)
    if not node:
        return {}, '找不到指定的 Node.js，请安装 Node 或正确设置 AGENTCHATTR_NODE_PATH。'
    warnings = []
    if agent_cfg.get('runtime_dir') or agent_cfg.get('dsh_runtime_dir'):
        warnings.append('旧 runtime_dir / dsh_runtime_dir 配置已废弃并忽略；请使用 dsh_entry 或 npx 缓存。')
    if entry_setting:
        entry = _path(entry_setting, root)
        if not entry.is_file():
            return {}, f'指定的 DSH 入口不存在：{entry}。{_HINT}'
        package = next((info for parent in entry.parents if (info := _package(parent)) and info['entry'] == str(entry)), None)
        if not package:
            package = {'entry': str(entry), 'package_root': entry.parent, 'version': None}
        source = 'explicit_entry'
    else:
        cache, error = _npm_cache(node)
        if error:
            return {}, f'{error}{_HINT}'
        npx_dir = cache / '_npx'
        package = None
        for spec in ('@deepseek-ai/dsh', '@deepseek-ai/dsh@latest'):
            package = _package(npx_dir / _npx_key(spec) / _PACKAGE)
            if package:
                break
        candidates = []
        try:
            if not package:
                for item in npx_dir.iterdir():
                    if item.is_dir():
                        info = _package(item / _PACKAGE)
                        if info:
                            candidates.append(info)
        except OSError:
            pass
        if not package and not candidates:
            return {}, f'未在 npm 缓存 {cache / "_npx"} 找到有效的 @deepseek-ai/dsh。{_HINT}'
        if not package:
            package = max(candidates, key=lambda item: (_version_key(item['version']), str(item['package_root'])))
            warnings.append('未找到原 npx 命令或 latest 的缓存，已选择其他缓存中的最高版本；可用 dsh_entry 指定入口。')
        source = 'npm_npx_cache'
    return {**package, 'node': node, 'runtime_dir': package['package_root'], 'source': source, 'warnings': warnings}, ''
