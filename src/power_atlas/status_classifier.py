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


# Conversation lines. Everything else Claude Code writes (mode, attachment,
# ai-title, bridge-session, file-history-snapshot, ...) is bookkeeping and
# carries no turn state, so it must not consume the error-scan window below.
_CLAUDE_TURN_TYPES = frozenset({"user", "human", "assistant"})


def _claude_content_blocks(obj: dict) -> list:
    """Content blocks of a Claude Code line, from whichever envelope holds them.

    Live transcripts nest the payload under ``message``; a top-level ``content``
    is the legacy shape.
    """
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if content is None:
        content = obj.get("content")
    return content if isinstance(content, list) else []


def _claude_has_error(obj: dict) -> bool:
    """True when a line flags itself, or one of its blocks, as an error."""
    if obj.get("is_error"):
        return True
    return any(
        isinstance(b, dict) and b.get("is_error")
        for b in _claude_content_blocks(obj)
    )


def classify_claude(tail_lines: list[str]) -> Optional[SemanticStatus]:
    """Classify session status from Claude Code JSONL tail.

    Claude Code splits one assistant turn across several lines — one per content
    block — so block types alone cannot say whether the turn is over: a lone
    ``text`` block is what both "here is my answer" and "here is what I am about
    to do next" look like. Every assistant line carries the API response's
    ``stop_reason``, which does separate them, and that is what decides here:
    ``tool_use`` means a tool call follows (the agent is working), anything else
    means the turn ended (the agent is waiting). Classifying by block instead
    holds a busy session at WAITING for the whole duration of every command it
    runs, and ``_workspace_status`` in ``web.py`` lets that outrank the
    provider's own "busy" report.

    Errors arrive as ``user`` lines whose ``tool_result`` block sets
    ``is_error``. A single one is routine — a grep that matches nothing exits
    non-zero — so ERRORED requires two within the recent conversation tail and a
    session that has since stopped, mirroring ``classify_kiro_v3``.
    """
    error_count = 0
    checked = 0
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if (obj.get("type") or obj.get("role")) not in _CLAUDE_TURN_TYPES:
            continue
        if any(
            isinstance(b, dict)
            and b.get("type") == "tool_result"
            and b.get("is_error")
            for b in _claude_content_blocks(obj)
        ):
            error_count += 1
        checked += 1
        if checked >= 5:
            break

    last_meaningful = None
    for line in reversed(tail_lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        msg_type = obj.get("type") or obj.get("role")
        if msg_type in ("user", "human"):
            # A prompt or a tool result coming back; both hand control to the agent.
            last_meaningful = SemanticStatus.WORKING
            break
        if msg_type == "assistant":
            message = obj.get("message")
            stop_reason = (
                message.get("stop_reason") if isinstance(message, dict) else None
            )
            if stop_reason == "tool_use":
                last_meaningful = SemanticStatus.WORKING
            elif _claude_has_error(obj):
                last_meaningful = SemanticStatus.ERRORED
            elif stop_reason is None and any(
                isinstance(b, dict) and b.get("type") == "tool_use"
                for b in _claude_content_blocks(obj)
            ):
                # Legacy envelope carries no stop_reason — fall back to blocks.
                last_meaningful = SemanticStatus.WORKING
            else:
                last_meaningful = SemanticStatus.WAITING
            break

    if error_count >= 2 and last_meaningful is not SemanticStatus.WORKING:
        return SemanticStatus.ERRORED
    return last_meaningful


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
#
# Locked like _path_cache above, and for the same reason: the status poll runs
# this in a worker thread while a render can reach it from the event loop, and
# a get/move_to_end/popitem interleaving across the two raises KeyError. The
# caller's blanket except swallows that into a "working" dot for one tick, so
# the failure is quiet rather than absent. The lock is never held across the
# file read.
_status_cache: "OrderedDict[tuple[str, str], tuple[float, float, SemanticStatus]]" = OrderedDict()
_status_cache_lock = threading.Lock()


def _evict_oldest() -> None:
    """Evict least-recently-used entries when the cache exceeds the size limit."""
    with _status_cache_lock:
        while len(_status_cache) > _MAX_CACHE_ENTRIES:
            _status_cache.popitem(last=False)


def get_semantic_status(
    session_id: str, provider: str, cwd: str
) -> Optional[SemanticStatus]:
    """Return cached semantic status for a session, refreshing as needed.

    Uses a 5-second TTL with an mtime guard: if the file hasn't changed,
    the cached value is reused even after TTL expires (cheap os.stat check).
    Cache is bounded to ``_MAX_CACHE_ENTRIES`` with oldest-eviction.

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

        with _status_cache_lock:
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
            with _status_cache_lock:
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
