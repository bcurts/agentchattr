"""Live-screen classifiers: busy markers and needs-input prompts."""
import unittest

from screen_state import agent_idle_by_marker, has_busy_marker, has_done_marker, needs_user_input


class BusyTests(unittest.TestCase):
    def test_live_status_lines(self):
        for line in ("✻ Thinking… (12s · esc to interrupt)",
                     "• Working (1m 3s • esc to interrupt)",
                     "⠋ Thinking (esc to cancel, 5s)"):
            self.assertTrue(has_busy_marker(["old", line, "> "]), line)

    def test_quoted_history_is_not_busy(self):
        history = ["I added esc to interrupt handling", "The footer says esc to interrupt."]
        self.assertFalse(has_busy_marker(history + ["", "› Ask Codex"]))

    def test_marker_scrolled_out_of_live_area_is_not_busy(self):
        rows = ["(3s · esc to interrupt)"] + [f"line {i}" for i in range(30)]
        self.assertFalse(has_busy_marker(rows))


class QuotedExampleTests(unittest.TestCase):
    def test_quoted_status_example_is_not_busy(self):
        self.assertFalse(has_busy_marker(["Example: (12s - esc to interrupt)", "> Ready"]))
        self.assertFalse(has_busy_marker(["He wrote Thinking (12s · esc to interrupt) in the docs:", "> Ready"]))

    def test_quoted_prompt_text_is_not_input(self):
        self.assertFalse(needs_user_input(['The tool asks "Do you want to proceed?" before running', "> Ready"]))
        self.assertFalse(needs_user_input(["Do you want to proceed?", "> Ready"]))  # no option rows


class DoneMarkerTests(unittest.TestCase):
    def test_finished_summary_lines(self):
        for line in ("✻ Worked for 1m 3s", "✻ Baked for 2m 5s", "─ Worked for 2m 14s ─────────────",
                     "• Worked for 45s", "done 1:03", "Done in 12s", "✻ Worked for 1h 2m 3s"):
            self.assertTrue(has_done_marker(["output", line, "", "> "]), line)
            self.assertTrue(agent_idle_by_marker(["output", line, "> "]), line)

    def test_quoted_or_partial_text_is_not_done(self):
        for line in ("I worked for 5m on it", "He said Worked for 5m yesterday", "worked for a long time",
                     "done", "The job is done 5 times", "Worked for 2 minutes",
                     '"Worked for 2m 14s"', "`Worked for 2m 14s`", "> Worked for 2m 14s",
                     "'done 1:03'", "- Worked for 2m 14s", "# Worked for 2m 14s", "(Worked for 2m 14s)"):
            self.assertFalse(has_done_marker([line, "> "]), line)

    def test_old_done_line_scrolled_away_is_ignored(self):
        rows = ["✻ Worked for 1m 3s"] + ["new output"] * 12
        self.assertFalse(has_done_marker(rows))

    def test_live_busy_status_beats_done_line(self):
        rows = ["✻ Worked for 1m 3s", "", "✻ Thinking… (4s · esc to interrupt)", "> "]
        self.assertTrue(has_done_marker(rows))
        self.assertFalse(agent_idle_by_marker(rows))


class NeedsInputTests(unittest.TestCase):
    def test_permission_prompts(self):
        for text in ("Do you want to proceed?\n❯ 1. Yes\n  2. No",
                     "Would you like to run the following command?\n  1. Yes, proceed (y)\n  2. No",
                     "Apply changes? [y/n]",
                     "Press enter to confirm or esc to cancel"):
            self.assertTrue(needs_user_input(text), text)

    def test_codex_style_prompts(self):
        edits = ["Would you like to make the following edits?", "", "  src/app.py (+3 -1)", "",
                 "› 1. Yes, proceed (y)", "  2. No, and tell Codex what to do differently (esc)"]
        self.assertTrue(needs_user_input(edits + ["> "]))
        approve = ["Allow Codex to run `git push`?", "› 1. Yes, proceed (y)", "  2. No (esc)"]
        self.assertTrue(needs_user_input(approve))

    def test_yes_proceed_option_alone_needs_input(self):
        for line in ("› 1. Yes, proceed (y)", "  1. Yes, proceed", "│ ❯ 1. Yes, proceed │", "> 1. Yes proceed"):
            self.assertTrue(needs_user_input(["some output", line, "  2. No", "> "]), line)
            self.assertTrue(needs_user_input([line]), line)

    def test_allow_options_need_input(self):
        for line in ("› 1. Allow", "  2. Allow for this session", "│ ❯ 1. Allow │", "> 2. Allow for this session"):
            self.assertTrue(needs_user_input(["some output", line, "  3. Deny", "> "]), line)

    def test_dont_ask_again_options_need_input(self):
        for line in ("› 1. Yes, proceed (y)", "  2. Yes, and don't ask again", "2. Yes, and don’t ask again (p)",
                     "  2. Yes, and don't ask again for commands that start with `git push` (p)",
                     "│ ❯ 2. Yes, and don't ask again │"):
            self.assertTrue(needs_user_input(["some output", line, "  3. No", "> "]), line)

    def test_quoted_dont_ask_again_is_not_input(self):
        for line in ("2. Yes, and don't ask again for example is a menu label.",
                     "2. Yes, and don't ask again for commands that start with `git push` (p) is what you press.",
                     "2. Yes, and don't ask again for commands that start with git push (p)",
                     "2. Yes, and don't ask again is the option you should select.",
                     "He chose \"2. Yes, and don't ask again\" earlier", "- text 2. Yes, and don't ask again",
                     "`2. Yes, and don't ask again`"):
            self.assertFalse(needs_user_input([line, "> "]), line)

    def test_quoted_allow_is_not_input(self):
        for line in ("1. Allow is the option you should select.", 'He chose "1. Allow" earlier',
                     "2. Allow for this session is quoted here", "- text 1. Allow", "`1. Allow`"):
            self.assertFalse(needs_user_input([line, "> "]), line)

    def test_quoted_yes_proceed_is_not_input(self):
        for line in ("1. Yes, proceed is the option you should select.",
                     "1. Yes, proceed-with-caution is just an example.",
                     "1. Yes, proceed (y) is what you press", "1. Yes, proceed.",
                     'He chose "1. Yes, proceed" earlier', "Option 1. Yes, proceed is quoted here",
                     "- text 1. Yes, proceed", "`1. Yes, proceed`", '"1. Yes, proceed"'):
            self.assertFalse(needs_user_input([line, "> "]), line)
        stale = ["› 1. Yes, proceed (y)"] + ["later output"] * 25
        self.assertFalse(needs_user_input(stale))

    def test_question_without_options_is_not_input(self):
        self.assertFalse(needs_user_input(["Would you like to make the following edits? I can do that.", "> "]))

    def test_normal_screen_is_not_input(self):
        self.assertFalse(needs_user_input("Done.\n\n› Ask Codex to do anything"))

    def test_blank_rows_do_not_pull_old_prompt_into_window(self):
        rows = ["Do you want to proceed?", "❯ 1. Yes"] + [""] * 25 + ["> Ready"]
        self.assertFalse(needs_user_input(rows))

    def test_old_prompt_scrolled_away_is_ignored(self):
        text = "Do you want to proceed?\n" + "\n".join(f"line {i}" for i in range(40))
        self.assertFalse(needs_user_input(text))


class CaptureFailureTests(unittest.TestCase):
    def test_unix_checker_clears_needs_input_on_capture_failure(self):
        import subprocess
        from unittest.mock import patch
        from wrapper_unix import get_activity_checker
        checker = get_activity_checker("t")
        ok = subprocess.CompletedProcess([], 0, stdout=b"Apply changes? [y/n]\n")
        with patch("wrapper_unix.subprocess.run", side_effect=[ok, RuntimeError("tmux gone")]):
            checker()
            self.assertTrue(checker.needs_input())
            checker()
            self.assertFalse(checker.needs_input())


if __name__ == "__main__":
    unittest.main()
