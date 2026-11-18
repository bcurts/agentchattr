"""Runtime behavior uses isolated fixtures; live pattern files are syntax checked only."""
from pathlib import Path
import tempfile
import unittest

from pattern_loader import PatternStore, validate_files


class PatternLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('_shared', 'codex', 'claude'):
            folder = self.root / name
            folder.mkdir()
            for state in ('working', 'idle', 'needs_input'):
                (folder / f'{state}.txt').write_text('# patterns\n', encoding='utf-8')
        self.write('_shared', 'working', '^WORKING$\n')
        self.write('_shared', 'idle', '^DONE$\n')
        self.write('codex', 'needs_input', '^APPROVE$\n')
        self.now = 100.0
        self.store = PatternStore('codex-2', self.root, clock=lambda: self.now)

    def write(self, agent, state, text):
        (self.root / agent / f'{state}.txt').write_text(text, encoding='utf-8')

    def advance(self):
        self.now += 6

    def test_precedence_case_and_agent_isolation(self):
        self.assertEqual(self.store.classify('DONE\nWORKING\napprove')[0], 'needs_input')
        self.assertEqual(self.store.classify('DONE\nWORKING')[0], 'working')
        self.assertEqual(self.store.classify('DONE')[0], 'idle')
        self.assertEqual(PatternStore('claude', self.root).classify('APPROVE')[0], None)
        self.assertEqual(PatternStore('../codex', self.root).classify('APPROVE')[0], None)

    def test_reload_valid_change_without_restart(self):
        self.assertIsNone(self.store.classify('NEW PROMPT')[0])
        self.write('codex', 'needs_input', '^APPROVE$\n^NEW PROMPT$\n')
        self.advance()
        state, matched = self.store.classify('NEW PROMPT')
        self.assertEqual(state, 'needs_input')
        self.assertEqual(matched[1], 2)

    def test_invalid_or_missing_file_keeps_whole_last_known_good_set(self):
        self.write('codex', 'needs_input', '[broken\n')
        self.write('_shared', 'working', '^CHANGED$\n')
        self.advance()
        with self.assertLogs('pattern_loader', level='WARNING') as log:
            self.assertEqual(self.store.classify('APPROVE')[0], 'needs_input')
        self.assertIn('needs_input.txt:1', log.output[0])
        self.assertEqual(self.store.classify('WORKING')[0], 'working')
        (self.root / 'codex' / 'needs_input.txt').unlink()
        self.advance()
        with self.assertLogs('pattern_loader', level='WARNING'):
            self.assertEqual(self.store.classify('APPROVE')[0], 'needs_input')

    def test_row_window_and_multi_line_question_pairing(self):
        self.assertIsNone(self.store.classify(['APPROVE'] + [''] * 21)[0])
        self.assertIsNone(self.store.classify('Would you like to make the following edits?')[0])
        self.assertEqual(self.store.classify('Would you like to make the following edits?\n1. Yes')[0], 'needs_input')

    def test_pathological_expression_has_enforced_timeout(self):
        self.write('codex', 'needs_input', '(a+)+$\n')
        self.advance()
        with self.assertLogs('pattern_loader', level='WARNING'):
            state, match = self.store.classify('a' * 1900 + '!')
        self.assertEqual(state, 'needs_input')
        self.assertEqual(match[0], '<timeout>')

    def test_syntax_check_accepts_any_valid_regex_and_reports_invalid_line(self):
        self.write('codex', 'working', '# comment\n.*\n^new valid expression$\n')
        self.assertEqual(validate_files(self.root), [])
        self.write('codex', 'working', '# comment\n[invalid\n')
        self.assertIn('working.txt:2', validate_files(self.root)[0])

    def test_isolated_codex_fixtures_cover_new_approval_and_trailing_status(self):
        fixtures = Path(__file__).parent / 'fixtures/screen_patterns'
        store = PatternStore('codex', fixtures)
        for line in ('› 1. Yes, just this once (y)',
                     '2. Yes, and allow these permissions for this session'):
            self.assertEqual(store.classify(line)[0], 'needs_input')
        self.assertEqual(store.classify('• Working (3s • esc to interrupt) · 1 background terminal running')[0], 'working')
        self.assertIsNone(store.classify('1 background terminal running · /ps to view')[0])


class DistributedPatternSyntaxTests(unittest.TestCase):
    def test_distributed_patterns_compile(self):
        self.assertEqual(validate_files(), [])


if __name__ == '__main__':
    unittest.main()
