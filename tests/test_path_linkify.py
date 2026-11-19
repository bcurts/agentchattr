"""Regression tests for the path-link helpers in static/chat.js (runs the real functions under Node)."""
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parent.parent / "static" / "chat.js"
RUNNER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const names = ['mapTextOutsideSkipped', 'linkifyUrls', 'pathSpaceSource', 'windowsPathSource', 'unixPathSource',
  'uncPathSource', 'protectedPathSource', 'linkifyPaths'];
const code = src.match(/const TEXT_SKIP_TAGS[^\r\n]*/)[0] + '\n' + names.map((n) => src.match(new RegExp('function ' + n + '[\\s\\S]*?\\r?\\n}\\r?\\n'))[0]).join('\n');
const make = (platform) => new Function('serverPlatform',
  code + ';return {linkifyPaths, linkifyUrls, protectedPathSource};')(platform);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map(([platform, text, mode]) => {
  const api = make(platform);
  if (mode === 'protect') return text.match(new RegExp(api.protectedPathSource(), 'g')) || [];
  return mode === 'urls' ? api.linkifyUrls(text) : api.linkifyPaths(text);
})));
"""


def run(cases):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    done = subprocess.run(["node", "-e", RUNNER, str(CHAT_JS)], input=json.dumps(cases), capture_output=True,
                          text=True, timeout=30, creationflags=flags)
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def linked(html):
    """The linked path texts, in order."""
    return re.findall(r'<a class="file-link"[^>]*>(.*?)</a>', html)


@unittest.skipUnless(shutil.which("node"), "node is required")
class LinkifyPathsTests(unittest.TestCase):
    def check(self, platform, text, expected):
        html = run([[platform, text]])[0]
        self.assertEqual(linked(html), expected, text)
        self.assertNotIn("<a ", re.sub(r'<a class="file-link"[^>]*>.*?</a>', "", html), text)

    def test_trailing_prose_is_not_linked(self):
        self.check("win32", r"in C:\Users\DL\Documents\Github 2. start the localhost dashboard 3. open browser",
                   [r"C:\Users\DL\Documents\Github"])
        self.check("linux", "open /Users/DL/Github 2. start the x", ["/Users/DL/Github"])

    def test_names_with_spaces_stay_whole(self):
        self.check("win32", r"open C:\Program Files and then", [r"C:\Program Files"])
        self.check("win32", r"open C:\Program Files\app.exe now", [r"C:\Program Files\app.exe"])
        self.check("win32", r"open C:\Users\DL\My Notes.txt please", [r"C:\Users\DL\My Notes.txt"])
        self.check("win32", r"open C:\Users\DL\my notes.txt please", [r"C:\Users\DL\my notes.txt"])
        self.check("win32", r"see C:\Users\My Docs\Github\x.txt now", [r"C:\Users\My Docs\Github\x.txt"])
        self.check("linux", "open /Users/My Docs/Github/a.txt now", ["/Users/My Docs/Github/a.txt"])

    def test_two_paths_on_one_line_stay_separate(self):
        self.check("win32", r"C:\a\b then read C:\x\y", [r"C:\a\b", r"C:\x\y"])
        self.check("linux", "/home/user/file then read /tmp/other", ["/home/user/file", "/tmp/other"])
        self.check("linux", "C:/Users/DL/x and /tmp/y", ["C:/Users/DL/x", "/tmp/y"])

    def test_sentence_punctuation_is_not_part_of_the_path(self):
        self.check("win32", r"saved to C:\Users\DL\out.txt.", [r"C:\Users\DL\out.txt"])
        self.check("linux", "look in /tmp/build.", ["/tmp/build"])

    def test_markdown_path_protection_uses_the_same_boundaries(self):
        # renderMarkdown protects these spans from escape handling; prose must stay outside them.
        self.assertEqual(run([["win32", r"in C:\Users\DL\Github 2. start the \new thing", "protect"]])[0],
                         [r"C:\Users\DL\Github"])
        self.assertEqual(run([["win32", r"open C:\Program Files and C:\x\y", "protect"]])[0],
                         [r"C:\Program Files", r"C:\x\y"])

    # The HTML fixtures below are what `marked` produces for the matching Markdown; I checked each
    # against the real parser by hand, since tests here run without a DOM or the CDN script.
    def test_attributes_are_never_rewritten(self):
        for platform, path in (("win32", "C:/Users/DL/file.txt"), ("linux", "/tmp/file.txt")):
            html = f'<img src="https://example.com/image.png" alt="inspect {path} https://evil.example">'
            self.assertEqual(run([[platform, html]])[0], html)
            self.assertEqual(run([[platform, html, "urls"]])[0], html)

    def test_code_stays_literal(self):
        inline = "run <code>C:\\Users\\DL\\file.txt</code> and <code>/tmp/file.txt</code> and <code>https://a.com</code>"
        block = "<pre><code>C:\\Users\\DL\\file.txt\n/tmp/x https://a.com\n</code></pre>"
        for platform in ("win32", "linux"):
            for html in (inline, block):
                self.assertEqual(run([[platform, html]])[0], html)
                self.assertEqual(run([[platform, html, "urls"]])[0], html)

    def test_text_after_code_is_still_linked(self):
        html = run([["win32", "<code>x</code> then C:\\a\\b"]])[0]
        self.assertEqual(linked(html), [r"C:\a\b"])

    def test_unc_text_is_protected_from_escape_handling(self):
        self.assertEqual(run([["win32", r"\\server\new\tests", "protect"]])[0], [r"\\server\new\tests"])
        self.assertEqual(run([["win32", r"open \\files\team share\x", "protect"]])[0], [r"\\files\team"])

    def test_urls_link_only_visible_text(self):
        html = run([["win32", 'see https://a.com/x and <a href="https://b.com">https://b.com</a>', "urls"]])[0]
        self.assertEqual(html.count("<a "), 2)
        self.assertIn('<a href="https://a.com/x" target="_blank" rel="noopener">https://a.com/x</a>', html)
        self.assertIn('<a href="https://b.com" target="_blank" rel="noopener noreferrer">https://b.com</a>', html)

    def test_existing_links_are_not_nested(self):
        html = run([["win32", '<a href="https://x.com/a">C:/Users/a</a> C:\\q\\r']])[0]
        self.assertEqual(html.count("<a "), 2)
        self.assertEqual(linked(html), [r"C:\q\r"])


if __name__ == "__main__":
    unittest.main()
