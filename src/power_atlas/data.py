"""Provider-aware session data orchestrator.

Shared types (Session, _FileInfo) and the compound-keyed SessionCache live here.
Provider adapters (data_kiro, data_claude) handle discovery and parsing.
"""

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path


# Simple TTL cache to avoid re-reading hundreds of files on every request
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


# Import provider modules AFTER defining shared types to avoid circular import
from . import data_kiro, data_claude  # noqa: E402


# Provider registry: name -> module
PROVIDERS: dict[str, object] = {
    "kiro-cli": data_kiro,
    "claude-code": data_claude,
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

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._file_stats.clear()
            self._loaded_keys.clear()
            self._original_cwds.clear()


session_cache = SessionCache()


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
    cache_key = f"workspaces_with_counts:{provider or 'all'}"
    if cache_key in _cache:
        ts, result = _cache[cache_key]
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
        # For unfound pinned sessions, scan kiro-cli metadata to find their workspace
        remaining = pinned_ids - found
        if remaining and data_kiro.SESSION_DIR.is_dir():
            import json
            for meta_file in data_kiro.SESSION_DIR.glob("*.json"):
                if meta_file.suffix == ".jsonl":
                    continue
                if meta_file.stem not in remaining:
                    continue
                try:
                    d = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    continue
                cwd = d.get("cwd", "")
                if cwd:
                    try:
                        get_sessions(cwd, "kiro-cli")
                    except OSError:
                        pass
                remaining.discard(meta_file.stem)
                if not remaining:
                    break


def get_session_tail(session_id: str, provider: str = "kiro-cli", cwd: str = "", max_lines: int = 15) -> list[str]:
    """Extract last N assistant message texts from a session. Dispatches to provider."""
    if provider == "claude-code":
        return data_claude.get_session_tail(session_id, cwd, max_lines)
    return data_kiro.get_session_tail(session_id, max_lines)


def get_first_prompt(session_id: str, provider: str = "kiro-cli", cwd: str = "") -> str:
    """Extract first_prompt for tooltip display. Dispatches to provider."""
    if provider == "claude-code":
        return data_claude.get_first_prompt(session_id, cwd)
    return data_kiro.get_first_prompt(session_id)
