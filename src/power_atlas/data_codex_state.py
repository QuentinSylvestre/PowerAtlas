"""Optional enrichment from Codex's state database (``state_<N>.sqlite`` in the Codex home).

The rollout scan in ``data_codex`` stays the authoritative record. This module only
reads a few columns of the database's ``threads`` table, for one consumer today:
``subagent_usage``, the lifetime token total of sub-agent threads (D4, D5 of
261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB).

Never ``immutable=1`` (it misses the newest row of a WAL database), never writes to the
database, never raises. Opening a WAL database read-only creates its ``-shm`` and
``-wal`` files when they are absent, which would write into Codex's folder. So the read
is skipped unless both the ``-wal`` and the ``-shm`` file exist (Codex is not running;
gate G9, the user's choice 2026-10-05) and the status says ``idle``. A process that
exits between that check and the open can still leave the open to create them.

Every value read from the file is untrusted: columns are cut with ``substr``, a result
that hit the row cap is dropped whole, and any failure gives None with the exception
class logged once a minute through ``quiet_log`` (no path, no id).

This module imports from ``data_codex`` only the Codex home, ``activity_epoch`` and
``canonical_rollouts``, and must not import ``overview``, ``web`` or ``presence``.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import quiet_log
from .data_codex import _CODEX_HOME, activity_epoch, canonical_rollouts

log = logging.getLogger(__name__)

# Tests redirect this like the other Codex folders.
CODEX_STATE_DIR = _CODEX_HOME

_STATE_NAME_RE = re.compile(r"state_(\d+)\.sqlite")
_REQUIRED_COLUMNS = frozenset({"source", "cwd", "tokens_used", "created_at_ms", "archived",
                               "updated_at_ms"})
_ROW_LIMIT = 20000
# One bound parameter and a LIMIT; no name comes from data. A cwd is cut at 260
# characters here, and `cwd_class` rejects one of 260 or more, so a cut path never
# reaches the hidden-workspace check (it could stop matching a hidden workspace's key).
_SUBAGENT_SQL = ("SELECT substr(source, 1, 96), substr(cwd, 1, 260), tokens_used, "
                 "created_at_ms FROM threads WHERE archived = 0 AND source LIKE '{\"subagent\"%' "
                 f"AND created_at_ms >= ? LIMIT {_ROW_LIMIT + 1}")
_NEWEST_SQL = "SELECT max(updated_at_ms) FROM threads WHERE archived = 0"

_CONNECT_TIMEOUT_S = 1.0
_QUERY_BUDGET_S = 1.5  # a query that runs longer is aborted by the progress handler
_PROGRESS_OPS = 1000  # SQLite virtual-machine steps between two handler calls
_MEMO_TTL_S = 30.0
_FRESH_MARGIN_S = 1800.0
_NEWEST_ROLLOUTS = 20
_MAX_TOKENS = 10 ** 15
_MAX_TOTAL = 2 ** 53 - 1
_MAX_CWD = 260

_clock = time.monotonic  # tests replace it to step the memo
_memo_lock = threading.Lock()
_memo: dict[tuple, tuple[float, list | None, str]] = {}


# --- Pure helpers ---------------------------------------------------------------

def strip_extended_prefix(raw: str) -> str:
    r"""`raw` without a Windows extended-length prefix, with `\` for `/`:
    ``\\?\UNC\srv\share`` becomes ``\\srv\share`` and ``\\?\C:\x`` becomes ``C:\x``."""
    flat = raw.replace("/", "\\")
    if flat.startswith("\\\\?\\"):
        rest = flat[4:]
        if rest[:4].upper() == "UNC\\":
            return "\\\\" + rest[4:]
        return rest
    return flat


def cwd_class(raw) -> str:
    """Classify a stored cwd without touching the filesystem.

    ``local`` (a drive-letter path), or why it is not trusted: ``empty`` (missing or
    blank), ``long`` (260 or more characters, which may be a cut path), ``network``
    (UNC, device or ``//server/share`` forms in any letter case) and ``relative``.
    Only ``local`` goes on to the hidden-workspace check, because that check expands
    8.3 short names with a filesystem call, which for a network share is network
    traffic from a usage request.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "empty"
    if len(raw) >= _MAX_CWD:
        return "long"
    clean = strip_extended_prefix(raw)
    if clean.startswith("\\\\"):
        return "network"
    if re.match(r"[A-Za-z]:\\", clean):
        return "local"
    return "relative"


def state_db_path() -> Path | None:
    """The state database: the file whose whole name is ``state_<N>.sqlite`` with the
    highest N, directly in the Codex home (not in a sub-folder: a stale copy sits in
    one). Never falls back to a lower number: when the highest-numbered entry is not a
    plain file (a directory, a link), the answer is None."""
    best: tuple[int, str, bool] | None = None
    try:
        with os.scandir(CODEX_STATE_DIR) as entries:
            for entry in entries:
                match = _STATE_NAME_RE.fullmatch(entry.name)
                if match:
                    number = int(match.group(1))
                    if best is None or number > best[0]:
                        best = (number, entry.path, entry.is_file(follow_symlinks=False))
    except OSError:
        return None
    return Path(best[1]) if best and best[2] else None


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- The read -------------------------------------------------------------------

def _newest_rollout_activity() -> float | None:
    """The newest ``activity_epoch`` among the 20 most recently modified top-level
    rollouts (the mtime of a held rollout is frozen on Windows, so the newest 20 by
    mtime are ranked again by activity)."""
    newest: float | None = None
    for rollout in sorted(canonical_rollouts(), key=lambda r: -r.mtime_ns)[:_NEWEST_ROLLOUTS]:
        try:
            value = activity_epoch(rollout.path, os.stat(rollout.path))
        except OSError:
            continue
        if newest is None or value > newest:
            newest = value
    return newest


def _query(path: Path, window_start_ms: int) -> tuple[list | None, str]:
    """Open the database read-only, run the two fixed statements, close it."""
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
    # One undecodable value must not fail the whole read: it becomes replacement
    # characters, and `cwd_class` then leaves that row out.
    con.text_factory = lambda raw: raw.decode("utf-8", "replace")
    try:
        deadline = time.monotonic() + _QUERY_BUDGET_S
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, _PROGRESS_OPS)
        con.execute("PRAGMA query_only=1")
        con.execute("PRAGMA trusted_schema=OFF")
        columns = {row[1] for row in con.execute("PRAGMA table_info(threads)")}
        if not _REQUIRED_COLUMNS <= columns:
            quiet_log.warn(log, "codex state database has an unexpected schema")
            return None, "error"
        newest_ms = _int(con.execute(_NEWEST_SQL).fetchone()[0])
        rows = con.execute(_SUBAGENT_SQL, (window_start_ms,)).fetchall()
    finally:
        con.close()
    newest_rollout = _newest_rollout_activity()
    # Fresh only when the database has seen activity as recent as the rollouts (D4).
    # With no rollout to compare against, or a database with no usable timestamp, it
    # counts as stale.
    if newest_rollout is None or newest_ms is None or newest_ms / 1000.0 < newest_rollout - _FRESH_MARGIN_S:
        return None, "stale"
    if len(rows) > _ROW_LIMIT:
        return None, "error"  # a partial total is never shown
    return rows, "ok"


def _read(window_start_ms: int) -> tuple[list | None, str]:
    path = state_db_path()
    if path is None:
        return None, "absent"
    # Both side files exist only while a process holds the database open in WAL mode.
    # Opening it read-only when either is missing would create it in Codex's folder.
    if not (Path(str(path) + "-wal").is_file() and Path(str(path) + "-shm").is_file()):
        return None, "idle"
    try:
        return _query(path, window_start_ms)
    except Exception as exc:
        if not path.exists():
            return None, "absent"  # removed between the scan and the open: silent
        quiet_log.warn(log, "codex state database read failed", exc)
        return None, "error"


def _memoised(window_start_ms: int, stop_event) -> tuple[list | None, str]:
    """The raw rows (not filtered), reused for about 30 s; a failure is reused too."""
    key = (str(CODEX_STATE_DIR), window_start_ms)
    now = _clock()
    with _memo_lock:
        hit = _memo.get(key)
        if hit is not None and now - hit[0] < _MEMO_TTL_S:
            return hit[1], hit[2]
    rows, status = _read(window_start_ms)
    if stop_event is not None and stop_event.is_set():
        return None, "error"  # a stopped refresh is not memoised
    with _memo_lock:
        if len(_memo) > 8:
            _memo.clear()
        _memo[key] = (now, rows, status)
    return rows, status


def clear_memo() -> None:
    """Drop the memo, for the tests' autouse fixtures."""
    with _memo_lock:
        _memo.clear()


# --- The consumer ---------------------------------------------------------------

def _tally(rows: list, is_hidden) -> dict:
    total = spawn = guardian = threads = 0
    hidden_memo: dict[str, bool] = {}
    for source, cwd, tokens, created in rows:
        if _int(tokens) is None or _int(created) is None or not isinstance(source, str):
            continue
        if cwd_class(cwd) != "local":
            continue  # fail closed: a long, network, relative or missing cwd is left out
        clean = strip_extended_prefix(cwd)
        if clean not in hidden_memo:
            hidden_memo[clean] = bool(is_hidden(clean))
        if hidden_memo[clean]:
            continue
        value = tokens if 0 <= tokens <= _MAX_TOKENS else 0
        threads += 1
        total += value
        # thread_spawn is tested first; a source holding neither string counts in the
        # total only. The source is cut at 96 characters, so it is never parsed as JSON.
        if '"thread_spawn"' in source:
            spawn += value
        elif '"guardian"' in source:
            guardian += value
    return {"threads": threads, "total": min(total, _MAX_TOTAL),
            "thread_spawn": min(spawn, _MAX_TOTAL), "guardian": min(guardian, _MAX_TOTAL)}


def subagent_usage(window_start_epoch: float, shown, is_hidden, stop_event=None) -> tuple[dict | None, str]:
    """(usage, status): the lifetime token total of sub-agent threads created since
    `window_start_epoch`, from the state database, and why there is none when there is none.

    `usage` is None without a usable database, else ``{"threads", "total",
    "thread_spawn", "guardian"}``. `status` is ``ok``, ``absent`` (no database), ``idle``
    (no ``-wal`` file: Codex is not running), ``stale`` (the freshness guard rejected it)
    or ``error``. `shown(provider)` and `is_hidden(cwd)` are the rail's filters; a Codex
    that is not shown gives ``(None, "absent")``. Never raises.
    """
    try:
        if stop_event is not None and stop_event.is_set():
            return None, "error"
        if not shown("codex"):
            return None, "absent"
        rows, status = _memoised(int(window_start_epoch * 1000), stop_event)
        if rows is None:
            return None, status
        if stop_event is not None and stop_event.is_set():
            return None, "error"
        return _tally(rows, is_hidden), "ok"
    except Exception as exc:
        quiet_log.warn(log, "codex sub-agent usage failed", exc)
        return None, "error"
