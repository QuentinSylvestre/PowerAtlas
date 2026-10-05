"""Which processes have a file open, asked of the Windows Restart Manager (Windows only).

Codex holds a thread's writer lock for as long as a process runs that thread, so the
processes that have ``<id>.lock`` open say who runs it. ``find_holders`` is the raw,
blocking lookup (about 0.6 s). ``holders`` is what a request thread calls: it answers from a
cache, queues the lookup for one background worker and returns ``PENDING`` the first time, so
no request waits for the Restart Manager (decisions D8 and D9 of
261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB).

This is a leaf module: it imports the standard library and ``quiet_log`` only, never ``presence``,
``web`` or ``data_codex``. It classifies nothing (a holder is a pid and a start time); what a
holder is, terminal or app, is decided by the caller against the process snapshot. Off Windows
``find_holders`` returns None and ``holders`` answers None, so the caller uses its fall-back.

A Restart Manager call cannot be cancelled from Python, so the damage of a hung one is
bounded instead: a lookup running past ``LOOKUP_WEDGE_SECONDS`` marks its worker abandoned and a
replacement starts; two such stalls in ``WEDGE_WINDOW_SECONDS`` disable the lookup for as long;
at most two abandoned workers may still be blocked. An abandoned worker that returns late
drops its result. No log line carries a pid, a path or a command line.
"""
from __future__ import annotations

import collections
import ctypes
import heapq
import logging
import os
import sys
import tempfile
import threading
import time

from . import quiet_log

log = logging.getLogger(__name__)


class _Pending:
    """The first sighting of a lock path: the lookup is queued and there is no answer yet."""

    def __repr__(self) -> str:
        return "PENDING"


PENDING = _Pending()

LOOKUP_WEDGE_SECONDS = 8.0       # a lookup running this long marks its worker wedged
WEDGE_WINDOW_SECONDS = 600.0     # two wedges within this disable the lookup for as long
MAX_ABANDONED = 2                # abandoned workers still blocked in the call
MIN_LOOKUP_GAP = 1.0             # seconds between two lookups
MAX_QUEUE = 8
MAX_PATHS = 256
MAX_ENTRIES = 64                 # more holders than this is not believed
FAILED_TTL = 30.0                # a failed lookup is cached as unknown this long
GONE_REQUEUE_GAP = 10.0          # a path whose holders are gone re-resolves at most this often
BACKOFF = (30.0, 300.0, 1800.0)  # a path that keeps giving no usable holder
FIRST_RESOLVES = (30.0, 180.0)   # after the first resolution, then every STEADY_SECONDS
STEADY_SECONDS = 300.0
START_TIME_TOLERANCE = 2.0

_ERROR_MORE_DATA = 234
_ERROR_INVALID_PARAMETER = 87
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800
_FILETIME_EPOCH = 116444736000000000  # 100 ns ticks between 1601 and 1970

# Priorities of the queue: a holder that vanished first, then new paths, then the schedule.
_TOP, _FIRST, _SCHEDULED = 0, 1, 2


def _filetime_epoch(low: int, high: int) -> float:
    return ((high << 32 | low) - _FILETIME_EPOCH) / 1e7


# --- The Restart Manager --------------------------------------------------------------------

_lib = None
_lib_failed = False


def _load_library(path: str):
    """Load a DLL by absolute path from the system folder only (a seam for the tests)."""
    return ctypes.WinDLL(path, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)  # type: ignore[attr-defined]


_structs = None


def _structures():
    """The Restart Manager's structures, built once (the same classes everywhere: a ctypes
    array of another class would not match the declared argument types) and only when the
    platform is known to be Windows."""
    global _structs
    if _structs is not None:
        return _structs
    from ctypes import wintypes

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", ctypes.c_wchar * 256),
                    ("strServiceShortName", ctypes.c_wchar * 64), ("ApplicationType", ctypes.c_int),
                    ("AppStatus", ctypes.c_ulong), ("TSSessionId", wintypes.DWORD),
                    ("bRestartable", wintypes.BOOL)]

    _structs = (RM_UNIQUE_PROCESS, RM_PROCESS_INFO)
    return _structs


def _library():
    """The Restart Manager library, or None when it cannot be loaded. Never raises."""
    global _lib, _lib_failed
    if _lib is not None or _lib_failed:
        return _lib
    try:
        from ctypes import wintypes
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        lib = _load_library(os.path.join(root, "System32", "rstrtmgr.dll"))
        _, info = _structures()
        lib.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_wchar_p]
        lib.RmStartSession.restype = wintypes.DWORD
        lib.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                            wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
        lib.RmRegisterResources.restype = wintypes.DWORD
        lib.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                                  ctypes.POINTER(info), ctypes.POINTER(wintypes.DWORD)]
        lib.RmGetList.restype = wintypes.DWORD
        lib.RmEndSession.argtypes = [wintypes.DWORD]
        lib.RmEndSession.restype = wintypes.DWORD
        _lib = lib
    except Exception as exc:
        _lib_failed = True
        quiet_log.warn(log, "lock owner: the Restart Manager could not be loaded", exc)
    return _lib


def find_holders(path: str) -> tuple | None:
    """`((pid, start_epoch), ...)` for every process that has `path` open, or None when the
    lookup could not be made (not Windows, the library did not load, a call failed, more than
    MAX_ENTRIES entries). An empty tuple means no process has it open. Blocking: about 0.6 s.
    The session is always ended."""
    if sys.platform != "win32":
        return None
    lib = _library()
    if lib is None:
        return None
    from ctypes import wintypes
    _unique, info = _structures()
    session = wintypes.DWORD(0)
    key = ctypes.create_unicode_buffer(33)  # CCH_RM_SESSION_KEY + 1
    if lib.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return None
    try:
        names = (ctypes.c_wchar_p * 1)(path)
        if lib.RmRegisterResources(session.value, 1, names, 0, None, 0, None) != 0:
            return None
        capacity = 8
        for _ in range(3):  # ERROR_MORE_DATA tells the size needed: at most three rounds
            needed, count, reasons = wintypes.UINT(0), wintypes.UINT(capacity), wintypes.DWORD(0)
            infos = (info * capacity)()
            code = lib.RmGetList(session.value, ctypes.byref(needed), ctypes.byref(count), infos,
                                 ctypes.byref(reasons))
            if code == 0:
                if count.value > MAX_ENTRIES:
                    return None
                out = []
                for i in range(count.value):
                    proc = infos[i].Process
                    out.append((int(proc.dwProcessId), _filetime_epoch(
                        proc.ProcessStartTime.dwLowDateTime, proc.ProcessStartTime.dwHighDateTime)))
                return tuple(out)
            if code != _ERROR_MORE_DATA or needed.value > MAX_ENTRIES:
                return None
            capacity = max(needed.value, capacity)
        return None
    finally:
        lib.RmEndSession(session.value)


_kernel32 = None


def _process_state(pid: int, start: float) -> str:
    """`alive` (the process exists with this start time), `gone` (no such process, it has
    exited, or the pid now belongs to a newer process) or `unknown` (it cannot be opened, such
    as access denied: the caller keeps what it knew)."""
    global _kernel32
    if sys.platform != "win32":
        return "unknown"
    from ctypes import wintypes
    if _kernel32 is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        _kernel32 = k32
    k32 = _kernel32
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "gone" if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else "unknown"
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return "unknown"
        code = wintypes.DWORD(0)
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            return "gone"
        if abs(_filetime_epoch(created.dwLowDateTime, created.dwHighDateTime) - start) > START_TIME_TOLERANCE:
            return "gone"
        return "alive"
    finally:
        k32.CloseHandle(handle)


# --- The resolver -------------------------------------------------------------------------


class _Entry:
    __slots__ = ("holders", "first_at", "last_at", "resolves", "failed_until", "not_before", "empty_streak")

    def __init__(self) -> None:
        self.holders: tuple | None = None
        self.first_at = 0.0
        self.last_at = 0.0
        self.resolves = 0
        self.failed_until = 0.0
        self.not_before = 0.0
        self.empty_streak = 0


class _Resolver:
    """The cache, the queue and the worker. `lookup(path)` and `alive(pid, start)` and `clock()`
    are seams: the tests inject them and drive `step()` without a thread (`threads=False`)."""

    def __init__(self, lookup=None, alive=None, clock=time.monotonic, threads: bool = True,
                 self_test: bool = True) -> None:
        self._lookup = lookup or find_holders
        self._alive = alive or _process_state
        self._clock = clock
        self._threads = threads
        self._self_test_wanted = self_test
        self._lock = threading.Lock()
        self._cache: collections.OrderedDict[str, _Entry] = collections.OrderedDict()
        self._heap: list[tuple[int, int, str]] = []
        self._queued: dict[str, int] = {}
        self._seq = 0
        self._gen = 0
        self._busy: tuple[int, str, float] | None = None  # (generation, path, started)
        self._abandoned = 0
        self._wedges: collections.deque[float] = collections.deque()
        self._disabled_until = 0.0
        self._self_tested = False
        self._last_lookup = -1e9
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._logged_disabled = False

    # --- the caller's side ---

    def holders(self, path: str):
        now = self._clock()
        with self._lock:
            self._watch(now)
            if self._disabled(now):
                return None
            entry = self._cache.get(path)
            if entry is None:
                self._store_new(path)
                self._enqueue(path, _FIRST)
                self._ensure_worker()
                return PENDING
            self._cache.move_to_end(path)
            if entry.holders is None:
                if now < entry.failed_until:
                    return None
                self._enqueue(path, _FIRST)
                self._ensure_worker()
                return PENDING
            held = entry.holders
            due = self._due(entry, now)
        states = [self._alive(pid, start) for pid, start in held]  # outside the lock
        if "unknown" in states:
            return held  # cannot verify (access denied): keep the answer, queue nothing
        if all(s == "alive" for s in states):
            if due:
                with self._lock:
                    self._enqueue(path, _SCHEDULED)
                    self._ensure_worker()
            return held
        with self._lock:  # a holder is gone: no answer, and look again now (at most every 10 s)
            entry = self._cache.get(path)
            if entry is not None and entry.holders is held:
                entry.holders = None
                again = max(entry.last_at + GONE_REQUEUE_GAP, entry.not_before)
                if now >= again:
                    entry.failed_until = 0.0
                    self._enqueue(path, _TOP)
                    self._ensure_worker()
                else:  # looked up less than 10 s ago: answer unknown, queue nothing until the gap is over
                    entry.failed_until = again
        return None

    def forget(self, path: str) -> None:
        with self._lock:
            self._cache.pop(path, None)
            self._queued.pop(path, None)

    def status(self) -> str:
        now = self._clock()
        with self._lock:
            self._watch(now)
            if self._disabled(now):
                return "disabled"
            if self._abandoned or any(e.not_before > now for e in self._cache.values()):
                return "backing-off"
            return "ok"

    # --- bookkeeping, under the lock ---

    def _disabled(self, now: float) -> bool:
        return now < self._disabled_until or self._abandoned >= MAX_ABANDONED

    def _store_new(self, path: str) -> None:
        self._cache[path] = _Entry()
        while len(self._cache) > MAX_PATHS:  # an evicted held path returns to PENDING
            old, _ = self._cache.popitem(last=False)
            self._queued.pop(old, None)

    def _due(self, entry: _Entry, now: float) -> bool:
        """Whether the schedule asks for a background re-resolve: 30 s and 3 minutes after the
        first resolution, then every 5 minutes after the last one."""
        if entry.resolves <= len(FIRST_RESOLVES):
            return now - entry.first_at >= FIRST_RESOLVES[entry.resolves - 1]
        return now - entry.last_at >= STEADY_SECONDS

    def _enqueue(self, path: str, priority: int) -> None:
        if path in self._queued and self._queued[path] <= priority:
            return
        if len(self._queued) >= MAX_QUEUE and path not in self._queued:
            return  # overflow stays PENDING and is retried after the back-off
        self._seq += 1
        self._queued[path] = priority
        heapq.heappush(self._heap, (priority, self._seq, path))
        self._wake.set()

    def _watch(self, now: float) -> None:
        """Abandon a worker whose lookup has run too long, and apply the wedge rule."""
        busy = self._busy
        if busy is None or busy[0] != self._gen or now - busy[2] < LOOKUP_WEDGE_SECONDS:
            return
        self._gen += 1  # the stuck worker is abandoned: it drops its result if it ever returns
        self._abandoned += 1
        self._busy = None
        self._queued.pop(busy[1], None)
        self._wedges.append(now)
        while self._wedges and now - self._wedges[0] > WEDGE_WINDOW_SECONDS:
            self._wedges.popleft()
        if len(self._wedges) >= 2:
            self._disabled_until = now + WEDGE_WINDOW_SECONDS
            self._self_tested = False
            if not self._logged_disabled:
                self._logged_disabled = True
                quiet_log.warn(log, "lock owner: lookup disabled after repeated stalls")
        elif self._abandoned < MAX_ABANDONED:
            self._worker = None
            self._ensure_worker()

    def _ensure_worker(self) -> None:
        if not self._threads or self._stop.is_set():
            return
        if self._worker is not None and self._worker.is_alive():
            return
        if self._disabled(self._clock()):
            return
        gen = self._gen
        self._worker = threading.Thread(target=self._loop, args=(gen,), name="lock-owner", daemon=True)
        self._worker.start()

    # --- the worker's side ---

    def step(self, now: float | None = None) -> bool:
        """Run at most one queued lookup on the calling thread; True when one ran. The worker
        loop calls this; the tests call it directly."""
        gen = self._gen
        now = self._clock() if now is None else now
        with self._lock:
            if gen != self._gen or self._disabled(now) or now - self._last_lookup < MIN_LOOKUP_GAP:
                return False
            if not self._self_tested and self._self_test_wanted:
                path = None
            else:
                path = None
                while self._heap:
                    _prio, _seq, candidate = heapq.heappop(self._heap)
                    entry = self._cache.get(candidate)
                    if self._queued.get(candidate) is None or entry is None:
                        continue  # forgotten or evicted while queued
                    if entry.not_before > now and _prio != _TOP:
                        self._queued.pop(candidate, None)
                        continue
                    self._queued.pop(candidate, None)
                    path = candidate
                    break
                if path is None:
                    return False
            self._busy = (gen, path or "", now)
        if path is None:
            self._run_self_test(gen, now)
            return True
        try:
            result = self._lookup(path)
        except Exception as exc:
            quiet_log.warn(log, "lock owner: a lookup failed", exc)
            result = None
        self._finish(gen, path, result)
        return True

    def _finish(self, gen: int, path: str, result) -> None:
        now = self._clock()
        with self._lock:
            if gen != self._gen:
                self._abandoned = max(0, self._abandoned - 1)  # an abandoned worker returned late
                if self._abandoned < MAX_ABANDONED and now >= self._disabled_until:
                    self._logged_disabled = False
                return
            self._busy = None
            self._last_lookup = now
            entry = self._cache.get(path)
            if entry is None:
                return
            if result is None:
                entry.holders = None
                entry.failed_until = now + FAILED_TTL
                return
            entry.last_at = now
            if entry.resolves == 0:
                entry.first_at = now
            entry.resolves += 1
            if result:
                entry.holders = result
                entry.empty_streak = 0
                entry.not_before = 0.0
            else:  # nobody has it open: no usable holder, so back off
                entry.holders = None
                entry.failed_until = now + FAILED_TTL
                entry.not_before = now + BACKOFF[min(entry.empty_streak, len(BACKOFF) - 1)]
                entry.empty_streak += 1

    def _run_self_test(self, gen: int, now: float) -> None:
        """On first use: a temporary file this process holds open must list this process."""
        ok = False
        path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as fh:
                path = fh.name
                found = self._lookup(path)
            ok = found is not None and os.getpid() in {pid for pid, _start in found}
        except Exception as exc:
            quiet_log.warn(log, "lock owner: the self-test failed", exc)
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        with self._lock:
            if gen != self._gen:
                self._abandoned = max(0, self._abandoned - 1)
                return
            self._busy = None
            self._last_lookup = self._clock()
            if ok:
                self._self_tested = True
            else:
                self._disabled_until = self._clock() + WEDGE_WINDOW_SECONDS
                quiet_log.warn(log, "lock owner: lookup disabled, the self-test did not list this process")

    def _loop(self, gen: int) -> None:
        while not self._stop.is_set() and gen == self._gen:
            try:
                ran = self.step()
            except Exception as exc:  # the worker must outlive any one failure
                quiet_log.warn(log, "lock owner: the worker failed", exc)
                ran = False
            if not ran:
                self._wake.wait(1.0)
                self._wake.clear()

    def stop(self) -> None:
        """Ask the worker to end (the tests' teardown); a daemon thread, so nothing waits for it."""
        self._stop.set()
        self._wake.set()


_resolver = _Resolver()


def holders(lock_path: str):
    """`((pid, start_epoch), ...)` for a lock path, `None` (unknown: not Windows, lookup
    disabled or failed, no usable holder, a cached holder is gone) or `PENDING` (the first
    sighting: the lookup is queued)."""
    if sys.platform != "win32" and _resolver._lookup is find_holders:
        return None
    return _resolver.holders(lock_path)


def forget(lock_path: str) -> None:
    """Drop what is known about a lock path (it was observed free)."""
    _resolver.forget(lock_path)


def status() -> str:
    """`ok`, `backing-off` (an abandoned worker, or a path that keeps giving no holder) or `disabled`."""
    return _resolver.status()
