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

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

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

# --- Sidecar identity files -------------------------------------------------
# Neither CLI puts its session id on argv, so argv matching only ever succeeds
# for sessions PowerAtlas launched itself (launcher.py passes --resume /
# --resume-id). Both providers do write a per-process sidecar naming the
# session, which is the only local way to attribute a terminal-started session
# to a live process:
#
#   kiro-cli     ~/.kiro/sessions/cli/<session-id>.lock   {"pid", "started_at"}
#   claude-code  ~/.claude/sessions/<pid>.json            {"pid","sessionId","cwd",
#                                                          "startedAt","status",...}
#
# A sidecar's presence does NOT mean the session is live. Neither provider
# reliably removes them, so pids get recycled onto unrelated processes: of 785
# kiro lock files observed on one machine, 21 named a live pid and exactly one
# was genuine — the rest had been inherited by svchost, firefox, and friends.
# Liveness therefore requires the pid to be alive AND its start time to be
# consistent with the sidecar's.
#
# Forward upper bound — claude-code ONLY, not a universal rule. One `claude`
# process owns one session and writes its sidecar just after spawn; observed
# deltas are +1.1s to +1.6s and the nearest false match was ~9500s off, so
# 120s is generous and still leaves ~80x margin. It is deliberately not
# applied to kiro-cli: PowerAtlas's ACP agent (`acp.py`) is spawned once and
# serves sessions for the whole lifetime of the app, so its locks are
# legitimately minutes or hours newer than the process that wrote them —
# measured 2026-07-31, +1.88s and +23.77s for two sessions opened 21.5s apart,
# with the lock's pid equal to the agent's. Any ceiling short enough to be
# useful against recycled pids would hide every session opened past it.
_SIDECAR_SKEW_S = 120.0
# Backward bound — both providers, and the check that actually rejects a
# recycled pid. A sidecar is never written before its own process starts, so a
# negative delta means a *different* process held this pid earlier: pid
# exclusivity guarantees the recycled writer ran before the live process
# existed. That is why kiro-cli can safely go without an upper bound. The
# small allowance covers clock-source jitter between the provider's timestamp
# and psutil's create_time, nothing more.
#
# That guarantee is conditional on a monotone, same-machine clock. started_at
# is stamped from the wall clock when the lock is written, while create_time
# derives from the process's absolute creation FILETIME — so if the clock steps
# *backward* between a stale write and the recycling process's spawn (NTP
# correction, VM resume, dual-boot with a local-time RTC), the delta comes out
# positive and the kiro-cli branch admits a lock the 120s ceiling used to
# reject. Accepted rather than overlooked: plan
# 260731_ACP_REMOTE_CLIENT_PRODUCTIZATION D10 and its risk row "Clock skew
# stamping a lock in the future is no longer rejected once the upper bound is
# dropped" (rated Low). D32 records the other accepted residual — a lock our
# own long-lived ACP agent orphaned reads live for that agent's lifetime. The
# guard set is deliberately good enough, not airtight.
_SIDECAR_BACKWARD_SKEW_S = 5.0

# What PowerAtlas's own ACP agent currently holds: `(live session ids, agent
# pid)`, or `(frozenset(), None)` when nothing has published — which is the
# state for a plain `presence` import, every test that does not opt in, and any
# build where `acp` failed to import.
#
# **This closes D32, and the shape is the point.** `session/load` makes our own
# agent write a lock naming *itself*, so a lock orphaned by a failed load, or by
# a close whose terminate raised, carries `pid == the live agent` and a forward
# delta. D10 dropped the forward ceiling for kiro-cli — legitimately, since the
# agent serves sessions for the whole life of the app — and with it went the
# only thing that used to expire such an orphan after 120 s. It now reads live
# for the agent's entire lifetime.
#
# D9 forbids the obvious fix, and rightly: `presence` runs on worker threads and
# `_supervisor.sessions` is loop-owned and unlocked, so iterating it here races
# every mutation the loop makes. What is read instead is a **published
# snapshot** — an immutable `frozenset` rebound by the loop, never mutated in
# place. A reader sees the old set or the new one, never a half-built one, and
# `presence` still touches no loop-owned structure.
#
# Neither module imports the other. `acp` states an isolation boundary in its
# own header — two names from the package, with a plan exit criterion grepping
# for module names — and `presence -> acp` is the direction D9 rejected. So the
# publisher is injected by `web.py`, which already imports both.
#
# Fails safe in the direction that matters. With nothing published the pid is
# `None`, no record is ever suppressed, and this file behaves exactly as it did
# before — a wrong "live" dot, which is today's accepted D32 residual, rather
# than a session wrongly hidden from the dashboard.
_acp_live: tuple[frozenset, int | None] = (frozenset(), None)


def publish_acp_sessions(session_ids, agent_pid) -> None:
    """Publish what the ACP supervisor holds. Called from the event loop only.

    Rebinding a module global is atomic under the GIL, so no lock is needed and
    none is taken: a worker thread mid-scan holds a reference to the previous
    tuple and finishes against a consistent view of it.

    Copied into a `frozenset` rather than stored as given. The caller's argument
    is derived from loop-owned state, and storing a live view would hand worker
    threads the very object D9 exists to keep away from them.
    """
    global _acp_live
    try:
        ids = frozenset(session_ids or ())
    except TypeError:
        ids = frozenset()
    _acp_live = (ids, agent_pid if isinstance(agent_pid, int) else None)


_KIRO_LOCK_DIR = Path.home() / ".kiro" / "sessions" / "cli"
_CLAUDE_SESSION_DIR = Path.home() / ".claude" / "sessions"

# path -> (mtime, size, parsed|None). Most sidecars do not change between
# scans, so re-parsing every one every time is pure waste; a stat is cheap.
# They are NOT write-once, though — kiro-cli rewrites a lock in place on
# `session/load` and claude-code rewrites its session file on every status
# change — which is why the key is (mtime, size) rather than mere presence,
# and why the listing that produces those stats is never cached
# (see _list_sidecars).
_sidecar_cache: dict[str, tuple[float, int, dict | None]] = {}


def _load_json_cached(path: Path, st: os.stat_result | None = None) -> dict | None:
    """Parse *path* as JSON, reusing the last result while mtime/size hold.

    *st* may be supplied by a caller that already has it — ``os.scandir``
    populates ``DirEntry.stat()`` from the directory walk on Windows, so
    passing it avoids a second syscall per file.
    """
    key = str(path)
    if st is None:
        try:
            st = path.stat()
        except OSError:
            _sidecar_cache.pop(key, None)
            return None
    hit = _sidecar_cache.get(key)
    if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    data: dict | None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        data = loaded if isinstance(loaded, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        data = None
    _sidecar_cache[key] = (st.st_mtime, st.st_size, data)
    return data


def _list_sidecars(directory: Path, suffix: str) -> list[tuple[str, os.stat_result]]:
    """Return (path, stat) for files in *directory* ending in *suffix*.

    The listing is rebuilt on every scan, deliberately. Caching it on the
    directory's own mtime was tried and removed: an in-place rewrite of a
    sidecar leaves the directory mtime untouched, and the stats collected here
    are exactly what ``_load_json_cached`` compares against — so a cached
    listing pins a stale parse for as long as no file is created or deleted.
    Both providers rewrite in place (kiro-cli on ``session/load``, claude-code
    on every ``status`` change), so neither directory qualified.

    The kiro lock directory also holds the session transcripts, so this walks
    13k+ entries; measured ~19ms, and it runs off the event loop behind
    ``get_snapshot``'s TTL.
    """
    found: list[tuple[str, os.stat_result]] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if not entry.name.endswith(suffix):
                    continue
                try:
                    if entry.is_file():
                        found.append((entry.path, entry.stat()))
                except OSError:
                    continue
    except OSError:
        return []
    return found


def _epoch_from_iso(value: str) -> float | None:
    """Parse an RFC3339/ISO-8601 instant to a POSIX timestamp."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class _Sidecar(NamedTuple):
    """One provider sidecar, parsed but not yet validated for liveness.

    A NamedTuple rather than a bare tuple because the field count outgrew what
    a positional unpack can be read safely at: `kind` and `entrypoint` joined
    an already-seven-wide row. Nothing outside this module consumes it, so the
    change costs no caller.
    """
    provider: str
    pid: int
    sid: str
    started: float
    cwd: str
    status: str
    reason: str
    kind: str
    entrypoint: str


def _sidecar_records() -> list[_Sidecar]:
    """Collect one ``_Sidecar`` per readable sidecar file.

    Everything but ``provider``, ``pid``, ``sid`` and ``started`` is
    best-effort and may be empty. No liveness filtering happens here — the
    caller validates against the process table.
    """
    out: list[_Sidecar] = []

    for lock, st in _list_sidecars(_KIRO_LOCK_DIR, ".lock"):
        data = _load_json_cached(Path(lock), st)
        if not data:
            continue
        pid = data.get("pid")
        started = _epoch_from_iso(str(data.get("started_at", "")))
        if not isinstance(pid, int) or started is None:
            continue
        # cwd lives in the sibling metadata; only worth reading for a
        # candidate, which the caller has not filtered yet — defer it.
        # kiro-cli's lock carries `{pid, started_at}` and nothing else, so it
        # has no equivalent of claude-code's `kind`/`entrypoint`.
        out.append(_Sidecar("kiro-cli", pid, Path(lock).stem, started,
                            "", "", "", "", ""))

    for meta, st in _list_sidecars(_CLAUDE_SESSION_DIR, ".json"):
        data = _load_json_cached(Path(meta), st)
        if not data:
            continue
        pid = data.get("pid")
        sid = data.get("sessionId") or ""
        started_ms = data.get("startedAt")
        cwd = data.get("cwd")
        if not isinstance(pid, int) or not isinstance(sid, str) or not sid:
            continue
        if not isinstance(started_ms, (int, float)) or isinstance(started_ms, bool):
            continue
        reason = data.get("waitingFor")
        # `kind` and `entrypoint` together say whether a human is sitting in
        # front of this session, and **both are needed** — measured 2026-08-04
        # on 2.1.221. `kind` comes straight from `CLAUDE_CODE_SESSION_KIND`, so
        # it only ever reads non-`interactive` when a caller sets that variable:
        # a plain `claude -p` — the shape every script and CI job uses — reports
        # `kind: interactive` with `entrypoint: sdk-cli`. Keying on `kind` alone
        # would therefore miss the commonest machine-driven session. Claude
        # Code's own `/resume` filter agrees, keying on entrypoint against
        # `{sdk-cli, sdk-ts, sdk-py}` and using `sessionKind` only for the
        # daemon pair.
        #
        # Passed through raw and unmapped, exactly as `status` is: this module
        # reports what the provider said, and `web._session_origin` decides what
        # it means. An unrecognised value arrives intact rather than as "".
        out.append(_Sidecar("claude-code", pid, sid, started_ms / 1000.0,
                            cwd if isinstance(cwd, str) else "",
                            str(data.get("status") or ""),
                            reason if isinstance(reason, str) else "",
                            str(data.get("kind") or ""),
                            str(data.get("entrypoint") or "")))

    return out


def _kiro_session_cwd(session_id: str) -> str:
    """cwd for a kiro-cli session, from the metadata beside its lock file."""
    data = _load_json_cached(_KIRO_LOCK_DIR / f"{session_id}.json")
    return (data or {}).get("cwd", "") or ""


def is_available() -> bool:
    """Return True if process introspection is possible."""
    return _AVAILABLE


class Snapshot:
    """Immutable view of which sessions/workspaces are live at scan time."""

    def __init__(self, live_sids: set[tuple[str, str]], live_cwds: set[tuple[str, str]],
                 sid_to_cwd: dict[tuple[str, str], str] | None = None,
                 sid_status: dict[tuple[str, str], str] | None = None,
                 sid_reason: dict[tuple[str, str], str] | None = None,
                 sid_kind: dict[tuple[str, str], str] | None = None,
                 sid_entrypoint: dict[tuple[str, str], str] | None = None):
        # live_sids: {(provider, session_id)}
        # live_cwds: {(provider, normalized_cwd)}
        self._live_sids = live_sids
        self._live_cwds = live_cwds
        # sid_to_cwd: {(provider, session_id) -> normalized_cwd}
        self._sid_to_cwd = sid_to_cwd or {}
        # sid_status: {(provider, session_id) -> provider-reported status}
        # Currently only claude-code reports one. The value is whatever its
        # sidecar carries, passed through unmapped — "busy", "shell", "waiting"
        # and "idle" are the four this build knows how to read (see
        # web._map_reported_status), and an unknown fifth would arrive here
        # intact. Both the session dot and the workspace dot read it via
        # reported_status().
        self._sid_status = sid_status or {}
        # sid_reason: {(provider, session_id) -> why the session is waiting}
        self._sid_reason = sid_reason or {}
        # sid_kind / sid_entrypoint: {(provider, session_id) -> raw provider
        # value}. claude-code only; kiro-cli's lock carries neither. Both are
        # **trailing and keyword-defaulted on purpose** — this class is
        # constructed positionally in `tests/test_web.py` and by keyword in
        # `tests/test_data.py`, so a parameter inserted anywhere but the end
        # silently re-binds an existing argument at every positional site.
        self._sid_kind = sid_kind or {}
        self._sid_entrypoint = sid_entrypoint or {}

    def reported_status(self, provider: str, session_id: str) -> str:
        """Provider-reported live status, or "" when the provider offers none."""
        return self._sid_status.get((provider, session_id), "")

    def session_kind(self, provider: str, session_id: str) -> str:
        """Provider-reported session kind, or "" when the provider offers none.

        claude-code only, and one of ``interactive``/``bg``/``daemon``/
        ``daemon-worker`` when present. Read it alongside
        ``session_entrypoint()`` rather than on its own — see the note in
        ``_sidecar_records`` for why ``kind`` misses most machine-driven
        sessions by itself.
        """
        return self._sid_kind.get((provider, session_id), "")

    def session_entrypoint(self, provider: str, session_id: str) -> str:
        """How the session was started, as the provider labels it.

        ``cli`` for a terminal session; ``sdk-cli``/``sdk-ts``/``sdk-py`` for
        an SDK or ``-p`` run; also ``mcp``, ``local-agent``, ``remote*``,
        ``claude-vscode`` and others. "" when the provider offers none.
        """
        return self._sid_entrypoint.get((provider, session_id), "")

    def waiting_reason(self, provider: str, session_id: str) -> str:
        """Why a waiting session is blocked, as the provider describes it.

        Only meaningful alongside a reported status of "waiting"; "" otherwise.
        """
        return self._sid_reason.get((provider, session_id), "")

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


def _match_provider(name: str, cmdline: list[str]) -> str | None:
    """Identify the provider from a process name / argv[0] basename.

    Electron helper processes are rejected: the Claude Desktop app ships a
    binary also called ``claude.exe`` and forks renderer/gpu/crashpad children
    that would otherwise be counted as CLI sessions. They are identifiable by
    the ``--type=`` switch Electron passes to every child.
    """
    if any(a.startswith("--type=") for a in cmdline[1:]):
        return None
    argv0 = cmdline[0] if cmdline else ""
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
    sid_status: dict[tuple[str, str], str] = {}
    sid_reason: dict[tuple[str, str], str] = {}
    sid_kind: dict[tuple[str, str], str] = {}
    sid_entrypoint: dict[tuple[str, str], str] = {}
    # pid -> (provider, create_time) for live provider processes only. A
    # sidecar is trusted only against one of these, which is both the cheap
    # guard and the strong one: a recycled pid almost always lands on some
    # unrelated binary (svchost, firefox, the IDE), and requiring the provider
    # to match eliminates it without touching the process at all.
    provider_pids: dict[int, tuple[str, float]] = {}
    try:
        # create_time is deliberately not requested for the whole table —
        # that costs ~25ms across ~500 processes. It is read below only for
        # the handful that matched a provider, off the Process object
        # process_iter already built.
        procs = psutil.process_iter(["pid", "name", "cmdline"])
    except Exception:  # pragma: no cover - platform edge
        log.exception("process scan failed")
        return _EMPTY
    for proc in procs:
        try:
            info = proc.info
            cmdline = info.get("cmdline") or []
            if not cmdline:
                continue
            provider = _match_provider(info.get("name") or "", cmdline)
            if provider is None:
                continue
            pid = info.get("pid")
            if pid is not None:
                try:
                    provider_pids[pid] = (provider, proc.create_time())
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
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

    # Second pass: recover session ids from the sidecar files. This is what
    # makes a terminal-started session identifiable at all — argv alone only
    # matches sessions PowerAtlas resumed itself. See _sidecar_records() for
    # why the start-time check is not optional.
    try:
        records = _sidecar_records()
    except Exception:  # pragma: no cover - sidecars are best-effort
        log.exception("sidecar enumeration failed")
        records = []
    # One read for the whole pass. Re-reading per record would let a mid-scan
    # publish show one snapshot for one session and a newer one for the next,
    # which is the inconsistency the immutable snapshot exists to prevent.
    acp_sids, acp_pid = _acp_live
    for rec in records:
        provider, pid, sid, started = rec.provider, rec.pid, rec.sid, rec.started
        cwd, status, reason = rec.cwd, rec.status, rec.reason
        # Per-record isolation: one unparseable or oddly-typed sidecar must
        # not drop the remaining sessions, which would silently regress to
        # the pre-sidecar behaviour of matching nothing.
        try:
            live = provider_pids.get(pid)
            if live is None or live[0] != provider:
                continue
            # A sidecar is always written after its process spawns, so only a
            # forward offset is physically meaningful. The backward bound is
            # what rejects a recycled pid — that writer necessarily ran before
            # the live process existed — and it applies to both providers.
            delta = started - live[1]
            if delta < -_SIDECAR_BACKWARD_SKEW_S:
                continue
            # The forward ceiling applies to every provider except kiro-cli,
            # whose locks may be arbitrarily newer than their process because
            # PowerAtlas's ACP agent serves sessions for the app's whole
            # lifetime. Exempting by name rather than naming the providers that
            # keep the ceiling is deliberate: a provider added later inherits
            # the conservative bound until someone makes kiro-cli's case for
            # it. See _SIDECAR_SKEW_S.
            if provider != "kiro-cli" and delta > _SIDECAR_SKEW_S:
                continue
            # D32, closed. A lock naming *our own* ACP agent for a session that
            # agent no longer holds is an orphan it left behind — a failed
            # `session/load`, or a close whose terminate raised. Dropping the
            # forward ceiling above is what made those permanent: the pid is
            # genuinely live and the delta is genuinely forward, so every other
            # check here passes.
            #
            # Read once, outside the try, so a scan cannot see one tuple for
            # one record and a newer one for the next.
            #
            # Only ever *removes* a claim, and only for a pid we own. A foreign
            # kiro-cli's lock is untouched — its pid is not the agent's — and a
            # session the agent really holds is in the published set. The
            # residual it replaces is the opposite direction and worse: a live
            # dot on a card for a session nothing can open.
            if (provider == "kiro-cli" and acp_pid is not None
                    and pid == acp_pid and sid not in acp_sids):
                continue
            key = (provider, sid)
            live_sids.add(key)
            if status:
                sid_status[key] = status
            if reason:
                sid_reason[key] = reason
            if rec.kind:
                sid_kind[key] = rec.kind
            if rec.entrypoint:
                sid_entrypoint[key] = rec.entrypoint
            if not cwd and provider == "kiro-cli":
                cwd = _kiro_session_cwd(sid)
            if cwd:
                norm = _normalize_path(cwd)
                sid_to_cwd[key] = norm
                live_cwds.add((provider, norm))
        except Exception:  # pragma: no cover - defensive per-record guard
            log.exception("sidecar record rejected: provider=%s sid=%r", provider, sid)
            continue

    return Snapshot(live_sids, live_cwds, sid_to_cwd, sid_status, sid_reason,
                    sid_kind, sid_entrypoint)


def get_snapshot(force: bool = False) -> Snapshot:
    """Return a recent process snapshot, rescanning at most once per TTL."""
    global _cached_snapshot, _cached_at
    now = time.monotonic()
    if not force and _cached_snapshot is not None and (now - _cached_at) < _SNAPSHOT_TTL:
        return _cached_snapshot
    _cached_snapshot = _scan()
    _cached_at = now
    return _cached_snapshot
