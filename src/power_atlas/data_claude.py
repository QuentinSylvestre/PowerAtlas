"""Claude Code session adapter: discovery, parsing, and caching."""

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .data import BoundedCache, Session, _FileInfo, _normalize_path, _cap_text

# Parsed-file caches. _parse_cache keys the whole parse result by (mtime, size)
# so an unchanged file is never re-read; _head_cache keys only the immutable
# head fields, which survive appends and so help the actively-written session.
_parse_cache = BoundedCache(2048)
_head_cache = BoundedCache(2048)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"

# Metadata line types to skip when looking for user messages
_METADATA_TYPES = frozenset({
    "mode", "bridge-session", "last-prompt", "file-history-snapshot",
    "permission-mode",
})

# XML tags used by Claude Code for command/meta messages — strip these from content
_XML_TAG_RE = re.compile(r"</?(?:command-message|command-name|command-args|local-command-caveat|local-command-stdout|local-command-stderr)(?:\s[^>]*)?>")

# Pattern to detect messages that are purely command metadata (should be skipped entirely)
_COMMAND_MSG_RE = re.compile(r"^\s*<(?:command-name|command-message|local-command-caveat|local-command-stdout|local-command-stderr|task-notification)")


def _strip_command_xml(text: str) -> str:
    """Strip Claude Code internal XML command tags from message content."""
    cleaned = _XML_TAG_RE.sub("", text)
    # Collapse multiple whitespace/newlines left after tag removal
    cleaned = re.sub(r"\n\s*\n", "\n", cleaned).strip()
    return cleaned


def _is_meta_or_command_message(obj: dict) -> bool:
    """Return True if this message is a meta/command message that should be skipped."""
    if obj.get("isMeta"):
        return True
    # Check if content is a command message (starts with command XML tags)
    msg = obj.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str) and content:
        if _COMMAND_MSG_RE.match(content):
            return True
    return False


def is_available() -> bool:
    """Return True if Claude Code project data exists on disk."""
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return False
    try:
        return any(CLAUDE_PROJECTS_DIR.iterdir())
    except OSError:
        return False


def _path_to_folder_name(path: str) -> str:
    """Convert a real path to Claude Code's folder naming convention.

    Non-alphanumeric characters are replaced with '-'.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


# --- Path index with TTL cache ---

_path_index_cache: tuple[float, dict[str, str]] | None = None
_PATH_INDEX_TTL = 60  # seconds
_path_index_lock = threading.Lock()


def _build_path_index() -> dict[str, str]:
    """Scan history.jsonl to map folder names to real paths.

    Returns {folder_name: real_path}. Cached for 60s.
    """
    global _path_index_cache
    with _path_index_lock:
        if _path_index_cache is not None:
            ts, index = _path_index_cache
            if time.time() - ts < _PATH_INDEX_TTL:
                return dict(index)  # return copy

    index: dict[str, str] = {}
    if CLAUDE_HISTORY_PATH.exists():
        try:
            with open(CLAUDE_HISTORY_PATH, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    project = entry.get("project")
                    if not project:
                        continue
                    folder_name = _path_to_folder_name(project)
                    # Keep the first (or any) real path for each folder name
                    if folder_name not in index:
                        index[folder_name] = project
        except OSError:
            pass

    with _path_index_lock:
        _path_index_cache = (time.time(), index)
    return dict(index)  # return copy


def _resolve_folder_to_path(folder_name: str, path_index: dict[str, str]) -> str:
    """Resolve a folder name to a real path using the path index.

    Falls back to the raw folder name if not found.
    """
    return path_index.get(folder_name, folder_name)


def _get_project_folder(cwd: str) -> Path | None:
    """Find the Claude Code project folder for a given cwd."""
    folder_name = _path_to_folder_name(cwd)
    folder_path = CLAUDE_PROJECTS_DIR / folder_name
    if folder_path.is_dir():
        return folder_path
    return None


def _is_session_file(filename: str) -> bool:
    """Check if a filename looks like a session .jsonl (UUID pattern)."""
    # Session files are UUID-named .jsonl files
    stem = Path(filename).stem
    # Exclude known non-session files
    if stem.startswith("_"):
        return False
    # UUID pattern: 8-4-4-4-12 hex
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", stem))


def discover_workspaces() -> list[tuple[str, int, str]]:
    """Discover workspaces from Claude Code project folders.

    Returns list of (cwd, session_count, updated_at_iso) tuples sorted by recency.
    """
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return []

    path_index = _build_path_index()
    results: list[tuple[str, int, str]] = []

    try:
        for folder in CLAUDE_PROJECTS_DIR.iterdir():
            if not folder.is_dir():
                continue
            # Count session .jsonl files
            session_files = [
                f for f in folder.iterdir()
                if f.suffix == ".jsonl" and _is_session_file(f.name)
            ]
            if not session_files:
                continue

            count = len(session_files)
            # Get latest mtime (skip files that fail stat)
            mtimes = []
            for f in session_files:
                try:
                    mtimes.append(f.stat().st_mtime)
                except OSError:
                    continue
            if not mtimes:
                continue
            latest_mtime = max(mtimes)
            updated_at = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()

            real_path = _resolve_folder_to_path(folder.name, path_index)
            results.append((real_path, count, updated_at))
    except OSError:
        return []

    results.sort(key=lambda x: x[2], reverse=True)
    return results


def load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Load sessions from Claude Code project folder for a given cwd.

    Returns (sessions, file_stats).
    """
    sessions: list[Session] = []
    file_stats: dict[str, _FileInfo] = {}

    folder = _get_project_folder(cwd)
    if folder is None:
        return sessions, file_stats

    try:
        session_files = [
            f for f in folder.iterdir()
            if f.suffix == ".jsonl" and _is_session_file(f.name)
        ]
    except OSError:
        return sessions, file_stats

    for jsonl_path in session_files:
        try:
            st = jsonl_path.stat()
        except OSError:
            continue

        file_stats[str(jsonl_path)] = _FileInfo(mtime=st.st_mtime, size=st.st_size)
        session_id = jsonl_path.stem
        title, first_prompt, last_prompt, last_reply_tail, created_at = _parse_session_cached(jsonl_path, st)

        updated_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        if not created_at:
            # Use file creation time as fallback
            try:
                ctime = os.path.getctime(str(jsonl_path))
                created_at = datetime.fromtimestamp(ctime, tz=timezone.utc).isoformat()
            except OSError:
                created_at = updated_at

        sessions.append(Session(
            session_id=session_id,
            title=title,
            cwd=cwd,
            created_at=created_at,
            updated_at=updated_at,
            first_prompt=first_prompt,
            last_prompt=last_prompt,
            last_reply_tail=last_reply_tail,
        ))

    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions, file_stats


def _parse_session_cached(jsonl_path: Path, st: os.stat_result) -> tuple[str, str, str, str, str]:
    """Parse a session file, reusing the previous result when it hasn't changed.

    Keyed by (mtime, size): any content change moves one of them, so a hit means
    the parse would produce the same tuple. This is what makes a refresh tick
    cost work proportional to what changed rather than to workspace size.
    """
    cache_key = str(jsonl_path)
    cached = _parse_cache.get(cache_key)
    if cached is not None:
        c_mtime, c_size, parsed = cached
        if c_mtime == st.st_mtime and c_size == st.st_size:
            return parsed

    parsed = _parse_session_file(jsonl_path, st)
    _parse_cache.put(cache_key, (st.st_mtime, st.st_size, parsed))
    return parsed


def _parse_session_file(jsonl_path: Path, st: os.stat_result | None = None) -> tuple[str, str, str, str, str]:
    """Parse a Claude Code session .jsonl file.

    Returns (title, first_prompt, last_prompt, last_reply_tail, created_at).

    The file is read in binary so the tail seek is a real byte offset rather
    than a text-mode cookie, and so most head lines can be skipped without
    being decoded at all. Lines carrying invalid UTF-8 are re-decoded with
    errors="replace" to match what the previous text-mode read produced.
    """
    title = ""
    custom_title = ""
    first_prompt = ""
    first_timestamp = ""
    last_prompt = ""
    last_reply_tail = ""

    # first_prompt/first_timestamp are immutable under append, so a cached head
    # lets command-heavy sessions (no user message in the first 500 lines) skip
    # the scan that would otherwise parse every one of those lines every time.
    cache_key = str(jsonl_path)
    if st is None:
        try:
            st = jsonl_path.stat()
        except OSError:
            st = None
    if st is not None:
        cached_head = _head_cache.get(cache_key)
        if cached_head is not None:
            h_mtime, h_size, h_prompt, h_ts = cached_head
            # JSONL here is append-only: a file that has only grown still has
            # the same head. Equal size with an unchanged mtime is also safe;
            # anything else (shrink, in-place rewrite) invalidates.
            if st.st_size > h_size or (st.st_size == h_size and st.st_mtime == h_mtime):
                first_prompt, first_timestamp = h_prompt, h_ts

    try:
        with open(jsonl_path, "rb") as fh:
            # Read first 500 lines for title and first_prompt (command-heavy sessions
            # can have hundreds of meta/task lines before real user text)
            for i, line in enumerate(fh):
                if i >= 500:
                    break

                # Once first_prompt is known the only lines that can still
                # affect the result are title lines, and every one of those
                # contains the bytes b"title" ("custom-title" / "ai-title" /
                # "customTitle" / "aiTitle"). Skipping the rest without parsing
                # is what makes this loop cheap. A false positive (the word
                # "title" inside message content) costs one redundant parse and
                # stays correct; a false negative is impossible.
                if first_prompt and b"title" not in line:
                    continue

                try:
                    obj = json.loads(line)
                except UnicodeDecodeError:
                    # Text-mode reads used errors="replace", which turns invalid
                    # bytes into U+FFFD *inside* the JSON string and still parses.
                    # Reproduce that rather than dropping the line. Caught before
                    # ValueError because UnicodeDecodeError subclasses it.
                    try:
                        obj = json.loads(line.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, ValueError):
                        continue
                except (json.JSONDecodeError, ValueError):
                    continue

                obj_type = obj.get("type", "")

                # Extract title — custom-title (user rename) takes priority over ai-title
                if obj_type == "custom-title":
                    custom_title = obj.get("customTitle", "")
                    continue
                if obj_type == "ai-title" and not title:
                    title = obj.get("aiTitle", "")
                    continue

                # Skip metadata lines
                if obj_type in _METADATA_TYPES:
                    continue
                # Skip hook_* types
                if obj_type.startswith("hook_"):
                    continue
                # Skip meta/command messages
                if obj_type == "user" and _is_meta_or_command_message(obj):
                    continue

                # Extract first user message
                if obj_type == "user" and not first_prompt:
                    msg = obj.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content:
                        cleaned = _strip_command_xml(content)
                        if cleaned:
                            first_prompt = cleaned[:200]
                        # Use first message timestamp as created_at if available
                        ts = obj.get("timestamp")
                        if ts and isinstance(ts, (int, float)):
                            first_timestamp = datetime.fromtimestamp(
                                ts / 1000, tz=timezone.utc
                            ).isoformat()
                    elif isinstance(content, list):
                        # Content can be array of blocks
                        text = _extract_text_from_content(content)
                        if text:
                            first_prompt = text[:200]

            # Seek tail for last_prompt and last_reply_tail
            fh.seek(0, 2)
            size = fh.tell()
            read_size = min(size, 262144)  # 256KB tail
            fh.seek(max(0, size - read_size))
            tail_text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        if not custom_title and not title and not first_prompt:
            title = jsonl_path.stem
        final_title = custom_title or title or first_prompt[:80] or jsonl_path.stem
        return final_title, first_prompt, "", "", first_timestamp

    if st is not None:
        # Always restamp, including on a hit: the shrink check compares against
        # the last *validated* size, so leaving a stale size here would let a
        # later truncate-and-rewrite look like an append and reuse a dead head.
        _head_cache.put(cache_key, (st.st_mtime, st.st_size, first_prompt, first_timestamp))

    # Parse tail for last user/assistant messages and custom-title
    tail_lines = tail_text.splitlines()

    # custom-title has no natural stopping point tied to real message content —
    # a rename can land chronologically before the session's last real turn,
    # which the message-scan loop below would hit first and break on. Scanning
    # for it in its own pass (cheap: a substring pre-filter before the JSON
    # parse, same trick the head-scan's "title" filter above uses) finds the
    # true most-recent rename over the whole tail regardless of where that
    # break fires, instead of silently reverting to the head-scanned name.
    for line in reversed(tail_lines):
        if "custom-title" not in line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if obj.get("type") == "custom-title":
            ct = obj.get("customTitle", "")
            if ct:
                custom_title = ct
            break

    for line in reversed(tail_lines):
        if last_prompt and last_reply_tail:
            break
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        obj_type = obj.get("type", "")

        if obj_type == "custom-title":
            continue

        if obj_type == "assistant" and not last_reply_tail:
            msg = obj.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                last_reply_tail = content[:100]
            elif isinstance(content, list):
                text = _extract_text_from_content(content)
                if text:
                    last_reply_tail = text[:100]

        elif obj_type == "user" and not last_prompt:
            # Skip meta/command messages
            if _is_meta_or_command_message(obj):
                continue
            msg = obj.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                cleaned = _strip_command_xml(content)
                if cleaned:
                    last_prompt = cleaned[:200]
            elif isinstance(content, list):
                text = _extract_text_from_content(content)
                if text:
                    last_prompt = text[:200]

    # Final title resolution: custom-title (user rename) > ai-title > first prompt > filename
    title = custom_title or title
    if not title:
        title = first_prompt[:80] if first_prompt else jsonl_path.stem

    return title, first_prompt, last_prompt, last_reply_tail, first_timestamp


def _extract_text_from_content(content: list) -> str:
    """Extract text from Claude Code content array (list of blocks)."""
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
    return " ".join(parts)


# --- Per-session caches (mtime-guarded) ---

_tail_cache: dict[str, tuple[float, float, list]] = {}  # jsonl_path -> (time, mtime, lines)
_TAIL_CACHE_TTL = 5  # seconds
_first_prompt_cache: dict[str, tuple[float, float, str]] = {}  # jsonl_path -> (time, mtime, prompt)
_FIRST_PROMPT_TTL = 60  # seconds


def get_session_tail(session_id: str, cwd: str, max_lines: int = 15) -> list[str]:
    """Extract last N assistant message texts from a Claude Code session.

    Requires cwd to locate the project folder (Claude Code keys sessions by project path).
    """
    folder = _get_project_folder(cwd)
    if folder is None:
        return []
    jsonl_path = folder / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return []

    try:
        st = jsonl_path.stat()
    except OSError:
        return []

    # Mtime-guarded cache: (cache_time, file_mtime, result)
    cache_key = str(jsonl_path)
    cached = _tail_cache.get(cache_key)
    if cached is not None:
        cache_time, cached_mtime, cached_result = cached
        if time.time() - cache_time < _TAIL_CACHE_TTL and cached_mtime == st.st_mtime:
            return list(cached_result)

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
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        obj_type = obj.get("type")
        if obj_type == "user":
            # Skip meta/command messages
            if _is_meta_or_command_message(obj):
                continue
        if obj_type != "assistant":
            continue
        msg = obj.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            messages.append(_cap_text(content))
        elif isinstance(content, list):
            text = _extract_text_from_content(content)
            if text:
                messages.append(_cap_text(text))
        if len(messages) >= max_lines:
            break

    messages.reverse()
    if messages:  # Don't cache empty results
        _tail_cache[cache_key] = (time.time(), st.st_mtime, messages)
    return messages


def get_first_prompt(session_id: str, cwd: str) -> str:
    """Extract first user message from a Claude Code session.

    Requires cwd to locate the project folder (Claude Code keys sessions by project path).
    """
    folder = _get_project_folder(cwd)
    if folder is None:
        return ""
    jsonl_path = folder / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return ""

    try:
        st = jsonl_path.stat()
    except OSError:
        return ""

    # Mtime-guarded cache: (cache_time, file_mtime, result)
    cache_key = str(jsonl_path)
    cached = _first_prompt_cache.get(cache_key)
    if cached is not None:
        cache_time, cached_mtime, cached_result = cached
        if time.time() - cache_time < _FIRST_PROMPT_TTL and cached_mtime == st.st_mtime:
            return cached_result

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 500:
                    break
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                obj_type = obj.get("type", "")
                if obj_type in _METADATA_TYPES or obj_type.startswith("hook_"):
                    continue
                if obj_type == "user":
                    # Skip meta/command messages
                    if _is_meta_or_command_message(obj):
                        continue
                    msg = obj.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content:
                        cleaned = _strip_command_xml(content)
                        if cleaned:
                            result = _cap_text(cleaned)
                            _first_prompt_cache[cache_key] = (time.time(), st.st_mtime, result)
                            return result
                    elif isinstance(content, list):
                        text = _extract_text_from_content(content)
                        if text:
                            result = _cap_text(text)
                            _first_prompt_cache[cache_key] = (time.time(), st.st_mtime, result)
                            return result
    except OSError:
        pass
    # Don't negative-cache empty results
    return ""


def find_session_workspace(session_id: str) -> str | None:
    """Find the workspace for a given Claude Code session by scanning project folders."""
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return None
    try:
        path_index = _build_path_index()
        for folder in CLAUDE_PROJECTS_DIR.iterdir():
            if not folder.is_dir():
                continue
            session_file = folder / f"{session_id}.jsonl"
            if session_file.exists():
                real_path = _resolve_folder_to_path(folder.name, path_index)
                return real_path or None
    except OSError:
        pass
    return None


def refresh_stale_entries_for_cwd(norm_cwd: str, old_stats: dict[str, _FileInfo]) -> bool:
    """Check if Claude Code session files for a cwd have changed. Returns True if stale."""
    if not old_stats:
        return False

    for path_str, old_info in old_stats.items():
        try:
            st = Path(path_str).stat()
            if st.st_mtime != old_info.mtime or st.st_size != old_info.size:
                return True
        except OSError:
            return True  # file deleted

    # Check for new session files
    # Derive the folder from any existing stat path
    if old_stats:
        sample_path = Path(next(iter(old_stats)))
        folder = sample_path.parent
        if folder.is_dir():
            for f in folder.iterdir():
                if f.suffix == ".jsonl" and _is_session_file(f.name):
                    if str(f) not in old_stats:
                        return True

    return False
