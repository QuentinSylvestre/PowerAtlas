"""Kiro IDE session adapter: discovery, parsing, and caching.

Reads sessions from the Kiro IDE workspace-sessions directory.
Folder names are URL-safe base64 encoded workspace paths; the canonical path
comes from the `workspaceDirectory` field inside each folder's `sessions.json`.
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

from .data import Session, _FileInfo, _normalize_path, _cap_text


def _sessions_dir() -> Path:
    """Platform-appropriate path to Kiro IDE workspace-sessions directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", ""))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    return base / "Kiro" / "User" / "globalStorage" / "kiro.kiroagent" / "workspace-sessions"


SESSIONS_DIR = _sessions_dir()


def _encode_path(path: str) -> str:
    """Encode a workspace path to the URL-safe base64 folder name used by Kiro IDE.

    Standard base64 with: + -> -, / -> _, = -> ?
    """
    raw = base64.b64encode(path.encode("utf-8")).decode("ascii")
    return raw.replace("+", "-").replace("/", "_").replace("=", "?")


def _decode_folder_name(folder_name: str) -> str:
    """Decode a folder name back to a workspace path (best-effort, may have trailing bytes)."""
    raw = folder_name.replace("-", "+").replace("_", "/").replace("?", "=")
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return ""


def is_available() -> bool:
    """Return True if Kiro IDE workspace-sessions directory exists and has content."""
    try:
        if not SESSIONS_DIR.is_dir():
            return False
        return any(SESSIONS_DIR.iterdir())
    except OSError:
        return False


def discover_workspaces() -> list[tuple[str, int, str]]:
    """Discover workspaces from Kiro IDE session data.

    Returns list of (cwd, session_count, updated_at) tuples sorted by recency.
    Reads workspaceDirectory from sessions.json as the canonical path (not base64 decode).
    """
    results: list[tuple[str, int, str]] = []

    if not SESSIONS_DIR.is_dir():
        return results

    for folder in SESSIONS_DIR.iterdir():
        if not folder.is_dir():
            continue
        sessions_file = folder / "sessions.json"
        if not sessions_file.exists():
            continue
        try:
            data = json.loads(sessions_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, list) or not data:
            continue

        # Get canonical workspace path from the first entry
        workspace_dir = data[0].get("workspaceDirectory", "")
        if not workspace_dir:
            # Fallback: try decoding folder name
            workspace_dir = _decode_folder_name(folder.name)
        if not workspace_dir:
            continue

        count = len(data)

        # Find most recent dateCreated (string of unix ms)
        max_ts = ""
        for entry in data:
            ts_str = entry.get("dateCreated", "")
            if ts_str and (not max_ts or ts_str > max_ts):
                max_ts = ts_str

        # Convert unix ms string to ISO-8601
        updated_at = ""
        if max_ts:
            try:
                ts_sec = int(max_ts) / 1000.0
                updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_sec))
            except (ValueError, OSError):
                updated_at = ""

        results.append((workspace_dir, count, updated_at))

    results.sort(key=lambda x: x[2], reverse=True)
    return results


def _find_workspace_folder(cwd: str) -> Path | None:
    """Find the workspace folder for a given cwd.

    First tries encoding the path, then falls back to scanning all folders.
    """
    if not SESSIONS_DIR.is_dir():
        return None

    # Try direct encoding lookup
    encoded = _encode_path(cwd)
    direct = SESSIONS_DIR / encoded
    if direct.is_dir() and (direct / "sessions.json").exists():
        return direct

    # Fallback: scan all folders comparing workspaceDirectory
    target = _normalize_path(cwd)
    for folder in SESSIONS_DIR.iterdir():
        if not folder.is_dir():
            continue
        sessions_file = folder / "sessions.json"
        if not sessions_file.exists():
            continue
        try:
            data = json.loads(sessions_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, list) or not data:
            continue
        ws_dir = data[0].get("workspaceDirectory", "")
        if ws_dir and _normalize_path(ws_dir) == target:
            return folder

    return None


def load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Load sessions from Kiro IDE for a given workspace. Returns (sessions, file_stats)."""
    sessions: list[Session] = []
    file_stats: dict[str, _FileInfo] = {}

    folder = _find_workspace_folder(cwd)
    if folder is None:
        return sessions, file_stats

    sessions_file = folder / "sessions.json"
    try:
        st = sessions_file.stat()
        file_stats[str(sessions_file)] = _FileInfo(mtime=st.st_mtime, size=st.st_size)
        data = json.loads(sessions_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return sessions, file_stats

    if not isinstance(data, list):
        return sessions, file_stats

    for entry in data:
        session_id = entry.get("sessionId", "")
        if not session_id:
            continue

        title = entry.get("title", "<untitled>")
        date_created_str = entry.get("dateCreated", "")

        # Convert unix ms string to ISO-8601
        created_at = ""
        if date_created_str:
            try:
                ts_sec = int(date_created_str) / 1000.0
                created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_sec))
            except (ValueError, OSError):
                pass

        workspace_dir = entry.get("workspaceDirectory", cwd)

        # Read per-session file for prompts
        first_prompt = ""
        last_reply_tail = ""
        last_prompt = ""
        session_file = folder / f"{session_id}.json"
        try:
            sess_st = session_file.stat()
            file_stats[str(session_file)] = _FileInfo(mtime=sess_st.st_mtime, size=sess_st.st_size)
            sess_data = json.loads(session_file.read_text(encoding="utf-8"))
            history = sess_data.get("history", [])
            if isinstance(history, list):
                first_prompt, last_prompt, last_reply_tail = _extract_from_history(history)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

        sessions.append(Session(
            session_id=session_id,
            title=title,
            cwd=workspace_dir,
            created_at=created_at,
            updated_at=created_at,  # IDE only has dateCreated
            first_prompt=first_prompt,
            last_prompt=last_prompt,
            last_reply_tail=last_reply_tail,
        ))

    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions, file_stats


def _extract_from_history(history: list) -> tuple[str, str, str]:
    """Extract first_prompt, last_prompt, last_reply_tail from IDE history array."""
    first_prompt = ""
    last_prompt = ""
    last_reply_tail = ""

    for entry in history:
        msg = entry.get("message", {}) if isinstance(entry, dict) else {}
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            text = _extract_user_text(content)
            if text:
                if not first_prompt:
                    first_prompt = text[:200]
                last_prompt = text[:200]
        elif role == "assistant":
            text = _extract_assistant_text(content)
            if text:
                last_reply_tail = text[:100]

    return first_prompt, last_prompt, last_reply_tail


def _extract_user_text(content) -> str:
    """Extract text from user message content (array of {type: "text", text: "..."})."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    return ""


def _extract_assistant_text(content) -> str:
    """Extract text from assistant message content (plain string)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Fallback if content is array format
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    return ""


def refresh_stale_entries_for_cwd(norm_cwd: str, old_stats: dict[str, _FileInfo]) -> bool:
    """Check if files for a workspace have changed. Returns True if stale (needs reload)."""
    if not old_stats:
        return False

    for path_str, old_info in old_stats.items():
        try:
            st = Path(path_str).stat()
            if st.st_mtime != old_info.mtime or st.st_size != old_info.size:
                return True
        except OSError:
            return True  # file deleted or moved

    return False


# --- Per-session caches ---

_tail_cache: dict[str, tuple[float, list[str]]] = {}  # sid -> (time, lines)
_TAIL_CACHE_TTL = 5  # seconds


def get_session_tail(session_id: str, cwd: str, max_lines: int = 15) -> list[str]:
    """Extract last N assistant message texts from a Kiro IDE session. Cached 5s."""
    cached = _tail_cache.get(session_id)
    if cached and (time.time() - cached[0] < _TAIL_CACHE_TTL):
        return list(cached[1])

    folder = _find_workspace_folder(cwd)
    if folder is None:
        return []

    session_file = folder / f"{session_id}.json"
    try:
        sess_data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []

    history = sess_data.get("history", [])
    if not isinstance(history, list):
        return []

    messages: list[str] = []
    for entry in reversed(history):
        msg = entry.get("message", {}) if isinstance(entry, dict) else {}
        if msg.get("role") == "assistant":
            text = _extract_assistant_text(msg.get("content", ""))
            if text:
                messages.append(_cap_text(text))
                if len(messages) >= max_lines:
                    break

    messages.reverse()
    _tail_cache[session_id] = (time.time(), messages)
    return list(messages)


_first_prompt_cache: dict[str, tuple[float, str]] = {}  # sid -> (time, prompt)
_FIRST_PROMPT_TTL = 60  # seconds


def get_first_prompt(session_id: str, cwd: str) -> str:
    """Extract first user message from a Kiro IDE session. Cached 60s."""
    cached = _first_prompt_cache.get(session_id)
    if cached and (time.time() - cached[0] < _FIRST_PROMPT_TTL):
        return cached[1]

    folder = _find_workspace_folder(cwd)
    if folder is None:
        _first_prompt_cache[session_id] = (time.time(), "")
        return ""

    session_file = folder / f"{session_id}.json"
    try:
        sess_data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        _first_prompt_cache[session_id] = (time.time(), "")
        return ""

    history = sess_data.get("history", [])
    if not isinstance(history, list):
        _first_prompt_cache[session_id] = (time.time(), "")
        return ""

    for entry in history:
        msg = entry.get("message", {}) if isinstance(entry, dict) else {}
        if msg.get("role") == "user":
            text = _extract_user_text(msg.get("content", ""))
            if text:
                result = _cap_text(text)
                _first_prompt_cache[session_id] = (time.time(), result)
                return result

    _first_prompt_cache[session_id] = (time.time(), "")
    return ""


# --- Reverse index for find_session_workspace ---

_reverse_index: dict[str, str] | None = None  # session_id -> workspaceDirectory
_reverse_index_time: float = 0.0
_REVERSE_INDEX_TTL = 30  # seconds


def _build_reverse_index() -> dict[str, str]:
    """Build a session_id -> workspaceDirectory reverse index. Cached with TTL."""
    global _reverse_index, _reverse_index_time

    if _reverse_index is not None and (time.time() - _reverse_index_time < _REVERSE_INDEX_TTL):
        return _reverse_index

    index: dict[str, str] = {}
    if not SESSIONS_DIR.is_dir():
        _reverse_index = index
        _reverse_index_time = time.time()
        return index

    for folder in SESSIONS_DIR.iterdir():
        if not folder.is_dir():
            continue
        sessions_file = folder / "sessions.json"
        if not sessions_file.exists():
            continue
        try:
            data = json.loads(sessions_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, list) or not data:
            continue

        workspace_dir = data[0].get("workspaceDirectory", "")
        if not workspace_dir:
            continue

        for entry in data:
            sid = entry.get("sessionId", "")
            if sid:
                index[sid] = workspace_dir

    _reverse_index = index
    _reverse_index_time = time.time()
    return index


def find_session_workspace(session_id: str) -> str | None:
    """Find which workspace owns a given session_id. Returns workspaceDirectory or None."""
    index = _build_reverse_index()
    return index.get(session_id)
