"""Transport contract for wrapper_unix.inject (Mac/Linux tmux injection).

Background: the wrapper used to deliver a prompt as ONE `tmux send-keys -l`
burst. A pty's raw input queue is finite (1024 bytes on macOS, 4096 on Linux),
so a longer prompt reaches the CLI as several reads. Claude Code classifies a
read above ~800 bytes as a paste and, when typed text follows a paste before
Enter, submits only the typed text: the first 1022 bytes of every long prompt
were silently lost on macOS. Bracketed paste fixes that because the CLI
reassembles one paste between the ESC[200~ / ESC[201~ delimiters regardless of
how the bytes were chunked.

These tests drive a REAL tmux server. The pane runs a raw-mode reader that,
depending on the case, enables bracketed paste mode (DECSET 2004) or not,
deliberately delays its reads so the kernel queue fills, and records the exact
byte stream it received. Assertions are on the reconstructed stream, never on
how many reads the OS chose to make.

Skipped when tmux is not installed. What they do NOT prove: that every CLI
handles a bracketed paste; that is the CLI's contract, verified separately
against Claude Code by transcript.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wrapper_unix  # noqa: E402

OPEN = b"\x1b[200~"
CLOSE = b"\x1b[201~"
HAVE_TMUX = shutil.which("tmux") is not None

# Raw-mode reader that runs INSIDE the tmux pane. argv: mode delay stream reads
READER = textwrap.dedent(r"""
    import os, select, sys, termios, time, tty
    mode, delay, stream_path, reads_path = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    if mode == "bracketed":
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
    with open(reads_path, "w") as f:
        f.write("READY\n")
    time.sleep(delay)                      # let the pty queue fill before the first read
    stream = bytearray()
    reads = []
    deadline = time.time() + 40
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        data = os.read(fd, 65536)
        if not data:
            break
        stream += data
        reads.append(len(data))
        time.sleep(0.05)                   # keep the reader slower than the writer
        body = bytes(stream)
        if b"\r" in body:
            last_open = body.rfind(b"\x1b[200~")
            last_close = body.rfind(b"\x1b[201~")
            if last_open == -1 or last_close > last_open:
                break                      # Enter arrived outside any open paste
    with open(stream_path, "wb") as f:
        f.write(bytes(stream))
    with open(reads_path, "w") as f:
        f.write("\n".join(str(n) for n in reads) + "\nDONE\n")
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
""")


def _payload() -> str:
    """> 4 KB of UTF-8 with non-ASCII, no newlines (the caller flattens)."""
    unit = "use mcp to read #général - you're mentioned; règle 中文 → ok; "
    text = ""
    while len(text.encode("utf-8")) < 4600:
        text += unit
    assert "\n" not in text
    return text


def _wait_for(path: str, marker: bytes, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            with open(path, "rb") as f:
                if marker in f.read():
                    return True
        time.sleep(0.1)
    return False


@unittest.skipIf(sys.platform == "win32", "tmux transport is unix-only")
@unittest.skipUnless(HAVE_TMUX, "tmux not installed")
class InjectTransportTests(unittest.TestCase):
    """Real tmux, real pty, raw-mode reader pane."""

    def _run(self, mode: str, payload: str, delay: float = 1.0):
        session = f"agentchattr-test-inject-{os.getpid()}-{mode}"
        tmp = tempfile.mkdtemp(prefix="agentchattr-inject-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        reader = os.path.join(tmp, "reader.py")
        with open(reader, "w", encoding="utf-8") as f:
            f.write(READER)
        stream = os.path.join(tmp, "stream.bin")
        reads = os.path.join(tmp, "reads.txt")
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50",
             f"{sys.executable} {reader} {mode} {delay} {stream} {reads}"],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(subprocess.run, ["tmux", "kill-session", "-t", session],
                        capture_output=True)
        self.assertTrue(_wait_for(reads, b"READY", 10), "reader pane never became ready")
        time.sleep(0.3)                    # let tmux parse the DECSET before pasting

        ok = wrapper_unix.inject(payload, tmux_session=session, delay=0.1)

        self.assertTrue(_wait_for(reads, b"DONE", 45), "reader never saw Enter")
        with open(stream, "rb") as f:
            data = f.read()
        with open(reads, "r") as f:
            sizes = [int(n) for n in f.read().split() if n.isdigit()]
        return ok, data, sizes

    def test_bracketed_pane_receives_one_framed_paste_then_enter(self):
        payload = _payload()
        ok, data, sizes = self._run("bracketed", payload)
        self.assertEqual(data.count(OPEN), 1, f"exactly one paste start; reads={sizes}")
        self.assertEqual(data.count(CLOSE), 1, f"exactly one paste end; reads={sizes}")
        self.assertEqual(data, OPEN + payload.encode("utf-8") + CLOSE + b"\r",
                         f"stream must be one framed paste then Enter; reads={sizes}")
        self.assertIs(ok, True, "inject must report delivery")

    def test_plain_pane_receives_raw_bytes_then_enter(self):
        payload = _payload()
        ok, data, sizes = self._run("plain", payload)
        self.assertNotIn(OPEN, data, "no delimiters when the app never asked for them")
        self.assertEqual(data, payload.encode("utf-8") + b"\r",
                         f"plain pane must get the raw bytes then Enter; reads={sizes}")
        self.assertIs(ok, True, "inject must report delivery")

    def test_missing_target_returns_false_and_leaves_no_buffer(self):
        ok = wrapper_unix.inject("hello", tmux_session="agentchattr-test-no-such-session",
                                 delay=0.1)
        self.assertIs(ok, False)
        result = subprocess.run(["tmux", "list-buffers", "-F", "#{buffer_name}"],
                                capture_output=True, text=True)
        leftovers = [b for b in result.stdout.split() if b.startswith("agentchattr-inject-")]
        self.assertEqual(leftovers, [], "a failed injection must not leak its paste buffer")


class InjectFailurePathTests(unittest.TestCase):
    """A paste that fails must be visible and must NOT be followed by Enter."""

    def test_failed_paste_sends_no_enter_and_reports(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            verb = argv[1]
            if verb == "display-message":
                return subprocess.CompletedProcess(argv, 0, stdout=b"%7\n", stderr=b"")
            if verb == "paste-buffer":
                return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"can't find pane\n")
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

        with mock.patch.object(wrapper_unix.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(wrapper_unix.time, "sleep"), \
             mock.patch("sys.stdout") as out:
            ok = wrapper_unix.inject("x" * 10, tmux_session="s", delay=0.1)

        self.assertIs(ok, False)
        self.assertFalse(any("Enter" in argv for argv in calls),
                         f"Enter must not be sent after a failed paste: {calls}")
        self.assertTrue(any(argv[1] == "delete-buffer" for argv in calls),
                        f"the paste buffer must be cleaned up: {calls}")
        printed = "".join(str(c.args[0]) for c in out.write.call_args_list)
        self.assertIn("INJECT FAILED", printed, "failure must be visible in the wrapper log")

    def test_exception_launching_paste_is_reported_cleaned_up_and_sends_no_enter(self):
        """An OSError starting tmux (not a non-zero exit) must not escape inject()."""
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            verb = argv[1]
            if verb == "display-message":
                return subprocess.CompletedProcess(argv, 0, stdout=b"%7\n", stderr=b"")
            if verb == "paste-buffer":
                raise OSError("tmux vanished")
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

        with mock.patch.object(wrapper_unix.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(wrapper_unix.time, "sleep"), \
             mock.patch("sys.stdout") as out:
            ok = wrapper_unix.inject("x" * 10, tmux_session="s", delay=0.1)

        self.assertIs(ok, False)
        self.assertFalse(any("Enter" in argv for argv in calls),
                         f"Enter must not be sent after an exception: {calls}")
        self.assertTrue(any(argv[1] == "delete-buffer" for argv in calls),
                        f"best-effort cleanup must still run: {calls}")
        printed = "".join(str(c.args[0]) for c in out.write.call_args_list)
        self.assertIn("INJECT FAILED", printed, "the exception must be visible in the wrapper log")


if __name__ == "__main__":
    unittest.main()
