"""Kiro-CLI session adapter: discovery, parsing, and caching."""

import collections
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from .data import Session, _FileInfo, _normalize_path, _cap_text

SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"


def _sqlite_path() -> Path:
    """Platform-appropriate path to kiro-cli conversation database."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", ""))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    return base / "Kiro-Cli" / "data.sqlite3"


SQLITE_PATH = _sqlite_path()


def _open_sqlite_readonly() -> sqlite3.Connection | None:
    """Open sqlite read-only with busy_timeout=5000. Returns None if unavailable."""
    try:
        uri = f"file:{SQLITE_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    except sqlite3.Error:
        return None


def is_available() -> bool:
    """Return True if kiro-cli session data exists on disk."""
    return SESSION_DIR.is_dir()


# --- Session metadata index -------------------------------------------------
# kiro-cli stores every session flat in one directory, so a naive
# "glob everything, keep what matches this cwd" costs a full parse of the
# store per workspace: with 5.6k sessions and 58 workspaces that is ~330k
# reads and 60s to enumerate them all, and ~1s every time a workspace card is
# opened for the first time. The parse is cached per file, and the
# cwd -> files grouping is cached against the directory's own mtime, which
# changes when a session is created or removed. A session's cwd is fixed at
# creation, so a rewrite of an existing metadata file cannot invalidate the
# grouping — only its contents, which load_sessions re-reads for the handful
# of files in the requested workspace.
_META_MAX_BYTES = 1_048_576

_meta_cache: dict[str, tuple[float, int, dict | None]] = {}
_cwd_index: dict[str, list[str]] = {}
_cwd_index_mtime: float | None = None


def _load_meta(path: str, st: os.stat_result) -> dict | None:
    """Parse a session metadata file, reusing the last parse while it holds."""
    hit = _meta_cache.get(path)
    if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    data: dict | None = None
    if st.st_size <= _META_MAX_BYTES:
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = None
    _meta_cache[path] = (st.st_mtime, st.st_size, data)
    return data


def _iter_meta_files():
    """Yield (path, stat) for session metadata files, newest listing wins."""
    try:
        with os.scandir(SESSION_DIR) as it:
            for entry in it:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    if entry.is_file():
                        yield entry.path, entry.stat()
                except OSError:
                    continue
    except OSError:
        return


def _cwd_to_files() -> dict[str, list[str]]:
    """Map normalized cwd -> metadata file paths, excluding sub-agent sessions."""
    global _cwd_index, _cwd_index_mtime
    try:
        dir_mtime = SESSION_DIR.stat().st_mtime
    except OSError:
        return {}
    if _cwd_index_mtime == dir_mtime and _cwd_index:
        return _cwd_index

    index: dict[str, list[str]] = {}
    live: set[str] = set()
    for path, st in _iter_meta_files():
        live.add(path)
        data = _load_meta(path, st)
        if not data or data.get("parent_session_id"):
            continue
        cwd = data.get("cwd", "")
        if not cwd:
            continue
        index.setdefault(_normalize_path(cwd), []).append(path)

    for gone in _meta_cache.keys() - live:
        _meta_cache.pop(gone, None)

    _cwd_index = index
    _cwd_index_mtime = dir_mtime
    return index


def discover_workspaces() -> list[tuple[str, int, str]]:
    """Discover workspaces from session metadata + sqlite.

    Returns list of (cwd, session_count, updated_at) tuples sorted by recency.
    """
    workspaces: dict[str, str] = {}  # norm_key -> updated_at
    counts: dict[str, int] = {}  # norm_key -> count
    display: dict[str, str] = {}  # norm_key -> original cwd (first seen)

    if SESSION_DIR.is_dir():
        for meta_path, st in _iter_meta_files():
            d = _load_meta(meta_path, st)
            if not d or d.get("parent_session_id"):
                continue
            cwd = d.get("cwd", "")
            if not cwd:
                continue
            key = _normalize_path(cwd)
            counts[key] = counts.get(key, 0) + 1
            if key not in display:
                display[key] = cwd
            updated = d.get("updated_at", "")
            if key not in workspaces or updated > workspaces[key]:
                workspaces[key] = updated

    conn = _open_sqlite_readonly()
    if conn:
        try:
            for row in conn.execute("SELECT key, updated_at FROM conversations_v2"):
                key = _normalize_path(row[0])
                if key not in workspaces:
                    counts[key] = counts.get(key, 0)
                    workspaces[key] = str(row[1]) if row[1] else ""
                if key not in display:
                    display[key] = row[0]
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    sorted_keys = sorted(workspaces.keys(), key=lambda k: workspaces[k], reverse=True)
    return [(display.get(k, k), counts.get(k, 0), workspaces[k]) for k in sorted_keys]


def load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Load sessions from disk for a given cwd. Returns (sessions, file_stats)."""
    sessions: list[Session] = []
    file_stats: dict[str, _FileInfo] = {}
    if not SESSION_DIR.is_dir():
        return sessions, file_stats

    target = _normalize_path(cwd)
    # Only the files already known to belong to this workspace are touched;
    # they are re-stat'd because an active session rewrites its metadata
    # (updated_at) without changing which workspace it belongs to.
    for meta_path in _cwd_to_files().get(target, []):
        meta_file = Path(meta_path)
        try:
            st = meta_file.stat()
        except OSError:
            continue
        data = _load_meta(meta_path, st)
        if not data or data.get("parent_session_id"):
            continue
        if _normalize_path(data.get("cwd", "")) != target:
            continue

        file_stats[str(meta_file)] = _FileInfo(mtime=st.st_mtime, size=st.st_size)
        session_id = data.get("session_id", meta_file.stem)
        jsonl_path = meta_file.with_suffix(".jsonl")
        try:
            jsonl_st = jsonl_path.stat()
            file_stats[str(jsonl_path)] = _FileInfo(mtime=jsonl_st.st_mtime, size=jsonl_st.st_size)
        except OSError:
            pass
        first_prompt, last_prompt, last_reply_tail = _extract_prompts(jsonl_path)

        sessions.append(Session(
            session_id=session_id,
            title=data.get("title", "<untitled>"),
            cwd=data.get("cwd", cwd),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            first_prompt=first_prompt,
            last_prompt=last_prompt,
            last_reply_tail=last_reply_tail,
        ))

    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions, file_stats


def _extract_prompts(jsonl_path: Path) -> tuple[str, str, str]:
    """Extract first_prompt, last_prompt, last_reply_tail from .jsonl."""
    first_prompt = ""
    last_prompt = ""
    last_reply_tail = ""

    if not jsonl_path.exists():
        return first_prompt, last_prompt, last_reply_tail

    # Prefer .history file for first_prompt (preserves /qskill prefix + newlines)
    history_path = jsonl_path.with_suffix(".history")
    if history_path.exists():
        try:
            first_line = history_path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            if first_line:
                first_prompt = first_line.replace("\\n", " ")[:200]
        except OSError:
            pass

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            # Fallback: extract from jsonl if .history didn't provide it
            if not first_prompt:
                for i, line in enumerate(fh):
                    if i >= 50:
                        break
                    text = _extract_content(line, "Prompt")
                    if text:
                        first_prompt = text[:200]
                        break
            # Tail: keep last 100 lines via deque
            tail = collections.deque(fh, maxlen=100)
    except OSError:
        return first_prompt, last_prompt, last_reply_tail

    for line in reversed(tail):
        if not last_reply_tail:
            text = _extract_content(line, "AssistantMessage")
            if text:
                last_reply_tail = text[:100]
        if not last_prompt:
            text = _extract_content(line, "Prompt")
            if text:
                last_prompt = text[:200]
        if last_prompt and last_reply_tail:
            break

    return first_prompt, last_prompt, last_reply_tail


def _extract_content(line: str, kind: str) -> str:
    """Extract text content from a .jsonl line of a given kind."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return ""
    if obj.get("kind") != kind:
        return ""
    data = obj.get("data", {})
    content = data.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("kind") == "text" or item.get("type") == "text":
                    text_val = item.get("text", "")
                    if not text_val:
                        d = item.get("data")
                        text_val = d if isinstance(d, str) else (d.get("text", "") if isinstance(d, dict) else "")
                    parts.append(text_val)
        return " ".join(parts)
    return ""


def refresh_stale_entries_for_cwd(norm_cwd: str, old_stats: dict[str, _FileInfo]) -> bool:
    """Check if files for a cwd have changed. Returns True if stale (needs reload)."""
    if not SESSION_DIR.is_dir():
        return False
    if not old_stats:
        return False

    for path_str, old_info in old_stats.items():
        try:
            st = Path(path_str).stat()
            if st.st_mtime != old_info.mtime or st.st_size != old_info.size:
                return True
        except OSError:
            return True  # file deleted

    # Check for new .json files not in old_stats
    for meta_file in SESSION_DIR.glob("*.json"):
        if meta_file.suffix == ".jsonl":
            continue
        if str(meta_file) in old_stats:
            continue
        try:
            d = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if d.get("parent_session_id"):
            continue
        if _normalize_path(d.get("cwd", "")) == norm_cwd:
            return True

    return False


# --- Per-session caches ---

_tail_cache: dict[str, tuple[float, float, list[str]]] = {}  # sid -> (time, mtime, lines)
_TAIL_CACHE_TTL = 5  # seconds


def find_session_workspace(session_id: str) -> str | None:
    """Find the workspace (cwd) for a given session by scanning metadata files."""
    if not SESSION_DIR.is_dir():
        return None
    meta_file = SESSION_DIR / f"{session_id}.json"
    if not meta_file.exists():
        return None
    try:
        d = json.loads(meta_file.read_text(encoding="utf-8"))
        cwd = d.get("cwd", "")
        return cwd or None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def get_session_tail(session_id: str, cwd: str = "", max_lines: int = 15) -> list[str]:
    """Extract last N assistant message texts from a session's .jsonl. Cached 5s."""
    jsonl_path = SESSION_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return []
    try:
        st = jsonl_path.stat()
    except OSError:
        return []
    cached = _tail_cache.get(session_id)
    if cached and (time.time() - cached[0] < _TAIL_CACHE_TTL) and cached[1] == st.st_mtime:
        return list(cached[2])
    try:
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            read_size = min(size, 131072)
            fh.seek(size - read_size)
            tail_bytes = fh.read()
        lines = tail_bytes.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    messages: list[str] = []
    for line in reversed(lines):
        if '"toolUse"' in line:
            continue
        text = _extract_content(line, "AssistantMessage")
        if text:
            truncated = _cap_text(text)
            messages.append(truncated)
            if len(messages) >= max_lines:
                break
    messages.reverse()
    _tail_cache[session_id] = (time.time(), st.st_mtime, messages)
    return list(messages)


_first_prompt_cache: dict[str, tuple[float, float, str]] = {}  # sid -> (time, mtime, prompt)
_FIRST_PROMPT_TTL = 60  # seconds


def get_first_prompt(session_id: str, cwd: str = "") -> str:
    """Extract first_prompt for tooltip display. Uses .history file (preserves newlines)."""
    # Determine which file will supply the value and its mtime
    history_path = SESSION_DIR / f"{session_id}.history"
    jsonl_path = SESSION_DIR / f"{session_id}.jsonl"

    # Resolve source file for mtime tracking (.history preferred, fallback to .jsonl)
    source_path = history_path if history_path.exists() else jsonl_path
    try:
        st = source_path.stat()
        current_mtime = st.st_mtime
    except OSError:
        return ""

    # Mtime-guarded cache check
    cached = _first_prompt_cache.get(session_id)
    if cached is not None:
        cache_time, cached_mtime, cached_result = cached
        if time.time() - cache_time < _FIRST_PROMPT_TTL and cached_mtime == current_mtime:
            return cached_result

    # .history file stores original user input with escaped newlines
    if history_path.exists():
        try:
            first_line = history_path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            if first_line:
                text = first_line.replace("\\n", "\n")
                result = _cap_text(text)
                _first_prompt_cache[session_id] = (time.time(), current_mtime, result)
                return result
        except OSError:
            pass

    # Fallback to jsonl extraction
    if not jsonl_path.exists():
        return ""  # Don't negative-cache empty strings
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 50:
                    break
                text = _extract_content(line, "Prompt")
                if text:
                    result = _cap_text(text)
                    _first_prompt_cache[session_id] = (time.time(), current_mtime, result)
                    return result
    except OSError:
        pass
    return ""  # Don't negative-cache empty strings
