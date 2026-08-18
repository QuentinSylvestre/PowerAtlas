"""Provider-aware session data orchestrator.

Shared types (Session, _FileInfo) and the compound-keyed SessionCache live here.
Provider adapters (data_kiro, data_claude) handle discovery and parsing.
"""

import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path


# Simple TTL cache to avoid re-reading hundreds of files on every request
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 30  # seconds

# Serialize concurrent discover_workspaces_with_counts calls to prevent pile-up
_discover_lock = threading.Lock()

# Signals that warmup_all() has finished populating caches
warmup_done = threading.Event()


@dataclass(frozen=True)
class Session:
    session_id: str
    title: str
    cwd: str
    created_at: str
    updated_at: str
    first_prompt: str
    last_prompt: str
    last_reply_tail: str
    extra_fields: dict = field(default_factory=dict, hash=False, compare=False)  # extra_fields must remain the last field -- positional Session(...) calls in tests/test_data.py use 8-argument form


@dataclass
class _FileInfo:
    mtime: float
    size: int


def _normalize_path(p: str) -> str:
    """Normalize path for cache keying: backslash + casefold on Windows, strip trailing sep."""
    if sys.platform == "win32":
        normalized = p.replace("/", "\\").rstrip("\\")
        return normalized.casefold()
    return p.rstrip("/") or "/"


def _cap_text(text: str, max_chars: int = 2000, max_lines: int = 15) -> str:
    """Cap text at max_chars OR max_lines, whichever is shorter."""
    lines = text.split("\n")[:max_lines]
    result = "\n".join(lines)
    return result[:max_chars]


class BoundedCache:
    """Thread-safe LRU cache for parsed file data.

    Provider adapters parse the same session files repeatedly (every refresh
    tick re-reads a whole workspace). Keying parse results by (mtime, size)
    lets unchanged files skip parsing entirely; the LRU bound keeps a large
    corpus from growing the cache without limit.
    """

    def __init__(self, maxsize: int):
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple] = OrderedDict()

    def get(self, key: str) -> tuple | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: str, value: tuple) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# Import provider modules AFTER defining shared types to avoid circular import
from . import data_kiro, data_claude, data_kiro_ide  # noqa: E402


# Provider registry: name -> module
PROVIDERS: dict[str, object] = {
    "kiro-cli": data_kiro,
    "claude-code": data_claude,
    "kiro-ide": data_kiro_ide,
}


def available_providers() -> list[str]:
    """Return names of providers whose data is available on disk."""
    return [name for name, mod in PROVIDERS.items() if mod.is_available()]


class SessionCache:
    """Thread-safe in-memory session cache with compound (provider, cwd) keys."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], list[Session]] = {}
        self._file_stats: dict[tuple[str, str], dict[str, _FileInfo]] = {}
        self._loaded_keys: set[tuple[str, str]] = set()
        self._original_cwds: dict[tuple[str, str], str] = {}
        self.last_refresh: str = ""

    def get(self, cwd: str, provider: str = "kiro-cli") -> list[Session] | None:
        key = (provider, _normalize_path(cwd))
        with self._lock:
            sessions = self._sessions.get(key)
            return list(sessions) if sessions is not None else None

    def put(self, cwd: str, sessions: list[Session], file_stats: dict[str, _FileInfo], provider: str = "kiro-cli") -> None:
        key = (provider, _normalize_path(cwd))
        with self._lock:
            self._sessions[key] = sessions
            self._file_stats[key] = file_stats
            self._loaded_keys.add(key)
            self._original_cwds[key] = cwd
            self.last_refresh = time.strftime("%H:%M:%S")

    def get_original_cwd(self, norm_cwd: str, provider: str) -> str:
        """Return the original (non-normalized) cwd for a given provider + normalized key."""
        key = (provider, norm_cwd)
        with self._lock:
            return self._original_cwds.get(key, norm_cwd)

    def get_loaded_cwds(self, provider: str | None = None) -> set[str]:
        """Return normalized cwds that have been loaded.

        If provider is None, returns cwds across all providers.
        """
        with self._lock:
            if provider is None:
                return {cwd for _, cwd in self._loaded_keys}
            return {cwd for p, cwd in self._loaded_keys if p == provider}

    def get_file_stats(self, cwd: str, provider: str = "kiro-cli") -> dict[str, _FileInfo]:
        key = (provider, _normalize_path(cwd))
        with self._lock:
            return self._file_stats.get(key, {}).copy()

    def forget(self, cwd: str, provider: str = "kiro-cli") -> None:
        """Drop one workspace's entry so the next get_sessions re-reads it.

        For a caller that has just changed the store on purpose. The polling
        path does not need this — refresh_stale_entries() re-reads whatever the
        recorded (mtime, size) no longer matches — but it only runs on its own
        schedule, so a UI action that deletes a session and then re-lists would
        be served the pre-deletion list from here and the row would come back.

        Narrow on purpose: clear() drops every workspace of every provider, and
        the store this exists for is 5,958 sessions across 65 workspaces, so
        using it here would turn one deletion into a full re-parse.
        """
        key = (provider, _normalize_path(cwd))
        with self._lock:
            self._sessions.pop(key, None)
            self._file_stats.pop(key, None)
            self._loaded_keys.discard(key)
            self._original_cwds.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._file_stats.clear()
            self._loaded_keys.clear()
            self._original_cwds.clear()


session_cache = SessionCache()


def invalidate_workspace_counts() -> None:
    """Expire the 30 s discover_workspaces_with_counts cache immediately.

    Its key is per provider plus an "all" bucket, and a deletion changes the
    count of exactly one workspace — but the cached value is the whole sorted
    list, so there is nothing finer to invalidate than the entry. Dropping every
    variant is correct rather than lazy: `provider=None` and `provider="kiro-cli"`
    are separate entries holding the same now-stale count.

    Without this, a deleted session keeps its workspace's "3 of 47" header
    honest for up to _CACHE_TTL seconds after the row it counted has gone.
    """
    for key in [k for k in _cache if k.startswith("workspaces_with_counts:")]:
        _cache.pop(key, None)


# --- Legacy API (kiro-cli only, retained for external/test use) ---

# Re-export constants for backward compatibility
SESSION_DIR = data_kiro.SESSION_DIR


def discover_workspaces() -> list[str]:
    """Discover workspaces from kiro-cli session metadata + sqlite. Returns unique cwds sorted by recency.

    Unused in production — prefer discover_workspaces_with_counts(). Retained for external/test use.
    """
    results = data_kiro.discover_workspaces()
    return [cwd for cwd, _, _ in results]


# --- Provider-aware API ---


def discover_workspaces_with_counts(provider: str | None = None) -> list[tuple[str, int, str, str]]:
    """Discover workspaces across providers. Cached for 30s.

    Args:
        provider: If specified, only discover for that provider. None = all providers.

    Returns:
        List of (cwd, session_count, updated_at, provider_name) tuples sorted by updated_at desc.
    """
    # Fail closed: reject unknown providers to prevent unbounded cache keys
    if provider is not None and provider not in PROVIDERS:
        return []

    cache_key = f"workspaces_with_counts:{provider or 'all'}"

    # Fast path (lock-free, TOCTOU-safe via .get)
    entry = _cache.get(cache_key)
    if entry is not None:
        ts, result = entry
        if time.time() - ts < _CACHE_TTL:
            return list(result)

    # Slow path: serialize discovery to prevent pile-up
    with _discover_lock:
        # Double-check after acquiring lock
        entry = _cache.get(cache_key)
        if entry is not None:
            ts, result = entry
            if time.time() - ts < _CACHE_TTL:
                return list(result)

        results: list[tuple[str, int, str, str]] = []

        providers_to_query = (
            {provider: PROVIDERS[provider]} if provider and provider in PROVIDERS
            else PROVIDERS
        )

        for prov_name, mod in providers_to_query.items():
            if not mod.is_available():
                continue
            try:
                workspace_data = mod.discover_workspaces()
                for cwd, count, updated_at in workspace_data:
                    results.append((cwd, count, updated_at, prov_name))
            except Exception:
                continue

        results.sort(key=lambda x: x[2], reverse=True)
        _cache[cache_key] = (time.time(), results)
        return list(results)


def get_sessions(cwd: str, provider: str = "kiro-cli") -> list[Session]:
    """Return sessions for a workspace from a specific provider. Cached."""
    cached = session_cache.get(cwd, provider)
    if cached is not None:
        return cached

    mod = PROVIDERS.get(provider)
    if mod is None:
        return []

    sessions, file_stats = mod.load_sessions(cwd)
    session_cache.put(cwd, sessions, file_stats, provider)
    return sessions


def refresh_stale_entries() -> None:
    """Check loaded workspaces for file changes across all providers; re-read only changed sessions."""
    for prov_name, mod in PROVIDERS.items():
        if not mod.is_available():
            continue
        for norm_cwd in session_cache.get_loaded_cwds(prov_name):
            try:
                old_stats = session_cache.get_file_stats(norm_cwd, prov_name)
                if not old_stats:
                    continue
                if mod.refresh_stale_entries_for_cwd(norm_cwd, old_stats):
                    original_cwd = session_cache.get_original_cwd(norm_cwd, prov_name)
                    sessions, file_stats = mod.load_sessions(original_cwd)
                    session_cache.put(original_cwd, sessions, file_stats, prov_name)
            except (OSError, Exception):
                continue


def warmup_pinned(pinned_folders: list[str]) -> None:
    """Pre-load sessions for pinned workspaces across all providers."""
    for folder in pinned_folders:
        try:
            if Path(folder).exists():
                for prov_name in PROVIDERS:
                    if PROVIDERS[prov_name].is_available():
                        get_sessions(folder, prov_name)
        except OSError:
            continue


def _find_pinned_session_workspace(session_id: str) -> tuple[str, str] | None:
    """Find the workspace for a pinned session across all providers.

    Returns (cwd, provider_name) or None if not found.
    """
    for prov_name, mod in PROVIDERS.items():
        if not mod.is_available():
            continue
        cwd = mod.find_session_workspace(session_id)
        if cwd:
            return (cwd, prov_name)
    return None


def warmup_all(pinned_folders: list[str], pinned_sessions: list[str] | None = None) -> None:
    """Pre-discover all workspaces and load pinned folder/session data."""
    discover_workspaces_with_counts()
    warmup_pinned(pinned_folders)
    # Pre-load workspaces that contain pinned sessions so they render from cache
    if pinned_sessions:
        pinned_ids = set(pinned_sessions)
        found = set()
        for prov_name in PROVIDERS:
            for norm_cwd in session_cache.get_loaded_cwds(prov_name):
                cached = session_cache.get(norm_cwd, prov_name)
                if cached:
                    for s in cached:
                        if s.session_id in pinned_ids:
                            found.add(s.session_id)
        # For unfound pinned sessions, scan all providers generically
        remaining = pinned_ids - found
        if remaining:
            for sid in list(remaining):
                result = _find_pinned_session_workspace(sid)
                if result:
                    cwd_found, prov_found = result
                    try:
                        get_sessions(cwd_found, prov_found)
                    except OSError:
                        pass
                    remaining.discard(sid)
    warmup_done.set()


def get_session_tail(session_id: str, provider: str = "kiro-cli", cwd: str = "", max_lines: int = 15) -> list[str]:
    """Extract last N assistant message texts from a session. Dispatches to provider."""
    mod = PROVIDERS.get(provider)
    if mod is None:
        return []
    return mod.get_session_tail(session_id, cwd, max_lines)


def get_first_prompt(session_id: str, provider: str = "kiro-cli", cwd: str = "") -> str:
    """Extract first_prompt for tooltip display. Dispatches to provider."""
    mod = PROVIDERS.get(provider)
    if mod is None:
        return ""
    return mod.get_first_prompt(session_id, cwd)


def get_all_sessions_paginated(
    page: int = 1,
    page_size: int = 20,
    provider: str | None = None,
    pinned_sessions: list[str] | None = None,
    enabled_providers: set[str] | None = None,
    exclude_cwds: set[str] | None = None,
) -> tuple[list[tuple[Session, str]], bool]:
    """Return sessions across all workspaces, interleaved by updated_at.

    Uses early-stopping: loads workspaces by recency until enough sessions
    are collected for the requested page, avoiding loading ALL workspaces.

    Args:
        page: 1-based page number (applies to non-pinned sessions only)
        page_size: number of non-pinned sessions per page
        provider: filter to specific provider, None = all
        pinned_sessions: session IDs to sort to top (always returned in full)
        enabled_providers: set of enabled provider names to include
        exclude_cwds: workspace paths to leave out of the result entirely.
            Normalized on entry, so callers may pass whatever form they hold.

            Excluded **before collection**, not filtered out of the result, and
            that placement is the whole point of the parameter. This function
            early-stops once it has read enough sessions for the requested
            page; a caller that instead drops rows afterwards has already let
            those rows decide where the read stopped, so it returns a page
            shorter than `page_size` and a `has_more` that no longer describes
            the data the caller is actually showing. Excluding here also skips
            the `get_sessions` disk read for a workspace nobody will see.

    Returns:
        ([(session, provider_name), ...], has_more)
        Pinned sessions first (all of them), then paginated non-pinned.
    """
    page = max(1, page)
    page_size = max(1, page_size)
    pinned_set = set(pinned_sessions) if pinned_sessions else set()
    target_count = page * page_size
    # Both passes below key on the normalized form — pass 1 because
    # `get_loaded_cwds` returns normalized paths, pass 2 because it normalizes
    # each workspace before the cache check. Normalizing once here is what lets
    # a caller pass raw config paths without every one of them having to know.
    excluded = {_normalize_path(c) for c in exclude_cwds} if exclude_cwds else set()

    # Determine which providers to check
    if provider is not None:
        providers_to_check = {provider: PROVIDERS[provider]} if provider in PROVIDERS else {}
    else:
        providers_to_check = dict(PROVIDERS)

    # Filter by enabled_providers if specified
    if enabled_providers is not None:
        providers_to_check = {
            name: mod for name, mod in providers_to_check.items()
            if name in enabled_providers
        }

    # Collect all sessions with deduplication
    seen: set[str] = set()
    all_sessions: list[tuple[Session, str]] = []

    def _collect(sessions: list[Session], prov_name: str) -> None:
        for s in sessions:
            if s.session_id not in seen:
                seen.add(s.session_id)
                all_sessions.append((s, prov_name))

    # Pass 1: cache-only (no disk IO)
    # NOTE: pinned sessions are found here because warmup_all() pre-loads their
    # workspaces into the cache at app startup.
    for prov_name in providers_to_check:
        for norm_cwd in session_cache.get_loaded_cwds(prov_name):
            if norm_cwd in excluded:
                continue
            cached = session_cache.get(norm_cwd, prov_name)
            if cached:
                _collect(cached, prov_name)

    # Pass 2: load uncached by recency, early stop
    workspace_data = discover_workspaces_with_counts(provider=None)
    loaded_cwds_by_provider: dict[str, set[str]] = {
        prov_name: session_cache.get_loaded_cwds(prov_name)
        for prov_name in providers_to_check
    }

    for cwd, _count, _updated_at, ws_provider in workspace_data:
        if ws_provider not in providers_to_check:
            continue
        norm_cwd = _normalize_path(cwd)
        # Ahead of the cache check and the disk read both, so an excluded
        # workspace costs nothing and — the part that matters — never counts
        # toward the early stop below.
        if norm_cwd in excluded:
            continue
        if norm_cwd in loaded_cwds_by_provider[ws_provider]:
            continue  # already collected in Pass 1

        # Load from disk
        sessions = get_sessions(cwd, ws_provider)
        _collect(sessions, ws_provider)

        # Early stop: count non-pinned sessions collected so far
        non_pinned_count = sum(1 for s, _ in all_sessions if s.session_id not in pinned_set)
        if non_pinned_count >= target_count + page_size:
            break

    # Sort all collected sessions by updated_at desc
    # Normalize 'Z' to '+00:00' for consistent string comparison
    def _sort_key(item: tuple[Session, str]) -> str:
        ua = item[0].updated_at
        if ua.endswith("Z"):
            ua = ua[:-1] + "+00:00"
        return ua

    all_sessions.sort(key=_sort_key, reverse=True)

    # Split into pinned and non-pinned
    pinned_items = [(s, p) for s, p in all_sessions if s.session_id in pinned_set]
    non_pinned = [(s, p) for s, p in all_sessions if s.session_id not in pinned_set]

    # Paginate non-pinned
    start = (page - 1) * page_size
    end = start + page_size
    page_items = non_pinned[start:end]
    has_more = end < len(non_pinned)

    return (pinned_items + page_items, has_more)
