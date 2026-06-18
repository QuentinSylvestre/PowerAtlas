"""Read-only access to kiro-cli session data with defensive parsing."""

import collections
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"
SQLITE_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Kiro-Cli" / "data.sqlite3"

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


def discover_workspaces() -> list[str]:
    """Discover workspaces from session metadata + sqlite. Returns unique cwds sorted by recency."""
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


def discover_workspaces_with_counts() -> list[tuple[str, int]]:
    """Like discover_workspaces but returns (cwd, session_count) tuples. Cached for 30s."""
    cache_key = "workspaces_with_counts"
    if cache_key in _cache:
        ts, result = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return result
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
    result = [(display.get(k, k), counts.get(k, 0)) for k in sorted_keys]
    _cache[cache_key] = (time.time(), result)
    return result


def get_sessions(cwd: str) -> list[Session]:
    """Return sessions for a workspace, sorted by updated_at desc. Filters subagents."""
    sessions: list[Session] = []
    if not SESSION_DIR.is_dir():
        return sessions

    target = _normalize_path(cwd)
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
        if _normalize_path(data.get("cwd", "")) != target:
            continue

        session_id = data.get("session_id", meta_file.stem)
        jsonl_path = meta_file.with_suffix(".jsonl")
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
    return sessions


def _extract_prompts(jsonl_path: Path) -> tuple[str, str, str]:
    """Extract first_prompt, last_prompt, last_reply_tail from .jsonl."""
    first_prompt = ""
    last_prompt = ""
    last_reply_tail = ""

    if not jsonl_path.exists():
        return first_prompt, last_prompt, last_reply_tail

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
            # First 50 lines for first_prompt
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


def _normalize_path(p: str) -> str:
    """Normalize path: case-fold on Windows, strip trailing sep."""
    normalized = p.rstrip("/\\")
    if sys.platform == "win32":
        normalized = normalized.casefold()
    return normalized


def _open_sqlite_readonly() -> sqlite3.Connection | None:
    """Open sqlite read-only with busy_timeout=5000. Returns None if unavailable."""
    try:
        uri = f"file:{SQLITE_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    except sqlite3.Error:
        return None
