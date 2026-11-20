"""Runtime behavior uses isolated fixtures; live pattern files are syntax checked only."""
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest

from pattern_loader import PatternStore, agent_for_command, preview, validate_files


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
        self.write('_shared', 'working', 'WORKING\n')
        self.write('_shared', 'idle', 'DONE\n')
        self.write('codex', 'needs_input', 'APPROVE\n')
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

    def test_readable_rules_normalize_and_never_cross_rows(self):
        self.write('codex', 'needs_input', r"""# comment
Allow * for this session
starts: Press enter
ends: [y/n]
contains: narrow phrase
\# literal hash
A literal \* star
""")
        self.advance()
        for line in ('❯ Allow commands for this session (y) │', 'Press Enter to continue',
                     'confirm [Y/N]', 'one narrow phrase here', '# literal hash', 'A literal * star'):
            self.assertEqual(self.store.classify(line)[0], 'needs_input', line)
        for line in ('"Allow commands for this session"', 'Allow ' + 'x' * 61 + ' for this session',
                     'Allow commands\nfor this session', 'A literal x star'):
            self.assertIsNone(self.store.classify(line)[0], line)

    def test_rule_whitespace_and_preview_contains_warning(self):
        self.write('codex', 'needs_input', '1.  Yes,\tproceed\ncontains: narrow  phrase\n')
        self.advance()
        self.assertEqual(self.store.classify('1. Yes, proceed')[0], 'needs_input')
        self.assertEqual(self.store.classify('1.   Yes,\tproceed')[0], 'needs_input')
        for screen, warning in (('short narrow phrase', False),
                                ('A' * 81 + ' narrow phrase', True),
                                ('A' * 81 + ' unrelated', False),
                                ('1. Yes, proceed\n' + 'A' * 81 + ' narrow phrase', False)):
            output = StringIO()
            with redirect_stdout(output):
                preview(screen, self.store)
            self.assertEqual('Warning:' in output.getvalue(), warning, screen)

    def test_reload_valid_change_without_restart(self):
        self.assertIsNone(self.store.classify('NEW PROMPT')[0])
        self.write('codex', 'needs_input', 'APPROVE\nNEW PROMPT\n')
        self.advance()
        state, matched = self.store.classify('NEW PROMPT')
        self.assertEqual(state, 'needs_input')
        self.assertEqual(matched[1], 2)

    def test_invalid_or_missing_file_keeps_whole_last_known_good_set(self):
        self.write('codex', 'needs_input', 're: [broken\n')
        self.write('_shared', 'working', 'CHANGED\n')
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
        self.write('codex', 'needs_input', 're: (a+)+$\n')
        self.advance()
        with self.assertLogs('pattern_loader', level='WARNING'):
            self.assertEqual(self.store.classify('a' * 1900 + '!')[0], 'unavailable')
            self.assertEqual(self.store.classify('a' * 1900 + '!')[0], 'unavailable')
            state, match = self.store.classify('a' * 1900 + '!')
        self.assertEqual(state, 'needs_input')
        self.assertEqual(match[0], '<repeated timeout>')
        self.assertEqual(self.store.classify('DONE')[0], 'idle')
        with self.assertLogs('pattern_loader', level='WARNING'):
            self.assertEqual(self.store.classify('a' * 1900 + '!')[0], 'unavailable')

    def test_cli_executable_selects_patterns_before_display_name(self):
        self.assertEqual(agent_for_command(r'C:\Tools\codex.exe', 'claude-music'), 'codex')
        self.assertEqual(agent_for_command('agy.cmd', 'renamed-agent'), 'antigravity')
        self.assertEqual(agent_for_command('claude', 'codex-2'), 'claude')
        self.assertEqual(agent_for_command('unknown-tool', 'codex-2'), 'codex')

    def test_syntax_check_accepts_any_valid_regex_and_reports_invalid_line(self):
        self.write('codex', 'working', '# comment\n*\nnew valid expression\n')
        self.assertEqual(validate_files(self.root), [])
        self.write('codex', 'working', '# comment\nre: [invalid\n')
        self.assertIn('working.txt:2', validate_files(self.root)[0])

    def test_isolated_codex_fixtures_cover_new_approval_and_trailing_status(self):
        fixtures = Path(__file__).parent / 'fixtures/screen_patterns'
        store = PatternStore('codex', fixtures)
        for line in ('› 1. Yes, just this once (y)',
                     '2. Yes, and allow these permissions for this session'):
            self.assertEqual(store.classify(line)[0], 'needs_input')
        self.assertEqual(store.classify('• Working (3s • esc to interrupt) · 1 background terminal running')[0], 'working')
        self.assertIsNone(store.classify('1 background terminal running · /ps to view')[0])


    def test_isolated_claude_fixtures_cover_permission_plan_form_and_status_screens(self):
        # Screens are written from memory of Claude Code's interface, not captured from a live session.
        store = PatternStore('claude', Path(__file__).parent / 'fixtures/screen_patterns')
        edit_menu = ['Do you want to make this edit to app.py?', '❯ 1. Yes', '  2. Yes, allow all edits during this session (shift+tab)',
                     '  3. No, and tell Claude what to do differently (esc)']
        shell_menu = ['Do you want to proceed?', '❯ 1. Yes', "  2. Yes, and don't ask again for git commands in /repo",
                      '  3. No, and tell Claude what to do differently (esc)']
        plan = ['Would you like to proceed?', '❯ 1. Yes, and auto-accept edits', '  2. Yes, and manually approve edits', '  3. No, keep planning']
        form = ['Which approach?', '❯ 1. Option A', '  2. Option B', 'Enter to select · ↑/↓ to navigate · Esc to cancel']
        for name, screen in (('edit', edit_menu), ('shell', shell_menu), ('plan', plan), ('form', form)):
            self.assertEqual(store.classify(screen)[0], 'needs_input', name)
        self.assertEqual(store.classify('✻ Thinking… (12s · esc to interrupt)')[0], 'working')
        self.assertEqual(store.classify('✢ Pondering… (esc to interrupt)')[0], 'working')
        self.assertEqual(store.classify(['✻ Worked for 1m 3s', '> '])[0], 'idle')
        # A working line beats a done line, and a prompt beats both.
        self.assertEqual(store.classify(['✻ Worked for 1m 3s', '✢ Pondering… (esc to interrupt)'])[0], 'working')
        self.assertEqual(store.classify(['✢ Pondering… (esc to interrupt)'] + plan)[0], 'needs_input')

    def test_isolated_claude_fixtures_ignore_quoted_text(self):
        store = PatternStore('claude', Path(__file__).parent / 'fixtures/screen_patterns')
        for quoted in ('The menu says: 3. No, and tell Claude what to do differently (esc)',
                       'I will say "Would you like to proceed?" with 1. Yes, and auto-accept edits',
                       'Press esc to interrupt (esc to interrupt) when needed',
                       'Enter to select and then Esc to cancel later'):
            self.assertIsNone(store.classify(quoted)[0], quoted)

    def test_antigravity_spinner_line_means_working(self):
        # The first line is the spinner text Derek pasted from a live Agy session.
        store = PatternStore('antigravity', Path(__file__).parent / 'fixtures/screen_patterns')
        for frame in '⣾⣽⣻⢿⡿⣟⣯⣷':        # the spinner's animation frames
            self.assertEqual(store.classify(f'{frame}  Calling MCP tool...')[0], 'working', frame)
        self.assertEqual(store.classify('⣾  Thinking')[0], 'working')
        self.assertEqual(store.classify(['│ ⣾  Generating response...', '> '])[0], 'working')
        # Not a spinner: no glyph, glyph mid-line, glyph-only rows (braille art), or a non-label after the glyph.
        for quiet in ('Calling MCP tool...', 'status ⣾  Calling MCP tool...', '⣿⣿⣿⣿', '⣾  ',
                      '⣾  ...', 'The spinner shows ⣾  Calling MCP tool...'):
            self.assertIsNone(store.classify(quiet)[0], quiet)
        # Only the live bottom rows count: a spinner line scrolled far up does not.
        self.assertIsNone(store.classify(['⣾  Calling MCP tool...'] + ['output'] * 14)[0])


class DistributedPatternSyntaxTests(unittest.TestCase):
    def test_distributed_patterns_compile(self):
        self.assertEqual(validate_files(), [])


if __name__ == '__main__':
    unittest.main()
