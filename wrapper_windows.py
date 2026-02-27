"""Windows agent injection — uses Win32 WriteConsoleInput to type into the agent CLI.

Called by wrapper.py on Windows. Not imported on other platforms.
"""

import ctypes
from ctypes import wintypes
import subprocess
import sys
import time

if sys.platform != "win32":
    raise ImportError("wrapper_windows only works on Windows")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B


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

    # Send Escape first to clear any pending/stacked input in the TUI.
    # Matches the unix fix for Gemini input stacking.
    _write_key(handle, "\x1b", True, vk=VK_ESCAPE, scan=0x01)
    _write_key(handle, "\x1b", False, vk=VK_ESCAPE, scan=0x01)

    for ch in text:
        _write_key(handle, ch, True)
        _write_key(handle, ch, False)

    # Let TUI process the text before sending Enter
    time.sleep(0.3)

    _write_key(handle, "\r", True, vk=VK_RETURN, scan=0x1C)
    _write_key(handle, "\r", False, vk=VK_RETURN, scan=0x1C)


def run_agent(command, extra_args, cwd, env, queue_file, agent, no_restart, start_watcher):
    """Run agent as a direct subprocess, inject via Win32 console."""
    start_watcher(inject)

    while True:
        try:
            proc = subprocess.Popen([command] + extra_args, cwd=cwd, env=env)
            proc.wait()

            if no_restart:
                break

            print(f"\n  {agent.capitalize()} exited (code {proc.returncode}).")
            print("  Restarting in 3s... (Ctrl+C to quit)")
            time.sleep(3)
        except KeyboardInterrupt:
            break
