"""Reloadable, time-bounded terminal-screen patterns for agent wrappers."""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import regex
import screen_state

ROOT = Path(__file__).resolve().parent / 'patterns'
STATES = ('working', 'idle', 'needs_input')
ROWS = {'working': 12, 'idle': 8, 'needs_input': 20}
INTERVAL = 5.0
MATCH_BUDGET = 0.25
SEARCH_TIMEOUT = 0.025
TIMEOUT_ALERT_THRESHOLD = 3
MAX_PATTERNS = 256
MAX_LINE_LENGTH = 1024
MAX_FILE_BYTES = 512_000
MAX_SCREEN_ROW = 2000
LOG = logging.getLogger(__name__)
KNOWN_AGENTS = frozenset(('codex', 'claude', 'antigravity', 'gemini', 'kimi', 'qwen',
                          'kilo', 'codebuddy', 'copilot', 'minimax'))


def normalize_agent(name):
    """Never allow a requested agent name to escape the pattern directory."""
    name = re.sub(r'-\d+$', '', str(name or '').lower())
    return name if re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name) else ''


def agent_for_command(command, fallback=''):
    """Choose patterns by CLI executable, not by a changeable chat identity."""
    if isinstance(command, str):
        executable = command.strip().replace('\\', '/').rsplit('/', 1)[-1].lower()
        executable = re.sub(r'\.(?:exe|cmd|bat)$', '', executable)
        executable = {'agy': 'antigravity'}.get(executable, executable)
        if executable in KNOWN_AGENTS:
            return executable
    return normalize_agent(fallback)


def parse_file(file):
    for line_number, line in enumerate(file.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        yield line_number, line



def normalize_row(row):
    """Remove terminal decoration, but preserve prose and quoted content."""
    row = str(row)[:MAX_SCREEN_ROW].strip()
    row = re.sub(r'^[\s│|╭╰─╮╯❯>›•●✻✶✢]+', '', row)
    row = re.sub(r'[\s│|╭╰─╮╯]+$', '', row)
    row = re.sub(r'\s*\((?:y|a|enter)\)\s*$', '', row, flags=re.IGNORECASE)
    return ' '.join(row.split())


def compile_rule(expression):
    """Compile readable row rules; explicit re: rules operate on the original row."""
    expression = expression.strip()
    if expression.lower().startswith('re:'):
        source = expression[3:].strip()
        if not source:
            raise ValueError('empty regex rule')
        re.compile(source, re.IGNORECASE)
        return regex.compile(source, regex.IGNORECASE), True
    mode = 'line'
    for prefix in ('starts:', 'ends:', 'contains:'):
        if expression.lower().startswith(prefix):
            mode, expression = prefix[:-1], expression[len(prefix):].strip()
            break
    if not expression:
        raise ValueError('empty rule')
    expression = ' '.join(expression.split())
    chunks = []
    i = 0
    while i < len(expression):
        char = expression[i]
        if char == '\\' and i + 1 < len(expression) and expression[i + 1] in '*#\\':
            i += 1
            chunks.append(re.escape(expression[i]))
        elif char == '*':
            chunks.append(r'[^\n]{0,60}')
        else:
            chunks.append(re.escape(char))
        i += 1
    body = ''.join(chunks)
    source = ('^' if mode in ('line', 'starts') else '') + body + ('$' if mode in ('line', 'ends') else '')
    return regex.compile(source, regex.IGNORECASE), False


def validate_files(root=ROOT):
    """Syntax-only CI/startup validation; adding any valid expression is allowed."""
    errors = []
    for file in sorted(Path(root).glob('*/*.txt')):
        try:
            for number, expression in parse_file(file):
                try:
                    compile_rule(expression)
                except (ValueError, re.error, regex.error) as error:
                    errors.append(f'{file}:{number}: {error}')
        except (OSError, UnicodeError) as error:
            errors.append(f'{file}: {error}')
    return errors


def _defaults():
    return {'working': [screen_state._BUSY_LINE], 'idle': [screen_state._DONE_LINE],
            'needs_input': [screen_state._YN_LINE, screen_state._ENTER_LINE, screen_state._SUBMIT_LINE,
                            screen_state._YES_PROCEED_LINE, screen_state._ALLOW_LINE,
                            screen_state._ALLOW_SESSION_LINE, screen_state._DONT_ASK_LINE]}


def _compile_defaults():
    return {state: [(regex.compile(p.pattern, regex.IGNORECASE), '<bundled default>', 0, True)
                    for p in patterns] for state, patterns in _defaults().items()}


def _signature(files):
    result = []
    for file in files:
        try:
            stat = file.stat()
            result.append((str(file), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            result.append((str(file), None, None))
    return tuple(result)


class PatternStore:
    """One instance per wrapper: atomically reload a complete last-known-good set."""
    def __init__(self, agent, root=ROOT, *, clock=time.monotonic, interval=INTERVAL):
        self.agent = normalize_agent(agent)
        self.root = Path(root)
        self.clock = clock
        self.interval = interval
        self.patterns = _compile_defaults()
        self._agent_files_expected = self.agent in KNOWN_AGENTS
        self._next_check = 0.0
        self._signature = None
        self._timeouts = 0
        self.refresh(force=True)

    def _files(self):
        files = [self.root / '_shared' / f'{state}.txt' for state in STATES]
        folder = self.root / self.agent if self.agent else None
        if folder and (self._agent_files_expected or folder.is_dir()):
            self._agent_files_expected = True
            files.extend(folder / f'{state}.txt' for state in STATES)
        return files

    def refresh(self, force=False):
        now = self.clock()
        if not force and now < self._next_check:
            return
        self._next_check = now + self.interval
        files = self._files()
        try:
            signature = _signature(files)
        except OSError as error:
            LOG.warning('Screen pattern files unavailable; retaining last known good: %s', error)
            return
        if not force and signature == self._signature:
            return
        self._signature = signature
        candidate = {state: [] for state in STATES}
        try:
            for file in files:
                if file.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError(f'{file}: file exceeds {MAX_FILE_BYTES} bytes')
                for number, expression in parse_file(file):
                    if len(expression) > MAX_LINE_LENGTH:
                        raise ValueError(f'{file}:{number}: expression exceeds {MAX_LINE_LENGTH} characters')
                    if sum(map(len, candidate.values())) >= MAX_PATTERNS:
                        raise ValueError(f'{file}:{number}: too many patterns')
                    try:
                        compiled, raw = compile_rule(expression)
                    except (ValueError, re.error, regex.error) as error:
                        raise ValueError(f'{file}:{number}: {error}') from error
                    candidate[file.stem].append((compiled, str(file), number, raw))
            if _signature(files) != signature:
                self._signature = None
                raise ValueError('files changed during reload; retrying')
            self.patterns = candidate
        except (OSError, UnicodeError, ValueError) as error:
            LOG.warning('Screen patterns not reloaded; retaining last known good: %s', error)

    def classify(self, screen):
        self.refresh()
        rows = screen.splitlines() if isinstance(screen, str) else list(screen)
        rows = [str(row)[:MAX_SCREEN_ROW].rstrip() for row in rows[-max(ROWS.values()):]]
        deadline = time.monotonic() + MATCH_BUDGET
        matches = {}
        try:
            for state in ('needs_input', 'working', 'idle'):
                for row in rows[-ROWS[state]:]:
                    for pattern, file, line, raw in self.patterns[state]:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('screen match budget exhausted')
                        if pattern.search(row if raw else normalize_row(row), timeout=min(SEARCH_TIMEOUT, remaining)):
                            matches[state] = (file, line)
                            break
                    if state in matches:
                        break
            # Some approval questions are only meaningful when options are visible too.
            live = rows[-ROWS['needs_input']:]
            if ('needs_input' not in matches and any(screen_state._QUESTION_LINE.match(r) for r in live)
                    and any(screen_state._OPTION_ROW.match(r) for r in live)):
                matches['needs_input'] = ('<question and options>', 0)
        except TimeoutError as error:
            self._timeouts += 1
            LOG.warning('Screen pattern match timed out (%s/%s): %s', self._timeouts, TIMEOUT_ALERT_THRESHOLD, error)
            if self._timeouts >= TIMEOUT_ALERT_THRESHOLD:
                return 'needs_input', ('<repeated timeout>', 0)
            return 'unavailable', ('<timeout; retain previous state>', 0)
        self._timeouts = 0
        for state in ('needs_input', 'working', 'idle'):
            if state in matches:
                return state, matches[state]
        return None, None


def preview(screen, store):
    """Show the selected rule and flag broad contains matches on long rows."""
    rows = screen.splitlines()[-max(ROWS.values()):]
    print('Normalized rows:')
    for row in rows:
        print(f'  {normalize_row(row)}')
    state, match = store.classify(screen)
    if match and match[1]:
        try:
            rules = Path(match[0]).read_text(encoding='utf-8-sig').splitlines()
            rule = rules[match[1] - 1]
            print(f'Rule: {rule}')
            if rule.strip().lower().startswith('contains:'):
                compiled, _ = compile_rule(rule)
                for row in rows[-ROWS[state]:]:
                    normalized = normalize_row(row)
                    if compiled.search(normalized, timeout=SEARCH_TIMEOUT):
                        if len(normalized) > 80:
                            print('Warning: contains: matched a long row; it may match ordinary prose.')
                        break
        except (OSError, UnicodeError, IndexError, TimeoutError):
            pass
    print(f'{state or "unknown"}: {match[0]}:{match[1]}' if match else 'unknown')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preview agent screen classification from stdin.')
    parser.add_argument('--agent', required=True)
    args = parser.parse_args()
    screen = sys.stdin.buffer.read().decode('utf-8-sig', errors='replace')
    preview(screen, PatternStore(args.agent))
