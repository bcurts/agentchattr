"""Windows agent injection — uses Win32 WriteConsoleInput to type into the agent CLI.

Called by wrapper.py on Windows. Not imported on other platforms.
"""

import ctypes
from ctypes import wintypes
import subprocess
import sys
import threading
import time
from pathlib import Path

if sys.platform != "win32":
    raise ImportError("wrapper_windows only works on Windows")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE_FOR_VT = -11  # kept distinct from STD_OUTPUT_HANDLE below to avoid forward-ref
KEY_EVENT = 0x0001
VK_RETURN = 0x0D
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
_console_lock = threading.RLock()
_console_generation = 0

# Console-mode bits for SetConsoleMode (Windows Console API)
ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Window message constants used by the wm_setfocus Enter backend.
WM_SETFOCUS = 0x0007
WM_ACTIVATE = 0x0006
WA_ACTIVE = 1


def enable_vt_mode(verbose: bool = True):
    """Enable virtual terminal processing on the underlying console.

    Newer TUI agents (codex, claude, etc.) emit ANSI escape sequences directly
    rather than calling SetConsoleMode themselves. Without VT processing enabled,
    sequences like `?2026h` (synchronized output) leak as literal text and the
    UI is unreadable.

    We open CONOUT$/CONIN$ directly via CreateFileW rather than going through the
    inherited STDOUT handle — this way, even if Python's stdio has been
    redirected through pipes (or a Node/Rust child later reopens its own handle
    to the console), the underlying conhost device gets the mode flipped.

    Do not enable ENABLE_VIRTUAL_TERMINAL_INPUT here. Codex's Windows TUI reads
    keyboard events from the console; forcing VT input changes how Enter and
    other keys are delivered, which can make both manual Enter and injected
    Enter stop submitting messages.

    Safe to call multiple times. Failures are logged but not fatal.
    """
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    # Set explicit signatures so HANDLE doesn't get truncated to 32 bits on x64.
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3

    targets = (
        ("CONOUT$", GENERIC_READ | GENERIC_WRITE,
         ENABLE_VIRTUAL_TERMINAL_PROCESSING | ENABLE_PROCESSED_OUTPUT, 0, "stdout"),
        ("CONIN$",  GENERIC_READ | GENERIC_WRITE,
         0, ENABLE_VIRTUAL_TERMINAL_INPUT, "stdin"),
    )

    for device, access, add_bits, clear_bits, label in targets:
        handle = kernel32.CreateFileW(
            device, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            if verbose:
                print(f"  [wrapper] VT enable ({label}): could not open {device}", flush=True)
            continue
        try:
            mode = wintypes.DWORD(0)
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                if verbose:
                    print(f"  [wrapper] VT enable ({label}): GetConsoleMode failed", flush=True)
                continue
            before = mode.value
            new_mode = (before | add_bits) & ~clear_bits
            if new_mode == before:
                if verbose:
                    print(f"  [wrapper] VT enable ({label}): already 0x{before:04x}", flush=True)
                continue
            ok = kernel32.SetConsoleMode(handle, new_mode)
            if verbose:
                status = "ok" if ok else "FAILED"
                print(f"  [wrapper] VT enable ({label}): 0x{before:04x} -> 0x{new_mode:04x} [{status}]", flush=True)
        finally:
            kernel32.CloseHandle(handle)


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
    ok = kernel32.WriteConsoleInputW(handle, ctypes.byref(rec), 1, ctypes.byref(written))
    return bool(ok and written.value == 1)


def _current_console_contains_pid(pid: int) -> bool:
    """Return whether *pid* shares the wrapper's currently attached console."""
    kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    kernel32.GetConsoleProcessList.restype = wintypes.DWORD
    capacity = 64
    while True:
        process_ids = (wintypes.DWORD * capacity)()
        count = kernel32.GetConsoleProcessList(process_ids, capacity)
        if count == 0:
            return False
        if count <= capacity:
            return pid in process_ids[:count]
        capacity = count


def _ensure_agent_console(pid: int | None, *, retries: int = 5, delay: float = 0.05) -> tuple[bool, str]:
    """Attach the wrapper to the agent console without disturbing shared consoles."""
    global _console_generation
    with _console_lock:
        if not pid:
            return False, "agent PID is not available"
        if _current_console_contains_pid(pid):
            return True, "already attached"

        kernel32.FreeConsole.argtypes = []
        kernel32.FreeConsole.restype = wintypes.BOOL
        kernel32.AttachConsole.argtypes = [wintypes.DWORD]
        kernel32.AttachConsole.restype = wintypes.BOOL
        last_error = 0
        for attempt in range(max(1, retries)):
            if kernel32.FreeConsole():
                _console_generation += 1
            if kernel32.AttachConsole(pid):
                _console_generation += 1
                return True, "attached"
            last_error = ctypes.get_last_error()
            if attempt + 1 < retries:
                time.sleep(delay)
        return False, f"AttachConsole({pid}) failed (winerror={last_error})"


def _open_console_input():
    """Open the input buffer for the console currently attached to the wrapper."""
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.WriteConsoleInputW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_INPUT_RECORD),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.WriteConsoleInputW.restype = wintypes.BOOL
    return kernel32.CreateFileW(
        "CONIN$",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )


def _open_console_output():
    """Open the current console output buffer, or return None if unavailable."""
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        "CONOUT$",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        return None
    return handle


def _report_injection_failure(
    detail: str,
    *,
    agent_pid: int | None,
    diagnostic_file: Path | None,
) -> None:
    message = f"[wrapper] Injection failed for pid={agent_pid or 'unknown'}: {detail}"
    print(f"  {message}", flush=True)
    if diagnostic_file is not None:
        try:
            diagnostic_file.parent.mkdir(parents=True, exist_ok=True)
            with diagnostic_file.open("a", encoding="utf-8") as log:
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass


def _send_wm_setfocus():
    """Tell the console window it just received focus — some Node TUIs
    (GitHub Copilot CLI) gate Enter processing on focus state, so this
    makes them accept injected Enter without an actual focus change."""
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        return
    user32.SendMessageW(hwnd, WM_SETFOCUS, 0, 0)
    user32.SendMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)


def inject(
    text: str,
    *,
    delay: float = 0.3,
    enter_backend: str = "console_input",
    agent_pid: int | None = None,
    diagnostic_file: Path | None = None,
) -> bool:
    """Inject text + Enter into the current console via WriteConsoleInput.

    Uses batch WriteConsoleInputW for the text (all records in one call)
    then a separate Enter keystroke after a scaled delay.

    `enter_backend` controls how the final Enter is delivered:
      - "console_input" (default): standard WriteConsoleInput + VK_RETURN.
        Works for Claude/Codex/Gemini/Kimi/Qwen/Kilo/etc.
      - "wm_setfocus": fake-focus message (WM_SETFOCUS + WM_ACTIVATE) to
        the console window before sending VK_RETURN. Needed for GitHub
        Copilot CLI, whose Ink-based input layer ignores Enter events
        when the console window is unfocused.
    """
    with _console_lock:
        attached, detail = _ensure_agent_console(agent_pid)
        if not attached:
            _report_injection_failure(detail, agent_pid=agent_pid, diagnostic_file=diagnostic_file)
            return False
        handle = _open_console_input()
    if not handle or handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        _report_injection_failure(
            f"could not open CONIN$ (winerror={error})",
            agent_pid=agent_pid,
            diagnostic_file=diagnostic_file,
        )
        return False

    # Build all key events at once (key down + key up per character)
    n_events = len(text) * 2
    if n_events > 0:
        records = (_INPUT_RECORD * n_events)()
        idx = 0
        for ch in text:
            for key_down in (True, False):
                rec = records[idx]
                rec.EventType = KEY_EVENT
                evt = rec.Event.KeyEvent
                evt.bKeyDown = key_down
                evt.wRepeatCount = 1
                evt.uChar.UnicodeChar = ch
                evt.wVirtualKeyCode = 0
                evt.wVirtualScanCode = 0
                idx += 1
        written = wintypes.DWORD(0)
        ok = kernel32.WriteConsoleInputW(handle, records, n_events, ctypes.byref(written))
        if not ok or written.value != n_events:
            error = ctypes.get_last_error()
            _report_injection_failure(
                f"wrote {written.value}/{n_events} input events (winerror={error})",
                agent_pid=agent_pid,
                diagnostic_file=diagnostic_file,
            )
            kernel32.CloseHandle(handle)
            return False

    # Scale delay with text length so longer prompts get more processing time
    scaled_delay = max(delay, len(text) * 0.001)
    time.sleep(scaled_delay)

    if enter_backend == "wm_setfocus":
        _send_wm_setfocus()
        # Tiny pause for the window to process the focus message
        time.sleep(0.05)

    enter_ok = _write_key(handle, "\r", True, vk=VK_RETURN, scan=0x1C)
    enter_ok = _write_key(handle, "\r", False, vk=VK_RETURN, scan=0x1C) and enter_ok
    kernel32.CloseHandle(handle)
    if not enter_ok:
        error = ctypes.get_last_error()
        _report_injection_failure(
            f"could not write Enter (winerror={error})",
            agent_pid=agent_pid,
            diagnostic_file=diagnostic_file,
        )
        return False
    return True


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
    pid_holder: retained for signature compatibility with other activity backends.
    """
    import array as _array
    import os as _os

    last_chars = [None]  # previous poll's character bytes
    handle = [None]
    handle_generation = [-1]
    MIN_CHANGED_CELLS = 10  # idle noise is 2-5 cells; real work is 50+
    IDLE_COOLDOWN = 5       # need 5 consecutive idle polls (5s) before going idle
    _consecutive_idle = [0]
    _is_active = [False]

    def advance_idle() -> bool:
        _consecutive_idle[0] += 1
        if _consecutive_idle[0] >= IDLE_COOLDOWN:
            _is_active[0] = False
        return _is_active[0]

    def close_output_handle() -> None:
        if handle[0] is not None:
            kernel32.CloseHandle(handle[0])
            handle[0] = None
        handle_generation[0] = -1
        last_chars[0] = None

    def check():
        # External trigger: queue watcher injected a message → force active
        triggered = False
        if trigger_flag is not None and trigger_flag[0]:
            trigger_flag[0] = False
            triggered = True
            _consecutive_idle[0] = 0
            _is_active[0] = True

        # Lazily open CONOUT$ after the wrapper has attached to the agent's
        # console. Reopen it whenever injection switches console generations.
        with _console_lock:
            if handle[0] is not None and handle_generation[0] != _console_generation:
                close_output_handle()
            if handle[0] is None:
                handle[0] = _open_console_output()
                if handle[0] is None:
                    return advance_idle()
                handle_generation[0] = _console_generation

            # Get buffer dimensions
            csbi = _CONSOLE_SCREEN_BUFFER_INFO()
            if not kernel32.GetConsoleScreenBufferInfo(handle[0], ctypes.byref(csbi)):
                close_output_handle()
                return advance_idle()

            rect = csbi.srWindow
            width = rect.Right - rect.Left + 1
            height = rect.Bottom - rect.Top + 1
            if width <= 0 or height <= 0:
                close_output_handle()
                return advance_idle()

            # Read visible window
            buffer_size = _COORD(width, height)
            buffer_coord = _COORD(0, 0)
            read_rect = _SMALL_RECT(rect.Left, rect.Top, rect.Right, rect.Bottom)
            char_info_array = (_CHAR_INFO * (width * height))()

            ok = kernel32.ReadConsoleOutputW(
                handle[0], char_info_array, buffer_size, buffer_coord,
                ctypes.byref(read_rect),
            )
            if not ok:
                close_output_handle()
                return advance_idle()

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
                    if prev[i:i+2] != char_data[i:i+2]:
                        n_changed += 1
        significant = n_changed >= MIN_CHANGED_CELLS
        last_chars[0] = char_data

        # Hysteresis: active immediately on significant change or trigger,
        # idle only after IDLE_COOLDOWN consecutive quiet polls
        if significant or triggered:
            _consecutive_idle[0] = 0
            _is_active[0] = True
        else:
            return advance_idle()

        return _is_active[0]

    return check


def _vt_keepalive_thread():
    """Re-assert VT mode every 10ms in case the child clears it.

    Newer codex builds appear to call SetConsoleMode themselves around their
    frame draws, sometimes stripping ENABLE_VIRTUAL_TERMINAL_PROCESSING in the
    process. A one-shot enable at launch wins the first frame then loses; a
    slow keepalive wins most frames but leaks during redraws. We need to win
    the race, so we hammer SetConsoleMode at 10ms intervals.

    Single long-lived CONOUT$ handle (opened once) means each iteration is just
    one SetConsoleMode syscall — ~100 per second, trivial cost.
    """
    import threading as _threading
    import time as _time

    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    REQUIRED_BITS = ENABLE_VIRTUAL_TERMINAL_PROCESSING | ENABLE_PROCESSED_OUTPUT

    out_handle = kernel32.CreateFileW(
        "CONOUT$", GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if not out_handle or out_handle == INVALID_HANDLE_VALUE:
        return  # no console — nothing to keep alive

    def _loop():
        mode = wintypes.DWORD(0)
        # Tight initial burst (1ms × ~500 iters ≈ 0.5s) to win the race against
        # the child's startup-time SetConsoleMode calls, then settle to a slower
        # steady-state rate.
        for _ in range(500):
            try:
                if kernel32.GetConsoleMode(out_handle, ctypes.byref(mode)):
                    if (mode.value & REQUIRED_BITS) != REQUIRED_BITS:
                        kernel32.SetConsoleMode(out_handle, mode.value | REQUIRED_BITS)
            except Exception:
                pass
            _time.sleep(0.001)
        while True:
            try:
                if kernel32.GetConsoleMode(out_handle, ctypes.byref(mode)):
                    if (mode.value & REQUIRED_BITS) != REQUIRED_BITS:
                        kernel32.SetConsoleMode(out_handle, mode.value | REQUIRED_BITS)
            except Exception:
                pass
            _time.sleep(0.01)

    t = _threading.Thread(target=_loop, daemon=True, name="vt-keepalive")
    t.start()


def run_agent(command, extra_args, cwd, env, queue_file, agent, no_restart, start_watcher, strip_env=None, pid_holder=None, session_name=None, inject_env=None, inject_delay: float = 0.3, enter_backend: str = "console_input"):
    """Run agent as a direct subprocess, inject via Win32 console."""
    # Newer codex/claude/etc TUIs require VT processing on the parent console;
    # without this, ANSI escape sequences leak as text into the terminal.
    # One-shot at startup (with diagnostic print) then a keepalive thread.
    enable_vt_mode()
    _vt_keepalive_thread()

    if inject_env:
        env = {**env, **inject_env}
    if pid_holder is None:
        pid_holder = [None]
    diagnostic_file = None
    if queue_file is not None:
        queue_path = Path(queue_file)
        diagnostic_file = queue_path.with_name(f"{queue_path.stem}_injection.log")
    watcher_started = False

    while True:
        try:
            proc = subprocess.Popen([command] + extra_args, cwd=cwd, env=env)
            pid_holder[0] = proc.pid
            if not watcher_started:
                start_watcher(
                    lambda text: inject(
                        text,
                        delay=inject_delay,
                        enter_backend=enter_backend,
                        agent_pid=pid_holder[0],
                        diagnostic_file=diagnostic_file,
                    )
                )
                watcher_started = True
            proc.wait()
            pid_holder[0] = None

            if no_restart:
                break

            print(f"\n  {agent.capitalize()} exited (code {proc.returncode}).")
            print(f"  Restarting in 3s... (Ctrl+C to quit)")
            time.sleep(3)
        except KeyboardInterrupt:
            break
