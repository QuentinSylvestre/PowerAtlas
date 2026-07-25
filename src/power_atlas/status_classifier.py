"""Semantic session status classifier.

Derives live session state (working, waiting, errored) from the tail of JSONL
session transcripts. Each provider has its own message envelope format, so
classification is dispatched to per-provider parsers.
"""

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Optional

from .data_claude import _get_project_folder

logger = logging.getLogger(__name__)

_CACHE_TTL = 5.0  # seconds

# Replicate path constant (avoids circular import through data_kiro → data → data_kiro)
SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"

# v3 sessions root (workspace-hash subdirs live here)
_V3_SESSIONS_ROOT = Path.home() / ".kiro" / "sessions"


class SemanticStatus(str, Enum):
    """Semantic state of a live session."""

    WORKING = "working"
    WAITING = "waiting"
    ERRORED = "errored"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


# Only the kiro-cli branch is memoized: its v3 fallback walks every workspace
# directory, and this runs per-session inside the 5s status poll, so an uncached
# miss costs O(sessions x workspace_dirs) stat calls per tick. The claude-code
# branch is two syscalls and not worth caching.
#
# The key carries the directory roots the lookup reads, so rebinding them (tests
# do; production does not) can never serve a path resolved against a different
# root. Positive entries are revalidated with is_file() so a deleted file
# re-resolves; negative entries expire quickly so a session whose file appears
# moments later is still picked up.
_PATH_CACHE_NEG_TTL = 5.0
_MAX_PATH_CACHE_ENTRIES = 512
_path_cache: "OrderedDict[tuple, tuple[float, Path | None]]" = OrderedDict()
_path_cache_lock = threading.Lock()


def _resolve_jsonl_path(
    session_id: str, provider: str, cwd: str
) -> Optional[Path]:
    """Locate the JSONL transcript file for a session.

    Returns None if the file does not exist on disk.
    """
    if provider != "kiro-cli":
        return _resolve_jsonl_path_uncached(session_id, provider, cwd)

    cache_key = (session_id, str(SESSION_DIR), str(_V3_SESSIONS_ROOT))
    with _path_cache_lock:
        cached = _path_cache.get(cache_key)
        if cached is not None:
            cached_at, cached_path = cached
            if cached_path is not None:
                # Cheap revalidation — one stat instead of a directory walk.
                try:
                    if cached_path.is_file():
                        _path_cache.move_to_end(cache_key)
                        return cached_path
                except OSError:
                    pass
            elif (time.monotonic() - cached_at) < _PATH_CACHE_NEG_TTL:
                _path_cache.move_to_end(cache_key)
                return None

    resolved = _resolve_jsonl_path_uncached(session_id, provider, cwd)

    with _path_cache_lock:
        _path_cache[cache_key] = (time.monotonic(), resolved)
        _path_cache.move_to_end(cache_key)
        while len(_path_cache) > _MAX_PATH_CACHE_ENTRIES:
            _path_cache.popitem(last=False)
    return resolved


def _resolve_jsonl_path_uncached(
    session_id: str, provider: str, cwd: str
) -> Path | None:
    """Locate the JSONL transcript file for a session, hitting the filesystem."""
    if provider == "kiro-cli":
        # v2 path
        path = SESSION_DIR / f"{session_id}.jsonl"
        if path.is_file():
            return path
        # v3 path: ~/.kiro/sessions/<workspace-hash>/sess_<session_id>/messages.jsonl
        if _V3_SESSIONS_ROOT.is_dir():
            # session_id may or may not have sess_ prefix
            sid = session_id if session_id.startswith("sess_") else f"sess_{session_id}"
            for ws_dir in _V3_SESSIONS_ROOT.iterdir():
                if not ws_dir.is_dir() or ws_dir.name == "cli":
                    continue
                v3_path = ws_dir / sid / "messages.jsonl"
                if v3_path.is_file():
                    return v3_path
        return None
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

# Ceiling for the widening retry in _read_tail_lines. Large enough to clear
# any observed single transcript line by an order of magnitude, small enough
# that one degenerate file cannot stall a dashboard refresh.
_MAX_TAIL_BYTES = 2 * 1024 * 1024


def _read_tail_lines(path: Path, max_bytes: int = 65536, file_size: int | None = None) -> list[str]:
    """Read the last *max_bytes* of a file and return complete lines.

    If the file is smaller than *max_bytes*, all lines are returned.
    When seeking into the middle of the file, the first (potentially partial)
    line is discarded to avoid feeding truncated JSON to parsers.

    *file_size* may be passed to avoid a redundant stat when the caller
    already knows the file size.

    A window smaller than the file's final line yields nothing: the seek lands
    mid-line and the partial-line discard below consumes the entire read. That
    silently classifies the session as unknown, which ``web.py`` then renders
    as "working" — the opposite of the truth for a session awaiting input.
    Claude Code lines reach 150 KB, so no fixed window is safe; when a window
    yields no complete line the read is retried wider rather than giving up.
    Cost is unaffected for normal transcripts because the read stays
    tail-bounded — a 57 MB file costs the same as a 64 KB one.
    """
    if file_size is None:
        size = path.stat().st_size
    else:
        size = file_size

    window = max(max_bytes, 1)
    while True:
        seeked = size > window
        with open(path, "rb") as f:
            if seeked:
                f.seek(size - window)
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

        if lines or not seeked or window >= _MAX_TAIL_BYTES:
            return lines
        # Nothing survived the discard: the final line is wider than the
        # window. Widen and retry, bounded so a pathological file cannot
        # pull an unlimited read onto the refresh path.
        window = min(window * 8, _MAX_TAIL_BYTES, size)


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
            return SemanticStatus.WORKING
        if kind == "ToolResults":
            return SemanticStatus.WORKING
        if kind == "AssistantMessage":
            data = obj.get("data", {})
            content = data.get("content", [])
            # content is a list; check if any item is a toolUse
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("kind") == "toolUse":
                        return SemanticStatus.WORKING
            return SemanticStatus.WAITING

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
            return SemanticStatus.WORKING
        if msg_type == "user" or msg_type == "human":
            return SemanticStatus.WORKING
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
            return SemanticStatus.WAITING

    return None


# Types to skip when looking for meaningful v3 messages
_V3_SKIP_TYPES = frozenset({"tool_result", "usage_summary", "session_metadata", "steering_inclusion"})


def classify_kiro_v3(tail_lines: list[str]) -> Optional[SemanticStatus]:
    """Classify session status from kiro-cli v3 JSONL tail.

    Format: ``{"id": "...", "timestamp": "...", "payload": {"type": "...", ...}}``

    Classification logic:
    - tool_call or user → WORKING (agent is actively executing)
    - assistant → WAITING (agent finished, awaiting user)
    - tool_result with success==false → ERRORED (check recent lines for error pattern)
    - Skip: tool_result, usage_summary, session_metadata, steering_inclusion
    """
    # First pass: check recent lines for error patterns (failed tool_results)
    error_count = 0
    lines_checked = 0
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if ptype == "tool_result" and payload.get("success") is False:
            error_count += 1
        lines_checked += 1
        if lines_checked >= 5:
            break

    # Second pass: find the last meaningful message type
    last_meaningful = None
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if ptype is None or ptype in _V3_SKIP_TYPES:
            continue

        if ptype in ("tool_call", "user"):
            last_meaningful = SemanticStatus.WORKING
            break
        if ptype == "assistant":
            last_meaningful = SemanticStatus.WAITING
            break
        # sub_agent_start means the agent is actively working
        if ptype == "sub_agent_start":
            last_meaningful = SemanticStatus.WORKING
            break
        # sub_agent_complete means work finished, awaiting user
        if ptype == "sub_agent_complete":
            last_meaningful = SemanticStatus.WAITING
            break
        # turn_start/turn_end are structural — skip
        if ptype in ("turn_start", "turn_end"):
            continue
        # pending_interaction means waiting for user
        if ptype == "pending_interaction":
            last_meaningful = SemanticStatus.WAITING
            break
        # interaction_resolved means user provided input — working
        if ptype == "interaction_resolved":
            last_meaningful = SemanticStatus.WORKING
            break
        # session_event/session_start — skip
        if ptype in ("session_event", "session_start"):
            continue

    # Only report ERRORED if recent errors AND the last meaningful message
    # is not a recovery (WORKING) — avoids false errored state after retries
    if error_count >= 2 and last_meaningful != SemanticStatus.WORKING:
        return SemanticStatus.ERRORED

    return last_meaningful


def _is_v3_format(tail_lines: list[str]) -> bool:
    """Detect whether JSONL lines are v3 format (payload.type field)."""
    for line in tail_lines[-5:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        # v3 has "payload" with "type" field; v2 has "kind" at top level
        if "payload" in obj and isinstance(obj.get("payload"), dict) and "type" in obj["payload"]:
            return True
        if "kind" in obj:
            return False
    return False


# ---------------------------------------------------------------------------
# Cache and public API
# ---------------------------------------------------------------------------

# 100 was below a realistic active-session count, so the cache thrashed on the
# 5s poll. Entries are small tuples, so a larger bound is cheap.
_MAX_CACHE_ENTRIES = 512

# LRU cache: (provider, session_id) -> (monotonic_time, file_mtime, status).
# OrderedDict gives O(1) eviction; the previous dict + min()-scan was O(n) on
# every insert, on a path that runs once per session per poll.
_status_cache: "OrderedDict[tuple[str, str], tuple[float, float, SemanticStatus]]" = OrderedDict()


def _evict_oldest() -> None:
    """Evict least-recently-used entries when the cache exceeds the size limit."""
    while len(_status_cache) > _MAX_CACHE_ENTRIES:
        _status_cache.popitem(last=False)


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
                _status_cache.move_to_end(cache_key)
                return cached_status
            # Beyond TTL: mtime guard — if file hasn't changed, reuse anyway
            if cached_mtime == mtime:
                # Refresh the timestamp so this entry isn't evicted as "oldest"
                _status_cache[cache_key] = (now, cached_mtime, cached_status)
                _status_cache.move_to_end(cache_key)
                return cached_status

        # Cache miss or stale — reclassify (pass path to avoid re-resolving)
        status = _classify_from_path(path, provider, file_size=st.st_size)
        if status is not None:
            _status_cache[cache_key] = (now, mtime, status)
            _status_cache.move_to_end(cache_key)
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
        # Detect v3 vs v2 format
        if _is_v3_format(tail_lines):
            return classify_kiro_v3(tail_lines)
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
