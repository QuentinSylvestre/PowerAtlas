"""OpenAI Codex session adapter: discovery, parsing, and caching.

Codex keeps one thread per file under ``~/.codex/sessions/YYYY/MM/DD/``
(``rollout-<local timestamp>-<uuid>.jsonl``). This adapter reads those rollout
files directly (decisions D4 and D6 of
261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1): the rollout
is the canonical record, and the SQLite catalogue next to it is a private,
versioned projection that is read only by ``data_codex_state``, as optional
enrichment (261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB
Phase 1); nothing here reads it.

OpenAI publishes no stability guarantee for the rollout format, and the files
are rewritten in place (compaction), can be torn mid-write, and reach tens of
MB. So nothing here assumes a file only grows, every read is bounded, every
record is type-checked, and no public function raises (D10, D13, D27):

* a parse result is cached by the file's (mtime_ns, size), never by mtime
  alone, because Windows freezes the mtime of a file Codex holds open;
* head, tail and transcript reads are capped (see the ``_*_CAP`` constants);
* a line over 256 KiB is skipped without being parsed, and the lines after it
  are still read;
* every rollout is opened through ``open_shared`` so another process can
  rename or delete it while a reader is open (Windows ``FILE_SHARE_DELETE``).

This module must not import ``overview``, ``web`` or ``presence``.
"""

import errno
import functools
import json
import logging
import os
import re
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .data import (
    SESSION_ID_RE, UUID_RE, BoundedCache, Session, TranscriptEvent, _FileInfo, _cap_text,
    _normalize_path,
)

log = logging.getLogger(__name__)


# --- Roots (D26) ---------------------------------------------------------------
# Derived once at import from CODEX_HOME (Codex's own relocation variable, read
# from its source in 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 0)
# else ~/.codex, into three module-level constants that tests redirect. Every
# function reads them at call time.

def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME", "")
    if env.strip():
        return Path(env)
    return Path.home() / ".codex"


_CODEX_HOME = _codex_home()
CODEX_SESSIONS_DIR = _CODEX_HOME / "sessions"
CODEX_SESSION_INDEX = _CODEX_HOME / "session_index.jsonl"
# Read by the writer-lock probe (session_writer_locked); tests redirect it.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3
CODEX_LOCKS_DIR = _CODEX_HOME / "thread-writer-locks"


# --- Bounds (D10, D13) ----------------------------------------------------------

_META_CAP = 256 * 1024            # first-line (session_meta) read
_LINE_CAP = 256 * 1024            # a longer line is skipped unparsed
_HEAD_CAP = 1024 * 1024           # head scan for the first real prompt
_TAIL_START = 256 * 1024          # first tail window ...
_TAIL_MAX = 2 * 1024 * 1024       # ... widening up to this much
_TRANSCRIPT_WINDOW = 8 * 1024 * 1024
_TRANSCRIPT_MAX_EVENTS = 3000
_TRANSCRIPT_KEYS = (b"item_completed", b"function_call", b"custom_tool_call")
_ARG_CHARS = 2000                 # per string in a tool call's arguments
_ARG_ITEMS = 50                   # per list or dict in a tool call's arguments
_INDEX_CAP = 8 * 1024 * 1024      # session_index.jsonl
_CHUNK = 64 * 1024
_OVERSIZE_HEAD = 2048             # bytes of a skipped oversize line shown to a notice hook
_OVERSIZE_NOTICES = 5             # per transcript
_CACHE_MIN = 4096

_STORE_TTL = 5.0                  # seconds the store index is reused (D11)
_AVAILABLE_TTL = 5.0
_MISSING_TTL = 60.0
_WARN_INTERVAL = 60.0

_ROLLOUT_RE = re.compile(r"rollout-.+-(" + UUID_RE.pattern + r")\.jsonl", re.I)
_NAME_CAP = 200                   # a thread name from session_index.jsonl, as the first-prompt cut
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003   # an NTFS junction (or a volume mount point)
# Bounded: a 4300-digit number makes int() raise, and a number that long is not an
# exit code. `(?!\d)` makes an over-long number no match at all (unknown), instead of
# its first nine digits.
_EXIT_CODE_RE = re.compile(r"Exit code: (-?\d{1,9})(?!\d)")
# Leading markers of an `exec` tool output (plan D13 as amended 2026-10-01).
EXEC_RUNNING = "Script running with cell"
_EXEC_MARKERS = (("Script completed", True), ("Script failed", False), ("aborted by user", False))

# A rollout whose parse fails with an OSError (a sharing violation on an idle file)
# is retried by later refresh polls, at most _RETRY_MAX times and no closer together
# than _RETRY_SPACING seconds.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, finding 21.
_RETRY_MAX = 3
_RETRY_SPACING = 5.0


# --- Failure isolation (D27) ----------------------------------------------------

_warn_lock = threading.Lock()
_warned: dict[str, float] = {}
_UUID_ANY_RE = re.compile(UUID_RE.pattern, re.I)


def redact_path(path: str) -> str:
    """`path` with the thread id of its file name hidden, for a log line: the
    folder and the timestamp in a rollout name still say which file it is."""
    path = os.fspath(path)
    name = os.path.basename(path)
    return path[:len(path) - len(name)] + _UUID_ANY_RE.sub("<id>", name)


def log_path(path: str) -> str:
    """`redact_path` for a rollout (`rollout-*`) name, `path` unchanged for any other.
    Overview log lines name files of every provider; only Codex's are redacted."""
    path = os.fspath(path)
    return redact_path(path) if os.path.basename(path).startswith("rollout-") else path


def _warn(kind: str, exc: BaseException | None = None, path: str = "", note: str = "") -> None:
    """One warning per kind per minute: the exception type, the path when known
    (an OSError carries its own), and never message content."""
    now = time.monotonic()
    with _warn_lock:
        last = _warned.get(kind)
        if last is not None and now - last < _WARN_INTERVAL:
            return
        _warned[kind] = now
    if not path and exc is not None:
        filename = getattr(exc, "filename", None)
        if isinstance(filename, (str, bytes, os.PathLike)):
            path = os.fsdecode(filename)
    what = f"failed ({type(exc).__name__})" if exc is not None else note
    log.warning("codex adapter: %s %s%s", kind, what, f" path={redact_path(path)}" if path else "")


def _safe(kind: str, neutral, path_arg: bool = False):
    """Make a public function total: any Exception is logged and `neutral()` returned.

    `path_arg` marks a function whose first argument is a rollout path, which the
    warning then names.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # RecursionError included
                path = ""
                if path_arg and args:
                    try:
                        path = os.fspath(args[0])
                    except TypeError:
                        pass
                _warn(kind, exc, path)
                return neutral()
        return wrapper
    return decorate


# --- Shared-delete open (D10) ---------------------------------------------------

_win_api = None


def _win32():
    """Lazily bind the Windows calls; a top-level msvcrt or ctypes.wintypes
    import would break import on POSIX (D23)."""
    global _win_api
    if _win_api is None:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        k32.CreateFileW.restype = ctypes.c_void_p  # a 64-bit HANDLE; c_int would truncate it
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = wintypes.BOOL
        _win_api = (ctypes, msvcrt, k32, ctypes.c_void_p(-1).value)
    return _win_api


def open_shared(path, mode: str = "rb"):
    """Open a Codex file so another process can still rename or delete it.

    CPython's ``open()`` on Windows omits ``FILE_SHARE_DELETE``, so a plain
    reader blocks Codex's own ``remove_file`` of a rollout and of a lock file
    for as long as the handle lives (measured on a scratch copy in
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 0). On
    Windows this calls ``CreateFileW`` with READ|WRITE|DELETE sharing and wraps
    the handle; elsewhere it is ``os.open``. Both go through ``os.fdopen``,
    never ``builtins.open``, so a test can forbid ``builtins.open`` for Codex
    paths. Never creates a file. Handles must be short-lived and never cached:
    a replace over an open target still fails even with DELETE sharing.

    ``mode`` is ``"rb"`` only: nothing writes a Codex file, and the lock files are
    locked through a read-only handle too. Raises OSError, like ``open``, and
    ValueError for any other mode.
    """
    if mode != "rb":
        raise ValueError(f"unsupported mode {mode!r}")
    target = os.fspath(path)
    if sys.platform == "win32":
        ctypes, msvcrt, k32, invalid = _win32()
        handle = k32.CreateFileW(target, 0x80000000, 0x7, None, 3, 0x80, None)  # GENERIC_READ, share R|W|D, OPEN_EXISTING
        if handle is None or handle == invalid:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            k32.CloseHandle(handle)
            raise
    else:
        fd = os.open(target, os.O_RDONLY)
    try:
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


# --- Line readers ---------------------------------------------------------------
# Public on purpose, because overview.py uses them: loads, read_first_line, item_of, item_text, output_text, exit_success, fit_caches and FUTURE_SKEW. 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4


def loads(raw: bytes):
    """Parse one line. None for anything unparseable, deeply nested JSON included.
    A leading UTF-8 byte-order mark is dropped (``utf-8-sig``): a first line that starts
    with one is still a ``session_meta`` or an index entry, not a vanished session."""
    try:
        return json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception:
        return None


def _iter_lines(fh, budget: int, skip_partial: bool = False, eof_at_budget: bool = False,
                on_oversize=None):
    """Yield complete lines forward from the current position, newest bytes last.

    Reads at most `budget` bytes in chunks. A line over _LINE_CAP is skipped
    without being yielded, and reading resumes at the next newline;
    `on_oversize(head)` is called with its first _OVERSIZE_HEAD bytes. With
    `skip_partial` the bytes up to the first newline are dropped (a window that
    starts mid-line). A line cut off by the end of the budget is dropped, unless
    the caller says the budget ends at the end of the file (`eof_at_budget`): then,
    like a line cut off by a real end of file (no trailing newline), it is yielded
    and the parser decides whether it is complete.
    """
    carry = b""
    skipping = skip_partial
    consumed = 0
    at_eof = False
    while consumed < budget:
        chunk = fh.read(min(_CHUNK, budget - consumed))
        if not chunk:
            at_eof = True
            break
        consumed += len(chunk)
        if skipping:
            nl = chunk.find(b"\n")
            if nl < 0:
                continue
            chunk = chunk[nl + 1:]
            skipping = False
        parts = (carry + chunk).split(b"\n")
        carry = parts.pop()
        for part in parts:
            if not part:
                continue
            if len(part) <= _LINE_CAP:
                yield part
            elif on_oversize is not None:
                on_oversize(part[:_OVERSIZE_HEAD])
        if len(carry) > _LINE_CAP:
            if on_oversize is not None:
                on_oversize(carry[:_OVERSIZE_HEAD])
            carry = b""
            skipping = True
    if (at_eof or eof_at_budget) and not skipping and carry:
        yield carry


def _iter_lines_reverse(fh, size: int):
    """Yield complete lines newest first from a tail window.

    The window starts at _TAIL_START bytes and widens (doubling the total) up to
    _TAIL_MAX, only as far as the consumer keeps iterating. Each widening reads
    just the new bytes, so the whole walk reads at most _TAIL_MAX. The partial
    line at the start of the window is held back until the next widening
    completes it, and dropped when the cap is reached.
    """
    pos = size
    buf = b""
    read_total = 0
    step = _TAIL_START
    while pos > 0 and read_total < _TAIL_MAX:
        want = min(pos, step, _TAIL_MAX - read_total)
        pos -= want
        fh.seek(pos)
        chunk = fh.read(want)
        if len(chunk) != want:
            return  # the file shrank under us; the next stat change re-parses
        read_total += want
        step = read_total
        buf = chunk + buf
        if pos > 0:
            nl = buf.find(b"\n")
            if nl < 0:
                continue  # one line longer than the window so far: widen
            head, body = buf[:nl], buf[nl + 1:]
        else:
            head, body = b"", buf
        buf = head
        for line in reversed(body.split(b"\n")):
            if line and len(line) <= _LINE_CAP:
                yield line


def read_first_line(fh) -> bytes | None:
    """The first line, or None when it is empty or longer than _META_CAP."""
    raw = fh.readline(_META_CAP + 1)
    if not raw or (len(raw) > _META_CAP and not raw.endswith(b"\n")):
        return None
    return raw.rstrip(b"\r\n")


# --- Record helpers -------------------------------------------------------------


def _parse_iso(value) -> datetime | None:
    """A timezone-aware UTC datetime, or None for anything that is not a timestamp."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _iso(dt: datetime) -> str:
    """Normalised ISO form (+00:00, microseconds) so string sorts agree across providers."""
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _ns_dt(ns: int) -> datetime:
    try:
        return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _record_time(obj) -> datetime | None:
    if isinstance(obj, dict):
        return _parse_iso(obj.get("timestamp"))
    return None


def item_of(obj) -> tuple[str, dict] | None:
    """(item type, item) of an ``event_msg`` ``item_completed`` record, else None."""
    if not isinstance(obj, dict) or obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "item_completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    return (item_type, item) if isinstance(item_type, str) else None


def item_text(item: dict) -> str:
    """The text of a UserMessage / AgentMessage item (element type ``text`` or ``Text``)."""
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts = []
    for element in content:
        if (isinstance(element, dict) and isinstance(element.get("text"), str)
                and isinstance(element.get("type"), str) and element["type"].lower() == "text"):
            parts.append(element["text"])
    return " ".join(parts).strip()


def _message_text(obj, item_type: str) -> str:
    """Text of a real UserMessage / AgentMessage record ("" for anything else).

    Only ``event_msg`` ``item_completed`` items count (D9). The user-role
    ``response_item`` messages carry injected context (an AGENTS.md block of
    about 44 KB) and are never read as prompts.
    """
    found = item_of(obj)
    if found is None or found[0] != item_type:
        return ""
    return item_text(found[1])


# --- Per-file verdict and parse (cached by (mtime_ns, size)) --------------------


@dataclass(frozen=True)
class _Verdict:
    kind: str                 # "top" | "subagent" | "skip"
    reason: str = ""
    session_id: str = ""
    cwd: str = ""
    norm_cwd: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class _Parsed:
    first_prompt: str
    last_prompt: str
    reply_tail: str
    last_record: datetime | None


# Both caches hold at least twice the store's rollout count (D10: never smaller
# than the file count; a cache smaller than a sequential scan thrashes on every
# pass). fit_caches grows them at store rebuild, with headroom, so the resize
# happens when the store doubles rather than on every new file.
_cache_cap = _CACHE_MIN
_verdict_cache = BoundedCache(_cache_cap)
_parse_cache = BoundedCache(_cache_cap)
# The last-record cache of D16 is sized with the store like the two above
# (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3 review fix): a fixed
# size below the file count makes every sequential pass over a large store re-read each tail.
_last_event_cache = BoundedCache(_cache_cap)
# Guards the three rebinds above. Not `_store_lock`: `_build_store` calls `fit_caches` with
# that lock held, and `overview._usage_files` calls it with none, so two growths at once
# would each publish new empty caches and drop the other's warm entries.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1
_caches_lock = threading.Lock()


def fit_caches(rollouts: int) -> None:
    global _cache_cap, _verdict_cache, _parse_cache, _last_event_cache
    with _caches_lock:
        if _cache_cap < 2 * rollouts:      # re-checked under the lock: a racing growth already fit
            _cache_cap = max(_CACHE_MIN, 4 * rollouts)
            _verdict_cache = BoundedCache(_cache_cap)
            _parse_cache = BoundedCache(_cache_cap)
            _last_event_cache = BoundedCache(_cache_cap)


def _norm_cwd(cwd: str) -> str:
    """The workspace key of a cwd: ``data._normalize_path``, except that on Windows a
    network or device path (one that starts with two slashes of either kind) is only
    slash-normalised and case-folded. ``_normalize_path`` expands 8.3 short names with
    ``GetLongPathNameW`` whenever the path holds a ``~``, which for a UNC path is SMB
    traffic (an NTLM handshake, or a hang on an offline server) for every rollout, under
    the store lock. A cwd read out of a rollout is untrusted input.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1"""
    if sys.platform == "win32":
        flat = cwd.replace("/", "\\")
        if flat.startswith("\\\\"):
            return flat.rstrip("\\").casefold()
    return _normalize_path(cwd)


def _file_uuid(path) -> str:
    m = _ROLLOUT_RE.fullmatch(os.path.basename(os.fspath(path)))
    return m.group(1).lower() if m else ""


def _read_meta_payload(path: str) -> dict:
    """The first line's ``session_meta`` payload, without its large ``base_instructions``.

    Raises on an unreadable file and returns {} for a first line that is not a
    ``session_meta`` object with a ``payload`` dict (legacy, torn, oversize).
    """
    with open_shared(path) as fh:
        raw = read_first_line(fh)
    if raw is None:
        return {}
    obj = loads(raw)
    if not isinstance(obj, dict) or obj.get("type") != "session_meta":
        return {}
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k != "base_instructions"}


def _compute_verdict(path: str, mtime_ns: int, file_uuid: str) -> _Verdict:
    # Per-file isolation: one bad file is a skipped file, never a blank listing.
    try:
        payload = _read_meta_payload(path)
        if not payload:
            return _Verdict("skip", "no-meta")
        source = payload.get("source")
        if isinstance(source, dict) and "subagent" in source:
            return _Verdict("subagent")
        cwd, sid = payload.get("cwd"), payload.get("id")
        if not isinstance(cwd, str) or not cwd.strip():
            return _Verdict("skip", "cwd")
        if (not isinstance(sid, str) or not UUID_RE.fullmatch(sid.lower())
                or not file_uuid or sid.lower() != file_uuid):
            return _Verdict("skip", "id")
        created = _parse_iso(payload.get("timestamp")) or _ns_dt(mtime_ns)
        return _Verdict("top", "", sid.lower(), cwd, _norm_cwd(cwd), _iso(created))
    except Exception as exc:
        _warn("verdict", exc, path)
        return _Verdict("skip", "error")


def _verdict_for(path: str, mtime_ns: int, size: int, file_uuid: str) -> _Verdict:
    cached = _verdict_cache.get(path)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2]
    verdict = _compute_verdict(path, mtime_ns, file_uuid)
    # An error is a failure to look, not a verdict about the file (a transient
    # open failure): never remembered, so the next pass looks again.
    if verdict.reason != "error":
        _verdict_cache.put(path, (mtime_ns, size, verdict))
    return verdict


def _parse_rollout(path: str) -> _Parsed:
    """Head scan for the first real prompt, tail scan for the last prompt, reply
    and record time. Bounded by _HEAD_CAP and _TAIL_MAX."""
    first_prompt = ""
    last_user = ""
    last_agent = ""
    last_record = None
    with open_shared(path) as fh:
        first_end = len(fh.readline(_META_CAP + 1))
        for line in _iter_lines(fh, max(0, _HEAD_CAP - first_end)):
            if b'"UserMessage"' not in line:
                continue
            first_prompt = _message_text(loads(line), "UserMessage")
            if first_prompt:
                break
        size = fh.seek(0, 2)
        for line in _iter_lines_reverse(fh, size):
            want_user = not last_user and b'"UserMessage"' in line
            want_agent = not last_agent and b'"AgentMessage"' in line
            if last_record is None or want_user or want_agent:
                obj = loads(line)
                if last_record is None:
                    last_record = _record_time(obj)
                if want_user:
                    last_user = _message_text(obj, "UserMessage")
                if want_agent:
                    last_agent = _message_text(obj, "AgentMessage")
            if last_record is not None and last_user and last_agent:
                break
    return _Parsed(first_prompt[:200], (last_user or first_prompt)[:200],
                   last_agent[:100], last_record)


def _parsed_for(path: str, mtime_ns: int, size: int) -> _Parsed:
    cached = _parse_cache.get(path)
    # The size is part of the key: Windows freezes the mtime of a file Codex has
    # open while it keeps growing (measured in
    # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 0), and
    # compaction can rewrite a file in place.
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2]
    parsed = _parse_rollout(path)
    _parse_cache.put(path, (mtime_ns, size, parsed))
    return parsed


# --- Store index (D11) -----------------------------------------------------------


@dataclass(frozen=True)
class _Rollout:
    path: str
    file_uuid: str
    mtime_ns: int
    size: int


class _Store:
    """One walk of the sessions folder: every rollout once, newest file per thread id."""

    def __init__(self, root: str):
        self.root = root
        self.built = 0.0
        self.rollouts: dict[str, _Rollout] = {}
        self.top: dict[str, tuple[_Rollout, _Verdict]] = {}
        self.by_cwd: dict[str, list[tuple[_Rollout, _Verdict]]] = {}


_store_lock = threading.Lock()
_store_memo: _Store | None = None


def _is_junction(entry) -> bool:
    """True for an NTFS junction (a directory reparse point of the mount-point kind).
    ``is_dir(follow_symlinks=False)`` is True for it, so it needs its own test; the tag
    comes from the directory listing, with no extra system call. Always False off Windows."""
    return getattr(entry.stat(follow_symlinks=False), "st_reparse_tag", 0) == _IO_REPARSE_TAG_MOUNT_POINT


def _walk_rollouts(root: str):
    """Yield (path, lower-case thread uuid) for every rollout file under `root`.

    ``archived_sessions/`` is a sibling folder and is never visited (D24). A symlink
    and an NTFS junction are never followed (a link could lead the walk out of the
    store, or into a loop); a symlinked file is refused by ``is_file(follow_symlinks=False)``.
    An unreadable root other than a missing one is logged once a minute, path only: it
    would otherwise read as "no Codex".
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1
    """
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < 5 and not _is_junction(entry):
                                stack.append((entry.path, depth + 1))
                        else:
                            m = _ROLLOUT_RE.fullmatch(entry.name)
                            if m and entry.is_file(follow_symlinks=False):
                                yield entry.path, m.group(1).lower()
                    except OSError:
                        continue
        except OSError as exc:
            if depth == 0 and not isinstance(exc, FileNotFoundError):
                _warn("sessions_root", exc, directory)
            continue


def _build_store(root: str) -> _Store:
    store = _Store(root)
    files: dict[str, list[_Rollout]] = {}
    for path, file_uuid in _walk_rollouts(root):
        try:
            st = os.stat(path)  # not DirEntry.stat(), which can be stale for open files on Windows
        except OSError:
            continue
        files.setdefault(file_uuid, []).append(_Rollout(path, file_uuid, st.st_mtime_ns, st.st_size))
    fit_caches(sum(len(group) for group in files.values()))
    counts: Counter = Counter()
    for file_uuid, group in files.items():
        # Two files for one thread id (resume, unarchive): the newest top-level one
        # wins, so a torn newer file cannot hide a valid older session. With no
        # top-level file at all, the newest file is the lookup target.
        group.sort(key=lambda r: (r.mtime_ns, r.path), reverse=True)
        judged = [(r, _verdict_for(r.path, r.mtime_ns, r.size, file_uuid)) for r in group]
        chosen, verdict = next(((r, v) for r, v in judged if v.kind == "top"), judged[0])
        store.rollouts[file_uuid] = chosen
        counts[verdict.kind if verdict.kind != "skip" else f"skip:{verdict.reason}"] += 1
        if verdict.kind == "top":
            store.top[file_uuid] = (chosen, verdict)
            store.by_cwd.setdefault(verdict.norm_cwd, []).append((chosen, verdict))
    log.debug("codex store: %s", dict(counts))
    return store


def _store_index() -> _Store:
    """The memoised store index, rebuilt at most once per _STORE_TTL seconds."""
    global _store_memo
    root = str(CODEX_SESSIONS_DIR)
    memo = _store_memo
    if memo is not None and memo.root == root and time.monotonic() - memo.built < _STORE_TTL:
        return memo
    with _store_lock:
        memo = _store_memo
        if memo is not None and memo.root == root and time.monotonic() - memo.built < _STORE_TTL:
            return memo
        store = _build_store(root)
        store.built = time.monotonic()
        _store_memo = store
        return store


class CanonicalRollout(NamedTuple):
    """One canonical rollout: its path and the (mtime_ns, size) the store index recorded."""
    path: str
    mtime_ns: int
    size: int


@_safe("canonical_rollouts", list)
def canonical_rollouts() -> list[CanonicalRollout]:
    """The rollouts that count as sessions: the same set discovery lists, from the same
    store index, so a usage pass and a workspace listing cannot disagree on what a Codex
    session file is. One per thread id (the newest top-level file), only a file named
    ``rollout-*-<uuid>.jsonl`` that is not a symlink or a file under a link, with a
    readable ``session_meta`` that names a cwd and the same id as the filename. A
    sub-agent rollout, a file that could not be examined and a duplicate-id copy are
    left out. Sorted by path. The stat is the index's, at most _STORE_TTL old: a caller
    that needs a fresh one stats the path itself.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1"""
    return sorted((CanonicalRollout(r.path, r.mtime_ns, r.size) for r, _v in _store_index().top.values()),
                  key=lambda c: c.path)


# --- Session index (titles, D9) --------------------------------------------------

_names_memo: tuple[tuple, dict[str, str]] | None = None


def _index_state() -> tuple[float, int, int]:
    """(mtime, mtime_ns, size) of session_index.jsonl from ONE stat; (0.0, 0, -1)
    when it is absent."""
    try:
        st = os.stat(CODEX_SESSION_INDEX)
    except OSError:
        return (0.0, 0, -1)
    return (st.st_mtime, st.st_mtime_ns, st.st_size)


def _index_sig() -> tuple[float, int]:
    """(mtime, size) of session_index.jsonl; (0.0, -1) when it is absent."""
    state = _index_state()
    return (state[0], state[2])


def _thread_names(state: tuple[float, int, int]) -> dict[str, str]:
    """thread id -> user-given name from session_index.jsonl, the last entry winning.

    `state` is the index's `_index_state()`, taken BEFORE this read: a rename that
    lands in between is then seen as a change by the next refresh, never recorded
    as already seen. Cached by that (mtime_ns, size), outside the rollout-keyed
    parse cache, so a rename shows without the rollout changing.
    """
    global _names_memo
    path = str(CODEX_SESSION_INDEX)
    if state[2] < 0:
        return {}
    key = (path, state[1], state[2])
    memo = _names_memo
    if memo is not None and memo[0] == key:
        return memo[1]
    names: dict[str, str] = {}
    if state[2] > _INDEX_CAP:
        _warn("session_index_cap", path=path,
              note=f"session index is over {_INDEX_CAP} bytes; entries past that are ignored")
    try:
        with open_shared(path) as fh:
            for line in _iter_lines(fh, _INDEX_CAP):
                obj = loads(line)
                if not isinstance(obj, dict):
                    continue
                thread_id, name = obj.get("id"), obj.get("thread_name")
                if isinstance(thread_id, str) and isinstance(name, str) and name.strip():
                    names[thread_id.lower()] = name.strip()[:_NAME_CAP]
    except Exception as exc:
        _warn("session_index", exc, path)
        return {}
    _names_memo = (key, names)
    return names


# --- Availability ---------------------------------------------------------------

_available_memo: tuple[float, str, bool] | None = None


@_safe("is_available", lambda: False)
def is_available() -> bool:
    """True if Codex rollout files exist. Total and cheap: a first-hit walk, cached 5 s."""
    global _available_memo
    root = str(CODEX_SESSIONS_DIR)
    memo = _available_memo
    now = time.monotonic()
    if memo is not None and memo[1] == root and now - memo[0] < _AVAILABLE_TTL:
        return memo[2]
    walker = _walk_rollouts(root)
    try:
        found = next(walker, None) is not None
    finally:
        walker.close()
    _available_memo = (now, root, found)
    return found


# --- Discovery and listing --------------------------------------------------------


def _parsed_for_listing(rollout: "_Rollout") -> tuple["_Parsed", int]:
    """(parse, mtime_ns) of a rollout for a workspace listing.

    `_parse_cache` holds one slot per path, keyed by the (mtime_ns, size) that
    `load_sessions` takes from a fresh stat, while the store index's own stat can be
    _STORE_TTL old. Keying a listing on the index stat alone made the two keys alternate
    on a growing file and re-parsed it on every call. A miss on the index stat is
    therefore retried with a fresh stat: a warm file costs no extra stat, and a changed
    file is parsed once per change, whoever asks first. If the stat fails the index
    values are used.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1"""
    cached = _parse_cache.get(rollout.path)
    if cached is not None and cached[0] == rollout.mtime_ns and cached[1] == rollout.size:
        return cached[2], rollout.mtime_ns
    try:
        st = os.stat(rollout.path)
    except OSError:
        return _parsed_for(rollout.path, rollout.mtime_ns, rollout.size), rollout.mtime_ns
    return _parsed_for(rollout.path, st.st_mtime_ns, st.st_size), st.st_mtime_ns


@_safe("discover_workspaces", list)
def discover_workspaces() -> list[tuple[str, int, str]]:
    """(display cwd, top-level session count, updated_at ISO) per workspace, newest first.

    Sessions are grouped by the normalised cwd, so the same folder written with
    an upper-case and a lower-case drive letter is one workspace. The display
    spelling is the one the most recently modified session used.
    """
    results: list[tuple[str, int, str]] = []
    now = datetime.now(timezone.utc)
    for items in _store_index().by_cwd.values():
        best = None
        for rollout, verdict in items:
            # The same recency a session row shows: Windows freezes the mtime of a
            # rollout Codex holds open, so the last record's stamp counts too. The
            # parse is cached by (mtime_ns, size), so a warm pass costs no read.
            try:
                parsed, mtime_ns = _parsed_for_listing(rollout)
                updated = _updated_at(mtime_ns, parsed, now)
            except Exception as exc:
                _warn("discover_workspaces.file", exc, rollout.path)
                updated = _ns_dt(rollout.mtime_ns)
            key = (updated, rollout.path)
            if best is None or key > best[0]:
                best = (key, verdict.cwd)
        results.append((best[1], len(items), _iso(best[0][0])))
    results.sort(key=lambda x: x[2], reverse=True)
    return results


# path -> (attempts, monotonic time of the last attempt, mtime, size): the parse
# failures of rollouts that stayed out of a listing. `attempts` counts failed loads
# of that exact (mtime, size); 0 means recovered. A deterministic failure (anything
# but an OSError) is stored as already exhausted. Bounded, so a store full of
# unreadable files cannot grow it; a path evicted from it simply starts a new budget.
_parse_failed = BoundedCache(512)


def _note_parse_outcome(path: str, st: os.stat_result, exc: BaseException | None) -> None:
    """Record how one rollout's load ended: `exc` is the failure, None a load that
    got past the parse (a session, or a file that is not one). Called with every
    outcome so that a recovery or a non-session ends the retries."""
    prior = _parse_failed.get(path)
    if exc is None:
        if prior is not None and prior[0]:
            _parse_failed.put(path, (0, 0.0, st.st_mtime, st.st_size))
        return
    # Equality, not ordering: a file that was rewritten shorter or older is a new
    # file here, whatever its size or mtime did.
    same = prior is not None and prior[2:] == (st.st_mtime, st.st_size)
    attempts = prior[0] + 1 if same else 1
    if not isinstance(exc, OSError):
        attempts = _RETRY_MAX + 1
    _parse_failed.put(path, (attempts, time.monotonic(), st.st_mtime, st.st_size))


def _retry_due(path: str) -> bool:
    """True when this rollout's last load failed with an OSError, retries are left,
    and the last attempt is at least _RETRY_SPACING seconds old. The caller has
    already compared the file's stat with the recorded one, so the entry (which
    every load of the file rewrites) describes the file as it is now."""
    entry = _parse_failed.get(path)
    if entry is None or not 1 <= entry[0] <= _RETRY_MAX:
        return False
    return time.monotonic() - entry[1] >= _RETRY_SPACING


def _updated_at(mtime_ns: int, parsed: "_Parsed", now: datetime) -> datetime:
    """The later of the file mtime and the last record's stamp; a future stamp cannot pin a row."""
    updated = _ns_dt(mtime_ns)
    if parsed.last_record is not None:
        updated = max(updated, min(parsed.last_record, now))
    return updated


def _failed_listing():
    """The neutral value of a failed `load_sessions`: no sessions, and a tombstone on
    the index key that no real index state equals, so the next refresh sees a change
    and reloads instead of keeping an empty workspace until restart."""
    return [], {str(CODEX_SESSION_INDEX): _FileInfo(-1.0, -2)}


@_safe("load_sessions", _failed_listing)
def load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Top-level sessions for one workspace, plus the file stats that guard the cache."""
    norm = _norm_cwd(cwd)
    state = _index_state()  # one stat, taken before the names are read
    names = _thread_names(state)
    file_stats: dict[str, _FileInfo] = {str(CODEX_SESSION_INDEX): _FileInfo(mtime=state[0], size=state[2])}
    sessions: list[Session] = []
    now = datetime.now(timezone.utc)
    for rollout, _listed in _store_index().by_cwd.get(norm, []):
        # Per-file isolation: one bad file never blanks the workspace.
        st = None
        try:
            st = os.stat(rollout.path)  # fresh: the index memo can be 5 s old
            # Recorded before anything below can skip or fail, so a listed file that
            # is not a session does not make every refresh report a change. It is the
            # real stat even for a failed parse: the retry of a transient failure is
            # `_parse_failed`'s job (refresh_stale_entries_for_cwd), not a fake stat.
            file_stats[rollout.path] = _FileInfo(mtime=st.st_mtime, size=st.st_size)
            verdict = _verdict_for(rollout.path, st.st_mtime_ns, st.st_size, rollout.file_uuid)
            if verdict.kind != "top" or verdict.norm_cwd != norm:
                _note_parse_outcome(rollout.path, st, None)
                continue
            parsed = _parsed_for(rollout.path, st.st_mtime_ns, st.st_size)
            _note_parse_outcome(rollout.path, st, None)
            updated = _updated_at(st.st_mtime_ns, parsed, now)
            title = names.get(verdict.session_id) or parsed.first_prompt[:80] or verdict.session_id
            sessions.append(Session(
                session_id=verdict.session_id,
                title=title,
                cwd=cwd,
                created_at=verdict.created_at,
                updated_at=_iso(updated),
                first_prompt=parsed.first_prompt,
                last_prompt=parsed.last_prompt,
                last_reply_tail=parsed.reply_tail,
            ))
        except Exception as exc:
            _warn("load_sessions.file", exc, rollout.path)
            if st is not None:
                _note_parse_outcome(rollout.path, st, exc)
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions, file_stats


@_safe("refresh_stale_entries_for_cwd", lambda: False)
def refresh_stale_entries_for_cwd(norm_cwd: str, old_stats: dict[str, _FileInfo]) -> bool:
    """True when a tracked rollout changed or vanished, a top-level rollout for this
    workspace appeared (in any date folder), the session index changed (D12), or a
    rollout whose parse failed with an OSError is due a retry."""
    if not old_stats:
        return False
    index_key = str(CODEX_SESSION_INDEX)
    for path_str, info in old_stats.items():
        if path_str == index_key:
            if _index_sig() != (info.mtime, info.size):
                return True
            continue
        try:
            st = os.stat(path_str)
        except OSError:
            return True
        if st.st_mtime != info.mtime or st.st_size != info.size:
            return True
        if _retry_due(path_str):
            return True
    return any(rollout.path not in old_stats
               for rollout, _verdict in _store_index().by_cwd.get(norm_cwd, []))


@_safe("find_session_workspace", lambda: None)
def find_session_workspace(session_id: str) -> str | None:
    if not SESSION_ID_RE.fullmatch(session_id):
        return None
    entry = _store_index().top.get(session_id.lower())
    return entry[1].cwd if entry else None


# --- Rollout lookup ---------------------------------------------------------------

# id -> (store generation, time): ids the store does not hold. An entry is valid only
# while the store it was recorded against is still the current one, so it lives at most
# one store rebuild (_STORE_TTL), whatever _MISSING_TTL says: a rollout that appears is
# found at the next rebuild. _MISSING_TTL is an upper bound that only matters if
# _STORE_TTL is ever raised above it.
_missing = BoundedCache(512)


@_safe("rollout_path", lambda: None)
def rollout_path(session_id: str) -> Path | None:
    """The rollout file of a thread id (the newest, when two files carry one id), or None.

    The id is validated before any lookup. Sub-agent rollouts are found too;
    only discovery excludes them. Of several files for one id, the newest
    top-level one (else the newest file) is returned.
    """
    if not SESSION_ID_RE.fullmatch(session_id):
        return None
    key = session_id.lower()
    store = _store_index()
    miss = _missing.get(key)
    if miss is not None and miss[0] == store.built and time.monotonic() - miss[1] < _MISSING_TTL:
        return None
    rollout = store.rollouts.get(key)
    if rollout is None:
        _missing.put(key, (store.built, time.monotonic()))
        return None
    return Path(rollout.path)


@_safe("read_meta", lambda: None, path_arg=True)
def read_meta(path) -> dict | None:
    """The ``session_meta`` payload of a rollout's first line (without
    ``base_instructions``), or None when the first line is not one. The one reader
    of that line, capped at 256 KiB."""
    return _read_meta_payload(os.fspath(path)) or None


@_safe("is_subagent_rollout", lambda: True, path_arg=True)
def is_subagent_rollout(path, st=None) -> bool:
    """True when the rollout's first line marks it as a sub-agent thread.

    Used to exclude such files (their histories embed the parent's, so they
    cannot be summed). A legacy, torn or invalid file is a known non-sub-agent:
    False. A file that could not be examined at all (a failed read) is unknown
    and excluded too: True, because counting a possible sub-agent is the silent
    multiply the exclusion exists to prevent (D7). That includes a failure that
    escapes this function (a stat of a vanished file): the neutral value is True.
    """
    target = os.fspath(path)
    st = st or os.stat(target)
    verdict = _verdict_for(target, st.st_mtime_ns, st.st_size, _file_uuid(target))
    return verdict.kind == "subagent" or (verdict.kind == "skip" and verdict.reason == "error")


# --- Tails and transcripts ----------------------------------------------------------


@_safe("get_session_tail", list)
def get_session_tail(session_id: str, cwd: str, max_lines: int = 15) -> list[str]:
    """The last `max_lines` assistant texts of a session, oldest first."""
    path = rollout_path(session_id)
    if path is None or max_lines < 1:
        return []
    messages: list[str] = []
    with open_shared(path) as fh:
        size = fh.seek(0, 2)
        for line in _iter_lines_reverse(fh, size):
            if b'"AgentMessage"' not in line:
                continue
            try:
                text = _message_text(loads(line), "AgentMessage")
            except Exception as exc:  # one bad record is one skipped record
                log.debug("codex tail: skipped a record (%s)", type(exc).__name__)
                continue
            if text:
                messages.append(_cap_text(text))
                if len(messages) >= max_lines:
                    break
    messages.reverse()
    return messages


@_safe("get_first_prompt", str)
def get_first_prompt(session_id: str, cwd: str) -> str:
    """The first real user prompt of a session (injected context is never one)."""
    path = rollout_path(session_id)
    if path is None:
        return ""
    with open_shared(path) as fh:
        first_end = len(fh.readline(_META_CAP + 1))
        for line in _iter_lines(fh, max(0, _HEAD_CAP - first_end)):
            if b'"UserMessage"' not in line:
                continue
            try:
                text = _message_text(loads(line), "UserMessage")
            except Exception as exc:  # one bad record is one skipped record
                log.debug("codex first prompt: skipped a record (%s)", type(exc).__name__)
                continue
            if text:
                return _cap_text(text)
    return ""


def _cap_value(value, depth: int = 0):
    """Bound a tool argument for display: strings to _ARG_CHARS, lists and dicts to
    _ARG_ITEMS (a final "(+N more)" element or key counts what was dropped), and
    nesting to four levels, below which a container becomes text. Scalars keep
    their type at every depth."""
    if isinstance(value, str):
        return value[:_ARG_CHARS]
    if isinstance(value, (list, dict)):
        if depth >= 4:
            return str(value)[:_ARG_CHARS]
        dropped = len(value) - _ARG_ITEMS
        if isinstance(value, list):
            out_list = [_cap_value(v, depth + 1) for v in value[:_ARG_ITEMS]]
            if dropped > 0:
                out_list.append(f"(+{dropped} more)")
            return out_list
        out = {str(k)[:_ARG_CHARS]: _cap_value(v, depth + 1) for k, v in list(value.items())[:_ARG_ITEMS]}
        if dropped > 0:
            out[f"(+{dropped} more)"] = ""
        return out
    return value


def _call_args(arguments) -> dict:
    """A function_call's ``arguments`` (a JSON string) as a dict, else ``{"raw": text}``."""
    if isinstance(arguments, dict):
        return _cap_value(arguments)
    if not isinstance(arguments, str):
        return {}
    try:
        parsed = json.loads(arguments)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return _cap_value(parsed)
    return {"raw": arguments[:_ARG_CHARS]}


def output_text(output) -> str:
    """A tool output as text: a string as is, a list's text elements joined."""
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(e["text"] for e in output
                         if isinstance(e, dict) and isinstance(e.get("text"), str))
    return ""


def exit_success(text: str) -> bool | None:
    """True/False from the tool's own exit code, None when it records none.

    A string starting ``Exit code: <n>`` (shell_command), or JSON with
    ``metadata.exit_code`` (older shell and apply_patch). Anything else is
    unknown, never a guess.
    """
    m = _EXIT_CODE_RE.match(text)
    if m:
        return int(m.group(1)) == 0
    if text.lstrip().startswith("{"):
        try:
            obj = json.loads(text)
        except Exception:
            return None
        meta = obj.get("metadata") if isinstance(obj, dict) else None
        code = meta.get("exit_code") if isinstance(meta, dict) else None
        if isinstance(code, int) and not isinstance(code, bool):
            return code == 0
    return None


def exec_outcome(text: str) -> bool | None:
    """True/False from an `exec` output's leading marker, else None (plan D13)."""
    for marker, success in _EXEC_MARKERS:
        if text.startswith(marker):
            return success
    return None


def _transcript_events(obj, exec_calls: set[str]) -> list[TranscriptEvent]:
    """The TranscriptEvents one rollout record yields (D13).

    `exec_calls` holds the call ids of the `exec` calls seen so far in this
    transcript (this function adds to it): an exec marker maps an outcome only
    for a result of one of those calls.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, finding 22.
    """
    if not isinstance(obj, dict):
        return []
    stamp = obj.get("timestamp")
    stamp = stamp if isinstance(stamp, str) else ""
    rtype = obj.get("type")
    if rtype == "event_msg":
        found = item_of(obj)
        if found is None or found[0] not in ("UserMessage", "AgentMessage"):
            return []
        text = item_text(found[1])
        if not text:
            return []
        return [TranscriptEvent(kind="user" if found[0] == "UserMessage" else "assistant",
                                text=text, timestamp=stamp)]
    if rtype != "response_item":
        return []
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return []
    ptype = payload.get("type")
    call_id = payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return []
    name = payload.get("name") if isinstance(payload.get("name"), str) else ""
    if ptype == "function_call":
        if name == "exec":
            exec_calls.add(call_id)
        return [TranscriptEvent(kind="tool_call", tool_call_id=call_id, tool_name=name,
                                tool_args=_call_args(payload.get("arguments")), timestamp=stamp)]
    if ptype == "custom_tool_call":
        if name == "exec":
            exec_calls.add(call_id)
        content = payload.get("input")
        return [TranscriptEvent(kind="tool_call", tool_call_id=call_id, tool_name=name,
                                tool_args={"content": content[:_ARG_CHARS] if isinstance(content, str) else ""},
                                timestamp=stamp)]
    if ptype in ("function_call_output", "custom_tool_call_output"):
        text = output_text(payload.get("output"))
        is_exec = call_id in exec_calls
        if is_exec and text.startswith(EXEC_RUNNING):
            return []  # not a final outcome: the call stays "started" (D13)
        success = exit_success(text)
        if success is None and is_exec:
            success = exec_outcome(text)
        # No exit code and no marker: the call finished and its outcome is unknown.
        # Never a success claim: the flag makes the translator show a neutral
        # status (D13). Only this adapter ever sets it.
        return [TranscriptEvent(kind="tool_result", tool_call_id=call_id, success=success,
                                outcome_unknown=success is None, timestamp=stamp)]
    return []


@_safe("get_full_transcript", list)
def get_full_transcript(session_id: str, cwd: str) -> list[TranscriptEvent]:
    """The transcript of a session in on-disk order, bounded unconditionally (D13).

    Reads only the last _TRANSCRIPT_WINDOW bytes (from the first complete line),
    streams line by line, keeps at most the last _TRANSCRIPT_MAX_EVENTS events,
    and when anything was left out prepends one synthetic assistant event saying
    so. A line over 256 KiB and any line that does not parse are skipped.
    """
    path = rollout_path(session_id)
    if path is None:
        return []
    events: deque = deque(maxlen=_TRANSCRIPT_MAX_EVENTS)
    total = 0
    notices = 0
    exec_calls: set[str] = set()  # bounded by the transcript window

    def add(event: TranscriptEvent) -> None:
        nonlocal total
        total += 1
        events.append(event)

    def oversized(head: bytes) -> None:
        # A record over the line cap is skipped (D13), but a reader should not see
        # a silent gap: a notice in its place, only for a record that would have
        # been an event, and at most _OVERSIZE_NOTICES per transcript.
        nonlocal notices
        if notices >= _OVERSIZE_NOTICES or not any(k in head for k in _TRANSCRIPT_KEYS):
            return
        notices += 1
        add(TranscriptEvent(kind="assistant", text="(An oversized record was omitted.)"))

    with open_shared(path) as fh:
        size = fh.seek(0, 2)
        start = max(0, size - _TRANSCRIPT_WINDOW)
        # One byte early: when the window starts exactly on a line boundary the
        # skipped "partial line" is just the previous newline.
        fh.seek(max(0, start - 1))
        budget = size - max(0, start - 1)
        # The budget runs to the end of the file, so a last line without a trailing
        # newline is a line (eof_at_budget).
        for line in _iter_lines(fh, budget, skip_partial=start > 0, eof_at_budget=True,
                                on_oversize=oversized):
            if not any(k in line for k in _TRANSCRIPT_KEYS):
                continue
            try:
                found = _transcript_events(loads(line), exec_calls)
            except Exception as exc:  # one bad record is one skipped record, not an empty transcript
                log.debug("codex transcript: skipped a record (%s)", type(exc).__name__)
                continue
            for event in found:
                add(event)
    result = list(events)
    if start > 0:
        notice = f"(Earlier events omitted: showing the last {len(result)} events.)"
    elif total > len(result):
        notice = f"(Earlier events omitted: showing the last {len(result)} of {total}.)"
    else:
        return result
    return [TranscriptEvent(kind="assistant", text=notice)] + result


# --- Recency of a rollout whose mtime is frozen (D16) -------------------------------
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3

ACTIVITY_WINDOW = 300.0     # seconds: the live rule's recency window (web._session_is_live)
FUTURE_SKEW = 5.0           # a stamp this far past now is a clock error, not activity (overview.py's window rule, D16, D19)

# path -> (mtime_ns, size, epoch of the last complete record or None). Keyed by
# (mtime_ns, size) because Windows freezes the mtime of a file Codex holds open while
# the size grows. Separate from _parse_cache so a read here is its own, countable event.
# (The cache itself is defined with the other two, above, so fit_caches sizes it.)


def _read_last_event(path: str) -> float | None:
    with open_shared(path) as fh:
        size = fh.seek(0, 2)
        for line in _iter_lines_reverse(fh, size):
            when = _record_time(loads(line))
            if when is not None:
                return when.timestamp()
    return None


def _last_event(path: str, st) -> float | None:
    cached = _last_event_cache.get(path)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    epoch = _read_last_event(path)
    _last_event_cache.put(path, (st.st_mtime_ns, st.st_size, epoch))
    return epoch


@_safe("last_event_epoch", lambda: None, path_arg=True)
def last_event_epoch(path) -> float | None:
    """POSIX time of the last complete record's ``timestamp``, or None (no such record,
    unreadable file). A torn last line is skipped. Cached by (mtime_ns, size)."""
    target = os.fspath(path)
    return _last_event(target, os.stat(target))


# --- Turn verdict (D11) ---------------------------------------------------------------------
# 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 4

TURN_STUCK_SECONDS = 30 * 60.0  # a turn whose newest record is older than this has no verdict
_TURN_RECORDS = {"task_started": "working", "task_complete": "idle", "turn_aborted": "idle"}
_turn_cache = BoundedCache(256)  # path -> (mtime_ns, size, verdict, newest record epoch)


def _read_turn_state(path: str) -> tuple[str | None, float | None]:
    """(verdict of the last turn record in the tail window, epoch of the newest record)."""
    newest = None
    with open_shared(path) as fh:
        size = fh.seek(0, 2)
        for line in _iter_lines_reverse(fh, size):
            obj = loads(line)
            if not isinstance(obj, dict):
                continue
            if newest is None:
                when = _record_time(obj)
                if when is not None:
                    newest = when.timestamp()
            payload = obj.get("payload")
            if obj.get("type") == "event_msg" and isinstance(payload, dict):
                verdict = _TURN_RECORDS.get(payload.get("type"))
                if verdict is not None:
                    return verdict, newest
    return None, newest


@_safe("turn_state", lambda: None, path_arg=True)
def turn_state(path) -> str | None:
    """``working`` or ``idle`` from the last turn record of a rollout, else None.

    The last of ``task_started`` (working), ``task_complete`` and ``turn_aborted`` (idle)
    in the tail window, matched on ``type`` and ``payload.type`` and never as text, because
    a rollout routinely quotes these words in tool output. None when the window holds no
    such record, and None when the verdict is working but the newest record is older than
    TURN_STUCK_SECONDS (a stuck turn). Cached by (mtime_ns, size), never mtime alone:
    Windows freezes the mtime of a rollout Codex holds open while its size grows.
    """
    target = os.fspath(path)
    st = os.stat(target)
    cached = _turn_cache.get(target)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        verdict, newest = cached[2], cached[3]
    else:
        verdict, newest = _read_turn_state(target)
        _turn_cache.put(target, (st.st_mtime_ns, st.st_size, verdict, newest))
    if verdict == "working" and (newest is None or time.time() - newest > TURN_STUCK_SECONDS):
        return None
    return verdict


def activity_epoch(path, st) -> float:
    """When the rollout last showed activity: the later of its mtime and, only when
    the mtime is older than ACTIVITY_WINDOW, the last complete record's timestamp (D16).

    Windows freezes the mtime of a rollout Codex holds open, so the mtime alone would
    switch the live dot off during an active turn. A warm active file costs one stat:
    the tail is read only when the mtime check fails. A timestamp (or mtime) more than
    FUTURE_SKEW past now is not trusted and is dropped, never clamped to now, so it
    cannot keep a session live. Total: any failure returns the mtime.
    """
    try:
        mtime = float(st.st_mtime)
    except Exception:
        return 0.0
    try:
        target = os.fsdecode(path)  # once, so the handler below never raises on a hostile argument
    except Exception:
        target = ""
    try:
        now = time.time()
        trusted = mtime if mtime <= now + FUTURE_SKEW else 0.0
        if trusted and now - trusted <= ACTIVITY_WINDOW:
            return min(trusted, now)
        stamp = _last_event(os.fspath(path), st)
        if stamp is not None and stamp <= now + FUTURE_SKEW:
            trusted = max(trusted, stamp)
        return min(trusted, now)
    except Exception as exc:
        _warn("activity_epoch", exc, target)
        return mtime if mtime <= time.time() + FUTURE_SKEW else 0.0


# --- Writer lock (D17) ---------------------------------------------------------------------
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3
#
# Codex's acquire() takes thread-writer-locks/.coordination.lock (blocking, whole file)
# and then try-locks <id>.lock; a drop removes the file under the coordination lock. A
# plain try-lock on <id>.lock from here made an emulated acquirer fail in 2.4-4.1 % of
# cycles (measured before this code existed), so the probe serialises on the coordination
# lock first.

_LOCK_TTL = 5.0             # seconds a per-id answer is reused
_LOCK_BUSY_TTL = 1.0        # seconds the fallback answer of a busy probe is reused
_LOCK_RETRIES = 3           # coordination busy: retries per call ...
_LOCK_RETRY_SLEEP = 0.005   # ... this far apart (one budget per call)
_LOCK_PROBE_WAIT = 0.5      # seconds a caller waits for the process-wide probe lock
_lock_cache = BoundedCache(512)     # id -> (monotonic time, bool); an expired entry is the stale fallback
_busy_cache = BoundedCache(512)     # id -> (monotonic time, bool): the fallback served while a probe cannot run
_probe_lock = threading.Lock()      # one probe at a time in this process; held only around the probe
_LOCK_STUCK_TTL = 2.0               # seconds a timed-out probe lock keeps callers off the wait
_probe_stuck_until = 0.0            # monotonic deadline of that marker; 0.0 = not stuck
_COORDINATION = ".coordination.lock"
_BUSY_WIN = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
_BUSY_POSIX = {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}

# Rules the probe keeps, beyond the order above:
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3
# - Both lock files are opened read-only: locking needs no write access, and a read-only
#   attribute or ACL made a read-write open fail, so a held thread read as free.
# - A busy probe's fallback answer is cached for _LOCK_BUSY_TTL, so a listing pays the retry
#   budget once per id per second; the fallback re-reads the answer cache, because another
#   thread may have refreshed it.
# - The wait for the process-wide lock is bounded. A timed-out wait sets a marker for
#   _LOCK_STUCK_TTL, during which a caller only tries the lock without waiting and otherwise
#   takes the fallback at once, so N uncached ids behind a hung holder cost one wait, not N.
#   Any successful acquire clears the marker.
# - No logging runs while the machine-wide coordination lock is held (only open, try-lock,
#   unlock, close): failures are queued, including a failure to open <id>.lock (whose False is
#   cached), and logged after it is released. A queued log can never change the probe's result.
# - A coordination lock that stays busy through the retry budget is logged (writer_lock.busy).


def _lock_byte(fh) -> bool:
    """Try a non-blocking one-byte lock. True: taken. False: someone holds it.
    Any other OSError is raised."""
    if sys.platform == "win32":
        import msvcrt  # lazy: a top-level import would break import on POSIX (D23)
        try:
            os.lseek(fh.fileno(), 0, 0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in _BUSY_WIN:
                return False
            raise
    import fcntl  # lazy, see above
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    except OSError as exc:
        if exc.errno in _BUSY_POSIX:
            return False
        raise


def _unlock_byte(fh) -> OSError | None:
    """Release a lock taken by _lock_byte. Best effort: closing the handle releases it too.
    Returns the OSError that stopped it, or None; it never logs, because it runs under the
    coordination lock (the caller logs after the lock is released)."""
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fh.fileno(), 0, 0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        return exc
    return None


def _release(fh, deferred: list, path: str = "") -> None:
    """Unlock `fh`; a failure of any kind is queued in `deferred`, never raised, so the
    probe's own answer survives it."""
    try:
        failure = _unlock_byte(fh)
    except Exception as exc:
        failure = exc
    if failure is not None:
        deferred.append(("writer_lock.unlock", failure, path))


def _thread_lock_held(path: str, deferred: list) -> bool:
    """True when another process holds <id>.lock. Runs under the coordination lock: only
    open, try-lock, unlock and close happen here, so a failure is queued in `deferred`
    and logged by the caller once the coordination lock is gone. A vanished file is False."""
    try:
        # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3: flock on an "rb" descriptor is unverified on POSIX/NFS (EBADF risk); a failure fails open, so Resume stays visible.
        fh = open_shared(path, "rb")
    except FileNotFoundError:
        return False
    except OSError as exc:  # PermissionError, a directory at the path: not contention, fail open
        deferred.append(("writer_lock.open", exc, path))
        return False
    try:
        try:
            got = _lock_byte(fh)
        except OSError as exc:  # EBADF, ENOTSUP, EINVAL...: not contention, fail open
            deferred.append(("writer_lock.lock", exc, path))
            return False
        if got:
            _release(fh, deferred, path)
            return False
        return True
    finally:
        fh.close()  # first: a Python handle on a lock file blocks Codex's removal of it


def _probe_under_coordination(lock_path: str, coord_path: str, deferred: list) -> bool | None:
    try:
        coord = open_shared(coord_path, "rb")  # never created: without it, not held
    except FileNotFoundError:
        return False
    try:
        if not _lock_byte(coord):
            return None
        try:
            return _thread_lock_held(lock_path, deferred)
        finally:
            _release(coord, deferred)  # last: Codex's blocking lock() has no timeout
    finally:
        coord.close()


def _probe_writer_lock(lock_path: str, coord_path: str) -> bool | None:
    """True / False for the thread lock, None when the coordination lock is busy
    (Codex is mid acquire, cleanup or publication). Failures noticed while the
    coordination lock was held are logged here, after it was released and closed."""
    deferred: list = []
    try:
        return _probe_under_coordination(lock_path, coord_path, deferred)
    finally:
        for kind, exc, path in deferred:
            try:
                _warn(kind, exc, path)
            except Exception:  # a failing log must neither replace the probe's result nor skip the rest
                pass


def _fresh(entry, ttl: float) -> bool:
    return entry is not None and time.monotonic() - entry[0] < ttl


def _acquire_probe_lock() -> bool:
    """Take _probe_lock, waiting at most _LOCK_PROBE_WAIT, unless a wait timed out less than
    _LOCK_STUCK_TTL ago: then it only tries, without waiting. A failed timed wait sets the
    marker; any successful acquire clears it. A failed try leaves the marker alone, so it
    expires on its own deadline and the next caller after it waits once more.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3"""
    global _probe_stuck_until
    stuck = time.monotonic() < _probe_stuck_until
    if _probe_lock.acquire(blocking=False) if stuck else _probe_lock.acquire(timeout=_LOCK_PROBE_WAIT):
        _probe_stuck_until = 0.0
        return True
    if not stuck:
        _probe_stuck_until = time.monotonic() + _LOCK_STUCK_TTL
    return False


@_safe("session_writer_locked", lambda: False)
def session_writer_locked(session_id: str) -> bool:
    """True when a running Codex process holds the thread's writer lock (so `codex resume`
    would refuse it). Fails open: any error is False.

    Order: validate the id; stat <id>.lock (missing is False, one stat); the 5 s per-id
    cache; the 1 s cache of a busy fallback; then, one probe at a time in this process
    (a wait of at most _LOCK_PROBE_WAIT), the probe under Codex's .coordination.lock. A
    busy coordination lock is retried 3 times at 5 ms (sleeping outside the process
    lock), then the last cached value, even an expired one, else False; that fallback is
    itself reused for 1 s. A process lock not obtained in time takes the same fallback;
    after such a timeout, callers for _LOCK_STUCK_TTL seconds do not wait for it at all.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return False
    key = session_id  # already lower-case: SESSION_ID_RE is
    lock_path = str(Path(CODEX_LOCKS_DIR) / f"{key}.lock")
    coord_path = str(Path(CODEX_LOCKS_DIR) / _COORDINATION)
    try:
        os.stat(lock_path)
    except OSError:
        return False
    stale = _lock_cache.get(key)
    if _fresh(stale, _LOCK_TTL):
        return stale[1]  # a fresh answer needs no lock at all
    busy = _busy_cache.get(key)
    if _fresh(busy, _LOCK_BUSY_TTL):
        return busy[1]

    def fallback(kind: str, why: str) -> bool:
        current = _lock_cache.get(key)  # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3: not `stale`, which another thread may have refreshed since
        answer = current[1] if current is not None else False
        _busy_cache.put(key, (time.monotonic(), answer))
        _warn(kind, note=why)
        return answer

    for attempt in range(_LOCK_RETRIES + 1):
        if not _acquire_probe_lock():
            return fallback("writer_lock.wait", "the probe lock was not free in time (an open or a lock call is stuck)")
        try:
            fresh = _lock_cache.get(key)  # another thread may have just probed this id
            if _fresh(fresh, _LOCK_TTL):
                return fresh[1]
            held = _probe_writer_lock(lock_path, coord_path)
            if held is not None:
                _lock_cache.put(key, (time.monotonic(), held))
                return held
        finally:
            _probe_lock.release()
        if attempt < _LOCK_RETRIES:
            time.sleep(_LOCK_RETRY_SLEEP)
    return fallback("writer_lock.busy", "the coordination lock stayed busy through the retry budget")


# --- Writer state (D10) --------------------------------------------------------------------
# 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 3
#
# `session_writer_locked` answers False for a probe that failed or could not run, which a caller
# that wants to know whether the thread is FREE cannot tell from "free". `session_writer_state`
# is its tri-state sibling: held, free or unknown. It shares the probe's rules (the coordination
# lock first, read-only opens, one probe at a time in this process) through the same helpers,
# but has caches of its own and never reads `_lock_cache` or `_busy_cache`, whose cached False
# for a busy or erroring probe would read as free here. `session_writer_locked` and its caches
# are not changed, so the Resume gate behaves as before on every platform.

_STATE_TTL = 5.0            # seconds a held or free answer is reused
_STATE_BUSY_TTL = 1.0       # seconds an unknown (or fallback) answer is reused
_STATE_STALE_MAX = 60.0     # a busy coordination lock falls back on a held or free answer this young
_state_cache = BoundedCache(512)        # id -> (monotonic time, "held" | "free"): definitive answers only
_state_busy_cache = BoundedCache(512)   # id -> (monotonic time, state): what a probe that could not run answered
WRITER_HELD, WRITER_FREE, WRITER_UNKNOWN = "held", "free", "unknown"


def _thread_lock_state(path: str, deferred: list) -> str:
    """held / free / unknown for <id>.lock. Runs under the coordination lock: only open,
    try-lock, unlock and close happen here (failures are queued in `deferred`, as in
    `_thread_lock_held`). A vanished file is free; an OSError opening or locking is unknown,
    not free."""
    try:
        fh = open_shared(path, "rb")
    except FileNotFoundError:
        return WRITER_FREE
    except OSError as exc:
        deferred.append(("writer_lock.open", exc, path))
        return WRITER_UNKNOWN
    try:
        try:
            got = _lock_byte(fh)
        except OSError as exc:
            deferred.append(("writer_lock.lock", exc, path))
            return WRITER_UNKNOWN
        if got:
            _release(fh, deferred, path)
            return WRITER_FREE
        return WRITER_HELD
    finally:
        fh.close()  # first: a Python handle on a lock file blocks Codex's removal of it


def _probe_writer_state(lock_path: str, coord_path: str) -> str | None:
    """held / free / unknown for the thread lock, None when the coordination lock is busy.
    An absent coordination file is unknown here (an older Codex build, so a free lock cannot
    be told from one never taken). Failures queued under the coordination lock are logged
    after it is released and closed."""
    deferred: list = []
    try:
        try:
            coord = open_shared(coord_path, "rb")
        except FileNotFoundError:
            return WRITER_UNKNOWN
        try:
            if not _lock_byte(coord):
                return None
            try:
                return _thread_lock_state(lock_path, deferred)
            finally:
                _release(coord, deferred)  # last: Codex's blocking lock() has no timeout
        finally:
            coord.close()
    finally:
        for kind, exc, path in deferred:
            try:
                _warn(kind, exc, path)
            except Exception:  # a failing log must neither replace the probe's result nor skip the rest
                pass


@_safe("session_writer_state", lambda: WRITER_UNKNOWN)
def session_writer_state(session_id: str) -> str:
    """`held` when another process holds the thread's writer lock, `free` when none does,
    `unknown` when this probe could not tell. Total: any error is `unknown`.

    free: the lock file is absent while `.coordination.lock` exists, or the probe took the
    lock itself. unknown: an invalid id; a stat error other than file-not-found; an OSError
    opening or locking <id>.lock; an absent `.coordination.lock`; a coordination lock busy
    through the retry budget with no held or free answer younger than _STATE_STALE_MAX. A
    held or free answer is reused for _STATE_TTL seconds and an unknown one for
    _STATE_BUSY_TTL only. The probe order, the retry budget and the process-wide lock are
    those of `session_writer_locked`.
    """
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return WRITER_UNKNOWN
    key = session_id
    lock_path = str(Path(CODEX_LOCKS_DIR) / f"{key}.lock")
    coord_path = str(Path(CODEX_LOCKS_DIR) / _COORDINATION)
    try:
        os.stat(lock_path)
    except FileNotFoundError:
        try:
            os.stat(coord_path)
        except OSError:
            return WRITER_UNKNOWN
        return WRITER_FREE
    except OSError:
        return WRITER_UNKNOWN
    known = _state_cache.get(key)
    if _fresh(known, _STATE_TTL):
        return known[1]
    busy = _state_busy_cache.get(key)
    if _fresh(busy, _STATE_BUSY_TTL):
        return busy[1]

    def settle(state: str) -> str:
        """Remember what a probe saw: held and free as definitive answers, unknown briefly."""
        if state == WRITER_UNKNOWN:
            _state_busy_cache.put(key, (time.monotonic(), state))
        else:
            _state_cache.put(key, (time.monotonic(), state))
        return state

    def fallback(kind: str, why: str) -> str:
        current = _state_cache.get(key)
        answer = current[1] if _fresh(current, _STATE_STALE_MAX) else WRITER_UNKNOWN
        _state_busy_cache.put(key, (time.monotonic(), answer))
        _warn(kind, note=why)
        return answer

    for attempt in range(_LOCK_RETRIES + 1):
        if not _acquire_probe_lock():
            return fallback("writer_state.wait", "the probe lock was not free in time (an open or a lock call is stuck)")
        try:
            fresh = _state_cache.get(key)  # another thread may have just probed this id
            if _fresh(fresh, _STATE_TTL):
                return fresh[1]
            state = _probe_writer_state(lock_path, coord_path)
            if state is not None:
                return settle(state)
        finally:
            _probe_lock.release()
        if attempt < _LOCK_RETRIES:
            time.sleep(_LOCK_RETRY_SLEEP)
    return fallback("writer_state.busy", "the coordination lock stayed busy through the retry budget")


# --- Test seam ---------------------------------------------------------------------


def _clear_caches() -> None:
    """Drop every cache and memo. Tests call it after each store mutation."""
    global _store_memo, _available_memo, _names_memo, _cache_cap, _verdict_cache, _parse_cache
    global _last_event_cache, _probe_stuck_until, _turn_cache
    with _store_lock:
        _store_memo = None
    _available_memo = None
    _names_memo = None
    with _caches_lock:
        _cache_cap = _CACHE_MIN
        _verdict_cache = BoundedCache(_cache_cap)
        _parse_cache = BoundedCache(_cache_cap)
        _last_event_cache = BoundedCache(_cache_cap)
        _turn_cache = BoundedCache(256)
    _missing.clear()
    _parse_failed.clear()
    _lock_cache.clear()
    _busy_cache.clear()
    _state_cache.clear()
    _state_busy_cache.clear()
    _probe_stuck_until = 0.0
    with _warn_lock:
        _warned.clear()
