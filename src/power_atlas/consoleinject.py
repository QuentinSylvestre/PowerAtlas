"""Type text into another process's console, as if it had been typed at the keyboard. Windows only.

Used to resume a Claude Code session whose terminal is still open: a hit leaves the TUI waiting at its
prompt, and the only way to answer it is to type. This module is the whole of that capability, kept small
on purpose.

**It runs in its own short-lived process** (`python -m power_atlas.consoleinject`, JSON on stdin, JSON on
stdout). `AttachConsole` and `FreeConsole` change the console of the *process*, not the thread, so doing
this inside the PowerAtlas server would move every other thread's console handles with it. The helper
has no other work to disturb.

**Guards, all in this module so no caller can skip them:**

* the target must be a running process whose image is `claude.exe` (`ALLOWED_IMAGES`). The names are a
  constant here and are not read from the request; only `inject()`'s keyword, which the tests use, can
  widen them. The caller decides *which* claude.exe (the one presence validated for the session);
  this module only refuses anything that is not one;
* the text is refused when it holds a control character, is empty, or is long;
* nothing is typed unless the console could be attached, and the Enter key is a second write after a
  pause, because a text-and-Enter burst is read by Claude Code as a paste and not submitted (measured
  2026-10-09 against `claude.exe` 2.1.296). A write that Windows accepted only in part raises, and the
  Enter is not sent after it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

ALLOWED_IMAGES = ("claude.exe",)
MAX_TEXT_CHARS = 4000
DEFAULT_ENTER_DELAY = 0.4
# C0 controls, DEL, NEL, LS, PS: anything that would type a key other than a character.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\x85  ]")

_KEY_EVENT = 0x0001
_VK_RETURN = 0x0D
_SCAN_RETURN = 0x1C
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GENERIC_READ_WRITE = 0x80000000 | 0x40000000
_SHARE_READ_WRITE = 3
_OPEN_EXISTING = 3


class InjectError(Exception):
    """The text was not typed; the message says why, in words a user can read."""


def check_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise InjectError("There is nothing to type")
    if len(text) > MAX_TEXT_CHARS:
        raise InjectError("The text is too long to type")
    if _CONTROL_RE.search(text):
        raise InjectError("The text holds a control character")
    return text


def _kernel32():
    """kernel32 with every signature declared. Without `argtypes` and `restype` ctypes treats handles as
    32-bit ints: a 64-bit handle would be truncated, and `INVALID_HANDLE_VALUE` would never compare equal."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.AttachConsole.argtypes = [wintypes.DWORD]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD)]
    return k32


def image_name(pid: int) -> str | None:
    """The full path of the running process `pid`, or None."""
    import ctypes
    from ctypes import wintypes
    k32 = _kernel32()
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        return buf.value if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
    finally:
        k32.CloseHandle(handle)


def inject(pid: int, text: str, *, enter_delay: float = DEFAULT_ENTER_DELAY,
           allowed_images: tuple[str, ...] = ALLOWED_IMAGES) -> int:
    """Type `text` and then Enter into the console `pid` is attached to; returns the records written.

    Raises `InjectError` before anything is typed when the text or the target is not acceptable.
    """
    if sys.platform != "win32":
        raise InjectError("Typing into a terminal is only available on Windows")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise InjectError("That is not a process id")
    text = check_text(text)
    image = image_name(pid)
    if not image:
        raise InjectError("The session's process is no longer running")
    if os.path.basename(image).lower() not in tuple(a.lower() for a in allowed_images):
        raise InjectError("That process is not Claude Code, so nothing was typed")

    import ctypes
    from ctypes import wintypes

    class KeyEvent(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                    ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                    ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class EventUnion(ctypes.Union):
        # The padding gives the union the size of the largest console event record.
        _fields_ = [("KeyEvent", KeyEvent), ("_pad", ctypes.c_byte * 16)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EventUnion)]

    def key(ch, down, vk=0, scan=0):
        rec = InputRecord()
        rec.EventType = _KEY_EVENT
        k = rec.Event.KeyEvent
        k.bKeyDown, k.wRepeatCount, k.wVirtualKeyCode, k.wVirtualScanCode, k.uChar, k.dwControlKeyState = (
            1 if down else 0, 1, vk, scan, ch, 0)
        return rec

    k32 = _kernel32()
    invalid = ctypes.c_void_p(-1).value

    def write(handle, records) -> int:
        arr = (InputRecord * len(records))(*records)
        written = wintypes.DWORD(0)
        if not k32.WriteConsoleInputW(handle, arr, len(records), ctypes.byref(written)):
            raise InjectError("Windows refused the keystrokes (error %d)" % ctypes.get_last_error())
        if written.value != len(records):
            raise InjectError("Windows accepted only part of the keystrokes")
        return written.value

    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        raise InjectError("Could not reach that session's terminal (error %d)" % ctypes.get_last_error())
    try:
        handle = k32.CreateFileW("CONIN$", _GENERIC_READ_WRITE, _SHARE_READ_WRITE, None, _OPEN_EXISTING, 0, None)
        if handle is None or handle == invalid:
            raise InjectError("Could not open that session's terminal input (error %d)" % ctypes.get_last_error())
        try:
            records = []
            for ch in text:
                records += [key(ch, True), key(ch, False)]
            total = write(handle, records)
            time.sleep(max(0.0, float(enter_delay)))
            total += write(handle, [key("\r", True, _VK_RETURN, _SCAN_RETURN),
                                    key("\r", False, _VK_RETURN, _SCAN_RETURN)])
            return total
        finally:
            k32.CloseHandle(handle)
    finally:
        k32.FreeConsole()


def main() -> int:
    """Entry point of the helper process: `{"pid": n, "text": "..."}` on stdin, one JSON line out."""
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise InjectError("Bad request")
        records = inject(request.get("pid"), request.get("text"))
        out = {"ok": True, "records": records}
    except InjectError as exc:
        out = {"ok": False, "error": str(exc)}
    except Exception as exc:  # the caller shows this text; keep it short and free of paths
        out = {"ok": False, "error": "Typing failed (%s)" % type(exc).__name__}
    sys.stdout.write(json.dumps(out) + "\n")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
