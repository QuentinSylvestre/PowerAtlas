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
#   - _hash_dir_mtimes tracks each session.json mtime keyed as "hash/sess_uuid"
#     (detects new/modified sessions within existing hash dirs)
#
# Why session.json, not hash-dir mtime: messages.jsonl is written on every
# agent turn, so hash-dir mtime changes constantly.  session.json changes only
# on creation / title update — the correct signal for workspace membership.
# Message-content freshness is handled separately via per-session file_stats.
# ---------------------------------------------------------------------------

_index_lock: threading.Lock = threading.Lock()
_root_mtime: float | None = None
_hash_dir_mtimes: dict[str, float] = {}   # "hash_name/sess_uuid" -> session.json mtime
_cwd_index: dict[str, list[tuple[str, str]]] = {}   # norm_cwd -> [(hash_name, sess_dir_name)]
_norm_cwd_to_hash: dict[str, str] = {}    # norm_cwd -> hash_dir_name (for refresh lookup)


def _cwd_to_sessions() -> dict[str, list[tuple[str, str]]]:
    """Return cached cwd index, rebuilding if any session.json mtime has changed."""
    global _root_mtime, _hash_dir_mtimes, _cwd_index, _norm_cwd_to_hash

    if not V3_SESSIONS_ROOT.is_dir():
        return {}
    try:
        current_root_mtime = V3_SESSIONS_ROOT.stat().st_mtime
    except OSError:
        return {}

    with _index_lock:
        # Collect current session.json mtimes to compare
        new_json_mtimes: dict[str, float] = {}
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
                            mtime = session_json.stat().st_mtime
                            key = f"{hash_dir.name}/{sess_dir.name}"
                            new_json_mtimes[key] = mtime
                        except OSError:
                            pass
                except OSError:
                    pass
        except OSError:
            return _cwd_index.copy()

        # Use cached index if nothing has changed
        if (current_root_mtime == _root_mtime
                and new_json_mtimes == _hash_dir_mtimes):
            return _cwd_index.copy()

        # Full rebuild
        new_index: dict[str, list[tuple[str, str]]] = {}
        new_norm_cwd_to_hash: dict[str, str] = {}
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
                        except (OSError, json.JSONDecodeError, ValueError):
                            continue
                except OSError:
                    continue
        except OSError:
            pass

        _root_mtime = current_root_mtime
        _hash_dir_mtimes = new_json_mtimes
        _cwd_index = new_index
        _norm_cwd_to_hash = new_norm_cwd_to_hash
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

    Reads first 50 lines for first_prompt, last 100 lines (deque) for
    last_prompt and last_reply_tail.
    """
    first_prompt = ""
    last_prompt = ""
    last_reply_tail = ""

    if not messages_path.exists():
        return first_prompt, last_prompt, last_reply_tail

    try:
        with open(messages_path, encoding="utf-8", errors="replace") as fh:
            # First 50 lines for first_prompt
            for i, line in enumerate(fh):
                if i >= 50:
                    break
                text = _extract_v3_content(line, "user")
                if text:
                    first_prompt = _cap_text(text)[:200]
                    break
            # Tail via deque — read rest of file to get last 100 lines
            # We re-open to get the full tail after the initial head scan
    except OSError:
        return first_prompt, last_prompt, last_reply_tail

    try:
        with open(messages_path, encoding="utf-8", errors="replace") as fh:
            tail = collections.deque(fh, maxlen=100)
    except OSError:
        return first_prompt, last_prompt, last_reply_tail

    for line in reversed(tail):
        if not last_reply_tail:
            text = _extract_v3_content(line, "assistant")
            if text:
                last_reply_tail = _cap_text(text)[:100]
        if not last_prompt:
            text = _extract_v3_content(line, "user")
            if text:
                last_prompt = _cap_text(text)[:200]
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
    """
    # Build cwd -> (count, max_updated_at, display_cwd) mapping
    workspace_info: dict[str, tuple[int, str, str]] = {}

    if not V3_SESSIONS_ROOT.is_dir():
        return []

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
                        updated = data.get("lastModifiedAt", "")
                        if norm not in workspace_info:
                            workspace_info[norm] = (1, updated, cwd)
                        else:
                            count, max_upd, disp = workspace_info[norm]
                            new_upd = updated if updated > max_upd else max_upd
                            workspace_info[norm] = (count + 1, new_upd, disp)
                    except (OSError, json.JSONDecodeError, ValueError):
                        continue
            except OSError:
                continue
    except OSError:
        return []

    results = [
        (info[2], info[0], info[1])
        for info in workspace_info.values()
    ]
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
    """Return True if any tracked file has changed or new sessions appeared."""
    if not V3_SESSIONS_ROOT.is_dir():
        return True  # Root gone — all cached stats are invalid

    # Check all tracked files for changes
    for path_str, fi in old_stats.items():
        try:
            st = Path(path_str).stat()
            if st.st_mtime != fi.mtime or st.st_size != fi.size:
                return True
        except OSError:
            return True  # file deleted

    # Check for new sessions using cached hash dir mapping.
    # Do NOT call _cwd_to_sessions() here — that triggers a full index rebuild
    # on every refresh tick. Instead use the cached reverse mapping.
    hash_name = _norm_cwd_to_hash.get(norm_cwd)
    if hash_name is None:
        # Index not yet built or cwd not in any session — force reload
        return True

    try:
        hash_dir = V3_SESSIONS_ROOT / hash_name
        if not hash_dir.is_dir():
            return True
        current_sess_dirs = {e.name for e in hash_dir.iterdir() if e.is_dir()}
        cached_sess_dirs = {
            sess_name for _, sess_name in _cwd_index.get(norm_cwd, [])
        }
        if current_sess_dirs != cached_sess_dirs:
            return True
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
    """Extract last N assistant message texts from a v3 session. Cached 5s + mtime guard."""
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
    """Extract first user message from a v3 session. Cached 60s + mtime guard."""
    messages_path = _find_v3_session_path(session_id)
    if messages_path is None:
        return ""
    try:
        st = messages_path.stat()
        current_mtime = st.st_mtime
    except OSError:
        return ""

    cached = _first_prompt_cache.get(session_id)
    if cached is not None:
        c_time, c_mtime, result = cached
        if (time.time() - c_time < _FIRST_PROMPT_TTL) and c_mtime == current_mtime:
            return result

    try:
        with open(messages_path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 50:
                    break
                text = _extract_v3_content(line, "user")
                if text:
                    result = _cap_text(text)
                    _first_prompt_cache.put(session_id, (time.time(), current_mtime, result))
                    return result
    except OSError:
        pass

    # No user message found in first 50 lines — don't negative-cache
    return ""
