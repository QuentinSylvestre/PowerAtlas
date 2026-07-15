"""Semantic session status classifier.

Derives live session state (active, idle, errored) from the tail of JSONL
session transcripts. Each provider has its own message envelope format, so
classification is dispatched to per-provider parsers.
"""

import json
import logging
import os
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from .data_claude import _get_project_folder

logger = logging.getLogger(__name__)

_CACHE_TTL = 5.0  # seconds

# Replicate path constant (avoids circular import through data_kiro → data → data_kiro)
SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"


class SemanticStatus(str, Enum):
    """Semantic state of a live session."""

    ACTIVE = "active"
    NEEDS_INPUT = "needs_input"
    IDLE = "idle"
    ERRORED = "errored"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_jsonl_path(
    session_id: str, provider: str, cwd: str
) -> Optional[Path]:
    """Locate the JSONL transcript file for a session.

    Returns None if the file does not exist on disk.
    """
    if provider == "kiro-cli":
        path = SESSION_DIR / f"{session_id}.jsonl"
    elif provider == "claude-code":
        folder = _get_project_folder(cwd)
        if folder is None:
            return None
        path = folder / f"{session_id}.jsonl"
    else:
        return None

    if path.is_file():
        return path
    return None


# ---------------------------------------------------------------------------
# Tail reader
# ---------------------------------------------------------------------------


def _read_tail_lines(path: Path, max_bytes: int = 4096, file_size: int | None = None) -> list[str]:
    """Read the last *max_bytes* of a file and return complete lines.

    If the file is smaller than *max_bytes*, all lines are returned.
    When seeking into the middle of the file, the first (potentially partial)
    line is discarded to avoid feeding truncated JSON to parsers.

    *file_size* may be passed to avoid a redundant stat when the caller
    already knows the file size.
    """
    if file_size is None:
        size = path.stat().st_size
    else:
        size = file_size
    seeked = False

    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            seeked = True
        raw = f.read()

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")

    # Discard potentially partial first line when we seeked past start
    if seeked and lines:
        lines = lines[1:]

    # Remove trailing empty string from final newline
    if lines and lines[-1] == "":
        lines = lines[:-1]

    # Strip \r from Windows-style line endings
    lines = [line.rstrip("\r") for line in lines]

    return lines


# ---------------------------------------------------------------------------
# Per-provider classifiers
# ---------------------------------------------------------------------------


def classify_kiro_v2(tail_lines: list[str]) -> Optional[SemanticStatus]:
    """Classify session status from kiro-cli v2 JSONL tail.

    Format: ``{"version":"v1","kind":"<kind>","data":{...}}``
    """
    # Walk in reverse to find the last parseable message
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        kind = obj.get("kind")
        if kind is None:
            continue

        if kind == "Prompt":
            return SemanticStatus.ACTIVE
        if kind == "ToolResults":
            return SemanticStatus.ACTIVE
        if kind == "AssistantMessage":
            data = obj.get("data", {})
            content = data.get("content", [])
            # content is a list; check if any item is a toolUse
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("kind") == "toolUse":
                        return SemanticStatus.ACTIVE
            return SemanticStatus.IDLE

    return None


def classify_claude(tail_lines: list[str]) -> Optional[SemanticStatus]:
    """Classify session status from Claude Code JSONL tail.

    Claude Code lines are JSON objects with a ``type`` field
    (user/assistant/tool_use/tool_result) or legacy ``role``-based format.
    """
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # Determine message type from either 'type' or 'role' field
        msg_type = obj.get("type") or obj.get("role")
        if msg_type is None:
            continue

        if msg_type in ("tool_result", "tool_use"):
            return SemanticStatus.ACTIVE
        if msg_type == "user" or msg_type == "human":
            return SemanticStatus.ACTIVE
        if msg_type == "assistant":
            # Check for error indicators
            content = obj.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("is_error"):
                        return SemanticStatus.ERRORED
            elif isinstance(content, str):
                # Legacy single-string content — no structured error signal
                pass
            # Check message-level error field
            if obj.get("is_error"):
                return SemanticStatus.ERRORED
            return SemanticStatus.IDLE

    return None


def classify_kiro_v3(tail_lines: list[str]) -> Optional[SemanticStatus]:
    """Classify session status from kiro-cli v3 JSONL tail.

    Placeholder — not yet implemented.
    """
    return None


# ---------------------------------------------------------------------------
# Cache and public API
# ---------------------------------------------------------------------------

_MAX_CACHE_ENTRIES = 100

# Cache: (provider, session_id) -> (monotonic_time, file_mtime, status)
_status_cache: dict[tuple[str, str], tuple[float, float, SemanticStatus]] = {}


def _evict_oldest() -> None:
    """Evict the oldest cache entry when the cache exceeds the size limit."""
    if len(_status_cache) <= _MAX_CACHE_ENTRIES:
        return
    # Find and remove the entry with the smallest (oldest) monotonic_time
    oldest_key = min(_status_cache, key=lambda k: _status_cache[k][0])
    del _status_cache[oldest_key]


def get_semantic_status(
    session_id: str, provider: str, cwd: str
) -> Optional[SemanticStatus]:
    """Return cached semantic status for a session, refreshing as needed.

    Uses a 5-second TTL with an mtime guard: if the file hasn't changed,
    the cached value is reused even after TTL expires (cheap os.stat check).
    Cache is bounded to 100 entries with oldest-eviction.

    Returns None on any failure — never raises.
    """
    try:
        path = _resolve_jsonl_path(session_id, provider, cwd)
        if path is None:
            logger.debug(
                "status_classifier: path not found for %s/%s", provider, session_id
            )
            return None

        now = time.monotonic()
        try:
            st = os.stat(path)
        except OSError:
            logger.debug(
                "status_classifier: stat failed for %s/%s", provider, session_id
            )
            return None

        mtime = st.st_mtime
        cache_key = (provider, session_id)

        cached = _status_cache.get(cache_key)
        if cached is not None:
            cached_time, cached_mtime, cached_status = cached
            # Within TTL → always reuse (avoids re-reading rapidly-changing files)
            if (now - cached_time) < _CACHE_TTL:
                return cached_status
            # Beyond TTL: mtime guard — if file hasn't changed, reuse anyway
            if cached_mtime == mtime:
                # Refresh the timestamp so this entry isn't evicted as "oldest"
                _status_cache[cache_key] = (now, cached_mtime, cached_status)
                return cached_status

        # Cache miss or stale — reclassify (pass path to avoid re-resolving)
        status = _classify_from_path(path, provider, file_size=st.st_size)
        if status is not None:
            _status_cache[cache_key] = (now, mtime, status)
            _evict_oldest()
        return status
    except Exception:
        logger.debug(
            "status_classifier: unexpected error for %s/%s",
            provider,
            session_id,
            exc_info=True,
        )
        return None


def _classify_from_path(
    path: Path, provider: str, file_size: int | None = None
) -> Optional[SemanticStatus]:
    """Read JSONL tail from a resolved path and classify via provider parser."""
    tail_lines = _read_tail_lines(path, file_size=file_size)
    if not tail_lines:
        return None

    if provider == "kiro-cli":
        return classify_kiro_v2(tail_lines)
    elif provider == "claude-code":
        return classify_claude(tail_lines)
    else:
        return classify_kiro_v3(tail_lines)


def classify_session(
    session_id: str, provider: str, cwd: str
) -> Optional[SemanticStatus]:
    """Classify a session by reading its JSONL tail and dispatching to the
    appropriate provider parser.

    Returns None on any failure.
    """
    try:
        path = _resolve_jsonl_path(session_id, provider, cwd)
        if path is None:
            return None
        return _classify_from_path(path, provider)
    except Exception:
        logger.debug(
            "status_classifier: classify failed for %s/%s",
            provider,
            session_id,
            exc_info=True,
        )
        return None
