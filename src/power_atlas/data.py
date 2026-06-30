"""Read-only access to kiro-cli session data with defensive parsing."""

import collections
import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"

def _sqlite_path() -> Path:
    """Platform-appropriate path to kiro-cli conversation database."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", ""))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share"))
    return base / "Kiro-Cli" / "data.sqlite3"

SQLITE_PATH = _sqlite_path()


def _cap_text(text: str, max_chars: int = 2000, max_lines: int = 15) -> str:
    """Cap text at max_chars OR max_lines, whichever is shorter."""
    lines = text.split("\n")[:max_lines]
    result = "\n".join(lines)
    return result[:max_chars]

# Simple TTL cache to avoid re-reading hundreds of OneDrive-synced files on every request
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 30  # seconds


@dataclass
class Session:
    session_id: str
    title: str
    cwd: str
    created_at: str
    updated_at: str
    first_prompt: str
    last_prompt: str
    last_reply_tail: str


@dataclass
class _FileInfo:
    mtime: float
    size: int


class SessionCache:
    """Thread-safe in-memory session cache with per-file stat tracking."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, list[Session]] = {}
        self._file_stats: dict[str, dict[str, _FileInfo]] = {}
        self._loaded_cwds: set[str] = set()
        self.last_refresh: str = ""

    def get(self, cwd: str) -> list[Session] | None:
        key = _normalize_path(cwd)
        with self._lock:
            sessions = self._sessions.get(key)
            return list(sessions) if sessions is not None else None

    def put(self, cwd: str, sessions: list[Session], file_stats: dict[str, _FileInfo]) -> None:
        key = _normalize_path(cwd)
        with self._lock:
            self._sessions[key] = sessions
            self._file_stats[key] = file_stats
            self._loaded_cwds.add(key)
            self.last_refresh = time.strftime("%H:%M:%S")

    def get_loaded_cwds(self) -> set[str]:
        with self._lock:
            return self._loaded_cwds.copy()

    def get_file_stats(self, cwd: str) -> dict[str, _FileInfo]:
        key = _normalize_path(cwd)
        with self._lock:
            return self._file_stats.get(key, {}).copy()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._file_stats.clear()
            self._loaded_cwds.clear()


session_cache = SessionCache()


def discover_workspaces() -> list[str]:
    """Discover workspaces from session metadata + sqlite. Returns unique cwds sorted by recency.

    Unused in production — prefer discover_workspaces_with_counts(). Retained for external/test use.
    """
    # cwd -> most recent updated_at
    workspaces: dict[str, str] = {}
    counts: dict[str, int] = {}

    # Primary: session .json metadata files
    if SESSION_DIR.is_dir():
        for meta_file in SESSION_DIR.glob("*.json"):
            if meta_file.suffix == ".jsonl":
                continue
            try:
                if meta_file.stat().st_size > 1_048_576:
                    continue
                data = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if data.get("parent_session_id"):
                continue
            cwd = data.get("cwd", "")
            if not cwd:
                continue
            updated = data.get("updated_at", "")
            key = _normalize_path(cwd)
            counts[key] = counts.get(key, 0) + 1
            if key not in workspaces or updated > workspaces[key]:
                workspaces[key] = updated

    # Supplementary: sqlite
    conn = _open_sqlite_readonly()
    if conn:
        try:
            for row in conn.execute("SELECT key, updated_at FROM conversations_v2"):
                key = _normalize_path(row[0])
                updated = str(row[1]) if row[1] else ""
                if key not in workspaces or updated > workspaces.get(key, ""):
                    workspaces[key] = updated
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    return sorted(workspaces.keys(), key=lambda k: workspaces[k], reverse=True)


def discover_workspaces_with_counts() -> list[tuple[str, int, str]]:
    """Like discover_workspaces but returns (cwd, session_count, updated_at) tuples. Cached for 30s."""
    cache_key = "workspaces_with_counts"
    if cache_key in _cache:
        ts, result = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return list(result)
    workspaces: dict[str, str] = {}  # norm_key -> updated_at
    counts: dict[str, int] = {}  # norm_key -> count
    display: dict[str, str] = {}  # norm_key -> original cwd (first seen)
    if SESSION_DIR.is_dir():
        for meta_file in SESSION_DIR.glob("*.json"):
            if meta_file.suffix == ".jsonl":
                continue
            try:
                if meta_file.stat().st_size > 1_048_576:
                    continue
                d = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if d.get("parent_session_id"):
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
    result = [(display.get(k, k), counts.get(k, 0), workspaces[k]) for k in sorted_keys]
    _cache[cache_key] = (time.time(), result)
    return list(result)


def get_sessions(cwd: str) -> list[Session]:
    """Return sessions for a workspace, sorted by updated_at desc. Filters subagents."""
    cached = session_cache.get(cwd)
    if cached is not None:
        return cached
    sessions, file_stats = _load_sessions(cwd)
    session_cache.put(cwd, sessions, file_stats)
    return sessions


def _load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Load sessions from disk. Returns (sessions, file_stats). No lock held."""
    sessions: list[Session] = []
    file_stats: dict[str, _FileInfo] = {}
    if not SESSION_DIR.is_dir():
        return sessions, file_stats

    target = _normalize_path(cwd)
    for meta_file in SESSION_DIR.glob("*.json"):
        if meta_file.suffix == ".jsonl":
            continue
        try:
            st = meta_file.stat()
            if st.st_size > 1_048_576:
                continue
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if data.get("parent_session_id"):
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
                last_reply_tail = text[-100:]
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


def refresh_stale_entries() -> None:
    """Check loaded workspaces for file changes; re-read only changed sessions."""
    if not SESSION_DIR.is_dir():
        return
    for norm_cwd in session_cache.get_loaded_cwds():
        try:
            old_stats = session_cache.get_file_stats(norm_cwd)
            if not old_stats:
                continue
            changed = False
            for path_str, old_info in old_stats.items():
                try:
                    st = Path(path_str).stat()
                    if st.st_mtime != old_info.mtime or st.st_size != old_info.size:
                        changed = True
                        break
                except OSError:
                    changed = True  # file deleted
                    break
            # Also check for new .json files not in old_stats
            if not changed:
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
                        changed = True
                        break
            if changed:
                sessions, file_stats = _load_sessions(norm_cwd)
                session_cache.put(norm_cwd, sessions, file_stats)
        except OSError:
            continue


def warmup_pinned(pinned_folders: list[str]) -> None:
    """Pre-load sessions for pinned workspaces. Safe to call from any thread."""
    for folder in pinned_folders:
        try:
            if Path(folder).exists():
                get_sessions(folder)
        except OSError:
            continue


_tail_cache: dict[str, tuple[float, float, list[str]]] = {}  # sid -> (time, mtime, lines)
_TAIL_CACHE_TTL = 5  # seconds


def get_session_tail(session_id: str, max_lines: int = 15) -> list[str]:
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


def get_first_prompt(session_id: str) -> str:
    """Extract first_prompt for tooltip display. Uses .history file (preserves newlines)."""
    # .history file stores original user input with escaped newlines
    history_path = SESSION_DIR / f"{session_id}.history"
    if history_path.exists():
        try:
            first_line = history_path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            if first_line:
                # Unescape literal \n sequences to real newlines
                text = first_line.replace("\\n", "\n")
                return _cap_text(text)
        except OSError:
            pass
    # Fallback to jsonl extraction
    jsonl_path = SESSION_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return ""
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 50:
                    break
                text = _extract_content(line, "Prompt")
                if text:
                    return _cap_text(text)
    except OSError:
        pass
    return ""


def _normalize_path(p: str) -> str:
    """Normalize path for cache keying: backslash + casefold on Windows, strip trailing sep."""
    if sys.platform == "win32":
        normalized = p.replace("/", "\\").rstrip("\\")
        return normalized.casefold()
    return p.rstrip("/") or "/"


def _open_sqlite_readonly() -> sqlite3.Connection | None:
    """Open sqlite read-only with busy_timeout=5000. Returns None if unavailable."""
    try:
        uri = f"file:{SQLITE_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    except sqlite3.Error:
        return None
