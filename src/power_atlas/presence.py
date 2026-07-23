"""Detect which discovered sessions are currently live in a running process.

A session is "live" when a provider CLI process (Claude Code / Kiro CLI) is
running that resumed it (its session id appears in the process command line).
This is a best-effort, read-only scan of the process table via ``psutil``.

Correlation is intentionally **session-id based**: a session gets marked live
only when its exact id is found in a running command line (e.g.
``claude --resume <id>`` / ``kiro-cli chat --resume-id <id>``). This avoids
false positives where one live agent in a folder would otherwise light up every
historical session in that folder. The trade-off is that a *freshly started*
session (no ``--resume`` on argv, so no id) is not detected at the row level —
a documented MVP limitation.

Workspace-level liveness additionally tracks the live process's working
directory, so the Workspaces panel can tell "this folder has something running"
even when the exact session id could not be matched.

``kiro-ide`` is an IDE, not a resumable CLI session, and is excluded entirely.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .data import _normalize_path

log = logging.getLogger("power_atlas.presence")


def _parse_created_at(ts: str) -> datetime:
    """Parse ISO-8601 timestamp, handling 'Z' suffix and timezone.

    Returns datetime.min (UTC) on parse failure so max()/sorting never crashes.
    """
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    # Replace trailing 'Z' with +00:00 for fromisoformat compatibility
    if ts.endswith("Z"):
        cleaned = ts[:-1] + "+00:00"
    else:
        cleaned = ts
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    # Ensure timezone-aware (assume UTC if naive)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

try:
    import psutil
    _AVAILABLE = True
except Exception as _e:  # pragma: no cover - import guard
    psutil = None
    _AVAILABLE = False
    log.warning("psutil unavailable — live session status disabled: %s", _e)


# provider -> (executable basenames that indicate the process, resume flag)
_PROVIDER_SPECS: dict[str, tuple[tuple[str, ...], str]] = {
    "claude-code": (("claude", "claude.exe", "claude.cmd"), "--resume"),
    "kiro-cli": (("kiro-cli", "kiro-cli.exe", "kiro-cli.cmd"), "--resume-id"),
}

_SNAPSHOT_TTL = 3.0  # seconds; many partials render per refresh — reuse one scan
_cached_snapshot: "Snapshot | None" = None
_cached_at = 0.0


def is_available() -> bool:
    """Return True if process introspection is possible."""
    return _AVAILABLE


class Snapshot:
    """Immutable view of which sessions/workspaces are live at scan time."""

    def __init__(self, live_sids: set[tuple[str, str]], live_cwds: set[tuple[str, str]],
                 sid_to_cwd: dict[tuple[str, str], str] | None = None):
        # live_sids: {(provider, session_id)}
        # live_cwds: {(provider, normalized_cwd)}
        self._live_sids = live_sids
        self._live_cwds = live_cwds
        # sid_to_cwd: {(provider, session_id) -> normalized_cwd}
        self._sid_to_cwd = sid_to_cwd or {}

    def is_live(self, provider: str, cwd: str, session_id: str) -> bool:
        """True if this exact session is running (id matched on a process cmdline)."""
        if provider not in _PROVIDER_SPECS:
            return False
        return bool(session_id) and (provider, session_id) in self._live_sids

    def live_cwds(self, providers: set[str] | None = None) -> set[str]:
        """Normalized cwds where a matching provider process is running.

        ``providers`` restricts to those provider names (None = any CLI provider).
        """
        if providers is None:
            return {c for _p, c in self._live_cwds}
        return {c for p, c in self._live_cwds if p in providers}

    def live_session_ids_for_cwd(self, provider: str, cwd: str) -> list[str]:
        """Return session IDs of live processes running in the given cwd."""
        from .data import _normalize_path
        norm_cwd = _normalize_path(cwd)
        return [sid for (prov, sid), c in self._sid_to_cwd.items()
                if prov == provider and c == norm_cwd]

    def probable_fresh_session(self, provider: str, cwd: str,
                               sessions: list) -> str | None:
        """If a provider process runs in cwd but no session id was matched,
        return the session_id of the newest session (created within 90s).

        .. deprecated::
            Replaced by cwd-based detection in _session_status(). Kept for
            backward compatibility but no longer called from the live gate.

        Args:
            provider: provider name (e.g. 'kiro-cli', 'claude-code')
            cwd: workspace directory path (will be normalized)
            sessions: list of Session objects for this workspace/provider

        Returns:
            session_id of the probable fresh session, or None
        """
        if not sessions:
            return None

        norm_cwd = _normalize_path(cwd)

        # Step 1: Check if a provider process is running in this cwd
        if norm_cwd not in self.live_cwds({provider}):
            return None

        # Step 2: If any session in this cwd already has an explicit match, skip
        for s in sessions:
            if (provider, s.session_id) in self._live_sids:
                return None

        # Step 3: Find the newest session by created_at
        newest = max(sessions, key=lambda s: _parse_created_at(s.created_at))
        created = _parse_created_at(newest.created_at)
        now = datetime.now(timezone.utc)

        # Step 4: Only match if created within 90 seconds
        age = (now - created).total_seconds()
        if age <= 90:
            return newest.session_id
        return None


_EMPTY = Snapshot(set(), set(), {})


def _extract_session_id(cmdline: list[str], flag: str) -> str | None:
    """Return the id following ``flag`` (space form or ``flag=id`` form)."""
    for i, tok in enumerate(cmdline):
        if tok == flag:
            if i + 1 < len(cmdline):
                return cmdline[i + 1]
            return None
        if tok.startswith(flag + "="):
            return tok[len(flag) + 1:]
    return None


def _match_provider(name: str, argv0: str) -> str | None:
    """Identify the provider from a process name / argv[0] basename."""
    candidates = set()
    if name:
        candidates.add(name.lower())
    if argv0:
        candidates.add(Path(argv0).name.lower())
    for provider, (binaries, _flag) in _PROVIDER_SPECS.items():
        if candidates & set(binaries):
            return provider
    return None


def _scan() -> Snapshot:
    if not _AVAILABLE:
        return _EMPTY
    live_sids: set[tuple[str, str]] = set()
    live_cwds: set[tuple[str, str]] = set()
    sid_to_cwd: dict[tuple[str, str], str] = {}
    try:
        procs = psutil.process_iter(["name", "cmdline"])
    except Exception:  # pragma: no cover - platform edge
        log.exception("process scan failed")
        return _EMPTY
    for proc in procs:
        try:
            info = proc.info
            cmdline = info.get("cmdline") or []
            if not cmdline:
                continue
            provider = _match_provider(info.get("name") or "", cmdline[0])
            if provider is None:
                continue
            _binaries, flag = _PROVIDER_SPECS[provider]
            sid = _extract_session_id(cmdline, flag)
            if sid:
                live_sids.add((provider, sid))
            # Best-effort cwd for workspace-level liveness (may be denied).
            try:
                cwd = proc.cwd()
            except Exception:
                cwd = ""
            if cwd:
                norm = _normalize_path(cwd)
                live_cwds.add((provider, norm))
                if sid:
                    sid_to_cwd[(provider, sid)] = norm
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:  # pragma: no cover - defensive per-process guard
            continue
    return Snapshot(live_sids, live_cwds, sid_to_cwd)


def get_snapshot(force: bool = False) -> Snapshot:
    """Return a recent process snapshot, rescanning at most once per TTL."""
    global _cached_snapshot, _cached_at
    now = time.monotonic()
    if not force and _cached_snapshot is not None and (now - _cached_at) < _SNAPSHOT_TTL:
        return _cached_snapshot
    _cached_snapshot = _scan()
    _cached_at = now
    return _cached_snapshot
