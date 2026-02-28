"""Windows agent injection — uses Win32 WriteConsoleInput to type into the agent CLI.

Optional: if pywinpty is installed and activity_url/token are set, runs the agent in a
PTY and streams stdout to the chat UI via POST /api/agent-activity.
Called by wrapper.py on Windows. Not imported on other platforms.
"""

import ctypes
import json
from ctypes import wintypes
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

if sys.platform != "win32":
    raise ImportError("wrapper_windows only works on Windows")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
VK_RETURN = 0x0D


class _CHAR_UNION(ctypes.Union):
    _fields_ = [("UnicodeChar", wintypes.WCHAR), ("AsciiChar", wintypes.CHAR)]


class _KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("uChar", _CHAR_UNION),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class _EVENT_UNION(ctypes.Union):
    _fields_ = [("KeyEvent", _KEY_EVENT_RECORD)]


class _INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT_UNION)]


def _write_key(handle, char: str, key_down: bool, vk: int = 0, scan: int = 0):
    rec = _INPUT_RECORD()
    rec.EventType = KEY_EVENT
    evt = rec.Event.KeyEvent
    evt.bKeyDown = key_down
    evt.wRepeatCount = 1
    evt.uChar.UnicodeChar = char
    evt.wVirtualKeyCode = vk
    evt.wVirtualScanCode = scan
    written = wintypes.DWORD(0)
    kernel32.WriteConsoleInputW(handle, ctypes.byref(rec), 1, ctypes.byref(written))


def inject(text: str):
    """Inject text + Enter into the current console via WriteConsoleInput."""
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)

    for ch in text:
        _write_key(handle, ch, True)
        _write_key(handle, ch, False)

    # Let TUI process the text before sending Enter
    time.sleep(0.3)

    _write_key(handle, "\r", True, vk=VK_RETURN, scan=0x1C)
    _write_key(handle, "\r", False, vk=VK_RETURN, scan=0x1C)


# ---------------------------------------------------------------------------
# Activity detection — console screen buffer hashing
# ---------------------------------------------------------------------------

STD_OUTPUT_HANDLE = -11


class _COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _SMALL_RECT(ctypes.Structure):
    _fields_ = [
        ("Left", wintypes.SHORT),
        ("Top", wintypes.SHORT),
        ("Right", wintypes.SHORT),
        ("Bottom", wintypes.SHORT),
    ]


class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", _COORD),
        ("dwCursorPosition", _COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", _SMALL_RECT),
        ("dwMaximumWindowSize", _COORD),
    ]


class _CHAR_INFO(ctypes.Structure):
    _fields_ = [("Char", _CHAR_UNION), ("Attributes", wintypes.WORD)]


kernel32.GetConsoleScreenBufferInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_CONSOLE_SCREEN_BUFFER_INFO),
]
kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL

kernel32.ReadConsoleOutputW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_CHAR_INFO),
    _COORD,
    _COORD,
    ctypes.POINTER(_SMALL_RECT),
]
kernel32.ReadConsoleOutputW.restype = wintypes.BOOL


def get_activity_checker(pid_holder, agent_name="unknown", trigger_flag=None):
    """Return a callable that detects agent activity by diffing visible characters.

    Counts how many visible characters changed since last poll. Filters out
    invisible buffer noise (ConPTY artifacts, cursor jitter, timer ticks) by
    requiring a minimum number of changed cells. Uses hysteresis: goes active
    immediately on significant change, requires sustained quiet to go idle.

    trigger_flag: shared [bool] list — set to [True] by queue watcher when a
    message is injected. Forces active state immediately (covers thinking phase).
    pid_holder: not used for screen hashing, but kept for signature compatibility.
    """
    import array as _array
    import os as _os

    last_chars = [None]  # previous poll's character bytes
    handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    MIN_CHANGED_CELLS = 10  # idle noise is 2-5 cells; real work is 50+
    IDLE_COOLDOWN = 5       # need 5 consecutive idle polls (5s) before going idle
    _consecutive_idle = [0]
    _is_active = [False]

    # Diagnostic log per agent
    _debug_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        f"activity_debug_{agent_name}.log",
    )
    _debug_file = open(_debug_path, "w")
    _poll_count = [0]

    def check():
        _poll_count[0] += 1

        # External trigger: queue watcher injected a message → force active
        triggered = False
        if trigger_flag is not None and trigger_flag[0]:
            trigger_flag[0] = False
            triggered = True
            _consecutive_idle[0] = 0
            _is_active[0] = True

        # Get buffer dimensions
        csbi = _CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi)):
            if triggered:
                import time as _t
                _debug_file.write(
                    f"[{_t.strftime('%H:%M:%S')}] poll={_poll_count[0]} TRIGGERED active=True\n"
                )
                _debug_file.flush()
            return _is_active[0]

        rect = csbi.srWindow
        width = rect.Right - rect.Left + 1
        height = rect.Bottom - rect.Top + 1
        if width <= 0 or height <= 0:
            return _is_active[0]

        # Read visible window
        buffer_size = _COORD(width, height)
        buffer_coord = _COORD(0, 0)
        read_rect = _SMALL_RECT(rect.Left, rect.Top, rect.Right, rect.Bottom)
        char_info_array = (_CHAR_INFO * (width * height))()

        ok = kernel32.ReadConsoleOutputW(
            handle,
            char_info_array,
            buffer_size,
            buffer_coord,
            ctypes.byref(read_rect),
        )
        if not ok:
            return _is_active[0]

        # Extract visible characters only (skip attributes)
        raw = bytes(char_info_array)
        shorts = _array.array("H")
        shorts.frombytes(raw)
        char_data = shorts[::2].tobytes()

        # Count how many characters actually changed
        prev = last_chars[0]
        n_changed = 0
        if prev is not None and len(prev) == len(char_data):
            if prev != char_data:  # fast path: skip counting if identical
                for i in range(0, len(prev), 2):
                    if prev[i:i + 2] != char_data[i:i + 2]:
                        n_changed += 1
        significant = n_changed >= MIN_CHANGED_CELLS
        last_chars[0] = char_data

        # Hysteresis: active immediately on significant change or trigger,
        # idle only after IDLE_COOLDOWN consecutive quiet polls
        if significant or triggered:
            _consecutive_idle[0] = 0
            _is_active[0] = True
        else:
            _consecutive_idle[0] += 1
            if _consecutive_idle[0] >= IDLE_COOLDOWN:
                _is_active[0] = False

        # Log: every poll when cells changed, every 10th poll otherwise, or on trigger
        if n_changed > 0 or _poll_count[0] % 10 == 0 or triggered:
            import time as _t
            extra = " TRIGGERED" if triggered else ""
            _debug_file.write(
                f"[{_t.strftime('%H:%M:%S')}] poll={_poll_count[0]} "
                f"changed={n_changed} significant={significant} "
                f"idle_count={_consecutive_idle[0]} active={_is_active[0]}{extra}\n"
            )
            _debug_file.flush()

        return _is_active[0]

    return check


def _post_activity(activity_url: str, activity_token: str, agent: str, chunk: str, done: bool = False):
    """POST a chunk of agent output to the server for live chat display."""
    if not activity_url or not activity_token:
        return
    try:
        data = json.dumps(
            {"agent": agent, "chunk": chunk, "done": done}
        ).encode("utf-8")
        req = urllib.request.Request(
            activity_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Session-Token": activity_token,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        # Don't break wrapper if server is down
        pass


def run_agent(
    command,
    extra_args,
    cwd,
    env,
    queue_file,
    agent,
    no_restart,
    start_watcher,
    strip_env=None,
    pid_holder=None,
    session_name=None,
    inject_env=None,
    activity_url: str = "",
    activity_token: str = "",
):
    """Run agent as a direct subprocess, inject via Win32 console.

    If activity_url and activity_token are set and pywinpty is available, runs in a PTY
    and streams output to the chat UI.
    """
    if inject_env:
        env = {**env, **inject_env}

    use_pty = bool(activity_url and activity_token)
    pty_fallback = False  # True if we tried PTY but failed and fell back
    pty_process = None
    pty_inject_fn = None

    if use_pty:
        try:
            from winpty import PtyProcess
        except ImportError:
            use_pty = False

    if use_pty:
        def _pty_inject(text: str):
            if pty_process and pty_process.isalive():
                try:
                    pty_process.write(text + "\r\n")
                except Exception:
                    pass

        pty_inject_fn = _pty_inject
        start_watcher(pty_inject_fn)

        def _reader():
            while pty_process and pty_process.isalive():
                try:
                    line = pty_process.readline()
                    if line is None:
                        break
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    line = line.rstrip("\r\n")
                    if line:
                        _post_activity(
                            activity_url,
                            activity_token,
                            agent,
                            line + "\n",
                            done=False,
                        )
                except Exception:
                    break
            _post_activity(activity_url, activity_token, agent, "", done=True)

        while True:
            try:
                pty_process = PtyProcess.spawn(
                    [command] + list(extra_args),
                    cwd=cwd,
                    env=env or None,
                )
                reader_thread = threading.Thread(target=_reader, daemon=True)
                reader_thread.start()
                pty_process.wait()
                reader_thread.join(timeout=2)

                if no_restart:
                    break

                print(
                    f"\n  {agent.capitalize()} exited (code {pty_process.exitstatus})."
                )
                print("  Restarting in 3s... (Ctrl+C to quit)")
                time.sleep(3)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"\n  PTY error: {e}")
                print("  Falling back to console mode (no live stream).")
                pty_fallback = True
                use_pty = False
                break
    else:
        # Default console mode: inject via Win32 console and use screen hashing for activity.
        start_watcher(inject)

    if pty_fallback:
        start_watcher(inject)

    if not use_pty:
        while True:
            try:
                proc = subprocess.Popen(
                    [command] + list(extra_args),
                    cwd=cwd,
                    env=env,
                )
                if pid_holder is not None:
                    pid_holder[0] = proc.pid
                proc.wait()
                if pid_holder is not None:
                    pid_holder[0] = None

                if no_restart:
                    break

                print(
                    f"\n  {agent.capitalize()} exited (code {proc.returncode})."
                )
                print("  Restarting in 3s... (Ctrl+C to quit)")
                time.sleep(3)
            except KeyboardInterrupt:
                break

