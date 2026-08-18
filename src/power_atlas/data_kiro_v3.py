"""kiro-cli v3 session adapter: discovery, parsing, and caching.

Reads sessions from the kiro-cli v3 store:
    ~/.kiro/sessions/<workspace-hash>/sess_<uuid>/
        session.json   -- session metadata (workspacePaths, createdAt, etc.)
        messages.jsonl -- conversation (v3 envelope: {id, timestamp, payload})

The v3 store lives under V3_SESSIONS_ROOT and coexists with the v2 "cli/"
subdirectory. Hash-dir names that appear in _V3_EXCLUDED_NAMES are skipped.
"""

import collections
import json
import threading
import time
from pathlib import Path

from .data import BoundedCache, Session, _FileInfo, _normalize_path, _cap_text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

V3_SESSIONS_ROOT = Path.home() / ".kiro" / "sessions"

# Subdirs of V3_SESSIONS_ROOT that are not workspace-hash dirs
_V3_EXCLUDED_NAMES: frozenset[str] = frozenset({"cli"})


# ---------------------------------------------------------------------------
# Session-ID helper
# ---------------------------------------------------------------------------

def _ensure_sess_prefix(session_id: str) -> str:
    """Normalize a session ID to the sess_<uuid> form."""
    return session_id if session_id.startswith("sess_") else f"sess_{session_id}"


# ---------------------------------------------------------------------------
# Module-level index cache
#
# Cache invalidation strategy:
#   - _root_mtime tracks V3_SESSIONS_ROOT.stat().st_mtime (detects new hash dirs)
#   - _session_json_mtimes tracks each session.json mtime keyed as "hash/sess_uuid"
#     (detects new/modified sessions within existing hash dirs)
#
# Why session.json, not hash-dir mtime: messages.jsonl is written on every
# agent turn, so hash-dir mtime changes constantly.  session.json changes only
# on creation / title update — the correct signal for workspace membership.
# Message-content freshness is handled separately via per-session file_stats.
#
# Thread-safety design:
#   - All filesystem I/O (mtime scan + session.json reads) is done OUTSIDE
#     _index_lock in a single pass, building new_json_mtimes and new_index
#     simultaneously.
#   - _index_lock is acquired only to compare cached state and swap atomically.
#     This prevents holding the lock across slow I/O while still ensuring a
#     consistent view of the cache.
# ---------------------------------------------------------------------------

_index_lock: threading.Lock = threading.Lock()
_root_mtime: float | None = None
_session_json_mtimes: dict[str, float] = {}   # "hash_name/sess_uuid" -> session.json mtime
_cwd_index: dict[str, list[tuple[str, str]]] = {}   # norm_cwd -> [(hash_name, sess_dir_name)]
_norm_cwd_to_hash: dict[str, str] = {}    # norm_cwd -> hash_dir_name (for refresh lookup)
# One cwd maps to exactly one hash dir by kiro-cli's workspace-hash design.
# _norm_cwd_to_hash records the LAST hash dir seen for a cwd (last-write-wins).
# Multi-hash-dir scenarios are not expected; if they occur, only the last hash
# dir is used for refresh checks — a conservative safe degradation.
_cwd_display: dict[str, tuple[str, str]] = {}  # norm_cwd -> (display_cwd, max_updated_at)


def _cwd_to_sessions() -> dict[str, list[tuple[str, str]]]:
    """Return cached cwd index, rebuilding if any session.json mtime has changed.

    Two-phase design:
    Phase 1 (fast): collect session.json mtimes only — O(n) stat() calls, no
        JSON reads.  Compare against cached mtimes under _index_lock.  Return
        cached index immediately if nothing changed (common case).
    Phase 2 (rebuild): only on cache miss — read session.json files to parse
        cwd and display metadata.  All I/O outside _index_lock; swap atomically.

    The lock is NEVER held across filesystem I/O.
    """
    global _root_mtime, _session_json_mtimes, _cwd_index, _norm_cwd_to_hash, _cwd_display

    if not V3_SESSIONS_ROOT.is_dir():
        return {}
    try:
        current_root_mtime = V3_SESSIONS_ROOT.stat().st_mtime
    except OSError:
        return {}

    # Phase 1: fast mtime-only scan (no JSON reads)
    new_json_mtimes: dict[str, float] = {}
    scan_error = False
    try:
        for hash_dir in V3_SESSIONS_ROOT.iterdir():
            if not hash_dir.is_dir() or hash_dir.name in _V3_EXCLUDED_NAMES:
                continue
            try:
                for sess_dir in hash_dir.iterdir():
                    if not sess_dir.is_dir():
                        continue
                    session_json = sess_dir / "session.json"
                    try:
                        new_json_mtimes[f"{hash_dir.name}/{sess_dir.name}"] = \
                            session_json.stat().st_mtime
                    except OSError:
                        pass
            except OSError:
                continue
    except OSError:
        scan_error = True

    if scan_error:
        # Partial walk — return cached state rather than swapping in a truncated index
        with _index_lock:
            return _cwd_index.copy()

    # Check under lock whether rebuild is needed
    with _index_lock:
        if (current_root_mtime == _root_mtime
                and new_json_mtimes == _session_json_mtimes):
            return _cwd_index.copy()

    # Phase 2: rebuild — parse session.json (outside lock)
    new_index: dict[str, list[tuple[str, str]]] = {}
    new_norm_cwd_to_hash: dict[str, str] = {}
    new_cwd_display: dict[str, tuple[str, str]] = {}  # norm_cwd -> (display_cwd, max_updated_at)
    rebuild_error = False
    try:
        for hash_dir in V3_SESSIONS_ROOT.iterdir():
            if not hash_dir.is_dir() or hash_dir.name in _V3_EXCLUDED_NAMES:
                continue
            try:
                for sess_dir in hash_dir.iterdir():
                    if not sess_dir.is_dir():
                        continue
                    session_json = sess_dir / "session.json"
                    try:
                        with session_json.open(encoding="utf-8", errors="replace") as f:
                            data = json.loads(f.read())
                        if not isinstance(data, dict):
                            continue
                        cwd = (data.get("workspacePaths") or [""])[0]
                        if not cwd:
                            continue
                        norm = _normalize_path(cwd)
                        new_index.setdefault(norm, []).append(
                            (hash_dir.name, sess_dir.name)
                        )
                        new_norm_cwd_to_hash[norm] = hash_dir.name
                        # Track original cwd and max lastModifiedAt for discover_workspaces
                        updated = data.get("lastModifiedAt", "")
                        prev_display, prev_updated = new_cwd_display.get(norm, (cwd, ""))
                        new_cwd_display[norm] = (
                            cwd,
                            updated if updated > prev_updated else prev_updated,
                        )
                    except (OSError, json.JSONDecodeError, ValueError):
                        continue
            except OSError:
                continue
    except OSError:
        rebuild_error = True

    if rebuild_error:
        with _index_lock:
            return _cwd_index.copy()

    # Atomic swap under lock
    with _index_lock:
        _root_mtime = current_root_mtime
        _session_json_mtimes = new_json_mtimes
        _cwd_index = new_index
        _norm_cwd_to_hash = new_norm_cwd_to_hash
        _cwd_display = new_cwd_display
        return new_index.copy()


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def _extract_v3_content(line: str, msg_type: str) -> str:
    """Extract text from a v3 JSONL line of a given payload type.

    Returns "" for image-only messages (no text blocks) — intentional.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return ""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return ""
    if payload.get("type") != msg_type:
        return ""
    content = payload.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Prompt extraction with caching
# ---------------------------------------------------------------------------

_prompts_cache: BoundedCache = BoundedCache(2048)


def _extract_prompts_v3(messages_path: Path) -> tuple[str, str, str]:
    """Extract first_prompt, last_prompt, last_reply_tail from messages.jsonl.

    Opens the file once, reads all lines, uses lines[:50] for first_prompt
    scan and lines[-100:] (deque-equivalent slice) for last_prompt and
    last_reply_tail.  Single open eliminates the double-read of the old
    implementation.
    """
    if not messages_path.exists():
        return "", "", ""

    try:
        with messages_path.open(encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "", "", ""

    # Scan first 50 lines for first_prompt
    first_prompt = ""
    for line in lines[:50]:
        text = _extract_v3_content(line, "user")
        if text:
            first_prompt = _cap_text(text)
            break

    # Scan last 100 lines for last_prompt and last_reply_tail
    tail = lines[-100:] if len(lines) > 100 else lines
    last_prompt = ""
    last_reply_tail = ""
    for line in reversed(tail):
        if not last_prompt:
            text = _extract_v3_content(line, "user")
            if text:
                last_prompt = _cap_text(text)
        if not last_reply_tail:
            text = _extract_v3_content(line, "assistant")
            if text:
                last_reply_tail = _cap_text(text)
        if last_prompt and last_reply_tail:
            break

    return first_prompt, last_prompt, last_reply_tail


def _extract_prompts_v3_cached(
    messages_path: Path, st: _FileInfo | None
) -> tuple[str, str, str]:
    """_extract_prompts_v3 memoized on (mtime, size)."""
    if st is None:
        return _extract_prompts_v3(messages_path)

    cache_key = str(messages_path)
    cached = _prompts_cache.get(cache_key)
    if cached is not None:
        c_mtime, c_size, parsed = cached
        if c_mtime == st.mtime and c_size == st.size:
            return parsed

    parsed = _extract_prompts_v3(messages_path)
    _prompts_cache.put(cache_key, (st.mtime, st.size, parsed))
    return parsed


# ---------------------------------------------------------------------------
# Per-session path finder
# ---------------------------------------------------------------------------

def _find_v3_session_path(session_id: str) -> Path | None:
    """Find the messages.jsonl path for a session by scanning hash dirs.

    Returns None if not found or on any OSError.
    """
    if not V3_SESSIONS_ROOT.is_dir():
        return None
    sess_dir_name = _ensure_sess_prefix(session_id)
    try:
        for hash_dir in V3_SESSIONS_ROOT.iterdir():
            if not hash_dir.is_dir() or hash_dir.name in _V3_EXCLUDED_NAMES:
                continue
            candidate = hash_dir / sess_dir_name / "messages.jsonl"
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Return True if any workspace-hash dir exists under V3_SESSIONS_ROOT."""
    if not V3_SESSIONS_ROOT.is_dir():
        return False
    try:
        return any(
            e.is_dir() and e.name not in _V3_EXCLUDED_NAMES
            for e in V3_SESSIONS_ROOT.iterdir()
        )
    except OSError:
        return False


def discover_workspaces() -> list[tuple[str, int, str]]:
    """Discover v3 workspaces.

    Returns list of (cwd, session_count, updated_at) tuples sorted by recency.

    Uses _cwd_to_sessions() for enumeration and _cwd_display for display
    metadata (original cwd string and max lastModifiedAt).  No independent
    walk of V3_SESSIONS_ROOT and no second read of session.json files.
    """
    index = _cwd_to_sessions()  # also populates _cwd_display as a side effect
    if not index:
        return []

    with _index_lock:
        display_snapshot = _cwd_display.copy()

    results = []
    for norm_cwd, entries in index.items():
        display_cwd, max_updated = display_snapshot.get(norm_cwd, (norm_cwd, ""))
        results.append((display_cwd, len(entries), max_updated))

    results.sort(key=lambda x: x[2], reverse=True)
    return results


def load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Load v3 sessions for a given workspace. Returns (sessions, file_stats)."""
    sessions: list[Session] = []
    file_stats: dict[str, _FileInfo] = {}

    if not V3_SESSIONS_ROOT.is_dir():
        return sessions, file_stats

    norm_cwd = _normalize_path(cwd)
    index = _cwd_to_sessions()

    for hash_name, sess_dir_name in index.get(norm_cwd, []):
        sess_dir = V3_SESSIONS_ROOT / hash_name / sess_dir_name
        session_json_path = sess_dir / "session.json"
        messages_path = sess_dir / "messages.jsonl"

        # Read session.json
        try:
            sj_st = session_json_path.stat()
        except OSError:
            continue
        try:
            with session_json_path.open(encoding="utf-8", errors="replace") as f:
                data = json.loads(f.read())
            if not isinstance(data, dict):
                continue
        except (OSError, json.JSONDecodeError, ValueError):
            continue

        cwd_from_data = (data.get("workspacePaths") or [""])[0]
        if not cwd_from_data:
            continue

        file_stats[str(session_json_path)] = _FileInfo(
            mtime=sj_st.st_mtime, size=sj_st.st_size
        )

        # Track messages.jsonl stat (may not exist)
        msg_fi: _FileInfo | None = None
        try:
            msg_st = messages_path.stat()
            msg_fi = _FileInfo(mtime=msg_st.st_mtime, size=msg_st.st_size)
            file_stats[str(messages_path)] = msg_fi
        except OSError:
            pass

        first_prompt, last_prompt, last_reply_tail = _extract_prompts_v3_cached(
            messages_path, msg_fi
        )

        sessions.append(Session(
            session_id=data.get("id", sess_dir_name),
            title=data.get("title", "<untitled>"),
            cwd=cwd_from_data,
            created_at=data.get("createdAt", ""),
            updated_at=data.get("lastModifiedAt", ""),
            first_prompt=first_prompt,
            last_prompt=last_prompt,
            last_reply_tail=last_reply_tail,
            extra_fields={"agentMode": data.get("agentMode", "")},
        ))

    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions, file_stats


def refresh_stale_entries_for_cwd(
    norm_cwd: str, old_stats: dict[str, _FileInfo]
) -> bool:
    """Return True if any tracked file has changed or new sessions appeared.

    New-session detection compares only sess_* dirs that belong to norm_cwd.
    The set of "known sessions for norm_cwd" is derived from the file-stat
    paths in old_stats (the session.json / messages.jsonl paths that were
    tracked when the cwd was last loaded).  This avoids false-positive stale
    detection when a sibling cwd gets a new session in the same hash directory:
    sess_dirs for other cwds are never in tracked_sess_dirs, so they do not
    appear in the "new dirs for norm_cwd" delta.
    """
    if not V3_SESSIONS_ROOT.is_dir():
        return True  # Root gone -- all cached stats are invalid

    # Check all tracked files for changes
    for path_str, fi in old_stats.items():
        try:
            st = Path(path_str).stat()
            if st.st_mtime != fi.mtime or st.st_size != fi.size:
                return True
        except OSError:
            return True  # file deleted

    # Check for new sessions: derive which sessions we were tracking for
    # norm_cwd by extracting parent directory names from old_stats paths.
    # old_stats only contains paths for sessions loaded under norm_cwd, so
    # sibling-cwd sessions never appear in tracked_sess_dirs.
    with _index_lock:
        hash_name = _norm_cwd_to_hash.get(norm_cwd)
    if hash_name is None:
        # Index not yet built or cwd not in any session -- force reload
        return True

    try:
        hash_dir = V3_SESSIONS_ROOT / hash_name
        hash_dir_str = str(hash_dir)

        # Session dirs we were tracking for norm_cwd (from file-stat paths)
        tracked_sess_dirs: set[str] = set()
        for path_str in old_stats:
            p = Path(path_str)
            # Path structure: .../hash_name/sess_<uuid>/session.json
            if str(p.parent.parent) == hash_dir_str:
                tracked_sess_dirs.add(p.parent.name)

        # Current sess_* dirs in the hash dir (all cwds combined)
        current_sess_dirs = {
            e.name for e in hash_dir.iterdir()
            if e.is_dir() and e.name.startswith("sess_")
        }

        # A previously tracked session was deleted
        if not tracked_sess_dirs.issubset(current_sess_dirs):
            return True

        # New sess_* dirs appeared: only trigger stale if one maps to norm_cwd.
        # This prevents a sibling-cwd addition from marking norm_cwd stale.
        # We read at most one session.json per new dir -- bounded, cheap.
        new_dirs = current_sess_dirs - tracked_sess_dirs
        for sess_dir_name in new_dirs:
            session_json = hash_dir / sess_dir_name / "session.json"
            try:
                with session_json.open(encoding="utf-8", errors="replace") as f:
                    data = json.loads(f.read())
                if not isinstance(data, dict):
                    continue
                cwd = (data.get("workspacePaths") or [""])[0]
                if cwd and _normalize_path(cwd) == norm_cwd:
                    return True  # New session belongs to THIS cwd
            except (OSError, json.JSONDecodeError, ValueError):
                continue

    except OSError:
        return True

    return False


def find_session_workspace(session_id: str) -> str | None:
    """Find the workspace (cwd) for a given v3 session ID.

    Scans all hash dirs for a matching sess_<uuid>/session.json.
    Returns workspacePaths[0] or None.
    """
    if not V3_SESSIONS_ROOT.is_dir():
        return None
    sess_dir_name = _ensure_sess_prefix(session_id)
    try:
        for hash_dir in V3_SESSIONS_ROOT.iterdir():
            if not hash_dir.is_dir() or hash_dir.name in _V3_EXCLUDED_NAMES:
                continue
            session_json = hash_dir / sess_dir_name / "session.json"
            try:
                if not session_json.is_file():
                    continue
                with session_json.open(encoding="utf-8", errors="replace") as f:
                    data = json.loads(f.read())
                if not isinstance(data, dict):
                    continue
                cwd = (data.get("workspacePaths") or [""])[0]
                return cwd or None
            except (OSError, json.JSONDecodeError, ValueError):
                continue
    except OSError:
        return None
    return None


# ---------------------------------------------------------------------------
# Per-session tail / first-prompt with BoundedCache
# ---------------------------------------------------------------------------

_tail_cache: BoundedCache = BoundedCache(512)   # session_id -> (time, mtime, lines)
_first_prompt_cache: BoundedCache = BoundedCache(512)  # session_id -> (time, mtime, result)

_TAIL_CACHE_TTL = 5    # seconds
_FIRST_PROMPT_TTL = 60  # seconds


def get_session_tail(session_id: str, cwd: str = "", max_lines: int = 15) -> list[str]:
    """Extract last N assistant message texts from a v3 session.

    Cached 5 s + mtime guard.  Empty tail is a valid transient state (session
    just started) and is NOT cached to avoid persisting stale emptiness across
    the TTL window.
    """
    messages_path = _find_v3_session_path(session_id)
    if messages_path is None:
        return []
    try:
        st = messages_path.stat()
        current_mtime = st.st_mtime
    except OSError:
        return []

    cached = _tail_cache.get(session_id)
    if cached is not None:
        c_time, c_mtime, lines = cached
        if (time.time() - c_time < _TAIL_CACHE_TTL) and c_mtime == current_mtime:
            return list(lines)

    try:
        with open(messages_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            read_size = min(size, 131072)
            fh.seek(size - read_size)
            tail_bytes = fh.read()
        lines_raw = tail_bytes.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []

    messages: list[str] = []
    for line in reversed(lines_raw):
        text = _extract_v3_content(line, "assistant")
        if text:
            messages.append(_cap_text(text))
            if len(messages) >= max_lines:
                break
    messages.reverse()

    _tail_cache.put(session_id, (time.time(), current_mtime, messages))
    return list(messages)


def get_first_prompt(session_id: str, cwd: str = "") -> str:
    """Extract first user message from a v3 session.

    Cached 60 s + mtime guard.  Negative results (no user message in first 50
    lines) are also cached so repeated calls against an empty/tool-only session
    do not re-scan the file on every TTL miss.
    """
    messages_path = _find_v3_session_path(session_id)
    if messages_path is None:
        return ""
    try:
        st = messages_path.stat()
        current_mtime = st.st_mtime
    except OSError:
        return ""

    cache_key = session_id
    cached = _first_prompt_cache.get(cache_key)
    if cached is not None:
        c_time, c_mtime, result = cached
        if (time.time() - c_time < _FIRST_PROMPT_TTL) and c_mtime == current_mtime:
            return result

    first_prompt = ""
    try:
        with open(messages_path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 50:
                    break
                text = _extract_v3_content(line, "user")
                if text:
                    first_prompt = _cap_text(text)
                    break
    except OSError:
        pass

    # Cache both positive and negative results — negative cache avoids re-scanning
    # a tool-only session on every TTL miss (M5 fix).
    _first_prompt_cache.put(cache_key, (time.time(), current_mtime, first_prompt))
    return first_prompt
